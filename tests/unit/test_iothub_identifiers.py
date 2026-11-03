from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import unquote, urlsplit

import httpx
import pytest

from exceptions import APIError
from helper import encode_iothub_id, validate_iothub_id
from routers.module_config.schemas import ModuleList


@pytest.mark.parametrize("value", [
    "device-1", "$edgeAgent", "$edgeHub", "module..name", "...", "a" * 128,
    "dev?%_*!(),:=@$'-.",
])
def test_valid_identifiers_remain_single_url_segments(value):
    encoded = encode_iothub_id(value)
    assert validate_iothub_id(value) == value
    assert unquote(encoded) == value
    request = httpx.Request("GET", f"https://hub.test/twins/{encoded}/modules/{encoded}?api-version=test")
    parsed = urlsplit(str(request.url))
    assert parsed.path == f"/twins/{encoded}/modules/{encoded}"
    assert parsed.query == "api-version=test"
    assert not parsed.fragment


@pytest.mark.parametrize("value", [
    "", ".", "..", "../outside", "a/b", "a\\b", "a#fragment", "a&x=1",
    "a b", "a\n", "a\t", "é", "a" * 129, None, 42,
])
def test_invalid_identifiers_raise_explicit_client_error(value):
    with pytest.raises(APIError) as exc:
        encode_iothub_id(value)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_config_status_validates_entire_batch_before_credentials_or_requests(monkeypatch):
    status = import_module("routers.module_config.routes.post_module_config_status")
    hub = AsyncMock()
    headers = MagicMock()
    monkeypatch.setattr(status, "get_async", hub)
    monkeypatch.setattr(status, "get_iothub_auth_headers", headers)
    with pytest.raises(APIError) as exc:
        await status.post_module_config_status("dev-1", ModuleList(modules=["valid", "../outside"]))
    assert exc.value.status_code == 400
    hub.assert_not_called()
    headers.assert_not_called()


@pytest.mark.asyncio
async def test_config_status_preserves_system_modules_and_output_shape(monkeypatch):
    status = import_module("routers.module_config.routes.post_module_config_status")
    urls = []

    async def get(url, responses, **kwargs):
        urls.append(url)
        module = unquote(urlsplit(url).path.split("/")[-1])
        responses[url] = SimpleNamespace(status_code=200, json=lambda: {
            "moduleId": module,
            "properties": {
                "desired": {"configId": "v1"},
                "reported": {"configId": "v1", "config": {"status": True, "message": "ok"}},
            },
        })

    monkeypatch.setattr(status, "get_async", get)
    monkeypatch.setattr(status, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(status, "IOT_HUB_NAME", "hub.test")
    modules = ["$edgeAgent", "$edgeHub", "module?name%2F", "module..name"]
    response = await status.post_module_config_status("dev?1%2F", ModuleList(modules=modules))
    assert set(response) == set(modules)
    for module, url in zip(modules, urls):
        parsed = urlsplit(str(httpx.Request("GET", url).url))
        assert parsed.path == f"/twins/dev%3F1%252F/modules/{encode_iothub_id(module)}"
        assert parsed.query == "api-version=2020-05-31-preview"
        assert response[module] == {
            "confStatus": "OK", "desiredConfId": "v1", "reportedConfId": "v1",
            "appStatus": "OK", "appMessage": "ok",
        }


MODULE_HANDLERS = [
    ("routers.module_config.routes.get_module_twin_config", "get_module_twin_config", ()),
    ("routers.module_config.routes.get_module_twin_config_binary", "get_module_twin_config_binary", ()),
    ("routers.module_config.routes.get_module_twin_identity_reported", "get_module_twin_identity_reported", ()),
    ("routers.module_config.routes.post_module_twin_config", "post_module_twin_config", (SimpleNamespace(),)),
    ("routers.general.routes.post_module_method", "post_module_method", (SimpleNamespace(), None)),
    ("routers.cmd_proxy.routes.get_device_cmd_status", "get_device_module_status", ()),
    ("extensions.upstreams.iotedge", "_twin_read", ()),
    ("extensions.upstreams.iotedge", "_twin_write", ({},)),
    ("extensions.upstreams.iotedge", "_direct_method", ("method", {})),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,name,extra", MODULE_HANDLERS)
@pytest.mark.parametrize("device,module", [("..", "valid"), ("valid", "../outside")])
async def test_related_module_handlers_reject_before_external_io(monkeypatch, path, name, extra, device, module):
    handler = import_module(path)
    calls = []
    for dependency in ("get_async", "post_async", "patch_async", "get_iothub_auth_headers", "BlobContainerContext"):
        if hasattr(handler, dependency):
            mock = MagicMock()
            monkeypatch.setattr(handler, dependency, mock)
            calls.append(mock)
    with pytest.raises(APIError) as exc:
        await getattr(handler, name)(device, module, *extra)
    assert exc.value.status_code == 400
    for call in calls:
        call.assert_not_called()


DEVICE_HANDLERS = [
    ("routers.general.routes.get_device_modules", "get_device_modules"),
    ("routers.general.routes.get_device_connection_status", "get_iot_hub_connection_status"),
    ("routers.general.routes.get_module_deployment_status", "get_module_deployment_status"),
    ("routers.general.routes.get_deployment_tag", "get_deployment_tag"),
    ("routers.network_discovery.routes.get_network_topology", "get_network_topology"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,name", DEVICE_HANDLERS)
async def test_related_device_reads_reject_before_external_io(monkeypatch, path, name):
    handler = import_module(path)
    hub = MagicMock()
    headers = MagicMock()
    monkeypatch.setattr(handler, "get_async", hub)
    monkeypatch.setattr(handler, "get_iothub_auth_headers", headers)
    with pytest.raises(APIError) as exc:
        await getattr(handler, name)("../outside")
    assert exc.value.status_code == 400
    hub.assert_not_called()
    headers.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "delete", "deployment"])
async def test_related_writes_reject_before_side_effects(monkeypatch, operation):
    repo = AsyncMock()
    if operation == "deployment":
        handler = import_module("routers.general.routes.put_deployment_tag")
        args = ("..", "base")
        name = "put_deployment_tag"
    else:
        handler = import_module(f"routers.devices.routes.{operation}_device")
        name = f"{operation}_device"
        args = ("..", {}, repo, AsyncMock()) if operation == "create" else ("..", repo)
    headers = MagicMock()
    monkeypatch.setattr(handler, "get_iothub_auth_headers", headers)
    with pytest.raises(APIError) as exc:
        await getattr(handler, name)(*args)
    assert exc.value.status_code == 400
    assert not repo.mock_calls
    headers.assert_not_called()
