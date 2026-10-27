"""GET /devices/with-data: paged read of devices and endpoints by field presence."""
from uuid import uuid4

import pytest
from sqlalchemy import text

from db.repos.endpoint import EndpointRepository
from tests.integration.test_abac_authorization import AbacFixtures, _repo


async def _endpoint_type(db_session) -> str:
    repo = _repo(EndpointRepository, db_session)
    created = await repo.create_endpoint_type(
        label=f"wd-{uuid4().hex[:8]}", description=None, fields={}
    )
    return created["type_id"]


async def _endpoint(db_session, device_id, type_id, data, created_at=None) -> str:
    repo = _repo(EndpointRepository, db_session)
    created = await repo.create_endpoint(device_id=device_id, type_id=type_id, endpoint_data=data)
    endpoint_id = created["endpoint_id"]
    if created_at is not None:
        await db_session.execute(
            text("update endpoints set created_at = cast(:ts as timestamptz) where endpoint_id = :id"),
            {"ts": created_at, "id": endpoint_id},
        )
    return endpoint_id


def _ids(body) -> list[str]:
    return [item["device_id"] for item in body["items"]]


class TestDevicesWithData:
    async def test_matches_by_device_field(self, client, db_session):
        await AbacFixtures(db_session).setup(
            devices={
                "wd-a": {"acme_id": "A-1"},
                "wd-b": {"acme_id": None},
                "wd-c": {"other": "x"},
            }
        )
        response = await client.get("/devices/with-data?device_field=acme_id")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["device_id"] == "wd-a"
        assert item["type_id"] == "default"
        assert item["device_data"]["acme_id"] == "A-1"
        assert item["created_at"]
        assert item["endpoints"] == []

    async def test_matches_by_endpoint_field_and_lists_only_matching_endpoints(self, client, db_session):
        await AbacFixtures(db_session).setup(devices={"wd-a": {}, "wd-b": {}})
        type_id = await _endpoint_type(db_session)
        late = await _endpoint(db_session, "wd-a", type_id, {"acme_ref": "R-2"}, "2026-01-02T00:00:00Z")
        early = await _endpoint(db_session, "wd-a", type_id, {"acme_ref": "R-1"}, "2026-01-01T00:00:00Z")
        await _endpoint(db_session, "wd-a", type_id, {"ip": "10.0.0.1"})
        await _endpoint(db_session, "wd-b", type_id, {"acme_ref": None, "ip": "10.0.0.2"})

        response = await client.get("/devices/with-data?endpoint_field=acme_ref")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["device_id"] == "wd-a"
        assert [e["endpoint_id"] for e in item["endpoints"]] == [early, late]
        first = item["endpoints"][0]
        assert first["type_id"] == type_id
        assert first["endpoint_data"] == {"acme_ref": "R-1"}
        assert first["updated_at"]
        assert set(first) == {"endpoint_id", "type_id", "endpoint_data", "updated_at"}

    async def test_any_semantics_across_fields(self, client, db_session):
        await AbacFixtures(db_session).setup(
            devices={
                "wd-a": {"acme_id": "A-1"},
                "wd-b": {"acme_name": "Bee"},
                "wd-c": {},
                "wd-d": {},
            }
        )
        type_id = await _endpoint_type(db_session)
        await _endpoint(db_session, "wd-c", type_id, {"acme_ref": "R-1"})
        await _endpoint(db_session, "wd-c", type_id, {"acme_flag": True})
        await _endpoint(db_session, "wd-d", type_id, {"ip": "10.0.0.1"})

        response = await client.get(
            "/devices/with-data?device_field=acme_id&device_field=acme_name"
            "&endpoint_field=acme_ref&endpoint_field=acme_flag&order_by=created_at&order=asc"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 3
        assert sorted(_ids(body)) == ["wd-a", "wd-b", "wd-c"]
        by_id = {item["device_id"]: item for item in body["items"]}
        assert len(by_id["wd-c"]["endpoints"]) == 2
        assert by_id["wd-a"]["endpoints"] == []

    async def test_orders_by_device_data_key_desc_nulls_last(self, client, db_session):
        await AbacFixtures(db_session).setup(
            devices={
                "wd-a": {"acme_id": "1", "acme_name": "Bravo"},
                "wd-b": {"acme_id": "2"},
                "wd-c": {"acme_id": "3", "acme_name": "Charlie"},
                "wd-d": {"acme_id": "4", "acme_name": "Alpha"},
                "wd-e": {"acme_id": "5"},
            }
        )
        base = "/devices/with-data?device_field=acme_id&order_by=device_data.acme_name"
        desc = await client.get(base + "&order=desc")
        assert desc.status_code == 200, desc.text
        assert _ids(desc.json()) == ["wd-c", "wd-a", "wd-d", "wd-b", "wd-e"]

        asc = await client.get(base + "&order=asc")
        assert asc.status_code == 200, asc.text
        assert _ids(asc.json()) == ["wd-d", "wd-a", "wd-c", "wd-b", "wd-e"]

    async def test_json_null_sort_value_sorts_after_valued_devices(self, client, db_session):
        await AbacFixtures(db_session).setup(
            devices={
                "wd-a": {"acme_id": "1", "acme_name": None},
                "wd-b": {"acme_id": "2", "acme_name": "Bravo"},
                "wd-c": {"acme_id": "3", "acme_name": "Alpha"},
            }
        )
        base = "/devices/with-data?device_field=acme_id&order_by=device_data.acme_name"
        for order, expected in (("asc", ["wd-c", "wd-b", "wd-a"]), ("desc", ["wd-b", "wd-c", "wd-a"])):
            response = await client.get(base + f"&order={order}")
            assert response.status_code == 200, response.text
            assert _ids(response.json()) == expected

    async def test_default_order_is_created_at_desc(self, client, db_session):
        await AbacFixtures(db_session).setup(
            devices={"wd-a": {"acme_id": "1"}, "wd-b": {"acme_id": "2"}, "wd-c": {"acme_id": "3"}}
        )
        for device_id, ts in (("wd-a", "2026-01-02"), ("wd-b", "2026-01-03"), ("wd-c", "2026-01-01")):
            await db_session.execute(
                text("update devices set created_at = cast(:ts as timestamptz) where device_id = :id"),
                {"ts": ts, "id": device_id},
            )
        response = await client.get("/devices/with-data?device_field=acme_id")
        assert response.status_code == 200, response.text
        assert _ids(response.json()) == ["wd-b", "wd-a", "wd-c"]

    async def test_paging_total_and_slices(self, client, db_session):
        await AbacFixtures(db_session).setup(
            devices={f"wd-{i}": {"acme_id": f"{i}"} for i in range(5)}
        )
        base = "/devices/with-data?device_field=acme_id&order_by=device_data.acme_id&order=asc&page_size=2"
        pages = []
        for page in (1, 2, 3, 4):
            response = await client.get(f"{base}&page={page}")
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 5
            pages.append(_ids(response.json()))
        assert pages == [["wd-0", "wd-1"], ["wd-2", "wd-3"], ["wd-4"], []]

    @pytest.mark.parametrize(
        "query",
        [
            "",
            "?order_by=created_at",
            "?device_field=acme_id&order_by=device_data.bad-key",
            "?device_field=acme_id&order_by=device_data.",
            "?device_field=acme_id&order_by=updated_at",
            "?device_field=acme_id&order=sideways",
            "?device_field=acme_id&page=0",
            "?device_field=acme_id&page_size=0",
            "?device_field=acme_id&page_size=1001",
            "?device_field=acme_id&page=abc",
            "?device_field=acme_id%0A",
            "?device_field=acme_id&order_by=device_data.acme_id%0A",
        ],
    )
    async def test_rejects_missing_fields_and_bad_order_by(self, client, query):
        response = await client.get(f"/devices/with-data{query}")
        assert response.status_code == 400, response.text

    async def test_route_not_shadowed_by_device_id(self, client, db_session):
        await AbacFixtures(db_session).setup(devices={"wd-a": {"acme_id": "A-1"}})
        response = await client.get("/devices/with-data?device_field=acme_id")
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"total", "items"}
        assert "with-data" not in _ids(body)


SCOPED_OID = "with-data-scoped-oid"


class TestDevicesWithDataScope:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": SCOPED_OID,
            "sub": SCOPED_OID,
            "preferred_username": "with-data@test.com",
            "name": "With Data",
            "roles": [],
        }

    async def test_scoped_caller_sees_only_scoped_devices(self, client, db_session):
        await AbacFixtures(db_session).setup(
            users={"tester": SCOPED_OID},
            scopes={"x-scope": {"attr": {"customer": "X"}, "access_rule": "ALL"}},
            roles={"reader": ["device.read", "device.endpoint.read"]},
            teams={"x-team": {"scope": "x-scope", "roles": ["reader"], "users": ["tester"]}},
            devices={
                "wd-in-1": {"customer": "X", "acme_id": "1"},
                "wd-out-1": {"customer": "Y", "acme_id": "2"},
                "wd-in-2": {"customer": "X", "acme_id": "3"},
                "wd-out-2": {"acme_id": "4"},
                "wd-in-3": {"customer": "X", "acme_id": "5"},
            },
        )
        base = "/devices/with-data?device_field=acme_id&order_by=device_data.acme_id&order=asc&page_size=2"
        first = await client.get(base + "&page=1")
        assert first.status_code == 200, first.text
        assert first.json()["total"] == 3
        assert _ids(first.json()) == ["wd-in-1", "wd-in-2"]

        second = await client.get(base + "&page=2")
        assert second.status_code == 200, second.text
        assert second.json()["total"] == 3
        assert _ids(second.json()) == ["wd-in-3"]

    async def test_device_must_pass_both_permission_scopes(self, client, db_session):
        await AbacFixtures(db_session).setup(
            users={"tester": SCOPED_OID},
            scopes={
                "x-scope": {"attr": {"customer": "X"}, "access_rule": "ALL"},
                "x-or-y-scope": {"attr": {"customer": ["X", "Y"]}, "access_rule": "ALL"},
            },
            roles={"device-reader": ["device.read"], "endpoint-reader": ["device.endpoint.read"]},
            teams={
                "read-x-or-y": {"scope": "x-or-y-scope", "roles": ["device-reader"], "users": ["tester"]},
                "endpoints-x-only": {"scope": "x-scope", "roles": ["endpoint-reader"], "users": ["tester"]},
            },
            devices={
                "wd-x": {"customer": "X", "acme_id": "1"},
                "wd-y": {"customer": "Y", "acme_id": "2"},
            },
        )
        response = await client.get("/devices/with-data?device_field=acme_id")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert _ids(response.json()) == ["wd-x"]

    @pytest.mark.parametrize(
        "actions",
        [["device.endpoint.read"], ["device.read"]],
    )
    async def test_requires_device_read(self, client, db_session, actions):
        await AbacFixtures(db_session).setup(
            users={"tester": SCOPED_OID},
            roles={"partial": actions},
            teams={"partial-team": {"roles": ["partial"], "users": ["tester"]}},
            devices={"wd-a": {"acme_id": "1"}},
        )
        response = await client.get("/devices/with-data?device_field=acme_id")
        assert response.status_code == 403, response.text
