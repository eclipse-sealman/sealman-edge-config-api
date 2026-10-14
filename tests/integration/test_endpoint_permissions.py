"""Per-device checks on /endpoints (spec §3)."""
import pytest

from tests.integration.endpoint_fixtures import seed_endpoint, seed_endpoint_type
from tests.integration.test_abac_authorization import AbacFixtures

USER_OID = "endpoint-scope-oid"
READ = ["device.endpoint.read"]
WRITE = ["device.endpoint.read", "device.endpoint.write"]
STRING_FIELD = {"type": "string", "label": "Serial"}


class TestEndpointRoutesDeviceScope:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": USER_OID,
            "sub": USER_OID,
            "preferred_username": "endpoint-scope@test.com",
            "name": "Endpoint Scope",
            "roles": [],
        }

    async def _world(self, db_session, actions, scoped=True):
        await AbacFixtures(db_session).setup(
            users={"tester": USER_OID},
            scopes={"eu": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
            roles={"r": actions},
            teams={"team": {"scope": "eu" if scoped else None, "roles": ["r"], "users": ["tester"]}},
            devices={"eu-dev": {"region": "EU"}, "us-dev": {"region": "US"}},
        )
        type_id = await seed_endpoint_type(db_session, fields={"serial": STRING_FIELD})
        eu_endpoint = await seed_endpoint(db_session, "eu-dev", type_id, {"serial": "EU-1"})
        us_endpoint = await seed_endpoint(db_session, "us-dev", type_id, {"serial": "US-1"})
        return type_id, eu_endpoint, us_endpoint

    async def test_list_on_device_in_scope(self, client, db_session):
        _, eu_endpoint, _ = await self._world(db_session, READ)
        response = await client.get("/endpoints?device_id=eu-dev")
        assert response.status_code == 200
        assert [e["endpoint_id"] for e in response.json()] == [eu_endpoint]

    async def test_list_on_device_out_of_scope_is_403(self, client, db_session):
        await self._world(db_session, READ)
        assert (await client.get("/endpoints?device_id=us-dev")).status_code == 403

    async def test_get_by_id_follows_the_endpoint_device(self, client, db_session):
        _, eu_endpoint, us_endpoint = await self._world(db_session, READ)
        assert (await client.get(f"/endpoints/{eu_endpoint}")).status_code == 200
        assert (await client.get(f"/endpoints/{us_endpoint}")).status_code == 403

    async def test_get_missing_endpoint_is_404(self, client, db_session):
        await self._world(db_session, READ)
        assert (await client.get("/endpoints/does-not-exist")).status_code == 404

    async def test_create_follows_the_device_query_param(self, client, db_session):
        type_id, _, _ = await self._world(db_session, WRITE)
        body = {"type_id": type_id, "endpoint_data": {"serial": "NEW"}}
        assert (await client.post("/endpoints?device_id=eu-dev", json=body)).status_code == 201
        assert (await client.post("/endpoints?device_id=us-dev", json=body)).status_code == 403

    async def test_reader_cannot_create(self, client, db_session):
        type_id, _, _ = await self._world(db_session, READ)
        body = {"type_id": type_id, "endpoint_data": {}}
        assert (await client.post("/endpoints?device_id=eu-dev", json=body)).status_code == 403

    async def test_patch_follows_the_endpoint_device(self, client, db_session):
        _, eu_endpoint, us_endpoint = await self._world(db_session, WRITE)
        body = {"endpoint_data": {"serial": "CHANGED"}}
        assert (await client.patch(f"/endpoints/{eu_endpoint}", json=body)).status_code == 200
        assert (await client.patch(f"/endpoints/{us_endpoint}", json=body)).status_code == 403
        assert (await client.patch("/endpoints/does-not-exist", json=body)).status_code == 404

    async def test_delete_follows_the_endpoint_device(self, client, db_session):
        _, eu_endpoint, us_endpoint = await self._world(db_session, WRITE)
        assert (await client.delete(f"/endpoints/{us_endpoint}")).status_code == 403
        assert (await client.delete(f"/endpoints/{eu_endpoint}")).status_code == 204
        assert (await client.delete("/endpoints/does-not-exist")).status_code == 404

    async def test_unscoped_team_reaches_every_device(self, client, db_session):
        await self._world(db_session, READ, scoped=False)
        assert (await client.get("/endpoints?device_id=us-dev")).status_code == 200


class TestEndpointRoutesMissingRows:
    """Admins (the default test user) pass DeviceAccess even for a device that doesn't exist,
    so the routes themselves must answer 404 instead of failing on a foreign key."""

    async def test_create_on_nonexistent_device_is_404(self, client, db_session):
        type_id = await seed_endpoint_type(db_session, fields={"serial": STRING_FIELD})
        body = {"type_id": type_id, "endpoint_data": {}}
        assert (await client.post("/endpoints?device_id=no-such-device", json=body)).status_code == 404

    async def test_by_id_routes_on_missing_endpoint_are_404_for_admin(self, client):
        assert (await client.get("/endpoints/does-not-exist")).status_code == 404
        assert (
            await client.patch("/endpoints/does-not-exist", json={"endpoint_data": {}})
        ).status_code == 404
        assert (await client.delete("/endpoints/does-not-exist")).status_code == 404
