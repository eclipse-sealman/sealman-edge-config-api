import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from extensions.upstreams import iotedge


def _make_request(method="POST", path_params=None, query_params=b"", body=b""):
    scope = {
        "type": "http",
        "method": method,
        "path": "/x",
        "raw_path": b"/x",
        "query_string": query_params,
        "headers": [],
        "path_params": path_params or {},
    }
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _fake_response(status_code=200, json_body=None, text=""):
    return SimpleNamespace(status_code=status_code, json=lambda: json_body, text=text)


@pytest.mark.asyncio
async def test_direct_method_uses_2021_04_12_and_forwards_device_embedded_status(monkeypatch):
    captured = {}

    async def fake_post_async(url, responses, _json=None, headers=None, timeout=5):
        captured["url"] = url
        captured["json"] = _json
        responses[url] = _fake_response(200, {"status": 500, "payload": {"error": "device failed"}})

    monkeypatch.setattr(iotedge, "post_async", fake_post_async)
    monkeypatch.setattr(iotedge, "get_iothub_auth_headers", lambda: {"Authorization": "SharedAccessSignature x"})
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")

    response = await iotedge._direct_method("device-1", "module-1", "restart", {"foo": "bar"})

    assert "api-version=2021-04-12" in captured["url"]
    assert "device-1" in captured["url"] and "module-1" in captured["url"]
    assert captured["json"]["methodName"] == "restart"
    assert captured["json"]["payload"] == {"foo": "bar"}
    # IoT Hub's own REST call succeeded (200), but the device's own embedded status (500)
    # must be what this route forwards as its own response status.
    assert response.status_code == 500
    assert json.loads(bytes(response.body)) == {"error": "device failed"}


@pytest.mark.asyncio
async def test_direct_method_raises_when_iothub_rest_call_itself_fails(monkeypatch):
    async def fake_post_async(url, responses, _json=None, headers=None, timeout=5):
        responses[url] = _fake_response(404, text="device not found")

    monkeypatch.setattr(iotedge, "post_async", fake_post_async)
    monkeypatch.setattr(iotedge, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")

    with pytest.raises(Exception):
        await iotedge._direct_method("device-1", "module-1", "restart", None)


@pytest.mark.asyncio
async def test_twin_read_calls_expected_url(monkeypatch):
    captured = {}

    async def fake_get_async(url, responses, headers=None, timeout=5):
        captured["url"] = url
        responses[url] = _fake_response(200, {"properties": {"reported": {}}})

    monkeypatch.setattr(iotedge, "get_async", fake_get_async)
    monkeypatch.setattr(iotedge, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")

    result = await iotedge._twin_read("device-1", "module-1")

    assert captured["url"] == (
        "https://myhub.azure-devices.net/twins/device-1/modules/module-1?api-version=2021-04-12"
    )
    assert result == {"properties": {"reported": {}}}


@pytest.mark.asyncio
async def test_twin_write_uploads_to_blob_and_patches_only_config_pointer(monkeypatch):
    patch_captured = {}

    async def fake_patch_async(url, responses, _json=None, headers=None, timeout=5):
        patch_captured["url"] = url
        patch_captured["json"] = _json
        responses[url] = _fake_response(200, {})

    uploaded = {}

    class FakeBlobClient:
        async def upload_blob(self, data, overwrite=True):
            uploaded["data"] = data

    class FakeContainer:
        def get_blob_client(self, path):
            uploaded["path"] = path
            return FakeBlobClient()

    class FakeContainerContext:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return FakeContainer()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(iotedge, "patch_async", fake_patch_async)
    monkeypatch.setattr(iotedge, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")
    monkeypatch.setattr(iotedge, "PUBLIC_STORAGE_ACCOUNT_NAME", "mystorage")
    monkeypatch.setattr(iotedge, "BLOB_SAS_TOKEN_MODULE_CONF", "sv=2021&sig=abc")
    monkeypatch.setattr(iotedge, "BlobContainerContext", FakeContainerContext)

    result = await iotedge._twin_write("device-1", "module-1", {"setting": "value"})

    assert json.loads(uploaded["data"]) == {"setting": "value"}
    desired = patch_captured["json"]["properties"]["desired"]
    assert set(desired.keys()) == {"configBlobUrl", "configId"}
    assert result == {"configId": desired["configId"]}


@pytest.mark.asyncio
async def test_probe_registration_health_reads_runtime_status_not_configurations(monkeypatch):
    async def fake_post_async(url, responses, _json=None, headers=None, timeout=5):
        responses[url] = _fake_response(200, [{"deviceId": "canary-1"}])

    async def fake_get_async(url, responses, headers=None, timeout=5):
        responses[url] = _fake_response(
            200,
            {
                "properties": {
                    "reported": {
                        "modules": {"other-module": {"runtimeStatus": "running"}},
                        "configurations": {"deploy-1": {"status": "Applied"}},
                    }
                }
            },
        )

    monkeypatch.setattr(iotedge, "post_async", fake_post_async)
    monkeypatch.setattr(iotedge, "get_async", fake_get_async)
    monkeypatch.setattr(iotedge, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")

    warning = await iotedge.probe_registration_health("my-module", "tags.env = 'prod'")

    assert warning is not None
    assert "my-module" in warning


@pytest.mark.asyncio
async def test_probe_registration_health_no_warning_when_module_present_with_runtime_status(monkeypatch):
    async def fake_post_async(url, responses, _json=None, headers=None, timeout=5):
        responses[url] = _fake_response(200, [{"deviceId": "canary-1"}])

    async def fake_get_async(url, responses, headers=None, timeout=5):
        responses[url] = _fake_response(
            200,
            {"properties": {"reported": {"modules": {"my-module": {"runtimeStatus": "running"}}}}},
        )

    monkeypatch.setattr(iotedge, "post_async", fake_post_async)
    monkeypatch.setattr(iotedge, "get_async", fake_get_async)
    monkeypatch.setattr(iotedge, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")

    warning = await iotedge.probe_registration_health("my-module", "tags.env = 'prod'")

    assert warning is None


@pytest.mark.asyncio
async def test_probe_registration_health_never_raises_on_exception(monkeypatch):
    async def fake_post_async(*a, **kw):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(iotedge, "post_async", fake_post_async)
    monkeypatch.setattr(iotedge, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")

    warning = await iotedge.probe_registration_health("my-module", "tags.env = 'prod'")
    assert warning is None


def test_resolve_device_id_scoped_from_path_param():
    request = _make_request(path_params={"device_id": "dev-42"})
    route = {"scoped": True, "scope_in": "path", "scope_param": "device_id"}
    assert iotedge._resolve_device_id(request, route) == "dev-42"


def test_resolve_device_id_scoped_from_query_param():
    request = _make_request(query_params=b"device_id=dev-77")
    route = {"scoped": True, "scope_in": "query", "scope_param": "device_id"}
    assert iotedge._resolve_device_id(request, route) == "dev-77"


def test_resolve_device_id_missing_raises_400():
    from fastapi import HTTPException

    request = _make_request()
    route = {"scoped": True, "scope_in": "query", "scope_param": "device_id"}
    with pytest.raises(HTTPException) as exc_info:
        iotedge._resolve_device_id(request, route)
    assert exc_info.value.status_code == 400


def test_resolve_device_id_rejects_characters_outside_iothub_rules():
    from fastapi import HTTPException

    request = _make_request(query_params=b"device_id=dev1%2Fmodules%2F%24edgeAgent%26x%3D")
    route = {"scoped": True, "scope_in": "query", "scope_param": "device_id"}
    with pytest.raises(HTTPException) as exc_info:
        iotedge._resolve_device_id(request, route)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_direct_method_rejects_payload_above_iothub_limit():
    from fastapi import HTTPException

    request = _make_request(query_params=b"device_id=dev-1")
    upstream = {"module_name": "module-1"}
    route = {"iotedge_operation": "direct_method", "method_name": "restart"}
    body = b'{"x": "' + b"a" * iotedge._DIRECT_METHOD_MAX_PAYLOAD_BYTES + b'"}'
    with pytest.raises(HTTPException) as exc_info:
        await iotedge.dispatch(request, upstream, route, body=body)
    assert exc_info.value.status_code == 413


def test_twin_url_percent_encodes_allowed_special_characters(monkeypatch):
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")
    url = iotedge._twin_url("dev?1$", "module-1")
    assert url == "https://myhub.azure-devices.net/twins/dev%3F1%24/modules/module-1?api-version=2021-04-12"


@pytest.mark.asyncio
async def test_check_module_health_unknown_when_no_query_configured():
    status, detail = await iotedge.check_module_health("my-module", None)
    assert status == "unknown"
    assert detail


@pytest.mark.asyncio
async def test_check_module_health_unknown_when_query_resolves_to_zero_devices(monkeypatch):
    async def fake_post_async(url, responses, _json=None, headers=None, timeout=5):
        responses[url] = _fake_response(200, [])

    monkeypatch.setattr(iotedge, "post_async", fake_post_async)
    monkeypatch.setattr(iotedge, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")

    status, detail = await iotedge.check_module_health("my-module", "tags.env = 'prod'")
    assert status == "unknown"
    assert detail


@pytest.mark.asyncio
async def test_check_module_health_unknown_when_twin_unreadable(monkeypatch):
    async def fake_post_async(url, responses, _json=None, headers=None, timeout=5):
        responses[url] = _fake_response(200, [{"deviceId": "canary-1"}])

    async def fake_get_async(url, responses, headers=None, timeout=5):
        responses[url] = _fake_response(404, text="not found")

    monkeypatch.setattr(iotedge, "post_async", fake_post_async)
    monkeypatch.setattr(iotedge, "get_async", fake_get_async)
    monkeypatch.setattr(iotedge, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")

    status, detail = await iotedge.check_module_health("my-module", "tags.env = 'prod'")
    assert status == "unknown"
    assert detail


@pytest.mark.asyncio
async def test_check_module_health_healthy_when_running(monkeypatch):
    async def fake_post_async(url, responses, _json=None, headers=None, timeout=5):
        responses[url] = _fake_response(200, [{"deviceId": "canary-1"}])

    async def fake_get_async(url, responses, headers=None, timeout=5):
        responses[url] = _fake_response(
            200, {"properties": {"reported": {"modules": {"my-module": {"runtimeStatus": "running"}}}}}
        )

    monkeypatch.setattr(iotedge, "post_async", fake_post_async)
    monkeypatch.setattr(iotedge, "get_async", fake_get_async)
    monkeypatch.setattr(iotedge, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")

    status, detail = await iotedge.check_module_health("my-module", "tags.env = 'prod'")
    assert status == "healthy"
    assert detail is None


@pytest.mark.asyncio
async def test_check_module_health_unhealthy_when_stopped(monkeypatch):
    async def fake_post_async(url, responses, _json=None, headers=None, timeout=5):
        responses[url] = _fake_response(200, [{"deviceId": "canary-1"}])

    async def fake_get_async(url, responses, headers=None, timeout=5):
        responses[url] = _fake_response(
            200, {"properties": {"reported": {"modules": {"my-module": {"runtimeStatus": "stopped"}}}}}
        )

    monkeypatch.setattr(iotedge, "post_async", fake_post_async)
    monkeypatch.setattr(iotedge, "get_async", fake_get_async)
    monkeypatch.setattr(iotedge, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")

    status, detail = await iotedge.check_module_health("my-module", "tags.env = 'prod'")
    assert status == "unhealthy"
    assert "stopped" in detail


@pytest.mark.asyncio
async def test_check_module_health_unknown_when_module_missing_from_reported_modules(monkeypatch):
    async def fake_post_async(url, responses, _json=None, headers=None, timeout=5):
        responses[url] = _fake_response(200, [{"deviceId": "canary-1"}])

    async def fake_get_async(url, responses, headers=None, timeout=5):
        responses[url] = _fake_response(200, {"properties": {"reported": {"modules": {}}}})

    monkeypatch.setattr(iotedge, "post_async", fake_post_async)
    monkeypatch.setattr(iotedge, "get_async", fake_get_async)
    monkeypatch.setattr(iotedge, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")

    status, detail = await iotedge.check_module_health("my-module", "tags.env = 'prod'")
    assert status == "unknown"
    assert detail


@pytest.mark.asyncio
async def test_check_module_health_never_raises_on_exception(monkeypatch):
    async def fake_post_async(*a, **kw):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(iotedge, "post_async", fake_post_async)
    monkeypatch.setattr(iotedge, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(iotedge, "IOT_HUB_NAME", "myhub.azure-devices.net")

    status, detail = await iotedge.check_module_health("my-module", "tags.env = 'prod'")
    assert status == "unknown"
    assert detail
