"""Authorization enforcement for network operations, SEMS info and module status."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import unquote, urlsplit

import pytest
from sqlalchemy import func, select

from authorization.permission_types import Device, Platform
from db.models.host_status import DeviceHostStatus, DevicePortStatus
from tests.integration.test_abac_authorization import AbacFixtures


USER_OID = "authorization-enforcement-oid"
SCAN_DEFINITION = {"networkDefinition": "192.0.2.1", "subnetMask": 32, "ports": [443]}
SCAN = {"scanDefinition": SCAN_DEFINITION, "scanResults": []}
OVERVIEW = {"scanDefinition": SCAN_DEFINITION, "endpoints": []}
NETWORK_ROUTES = [
    ("GET", "topology", None, "_get_network_topology", None, {Device.ENDPOINT_READ}),
    ("GET", "last-known", None, "_build_last_known_network_overview", OVERVIEW, {Device.ENDPOINT_READ}),
    ("GET", "scan-ports", None, "_get_network_scan_ports", [], {Device.ENDPOINT_READ}),
    ("GET", "scan-range", None, "_get_network_scan_range", None, {Device.ENDPOINT_READ, Device.NETWORK_DISCOVER}),
    ("POST", "overview", SCAN, "_build_network_overview", OVERVIEW, {Device.ENDPOINT_READ, Device.NETWORK_DISCOVER}),
    ("POST", "discover", SCAN_DEFINITION, "_post_network_discover2", {"status": 200, "payload": SCAN}, {Device.NETWORK_DISCOVER}),
    ("POST", "scan-ports", {"ports": [443]}, "_post_device_scan_ports", [443], {Device.NETWORK_WRITE}),
]


@pytest.fixture
def fake_jwt_user():
    return {
        "oid": USER_OID,
        "sub": USER_OID,
        "preferred_username": "authorization-enforcement@example.test",
        "name": "Authorization Enforcement",
        "roles": [],
    }


async def _setup_scoped_user(db_session, actions):
    await AbacFixtures(db_session).setup(
        users={"tester": USER_OID},
        scopes={"eu": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
        roles={"r": list(actions)},
        teams={"t": {"scope": "eu", "roles": ["r"], "users": ["tester"]}},
        devices={"eu-dev": {"region": "EU"}, "us-dev": {"region": "US"}},
    )


@pytest.mark.parametrize("method,path,body,handler_name,result,required", NETWORK_ROUTES)
@pytest.mark.parametrize("actions", [
    [Device.READ],
    [Device.ENDPOINT_READ],
    [Device.NETWORK_DISCOVER],
    [Device.ENDPOINT_READ, Device.NETWORK_DISCOVER],
    [Device.NETWORK_WRITE],
])
async def test_network_action_matrix(
    client, db_session, monkeypatch, method, path, body, handler_name, result, required, actions
):
    import routers.network_discovery.router as router

    await _setup_scoped_user(db_session, actions)
    handler = AsyncMock(return_value=result)
    monkeypatch.setattr(router, handler_name, handler)
    response = await client.request(method, f"/eu-dev/network/{path}", json=body)
    if required.issubset(actions):
        assert response.status_code == 200, response.text
        handler.assert_awaited_once()
    else:
        assert response.status_code == 403, response.text
        handler.assert_not_awaited()


@pytest.mark.parametrize("method,path,body,handler_name,result,required", NETWORK_ROUTES)
async def test_network_actions_remain_device_scoped(
    client, db_session, monkeypatch, method, path, body, handler_name, result, required
):
    import routers.network_discovery.router as router

    await _setup_scoped_user(db_session, required)
    handler = AsyncMock(return_value=result)
    monkeypatch.setattr(router, handler_name, handler)
    response = await client.request(method, f"/us-dev/network/{path}", json=body)
    assert response.status_code == 403, response.text
    handler.assert_not_awaited()


@pytest.mark.parametrize("method,path,body,handler_name,result,required", NETWORK_ROUTES)
async def test_network_admin_bypass(
    client, fake_jwt_user, monkeypatch, method, path, body, handler_name, result, required
):
    import routers.network_discovery.router as router

    fake_jwt_user["roles"] = ["user.admin"]
    handler = AsyncMock(return_value=result)
    monkeypatch.setattr(router, handler_name, handler)
    response = await client.request(method, f"/any-device/network/{path}", json=body)
    assert response.status_code == 200, response.text
    handler.assert_awaited_once()


@pytest.mark.parametrize("method,path,body,handler_name", [
    ("GET", "scan-range", None, "_get_network_scan_range"),
    ("POST", "overview", SCAN, "_build_network_overview"),
])
@pytest.mark.parametrize("device", ["eu-dev", "us-dev"])
async def test_combined_network_actions_each_require_matching_scope(
    client, db_session, monkeypatch, method, path, body, handler_name, device
):
    import routers.network_discovery.router as router

    await AbacFixtures(db_session).setup(
        users={"tester": USER_OID},
        scopes={
            "eu": {"attr": {"region": "EU"}, "access_rule": "ALL"},
            "us": {"attr": {"region": "US"}, "access_rule": "ALL"},
        },
        roles={"read": [Device.ENDPOINT_READ], "discover": [Device.NETWORK_DISCOVER]},
        teams={
            "read-us": {"scope": "us", "roles": ["read"], "users": ["tester"]},
            "discover-eu": {"scope": "eu", "roles": ["discover"], "users": ["tester"]},
        },
        devices={"eu-dev": {"region": "EU"}, "us-dev": {"region": "US"}},
    )
    handler = AsyncMock()
    monkeypatch.setattr(router, handler_name, handler)
    assert (await client.request(method, f"/{device}/network/{path}", json=body)).status_code == 403
    handler.assert_not_awaited()


@pytest.mark.parametrize("authorized", [False, True])
async def test_overview_persistence_requires_both_actions(client, db_session, authorized):
    actions = [Device.ENDPOINT_READ, Device.NETWORK_DISCOVER] if authorized else [Device.READ]
    await _setup_scoped_user(db_session, actions)
    scan = {
        "scanDefinition": {"networkDefinition": "192.0.2.77", "subnetMask": 32, "ports": [48077]},
        "scanResults": [{"ip": "192.0.2.77", "status": "online", "ports": {"48077": {"status": "online"}}}],
    }
    response = await client.post("/eu-dev/network/overview", json=scan)
    assert response.status_code == (200 if authorized else 403), response.text
    for model in (DeviceHostStatus, DevicePortStatus):
        count = await db_session.scalar(select(func.count()).select_from(model).where(model.device_id == "eu-dev"))
        assert count == (1 if authorized else 0)


@pytest.mark.parametrize("actions", [
    [Device.READ],
    [Platform.CONFIG_READ, Platform.CONFIG_WRITE],
    [Platform.AUTHORIZATION_READ, Platform.AUTHORIZATION_WRITE],
])
async def test_sems_info_requires_admin_not_abac_actions(client, db_session, monkeypatch, actions):
    import routers.smart_ems.router as router

    await _setup_scoped_user(db_session, actions)
    handler = AsyncMock()
    monkeypatch.setattr(router, "_get_smart_ems_device_info", handler)
    assert (await client.get("/eu-dev/smartems/info")).status_code == 403
    handler.assert_not_awaited()


async def test_admin_can_read_sems_info_without_device_read(client, fake_jwt_user, monkeypatch):
    import routers.smart_ems.router as router

    fake_jwt_user["roles"] = ["user.admin"]
    info = {
        "enabled": True, "lastSeenAt": "", "hardwareVersion": "", "updateFirmware": False,
        "semsTemplate": "", "firmwareVersion": "", "deviceTypeId": 9, "deviceTypeName": "Test",
        "description": "", "variables": [{"name": "sample", "variableValue": "synthetic"}],
    }
    handler = AsyncMock(return_value=info)
    monkeypatch.setattr(router, "_get_smart_ems_device_info", handler)
    response = await client.get("/any-device/smartems/info")
    assert response.status_code == 200, response.text
    assert response.json()["variables"] == info["variables"]
    handler.assert_awaited_once_with("any-device")


@pytest.mark.parametrize("modules", [
    ["../outside/modules/other"],
    ["../../us-dev/modules/$edgeAgent"],
    ["."],
    [".."],
    ["valid", "../outside"],
    ["bad\\module"],
    ["bad#fragment"],
])
async def test_config_status_rejects_malformed_modules_before_hub_calls(
    client, db_session, monkeypatch, modules
):
    import routers.module_config.routes.post_module_config_status as status

    await _setup_scoped_user(db_session, [Device.READ])
    hub = AsyncMock()
    monkeypatch.setattr(status, "get_async", hub)
    response = await client.post("/eu-dev/config/status", json={"modules": modules})
    assert response.status_code == 400, response.text
    hub.assert_not_awaited()


@pytest.mark.parametrize("device", ["eu-dev", "us-dev"])
async def test_valid_config_status_keeps_read_action_scope_and_system_modules(
    client, db_session, monkeypatch, device
):
    import routers.module_config.routes.post_module_config_status as status

    await _setup_scoped_user(db_session, [Device.READ])
    urls = []

    async def get(url, responses, **kwargs):
        urls.append(url)
        responses[url] = SimpleNamespace(status_code=200, json=lambda: {
            "moduleId": unquote(urlsplit(url).path.split("/")[-1]),
            "properties": {"desired": {}, "reported": {}},
        })

    monkeypatch.setattr(status, "get_async", get)
    monkeypatch.setattr(status, "get_iothub_auth_headers", lambda: {})
    response = await client.post(f"/{device}/config/status", json={"modules": ["$edgeAgent", "$edgeHub"]})
    if device == "eu-dev":
        assert response.status_code == 200, response.text
        assert set(response.json()) == {"$edgeAgent", "$edgeHub"}
        assert [urlsplit(url).path for url in urls] == [
            "/twins/eu-dev/modules/%24edgeAgent", "/twins/eu-dev/modules/%24edgeHub",
        ]
    else:
        assert response.status_code == 403
        assert not urls
