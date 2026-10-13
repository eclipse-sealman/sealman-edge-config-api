"""Combined module/response_ref contracts through real management, PostgreSQL and
loopback HTTP. These generated models are neutral framework fixtures, not protocol
or live-cloud parity evidence. Only external Blob/IoT Hub I/O is replaced.
"""

import copy
import json
import re
import threading
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from azure.core.exceptions import ResourceNotFoundError
from fastapi import FastAPI
from jsonschema import Draft202012Validator
from pydantic import ConfigDict, Field, create_model

import auth
from db.repos.extension import ExtensionRepository
from db.repos.role import RoleRepository
from db.session import get_repository
from extensions import body_validation, setup as extensions_setup
from extensions.upstreams import iotedge as iotedge_upstream
from main import app
from routers.module_config.registry import module_schema_registry
from routers.module_config.routes import get_module_twin_config as read_route
from routers.module_config.routes import get_module_twin_identity_reported as reported_route
from routers.module_config.routes import post_module_twin_config as write_route
from tests.integration.test_abac_authorization import AbacFixtures
from tests.integration.test_extensions_management_routes import (  # noqa: F401
    _fresh_http_upstream_client,
    _unique_name,
)

DEVICE = "neutral-device"
MODULE = "neutral-module"
FIXED_IDS = ("seal-module-opcua-client", "seal-app-net-discover")
MODULE_COMPONENTS = dict(zip((MODULE, *FIXED_IDS), ("NeutralConfig", "FixedAConfig", "FixedBConfig")))
CONFIG_PATH = "/{device}/twin/config/{module}"
REPORTED_PATH = "/{device}/twin/identity/{module}/reported"
MISMATCH = {"message": "Module data could not be validated against its active schema"}
UNAVAILABLE = {"message": "Module schema is unavailable"}
TWIN_URL = re.compile(r"^https://testhub.invalid/twins/([^/]+)/modules/([^?]+)\?api-version=2020-05-31-preview$")


def _provider_document(configuration_version=1, reported_version=1, minimum=1):
    """Generate OpenAPI rather than inventing snapshots; component names deliberately
    repeat across providers, purposes and the dynamic response contract."""
    provider = FastAPI()
    detail = create_model(
        "NeutralDetail", __config__=ConfigDict(extra="allow"),
        label=(str, ...), samples=(list[int | None], ...),
    )
    diagnostic = create_model(
        "NeutralDiagnostic", count=(int, Field(ge=reported_version - 1)), detail=(detail | None, None),
    )
    reported = create_model(
        "NeutralReported", __config__=ConfigDict(title=f"NeutralReportedV{reported_version}", extra="forbid"),
        config=(dict[str, str] | None, None), diagnostic=(diagnostic | None, None),
        diagnostics=(dict[str, list[str]] | None, None), lastUpdated=(str | None, None),
    )
    for offset, component in enumerate(MODULE_COMPONENTS.values()):
        configuration = create_model(
            component, __config__=ConfigDict(title=f"{component}V{configuration_version}", extra="allow"),
            setting=(int, Field(ge=minimum + offset)), nullable=(str | None, ...),
            detail=(detail, ...), interval=(int, 30),
        )

        # The provider endpoints exist to produce the service's schema. The core
        # only fetches /openapi.json; its module requests never proxy here.
        async def config_endpoint():
            return {}

        provider.add_api_route(f"/schema/{component}", config_endpoint, response_model=configuration)

    async def reported_endpoint():
        return {}

    async def probe_endpoint():
        return {}

    provider.add_api_route("/schema/reported", reported_endpoint, response_model=reported)
    probe = create_model(
        "NeutralProbe", __config__=ConfigDict(title=f"NeutralProbeV{configuration_version}"),
        detail=(detail, ...),
    )
    provider.add_api_route("/probe", probe_endpoint, response_model=probe)
    return provider.openapi()


class _ProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        self.server.calls[parsed.path] += 1
        if parsed.path == "/openapi.json" and self.server.document is not None:
            status, value = 200, self.server.document
        elif parsed.path == "/probe":
            # Intentionally violates NeutralProbe: response_ref is documentation
            # only, not a validator, projection or response-model coercion.
            status, value = 200, {"literal": None, "query": parse_qs(parsed.query)}
        else:
            status, value = 404, {}
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def provider_factory():
    servers = []

    def start(**versions):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
        server.document = _provider_document(**versions)
        server.calls = Counter()
        server.base_url = f"http://127.0.0.1:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return server

    try:
        yield start
    finally:
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            assert not thread.is_alive()


@pytest.fixture(autouse=True)
def lifecycle_state():
    """Restore shared state even on failed assertions; unmount only owned routes."""
    active = module_schema_registry._active.copy()
    claims = module_schema_registry._claims.copy()
    unavailable = module_schema_registry._unavailable.copy()
    validators = body_validation._module_validator_cache.copy()
    public_cache = app.openapi_schema
    internal_cache = extensions_setup.internal_app.openapi_schema
    module_schema_registry._active.clear()
    module_schema_registry._claims.clear()
    module_schema_registry._unavailable.clear()
    body_validation._module_validator_cache.clear()
    app.openapi_schema = None
    owned = []
    try:
        yield owned
    finally:
        for name in owned:
            extensions_setup._unmount_extension(name)
            extensions_setup._extension_locks.pop(name, None)
        module_schema_registry._active = active
        module_schema_registry._claims = claims
        module_schema_registry._unavailable = unavailable
        body_validation._module_validator_cache.clear()
        body_validation._module_validator_cache.update(validators)
        app.openapi_schema = public_cache
        extensions_setup.internal_app.openapi_schema = internal_cache


@pytest.fixture
def hydrate(db_session, monkeypatch):
    @asynccontextmanager
    async def session():
        yield db_session

    monkeypatch.setattr(extensions_setup, "AsyncSessionLocal", session)
    return extensions_setup.hydrate_all_routes


@dataclass
class _Cloud:
    blobs: dict = field(default_factory=dict)
    twins: dict = field(default_factory=dict)
    effects: list = field(default_factory=list)
    direct_methods: int = 0


@pytest.fixture
def cloud(monkeypatch):
    cloud = _Cloud()

    class Blob:
        def __init__(self, name):
            self.name = name

        async def upload_blob(self, data, overwrite):
            cloud.effects.append(("upload", self.name, data, overwrite))
            cloud.blobs[self.name] = data.encode()

        async def download_blob(self):
            cloud.effects.append(("download", self.name))
            if self.name not in cloud.blobs:
                raise ResourceNotFoundError("synthetic missing blob")
            return self

        async def readall(self):
            return cloud.blobs[self.name]

    class Context:
        def __init__(self, *args, **kwargs):
            cloud.effects.append(("container", args, kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get_blob_client(self, name):
            cloud.effects.append(("blob", name))
            return Blob(name)

    async def get(url, responses, headers=None, timeout=5):
        device, module = TWIN_URL.fullmatch(url).groups()
        cloud.effects.append(("twin_read", device, module, headers))
        twin = cloud.twins.get((device, module))
        responses[url] = SimpleNamespace(status_code=200 if twin is not None else 404, text=json.dumps(twin or {}))

    async def patch(url, responses, _json, headers):
        assert TWIN_URL.fullmatch(url)
        cloud.effects.append(("patch", url, _json, headers))
        responses[url] = SimpleNamespace(status_code=200, text="{}")

    def direct_method(*args, **kwargs):
        cloud.direct_methods += 1
        raise AssertionError("module operations must not call direct methods")

    for route in (read_route, write_route):
        monkeypatch.setattr(route, "PUBLIC_STORAGE_ACCOUNT_NAME", "teststorage")
        monkeypatch.setattr(route, "BLOB_SAS_TOKEN_MODULE_CONF", "sig=synthetic")
        monkeypatch.setattr(route, "BlobContainerContext", Context)
    for route in (read_route, reported_route, write_route):
        monkeypatch.setattr(route, "IOT_HUB_NAME", "testhub.invalid")
        monkeypatch.setattr(route, "get_iothub_auth_headers", lambda: {"Authorization": "synthetic"})
    for route in (read_route, reported_route):
        monkeypatch.setattr(route, "get_async", get)
    monkeypatch.setattr(write_route, "patch_async", patch)
    monkeypatch.setattr(iotedge_upstream, "_direct_method", direct_method)
    return cloud


def _manifest(name, provider, modules=None):
    modules = MODULE_COMPONENTS if modules is None else modules
    return {
        "schema_version": 1, "name": name, "description": "Neutral combined lifecycle fixture",
        "upstreams": {"svc": {"type": "http", "base_url": provider.base_url}},
        "actions": [{"name": f"{name}.probe", "description": "Independent probe permission"}],
        "routes": [{
            "upstream": "svc", "path": f"/{name}/probe", "upstream_path": "/probe", "method": "GET",
            "response_ref": True, "required_action": f"{name}.probe", "summary": "Neutral probe",
            "query_params": [{"name": "sample", "type": "string", "required": True}],
        }],
        "module_schemas": {
            module: {"upstream": "svc", "schema_ref": f"#/components/schemas/{component}",
                     "reported_schema_ref": "#/components/schemas/NeutralReported"}
            for module, component in modules.items()
        },
    }


async def _register(client, owned, provider, modules=None, enable=True):
    name = _unique_name("neutral_provider")
    owned.append(name)
    manifest = _manifest(name, provider, modules)
    schema = await client.get("/extensions/schema")
    assert schema.status_code == 200
    Draft202012Validator(schema.json()).validate(manifest)
    response = await client.post("/extensions", json=manifest)
    assert response.status_code == 201, response.text
    assert response.json()["enabled"] is False
    if enable:
        response = await client.post(f"/extensions/{name}/enable")
        assert response.status_code == 200, response.text
    return name, manifest


def _payload(setting=3):
    return {"setting": setting, "nullable": None,
            "detail": {"label": "neutral", "samples": [1, None, 1], "extra": None},
            "extra": {"keep": None}, "_sa_fixture": [None, "supplied"]}


def _set_reported(cloud, module, projection=None):
    reported = {"config": {"status": "snapshot"}, "diagnostic": {"count": 2},
                "diagnostics": {"mixed": ["a", "b"]},
                "$metadata": {"$lastUpdated": "2026-10-13T08:00:00Z"},
                "configId": "not-returned", "other": "not-returned"}
    if projection is not None:
        reported = projection
    cloud.twins[(DEVICE, module)] = {"properties": {"reported": reported, "desired": {"secret": "not-returned"}}}


async def _round_trip(client, cloud, module, payload):
    start = len(cloud.effects)
    response = await client.post(f"/{DEVICE}/twin/config/{module}", json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == [payload, 200]
    container, blob, upload, patch = cloud.effects[start:]
    assert container == ("container", ("teststorage", "iotedge-device-twin"), {"sas_token": "sig=synthetic"})
    assert blob == ("blob", f"{DEVICE}/{module}/current")
    assert upload == ("upload", f"{DEVICE}/{module}/current", json.dumps(payload, separators=(",", ":")), True)
    _, url, body, headers = patch
    config_id = body["properties"]["desired"]["configId"]
    assert len(config_id) == 32 and UUID(config_id).hex == config_id
    assert url == f"https://testhub.invalid/twins/{DEVICE}/modules/{module}?api-version=2020-05-31-preview"
    assert body == {"properties": {"desired": {
        "configBlobUrl": f"https://teststorage.blob.core.windows.net/iotedge-device-twin/{DEVICE}/{module}/current?sig=synthetic",
        "configId": config_id,
    }}}
    assert headers == {"Authorization": "synthetic"}
    assert json.loads(cloud.blobs[f"{DEVICE}/{module}/current"]) == payload
    read = await client.get(f"/{DEVICE}/twin/config/{module}")
    assert read.status_code == 200 and read.json() == payload
    assert cloud.effects[start + 4:] == [container, blob, ("download", f"{DEVICE}/{module}/current")]


def _resolve(document, schema):
    while "$ref" in schema:
        schema = document["components"]["schemas"][schema["$ref"].removeprefix("#/components/schemas/")]
    return schema


def _assert_refs(node, document):
    if isinstance(node, list):
        for value in node:
            _assert_refs(value, document)
    elif isinstance(node, dict):
        if "$ref" in node:
            assert node["$ref"].startswith("#/components/schemas/")
            assert node["$ref"].removeprefix("#/components/schemas/") in document["components"]["schemas"]
        for value in node.values():
            _assert_refs(value, document)


async def _openapi(client, core, configurations=None, reported=None, probes=None):
    configurations, reported, probes = configurations or {}, reported or {}, probes or {}
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    document = response.json()
    cached = copy.deepcopy(app.openapi_schema)
    assert (await client.get("/openapi.json")).json() == document
    assert app.openapi_schema == cached
    assert not any(key.startswith(("Module_", "ext_")) for key in cached["components"]["schemas"])
    _assert_refs(document, document)
    for path, expected in ((CONFIG_PATH, configurations), (REPORTED_PATH, reported)):
        operation = document["paths"][path]["get"]
        original = core["paths"][path]["get"]
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        if expected:
            assert "oneOf" not in schema
            alternatives = schema["anyOf"]
            assert alternatives[-1] == {"type": "null"}
            assert sorted(_resolve(document, item)["title"] for item in alternatives[1:-1]) == sorted(expected.values())
            if path == REPORTED_PATH:
                for alternative in alternatives[1:-1]:
                    root = _resolve(document, alternative)
                    diagnostic = _resolve(document, root["properties"]["diagnostic"]["anyOf"][0])
                    nested = diagnostic["properties"]["detail"]["anyOf"][0]["$ref"]
                    assert nested.startswith("#/components/schemas/Module_reported_")
                    assert _resolve(document, {"$ref": nested})["title"] == "NeutralDetail"
        else:
            assert schema == original["responses"]["200"]["content"]["application/json"]["schema"]
        for key in ("operationId", "security", "parameters", "tags"):
            assert operation[key] == original[key]
    post = document["paths"][CONFIG_PATH]["post"]
    for key in ("operationId", "security", "parameters", "tags"):
        assert post[key] == core["paths"][CONFIG_PATH]["post"][key]
    assert post["requestBody"]["required"] is True
    alternatives = post["requestBody"]["content"]["application/json"]["schema"]["anyOf"]
    assert alternatives[0] == {"type": "object"}
    roots = alternatives[1:]
    assert sorted(_resolve(document, schema)["title"] for schema in roots) == sorted(configurations.values())
    for root in roots:
        nested = _resolve(document, root)["properties"]["detail"]["$ref"]
        assert nested.startswith("#/components/schemas/Module_configuration_")
        assert _resolve(document, {"$ref": nested})["properties"]["samples"]["items"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    for module in FIXED_IDS:
        path = f"/{{device}}/twin/config/{module}"
        operations = document["paths"][path]
        for method in ("get", "post"):
            assert operations[method]["deprecated"] is True
            for key in ("operationId", "security", "parameters", "tags"):
                assert operations[method][key] == core["paths"][path][method][key]
        get_schema = operations["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert operations["post"]["requestBody"]["required"] is True
        post_schema = operations["post"]["requestBody"]["content"]["application/json"]["schema"]
        if module in configurations:
            root = next(schema for schema in roots if _resolve(document, schema)["title"] == configurations[module])
            assert get_schema == {"anyOf": [root, {"type": "null"}]}
            assert post_schema == root
        else:
            assert get_schema == core["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
            assert post_schema == {"anyOf": [{"type": "object"}]}
    dynamic_paths = {path for path in document["paths"] if path.endswith("/probe")}
    assert dynamic_paths == set(probes)
    for path, title in probes.items():
        operation = document["paths"][path]["get"]
        root = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert _resolve(document, root)["title"] == title
        assert _resolve(document, root)["properties"]["detail"]["$ref"].startswith("#/components/schemas/ext_")
        assert operation["summary"] == "Neutral probe"
        assert operation["security"] == post["security"] and operation["security"]
        query = next(item for item in operation["parameters"] if item["name"] == "sample")
        assert query["in"] == "query" and query["required"] is True
    return document


def _titles(version):
    return {module: f"{component}V{version}" for module, component in MODULE_COMPONENTS.items()}


async def test_combined_lifecycle_literal_round_trip_reported_and_mixed_openapi(
    client, db_session, provider_factory, cloud, lifecycle_state, hydrate,
):
    provider = provider_factory()
    core = (await client.get("/openapi.json")).json()
    name, manifest = await _register(client, lifecycle_state, provider, enable=False)
    repo = get_repository(ExtensionRepository)(db_session)
    rows = await repo.list_module_schemas(name)
    assert {(row["module_id"], row["purpose"]) for row in rows} == {
        (module, purpose) for module in MODULE_COMPONENTS for purpose in ("configuration", "reported")
    }
    assert len({row["version"] for row in rows}) == 4  # three configurations plus one shared reported contract
    assert all(row["resolved_at"] and len(row["version"]) == 64 for row in rows)
    await _openapi(client, core)
    calls = provider.calls.copy()
    await _round_trip(client, cloud, MODULE, {"fallback": None})
    assert provider.calls == calls

    assert (await client.post(f"/extensions/{name}/enable")).status_code == 200
    assert provider.calls["/openapi.json"] == 2
    reported_titles = dict.fromkeys(MODULE_COMPONENTS, "NeutralReportedV1")
    probes = {f"/{name}/probe": "NeutralProbeV1"}
    enabled = await _openapi(client, core, _titles(1), reported_titles, probes)
    calls = provider.calls.copy()
    for offset, module in enumerate(MODULE_COMPONENTS):
        # All three IDs are active together with different minima. A fixed alias
        # must not accidentally select a sibling's otherwise identical schema.
        before_effects = len(cloud.effects)
        invalid = await client.post(f"/{DEVICE}/twin/config/{module}", json=_payload(offset))
        assert invalid.status_code == 422 and invalid.json()["errors"][0]["rule"] == "minimum"
        assert len(cloud.effects) == before_effects
        await _round_trip(client, cloud, module, _payload(offset + 1))
        _set_reported(cloud, module)
        start = len(cloud.effects)
        response = await client.get(f"/{DEVICE}/twin/identity/{module}/reported")
        assert response.status_code == 200
        assert response.json() == {"config": {"status": "snapshot"}, "diagnostic": {"count": 2},
                                   "diagnostics": {"mixed": ["a", "b"]}, "lastUpdated": "2026-10-13T08:00:00Z"}
        assert cloud.effects[start:] == [("twin_read", DEVICE, module, {"Authorization": "synthetic"})]
        for projection, expected in (({"diagnostics": {"partial": []}}, {"diagnostics": {"partial": []}}),
                                     ({"diagnostic": None}, {"diagnostic": None}), ({"other": 1}, None)):
            _set_reported(cloud, module, projection)
            response = await client.get(f"/{DEVICE}/twin/identity/{module}/reported")
            assert response.status_code == 200 and response.json() == expected
    assert provider.calls == calls and cloud.direct_methods == 0
    probe = await client.get(f"/{name}/probe", params={"sample": "raw value", "extra": "forwarded"})
    assert probe.status_code == 200
    assert probe.json() == {"literal": None, "query": {"sample": ["raw value"], "extra": ["forwarded"]}}
    assert provider.calls["/probe"] == 1

    provider.document = _provider_document(configuration_version=2, reported_version=2, minimum=10)
    assert (await client.put(f"/extensions/{name}", json=manifest)).status_code == 200
    replaced = await _openapi(client, core, _titles(2), dict.fromkeys(MODULE_COMPONENTS, "NeutralReportedV2"),
                              {f"/{name}/probe": "NeutralProbeV2"})
    assert "ConfigV1" not in json.dumps(replaced) and "ReportedV1" not in json.dumps(replaced)
    assert enabled != replaced
    calls = provider.calls.copy()
    for module in MODULE_COMPONENTS:
        read = await client.get(f"/{DEVICE}/twin/config/{module}")
        assert read.status_code == 500 and read.json() == MISMATCH
        before = len(cloud.effects)
        invalid = await client.post(f"/{DEVICE}/twin/config/{module}", json=_payload())
        assert invalid.status_code == 422 and invalid.json()["errors"][0]["rule"] == "minimum"
        assert len(cloud.effects) == before
        await _round_trip(client, cloud, module, _payload(12))
    assert provider.calls == calls

    # Explicit startup entrypoint, not direct registry activation or ASGI lifespan.
    extensions_setup._unmount_extension(name)
    await _openapi(client, core)
    await hydrate()
    assert (await repo.get_extension_row(name))["enabled"] is True
    await _openapi(client, core, _titles(2), dict.fromkeys(MODULE_COMPONENTS, "NeutralReportedV2"),
                   {f"/{name}/probe": "NeutralProbeV2"})
    for module in MODULE_COMPONENTS:
        assert module_schema_registry.get(module, "configuration").extension_name == name

    for operation in ("disable", "enable", "disable"):
        assert (await client.post(f"/extensions/{name}/{operation}")).status_code == 200
        if operation == "enable":
            await _openapi(client, core, _titles(2), dict.fromkeys(MODULE_COMPONENTS, "NeutralReportedV2"),
                           {f"/{name}/probe": "NeutralProbeV2"})
        else:
            await _openapi(client, core)
            calls = provider.calls.copy()
            for module in MODULE_COMPONENTS:
                await _round_trip(client, cloud, module, {"setting": "no-coercion", "keep": None})
                _set_reported(cloud, module, {"diagnostic": {"count": "generic"}})
                response = await client.get(f"/{DEVICE}/twin/identity/{module}/reported")
                assert response.status_code == 200 and response.json() == {"diagnostic": {"count": "generic"}}
            assert provider.calls == calls
    assert (await client.delete(f"/extensions/{name}")).status_code == 204
    await _openapi(client, core)
    assert await repo.list_module_schemas(name) == []
    calls = provider.calls.copy()
    for module in MODULE_COMPONENTS:
        assert await repo.get_module_schema_owner(module) is None
        await _round_trip(client, cloud, module, {"deregistered": None})
        response = await client.get(f"/{DEVICE}/twin/identity/{module}/reported")
        assert response.status_code == 200 and response.json() == {"diagnostic": {"count": "generic"}}
    assert provider.calls == calls and cloud.direct_methods == 0


async def test_failed_register_and_replace_preserve_manifest_snapshots_runtime_and_openapi(
    client, db_session, provider_factory, cloud, lifecycle_state,
):
    provider = provider_factory()
    core = (await client.get("/openapi.json")).json()
    name, manifest = await _register(client, lifecycle_state, provider)
    repo = get_repository(ExtensionRepository)(db_session)
    before_rows = copy.deepcopy(await repo.list_module_schemas(name))
    before_routes = copy.deepcopy(await repo.list_routes(name))
    before_manifest = (await client.get(f"/extensions/{name}")).json()
    before_runtime = module_schema_registry.list_active()
    before_openapi = await _openapi(client, core, _titles(1), dict.fromkeys(MODULE_COMPONENTS, "NeutralReportedV1"),
                                   {f"/{name}/probe": "NeutralProbeV1"})
    provider.document = _provider_document(configuration_version=2, minimum=10)
    invalid = copy.deepcopy(manifest)
    invalid["description"] = "must never be saved"
    invalid["module_schemas"][MODULE]["reported_schema_ref"] = "#/components/schemas/MissingReported"
    response = await client.put(f"/extensions/{name}", json=invalid)
    assert response.status_code == 422
    assert (await client.get(f"/extensions/{name}")).json() == before_manifest
    assert await repo.list_module_schemas(name) == before_rows
    assert await repo.list_routes(name) == before_routes
    assert module_schema_registry.list_active() == before_runtime
    assert (await client.get("/openapi.json")).json() == before_openapi

    invalid_name = _unique_name("invalid_provider")
    lifecycle_state.append(invalid_name)
    invalid["name"] = invalid_name
    response = await client.post("/extensions", json=invalid)
    assert response.status_code == 422
    assert (await client.get(f"/extensions/{invalid_name}")).status_code == 404
    assert await repo.list_module_schemas(invalid_name) == []
    assert await repo.list_routes(invalid_name) == []
    assert await repo.list_extension_actions(invalid_name) == []
    calls = provider.calls.copy()
    for module in MODULE_COMPONENTS:
        await _round_trip(client, cloud, module, _payload())  # still governed by V1
        _set_reported(cloud, module, {"diagnostic": {"count": "secret-data"}})
        response = await client.get(f"/{DEVICE}/twin/identity/{module}/reported")
        assert response.status_code == 500 and response.json() == MISMATCH
    assert provider.calls == calls and cloud.direct_methods == 0


@pytest.mark.parametrize("failed_purpose", ["configuration", "reported"])
@pytest.mark.parametrize("trigger", ["enable", "hydrate"])
async def test_refresh_retains_failed_purpose_and_independently_advances_successful_version(
    client, db_session, provider_factory, cloud, lifecycle_state, hydrate, failed_purpose, trigger,
):
    provider = provider_factory()
    core = (await client.get("/openapi.json")).json()
    name, _ = await _register(client, lifecycle_state, provider, {MODULE: "NeutralConfig"})
    repo = get_repository(ExtensionRepository)(db_session)
    before = {row["purpose"]: row for row in await repo.list_module_schemas(name)}
    provider.document = _provider_document(configuration_version=2, reported_version=2, minimum=10)
    missing = "NeutralConfig" if failed_purpose == "configuration" else "NeutralReported"
    del provider.document["components"]["schemas"][missing]
    if trigger == "enable":
        assert (await client.post(f"/extensions/{name}/disable")).status_code == 200
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200
    else:
        extensions_setup._unmount_extension(name)
        await hydrate()
    after = {row["purpose"]: row for row in await repo.list_module_schemas(name)}
    successful = "reported" if failed_purpose == "configuration" else "configuration"
    # Replacing the row set regenerates database surrogate IDs, not snapshots.
    assert {key: value for key, value in after[failed_purpose].items() if key != "id"} == {
        key: value for key, value in before[failed_purpose].items() if key != "id"
    }
    assert after[successful]["version"] != before[successful]["version"]
    assert after[successful]["schema"] != before[successful]["schema"]
    for purpose, row in after.items():
        assert module_schema_registry.get(MODULE, purpose).version == row["version"]
    await _openapi(client, core, {MODULE: f"NeutralConfigV{1 if failed_purpose == 'configuration' else 2}"},
                   {MODULE: f"NeutralReportedV{1 if failed_purpose == 'reported' else 2}"},
                   {f"/{name}/probe": "NeutralProbeV2"})
    calls = provider.calls.copy()
    if failed_purpose == "configuration":
        await _round_trip(client, cloud, MODULE, _payload())
        _set_reported(cloud, MODULE, {"diagnostic": {"count": 0}})
        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")
        assert response.status_code == 500 and response.json() == MISMATCH  # refreshed reported V2
    else:
        before_effects = len(cloud.effects)
        assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=_payload())).status_code == 422
        assert len(cloud.effects) == before_effects
        await _round_trip(client, cloud, MODULE, _payload(10))
        _set_reported(cloud, MODULE, {"diagnostic": {"count": 0}})
        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")
        assert response.status_code == 200 and response.json() == {"diagnostic": {"count": 0}}
    # The failed purpose's old constraints remain enforced, not generic fallback.
    before_effects = len(cloud.effects)
    assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=_payload(0))).status_code == 422
    assert len(cloud.effects) == before_effects
    _set_reported(cloud, MODULE, {"diagnostic": {"count": "secret"}})
    response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")
    assert response.status_code == 500 and response.json() == MISMATCH
    assert provider.calls == calls and cloud.direct_methods == 0


@pytest.mark.parametrize("module", MODULE_COMPONENTS)
@pytest.mark.parametrize("purpose", ["configuration", "reported"])
async def test_invalid_persisted_syntax_hydrates_as_claimed_unavailable_before_any_effect(
    client, db_session, provider_factory, cloud, lifecycle_state, hydrate, monkeypatch, module, purpose,
):
    provider = provider_factory()
    core = (await client.get("/openapi.json")).json()
    name, _ = await _register(client, lifecycle_state, provider, {module: MODULE_COMPONENTS[module]})
    repo = get_repository(ExtensionRepository)(db_session)
    rows = await repo.list_module_schemas(name)
    await repo.replace_module_schemas(name, [
        {**row, "schema": {"type": "invalid-json-schema-type"}} if row["purpose"] == purpose else row for row in rows
    ])
    provider.document = None  # real failed HTTP refresh cannot repair the corrupted persisted syntax
    extensions_setup._unmount_extension(name)
    await hydrate()
    assert module_schema_registry.claim_owner(module) == name
    assert module_schema_registry.is_unavailable(module)
    assert module_schema_registry.get(module, "configuration") is None
    assert module_schema_registry.get(module, "reported") is None
    await _openapi(client, core, probes={f"/{name}/probe": "NeutralProbeV1"})
    calls = provider.calls.copy()
    for path in (f"/{DEVICE}/twin/config/{module}", f"/{DEVICE}/twin/identity/{module}/reported"):
        response = await client.get(path)
        assert response.status_code == 503 and response.json() == UNAVAILABLE
    for raw in ("not JSON", " " * (1024 * 1024 + 1)):
        response = await client.post(f"/{DEVICE}/twin/config/{module}", content=raw)
        assert response.status_code == 503 and response.json() == UNAVAILABLE
    assert cloud.effects == [] and provider.calls == calls and cloud.direct_methods == 0

    # Real non-admin ABAC still precedes an unavailable claim, registry lookup
    # and body buffering for configuration AND reported, including fixed IDs.
    await AbacFixtures(db_session).setup(users={"denied": "denied-neutral-oid"})

    async def denied():
        return {"oid": "denied-neutral-oid", "sub": "denied-neutral-oid", "roles": []}

    def forbidden(*args, **kwargs):
        raise AssertionError("403 must precede schema lookup and body buffering")

    monkeypatch.setattr(module_schema_registry, "is_unavailable", forbidden)
    monkeypatch.setattr(module_schema_registry, "get", forbidden)
    monkeypatch.setattr(body_validation, "read_limited_body", forbidden)
    previous = app.dependency_overrides[auth.validate_jwt]
    app.dependency_overrides[auth.validate_jwt] = denied
    try:
        for method, path in (("get", f"/{DEVICE}/twin/config/{module}"),
                             ("post", f"/{DEVICE}/twin/config/{module}"),
                             ("get", f"/{DEVICE}/twin/identity/{module}/reported")):
            response = await getattr(client, method)(path)
            assert response.status_code == 403
        assert cloud.effects == [] and provider.calls == calls
    finally:
        app.dependency_overrides[auth.validate_jwt] = previous


async def test_identical_component_names_isolate_providers_modules_purposes_and_reported_removal(
    client, provider_factory, cloud, lifecycle_state,
):
    first, second = provider_factory(), provider_factory(minimum=20)
    core = (await client.get("/openapi.json")).json()
    name, manifest = await _register(client, lifecycle_state, first, {MODULE: "NeutralConfig"})
    other_module = "second-neutral-module"
    other_name, _ = await _register(client, lifecycle_state, second, {other_module: "NeutralConfig"})
    configurations = {MODULE: "NeutralConfigV1", other_module: "NeutralConfigV1"}
    reported = dict.fromkeys(configurations, "NeutralReportedV1")
    probes = {f"/{name}/probe": "NeutralProbeV1", f"/{other_name}/probe": "NeutralProbeV1"}
    document = await _openapi(client, core, configurations, reported, probes)
    alternatives = document["paths"][CONFIG_PATH]["post"]["requestBody"]["content"]["application/json"]["schema"]["anyOf"][1:]
    roots = [_resolve(document, item) for item in alternatives]
    assert len({item["properties"]["detail"]["$ref"] for item in roots}) == 2
    assert sorted(item["properties"]["setting"]["minimum"] for item in roots) == [1, 20]
    calls = (first.calls.copy(), second.calls.copy())
    await _round_trip(client, cloud, MODULE, _payload())
    before_effects = len(cloud.effects)
    assert (await client.post(f"/{DEVICE}/twin/config/{other_module}", json=_payload())).status_code == 422
    assert len(cloud.effects) == before_effects
    await _round_trip(client, cloud, other_module, _payload(20))
    await _round_trip(client, cloud, "unknown-neutral-module", {"setting": "fallback", "keep": None})
    assert (first.calls, second.calls) == calls
    assert (await client.post(f"/extensions/{name}/disable")).status_code == 200
    duplicate = _manifest(_unique_name("duplicate_owner"), second, {MODULE: "NeutralConfig"})
    response = await client.post("/extensions", json=duplicate)
    assert response.status_code == 409
    assert (await client.get(f"/extensions/{duplicate['name']}")).status_code == 404
    assert (await client.post(f"/extensions/{name}/enable")).status_code == 200
    del manifest["module_schemas"][MODULE]["reported_schema_ref"]
    assert (await client.put(f"/extensions/{name}", json=manifest)).status_code == 200
    await _openapi(client, core, configurations, {other_module: "NeutralReportedV1"}, probes)
    calls = (first.calls.copy(), second.calls.copy())
    for module, expected_status in ((MODULE, 200), (other_module, 500), ("unknown-neutral-module", 200)):
        _set_reported(cloud, module, {"diagnostic": {"count": "generic"}})
        response = await client.get(f"/{DEVICE}/twin/identity/{module}/reported")
        assert response.status_code == expected_status
        assert response.json() == ({"diagnostic": {"count": "generic"}} if expected_status == 200 else MISMATCH)
    assert (first.calls, second.calls) == calls
    # Legacy manifests still mount response_ref without any module declaration.
    legacy_name = _unique_name("legacy_provider")
    lifecycle_state.append(legacy_name)
    legacy = _manifest(legacy_name, second, {})
    del legacy["module_schemas"]
    assert (await client.post("/extensions", json=legacy)).status_code == 201
    assert (await client.post(f"/extensions/{legacy_name}/enable")).status_code == 200
    await _openapi(client, core, configurations, {other_module: "NeutralReportedV1"},
                   {**probes, f"/{legacy_name}/probe": "NeutralProbeV1"})


@pytest.mark.parametrize("module", MODULE_COMPONENTS)
@pytest.mark.parametrize("actions,region,read_status,write_status", [
    (["device.read"], "EU", 200, 403),
    (["device.module_twin_config.write"], "EU", 403, 200),
    (["device.read", "device.module_twin_config.write"], "US", 403, 403),
    ([], "EU", 403, 403),
    (["device.read", "device.module_twin_config.write"], "EU", 200, 200),
])
async def test_non_admin_module_scope_and_probe_action_are_independent(
    client, db_session, provider_factory, cloud, lifecycle_state, module, actions, region, read_status, write_status,
):
    provider = provider_factory()
    name, _ = await _register(client, lifecycle_state, provider)
    world = await AbacFixtures(db_session).setup(
        users={"caller": "neutral-caller-oid"},
        scopes={"eu": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
        roles={"module-role": actions},
        teams={"module-team": {"scope": "eu", "roles": ["module-role"], "users": ["caller"]}},
        devices={DEVICE: {"region": region}},
    )
    identity = {"oid": "neutral-caller-oid", "sub": "neutral-caller-oid", "roles": []}

    async def caller():
        return identity

    previous = app.dependency_overrides[auth.validate_jwt]
    app.dependency_overrides[auth.validate_jwt] = caller
    try:
        calls = provider.calls.copy()
        cloud.blobs[f"{DEVICE}/{module}/current"] = json.dumps(_payload()).encode()
        _set_reported(cloud, module)
        for path in (f"/{DEVICE}/twin/config/{module}", f"/{DEVICE}/twin/identity/{module}/reported"):
            start = len(cloud.effects)
            response = await client.get(path)
            assert response.status_code == read_status
            if read_status == 403:
                assert len(cloud.effects) == start
        start = len(cloud.effects)
        response = await client.post(f"/{DEVICE}/twin/config/{module}", json=_payload())
        assert response.status_code == write_status
        if write_status == 403:
            assert len(cloud.effects) == start
            # Both schema-invalid and malformed/oversized bodies remain hidden.
            for raw in (json.dumps({"setting": "secret"}), "not JSON", " " * (1024 * 1024 + 1)):
                response = await client.post(f"/{DEVICE}/twin/config/{module}", content=raw)
                assert response.status_code == 403 and "errors" not in response.json()
                assert len(cloud.effects) == start
        assert (await client.get(f"/{name}/probe", params={"sample": "value"})).status_code == 403
        assert provider.calls == calls and cloud.direct_methods == 0
        role_repo = get_repository(RoleRepository)(db_session)
        await role_repo.add_actions_to_role(world.roles["module-role"], [f"{name}.probe"])
        response = await client.get(f"/{name}/probe", params={"sample": "value"})
        assert response.status_code == 200  # global probe permission, independent of device region/actions
        assert response.json() == {"literal": None, "query": {"sample": ["value"]}}
        assert provider.calls["/probe"] == 1
    finally:
        app.dependency_overrides[auth.validate_jwt] = previous


async def test_missing_credentials_rejected_by_public_dependency_chain_without_oidc(
    client, provider_factory, cloud, lifecycle_state, monkeypatch,
):
    provider = provider_factory()
    name, _ = await _register(client, lifecycle_state, provider)

    async def forbidden_oidc():
        raise AssertionError("missing bearer must be rejected before OIDC network I/O")

    monkeypatch.setattr(auth, "ensure_oidc_initialized", forbidden_oidc)
    previous = app.dependency_overrides.pop(auth.validate_jwt)
    calls = provider.calls.copy()
    try:
        for method, path in (("get", "/extensions/schema"), ("get", f"/{name}/probe?sample=value"),
                             ("get", f"/{DEVICE}/twin/identity/{MODULE}/reported"),
                             *((method, f"/{DEVICE}/twin/config/{module}")
                               for module in MODULE_COMPONENTS for method in ("get", "post"))):
            response = await getattr(client, method)(path)
            assert response.status_code == 401
            assert response.json() == {"message": "Not authenticated"}
        assert provider.calls == calls and cloud.effects == [] and cloud.direct_methods == 0
    finally:
        app.dependency_overrides[auth.validate_jwt] = previous


async def test_manifest_guide_examples_match_live_schema_runtime_and_openapi(
    client, provider_factory, cloud, lifecycle_state,
):
    guide = (Path(__file__).parents[2] / "doc/extension/manifest.md").read_text()
    examples = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", guide, re.DOTALL)]
    provider = provider_factory()
    name = _unique_name("guide_provider")
    lifecycle_state.append(name)
    schema = (await client.get("/extensions/schema")).json()
    validator = Draft202012Validator(schema)
    # The two introductory fragments are intentionally not complete manifests.
    # Validate their exact published fields inside a registration envelope.
    for fragment in examples:
        if "schema_version" in fragment:
            validator.validate(fragment)
        else:
            envelope = {"schema_version": 1, "name": name,
                        "upstreams": {"example-http": {"type": "http", "base_url": provider.base_url}},
                        "routes": []}
            if "module_schemas" in fragment:
                envelope.update(fragment)
            else:
                envelope["routes"] = [fragment]
            validator.validate(envelope)
    manifest = next(example for example in examples if "schema_version" in example)
    manifest["name"] = name
    manifest["upstreams"]["svc"]["base_url"] = provider.base_url
    manifest["actions"][0]["name"] = f"{name}.probe"
    manifest["routes"][0]["path"] = f"/{name}/probe"
    manifest["routes"][0]["required_action"] = f"{name}.probe"
    validator.validate(manifest)
    core = (await client.get("/openapi.json")).json()
    response = await client.post("/extensions", json=manifest)
    assert response.status_code == 201 and response.json()["enabled"] is False
    assert (await client.post(f"/extensions/{name}/enable")).status_code == 200
    await _openapi(client, core, {MODULE: "NeutralConfigV1"}, {MODULE: "NeutralReportedV1"},
                   {f"/{name}/probe": "NeutralProbeV1"})
    calls = provider.calls.copy()
    await _round_trip(client, cloud, MODULE, _payload())
    _set_reported(cloud, MODULE, {"diagnostics": {"partial": []}})
    response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")
    assert response.status_code == 200 and response.json() == {"diagnostics": {"partial": []}}
    assert provider.calls == calls
    response = await client.get(f"/{name}/probe", params={"sample": "example"})
    assert response.status_code == 200 and response.json() == {"literal": None, "query": {"sample": ["example"]}}
    assert (await client.delete(f"/extensions/{name}")).status_code == 204
    await _openapi(client, core)


@pytest.mark.parametrize("invalid_case", ["unknown-upstream", "invalid-schema-syntax"])
async def test_initial_module_contract_rejection_has_no_partial_persistence_or_effects(
    client, db_session, provider_factory, cloud, lifecycle_state, invalid_case,
):
    provider = provider_factory()
    name = _unique_name("invalid_initial_provider")
    lifecycle_state.append(name)
    manifest = _manifest(name, provider, {MODULE: "NeutralConfig"})
    if invalid_case == "unknown-upstream":
        manifest["module_schemas"][MODULE]["upstream"] = "missing-upstream"
    else:
        provider.document["components"]["schemas"]["NeutralConfig"]["properties"]["setting"] = {"type": "invalid-type"}
    core = (await client.get("/openapi.json")).json()
    response = await client.post("/extensions", json=manifest)
    assert response.status_code == 422
    assert (await client.get(f"/extensions/{name}")).status_code == 404
    repo = get_repository(ExtensionRepository)(db_session)
    assert await repo.get_extension_row(name) is None
    assert await repo.list_module_schemas(name) == []
    assert await repo.list_upstreams(name) == []
    assert await repo.list_routes(name) == []
    assert await repo.list_extension_actions(name) == []
    assert await repo.get_module_schema_owner(MODULE) is None
    assert module_schema_registry.claim_owner(MODULE) is None
    assert (await client.get("/openapi.json")).json() == core
    assert provider.calls["/openapi.json"] == (0 if invalid_case == "unknown-upstream" else 1)
    assert provider.calls["/probe"] == 0 and cloud.effects == [] and cloud.direct_methods == 0
