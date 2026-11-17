import json
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from azure.core.exceptions import ResourceNotFoundError

from blob_service import BlobContainerContext, blob_url
from exceptions import IoTBackendAPIError

reader = import_module("routers.module_config.routes.get_module_twin_config")


def test_blob_url_preserves_cloud_default_and_encodes_identifiers():
    assert blob_url("cloud", "configs", "dev?/mod%/current", "?sig=test") == (
        "https://cloud.blob.core.windows.net/configs/dev%3F/mod%25/current?sig=test"
    )
    assert blob_url("local", "configs", "dev/mod/current", "sig=test",
                    "http://blob:10000/local/") == "http://blob:10000/local/configs/dev/mod/current?sig=test"


@pytest.mark.asyncio
async def test_container_supports_path_style_emulator_endpoint(monkeypatch):
    import blob_service
    calls = {}

    class Client:
        def __init__(self, **kwargs):
            calls.update(kwargs)

        def get_container_client(self, name):
            return name

        async def close(self):
            calls["closed"] = True

    monkeypatch.setattr(blob_service, "BlobServiceClient", Client)
    async with BlobContainerContext("local", "configs", "?sig=test", "http://blob:10000/local/") as container:
        assert container == "configs"
    assert calls == {"account_url": "http://blob:10000/local", "credential": "sig=test", "closed": True}


@pytest.fixture
def storage(monkeypatch):
    state = {"desired": {}, "path": None, "missing": False}

    async def get(url, responses, **kwargs):
        responses[url] = SimpleNamespace(status_code=200, json=lambda: {"properties": {"desired": state["desired"]}})

    class Blob:
        async def download_blob(self):
            if state["missing"]:
                raise ResourceNotFoundError("missing")
            return SimpleNamespace(readall=AsyncMock(return_value=json.dumps({"schemaVersion": "1.0", "clients": {}}).encode()))

    class Container:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get_blob_client(self, path):
            state["path"] = path
            return Blob()

    monkeypatch.setattr(reader, "get_async", get)
    monkeypatch.setattr(reader, "get_iothub_auth_headers", lambda: {})
    monkeypatch.setattr(reader, "BlobContainerContext", lambda *args, **kwargs: Container())
    monkeypatch.setattr(reader, "PUBLIC_STORAGE_ACCOUNT_NAME", "local")
    monkeypatch.setattr(reader, "MODULE_CONFIG_STORAGE_URL", "http://127.0.0.1:10000/local")
    monkeypatch.setattr(reader, "MODULE_CONFIG_DEVICE_STORAGE_URL", "http://blob:10000/local")
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["current", "a" * 32])
async def test_reads_twin_referenced_blob_not_stale_current(storage, filename):
    storage["desired"] = {
        "configId": "a" * 32,
        "configBlobUrl": f"http://blob:10000/local/iotedge-device-twin/dev/mod/{filename}?sig=hidden",
    }
    assert await reader.get_module_twin_config("dev", "mod") == {"schemaVersion": "1.0", "clients": {}}
    assert storage["path"] == f"dev/mod/{filename}"


@pytest.mark.asyncio
async def test_unconfigured_module_retains_legacy_missing_current_behavior(storage):
    storage["missing"] = True
    assert await reader.get_module_twin_config("dev", "mod") is None


@pytest.mark.asyncio
async def test_missing_referenced_blob_is_explicit_error(storage):
    storage["desired"] = {"configBlobUrl": "http://blob:10000/local/iotedge-device-twin/dev/mod/current?sig=x"}
    storage["missing"] = True
    with pytest.raises(IoTBackendAPIError) as error:
        await reader.get_module_twin_config("dev", "mod")
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("pointer", [
    "http://other:10000/local/iotedge-device-twin/dev/mod/current",
    "http://blob:10000/local/iotedge-device-twin/other/mod/current",
    "http://blob:10000/local/iotedge-device-twin/dev/mod/../current",
    "http://blob:10000/local/iotedge-device-twin/dev/mod/" + "b" * 32,
])
async def test_rejects_foreign_or_mismatched_blob_pointer_without_storage_io(storage, pointer):
    storage["desired"] = {"configId": "a" * 32, "configBlobUrl": pointer}
    with pytest.raises(IoTBackendAPIError) as error:
        await reader.get_module_twin_config("dev", "mod")
    assert error.value.status_code == 409
    assert storage["path"] is None


@pytest.mark.asyncio
async def test_reported_returns_everything_but_iot_hub_metadata(monkeypatch):
    reported_reader = import_module("routers.module_config.routes.get_module_twin_reported")
    reported = {"configId": "c1", "config": {"status": True}, "custom": {"a": 1}, "$version": 4,
                "$metadata": {"$lastUpdated": "2026-11-09T10:00:00Z"}}

    async def get(url, responses, **kwargs):
        responses[url] = SimpleNamespace(status_code=200, json=lambda: {"properties": {"reported": reported}})

    monkeypatch.setattr(reported_reader, "get_async", get)
    monkeypatch.setattr(reported_reader, "get_iothub_auth_headers", lambda: {})
    assert await reported_reader.get_module_twin_reported("dev", "mod") == {
        "configId": "c1", "config": {"status": True}, "custom": {"a": 1}, "$version": 4,
        "lastUpdated": "2026-11-09T10:00:00Z",
    }
    reported.clear()
    assert await reported_reader.get_module_twin_reported("dev", "mod") is None

    async def missing(url, responses, **kwargs):
        responses[url] = SimpleNamespace(status_code=404, text="no")

    monkeypatch.setattr(reported_reader, "get_async", missing)
    with pytest.raises(IoTBackendAPIError) as exc:
        await reported_reader.get_module_twin_reported("dev", "mod")
    assert exc.value.status_code == 404
