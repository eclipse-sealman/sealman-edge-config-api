"""Retyping a device keeps all its metadata."""
from uuid import uuid4

from tests.integration.test_abac_authorization import AbacFixtures

DEVICE = "device-retype-dev"
STRING_FIELD = {"type": "string", "label": "Text"}


async def _type(client, fields) -> str:
    response = await client.post(
        "/device-types", json={"label": f"rt-{uuid4().hex[:8]}", "fields": fields}
    )
    assert response.status_code == 201, response.text
    return response.json()["type_id"]


class TestDeviceRetype:
    async def test_retype_keeps_all_data(self, client, db_session):
        await AbacFixtures(db_session).setup(devices={DEVICE: {}})
        new_type = await _type(client, {"site": STRING_FIELD})
        await client.patch(f"/{DEVICE}/metadata", json={"note": "keep me"})

        response = await client.patch(
            f"/{DEVICE}/type", json={"typeId": new_type, "deviceMetadata": {"site": "hall"}}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["typeId"] == new_type
        assert body["deviceMetadata"]["site"]["value"] == "hall"
        assert body["deviceMetadata"]["site"]["field"] is not None
        assert body["deviceMetadata"]["note"] == {"value": "keep me", "field": None}

    async def test_retype_missing_required_field_is_422_and_changes_nothing(self, client, db_session):
        await AbacFixtures(db_session).setup(devices={DEVICE: {}})
        before = (await client.get(f"/{DEVICE}/metadata")).json()["typeId"]
        strict_type = await _type(client, {"asset_no": {**STRING_FIELD, "required": True}})

        response = await client.patch(f"/{DEVICE}/type", json={"typeId": strict_type})

        assert response.status_code == 422, response.text
        assert (await client.get(f"/{DEVICE}/metadata")).json()["typeId"] == before

    async def test_retype_unknown_device_is_404(self, client, db_session):
        await AbacFixtures(db_session).setup(devices={DEVICE: {}})
        new_type = await _type(client, {"site": STRING_FIELD})

        response = await client.patch("/does-not-exist/type", json={"typeId": new_type})

        assert response.status_code == 404, response.text
