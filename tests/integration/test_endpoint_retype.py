"""Retyping keeps all data (spec §1.3)."""
from uuid import uuid4

from tests.integration.test_abac_authorization import AbacFixtures

DEVICE = "retype-dev"
STRING_FIELD = {"type": "string", "label": "Text"}


def _label() -> str:
    return f"rt-{uuid4().hex[:8]}"


async def _type(client, fields, mapping=None) -> str:
    response = await client.post(
        "/endpoint-types", json={"label": _label(), "fields": fields, "mapping": mapping or {}}
    )
    assert response.status_code == 201, response.text
    return response.json()["type_id"]


async def _setup(client, db_session):
    await AbacFixtures(db_session).setup(devices={DEVICE: {}})
    old_type = await _type(client, {"ip": STRING_FIELD, "serial": STRING_FIELD}, {"ip": "ip"})
    new_type = await _type(
        client, {"serial": {**STRING_FIELD, "required": True}, "location": STRING_FIELD}
    )
    created = await client.post(
        f"/endpoints?device_id={DEVICE}",
        json={"type_id": old_type, "endpoint_data": {"ip": "10.1.0.1", "serial": "S-1", "note": "keep me"}},
    )
    assert created.status_code == 201, created.text
    return old_type, new_type, created.json()["endpoint_id"]


class TestEndpointRetype:
    async def test_retype_keeps_all_data(self, client, db_session):
        _, new_type, endpoint_id = await _setup(client, db_session)
        response = await client.patch(
            f"/endpoints/{endpoint_id}",
            json={"type_id": new_type, "endpoint_data": {"location": "hall"}},
        )
        assert response.status_code == 200, response.text
        data = response.json()["endpoint_data"]
        # matching key becomes a field value of the new type
        assert data["serial"]["value"] == "S-1"
        assert data["serial"]["field"] is not None
        # keys the new type doesn't define stay as Custom Fields
        assert data["ip"] == {"value": "10.1.0.1", "field": None}
        assert data["note"] == {"value": "keep me", "field": None}
        assert data["location"]["value"] == "hall"
        assert response.json()["type_id"] == new_type

    async def test_retype_patch_null_removes_the_key(self, client, db_session):
        _, new_type, endpoint_id = await _setup(client, db_session)
        response = await client.patch(
            f"/endpoints/{endpoint_id}",
            json={"type_id": new_type, "endpoint_data": {"note": None}},
        )
        assert response.status_code == 200, response.text
        assert "note" not in response.json()["endpoint_data"]

    async def test_retype_missing_required_field_is_422_and_changes_nothing(self, client, db_session):
        old_type, _, endpoint_id = await _setup(client, db_session)
        strict_type = await _type(client, {"asset_no": {**STRING_FIELD, "required": True}})
        response = await client.patch(f"/endpoints/{endpoint_id}", json={"type_id": strict_type})
        assert response.status_code == 422, response.text
        unchanged = (await client.get(f"/endpoints/{endpoint_id}")).json()
        assert unchanged["type_id"] == old_type
        assert unchanged["endpoint_data"]["note"]["value"] == "keep me"

    async def test_retype_with_value_of_wrong_type_is_422(self, client, db_session):
        _, _, endpoint_id = await _setup(client, db_session)
        int_serial_type = await _type(client, {"serial": {"type": "integer", "label": "Serial"}})
        response = await client.patch(f"/endpoints/{endpoint_id}", json={"type_id": int_serial_type})
        assert response.status_code == 422, response.text


class TestRetypeIgnoresChangeable:
    async def test_retype_onto_type_with_non_changeable_field_succeeds(self, client, db_session):
        # `changeable: false` is not enforced on retype (decision Q3).
        _, _, endpoint_id = await _setup(client, db_session)
        locked_type = await _type(
            client, {"serial": {**STRING_FIELD, "changeable": False}}
        )
        response = await client.patch(f"/endpoints/{endpoint_id}", json={"type_id": locked_type})
        assert response.status_code == 200, response.text
        assert response.json()["type_id"] == locked_type
        assert response.json()["endpoint_data"]["serial"]["value"] == "S-1"


class TestRawDataNotExposed:
    async def test_get_endpoint_and_service_do_not_contain_raw_data(self, client, db_session):
        _, _, endpoint_id = await _setup(client, db_session)
        service_type = (
            await client.post("/service-types", json={"label": _label(), "fields": {"proto": STRING_FIELD}})
        ).json()["type_id"]
        service = await client.post(
            f"/services?endpoint_id={endpoint_id}",
            json={"type_id": service_type, "service_data": {"proto": "x"}},
        )
        assert service.status_code == 201, service.text
        service_id = service.json()["service_id"]

        endpoint_json = (await client.get(f"/endpoints/{endpoint_id}")).text
        service_json = (await client.get(f"/services/{service_id}")).text
        assert "raw_endpoint_data" not in endpoint_json
        assert "raw_service_data" not in service_json
