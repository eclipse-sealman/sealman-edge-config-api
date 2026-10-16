"""OPCUA lifecycle and canonical configuration adoption of committed artifacts.

No sibling-repository imports or live cloud credentials: PostgreSQL is disposable,
provider OpenAPI travels over real loopback HTTP; only external provider/cloud/JWT
boundaries are replaced.
Configuration uses real core helpers and non-admin ABAC, not a provider callback.
Template HTTP execution uses synthetic committed provider test literals, not a live
catalog. Captured reporter patches are merged by a test-only IoT Hub oracle and
replayed through the real reported helper/GET; no live acquisition is claimed.
"""

import copy
import hashlib
import json
import re
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from uuid import UUID

from starlette.routing import Match

import pytest
from jsonschema import Draft202012Validator

import auth
from authorization.abac_permission_check import ABACPermissionCheck
from db.repos.extension import ExtensionRepository
from db.session import get_repository
from extensions import body_validation, schemas as extension_schemas, setup as extensions_setup
from extensions.upstreams import http as http_upstream
from main import app
from routers.module_config.registry import module_schema_registry
from routers.module_config.routes import get_module_twin_identity_reported as reported_route
from routers.module_config.routes import post_module_twin_config as write_route
from tests.integration.test_abac_authorization import AbacFixtures
from tests.integration.test_extension_direct_method_discovery import forbid_discovery_io  # noqa: F401
from tests.integration.test_module_config import blob_store, iot_hub, post_writes  # noqa: F401
from tests.integration.test_extensions_management_routes import (  # noqa: F401
    _fresh_http_upstream_client,
    _unique_name,
)
from tests.integration.test_module_schema_lifecycle import (  # noqa: F401
    DEVICE,
    MISMATCH,
    UNAVAILABLE,
    _assert_refs,
    _resolve,
    cloud,
    hydrate,
    lifecycle_state,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "opcua"
MODULE = "seal-module-opcua-client"
NAME = "opcua"
HTTP_UPSTREAM = "opcua-http"
CONFIG_COMPONENT = "OpcuaClientModuleConfigV1"
REPORTED_COMPONENT = "OpcuaClientReportedV1"
COMPONENTS = {"configuration": CONFIG_COMPONENT, "reported": REPORTED_COMPONENT}
TEMPLATE_PATHS = {"/template/opc-ua/list": "/opcua/template/list", "/template/opc-ua": "/opcua/template"}
CONFIG_PATH = "/{device}/twin/config/{module}"
REPORTED_PATH = "/{device}/twin/identity/{module}/reported"
METHODS = {
    "moduleDefinition", "getEndpoints", "getClients", "getSessions",
    "readNode", "readNodes", "browseNode", "browseNodes", "writeNode", "writeNodes",
    "monitorItem", "monitorItems",
}


def _artifact(name):
    return json.loads((FIXTURES / name).read_text())


class _OpcuaDocumentHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        self.server.calls[path] += 1
        self.server.requests.append({
            "method": "GET", "path": path, "query": parsed.query,
            "headers": dict(self.headers),
            "body": self.rfile.read(int(self.headers.get("Content-Length", 0))),
        })
        if path == "/openapi.json" and self.server.document is not None:
            status, value = 200, self.server.document
        elif path == "/opcua/template/list":
            status, value = 200, self.server.template_names
        elif path == "/opcua/template":
            name = parse_qs(parsed.query, keep_blank_values=True).get("template_name", [""])[-1]
            if not name:
                status, value = 422, {"detail": "Template name is required"}
            elif name == "missing":
                status, value = 404, {"detail": "Template not found"}
            else:
                status, value = 200, self.server.template_document
        else:
            status, value = 404, {}
        status, value = self.server.template_errors.get(path, (status, value))
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def opcua_provider(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpcuaDocumentHandler)
    server.document = _artifact("openapi.json")
    server.calls = Counter()
    server.requests = []
    templates = _artifact("template-contract.json")
    server.template_names = templates["names"]
    server.template_document = templates["document"]
    server.template_errors = {}
    server.base_url = f"http://127.0.0.1:{server.server_port}"
    # Explicit host AND port allowlisting; no bypass of manifest URL validation.
    monkeypatch.setattr(extension_schemas, "EXTENSIONS_HTTP_UPSTREAM_ALLOWLIST", f"127.0.0.1:{server.server_port}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def _manifest(provider):
    manifest = _artifact("registration.json")
    # The sole adaptation of the committed contract is its ephemeral test port.
    manifest["upstreams"][HTTP_UPSTREAM]["base_url"] = provider.base_url
    return manifest


async def _register_opcua(client, owned, provider, enable=True):
    manifest = _manifest(provider)
    owned.append(NAME)
    schema = await client.get("/extensions/schema")
    assert schema.status_code == 200, schema.text
    # Validate the exact artifact as well as its test-port adaptation.
    Draft202012Validator(schema.json()).validate(_artifact("registration.json"))
    Draft202012Validator(schema.json()).validate(manifest)
    response = await client.post("/extensions", json=manifest)
    assert response.status_code == 201, response.text
    assert response.json()["enabled"] is False
    if enable:
        response = await client.post(f"/extensions/{NAME}/enable")
        assert response.status_code == 200, response.text
    return manifest


def _component_closure(document, component):
    """Independent expected closure, retaining the provider's recursive local refs."""
    schemas = document["components"]["schemas"]
    closure = {}

    def visit(node):
        if isinstance(node, list):
            for value in node:
                visit(value)
        elif isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].removeprefix("#/components/schemas/")
                if name not in closure:
                    closure[name] = schemas[name]
                    visit(schemas[name])
            for key, value in node.items():
                if key != "$ref":
                    visit(value)

    root = {"$ref": f"#/components/schemas/{component}"}
    visit(root)
    return {**root, "components": {"schemas": closure}}


async def _snapshots(repo, purposes=("configuration", "reported"), active=True):
    rows = {row["purpose"]: row for row in await repo.list_module_schemas(NAME)}
    assert set(rows) == set(purposes)
    for purpose, row in rows.items():
        assert row["module_id"] == MODULE
        assert row["upstream"] == HTTP_UPSTREAM
        assert row["schema_ref"] == f"#/components/schemas/{COMPONENTS[purpose]}"
        assert len(row["version"]) == 64 and row["resolved_at"]
        Draft202012Validator.check_schema(row["schema"])
        _assert_refs(row["schema"], row["schema"])
        current = module_schema_registry.get(MODULE, purpose)
        if active:
            assert current.extension_name == NAME and current.schema == row["schema"]
            assert current.version == row["version"]
        else:
            assert current is None
    assert module_schema_registry.get("seal-app-net-discover", "configuration") is None
    assert module_schema_registry.get("seal-module-opcua-client-other", "reported") is None
    return rows


async def _opcua_openapi(client, baseline, purposes=("configuration", "reported"), mounted=True):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    document = response.json()
    cached = copy.deepcopy(app.openapi_schema)
    assert (await client.get("/openapi.json")).json() == document
    assert app.openapi_schema == cached
    assert not any(name.startswith(("Module_", "ext_")) for name in cached["components"]["schemas"])
    _assert_refs(document, document)

    for purpose, path in (("configuration", CONFIG_PATH), ("reported", REPORTED_PATH)):
        operation = document["paths"][path]["get"]
        original = baseline["paths"][path]["get"]
        for key in ("operationId", "security", "parameters", "tags"):
            assert operation[key] == original[key]
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        if purpose in purposes:
            original_schema = original["responses"]["200"]["content"]["application/json"]["schema"]
            assert schema["anyOf"][0] == original_schema.get("anyOf", [original_schema])[0]
            assert schema["anyOf"][-1] == {"type": "null"}
            assert len(schema["anyOf"]) == 3
            root = schema["anyOf"][1]
            assert root["$ref"].startswith(f"#/components/schemas/Module_{purpose}_")
            resolved = _resolve(document, root)
            assert resolved["title"].startswith(COMPONENTS[purpose])
            nested = (resolved["properties"]["clients"]["additionalProperties"] if purpose == "configuration"
                      else resolved["properties"]["config"])
            assert nested["$ref"].startswith(f"#/components/schemas/Module_{purpose}_")
            assert _resolve(document, nested)["properties"]
        else:
            assert schema == original["responses"]["200"]["content"]["application/json"]["schema"]

    post = document["paths"][CONFIG_PATH]["post"]
    for key in ("operationId", "security", "parameters", "tags"):
        assert post[key] == baseline["paths"][CONFIG_PATH]["post"][key]
    body = post["requestBody"]
    assert body["required"] is True
    alternatives = body["content"]["application/json"]["schema"]["anyOf"]
    assert alternatives[0] == {"type": "object"}
    assert len(alternatives) == (2 if "configuration" in purposes else 1)
    fixed = document["paths"][f"/{{device}}/twin/config/{MODULE}"]
    for method in ("get", "post"):
        assert fixed[method]["deprecated"] is True
        for key in ("operationId", "security", "parameters", "tags"):
            assert fixed[method][key] == baseline["paths"][f"/{{device}}/twin/config/{MODULE}"][method][key]
    if "configuration" in purposes:
        assert fixed["post"]["requestBody"]["content"]["application/json"]["schema"] == alternatives[1]
    else:
        assert fixed == baseline["paths"][f"/{{device}}/twin/config/{MODULE}"]

    methods = _artifact("methods.json")
    paths = {path for path in document["paths"] if path.startswith("/opcua/")}
    assert paths == ({f"/opcua/{method}" for method in METHODS} if mounted else set())
    for method in METHODS if mounted else ():
        operation = document["paths"][f"/opcua/{method}"]["post"]
        assert set(document["paths"][f"/opcua/{method}"]) == {"post"}
        assert operation["operationId"] == f"opcua_post_opcua_{method}"
        assert operation["security"] == post["security"] and operation["security"]
        assert operation["parameters"] == [{"name": "device_id", "in": "query", "required": True,
                                             "schema": {"type": "string", "title": "Device Id"}}]
        if methods[method] is None:
            assert "requestBody" not in operation
        else:
            assert operation["requestBody"] == {"required": True, "content": {
                "application/json": {"schema": methods[method]}}}
    _assert_template_openapi(document, mounted=mounted)
    # HTTP response components use surrogate route IDs in this inherited core.
    # Compare lifecycle documentation semantically; raw repeated reads above must
    # still be byte-for-byte equivalent and never mutate the cached base document.
    return _stable_opcua_openapi(document)


def test_opcua_committed_artifact_hashes_and_src_app_registered_handler_contract():
    provenance = _artifact("provenance.json")
    assert provenance["providerCommit"] == "47359569edf53bdcf55c50591916c3cda35a655a"
    assert [record["providerCommit"] for record in provenance["providerHistory"]] == [
        "ad10d2d67d02b4bf0493c273487ad3e36b68d2fc",
        "b9c6490fc881ee87dd694b80132ef8895443314e",
    ]
    assert provenance["runtimeSourceCommit"] == "1761b5f237a8d177d69f8dabca9f3d70166e9c08"
    for name, record in provenance["files"].items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == record["sha256"]
    assert provenance["sourceSha256"]["edge-opcua-client/src/app.ts"] == "217bf821ca23319eb7349b4670a464cfa5ac1167f59c595e1fdd0f38c031f874"
    handlers = provenance["registeredHandlers"]
    manifest, methods = _artifact("registration.json"), _artifact("methods.json")
    assert manifest["name"] == NAME and manifest["schema_version"] == 1
    assert manifest["upstreams"]["opcua-iotedge"] == {"type": "iotedge", "module_name": MODULE}
    assert set(handlers) == set(methods) == METHODS
    assert set(manifest["module_schemas"]) == {MODULE}
    assert {action["name"] for action in manifest["actions"]} == {
        "opcua.methods.read", "opcua.methods.write", "opcua.templates.list", "opcua.templates.read",
    }
    assert len(manifest["routes"]) == 14
    for route in (route for route in manifest["routes"] if route["upstream"] == "opcua-iotedge"):
        method = route["iotedge"]["method_name"]
        assert route["iotedge"] == {"operation": "direct_method", "method_name": method}
        assert route["path"] == f"/opcua/{method}" and route["method"] == "POST"
        assert route["visibility"] == "public" and route["upstream"] == "opcua-iotedge"
        assert route["required_action"] == ("opcua.methods.write" if method in {"writeNode", "writeNodes"} else "opcua.methods.read")
        assert route["scoped"] is True and route["scope_param"] == "device_id" and route["scope_in"] == "query"
        assert route["query_params"] == [{"name": "device_id", "type": "string", "required": True}]
        assert route.get("body") == methods[method]
        assert handlers[method]["readsPayload"] == (method not in {"moduleDefinition", "getClients"})
        assert bool(handlers[method]["payloadSchemas"]) == (methods[method] is not None)
        if methods[method] is not None:
            Draft202012Validator.check_schema(methods[method])
    document = _artifact("openapi.json")
    _assert_refs(document, document)
    assert set(document["paths"]) == {"/health", *TEMPLATE_PATHS.values()}
    templates = [route for route in manifest["routes"] if route["upstream"] == HTTP_UPSTREAM]
    assert {route["path"]: route["upstream_path"] for route in templates} == TEMPLATE_PATHS
    for route in templates:
        assert route["method"] == "GET" and route["visibility"] == "public"
        assert route["response_ref"] is True and route["scoped"] is False
        assert "body" not in route and "device_id" not in json.dumps(route)
        suffix = "list" if route["path"].endswith("/list") else "read"
        assert route["required_action"] == f"opcua.templates.{suffix}"
        assert route.get("query_params", []) == ([] if suffix == "list" else [
            {"name": "template_name", "required": True, "type": "string"},
        ])
    configuration = _artifact("configuration.json")
    reported = _artifact("reported-operational.json")
    assert configuration["result"]["success"] is True and configuration["literal"]["schemaVersion"] == "1.0"
    assert reported["emittedPatch"]["config"]["configs"] is None
    assert "configs" not in reported["effectiveConfig"]
    for purpose, payload in (("configuration", configuration["literal"]),
                             ("reported", {"config": reported["effectiveConfig"]})):
        Draft202012Validator(_component_closure(document, COMPONENTS[purpose])).validate(payload)


async def test_opcua_registration_lifecycle_purpose_snapshots_nested_openapi_and_hydration(
    client, db_session, opcua_provider, lifecycle_state, hydrate,
):
    baseline = (await client.get("/openapi.json")).json()
    manifest = await _register_opcua(client, lifecycle_state, opcua_provider, enable=False)
    repo = get_repository(ExtensionRepository)(db_session)
    rows = await _snapshots(repo, active=False)
    for purpose, row in rows.items():
        assert row["schema"] == _component_closure(opcua_provider.document, COMPONENTS[purpose])
    assert rows["configuration"]["version"] != rows["reported"]["version"]
    await _opcua_openapi(client, baseline, purposes=(), mounted=False)
    assert opcua_provider.calls == Counter({"/openapi.json": 1})
    for action in ("opcua.methods.read", "opcua.methods.write", "opcua.templates.list", "opcua.templates.read"):
        assert not await repo.is_action_granted_to_any_role(action)

    assert (await client.post(f"/extensions/{NAME}/enable")).status_code == 200
    assert opcua_provider.calls == Counter({"/openapi.json": 2})
    enabled = await _opcua_openapi(client, baseline)
    await _snapshots(repo)
    methods = _artifact("methods.json")
    routes = await repo.list_routes(NAME)
    assert len(routes) == 14
    method_routes = [row for row in routes if row["upstream"] == "opcua-iotedge"]
    assert len(method_routes) == 12 and {row["method_name"] for row in method_routes} == METHODS
    for row in method_routes:
        assert UUID(row["id"])
        assert row["iotedge_operation"] == "direct_method"
        method = row["method_name"]
        assert row["path"] == f"/opcua/{method}" and row["method"] == "POST"
        assert row["upstream"] == "opcua-iotedge" and row["visibility"] == "public"
        assert row["query_params"] == [{"name": "device_id", "type": "string", "required": True,
                                        "description": None}]
        assert not row["body_ref"] and not row["response_ref"] and row["response_schema"] is None
        assert row["body"] == methods[method]
        assert row["validation_mode"] == ("declared" if methods[method] is not None else "none")
        assert row["required_action"] == ("opcua.methods.write" if method.startswith("write") else "opcua.methods.read")
        assert row["scoped"] is True and row["scope_in"] == "query" and row["scope_param"] == "device_id"

    # Replace recreates surrogate route IDs (including HTTP response namespaces),
    # but public operation IDs and logical schemas remain stable.
    manifest["description"] = "OPCUA committed contract replacement"
    response = await client.put(f"/extensions/{NAME}", json=manifest)
    assert response.status_code == 200 and response.json()["enabled"] is True
    assert {row["id"] for row in await repo.list_routes(NAME)}.isdisjoint({row["id"] for row in routes})
    assert await _opcua_openapi(client, baseline) == enabled
    await _snapshots(repo)

    extensions_setup._unmount_extension(NAME)
    await _opcua_openapi(client, baseline, purposes=(), mounted=False)
    await hydrate()  # Real startup entrypoint, with only its DB session injected.
    assert (await repo.get_extension_row(NAME))["enabled"] is True
    assert await _opcua_openapi(client, baseline) == enabled
    await _snapshots(repo)
    for operation in ("disable", "enable"):
        assert (await client.post(f"/extensions/{NAME}/{operation}")).status_code == 200
        assert await _opcua_openapi(client, baseline, purposes=() if operation == "disable" else tuple(COMPONENTS),
                                   mounted=operation == "enable") == (baseline if operation == "disable" else enabled)
        await _snapshots(repo, active=operation == "enable")
    assert (await client.delete(f"/extensions/{NAME}")).status_code == 204
    assert await repo.list_module_schemas(NAME) == [] and await repo.list_routes(NAME) == []
    assert await repo.get_module_schema_owner(MODULE) is None
    assert module_schema_registry.claim_owner(MODULE) is None
    assert await _opcua_openapi(client, baseline, purposes=(), mounted=False) == baseline
    assert set(opcua_provider.calls) == {"/openapi.json"}


async def test_opcua_reported_ref_removal_replace_and_restore_are_purpose_specific(
    client, db_session, opcua_provider, lifecycle_state,
):
    baseline = (await client.get("/openapi.json")).json()
    manifest = await _register_opcua(client, lifecycle_state, opcua_provider)
    repo = get_repository(ExtensionRepository)(db_session)
    full = await _opcua_openapi(client, baseline)
    del manifest["module_schemas"][MODULE]["reported_schema_ref"]
    assert (await client.put(f"/extensions/{NAME}", json=manifest)).status_code == 200
    await _snapshots(repo, purposes=("configuration",))
    assert module_schema_registry.get(MODULE, "reported") is None
    removed = await _opcua_openapi(client, baseline, purposes=("configuration",))
    assert not any(name.startswith("Module_reported_") for name in removed["components"]["schemas"])
    assert (await client.put(f"/extensions/{NAME}", json=_manifest(opcua_provider))).status_code == 200
    await _snapshots(repo)
    assert await _opcua_openapi(client, baseline) == full


@pytest.mark.parametrize("failure", ["missing-configuration", "missing-reported", "missing-nested", "external-ref", "invalid-schema", "unreachable"])
async def test_opcua_invalid_module_contract_register_replace_are_transactional(
    client, db_session, opcua_provider, lifecycle_state, failure,
):
    manifest = await _register_opcua(client, lifecycle_state, opcua_provider)
    repo = get_repository(ExtensionRepository)(db_session)
    before = {
        "detail": (await client.get(f"/extensions/{NAME}")).json(),
        "rows": copy.deepcopy(await repo.list_module_schemas(NAME)),
        "routes": copy.deepcopy(await repo.list_routes(NAME)),
        "runtime": module_schema_registry.list_active(),
        "openapi": (await client.get("/openapi.json")).json(),
    }
    schemas = opcua_provider.document["components"]["schemas"]
    if failure == "unreachable":
        opcua_provider.document = None
    elif failure.startswith("missing-"):
        component = {"missing-configuration": CONFIG_COMPONENT, "missing-reported": REPORTED_COMPONENT,
                     "missing-nested": "OpcuaClientClientConfig"}[failure]
        del schemas[component]
    elif failure == "external-ref":
        schemas[CONFIG_COMPONENT]["properties"]["clients"]["additionalProperties"] = {"$ref": "https://not-called.invalid/schema.json"}
    else:
        schemas[REPORTED_COMPONENT]["type"] = "not-a-schema-type"
    expected = 502 if failure == "unreachable" else 422
    replacement = copy.deepcopy(manifest)
    replacement["description"] = "must not persist"
    response = await client.put(f"/extensions/{NAME}", json=replacement)
    assert response.status_code == expected, response.text
    assert (await client.get(f"/extensions/{NAME}")).json() == before["detail"]
    assert await repo.list_module_schemas(NAME) == before["rows"]
    assert await repo.list_routes(NAME) == before["routes"]
    assert module_schema_registry.list_active() == before["runtime"]
    assert (await client.get("/openapi.json")).json() == before["openapi"]
    new_name = _unique_name("invalid_opcua")
    lifecycle_state.append(new_name)
    replacement["name"] = new_name
    response = await client.post("/extensions", json=replacement)
    assert response.status_code == expected, response.text
    assert (await client.get(f"/extensions/{new_name}")).status_code == 404
    assert await repo.list_module_schemas(new_name) == []
    assert await repo.list_routes(new_name) == []
    assert await repo.list_extension_actions(new_name) == []
    assert set(opcua_provider.calls) == {"/openapi.json"}


async def test_opcua_disabled_registration_still_owns_exact_module_id(
    client, db_session, opcua_provider, lifecycle_state,
):
    manifest = await _register_opcua(client, lifecycle_state, opcua_provider, enable=False)
    other = _unique_name("duplicate_opcua")
    lifecycle_state.append(other)
    manifest.update(name=other, actions=[], routes=[])
    response = await client.post("/extensions", json=manifest)
    assert response.status_code == 409 and MODULE in response.json()["message"]
    repo = get_repository(ExtensionRepository)(db_session)
    assert await repo.get_module_schema_owner(MODULE) == NAME
    assert await repo.list_module_schemas(other) == []
    assert (await client.get(f"/extensions/{other}")).status_code == 404
    assert module_schema_registry.claim_owner(MODULE) is None  # disabled is not active


@pytest.mark.parametrize("failed_purpose", ["configuration", "reported"])
@pytest.mark.parametrize("trigger", ["enable", "hydrate"])
async def test_opcua_refresh_retains_last_valid_failed_purpose_snapshot(
    client, db_session, opcua_provider, lifecycle_state, hydrate, failed_purpose, trigger,
):
    baseline = (await client.get("/openapi.json")).json()
    await _register_opcua(client, lifecycle_state, opcua_provider)
    repo = get_repository(ExtensionRepository)(db_session)
    before = await _snapshots(repo)
    for component in COMPONENTS.values():
        opcua_provider.document["components"]["schemas"][component]["title"] += " (refresh)"
    del opcua_provider.document["components"]["schemas"][COMPONENTS[failed_purpose]]
    if trigger == "enable":
        assert (await client.post(f"/extensions/{NAME}/disable")).status_code == 200
        assert (await client.post(f"/extensions/{NAME}/enable")).status_code == 200
    else:
        extensions_setup._unmount_extension(NAME)
        await hydrate()
    after = await _snapshots(repo)
    assert {key: value for key, value in after[failed_purpose].items() if key != "id"} == {
        key: value for key, value in before[failed_purpose].items() if key != "id"}
    successful = "reported" if failed_purpose == "configuration" else "configuration"
    assert after[successful]["version"] != before[successful]["version"]
    assert after[successful]["schema"] == _component_closure(opcua_provider.document, COMPONENTS[successful])
    await _opcua_openapi(client, baseline)


@pytest.mark.parametrize("purpose", ["configuration", "reported"])
async def test_opcua_invalid_persisted_contract_hydrates_claimed_unavailable_before_cloud_io(
    client, db_session, opcua_provider, lifecycle_state, hydrate, cloud, purpose,
):
    baseline = (await client.get("/openapi.json")).json()
    await _register_opcua(client, lifecycle_state, opcua_provider)
    repo = get_repository(ExtensionRepository)(db_session)
    rows = await repo.list_module_schemas(NAME)
    await repo.replace_module_schemas(NAME, [
        {**row, "schema": {"type": "not-a-schema-type"}} if row["purpose"] == purpose else row for row in rows
    ])
    opcua_provider.document = None
    extensions_setup._unmount_extension(NAME)
    await hydrate()
    assert module_schema_registry.claim_owner(MODULE) == NAME
    assert module_schema_registry.is_unavailable(MODULE)
    for current_purpose in COMPONENTS:
        assert module_schema_registry.get(MODULE, current_purpose) is None
    await _opcua_openapi(client, baseline, purposes=())  # persisted route docs survive module failure
    calls = opcua_provider.calls.copy()
    for path in (f"/{DEVICE}/twin/config/{MODULE}", f"/{DEVICE}/twin/identity/{MODULE}/reported"):
        response = await client.get(path)
        assert response.status_code == 503 and response.json() == UNAVAILABLE
    for raw in (json.dumps(_artifact("configuration.json")["literal"]), "not JSON", " " * (1024 * 1024 + 1)):
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", content=raw)
        assert response.status_code == 503 and response.json() == UNAVAILABLE
    assert cloud.effects == [] and cloud.direct_methods == 0 and opcua_provider.calls == calls


async def test_opcua_active_contracts_validate_configuration_and_reported_get_without_schema_fetch(
    client, db_session, opcua_provider, lifecycle_state, cloud,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    repo = get_repository(ExtensionRepository)(db_session)
    rows = await _snapshots(repo)
    configuration = _artifact("configuration.json")["literal"]
    reported = {"config": _artifact("reported-operational.json")["effectiveConfig"]}
    Draft202012Validator(rows["configuration"]["schema"]).validate(configuration)
    Draft202012Validator(rows["reported"]["schema"]).validate(reported)
    calls = opcua_provider.calls.copy()
    cloud.blobs[f"{DEVICE}/{MODULE}/current"] = json.dumps(configuration).encode()
    cloud.twins[(DEVICE, MODULE)] = {"properties": {"reported": reported}}
    for path, expected in ((f"/{DEVICE}/twin/config/{MODULE}", configuration),
                           (f"/{DEVICE}/twin/identity/{MODULE}/reported", reported)):
        response = await client.get(path)
        assert response.status_code == 200 and response.json() == expected
    cloud.blobs[f"{DEVICE}/{MODULE}/current"] = json.dumps({**configuration, "schemaVersion": "2.0"}).encode()
    cloud.twins[(DEVICE, MODULE)]["properties"]["reported"] = {"config": {"health": "not-runtime-health"}}
    for path in (f"/{DEVICE}/twin/config/{MODULE}", f"/{DEVICE}/twin/identity/{MODULE}/reported"):
        response = await client.get(path)
        assert response.status_code == 500 and response.json() == MISMATCH
    assert cloud.direct_methods == 0 and opcua_provider.calls == calls
    assert all(effect[0] not in {"upload", "patch"} for effect in cloud.effects)


async def test_opcua_management_schema_requires_authentication_without_oidc_io(client, monkeypatch):
    async def forbidden_oidc():
        raise AssertionError("missing bearer must not initiate OIDC I/O")

    monkeypatch.setattr(auth, "ensure_oidc_initialized", forbidden_oidc)
    previous = app.dependency_overrides.pop(auth.validate_jwt)
    try:
        response = await client.get("/extensions/schema")
        assert response.status_code == 401 and response.json() == {"message": "Not authenticated"}
    finally:
        app.dependency_overrides[auth.validate_jwt] = previous


def _configuration(*, literal_boundaries=False):
    payload = _artifact("configuration.json")["literal"]
    if literal_boundaries:
        # Derived from the runtime-accepted anonymous fixture. Zod's topic default
        # and unknown-key stripping are parse-output behavior, never persistence
        # instructions (provider corpus: accept-edgehub-topic-default-not-injected
        # and accept-extra-top-level-field-stripped). Nulls here are ONLY extras,
        # not the runtime's optional-but-non-nullable declared fields.
        subscription = payload["clients"]["powerpak-client"]["sessions"]["session1"]["subscriptions"]["mqttSubscription"]
        del subscription["edgeHubOutput"][0]["topic"]
        item = subscription["monitoredItems"]["MachineDate"]
        del item["samplingInterval"]
        item["literalNull"] = None
        payload["extraField"] = {"nullable": None, "items": [None, {"keep": "literal"}]}
    return payload


def _assert_configuration_write(cloud, payload, *, module=MODULE):
    assert len(cloud.effects) == 4
    container, blob, upload, patch = cloud.effects
    assert container == ("container", ("teststorage", "iotedge-device-twin"), {"sas_token": "sig=synthetic"})
    assert blob == ("blob", f"{DEVICE}/{module}/current")
    assert upload == ("upload", f"{DEVICE}/{module}/current", json.dumps(payload, separators=(",", ":")), True)
    config_id = patch[2]["properties"]["desired"]["configId"]
    assert len(config_id) == 32 and UUID(config_id).version == 4 and UUID(config_id).hex == config_id
    assert patch == ("patch", f"https://testhub.invalid/twins/{DEVICE}/modules/{module}?api-version=2020-05-31-preview",
                     {"properties": {"desired": {
                         "configId": config_id,
                         "configBlobUrl": f"https://teststorage.blob.core.windows.net/iotedge-device-twin/{DEVICE}/{module}/current?sig=synthetic",
                     }}}, {"Authorization": "synthetic"})
    assert json.loads(cloud.blobs[f"{DEVICE}/{module}/current"]) == payload


def _assert_configuration_read(cloud, *, module=MODULE, twin=False):
    expected = [
        ("container", ("teststorage", "iotedge-device-twin"), {"sas_token": "sig=synthetic"}),
        ("blob", f"{DEVICE}/{module}/current"),
        ("download", f"{DEVICE}/{module}/current"),
    ]
    if twin:
        expected.append(("twin_read", DEVICE, module, {"Authorization": "synthetic"}))
    assert cloud.effects == expected


@pytest.mark.parametrize("method,endpoint", [("GET", "get_opcua_client_twin_config"),
                                             ("POST", "post_module_twin_config_opcua")])
def test_opcua_canonical_url_really_selects_deprecated_fixed_alias(method, endpoint):
    # This concrete URL is also matched by the generic route, but routing selects
    # the fixed alias first. Do not manufacture two independently selectable URLs.
    scope = {"type": "http", "path": f"/{DEVICE}/twin/config/{MODULE}", "method": method}
    matches = [route for route in app.routes if route.matches(scope)[0] == Match.FULL]
    assert matches[0].path == f"/{{device}}/twin/config/{MODULE}"
    assert matches[0].endpoint.__name__ == endpoint and matches[0].deprecated is True
    assert any(route.path == CONFIG_PATH and not route.deprecated for route in matches[1:])


@pytest.mark.parametrize("literal_boundaries", [False, True], ids=["runtime-1.0-fixture", "literal-nulls-extras-omissions"])
async def test_opcua_canonical_post_get_real_helpers_keep_literal_json_and_exact_targets(
    client, db_session, opcua_provider, lifecycle_state, cloud, literal_boundaries,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    rows = await _snapshots(get_repository(ExtensionRepository)(db_session))
    payload = _configuration(literal_boundaries=literal_boundaries)
    Draft202012Validator(rows["configuration"]["schema"]).validate(payload)
    calls = opcua_provider.calls.copy()
    response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=payload)
    assert response.status_code == 200 and response.json() == [payload, 200]
    _assert_configuration_write(cloud, payload)
    cloud.effects.clear()
    response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")
    assert response.status_code == 200 and response.json() == payload
    _assert_configuration_read(cloud)
    assert set(cloud.blobs) == {f"{DEVICE}/{MODULE}/current"}
    assert cloud.twins == {} and cloud.direct_methods == 0 and opcua_provider.calls == calls


@pytest.mark.parametrize("actions,region,read_status,write_status", [
    (["device.read"], "EU", 200, 403),
    (["device.module_twin_config.write"], "EU", 403, 200),
    (["device.read", "device.module_twin_config.write"], "US", 403, 403),
    ([], "EU", 403, 403),
    (["device.read", "device.module_twin_config.write"], "EU", 200, 200),
])
async def test_opcua_non_admin_read_write_and_device_scope_are_independent(
    client, db_session, opcua_provider, lifecycle_state, cloud, actions, region, read_status, write_status,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    await AbacFixtures(db_session).setup(
        users={"caller": "opcua-config-caller-oid"},
        scopes={"eu": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
        roles={"config-role": actions},
        teams={"config-team": {"scope": "eu", "roles": ["config-role"], "users": ["caller"]}},
        devices={DEVICE: {"region": region}},
    )

    async def caller():
        return {"oid": "opcua-config-caller-oid", "sub": "opcua-config-caller-oid", "roles": []}

    previous = app.dependency_overrides[auth.validate_jwt]
    app.dependency_overrides[auth.validate_jwt] = caller
    try:
        payload = _configuration()
        cloud.blobs[f"{DEVICE}/{MODULE}/current"] = json.dumps(payload).encode()
        calls = opcua_provider.calls.copy()
        response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")
        assert response.status_code == read_status
        if read_status == 403:
            assert cloud.effects == []
        else:
            assert response.json() == payload
            _assert_configuration_read(cloud)
        cloud.effects.clear()
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=payload)
        assert response.status_code == write_status
        if write_status == 200:
            assert response.json() == [payload, 200]
            _assert_configuration_write(cloud, payload)
        else:
            assert cloud.effects == []
            # Auth precedes parser/validator/size checks, even for unavailable
            # contracts. Denied callers do not learn contract/body diagnostics.
            for unavailable in (False, True):
                if unavailable:
                    module_schema_registry.replace_extension_entries(NAME, [], [MODULE])
                for raw in ('{"schemaVersion":"configuration-secret"}', "not JSON", " " * (1024 * 1024 + 1)):
                    denied = await client.post(f"/{DEVICE}/twin/config/{MODULE}", content=raw)
                    assert denied.status_code == 403 and "errors" not in denied.json()
                    assert "configuration-secret" not in denied.text and cloud.effects == []
                if unavailable:
                    denied = await client.get(f"/{DEVICE}/twin/config/{MODULE}")
                    assert denied.status_code == (403 if read_status == 403 else 503)
                    if read_status == 200:
                        assert denied.json() == UNAVAILABLE
                    else:
                        assert "errors" not in denied.json()
                    assert cloud.effects == []
        assert cloud.direct_methods == 0 and opcua_provider.calls == calls
    finally:
        app.dependency_overrides[auth.validate_jwt] = previous


@pytest.mark.parametrize("raw", [b"", b"{", b"\xff", b'{"value":NaN}', b'{"value":Infinity}',
                                b'{"value":-Infinity}', b'{"value":1e400}'])
async def test_opcua_malformed_nonfinite_json_is_400_before_helpers(
    client, opcua_provider, lifecycle_state, cloud, raw,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()
    response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", content=raw)
    assert response.status_code == 400 and response.json() == {"message": "Request body must be valid finite JSON"}
    assert cloud.effects == [] and cloud.direct_methods == 0 and opcua_provider.calls == calls


@pytest.mark.parametrize("streamed", [False, True], ids=["content-length", "stream-without-length"])
async def test_opcua_over_one_mib_is_413_before_helpers(
    client, opcua_provider, lifecycle_state, cloud, streamed,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()
    raw = json.dumps({**_configuration(), "extraField": "x" * (1024 * 1024)}).encode()
    assert len(raw) > 1024 * 1024

    async def chunks():
        yield raw[:1024 * 1024]
        yield raw[1024 * 1024:]

    response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", content=chunks() if streamed else raw)
    assert response.status_code == 413 and response.json() == {"message": "Request body exceeds 1048576 bytes"}
    assert cloud.effects == [] and cloud.direct_methods == 0 and opcua_provider.calls == calls


_CLIENT = ("clients", "powerpak-client")
_SESSION = (*_CLIENT, "sessions", "session1")
_SUBSCRIPTION = (*_SESSION, "subscriptions", "mqttSubscription")
_ITEM = (*_SUBSCRIPTION, "monitoredItems", "MachineDate")


@pytest.mark.parametrize("path,value,rule", [
    (("schemaVersion",), "2.0", "const"),
    (("mqttEndpoint",), None, "type"),
    ((*_CLIENT, "clientCertificate"), None, "type"),
    ((*_CLIENT, "endpoint"), "https://configuration-secret", "pattern"),
    ((*_CLIENT, "securityPolicy"), "configuration-secret", "enum"),
    ((*_SUBSCRIPTION, "priority"), 256, "maximum"),
    ((*_ITEM, "queueSize"), "5", "type"),
    ((*_ITEM, "scalingFactor"), None, "type"),
    ((*_SUBSCRIPTION, "publishingEnabled"), None, "type"),
    ((*_SUBSCRIPTION, "edgeHubOutput", 0, "messageFormat"), "configuration-secret", "enum"),
    ((*_SUBSCRIPTION, "mqttOutput", 0, "applicationData"), None, "type"),
])
async def test_opcua_runtime_structural_schema_rejects_without_coercion_or_value_leaks(
    client, opcua_provider, lifecycle_state, cloud, path, value, rule,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()
    payload = _configuration()
    node = payload
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    payload["extraField"] = "configuration-secret"
    response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body["message"] == "Module configuration does not match its schema"
    assert 1 <= len(body["errors"]) <= 20
    assert any(error["loc"] == list(path) and error["rule"] == rule for error in body["errors"])
    assert all(set(error) == {"loc", "rule", "msg"} for error in body["errors"])
    for hidden in ("configuration-secret", CONFIG_COMPONENT, "components/schemas", '"input"', '"ctx"'):
        assert hidden not in response.text
    assert cloud.effects == [] and cloud.direct_methods == 0 and opcua_provider.calls == calls


@pytest.mark.parametrize("payload", [{}, {"schemaVersion": "1.0"}, {"schemaVersion": "1.0", "clients": []}])
async def test_opcua_required_properties_and_root_shapes_are_safe_422(
    client, opcua_provider, lifecycle_state, cloud, payload,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()
    response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=payload)
    assert response.status_code == 422
    assert response.json()["message"] == "Module configuration does not match its schema"
    assert {error["rule"] for error in response.json()["errors"]} <= {"required", "type"}
    assert cloud.effects == [] and cloud.direct_methods == 0 and opcua_provider.calls == calls


async def test_opcua_unusable_active_write_validator_is_safe_503_before_helpers(
    client, opcua_provider, lifecycle_state, cloud, monkeypatch,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()

    def unusable(*args):
        raise RuntimeError("validator-configuration-secret")

    monkeypatch.setattr(body_validation, "validate_module_snapshot", unusable)
    response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=_configuration())
    assert response.status_code == 503 and response.json() == UNAVAILABLE
    assert cloud.effects == [] and cloud.direct_methods == 0 and opcua_provider.calls == calls


@pytest.mark.parametrize("stored", [
    {"schemaVersion": "2.0", "clients": {}},
    {"schemaVersion": "1.0", "clients": {}, "mqttEndpoint": None},
    {"schemaVersion": "configuration-secret", "clients": {}},
    ["configuration-secret"],
])
async def test_opcua_invalid_active_stored_data_is_safe_500_without_writes(
    client, opcua_provider, lifecycle_state, cloud, stored,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()
    cloud.blobs[f"{DEVICE}/{MODULE}/current"] = json.dumps(stored).encode()
    response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")
    assert response.status_code == 500 and response.json() == MISMATCH
    _assert_configuration_read(cloud)
    assert cloud.direct_methods == 0 and opcua_provider.calls == calls


async def test_opcua_unusable_active_read_validator_is_safe_500_after_blob_read(
    client, opcua_provider, lifecycle_state, cloud, monkeypatch,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()
    cloud.blobs[f"{DEVICE}/{MODULE}/current"] = json.dumps(_configuration()).encode()

    def unusable(*args):
        raise RuntimeError("validator-configuration-secret")

    monkeypatch.setattr(body_validation, "validate_module_snapshot", unusable)
    response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")
    assert response.status_code == 500 and response.json() == MISMATCH
    _assert_configuration_read(cloud)
    assert cloud.direct_methods == 0 and opcua_provider.calls == calls


@pytest.mark.parametrize("state", ["stored-null", "no-blob-module-exists", "no-blob-no-module", "invalid-json"])
async def test_opcua_active_get_null_and_existing_helper_error_contracts(
    client, opcua_provider, lifecycle_state, cloud, state,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()
    if state == "stored-null":
        cloud.blobs[f"{DEVICE}/{MODULE}/current"] = b"null"
    elif state == "no-blob-module-exists":
        cloud.twins[(DEVICE, MODULE)] = {"properties": {"desired": {"notConfig": "not-returned"}}}
    elif state == "invalid-json":
        cloud.blobs[f"{DEVICE}/{MODULE}/current"] = b"{"
    response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")
    if state in {"stored-null", "no-blob-module-exists"}:
        assert response.status_code == 200 and response.json() is None
    elif state == "no-blob-no-module":
        assert response.status_code == 404
        assert response.json() == {"message": f"{{'error': 'IoTBackendAPIError', 'message': 'Module {MODULE} not found on device {DEVICE}'}}"}
    else:
        assert response.status_code == 500
        assert response.json() == {"message": "{'error': 'IoTBackendAPIError', 'message': 'Could not retrieve module twin config: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'}"}
    _assert_configuration_read(cloud, twin=state.startswith("no-blob"))
    assert cloud.direct_methods == 0 and opcua_provider.calls == calls


@pytest.mark.parametrize("failure", ["storage", "sas", "upload", "hub"])
async def test_opcua_registered_write_keeps_helper_error_wrappers_and_partial_effects(
    client, opcua_provider, lifecycle_state, cloud, post_writes, monkeypatch, failure,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()
    payload = _configuration()
    if failure == "storage":
        monkeypatch.setattr(write_route, "PUBLIC_STORAGE_ACCOUNT_NAME", "")
    elif failure == "sas":
        monkeypatch.setattr(write_route, "BLOB_SAS_TOKEN_MODULE_CONF", "")
    elif failure == "upload":
        post_writes.upload_error = RuntimeError("synthetic upload error")
    else:
        post_writes.status = 409
    response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=payload)
    assert response.status_code == {"storage": 500, "sas": 500, "upload": 400, "hub": 409}[failure]
    if failure in {"storage", "sas"}:
        message = ("Public storage account name is not set. Cannot upload file." if failure == "storage" else
                   "BLOB_SAS_TOKEN_MODULE_CONF is not configured. SAS token is required for Edge Device blob access.")
        assert response.json() == {"message": str({"error": "IoTBackendAPIError", "message": message})}
        assert post_writes.contexts == post_writes.blobs == post_writes.uploads == post_writes.patches == []
    elif failure == "upload":
        assert response.json() == {"message": "{'error': 'UploadError', 'message': 'could not upload data object to blob storage: synthetic upload error'}"}
        assert post_writes.uploads == post_writes.patches == []
    else:
        # Existing failure is after upload, not an atomic rollback or a second
        # write through the provider. The failed patch still carries a UUID.
        assert post_writes.uploads == [(json.dumps(payload, separators=(",", ":")), True)]
        assert len(post_writes.patches) == 1
        url, patch, headers = post_writes.patches[0]
        config_id = patch["properties"]["desired"]["configId"]
        assert UUID(config_id).version == 4 and len(config_id) == 32
        assert url == f"https://testhub.invalid/twins/{DEVICE}/modules/{MODULE}?api-version=2020-05-31-preview"
        assert headers == {"Authorization": "synthetic"}
        assert response.json() == {"message": str({"error": "IoTBackendAPIError", "message":
            f"could not update module twin of {MODULE} on {DEVICE} on the Azure IoT Hub. Config-Id: [{config_id}]. Error: synthetic hub error"})}
    if failure in {"upload", "hub"}:
        assert post_writes.contexts == [(('teststorage', 'iotedge-device-twin'), {"sas_token": "sig=synthetic"})]
        assert post_writes.blobs == [f"{DEVICE}/{MODULE}/current"]
    assert cloud.effects == [] and cloud.direct_methods == 0 and opcua_provider.calls == calls


@pytest.mark.parametrize("module", [MODULE, "seal-module-opcua-client-other", "seal-app-net-discover"])
async def test_opcua_no_schema_and_sibling_modules_keep_literal_object_fallback(
    client, opcua_provider, lifecycle_state, cloud, module,
):
    if module != MODULE:
        await _register_opcua(client, lifecycle_state, opcua_provider)
    assert module_schema_registry.get(module, "configuration") is None
    calls = opcua_provider.calls.copy()
    payload = {"schemaVersion": "not-runtime-version", "nullable": None, "items": [None]}
    response = await client.post(f"/{DEVICE}/twin/config/{module}", json=payload)
    assert response.status_code == 200 and response.json() == [payload, 200]
    _assert_configuration_write(cloud, payload, module=module)
    cloud.effects.clear()
    response = await client.get(f"/{DEVICE}/twin/config/{module}")
    assert response.status_code == 200 and response.json() == payload
    _assert_configuration_read(cloud, module=module)
    cloud.effects.clear()
    response = await client.post(f"/{DEVICE}/twin/config/{module}", content="null")
    assert response.status_code == 422 and response.json() == {
        "message": "Module configuration does not match its schema",
        "errors": [{"loc": [], "rule": "type", "msg": "must match the declared type"}],
    }
    assert cloud.effects == [] and cloud.direct_methods == 0 and opcua_provider.calls == calls


async def test_opcua_disabled_removed_module_binding_and_deregistered_use_object_fallback(
    client, opcua_provider, lifecycle_state, cloud,
):
    manifest = await _register_opcua(client, lifecycle_state, opcua_provider)
    fallback = {"schemaVersion": "not-runtime-version", "keepNull": None}
    for state in ("disabled", "module-binding-removed", "deregistered"):
        if state == "disabled":
            assert (await client.post(f"/extensions/{NAME}/disable")).status_code == 200
        elif state == "module-binding-removed":
            # schema_ref is required for a module binding; removing the entire
            # binding is the real configuration-contract removal operation.
            del manifest["module_schemas"][MODULE]
            assert (await client.put(f"/extensions/{NAME}", json=manifest)).status_code == 200
            assert module_schema_registry.get(MODULE, "reported") is None
        else:
            assert (await client.delete(f"/extensions/{NAME}")).status_code == 204
        assert module_schema_registry.get(MODULE, "configuration") is None
        calls = opcua_provider.calls.copy()
        cloud.effects.clear()
        response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=fallback)
        assert response.status_code == 200 and response.json() == [fallback, 200]
        _assert_configuration_write(cloud, fallback)
        cloud.effects.clear()
        response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")
        assert response.status_code == 200 and response.json() == fallback
        _assert_configuration_read(cloud)
        assert cloud.direct_methods == 0 and opcua_provider.calls == calls
        if state != "deregistered":
            if state == "disabled":
                assert (await client.post(f"/extensions/{NAME}/enable")).status_code == 200
            else:
                assert (await client.put(f"/extensions/{NAME}", json=_manifest(opcua_provider))).status_code == 200
            assert module_schema_registry.get(MODULE, "configuration") is not None
            calls = opcua_provider.calls.copy()
            cloud.effects.clear()
            assert (await client.get(f"/{DEVICE}/twin/config/{MODULE}")).status_code == 500
            _assert_configuration_read(cloud)
            cloud.effects.clear()
            assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=fallback)).status_code == 422
            assert cloud.effects == [] and opcua_provider.calls == calls


async def test_opcua_nested_session_credentials_are_required_before_helpers(
    client, opcua_provider, lifecycle_state, cloud,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()
    payload = _configuration()
    del payload["clients"]["powerpak-client"]["sessions"]["session1"]["credentials"]
    response = await client.post(f"/{DEVICE}/twin/config/{MODULE}", json=payload)
    assert response.status_code == 422 and response.json() == {
        "message": "Module configuration does not match its schema",
        "errors": [{"loc": list(_SESSION), "rule": "required", "msg": "must include the required properties"}],
    }
    assert cloud.effects == [] and cloud.direct_methods == 0 and opcua_provider.calls == calls


async def test_opcua_active_blob_io_failure_keeps_existing_500_wrapper_and_no_twin_fallback(
    client, opcua_provider, lifecycle_state, cloud, blob_store, iot_hub,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()
    blob_store.blobs[f"{DEVICE}/{MODULE}/current"] = RuntimeError("synthetic blob unavailable")
    response = await client.get(f"/{DEVICE}/twin/config/{MODULE}")
    assert response.status_code == 500 and response.json() == {
        "message": "{'error': 'IoTBackendAPIError', 'message': 'Could not retrieve module twin config: synthetic blob unavailable'}",
    }
    assert blob_store.requested == [f"{DEVICE}/{MODULE}/current"]
    assert iot_hub.requested == [] and cloud.effects == []
    assert cloud.direct_methods == 0 and opcua_provider.calls == calls


def _stable_opcua_openapi(document):
    """Normalize ONLY inherited per-route HTTP response namespaces for lifecycle comparison."""
    encoded = json.dumps(document)
    for path in TEMPLATE_PATHS:
        operation = document["paths"].get(path, {}).get("get")
        if operation is None:
            continue
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        if "$ref" in schema:
            prefix = re.match(r"#/components/schemas/(ext_[0-9a-f]{32}_)", schema["$ref"])
            assert prefix, schema
            encoded = encoded.replace(prefix[1], f"ext_{operation['operationId']}_")
    return json.loads(encoded)


def _assert_template_openapi(document, *, mounted=True):
    provider = _artifact("openapi.json")
    assert {path for path in document["paths"] if path.startswith("/template/opc-ua")} == (
        set(TEMPLATE_PATHS) if mounted else set()
    )
    refs = set()
    for path, private in TEMPLATE_PATHS.items() if mounted else ():
        assert set(document["paths"][path]) == {"get"}
        operation = document["paths"][path]["get"]
        assert operation["operationId"] == "opcua_get_" + path.strip("/").replace("/", "_").replace("-", "_")
        assert operation["security"] and "requestBody" not in operation
        parameters = operation.get("parameters", [])
        assert not any(parameter["name"] == "device_id" for parameter in parameters)
        if path.endswith("/list"):
            assert parameters == []
        else:
            assert parameters == [{"name": "template_name", "in": "query", "required": True,
                                   "schema": {"type": "string", "title": "Template Name"}}]
        root = operation["responses"]["200"]["content"]["application/json"]["schema"]
        component = root["$ref"].removeprefix("#/components/schemas/")
        prefix = re.match(r"ext_[0-9a-f]{32}_", component)[0]
        closure = _component_closure(document, component)
        assert json.loads(json.dumps(closure).replace(prefix, "")) == _component_closure(
            provider, "OpcuaTemplateNames" if path.endswith("/list") else "OpcuaTemplateDocument",
        )
        components = set(closure["components"]["schemas"])
        assert refs.isdisjoint(components)
        refs.update(components)
        assert "502" not in operation["responses"]  # Core response_ref copies 200 only, not provider error docs.
    _assert_refs(document, document)


@pytest.fixture
async def template_http_responses(monkeypatch, _fresh_http_upstream_client):
    """Observe real httpx streaming responses/closure without substituting transport or dispatch."""
    responses = []
    send = http_upstream._client.send

    async def observed_send(request, **kwargs):
        response = await send(request, **kwargs)
        if request.url.path in TEMPLATE_PATHS.values():
            record = {"response": response, "close_calls": 0}
            close = response.aclose

            async def observed_close():
                record["close_calls"] += 1
                await close()

            monkeypatch.setattr(response, "aclose", observed_close)
            responses.append(record)
        return response

    monkeypatch.setattr(http_upstream._client, "send", observed_send)
    yield responses
    assert all(record["response"].is_closed and record["close_calls"] >= 1 for record in responses)


def _assert_templates_have_no_module_effects(cloud):
    assert cloud.effects == [] and cloud.direct_methods == 0
    assert cloud.blobs == {} and cloud.twins == {}


@pytest.mark.parametrize("actions,list_status,read_status", [
    (["opcua.templates.list"], 200, 403),
    (["opcua.templates.read"], 403, 200),
    (["opcua.templates.list", "opcua.templates.read"], 200, 200),
    (["device.read", "device.module_twin_config.write", "opcua.methods.read"], 403, 403),
    ([], 403, 403),
])
async def test_opcua_templates_non_admin_independent_global_actions_without_device_id(
    client, db_session, opcua_provider, lifecycle_state, cloud, template_http_responses,
    actions, list_status, read_status,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    repo = get_repository(ExtensionRepository)(db_session)
    for action in ("opcua.templates.list", "opcua.templates.read"):
        assert not await repo.is_action_granted_to_any_role(action)
    # Test-only role/team rows roll back with the disposable DB; no production grants.
    await AbacFixtures(db_session).setup(
        users={"caller": "opcua-template-caller-oid"},
        scopes={"restricted": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
        roles={"template-role": actions},
        teams={"template-team": {"scope": "restricted", "roles": ["template-role"], "users": ["caller"]}},
    )
    # No device is seeded. Even a team with a device scope must be global here.
    for path in TEMPLATE_PATHS:
        route = next(route for route in app.routes if route.path == path and getattr(route, "extension_name", None) == NAME)
        permission, = [dependency.call for dependency in route.dependant.dependencies
                       if isinstance(dependency.call, ABACPermissionCheck)]
        assert permission.device_path is None
        assert permission.permission == "opcua.templates." + ("list" if path.endswith("/list") else "read")

    async def caller():
        return {"oid": "opcua-template-caller-oid", "sub": "opcua-template-caller-oid", "roles": []}

    previous = app.dependency_overrides[auth.validate_jwt]
    app.dependency_overrides[auth.validate_jwt] = caller
    try:
        calls = opcua_provider.calls.copy()
        for path, status, expected in [
            ("/template/opc-ua/list", list_status, opcua_provider.template_names),
            ("/template/opc-ua?template_name=fixture", read_status, opcua_provider.template_document),
        ]:
            response = await client.get(path)
            assert response.status_code == status
            if status == 200:
                assert response.json() == expected
            else:
                assert "errors" not in response.json()
        assert opcua_provider.calls - calls == Counter({
            private: 1 for private, status in [("/opcua/template/list", list_status), ("/opcua/template", read_status)]
            if status == 200
        })
        calls = opcua_provider.calls.copy()
        denied_or_missing = await client.get("/template/opc-ua")
        assert denied_or_missing.status_code == (400 if read_status == 200 else 403)
        assert opcua_provider.calls == calls
        # Independently authorized template callers are not management admins.
        assert (await client.get("/extensions/schema")).status_code == 403
        _assert_templates_have_no_module_effects(cloud)
    finally:
        app.dependency_overrides[auth.validate_jwt] = previous


@pytest.mark.parametrize("path", ["/template/opc-ua/list", "/template/opc-ua?template_name=fixture"])
async def test_opcua_templates_missing_bearer_is_401_before_provider_or_oidc(
    client, opcua_provider, lifecycle_state, cloud, monkeypatch, path,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)

    async def forbidden_oidc():
        pytest.fail("Missing template bearer must not initiate OIDC I/O")

    monkeypatch.setattr(auth, "ensure_oidc_initialized", forbidden_oidc)
    previous = app.dependency_overrides.pop(auth.validate_jwt)
    calls = opcua_provider.calls.copy()
    try:
        response = await client.get(path)
        assert response.status_code == 401 and response.json() == {"message": "Not authenticated"}
        assert opcua_provider.calls == calls
        _assert_templates_have_no_module_effects(cloud)
    finally:
        app.dependency_overrides[auth.validate_jwt] = previous


@pytest.mark.parametrize("name", _artifact("template-contract.json")["names"])
async def test_opcua_templates_encoded_names_queries_and_logical_json_are_relayed_unchanged(
    client, opcua_provider, lifecycle_state, cloud, template_http_responses, name,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()
    query = f"template_name={quote(name, safe='')}&tag=first&tag=second&account=ignored&container=ignored"
    response = await client.get("/template/opc-ua?" + query)
    assert response.status_code == 200 and response.json() == _artifact("template-contract.json")["document"]
    assert response.content == json.dumps(opcua_provider.template_document, ensure_ascii=False).encode()
    assert response.headers["content-type"] == "application/json"
    request = opcua_provider.requests[-1]
    assert request["method"] == "GET" and request["path"] == "/opcua/template"
    assert parse_qs(request["query"], keep_blank_values=True) == {
        "template_name": [name], "tag": ["first", "second"], "account": ["ignored"], "container": ["ignored"],
    }
    assert request["body"] == b"" and "Authorization" not in request["headers"]
    assert opcua_provider.calls - calls == Counter({"/opcua/template": 1})
    assert len(template_http_responses) == 1 and template_http_responses[0]["response"].is_closed
    _assert_templates_have_no_module_effects(cloud)


@pytest.mark.parametrize("document", [None, [1, "Ü", {"placeholder": "{{x}}"}], "literal", 7, 2.5, False, {}])
async def test_opcua_template_content_is_logical_json_not_module_config_or_generated_envelope(
    client, opcua_provider, lifecycle_state, cloud, template_http_responses, document,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    opcua_provider.template_document = document
    response = await client.get("/template/opc-ua", params={"template_name": "fixture"})
    assert response.status_code == 200 and response.json() == document
    assert opcua_provider.requests[-1]["body"] == b""
    _assert_templates_have_no_module_effects(cloud)


async def test_opcua_template_list_raw_names_empty_versus_failing_source_and_query_errors(
    client, opcua_provider, lifecycle_state, cloud, template_http_responses,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    response = await client.get("/template/opc-ua/list")
    assert response.status_code == 200 and response.json() == _artifact("template-contract.json")["names"]
    opcua_provider.template_names = []
    response = await client.get("/template/opc-ua/list")
    assert response.status_code == 200 and response.json() == []
    opcua_provider.template_errors["/opcua/template/list"] = (503, {"detail": "Template source unavailable"})
    response = await client.get("/template/opc-ua/list")
    assert response.status_code == 503 and response.json() == {"detail": "Template source unavailable"}
    calls = opcua_provider.calls.copy()
    response = await client.get("/template/opc-ua")
    assert response.status_code == 400 and opcua_provider.calls == calls
    # Core requires presence only; the provider rejects an explicitly empty value.
    response = await client.get("/template/opc-ua?template_name=")
    assert response.status_code == 422
    assert parse_qs(opcua_provider.requests[-1]["query"], keep_blank_values=True) == {"template_name": [""]}
    response = await client.get("/template/opc-ua?template_name=missing")
    assert response.status_code == 404 and response.json() == {"detail": "Template not found"}
    assert len(template_http_responses) == 5
    _assert_templates_have_no_module_effects(cloud)


@pytest.mark.parametrize("path,private", TEMPLATE_PATHS.items())
@pytest.mark.parametrize("status,detail", [(503, "Template source unavailable"), (504, "Template source timed out"),
                                          (502, "Invalid template source JSON")])
async def test_opcua_template_provider_error_status_and_json_forwarding_close_real_responses(
    client, opcua_provider, lifecycle_state, cloud, template_http_responses, path, private, status, detail,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    opcua_provider.template_errors[private] = (status, {"detail": detail})
    response = await client.get(path, params={"template_name": "fixture"} if path == "/template/opc-ua" else None)
    assert response.status_code == status and response.json() == {"detail": detail}
    assert len(template_http_responses) == 1 and template_http_responses[0]["response"].is_closed
    _assert_templates_have_no_module_effects(cloud)


@pytest.mark.parametrize("path", TEMPLATE_PATHS)
async def test_opcua_template_real_provider_connection_failure_is_safe_502_not_empty(
    client, opcua_provider, lifecycle_state, cloud, template_http_responses, path,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()
    # Close the actual listener, not a mocked HTTP transport or exception.
    opcua_provider.shutdown()
    opcua_provider.server_close()
    response = await client.get(path, params={"template_name": "fixture"} if path == "/template/opc-ua" else None)
    assert response.status_code == 502 and response.json() == {"detail": "Upstream unavailable"}
    assert opcua_provider.calls == calls and template_http_responses == []
    _assert_templates_have_no_module_effects(cloud)


async def test_opcua_template_manifest_response_snapshots_and_executing_lifecycle_hydration(
    client, db_session, opcua_provider, lifecycle_state, cloud, hydrate, template_http_responses,
):
    baseline = (await client.get("/openapi.json")).json()
    manifest = await _register_opcua(client, lifecycle_state, opcua_provider, enable=False)
    repo = get_repository(ExtensionRepository)(db_session)
    rows = [row for row in await repo.list_routes(NAME) if row["upstream"] == HTTP_UPSTREAM]
    assert len(rows) == 2
    for row in rows:
        component = "OpcuaTemplateNames" if row["path"].endswith("/list") else "OpcuaTemplateDocument"
        assert row["response_schema"] == _component_closure(opcua_provider.document, component)
        assert row["response_ref"] and not row["body_ref"] and row["body"] is None
        assert row["validation_mode"] == "none" and row["scoped"] is False
        assert row["method_name"] is None and row["iotedge_operation"] is None
    for path in TEMPLATE_PATHS:
        assert (await client.get(path)).status_code == 404
    assert (await client.post(f"/extensions/{NAME}/enable")).status_code == 200
    enabled = (await client.get("/openapi.json")).json()
    _assert_template_openapi(enabled)

    async def check_reads():
        calls = opcua_provider.calls.copy()
        for path in TEMPLATE_PATHS:
            response = await client.get(path, params={"template_name": "fixture"} if path == "/template/opc-ua" else None)
            assert response.status_code == 200
        assert opcua_provider.calls - calls == Counter({private: 1 for private in TEMPLATE_PATHS.values()})
        _assert_templates_have_no_module_effects(cloud)

    await check_reads()
    manifest["description"] = "Template lifecycle self-replacement"
    old_ids = {row["id"] for row in rows}
    assert (await client.put(f"/extensions/{NAME}", json=manifest)).status_code == 200
    assert old_ids.isdisjoint({row["id"] for row in await repo.list_routes(NAME)})
    replaced = (await client.get("/openapi.json")).json()
    _assert_template_openapi(replaced)
    assert _stable_opcua_openapi(replaced) == _stable_opcua_openapi(enabled)
    old_response_names = {name for name in enabled["components"]["schemas"] if name.startswith("ext_")}
    assert old_response_names and old_response_names.isdisjoint(replaced["components"]["schemas"])
    await check_reads()
    extensions_setup._unmount_extension(NAME)
    await hydrate()  # ASGITransport skips lifespan; explicitly invoke the real DB hydration entrypoint.
    assert (await repo.get_extension_row(NAME))["enabled"] is True
    _assert_template_openapi((await client.get("/openapi.json")).json())
    await check_reads()
    for operation in ("disable", "enable"):
        assert (await client.post(f"/extensions/{NAME}/{operation}")).status_code == 200
        document = (await client.get("/openapi.json")).json()
        _assert_template_openapi(document, mounted=operation == "enable")
        if operation == "enable":
            await check_reads()
        else:
            assert document == baseline
            for path in TEMPLATE_PATHS:
                assert (await client.get(path)).status_code == 404
    # Remove only template bindings: method/module contracts stay active, response namespaces disappear.
    manifest["routes"] = [route for route in manifest["routes"] if route["path"] not in TEMPLATE_PATHS]
    assert (await client.put(f"/extensions/{NAME}", json=manifest)).status_code == 200
    document = (await client.get("/openapi.json")).json()
    _assert_template_openapi(document, mounted=False)
    assert not any(name.startswith("ext_") for name in document["components"]["schemas"])
    await _snapshots(repo)
    assert {row["method_name"] for row in await repo.list_routes(NAME)} == METHODS
    for path in TEMPLATE_PATHS:
        assert (await client.get(path)).status_code == 404
    assert (await client.put(f"/extensions/{NAME}", json=_manifest(opcua_provider))).status_code == 200
    await check_reads()
    assert (await client.delete(f"/extensions/{NAME}")).status_code == 204
    assert (await client.get("/openapi.json")).json() == baseline
    assert await repo.list_routes(NAME) == [] and await repo.list_module_schemas(NAME) == []
    assert set(opcua_provider.calls) == {"/openapi.json", *TEMPLATE_PATHS.values()}
    _assert_templates_have_no_module_effects(cloud)


@pytest.mark.parametrize("path,private", TEMPLATE_PATHS.items())
async def test_opcua_template_exact_public_route_ownership_enable_conflict_and_release(
    client, db_session, opcua_provider, lifecycle_state, cloud, path, private,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    other = _unique_name("template_claimant")
    lifecycle_state.append(other)
    claimant = {"schema_version": 1, "name": other, "upstreams": {"svc": {"type": "http", "base_url": opcua_provider.base_url}},
                "routes": [{"upstream": "svc", "path": path, "upstream_path": private, "method": "GET"}]}
    assert (await client.post("/extensions", json=claimant)).status_code == 201
    before = (await client.get(f"/extensions/{other}")).json()
    openapi = (await client.get("/openapi.json")).json()
    repo = get_repository(ExtensionRepository)(db_session)
    rows = copy.deepcopy(await repo.list_routes(NAME))
    response = await client.post(f"/extensions/{other}/enable")
    assert response.status_code == 409
    assert (await client.get(f"/extensions/{other}")).json() == before
    # list_routes has no ORDER BY, so compare rows by id rather than by list order.
    assert {row["id"]: row for row in await repo.list_routes(NAME)} == {row["id"]: row for row in rows}
    assert (await client.get("/openapi.json")).json() == openapi
    assert not any(getattr(route, "extension_name", None) == other for route in app.routes)
    assert (await client.post(f"/extensions/{NAME}/disable")).status_code == 200
    assert (await client.post(f"/extensions/{other}/enable")).status_code == 200
    assert (await client.get(path, params={"template_name": "fixture"} if path == "/template/opc-ua" else None)).status_code == 200
    assert (await client.post(f"/extensions/{NAME}/enable")).status_code == 409
    assert (await client.delete(f"/extensions/{other}")).status_code == 204
    assert (await client.post(f"/extensions/{NAME}/enable")).status_code == 200
    _assert_template_openapi((await client.get("/openapi.json")).json())
    _assert_templates_have_no_module_effects(cloud)


@pytest.mark.parametrize("path", TEMPLATE_PATHS)
async def test_opcua_templates_expose_no_write_delete_or_configuration_facade(
    client, opcua_provider, lifecycle_state, cloud, path,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    calls = opcua_provider.calls.copy()
    for method in ("POST", "PUT", "DELETE"):
        response = await client.request(method, path, params={"template_name": "fixture"}, json={"config": "not-written"})
        assert response.status_code == 405
    assert (await client.get("/opcua/config/test-device")).status_code == 404
    assert opcua_provider.calls == calls
    _assert_templates_have_no_module_effects(cloud)


@pytest.mark.parametrize("actions, region, expected", [
    (["opcua.methods.read"], "EU", METHODS - {"writeNode", "writeNodes"}),
    (["opcua.methods.write"], "EU", {"writeNode", "writeNodes"}),
    (["opcua.methods.read", "opcua.methods.write"], "EU", METHODS),
    ([], "EU", set()),
    (["opcua.methods.read", "opcua.methods.write"], "US", set()),
])
async def test_opcua_discovery_actual_handler_subset_full_twelve_and_dto_parity(
    client, db_session, fake_jwt_user, opcua_provider, lifecycle_state, cloud,
    forbid_discovery_io, actions, region, expected,
):
    manifest = await _register_opcua(client, lifecycle_state, opcua_provider)
    await AbacFixtures(db_session).setup(
        users={"caller": "opcua-discovery-caller"},
        scopes={"eu": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
        roles={"method-role": actions},
        teams={"method-team": {"scope": "eu", "roles": ["method-role"], "users": ["caller"]}},
        devices={DEVICE: {"region": region}},
    )
    fake_jwt_user.clear()
    fake_jwt_user.update({"oid": "opcua-discovery-caller", "name": "OPCUA discovery", "roles": []})
    assert (await client.get(f"/extensions/{NAME}")).status_code == 403
    before_calls = opcua_provider.calls.copy()
    before_requests = copy.deepcopy(opcua_provider.requests)
    with forbid_discovery_io():
        response = await client.get(f"/extensions/{NAME}/direct-methods", params={"device_id": DEVICE})
        assert response.status_code == 200, response.text
        result = response.json()
        assert (await client.get(f"/extensions/{NAME}/direct-methods", params={"device_id": DEVICE})).json() == result
    assert result["extension"] == NAME and result["device_id"] == DEVICE and result["enabled"] is True
    assert {m["method_name"] for m in result["methods"]} == expected
    assert [m["method_name"] for m in result["methods"]] == sorted(expected)
    handlers = _artifact("provenance.json")["registeredHandlers"]
    methods = _artifact("methods.json")
    routes = {r["iotedge"]["method_name"]: r for r in manifest["routes"] if "iotedge" in r}
    for method in result["methods"]:
        name = method["method_name"]
        assert name in handlers and method["module_id"] == MODULE
        assert len(method["invocations"]) == 1
        invocation = method["invocations"][0]
        binding = routes[name]
        assert invocation["path"] == binding["path"] == f"/opcua/{name}"
        assert invocation["http_method"] == binding["method"] == "POST"
        assert invocation["summary"] == binding.get("summary", f"{NAME}: POST /opcua/{name}")
        assert invocation["scoped"] is True
        assert (invocation["scope_param"], invocation["scope_in"]) == ("device_id", "query")
        assert invocation["query_params"] == [{**binding["query_params"][0], "description": binding["query_params"][0].get("description")}]
        assert invocation["request_schema"] == binding.get("body") == methods[name]
        if handlers[name]["readsPayload"]:
            Draft202012Validator.check_schema(invocation["request_schema"])
            _assert_refs(invocation["request_schema"], invocation["request_schema"])
        else:
            assert invocation["request_schema"] is None
    assert opcua_provider.calls == before_calls and opcua_provider.requests == before_requests
    assert cloud.effects == [] and cloud.direct_methods == 0
    assert cloud.blobs == {} and cloud.twins == {}


# Recorded IoT Hub metadata is opaque: a stale report is not a current health probe.
REPORTED_TIMESTAMP = "2026-10-14T10:00:00.1234567Z"
REPORTED_OFFSET_TIMESTAMP = "2026-10-14T12:00:00.7654321+02:00"
CAPTURE_NAMES = ["operational", "warning", "failed", "no-conditions"]


def _effective_reported(previous, patch):
    """Test-only IoT Hub oracle: null deletes, objects merge, arrays/scalars replace."""
    effective = copy.deepcopy(previous)
    for key, value in patch.items():
        if value is None:
            effective.pop(key, None)
        elif isinstance(value, dict):
            prior = effective.get(key)
            effective[key] = _effective_reported(prior if isinstance(prior, dict) else {}, value)
        else:
            effective[key] = copy.deepcopy(value)
    return effective


def _set_opcua_reported(cloud, reported, *, module=MODULE):
    cloud.twins[(DEVICE, module)] = {
        "deviceId": DEVICE, "moduleId": module, "etag": "private-twin-envelope",
        "properties": {"reported": copy.deepcopy(reported), "desired": {"credentials": "private-desired"}},
    }


@pytest.fixture
def opcua_reported_io(cloud, monkeypatch):
    """Observe the real helper's mocked HTTP boundary, including URL and deadline."""
    requests = []
    get = reported_route.get_async

    async def observed_get(url, responses, headers=None, timeout=5):
        requests.append((url, headers, timeout))
        await get(url, responses, headers=headers, timeout=timeout)

    monkeypatch.setattr(reported_route, "get_async", observed_get)
    return requests


def _assert_reported_read_only(cloud, requests, *, module=MODULE, reads=1):
    assert requests == [(
        f"https://testhub.invalid/twins/{DEVICE}/modules/{module}?api-version=2020-05-31-preview",
        {"Authorization": "synthetic"}, 15,
    )] * reads
    assert cloud.effects == [("twin_read", DEVICE, module, {"Authorization": "synthetic"})] * reads
    assert cloud.blobs == {} and cloud.direct_methods == 0


@pytest.mark.parametrize("name", CAPTURE_NAMES)
@pytest.mark.parametrize("timestamp", [None, REPORTED_TIMESTAMP, REPORTED_OFFSET_TIMESTAMP],
                         ids=["omitted-metadata", "recorded-z-precision", "recorded-offset"])
async def test_opcua_configuration_state_reporter_captures_effective_twin_public_get(
    client, db_session, opcua_provider, lifecycle_state, cloud, opcua_reported_io, name, timestamp,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    capture = _artifact(f"reported-{name}.json")
    original = copy.deepcopy(capture)
    assert capture["provenance"]["sourceCommit"] == _artifact("provenance.json")["runtimeSourceCommit"]
    assert capture["provenance"]["symbols"] == [
        "ConfigurationStateReporter.reportCondition", "ConfigurationStateReporter.removeConditionsByPrefix",
        "ConfigurationStateReporter.calculateState", "IoTEdgeInterface.updatePropertiesReported",
    ]
    previous = {
        "configId": "private-runtime-config-id", "$version": 17, "unrelated": {"private": True},
        "config": {"configs": {"legacy-client": {"status": False}}, "status": False,
                   "health": "FAILED", "conditions": [{"name": "stale"}], "message": "Previous state"},
    }
    if timestamp is not None:
        previous["$metadata"] = {"$lastUpdated": timestamp, "config": {"$lastUpdated": "1999-01-01T00:00:00Z"}}
    prior = copy.deepcopy(previous)
    assert capture["emittedPatch"]["config"]["configs"] is None
    effective = _effective_reported(previous, capture["emittedPatch"])
    assert effective["config"] == capture["effectiveConfig"]
    assert "configs" not in effective["config"]
    assert effective["configId"] == previous["configId"]
    assert previous == prior and capture == original
    _set_opcua_reported(cloud, effective)
    twin = copy.deepcopy(cloud.twins)
    expected = {"config": capture["effectiveConfig"]}
    if timestamp is not None:
        expected["lastUpdated"] = timestamp
    rows = await _snapshots(get_repository(ExtensionRepository)(db_session))
    Draft202012Validator(rows["reported"]["schema"]).validate(expected)
    calls = opcua_provider.calls.copy()
    response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")
    assert response.status_code == 200 and response.json() == expected
    assert not {"configId", "diagnostic", "diagnostics", "$metadata", "properties", "etag"}.intersection(response.json())
    assert cloud.twins == twin and opcua_provider.calls == calls
    _assert_reported_read_only(cloud, opcua_reported_io)


@pytest.mark.parametrize("reported,expected", [
    (None, None),
    ({}, None),
    ({"configId": "private", "$version": 1}, None),
    ({"$metadata": {}}, None),
    ({"$metadata": {"$lastUpdated": None}}, None),
    ({"$metadata": {"$lastUpdated": REPORTED_TIMESTAMP}}, {"lastUpdated": REPORTED_TIMESTAMP}),
    ({"configId": "private", "config": {"status": True, "health": "OPERATIONAL", "conditions": []}},
     {"config": {"status": True, "health": "OPERATIONAL", "conditions": []}}),
], ids=["null-reported", "empty", "only-private", "empty-metadata", "null-timestamp", "metadata-only", "message-omitted"])
async def test_opcua_reported_partial_null_no_data_public_get_has_no_injected_fields(
    client, opcua_provider, lifecycle_state, cloud, opcua_reported_io, reported, expected,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    _set_opcua_reported(cloud, reported)
    twin = copy.deepcopy(cloud.twins)
    calls = opcua_provider.calls.copy()
    response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")
    assert response.status_code == 200 and response.json() == expected
    assert cloud.twins == twin and opcua_provider.calls == calls
    _assert_reported_read_only(cloud, opcua_reported_io)


async def test_opcua_reported_missing_section_is_null_without_module_or_health_probe(
    client, opcua_provider, lifecycle_state, cloud, opcua_reported_io,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    cloud.twins[(DEVICE, MODULE)] = {"properties": {"desired": {"private": "not-returned"}}}
    calls = opcua_provider.calls.copy()
    response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")
    assert response.status_code == 200 and response.json() is None
    assert opcua_provider.calls == calls
    _assert_reported_read_only(cloud, opcua_reported_io)


async def test_opcua_reported_stale_metadata_repeats_without_freshness_or_health_guarantee(
    client, opcua_provider, lifecycle_state, cloud, opcua_reported_io,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    timestamp = "1999-01-01T00:00:00.1234567+02:00"
    config = _artifact("reported-operational.json")["effectiveConfig"]
    _set_opcua_reported(cloud, {"config": config, "$metadata": {"$lastUpdated": timestamp}})
    twin = copy.deepcopy(cloud.twins)
    calls = opcua_provider.calls.copy()
    for _ in range(2):
        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")
        assert response.status_code == 200 and response.json() == {"config": config, "lastUpdated": timestamp}
        assert response.json()["lastUpdated"].encode() == timestamp.encode()
    assert cloud.twins == twin and opcua_provider.calls == calls
    _assert_reported_read_only(cloud, opcua_reported_io, reads=2)


@pytest.mark.parametrize("payload", [
    {"config": None}, {"config": {}},
    {"config": {"status": True, "health": "OPERATIONAL"}},
    {"config": {"status": "true", "health": "OPERATIONAL", "conditions": []}},
    {"config": {"status": True, "health": "UNKNOWN", "conditions": []}},
    {"config": {"status": True, "health": "OPERATIONAL", "message": None, "conditions": []}},
    {"config": {"status": False, "health": "FAILED", "conditions": [{"name": "configuration", "health": "FAILED"}]}},
    {"$metadata": {"$lastUpdated": 123}},
], ids=["null-config", "empty-config", "missing-conditions", "no-status-coercion", "unknown-health",
        "null-message", "missing-condition-message", "numeric-metadata"])
async def test_opcua_reported_active_invalid_data_is_safe_500_after_one_exact_twin_read(
    client, opcua_provider, lifecycle_state, cloud, opcua_reported_io, payload,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    _set_opcua_reported(cloud, payload)
    twin = copy.deepcopy(cloud.twins)
    calls = opcua_provider.calls.copy()
    response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")
    assert response.status_code == 500 and response.json() == MISMATCH
    assert "configuration" not in response.text and "errors" not in response.json()
    assert cloud.twins == twin and opcua_provider.calls == calls
    _assert_reported_read_only(cloud, opcua_reported_io)


@pytest.mark.parametrize("actions,region,status", [
    (["device.read"], "EU", 200),
    (["device.read"], "US", 403),
    (["opcua.methods.read", "device.module_twin_config.write"], "EU", 403),
    ([], "EU", 403),
])
async def test_opcua_reported_non_admin_device_read_auth_precedes_invalid_or_unavailable_contract(
    client, db_session, opcua_provider, lifecycle_state, cloud, opcua_reported_io, actions, region, status,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    await AbacFixtures(db_session).setup(
        users={"caller": "opcua-reported-caller-oid"},
        scopes={"eu": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
        roles={"reported-role": actions},
        teams={"reported-team": {"scope": "eu", "roles": ["reported-role"], "users": ["caller"]}},
        devices={DEVICE: {"region": region}},
    )

    async def caller():
        return {"oid": "opcua-reported-caller-oid", "sub": "opcua-reported-caller-oid", "roles": []}

    previous = app.dependency_overrides[auth.validate_jwt]
    app.dependency_overrides[auth.validate_jwt] = caller
    try:
        calls = opcua_provider.calls.copy()
        config = _artifact("reported-warning.json")["effectiveConfig"]
        _set_opcua_reported(cloud, {"config": config})
        for state, allowed_status in [("valid", 200), ("invalid", 500), ("unavailable", 503)]:
            if state == "invalid":
                _set_opcua_reported(cloud, {"config": {"health": "private-invalid-data"}})
            elif state == "unavailable":
                module_schema_registry.replace_extension_entries(NAME, [], [MODULE])
            cloud.effects.clear()
            opcua_reported_io.clear()
            response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")
            assert response.status_code == (allowed_status if status == 200 else 403)
            if status == 403 or state == "unavailable":
                _assert_reported_read_only(cloud, opcua_reported_io, reads=0)
            else:
                _assert_reported_read_only(cloud, opcua_reported_io)
            if status == 200:
                assert response.json() == {"valid": {"config": config}, "invalid": MISMATCH, "unavailable": UNAVAILABLE}[state]
            assert "private-invalid-data" not in response.text and "errors" not in response.json()
        assert opcua_provider.calls == calls
    finally:
        app.dependency_overrides[auth.validate_jwt] = previous


@pytest.mark.parametrize("state", ["unregistered", "no-module-schemas", "configuration-only", "disabled"])
async def test_opcua_reported_generic_fallback_without_active_reported_schema(
    client, opcua_provider, lifecycle_state, cloud, opcua_reported_io, state,
):
    if state != "unregistered":
        manifest = await _register_opcua(client, lifecycle_state, opcua_provider, enable=state != "disabled")
        if state in {"no-module-schemas", "configuration-only"}:
            if state == "no-module-schemas":
                del manifest["module_schemas"]
            else:
                del manifest["module_schemas"][MODULE]["reported_schema_ref"]
            assert (await client.put(f"/extensions/{NAME}", json=manifest)).status_code == 200
    assert module_schema_registry.get(MODULE, "reported") is None
    # Deliberately invalid typed data distinguishes generic fallback from enforcement.
    raw = {"config": {"health": "legacy-generic", "keepNull": None}, "configId": "private"}
    _set_opcua_reported(cloud, raw)
    calls = opcua_provider.calls.copy()
    response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")
    assert response.status_code == 200 and response.json() == {"config": raw["config"]}
    assert opcua_provider.calls == calls
    _assert_reported_read_only(cloud, opcua_reported_io)


async def test_opcua_reported_schema_is_selected_by_exact_module_not_prefix(
    client, opcua_provider, lifecycle_state, cloud, opcua_reported_io,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    other = f"{MODULE}-other"
    _set_opcua_reported(cloud, {"config": {"health": "generic-other-module"}}, module=other)
    calls = opcua_provider.calls.copy()
    response = await client.get(f"/{DEVICE}/twin/identity/{other}/reported")
    assert response.status_code == 200 and response.json() == {"config": {"health": "generic-other-module"}}
    assert opcua_provider.calls == calls
    _assert_reported_read_only(cloud, opcua_reported_io, module=other)


async def test_opcua_reported_public_ref_remove_restore_replace_disable_hydrate_deregister(
    client, db_session, opcua_provider, lifecycle_state, cloud, opcua_reported_io, hydrate,
):
    manifest = await _register_opcua(client, lifecycle_state, opcua_provider)
    repo = get_repository(ExtensionRepository)(db_session)
    config = _artifact("reported-warning.json")["effectiveConfig"]
    _set_opcua_reported(cloud, {"config": config})

    async def check(status, expected):
        calls = opcua_provider.calls.copy()
        cloud.effects.clear()
        opcua_reported_io.clear()
        response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")
        assert response.status_code == status and response.json() == expected
        assert opcua_provider.calls == calls
        _assert_reported_read_only(cloud, opcua_reported_io)

    await check(200, {"config": config})
    _set_opcua_reported(cloud, {"config": {"health": "generic"}})
    await check(500, MISMATCH)
    del manifest["module_schemas"][MODULE]["reported_schema_ref"]
    assert (await client.put(f"/extensions/{NAME}", json=manifest)).status_code == 200
    await _snapshots(repo, purposes=("configuration",))
    await check(200, {"config": {"health": "generic"}})
    assert (await client.put(f"/extensions/{NAME}", json=_manifest(opcua_provider))).status_code == 200
    before = await _snapshots(repo)
    await check(500, MISMATCH)
    _set_opcua_reported(cloud, {"config": config})
    await check(200, {"config": config})

    # Test-only replacement provider document tightens one health enum to prove
    # public GET uses the new persisted snapshot, not a stale validator cache.
    component = opcua_provider.document["components"]["schemas"]["OpcuaClientReportedConfig"]
    component["properties"]["health"]["enum"] = ["OPERATIONAL"]
    assert (await client.put(f"/extensions/{NAME}", json=_manifest(opcua_provider))).status_code == 200
    after = await _snapshots(repo)
    assert after["reported"]["version"] != before["reported"]["version"]
    assert after["reported"]["schema"] != before["reported"]["schema"]
    # Version also includes resolution time; compare the unaffected purpose's schema.
    assert after["configuration"]["schema"] == before["configuration"]["schema"]
    await check(500, MISMATCH)
    assert (await client.post(f"/extensions/{NAME}/disable")).status_code == 200
    await check(200, {"config": config})
    assert (await client.post(f"/extensions/{NAME}/enable")).status_code == 200
    persisted = await _snapshots(repo)
    await check(500, MISMATCH)
    # Offline provider hydration retains the valid persisted contract, without
    # making availability/freshness of hardware a condition for reported GET.
    opcua_provider.document = None
    extensions_setup._unmount_extension(NAME)
    await hydrate()
    assert not module_schema_registry.is_unavailable(MODULE)
    assert (await _snapshots(repo))["reported"]["version"] == persisted["reported"]["version"]
    await check(500, MISMATCH)
    config = _artifact("reported-operational.json")["effectiveConfig"]
    _set_opcua_reported(cloud, {"config": config})
    await check(200, {"config": config})
    assert (await client.delete(f"/extensions/{NAME}")).status_code == 204
    assert await repo.list_module_schemas(NAME) == []
    assert module_schema_registry.claim_owner(MODULE) is None
    _set_opcua_reported(cloud, {"config": {"health": "generic"}})
    await check(200, {"config": {"health": "generic"}})


@pytest.mark.parametrize("failure,message", [
    ("missing-module", f"Module {MODULE} not found on device {DEVICE}"),
    ("backend", "Could not retrieve module identity twin: synthetic upstream failure"),
    ("network", "synthetic network failure"),
    ("invalid-json", "Expecting value: line 1 column 1 (char 0)"),
])
async def test_opcua_reported_real_helper_preserves_existing_500_error_wrappers(
    client, opcua_provider, lifecycle_state, cloud, opcua_reported_io, monkeypatch, failure, message,
):
    await _register_opcua(client, lifecycle_state, opcua_provider)
    if failure != "missing-module":
        observed_get = reported_route.get_async

        async def failed_get(url, responses, headers=None, timeout=5):
            # Record the same exact boundary as a successful/missing-module read.
            await observed_get(url, responses, headers=headers, timeout=timeout)
            if failure == "network":
                raise RuntimeError("synthetic network failure")
            responses[url].status_code = 200 if failure == "invalid-json" else 502
            responses[url].text = "not JSON" if failure == "invalid-json" else "synthetic upstream failure"

        monkeypatch.setattr(reported_route, "get_async", failed_get)
    calls = opcua_provider.calls.copy()
    response = await client.get(f"/{DEVICE}/twin/identity/{MODULE}/reported")
    assert response.status_code == 500
    # The shipped APIError handler stringifies the helper's error dict under message.
    helper_error = {"error": "IoTBackendAPIError", "message": f"Could not retrieve module identity twin: {message}"}
    assert response.json() == {"message": str(helper_error)}
    assert opcua_provider.calls == calls
    _assert_reported_read_only(cloud, opcua_reported_io)
