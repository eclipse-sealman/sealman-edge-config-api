"""Platform functions: global network scan switch and its enforcement on the network endpoints."""
import pytest

from tests.integration.test_abac_authorization import AbacFixtures

DEVICE = "functions-dev"
EMPTY_SCAN = {
    "scanResults": [],
    "scanDefinition": {"networkDefinition": "0.0.0.0", "subnetMask": 32, "ports": []},
}
DISCOVER = {"networkDefinition": "10.0.0.0", "subnetMask": 24, "ports": [80]}

SCAN_REQUESTS = [
    ("post", f"/{DEVICE}/network/discover", DISCOVER),
    ("get", f"/{DEVICE}/network/topology", None),
    ("post", f"/{DEVICE}/network/overview", EMPTY_SCAN),
    ("get", f"/{DEVICE}/network/scan-ports", None),
    ("post", f"/{DEVICE}/network/scan-ports", {"ports": [8080]}),
    ("get", f"/{DEVICE}/network/scan-range", None),
    ("get", "/network/default-scan-ports", None),
    ("post", "/network/default-scan-ports", {"port": 8080}),
    ("delete", "/network/default-scan-ports/8080", None),
    ("post", f"/{DEVICE}/seal-app-net-discover/methods", {"methodName": "scanNetwork2", "methodPayload": {}}),
]


async def _send(client, method, path, body):
    if body is None:
        return await client.request(method, path)
    return await client.request(method, path, json=body)


async def _set_network_scan(client, enabled: bool):
    response = await client.put("/platform/functions", json={"network_scan_enabled": enabled})
    assert response.status_code == 200, response.text
    assert response.json() == {"network_scan_enabled": enabled}


class TestSettings:
    async def test_network_scan_enabled_by_default(self, client):
        response = await client.get("/platform/functions")
        assert response.status_code == 200
        assert response.json() == {"network_scan_enabled": True}

    async def test_toggle_is_persisted(self, client):
        await _set_network_scan(client, False)
        assert (await client.get("/platform/functions")).json() == {"network_scan_enabled": False}
        await _set_network_scan(client, True)
        assert (await client.get("/platform/functions")).json() == {"network_scan_enabled": True}

    async def test_rejects_invalid_body(self, client):
        assert (await client.put("/platform/functions", json={})).status_code == 400


class TestDisabledNetworkScan:
    @pytest.mark.parametrize("method,path,body", SCAN_REQUESTS)
    async def test_scan_endpoints_are_rejected(self, client, db_session, method, path, body):
        await AbacFixtures(db_session).setup(devices={DEVICE: {}})
        await _set_network_scan(client, False)

        response = await _send(client, method, path, body)

        assert response.status_code == 403, response.text
        assert "disabled" in response.text

    async def test_configured_assets_stay_readable(self, client, db_session):
        await AbacFixtures(db_session).setup(devices={DEVICE: {}})
        await _set_network_scan(client, False)

        response = await client.get(f"/{DEVICE}/network/last-known")

        assert response.status_code == 200, response.text

    async def test_other_modules_methods_are_not_affected(self, client, db_session, monkeypatch):
        import routers.general.router as general_router

        async def _fake_post_module_method(device, module, method_data, auth_context):
            return {"status": 200, "payload": {"module": module}}

        monkeypatch.setattr(general_router, "_post_module_method", _fake_post_module_method)
        await AbacFixtures(db_session).setup(devices={DEVICE: {}})
        await _set_network_scan(client, False)

        response = await client.post(f"/{DEVICE}/seal-cmd-proxy/methods", json={"methodName": "ping", "methodPayload": {}})

        assert response.status_code == 200, response.text

    async def test_reenabling_restores_scan_endpoints(self, client, db_session):
        await AbacFixtures(db_session).setup(devices={DEVICE: {}})
        await _set_network_scan(client, False)
        await _set_network_scan(client, True)

        assert (await client.get("/network/default-scan-ports")).status_code == 200
        assert (await client.post(f"/{DEVICE}/network/overview", json=EMPTY_SCAN)).status_code == 200


class TestPermissions:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "functions-viewer-oid",
            "sub": "functions-viewer-oid",
            "preferred_username": "functions-viewer@test.com",
            "name": "Viewer",
            "roles": [],
        }

    async def test_every_user_can_read(self, client):
        assert (await client.get("/platform/functions")).status_code == 200

    async def test_write_requires_platform_config_write(self, client):
        response = await client.put("/platform/functions", json={"network_scan_enabled": False})
        assert response.status_code == 403
