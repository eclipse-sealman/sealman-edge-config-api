"""OPCUA WP-5 adoption of committed provider artifacts through real management.

No sibling-repository imports or live cloud credentials: PostgreSQL is disposable,
provider OpenAPI travels over real loopback HTTP, and only cloud I/O is replaced.
Configuration/ABAC, template execution and reported readiness are later stages.
"""

import copy
import hashlib
import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator

import auth
from db.repos.extension import ExtensionRepository
from db.session import get_repository
from extensions import schemas as extension_schemas, setup as extensions_setup
from main import app
from routers.module_config.registry import module_schema_registry
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
        path = urlsplit(self.path).path
        self.server.calls[path] += 1
        status = 200 if path == "/openapi.json" and self.server.document is not None else 404
        body = json.dumps(self.server.document if status == 200 else {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def opcua_provider(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpcuaDocumentHandler)
    server.document = _artifact("openapi.json")
    server.calls = Counter()
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
    return document


def test_opcua_committed_artifact_hashes_and_src_app_registered_handler_contract():
    provenance = _artifact("provenance.json")
    assert provenance["providerCommit"] == "ad10d2d67d02b4bf0493c273487ad3e36b68d2fc"
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
    assert {action["name"] for action in manifest["actions"]} == {"opcua.methods.read", "opcua.methods.write"}
    assert len(manifest["routes"]) == 12
    for route in manifest["routes"]:
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
    assert set(document["paths"]) == {"/health"}
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
    for action in ("opcua.methods.read", "opcua.methods.write"):
        assert not await repo.is_action_granted_to_any_role(action)

    assert (await client.post(f"/extensions/{NAME}/enable")).status_code == 200
    assert opcua_provider.calls == Counter({"/openapi.json": 2})
    enabled = await _opcua_openapi(client, baseline)
    await _snapshots(repo)
    methods = _artifact("methods.json")
    routes = await repo.list_routes(NAME)
    assert len(routes) == 12 and {row["method_name"] for row in routes} == METHODS
    for row in routes:
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

    # Replace recreates surrogate route IDs, but public operation IDs and nested
    # documentation must not depend on those IDs or resolution timestamps.
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
    await _opcua_openapi(client, baseline, purposes=())  # method routes survive module failure
    calls = opcua_provider.calls.copy()
    for path in (f"/{DEVICE}/twin/config/{MODULE}", f"/{DEVICE}/twin/identity/{MODULE}/reported"):
        response = await client.get(path)
        assert response.status_code == 503 and response.json() == UNAVAILABLE
    for raw in ("not JSON", " " * (1024 * 1024 + 1)):
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
