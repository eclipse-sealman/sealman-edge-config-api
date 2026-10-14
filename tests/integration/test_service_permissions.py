"""Per-device checks on /services: service -> endpoint -> device (spec §3)."""
import pytest

from tests.integration.endpoint_fixtures import (
    seed_endpoint,
    seed_endpoint_type,
    seed_service,
    seed_service_type,
)
from tests.integration.test_abac_authorization import AbacFixtures

USER_OID = "service-scope-oid"
READ = ["device.endpoint.read"]
WRITE = ["device.endpoint.read", "device.endpoint.write"]
STRING_FIELD = {"type": "string", "label": "Protocol"}


class TestServiceRoutesDeviceScope:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": USER_OID,
            "sub": USER_OID,
            "preferred_username": "service-scope@test.com",
            "name": "Service Scope",
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
        endpoint_type = await seed_endpoint_type(db_session)
        service_type = await seed_service_type(db_session, fields={"protocol": STRING_FIELD})
        eu_endpoint = await seed_endpoint(db_session, "eu-dev", endpoint_type)
        us_endpoint = await seed_endpoint(db_session, "us-dev", endpoint_type)
        eu_service = await seed_service(db_session, eu_endpoint, service_type, {"protocol": "a"})
        us_service = await seed_service(db_session, us_endpoint, service_type, {"protocol": "b"})
        return {
            "service_type": service_type,
            "eu_endpoint": eu_endpoint, "us_endpoint": us_endpoint,
            "eu_service": eu_service, "us_service": us_service,
        }

    async def test_list_follows_the_endpoint_device(self, client, db_session):
        w = await self._world(db_session, READ)
        in_scope = await client.get(f"/services?endpoint_id={w['eu_endpoint']}")
        assert in_scope.status_code == 200
        assert [s["service_id"] for s in in_scope.json()] == [w["eu_service"]]
        assert (await client.get(f"/services?endpoint_id={w['us_endpoint']}")).status_code == 403
        assert (await client.get("/services?endpoint_id=does-not-exist")).status_code == 404

    async def test_unscoped_team_lists_services_of_every_device(self, client, db_session):
        w = await self._world(db_session, READ, scoped=False)
        for endpoint_key, service_key in (("eu_endpoint", "eu_service"), ("us_endpoint", "us_service")):
            response = await client.get(f"/services?endpoint_id={w[endpoint_key]}")
            assert response.status_code == 200, response.text
            assert [s["service_id"] for s in response.json()] == [w[service_key]]

    async def test_create_follows_the_endpoint_device(self, client, db_session):
        w = await self._world(db_session, WRITE)
        body = {"type_id": w["service_type"], "service_data": {"protocol": "c"}}
        assert (await client.post(f"/services?endpoint_id={w['eu_endpoint']}", json=body)).status_code == 201
        assert (await client.post(f"/services?endpoint_id={w['us_endpoint']}", json=body)).status_code == 403
        assert (await client.post("/services?endpoint_id=does-not-exist", json=body)).status_code == 404

    async def test_reader_cannot_create(self, client, db_session):
        w = await self._world(db_session, READ)
        body = {"type_id": w["service_type"], "service_data": {}}
        assert (await client.post(f"/services?endpoint_id={w['eu_endpoint']}", json=body)).status_code == 403

    async def test_get_by_id_follows_the_device(self, client, db_session):
        w = await self._world(db_session, READ)
        assert (await client.get(f"/services/{w['eu_service']}")).status_code == 200
        assert (await client.get(f"/services/{w['us_service']}")).status_code == 403
        assert (await client.get("/services/does-not-exist")).status_code == 404

    async def test_patch_follows_the_device(self, client, db_session):
        w = await self._world(db_session, WRITE)
        body = {"service_data": {"protocol": "z"}}
        assert (await client.patch(f"/services/{w['eu_service']}", json=body)).status_code == 200
        assert (await client.patch(f"/services/{w['us_service']}", json=body)).status_code == 403
        assert (await client.patch("/services/does-not-exist", json=body)).status_code == 404

    async def test_delete_follows_the_device(self, client, db_session):
        w = await self._world(db_session, WRITE)
        assert (await client.delete(f"/services/{w['us_service']}")).status_code == 403
        assert (await client.delete(f"/services/{w['eu_service']}")).status_code == 204
        assert (await client.delete("/services/does-not-exist")).status_code == 404


class TestServiceRoutesMissingRows:
    """Admins (the default test user) pass DeviceAccess even for unknown devices, so the
    routes themselves must answer 404 instead of failing on a foreign key."""

    async def test_create_on_nonexistent_endpoint_is_404(self, client, db_session):
        type_id = await seed_service_type(db_session, fields={"protocol": STRING_FIELD})
        body = {"type_id": type_id, "service_data": {}}
        assert (await client.post("/services?endpoint_id=no-such-endpoint", json=body)).status_code == 404

    async def test_list_and_by_id_routes_on_missing_rows_are_404_for_admin(self, client):
        assert (await client.get("/services?endpoint_id=no-such-endpoint")).status_code == 404
        assert (await client.get("/services/does-not-exist")).status_code == 404
        assert (
            await client.patch("/services/does-not-exist", json={"service_data": {}})
        ).status_code == 404
        assert (await client.delete("/services/does-not-exist")).status_code == 404
