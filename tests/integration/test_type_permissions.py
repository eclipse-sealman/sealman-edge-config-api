"""Type management permissions (spec §3): writes need platform.config.write, reads of
endpoint/service types stay at device.endpoint.read, /device-types needs platform.config.*."""
import pytest

from tests.integration.endpoint_fixtures import seed_endpoint_type, seed_service_type, unique_label
from tests.integration.test_abac_authorization import AbacFixtures

USER_OID = "types-perm-oid"


class _NonAdmin:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": USER_OID,
            "sub": USER_OID,
            "preferred_username": "types-perm@test.com",
            "name": "Types Perm",
            "roles": [],
        }


async def _grant(db_session, *actions):
    await AbacFixtures(db_session).setup(
        users={"tester": USER_OID},
        roles={"r": list(actions)},
        teams={"t": {"roles": ["r"], "users": ["tester"]}},
    )


class TestEndpointTypePermissions(_NonAdmin):
    async def test_endpoint_write_cannot_create(self, client, db_session):
        await _grant(db_session, "device.endpoint.read", "device.endpoint.write")
        response = await client.post("/endpoint-types", json={"label": unique_label()})
        assert response.status_code == 403

    async def test_endpoint_write_cannot_patch_or_delete(self, client, db_session):
        await _grant(db_session, "device.endpoint.read", "device.endpoint.write")
        type_id = await seed_endpoint_type(db_session)
        assert (await client.patch(f"/endpoint-types/{type_id}", json={"label": unique_label()})).status_code == 403
        assert (await client.delete(f"/endpoint-types/{type_id}")).status_code == 403

    async def test_platform_config_write_can_create_patch_and_delete(self, client, db_session):
        await _grant(db_session, "platform.config.write")
        created = await client.post("/endpoint-types", json={"label": unique_label()})
        assert created.status_code == 201, created.text
        type_id = created.json()["type_id"]
        assert (await client.patch(f"/endpoint-types/{type_id}", json={"label": unique_label()})).status_code == 200
        assert (await client.delete(f"/endpoint-types/{type_id}")).status_code == 204

    async def test_endpoint_read_still_lists_and_gets(self, client, db_session):
        await _grant(db_session, "device.endpoint.read")
        type_id = await seed_endpoint_type(db_session)
        assert (await client.get("/endpoint-types")).status_code == 200
        assert (await client.get(f"/endpoint-types/{type_id}")).status_code == 200

    async def test_platform_config_write_alone_cannot_list(self, client, db_session):
        await _grant(db_session, "platform.config.write")
        assert (await client.get("/endpoint-types")).status_code == 403


class TestServiceTypePermissions(_NonAdmin):
    async def test_endpoint_write_cannot_create(self, client, db_session):
        await _grant(db_session, "device.endpoint.read", "device.endpoint.write")
        response = await client.post("/service-types", json={"label": unique_label()})
        assert response.status_code == 403

    async def test_endpoint_write_cannot_patch_or_delete(self, client, db_session):
        await _grant(db_session, "device.endpoint.read", "device.endpoint.write")
        type_id = await seed_service_type(db_session)
        assert (await client.patch(f"/service-types/{type_id}", json={"label": unique_label()})).status_code == 403
        assert (await client.delete(f"/service-types/{type_id}")).status_code == 403

    async def test_platform_config_write_can_create_patch_and_delete(self, client, db_session):
        await _grant(db_session, "platform.config.write")
        created = await client.post("/service-types", json={"label": unique_label()})
        assert created.status_code == 201, created.text
        type_id = created.json()["type_id"]
        assert (await client.patch(f"/service-types/{type_id}", json={"label": unique_label()})).status_code == 200
        assert (await client.delete(f"/service-types/{type_id}")).status_code == 204

    async def test_endpoint_read_still_lists_and_gets(self, client, db_session):
        await _grant(db_session, "device.endpoint.read")
        type_id = await seed_service_type(db_session)
        assert (await client.get("/service-types")).status_code == 200
        assert (await client.get(f"/service-types/{type_id}")).status_code == 200

    async def test_platform_config_write_alone_cannot_list(self, client, db_session):
        await _grant(db_session, "platform.config.write")
        assert (await client.get("/service-types")).status_code == 403


class TestDeviceTypePermissions(_NonAdmin):
    async def test_without_platform_config_read_cannot_list_or_get(self, client, db_session):
        await _grant(db_session, "device.read")
        assert (await client.get("/device-types")).status_code == 403
        assert (await client.get("/device-types/default")).status_code == 403

    async def test_platform_config_read_can_list_and_get(self, client, db_session):
        await _grant(db_session, "platform.config.read")
        assert (await client.get("/device-types")).status_code == 200
        assert (await client.get("/device-types/default")).status_code == 200

    async def test_platform_config_read_cannot_write(self, client, db_session):
        await _grant(db_session, "platform.config.read")
        assert (await client.post("/device-types", json={"label": unique_label()})).status_code == 403
        assert (await client.patch("/device-types/default", json={"label": unique_label()})).status_code == 403
        assert (await client.delete("/device-types/default")).status_code == 403

    async def test_platform_config_write_can_create_patch_and_delete(self, client, db_session):
        await _grant(db_session, "platform.config.write")
        created = await client.post("/device-types", json={"label": unique_label()})
        assert created.status_code == 201, created.text
        type_id = created.json()["type_id"]
        assert (await client.patch(f"/device-types/{type_id}", json={"label": unique_label()})).status_code == 200
        assert (await client.delete(f"/device-types/{type_id}")).status_code == 204
