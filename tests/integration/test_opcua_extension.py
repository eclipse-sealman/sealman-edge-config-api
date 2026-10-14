"""OPCUA lifecycle and canonical configuration adoption of committed artifacts.

No sibling-repository imports or live cloud credentials: PostgreSQL is disposable,
provider OpenAPI travels over real loopback HTTP, and only cloud I/O is replaced.
Configuration uses real core helpers and non-admin ABAC, not a provider callback.
Template execution and reported readiness remain later stages.
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

from starlette.routing import Match

import pytest
from jsonschema import Draft202012Validator

import auth
from db.repos.extension import ExtensionRepository
from db.session import get_repository
from extensions import body_validation, schemas as extension_schemas, setup as extensions_setup
from main import app
from routers.module_config.registry import module_schema_registry
from routers.module_config.routes import post_module_twin_config as write_route
from tests.integration.test_abac_authorization import AbacFixtures
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
