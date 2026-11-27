"""Device search on one endpoint field: POST /devices/search, GET /devices/search/keys and
/devices/search/suggestions with `field`."""
from tests.integration.endpoint_fixtures import seed_endpoint, seed_endpoint_type
from tests.integration.test_abac_authorization import AbacFixtures


async def _setup(db_session):
    await AbacFixtures(db_session).setup(devices={"sf-a": {}, "sf-b": {}})
    type_id = await seed_endpoint_type(
        db_session, fields={"serial": {"label": "Serial Number", "type": "string"}, "ip": {"label": "IP"}}
    )
    await seed_endpoint(db_session, "sf-a", type_id, {"serial": "SN-123456", "ip": "10.0.0.1"})
    # the value only appears under another key on this device
    await seed_endpoint(db_session, "sf-b", type_id, {"serial": "SN-999", "ip": "SN-123456"})
    return type_id


class TestEndpointFieldSearch:
    async def test_filters_on_the_named_field_only(self, client, db_session):
        await _setup(db_session)
        body = {"filters": [{"type": "endpoint", "field": "serial", "value": "123456"}]}
        response = await client.post("/devices/search", json=body)
        assert response.status_code == 200, response.text
        result = response.json()
        assert [item["device_id"] for item in result["items"]] == ["sf-a"]
        match = result["items"][0]["matches"][0]
        assert match["kind"] == "endpoint"
        assert [f["key"] for f in match["fields"]] == ["serial"]

    async def test_without_field_matches_any_field(self, client, db_session):
        await _setup(db_session)
        body = {"filters": [{"type": "endpoint", "value": "123456"}]}
        response = await client.post("/devices/search", json=body)
        assert response.status_code == 200, response.text
        assert [item["device_id"] for item in response.json()["items"]] == ["sf-a", "sf-b"]

    async def test_field_is_rejected_for_device_id_filters(self, client, db_session):
        await _setup(db_session)
        body = {"filters": [{"type": "device_id", "field": "serial", "value": "sf"}]}
        response = await client.post("/devices/search", json=body)
        assert response.status_code in (400, 422)

    async def test_lists_the_field_keys_of_endpoints(self, client, db_session):
        await _setup(db_session)
        response = await client.get("/devices/search/keys?type=endpoint")
        assert response.status_code == 200, response.text
        keys = {entry["key"]: entry["label"] for entry in response.json()["keys"]}
        assert keys["serial"] == "Serial Number"
        assert keys["ip"] == "IP"

    async def test_keys_can_be_searched_by_key_or_label(self, client, db_session):
        await _setup(db_session)
        response = await client.get("/devices/search/keys?type=endpoint&q=numb")
        assert response.status_code == 200, response.text
        assert [entry["key"] for entry in response.json()["keys"]] == ["serial"]
        response = await client.get("/devices/search/keys?type=endpoint&q=zzz")
        assert response.json() == {"keys": [], "has_more": False}

    async def test_keys_are_capped_and_flag_that_more_exist(self, client, db_session):
        await AbacFixtures(db_session).setup(devices={"sf-many": {}})
        type_id = await seed_endpoint_type(db_session)
        await seed_endpoint(db_session, "sf-many", type_id, {f"key_{i:02d}": "v" for i in range(25)})
        response = await client.get("/devices/search/keys?type=endpoint&q=key_")
        body = response.json()
        assert len(body["keys"]) == 20
        assert body["has_more"] is True

    async def test_suggestions_are_limited_to_the_field(self, client, db_session):
        await _setup(db_session)
        response = await client.get("/devices/search/suggestions?type=endpoint&field=serial&q=SN-")
        assert response.status_code == 200, response.text
        assert response.json()["values"] == ["SN-123456", "SN-999"]
        response = await client.get("/devices/search/suggestions?type=endpoint&field=ip&q=SN-")
        assert response.json()["values"] == ["SN-123456"]


async def test_a_file_is_found_by_its_name_only(client, db_session):
    from sqlalchemy import update
    from db.models.device import Device
    from db.models.endpoint import Endpoint

    await AbacFixtures(db_session).setup(devices={"sf-file": {}})
    ref = {"$file": "4f1c2e9a-file-id", "name": "plc-client.crt", "kind": "certificate", "not_after": None,
           "uploaded_at": "2026-01-01T00:00:00Z"}
    type_id = await seed_endpoint_type(db_session, fields={"cert": {"label": "Cert", "type": "file"}})
    endpoint_id = await seed_endpoint(db_session, "sf-file", type_id)
    # stored directly: only an upload may set a reference
    await db_session.execute(update(Endpoint).where(Endpoint.endpoint_id == endpoint_id)
                             .values(endpoint_data={"cert": ref}))
    device_data = dict((await db_session.get(Device, "sf-file")).device_data)
    await db_session.execute(update(Device).where(Device.device_id == "sf-file")
                             .values(device_data={**device_data, "cert": ref}))
    await db_session.commit()

    for kind in ("endpoint", "metadata"):
        found = await client.post("/devices/search", json={"filters": [{"type": kind, "value": "plc-client"}]})
        assert found.status_code == 200, found.text
        assert [item["device_id"] for item in found.json()["items"]] == ["sf-file"], kind
        for other in ("4f1c2e9a", "certificate"):
            response = await client.post("/devices/search", json={"filters": [{"type": kind, "value": other}]})
            assert response.json()["items"] == [], (kind, other)
