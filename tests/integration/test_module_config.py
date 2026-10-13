"""Integration coverage for the generic module-configuration GET routes:
`GET /{device}/twin/config/{module}` and `GET /{device}/twin/identity/{module}/reported`.

Blob storage and IoT Hub are mocked at the helper modules that call them, so no real
device, module or edge service is contacted. Extension schemas are registered through
the real manifest lifecycle against the test database.
"""

import json
import re
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Tuple

import pytest
from azure.core.exceptions import ResourceNotFoundError

from db.repos.extension import ExtensionRepository
from db.session import get_repository
from extensions import setup as extensions_setup
from extensions.setup import _activate_module_schemas
from extensions.upstreams import http as http_upstream
from extensions.upstreams import iotedge as iotedge_upstream
from main import app as public_app
from routers.module_config.registry import module_schema_registry
from routers.module_config.routes import get_module_twin_config as config_route
from routers.module_config.routes import get_module_twin_identity_reported as reported_route
from tests.integration.test_abac_authorization import AbacFixtures
from tests.integration.test_extensions_management_routes import (  # noqa: F401
    _fresh_http_upstream_client,
    _unique_name,
    openapi_upstream,
)

DEVICE = "device-1"
MODULE = "widget-module-1"
OTHER_MODULE = "widget-module-2"
CONFIG_REF = "#/components/schemas/WidgetConfigV1"
REPORTED_REF = "#/components/schemas/WidgetReportedV1"
MISMATCH_MESSAGE = "Module data could not be validated against its active schema"
UNAVAILABLE_MESSAGE = "Module schema is unavailable"
CONFIG_PATH = "/{device}/twin/config/{module}"
REPORTED_PATH = "/{device}/twin/identity/{module}/reported"
TWIN_URL = re.compile(r"^https://[^/]+/twins/([^/]+)/modules/([^?]+)\?")


class _FakeDownload:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def readall(self) -> bytes:
        return self._content


class _FakeBlob:
    def __init__(self, store: "_FakeBlobStore", name: str) -> None:
        self._store = store
        self._name = name

    async def download_blob(self) -> _FakeDownload:
        value = self._store.blobs.get(self._name)
        if value is None:
            raise ResourceNotFoundError("blob not found")
        if isinstance(value, Exception):
            raise value
        return _FakeDownload(value)


class _FakeContainer:
    def __init__(self, store: "_FakeBlobStore") -> None:
        self._store = store

    def get_blob_client(self, name: str) -> _FakeBlob:
        self._store.requested.append(name)
        return _FakeBlob(self._store, name)


class _FakeBlobContext:
    def __init__(self, store: "_FakeBlobStore") -> None:
        self._store = store

    async def __aenter__(self) -> _FakeContainer:
        return _FakeContainer(self._store)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeBlobStore:
    """Blob content by blob name. A missing name raises ResourceNotFoundError, an exception value raises it."""

    def __init__(self) -> None:
        self.blobs: Dict[str, Any] = {}
        self.requested: list = []


class _FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class _FakeIotHub:
    """Module twins by (device, module). An unknown twin answers 404, like IoT Hub."""

    def __init__(self) -> None:
        self.twins: Dict[Tuple[str, str], Tuple[int, str]] = {}
        self.requested: list = []

    async def get(self, url: str, data: dict, headers: dict = None, timeout: int = 5) -> None:
        device, module = TWIN_URL.match(url).groups()
        self.requested.append((device, module))
        status_code, text = self.twins.get((device, module), (404, "{}"))
        data[url] = _FakeResponse(status_code, text)

    def set_twin(self, module_twin: dict, device: str = DEVICE, module: str = MODULE) -> None:
        self.twins[(device, module)] = (200, json.dumps(module_twin))


@pytest.fixture
def blob_store(monkeypatch) -> _FakeBlobStore:
    store = _FakeBlobStore()
    monkeypatch.setattr(config_route, "BlobContainerContext", lambda *args, **kwargs: _FakeBlobContext(store))
    return store


@pytest.fixture
def iot_hub(monkeypatch) -> _FakeIotHub:
    hub = _FakeIotHub()
    for route in (config_route, reported_route):
        monkeypatch.setattr(route, "get_async", hub.get)
        monkeypatch.setattr(route, "get_iothub_auth_headers", lambda: {"Authorization": "test-token"})
    return hub


class TestGenericGetCharacterization:
    """Pins the current generic GET responses, null results, error wrappers and read authorization."""

    async def test_config_returns_the_current_blob_content_unchanged(self, client, blob_store, iot_hub):
        blob_store.blobs[f"{DEVICE}/{MODULE}/current"] = json.dumps({"interval": 5, "unit": "s"}).encode()

        response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")

        assert response.status_code == 200
        assert response.json() == {"interval": 5, "unit": "s"}
        assert blob_store.requested == [f"{DEVICE}/{MODULE}/current"]
        assert iot_hub.requested == []

    async def test_config_returns_null_when_the_module_exists_without_a_blob(self, client, blob_store, iot_hub):
        iot_hub.set_twin({})

        response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")

        assert response.status_code == 200
        assert response.json() is None
        assert iot_hub.requested == [(DEVICE, MODULE)]

    async def test_config_missing_module_keeps_the_404_wrapper(self, client, blob_store, iot_hub):
        response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")

        assert response.status_code == 404
        assert response.json() == {
            "message": f"{{'error': 'IoTBackendAPIError', 'message': 'Module {MODULE} not found on device {DEVICE}'}}"
        }

    async def test_config_read_failure_keeps_the_500_wrapper(self, client, blob_store, iot_hub):
        blob_store.blobs[f"{DEVICE}/{MODULE}/current"] = RuntimeError("blob unavailable")

        response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")

        assert response.status_code == 500
        assert response.json() == {
            "message": "{'error': 'IoTBackendAPIError', 'message': 'Could not retrieve module twin config: blob unavailable'}"
        }

    async def test_reported_returns_the_filtered_projection_unchanged(self, client, blob_store, iot_hub):
        iot_hub.set_twin({
            "properties": {
                "desired": {"secret": "not-returned"},
                "reported": {
                    "config": {"status": "OK"},
                    "diagnostic": {"numberOfConnectedClients": 2},
                    "other": "dropped",
                    "$metadata": {"$lastUpdated": "2026-10-13T08:00:00Z"},
                },
            }
        })

        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 200
        assert response.json() == {
            "config": {"status": "OK"},
            "diagnostic": {"numberOfConnectedClients": 2},
            "lastUpdated": "2026-10-13T08:00:00Z",
        }
        assert iot_hub.requested == [(DEVICE, MODULE)]
        assert blob_store.requested == []

    async def test_reported_keeps_diagnostics_plural_and_omits_absent_fields(self, client, blob_store, iot_hub):
        iot_hub.set_twin({"properties": {"reported": {"diagnostics": {"info": "ok"}}}})

        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 200
        assert response.json() == {"diagnostics": {"info": "ok"}}

    async def test_reported_returns_null_without_a_reported_section(self, client, blob_store, iot_hub):
        iot_hub.set_twin({"properties": {"desired": {"interval": 5}}})

        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 200
        assert response.json() is None

    async def test_reported_returns_null_when_nothing_matches_the_projection(self, client, blob_store, iot_hub):
        iot_hub.set_twin({"properties": {"reported": {"unrelated": {"x": 1}}}})

        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 200
        assert response.json() is None

    async def test_reported_missing_module_is_wrapped_as_500_not_404(self, client, blob_store, iot_hub):
        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 500
        assert response.json() == {
            "message": (
                "{'error': 'IoTBackendAPIError', 'message': "
                f"'Could not retrieve module identity twin: Module {MODULE} not found on device {DEVICE}'}}"
            )
        }

    async def test_reported_upstream_error_is_wrapped_twice_as_500(self, client, blob_store, iot_hub):
        # The helper re-wraps its own non-200 IoTBackendAPIError in its catch-all, so the prefix repeats.
        iot_hub.twins[(DEVICE, MODULE)] = (503, "hub busy")

        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 500
        assert response.json() == {
            "message": (
                "{'error': 'IoTBackendAPIError', 'message': "
                "'Could not retrieve module identity twin: Could not retrieve module identity twin: hub busy'}"
            )
        }



class TestGenericGetAuthorization:
    """A caller without device.read is refused before any Blob or IoT Hub read."""

    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "module-config-viewer-oid",
            "sub": "module-config-viewer-oid",
            "preferred_username": "viewer@test.com",
            "name": "Viewer",
            "roles": [],
        }

    @pytest.mark.parametrize("path", [f"/{DEVICE}/twin/config/{MODULE}", f"/{DEVICE}/twin/identity/{MODULE}/reported"])
    async def test_caller_without_device_read_gets_403_before_any_read(self, client, db_session, blob_store, iot_hub, path):
        await AbacFixtures(db_session).setup(
            users={"viewer": "module-config-viewer-oid"},
            roles={"auth-reader": ["platform.authorization.read"]},
            teams={"t": {"roles": ["auth-reader"], "users": ["viewer"]}},
        )
        blob_store.blobs[f"{DEVICE}/{MODULE}/current"] = b"{}"
        iot_hub.set_twin({"properties": {"reported": {"config": {}}}})

        response = await client.get(path)

        assert response.status_code == 403
        assert blob_store.requested == []
        assert iot_hub.requested == []


def _module_document() -> dict:
    """Neutral widget contracts: a configuration and an independent reported response, each with a nested component."""
    return {
        "openapi": "3.1.0",
        "paths": {},
        "components": {
            "schemas": {
                "WidgetConfigV1": {
                    "title": "WidgetConfigV1",
                    "type": "object",
                    "required": ["interval"],
                    "properties": {
                        "interval": {"type": "integer", "minimum": 1},
                        "unit": {"$ref": "#/components/schemas/WidgetUnit"},
                    },
                },
                "WidgetUnit": {"title": "WidgetUnit", "type": "string", "enum": ["ms", "s"]},
                "WidgetReportedV1": {
                    "title": "WidgetReportedV1",
                    "type": "object",
                    "properties": {
                        "config": {"$ref": "#/components/schemas/WidgetReportedConfig"},
                        "diagnostic": {
                            "type": "object",
                            "properties": {"numberOfConnectedClients": {"type": "integer"}},
                        },
                        "lastUpdated": {"type": "string"},
                    },
                },
                "WidgetReportedConfig": {
                    "title": "WidgetReportedConfig",
                    "type": "object",
                    "properties": {"status": {"type": "string", "enum": ["OK", "ERROR"]}},
                },
            }
        },
    }


@pytest.fixture
def module_upstream(openapi_upstream):
    openapi_upstream.document = _module_document()
    return openapi_upstream


@pytest.fixture
async def extension_names():
    """Names of extensions a test registers. Teardown unmounts their routes and active schemas; DB writes roll back."""
    names: List[str] = []
    yield names
    for name in names:
        extensions_setup._unmount_extension(name)


def _module_registration(name: str, base_url: str, module_schemas: Dict[str, dict]) -> dict:
    return {
        "schema_version": 1,
        "name": name,
        "description": "generic module GET test",
        "upstreams": {"svc": {"type": "http", "base_url": base_url}},
        "routes": [],
        "module_schemas": module_schemas,
    }


async def _enable(client, names: List[str], module_schemas: Dict[str, dict], base_url: str) -> str:
    name = _unique_name("module_get_ext")
    names.append(name)
    response = await client.post("/extensions", json=_module_registration(name, base_url, module_schemas))
    assert response.status_code == 201, response.text
    assert (await client.post(f"/extensions/{name}/enable")).status_code == 200
    return name


def _both_schemas(module_id: str = MODULE) -> Dict[str, dict]:
    return {module_id: {"upstream": "svc", "schema_ref": CONFIG_REF, "reported_schema_ref": REPORTED_REF}}


def _config_only(module_id: str = MODULE) -> Dict[str, dict]:
    return {module_id: {"upstream": "svc", "schema_ref": CONFIG_REF}}


def _resolve(openapi: dict, schema: dict) -> dict:
    while "$ref" in schema:
        schema = openapi["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
    return schema


def _alternatives(openapi: dict, path: str) -> List[dict]:
    return openapi["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["anyOf"]


class TestConfigurationSchemaSelection:
    async def test_valid_configuration_is_returned_as_stored(self, client, module_upstream, extension_names, blob_store, iot_hub):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        stored = {"interval": 5, "unit": "s"}
        blob_store.blobs[f"{DEVICE}/{MODULE}/current"] = json.dumps(stored).encode()

        response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")

        assert response.status_code == 200
        assert response.json() == stored
        assert blob_store.requested == [f"{DEVICE}/{MODULE}/current"]
        assert iot_hub.requested == []

    async def test_invalid_stored_configuration_returns_a_fixed_500_without_echoing_data(
        self, client, module_upstream, extension_names, blob_store, iot_hub
    ):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        blob_store.blobs[f"{DEVICE}/{MODULE}/current"] = json.dumps({"interval": "fast-secret", "unit": "minutes"}).encode()

        response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")

        assert response.status_code == 500
        assert response.json() == {"message": MISMATCH_MESSAGE}
        for leaked in ("fast-secret", "minutes", "interval", "unit", "must be"):
            assert leaked not in response.text

    async def test_configuration_nested_component_is_enforced(self, client, module_upstream, extension_names, blob_store, iot_hub):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        blob_store.blobs[f"{DEVICE}/{MODULE}/current"] = json.dumps({"interval": 5, "unit": "minutes"}).encode()

        assert (await client.get(f"/{DEVICE}/twin/config/{MODULE}")).status_code == 500

    async def test_registered_module_without_a_blob_returns_null(self, client, module_upstream, extension_names, blob_store, iot_hub):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        iot_hub.set_twin({})

        response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")

        assert response.status_code == 200
        assert response.json() is None

    async def test_missing_module_keeps_the_404_with_an_active_schema(self, client, module_upstream, extension_names, blob_store, iot_hub):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)

        response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")

        assert response.status_code == 404

    async def test_unregistered_module_keeps_the_generic_configuration_unvalidated(
        self, client, module_upstream, extension_names, blob_store, iot_hub
    ):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        stored = {"interval": "not-validated-for-this-module"}
        blob_store.blobs[f"{DEVICE}/{OTHER_MODULE}/current"] = json.dumps(stored).encode()

        response = await client.get(f"/{DEVICE}/twin/config/{OTHER_MODULE}")

        assert response.status_code == 200
        assert response.json() == stored

    async def test_configuration_does_not_validate_against_the_reported_schema(
        self, client, module_upstream, extension_names, blob_store, iot_hub
    ):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        blob_store.blobs[f"{DEVICE}/{MODULE}/current"] = json.dumps({"config": {"status": "OK"}}).encode()

        assert (await client.get(f"/{DEVICE}/twin/config/{MODULE}")).status_code == 500


class TestReportedSchemaSelection:
    async def test_valid_reported_projection_is_returned_unchanged(self, client, module_upstream, extension_names, blob_store, iot_hub):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        projection = {
            "config": {"status": "OK"},
            "diagnostic": {"numberOfConnectedClients": 2},
            "lastUpdated": "2026-10-13T08:00:00Z",
        }
        reported = {
            "config": {"status": "OK"},
            "diagnostic": {"numberOfConnectedClients": 2},
            "other": "dropped",
            "$metadata": {"$lastUpdated": "2026-10-13T08:00:00Z"},
        }
        iot_hub.set_twin({"properties": {"desired": {"interval": 5}, "reported": reported}})

        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 200
        assert response.json() == projection

    async def test_partial_projection_is_returned_unchanged(self, client, module_upstream, extension_names, blob_store, iot_hub):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        iot_hub.set_twin({"properties": {"reported": {"diagnostic": {"numberOfConnectedClients": 0}}}})

        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 200
        assert response.json() == {"diagnostic": {"numberOfConnectedClients": 0}}

    async def test_invalid_reported_projection_returns_a_fixed_500_not_the_generic_response(
        self, client, module_upstream, extension_names, blob_store, iot_hub
    ):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        iot_hub.set_twin({"properties": {"reported": {"diagnostic": {"numberOfConnectedClients": "many-secret"}}}})

        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 500
        assert response.json() == {"message": MISMATCH_MESSAGE}
        assert "many-secret" not in response.text

    async def test_reported_nested_component_is_enforced(self, client, module_upstream, extension_names, blob_store, iot_hub):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        iot_hub.set_twin({"properties": {"reported": {"config": {"status": "UNKNOWN"}}}})

        assert (await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")).status_code == 500

    async def test_no_reported_projection_stays_null(self, client, module_upstream, extension_names, blob_store, iot_hub):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        iot_hub.set_twin({"properties": {"desired": {"interval": 5}}})

        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 200
        assert response.json() is None

    async def test_configuration_only_entry_keeps_the_generic_reported_response(
        self, client, module_upstream, extension_names, blob_store, iot_hub
    ):
        await _enable(client, extension_names, _config_only(), module_upstream.base_url)
        generic = {"diagnostic": {"numberOfConnectedClients": "not-validated"}}
        iot_hub.set_twin({"properties": {"reported": generic}}, module=MODULE)

        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 200
        assert response.json() == generic

    async def test_unregistered_module_keeps_the_generic_reported_response_for_any_id(
        self, client, module_upstream, extension_names, blob_store, iot_hub
    ):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        generic = {"diagnostic": {"numberOfConnectedClients": "not-validated"}}
        iot_hub.set_twin({"properties": {"reported": generic}}, module=OTHER_MODULE)

        response = await client.get(f"/{DEVICE}/twin/identity/{OTHER_MODULE}/reported")

        assert response.status_code == 200
        assert response.json() == generic

    async def test_disabled_module_falls_back_to_the_generic_reported_response(
        self, client, module_upstream, extension_names, blob_store, iot_hub
    ):
        name = await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        assert (await client.post(f"/extensions/{name}/disable")).status_code == 200
        generic = {"diagnostic": {"numberOfConnectedClients": "not-validated"}}
        iot_hub.set_twin({"properties": {"reported": generic}})

        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 200
        assert response.json() == generic

    async def test_reported_missing_module_keeps_the_500_wrapper_with_an_active_schema(
        self, client, module_upstream, extension_names, blob_store, iot_hub
    ):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)

        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 500
        assert "Module widget-module-1 not found on device device-1" in response.json()["message"]


class TestUnavailableModule:
    async def _make_unavailable(self, client, db_session, names, module_upstream) -> None:
        name = await _enable(client, names, _both_schemas(), module_upstream.base_url)
        extension_repo = get_repository(ExtensionRepository)(db_session)
        stored = (await extension_repo.list_module_schemas(name))[0]
        await extension_repo.replace_module_schemas(name, [{**stored, "schema": {"type": "not-a-type"}}])
        await _activate_module_schemas(extension_repo, name)
        assert module_schema_registry.is_unavailable(MODULE)

    async def test_claimed_but_unavailable_module_returns_503_before_any_read(
        self, client, db_session, module_upstream, extension_names, blob_store, iot_hub
    ):
        await self._make_unavailable(client, db_session, extension_names, module_upstream)
        blob_store.blobs[f"{DEVICE}/{MODULE}/current"] = b"{}"
        iot_hub.set_twin({"properties": {"reported": {"config": {}}}})

        config = await client.get(f"/{DEVICE}/twin/config/{MODULE}")
        reported = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert config.status_code == 503
        assert reported.status_code == 503
        assert config.json() == {"message": UNAVAILABLE_MESSAGE}
        assert reported.json() == {"message": UNAVAILABLE_MESSAGE}
        assert blob_store.requested == []
        assert iot_hub.requested == []

    async def test_unavailable_module_never_falls_back_when_the_helper_would_fail(
        self, client, db_session, module_upstream, extension_names, blob_store, iot_hub
    ):
        await self._make_unavailable(client, db_session, extension_names, module_upstream)

        response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")

        assert response.status_code == 503

    async def test_sibling_module_of_an_unavailable_extension_is_not_affected(
        self, client, db_session, module_upstream, extension_names, blob_store, iot_hub
    ):
        await self._make_unavailable(client, db_session, extension_names, module_upstream)
        stored = {"interval": "generic-for-unregistered"}
        blob_store.blobs[f"{DEVICE}/{OTHER_MODULE}/current"] = json.dumps(stored).encode()

        response = await client.get(f"/{DEVICE}/twin/config/{OTHER_MODULE}")

        assert response.status_code == 200
        assert response.json() == stored

    async def test_schema_resolution_failure_returns_a_fixed_500(
        self, client, module_upstream, extension_names, blob_store, iot_hub, monkeypatch
    ):
        from extensions import body_validation

        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        blob_store.blobs[f"{DEVICE}/{MODULE}/current"] = json.dumps({"interval": 5}).encode()

        def broken_validator(*args, **kwargs):
            raise RuntimeError("schema could not be compiled")

        monkeypatch.setattr(body_validation, "validate_module_snapshot", broken_validator)

        response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")

        assert response.status_code == 500
        assert response.json() == {"message": MISMATCH_MESSAGE}
        assert "compiled" not in response.text


class TestReadIsolation:
    async def test_reported_get_reads_only_the_iot_hub_twin(
        self, client, module_upstream, extension_names, blob_store, iot_hub, monkeypatch
    ):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        iot_hub.set_twin({"properties": {"reported": {"diagnostic": {"numberOfConnectedClients": 1}}}})

        def forbidden(*args, **kwargs):
            raise AssertionError("the reported GET must not call an extension upstream, schema service or direct method")

        monkeypatch.setattr(http_upstream, "fetch_openapi_document", forbidden)
        monkeypatch.setattr(iotedge_upstream, "_direct_method", forbidden)

        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")

        assert response.status_code == 200
        assert iot_hub.requested == [(DEVICE, MODULE)]
        assert blob_store.requested == []

    async def test_configuration_forwards_exact_device_and_module_to_blob(
        self, client, module_upstream, extension_names, blob_store, iot_hub
    ):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        blob_store.blobs[f"{DEVICE}/{MODULE}/current"] = json.dumps({"interval": 1}).encode()

        assert (await client.get(f"/{DEVICE}/twin/config/{MODULE}")).status_code == 200
        assert blob_store.requested == [f"{DEVICE}/{MODULE}/current"]
        assert iot_hub.requested == []


class TestSharedModuleOwnership:
    async def test_second_extension_cannot_claim_a_served_module(self, client, module_upstream, extension_names, blob_store, iot_hub):
        owner = await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        consumer = _unique_name("module_consumer_ext")
        extension_names.append(consumer)

        response = await client.post(
            "/extensions", json=_module_registration(consumer, module_upstream.base_url, _both_schemas())
        )

        assert response.status_code == 409
        assert f"already claimed by extension '{owner}'" in response.json()["message"]
        assert module_schema_registry.claim_owner(MODULE) == owner

    async def test_owner_schema_still_governs_the_shared_module(self, client, module_upstream, extension_names, blob_store, iot_hub):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        blob_store.blobs[f"{DEVICE}/{MODULE}/current"] = json.dumps({"interval": 0}).encode()

        assert (await client.get(f"/{DEVICE}/twin/config/{MODULE}")).status_code == 500


class TestGenericOpenApiAlternatives:
    async def test_enabled_schemas_publish_anyof_alternatives_for_their_own_purpose(
        self, client, module_upstream, extension_names, blob_store, iot_hub
    ):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)

        openapi = (await client.get("/openapi.json")).json()

        config = _alternatives(openapi, CONFIG_PATH)
        assert {"$ref": "#/components/schemas/GetModuleTwinResponse"} in config
        assert "GetModuleTwinResponse" in openapi["components"]["schemas"]
        assert "GetModuleTwinIdentityResponse" in openapi["components"]["schemas"]
        assert {"type": "null"} in config
        config_roots = [_resolve(openapi, alt) for alt in config if "$ref" in alt and alt["$ref"] != "#/components/schemas/GetModuleTwinResponse"]
        assert [root.get("title") for root in config_roots] == ["WidgetConfigV1"]
        unit = openapi["components"]["schemas"][config_roots[0]["properties"]["unit"]["$ref"].rsplit("/", 1)[1]]
        assert unit == {"title": "WidgetUnit", "type": "string", "enum": ["ms", "s"]}

        reported = openapi["paths"][REPORTED_PATH]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["anyOf"]
        assert {"$ref": "#/components/schemas/GetModuleTwinIdentityResponse"} in reported
        assert {"type": "null"} in reported
        reported_titles = [_resolve(openapi, alt).get("title") for alt in reported if "$ref" in alt]
        assert "WidgetReportedV1" in reported_titles
        assert "WidgetConfigV1" not in reported_titles

    async def test_registry_change_appears_in_the_next_openapi_read_and_disable_removes_it(
        self, client, module_upstream, extension_names, blob_store, iot_hub
    ):
        core_only = (await client.get("/openapi.json")).json()
        assert _alternatives(core_only, CONFIG_PATH) == [
            {"$ref": "#/components/schemas/GetModuleTwinResponse"},
            {"type": "null"},
        ]

        name = await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        enabled = (await client.get("/openapi.json")).json()
        assert len(_alternatives(enabled, CONFIG_PATH)) == 3

        assert (await client.post(f"/extensions/{name}/disable")).status_code == 200
        disabled = (await client.get("/openapi.json")).json()
        assert disabled == core_only

    async def test_replace_without_reported_ref_removes_only_reported_alternatives(
        self, client, module_upstream, extension_names, blob_store, iot_hub
    ):
        name = await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        assert len(_alternatives(enabled := (await client.get("/openapi.json")).json(), CONFIG_PATH)) == 3
        replacement = _module_registration(name, module_upstream.base_url, _config_only())

        assert (await client.put(f"/extensions/{name}", json=replacement)).status_code == 200

        openapi = (await client.get("/openapi.json")).json()
        assert len(_alternatives(openapi, CONFIG_PATH)) == 3
        assert openapi["paths"][REPORTED_PATH]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/GetModuleTwinIdentityResponse"
        }
        assert enabled is not openapi

    async def test_openapi_reads_never_mutate_the_cached_document(
        self, client, module_upstream, extension_names, blob_store, iot_hub
    ):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)

        first = (await client.get("/openapi.json")).json()
        second = (await client.get("/openapi.json")).json()

        assert first == second
        assert "Module_configuration_" not in json.dumps(public_app.openapi_schema)

    async def test_startup_hydration_restores_the_alternatives(
        self, client, db_session, module_upstream, extension_names, blob_store, iot_hub, monkeypatch
    ):
        name = await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        expected = (await client.get("/openapi.json")).json()

        # Simulate a restarted process: the in-memory schemas start empty, then startup hydration runs.
        extensions_setup._unmount_extension(name)
        assert module_schema_registry.get(MODULE, "configuration") is None

        @asynccontextmanager
        async def startup_session():
            yield db_session

        monkeypatch.setattr(extensions_setup, "AsyncSessionLocal", startup_session)
        await extensions_setup.hydrate_all_routes()

        restored = (await client.get("/openapi.json")).json()
        assert restored["paths"][CONFIG_PATH] == expected["paths"][CONFIG_PATH]
        assert restored["paths"][REPORTED_PATH] == expected["paths"][REPORTED_PATH]
        assert len(_alternatives(restored, CONFIG_PATH)) == 3


# POST fixtures are separate from WP-2's read fakes and authorization fixtures.
@pytest.fixture
async def post_state():
    from extensions import body_validation

    module_schema_registry._active.clear()
    module_schema_registry._claims.clear()
    module_schema_registry._unavailable.clear()
    body_validation._module_validator_cache.clear()
    public_app.openapi_schema = None
    yield
    module_schema_registry._active.clear()
    module_schema_registry._claims.clear()
    module_schema_registry._unavailable.clear()
    body_validation._module_validator_cache.clear()
    public_app.openapi_schema = None


@pytest.fixture
def post_writes(monkeypatch):
    from types import SimpleNamespace
    from routers.module_config.routes import post_module_twin_config as write_route

    effects = SimpleNamespace(contexts=[], blobs=[], uploads=[], patches=[], upload_error=None, status=200)

    class Context:
        def __init__(self, *args, **kwargs):
            effects.contexts.append((args, kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get_blob_client(self, name):
            effects.blobs.append(name)
            return self

        async def upload_blob(self, data, overwrite):
            if effects.upload_error:
                raise effects.upload_error
            effects.uploads.append((data, overwrite))

    async def patch(url, responses, _json, headers):
        effects.patches.append((url, _json, headers))
        responses[url] = _FakeResponse(effects.status, "synthetic hub error")

    monkeypatch.setattr(write_route, "PUBLIC_STORAGE_ACCOUNT_NAME", "teststorage")
    monkeypatch.setattr(write_route, "BLOB_SAS_TOKEN_MODULE_CONF", "sig=synthetic")
    monkeypatch.setattr(write_route, "IOT_HUB_NAME", "testhub.invalid")
    monkeypatch.setattr(write_route, "BlobContainerContext", Context)
    monkeypatch.setattr(write_route, "get_iothub_auth_headers", lambda: {"Authorization": "synthetic"})
    monkeypatch.setattr(write_route, "patch_async", patch)
    return effects


def _post_alternatives(document):
    body = document["paths"][CONFIG_PATH]["post"]["requestBody"]
    assert body["required"] is True
    return body["content"]["application/json"]["schema"]["anyOf"]


def _assert_no_writes(effects):
    assert effects.contexts == []
    assert effects.uploads == []
    assert effects.patches == []


@pytest.mark.usefixtures("post_state")
class TestGenericPost:
    async def test_exact_storage_and_twin_targets_and_literal_http_result(
        self, client, module_upstream, extension_names, post_writes, blob_store, iot_hub, monkeypatch
    ):
        from uuid import UUID

        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        payload = {"interval": 2, "unit": "ms", "nested": {"key": None}, "items": [None], "extra": None}

        def forbidden(*args, **kwargs):
            raise AssertionError("POST validation must not fetch schemas, call direct methods or read cloud data")

        monkeypatch.setattr(http_upstream, "fetch_openapi_document", forbidden)
        monkeypatch.setattr(http_upstream, "dispatch", forbidden)
        monkeypatch.setattr(iotedge_upstream, "_direct_method", forbidden)
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=payload)

        assert response.status_code == 200
        assert response.json() == [payload, 200]
        assert post_writes.contexts == [(("teststorage", "iotedge-device-twin"), {"sas_token": "sig=synthetic"})]
        assert post_writes.blobs == [f"{DEVICE}/{MODULE}/current"]
        assert post_writes.uploads == [(json.dumps(payload, separators=(",", ":")), True)]
        url, patch, headers = post_writes.patches[0]
        assert url == f"https://testhub.invalid/twins/{DEVICE}/modules/{MODULE}?api-version=2020-05-31-preview"
        desired = patch["properties"]["desired"]
        assert len(desired["configId"]) == 32 and UUID(desired["configId"]).hex == desired["configId"]
        assert patch == {"properties": {"desired": {
            "configBlobUrl": f"https://teststorage.blob.core.windows.net/iotedge-device-twin/{DEVICE}/{MODULE}/current?sig=synthetic",
            "configId": desired["configId"],
        }}}
        assert headers == {"Authorization": "synthetic"}
        assert blob_store.requested == [] and iot_hub.requested == []

    @pytest.mark.parametrize("payload,location,rule", [
        ({"interval": "secret-password"}, ["interval"], "type"),
        ({"interval": 0}, ["interval"], "minimum"),
        ({"interval": 5, "unit": "secret-password"}, ["unit"], "enum"),
        ({"config": {"status": "OK"}}, [], "required"),
    ])
    async def test_invalid_configuration_has_structured_safe_422_and_zero_writes(
        self, client, module_upstream, extension_names, post_writes, payload, location, rule
    ):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=payload)
        assert response.status_code == 422
        assert response.json()["errors"][0]["loc"] == location
        assert response.json()["errors"][0]["rule"] == rule
        assert "secret-password" not in response.text
        assert "WidgetConfig" not in response.text
        _assert_no_writes(post_writes)

    async def test_unknown_module_does_not_use_a_sibling_schema(self, client, module_upstream, extension_names, post_writes):
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        payload = {"interval": "not-an-integer", "keep": None}
        response = await client.post(f"/{DEVICE}/twin/config/{OTHER_MODULE}", json=payload)
        assert response.status_code == 200
        assert response.json() == [payload, 200]
        assert post_writes.blobs == [f"{DEVICE}/{OTHER_MODULE}/current"]

    @pytest.mark.parametrize("raw", ["[]", "null", "true", "123", '"text"'])
    async def test_schema_less_non_object_is_a_structured_422(self, client, post_writes, raw):
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", content=raw)
        assert response.status_code == 422
        assert response.json()["errors"][0]["rule"] == "type"
        _assert_no_writes(post_writes)

    @pytest.mark.parametrize("raw", ["", " ", "{", "NaN", "Infinity", "-Infinity", "1e9999", '{"nested":[1e9999]}'])
    async def test_malformed_or_non_finite_is_a_safe_400(self, client, post_writes, raw):
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", content=raw)
        assert response.status_code == 400
        assert response.json() == {"message": "Request body must be valid finite JSON"}
        _assert_no_writes(post_writes)

    async def test_oversized_body_is_413_without_writes(self, client, post_writes):
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", content=b" " * (1024 * 1024 + 1))
        assert response.status_code == 413
        assert response.json() == {"message": "Request body exceeds 1048576 bytes"}
        _assert_no_writes(post_writes)

    async def test_claimed_unavailable_is_503_before_body_or_writes(
        self, client, db_session, module_upstream, extension_names, post_writes, monkeypatch
    ):
        from extensions import body_validation

        await TestUnavailableModule()._make_unavailable(client, db_session, extension_names, module_upstream)

        async def forbidden(*args):
            raise AssertionError("unavailable module must not buffer the body")

        monkeypatch.setattr(body_validation, "read_limited_body", forbidden)
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", content="not JSON")
        assert response.status_code == 503
        assert response.json() == {"message": UNAVAILABLE_MESSAGE}
        _assert_no_writes(post_writes)

    async def test_unusable_active_schema_returns_safe_503(
        self, client, module_upstream, extension_names, post_writes, monkeypatch
    ):
        from extensions import body_validation

        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)

        def broken(*args):
            raise RuntimeError("raw validator secret")

        monkeypatch.setattr(body_validation, "validate_module_snapshot", broken)
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json={"interval": 2})
        assert response.status_code == 503
        assert response.json() == {"message": UNAVAILABLE_MESSAGE}
        _assert_no_writes(post_writes)

    async def test_nullable_schema_has_no_defaults_coercion_or_null_pruning(
        self, client, module_upstream, extension_names, post_writes
    ):
        module_upstream.document["components"]["schemas"]["WidgetConfigV1"] = {
            "type": "object", "required": ["required"], "properties": {
                "required": {"type": ["string", "null"]},
                "optional": {"type": ["string", "null"]},
                "defaulted": {"type": "integer", "default": 5},
            },
        }
        await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        for payload in ({"required": None}, {"required": None, "optional": None, "dict": {"null": None}, "items": [None]}):
            response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=payload)
            assert response.status_code == 200
            assert response.json() == [payload, 200]
            assert json.loads(post_writes.uploads[-1][0]) == payload
        count = len(post_writes.uploads)
        for payload in ({}, {"required": None, "defaulted": "5"}):
            assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=payload)).status_code == 422
        assert len(post_writes.uploads) == count

    @pytest.mark.parametrize("schema,payload", [
        ({"type": "array", "items": {"type": ["string", "null"]}}, ["item", None]),
        ({"type": "null"}, None), ({"type": "string"}, "text"), ({"type": "boolean"}, False),
    ])
    async def test_active_declared_root_shape_is_accepted(self, client, module_upstream, extension_names, post_writes, schema, payload):
        module_upstream.document["components"]["schemas"]["WidgetConfigV1"] = schema
        await _enable(client, extension_names, _config_only(), module_upstream.base_url)
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", content=json.dumps(payload))
        assert response.status_code == 200
        assert response.json() == [payload, 200]
        assert json.loads(post_writes.uploads[0][0]) == payload

    @pytest.mark.parametrize("failure", ["storage", "sas", "upload", "hub"])
    async def test_core_error_http_wrappers_and_ordering_are_preserved(self, client, post_writes, monkeypatch, failure):
        from routers.module_config.routes import post_module_twin_config as write_route

        if failure == "storage":
            monkeypatch.setattr(write_route, "PUBLIC_STORAGE_ACCOUNT_NAME", "")
        elif failure == "sas":
            monkeypatch.setattr(write_route, "BLOB_SAS_TOKEN_MODULE_CONF", "")
        elif failure == "upload":
            post_writes.upload_error = RuntimeError("synthetic upload error")
        else:
            post_writes.status = 409
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json={})
        assert response.status_code == {"storage": 500, "sas": 500, "upload": 400, "hub": 409}[failure]
        assert list(response.json()) == ["message"]
        assert ("UploadError" if failure == "upload" else "IoTBackendAPIError") in response.json()["message"]
        assert len(post_writes.uploads) == (1 if failure == "hub" else 0)
        assert len(post_writes.patches) == (1 if failure == "hub" else 0)


@pytest.mark.usefixtures("post_state")
class TestGenericPostLifecycle:
    async def test_register_enable_replace_disable_deregister_and_openapi(
        self, client, db_session, module_upstream, extension_names, post_writes
    ):
        from extensions import body_validation

        core = (await client.get("/openapi.json")).json()
        assert _post_alternatives(core) == [{"type": "object"}]
        name = _unique_name("module_post_ext")
        extension_names.append(name)
        registration = _module_registration(name, module_upstream.base_url, _both_schemas())
        assert (await client.post("/extensions", json=registration)).status_code == 201
        assert (await client.get("/openapi.json")).json() == core
        assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", json={})).status_code == 200
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200
        version1 = module_schema_registry.get(MODULE, "configuration").version
        enabled = (await client.get("/openapi.json")).json()
        assert enabled == (await client.get("/openapi.json")).json()
        alternatives = _post_alternatives(enabled)
        assert len(alternatives) == 2
        root = _resolve(enabled, alternatives[1])
        assert root["title"] == "WidgetConfigV1" and root["required"] == ["interval"]
        assert _resolve(enabled, root["properties"]["unit"]) == {"title": "WidgetUnit", "type": "string", "enum": ["ms", "s"]}
        assert "WidgetReported" not in json.dumps(alternatives)
        assert "Module_configuration_" not in json.dumps(public_app.openapi_schema)
        assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", json={"interval": 2})).status_code == 200
        assert (MODULE, "configuration", version1) in body_validation._module_validator_cache

        module_upstream.document["components"]["schemas"]["WidgetConfigV1"] = {
            "title": "WidgetConfigV2", "type": "array", "items": {"type": ["string", "null"]},
        }
        assert (await client.put(f"/extensions/{name}", json=registration)).status_code == 200
        version2 = module_schema_registry.get(MODULE, "configuration").version
        assert version2 != version1
        replaced = (await client.get("/openapi.json")).json()
        assert all(component.get("title") != "WidgetConfigV1" for component in replaced["components"]["schemas"].values())
        assert "WidgetUnit" not in json.dumps(replaced["components"])
        assert _resolve(replaced, _post_alternatives(replaced)[1])["title"] == "WidgetConfigV2"
        assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", json={"interval": 2})).status_code == 422
        assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=["new", None])).status_code == 200
        assert (MODULE, "configuration", version2) in body_validation._module_validator_cache
        assert (await client.post(f"/extensions/{name}/disable")).status_code == 200
        assert (await client.get("/openapi.json")).json() == core
        assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", content="[]")).status_code == 422
        assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", json={"keep": None})).status_code == 200
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200
        assert (await client.delete(f"/extensions/{name}")).status_code == 204
        assert (await client.get("/openapi.json")).json() == core
        assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", json={})).status_code == 200
        assert len(post_writes.uploads) == 5

    async def test_hydration_refreshes_post_schema_and_removes_stale_alternatives(
        self, client, db_session, module_upstream, extension_names, post_writes, monkeypatch
    ):
        name = await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        before = (await client.get("/openapi.json")).json()
        assert _resolve(before, _post_alternatives(before)[1])["title"] == "WidgetConfigV1"
        extensions_setup._unmount_extension(name)
        assert _post_alternatives((await client.get("/openapi.json")).json()) == [{"type": "object"}]
        module_upstream.document["components"]["schemas"]["WidgetConfigV1"] = {
            "title": "HydratedConfig", "type": "object", "required": ["new"], "properties": {"new": {"type": "boolean"}},
        }

        @asynccontextmanager
        async def startup_session():
            yield db_session

        monkeypatch.setattr(extensions_setup, "AsyncSessionLocal", startup_session)
        await extensions_setup.hydrate_all_routes()
        document = (await client.get("/openapi.json")).json()
        assert _resolve(document, _post_alternatives(document)[1])["title"] == "HydratedConfig"
        assert all(component.get("title") != "WidgetConfigV1" for component in document["components"]["schemas"].values())
        assert "WidgetUnit" not in json.dumps(document["components"])
        assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", json={"interval": 2})).status_code == 422
        assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", json={"new": True})).status_code == 200
        assert len(post_writes.uploads) == 1


    async def test_extensions_disabled_skips_hydration_and_uses_object_fallback(
        self, client, module_upstream, extension_names, post_writes, monkeypatch
    ):
        import main
        from fastapi import FastAPI

        name = await _enable(client, extension_names, _both_schemas(), module_upstream.base_url)
        # Simulate restart with extensions disabled: persisted snapshots remain, no in-memory activation.
        extensions_setup._unmount_extension(name)
        monkeypatch.setattr(main, "EXTENSIONS_ENABLED", False)

        def forbidden(*args, **kwargs):
            raise AssertionError("disabled extensions must not hydrate, start or fetch schemas")

        monkeypatch.setattr(main, "hydrate_all_routes", forbidden)
        monkeypatch.setattr(main, "start_side_apps", forbidden)
        monkeypatch.setattr(http_upstream, "fetch_openapi_document", forbidden)
        disabled_app = FastAPI()
        main._register_extensions_router(disabled_app)
        assert not any(route.path.startswith("/extensions") for route in disabled_app.routes)
        assert await main._start_extensions() == []
        payload = {"interval": "generic-not-validated", "keep": None}
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=payload)
        assert response.status_code == 200
        assert response.json() == [payload, 200]
        assert json.loads(post_writes.uploads[0][0]) == payload
        assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", content="[]")).status_code == 422
        assert len(post_writes.uploads) == 1
        assert _post_alternatives((await client.get("/openapi.json")).json()) == [{"type": "object"}]


@pytest.mark.usefixtures("post_state")
class TestGenericPostAuthorization:
    @pytest.fixture
    def fake_jwt_user(self):
        return {"oid": "post-user-oid", "sub": "post-user-oid", "preferred_username": "post@test.com", "name": "Post", "roles": []}

    @pytest.mark.parametrize("action,region,expected", [
        ("device.read", "EU", 403),
        ("device.module_twin_config.write", "US", 403),
        ("device.module_twin_config.write", "EU", 200),
    ])
    async def test_real_non_admin_write_role_and_device_scope(self, client, db_session, post_writes, action, region, expected):
        await AbacFixtures(db_session).setup(
            users={"post": "post-user-oid"},
            scopes={"eu": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
            roles={"role": [action]}, teams={"team": {"scope": "eu", "roles": ["role"], "users": ["post"]}},
            devices={DEVICE: {"region": region}},
        )
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json={"keep": None})
        assert response.status_code == expected
        if expected == 403:
            _assert_no_writes(post_writes)
        else:
            assert json.loads(post_writes.uploads[0][0]) == {"keep": None}

    @pytest.mark.parametrize("raw", ["not JSON", " " * (1024 * 1024 + 1)])
    async def test_403_precedes_registry_disclosure_body_parsing_and_helper(
        self, client, db_session, post_writes, monkeypatch, raw
    ):
        from extensions import body_validation
        from routers.module_config import router

        await AbacFixtures(db_session).setup(
            users={"post": "post-user-oid"}, roles={"reader": ["device.read"]},
            teams={"team": {"roles": ["reader"], "users": ["post"]}},
        )
        module_schema_registry.replace_extension_entries("unavailable", [], [MODULE])

        def forbidden(*args, **kwargs):
            raise AssertionError("forbidden caller must not access registry, read body or call helper")

        monkeypatch.setattr(module_schema_registry, "is_unavailable", forbidden)
        monkeypatch.setattr(module_schema_registry, "get", forbidden)
        monkeypatch.setattr(body_validation, "read_limited_body", forbidden)
        monkeypatch.setattr(router, "_post_module_twin_config", forbidden)
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", content=raw)
        assert response.status_code == 403
        _assert_no_writes(post_writes)


@pytest.mark.usefixtures("post_state")
class TestGenericPostSerializationRepair:
    @pytest.fixture
    def fake_jwt_user(self):
        return {"oid": "repair-writer-oid", "sub": "repair-writer-oid", "preferred_username": "repair@test.com",
                "name": "Repair Writer", "roles": []}

    @pytest.fixture(autouse=True)
    async def scoped_writer(self, db_session):
        await AbacFixtures(db_session).setup(
            users={"writer": "repair-writer-oid"},
            scopes={"eu": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
            roles={"writer": ["device.module_twin_config.write", "extension.register"]},
            teams={"eu-writers": {"scope": "eu", "roles": ["writer"], "users": ["writer"]}},
            devices={DEVICE: {"region": "EU"}},
        )

    @pytest.mark.parametrize("depth", [300, 900])
    async def test_parser_accepted_deep_json_round_trips_through_real_app(self, client, post_writes, depth):
        from routers.module_config.payload import parse_module_json

        raw = b'{"x":' + b"[" * depth + b'"leaf"' + b"]" * depth + b"}"
        payload = parse_module_json(raw)
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", content=raw)
        assert response.status_code == 200
        assert response.json() == [payload, 200]
        assert json.loads(post_writes.uploads[0][0]) == payload
        assert post_writes.blobs == [f"{DEVICE}/{MODULE}/current"]
        assert len(post_writes.patches) == 1

    @pytest.mark.parametrize("raw", [
        b'{"x":"\\ud800"}', b'{"\\ud800":null}', b'{"x":["\\udfff",{"\\ud800":"\\ud800"}]}',
    ])
    async def test_accepted_surrogate_keys_and_values_round_trip_through_real_app(self, client, post_writes, raw):
        payload = json.loads(raw)
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", content=raw)
        assert response.status_code == 200
        assert response.json() == [payload, 200]
        assert json.loads(post_writes.uploads[0][0]) == payload
        assert post_writes.uploads[0][0].encode("ascii")
        assert len(post_writes.patches) == 1

    async def test_surrogate_error_location_is_safe_422_without_writes(
        self, client, module_upstream, extension_names, post_writes
    ):
        module_upstream.document["components"]["schemas"]["WidgetConfigV1"] = {
            "type": "object", "additionalProperties": {"type": "integer"},
        }
        await _enable(client, extension_names, _config_only(), module_upstream.base_url)
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", content=b'{"\\ud800":"bad"}')
        assert response.status_code == 422
        assert response.json()["errors"] == [
            {"loc": ["\ud800"], "rule": "type", "msg": "must match the declared type"}
        ]
        assert "bad" not in response.text
        _assert_no_writes(post_writes)

    async def test_sa_prefixed_properties_match_input_blob_and_http(self, client, post_writes):
        payload = {"_sa_keep": None, "nested": {"_sa_name": "x"}, "_sampleRate": 5,
                   "items": [{"_sa_item": None}]}
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=payload)
        assert response.status_code == 200
        assert response.json() == [payload, 200]
        assert json.loads(post_writes.uploads[0][0]) == payload
        assert post_writes.blobs == [f"{DEVICE}/{MODULE}/current"]
        assert len(post_writes.patches) == 1
