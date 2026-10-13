"""Typed write characterization and generic POST contract for module_config/router.py."""

import importlib
import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from authorization.abac_permission_check import ABACPermissionCheck
from exceptions import IoTBackendAPIError, UploadError
from routers.module_config import router

write_route = importlib.import_module("routers.module_config.routes.post_module_twin_config")


@pytest.fixture
def writes(monkeypatch):
    effects = SimpleNamespace(contexts=[], blobs=[], uploads=[], patches=[], upload_error=None, status=200)

    class Blob:
        async def upload_blob(self, data, overwrite):
            if effects.upload_error:
                raise effects.upload_error
            effects.uploads.append((data, overwrite))

    class Context:
        def __init__(self, *args, **kwargs):
            effects.contexts.append((args, kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get_blob_client(self, name):
            effects.blobs.append(name)
            return Blob()

    async def patch(url, responses, _json, headers):
        effects.patches.append((url, _json, headers))
        responses[url] = SimpleNamespace(status_code=effects.status, text="synthetic hub error")

    monkeypatch.setattr(write_route, "PUBLIC_STORAGE_ACCOUNT_NAME", "teststorage")
    monkeypatch.setattr(write_route, "BLOB_SAS_TOKEN_MODULE_CONF", "sig=synthetic")
    monkeypatch.setattr(write_route, "IOT_HUB_NAME", "testhub.invalid")
    monkeypatch.setattr(write_route, "BlobContainerContext", Context)
    monkeypatch.setattr(write_route, "get_iothub_auth_headers", lambda: {"Authorization": "synthetic"})
    monkeypatch.setattr(write_route, "patch_async", patch)
    return effects


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router.module_config)
    for route in router.module_config.routes:
        for dependency in route.dependant.dependencies:
            if isinstance(dependency.call, ABACPermissionCheck):
                app.dependency_overrides[dependency.call] = lambda: None
    return app


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


OPCUA = {"schemaVersion": "1.0", "mqttEndpoint": None, "clients": {}}
DISCOVER = {
    "scheduledCron": "* * * * *",
    "scanDefinition": {"networkDefinition": "192.0.2.0", "subnetMask": 24, "ports": [80]},
    "endpointNames": {"192.0.2.1": {"name": "test", "description": None, "serviceNames": {"80": "http"}}},
}


def assert_targets(writes, device, module):
    assert writes.contexts == [(("teststorage", "iotedge-device-twin"), {"sas_token": "sig=synthetic"})]
    assert writes.blobs == [f"{device}/{module}/current"]
    assert writes.uploads[0][1] is True
    url, patch, headers = writes.patches[0]
    assert url == f"https://testhub.invalid/twins/{device}/modules/{module}?api-version=2020-05-31-preview"
    desired = patch["properties"]["desired"]
    config_id = desired["configId"]
    assert UUID(config_id).hex == config_id and len(config_id) == 32
    assert patch == {"properties": {"desired": {
        "configId": config_id,
        "configBlobUrl": f"https://teststorage.blob.core.windows.net/iotedge-device-twin/{device}/{module}/current?sig=synthetic",
    }}}
    assert headers == {"Authorization": "synthetic"}


@pytest.mark.parametrize("module,payload,blob", [
    ("seal-module-opcua-client", OPCUA, '{"schemaVersion":"1.0","clients":{}}'),
    ("seal-app-net-discover", DISCOVER,
     '{"scheduledCron":"* * * * *","scanDefinition":{"networkDefinition":"192.0.2.0","subnetMask":24,"ports":[80]},"endpointNames":{"192.0.2.1":{"name":"test","serviceNames":{"80":"http"}}}}'),
])
async def test_typed_post_characterization_bytes_http_and_targets(client, writes, module, payload, blob):
    response = await client.post(f"/Device-Exact/twin/config/{module}", json=payload)
    assert response.status_code == 200
    assert response.json() == [payload, 200]
    assert writes.uploads == [(blob, True)]
    assert_targets(writes, "Device-Exact", module)


@pytest.mark.parametrize("constant,value,message", [
    ("PUBLIC_STORAGE_ACCOUNT_NAME", "", "Public storage account name is not set. Cannot upload file."),
    ("BLOB_SAS_TOKEN_MODULE_CONF", " ", "BLOB_SAS_TOKEN_MODULE_CONF is not configured. SAS token is required for Edge Device blob access."),
])
async def test_typed_post_characterization_missing_storage(client, writes, monkeypatch, constant, value, message):
    monkeypatch.setattr(write_route, constant, value)
    with pytest.raises(IoTBackendAPIError) as exc:
        await client.post("/Device-Exact/twin/config/seal-module-opcua-client", json=OPCUA)
    assert exc.value.status_code == 500
    assert exc.value.message == {"error": "IoTBackendAPIError", "message": message}
    assert writes.contexts == [] and writes.patches == []


async def test_typed_post_characterization_upload_failure(client, writes):
    writes.upload_error = RuntimeError("synthetic upload error")
    with pytest.raises(UploadError) as exc:
        await client.post("/Device-Exact/twin/config/seal-module-opcua-client", json=OPCUA)
    assert exc.value.status_code == 400
    assert exc.value.message == {"error": "UploadError", "message": "could not upload data object to blob storage: synthetic upload error"}
    assert writes.uploads == [] and writes.patches == []


async def test_typed_post_characterization_hub_failure_keeps_upload(client, writes):
    writes.status = 409
    with pytest.raises(IoTBackendAPIError) as exc:
        await client.post("/Device-Exact/twin/config/seal-module-opcua-client", json=OPCUA)
    config_id = writes.patches[0][1]["properties"]["desired"]["configId"]
    assert exc.value.status_code == 409
    assert exc.value.message == {"error": "IoTBackendAPIError", "message": (
        "could not update module twin of seal-module-opcua-client on Device-Exact on the Azure IoT Hub. "
        f"Config-Id: [{config_id}]. Error: synthetic hub error"
    )}
    assert len(writes.uploads) == 1


@pytest.fixture
def registry(monkeypatch):
    from extensions import body_validation
    from routers.module_config.registry import ModuleSchemaRegistry

    fresh = ModuleSchemaRegistry()
    monkeypatch.setattr(router, "module_schema_registry", fresh)
    body_validation._module_validator_cache.clear()
    yield fresh
    body_validation._module_validator_cache.clear()


def activate(registry, schema, module="neutral-module", purpose="configuration", version="v1"):
    from routers.module_config.registry import ActiveModuleSchema

    active = ActiveModuleSchema(module, purpose, "neutral", version, schema)
    registry.replace_extension_entries("neutral", [active], [])
    return active


async def test_generic_post_fallback_keeps_all_supplied_json(client, registry, writes):
    payload = {"null": None, "nested": {"null": None}, "array": [None, {"x": None}], "extra": "retained"}
    response = await client.post("/Device-Exact/twin/config/Module-Exact", json=payload)
    assert response.status_code == 200
    assert response.json() == [payload, 200]
    assert json.loads(writes.uploads[0][0]) == payload
    assert_targets(writes, "Device-Exact", "Module-Exact")


@pytest.mark.parametrize("raw", ["[]", "null", "true", "4", '"text"'])
async def test_generic_post_fallback_rejects_non_objects(client, registry, writes, raw):
    response = await client.post("/device/twin/config/neutral-module", content=raw)
    assert response.status_code == 422
    assert response.json()["errors"] == [{"loc": [], "rule": "type", "msg": "must match the declared type"}]
    assert writes.contexts == [] and writes.patches == []


@pytest.mark.parametrize("raw", ["", "{", "NaN", "Infinity", "-Infinity", "1e9999", '{"x":-1e9999}'])
async def test_generic_post_bad_json_never_writes(client, registry, writes, raw):
    response = await client.post("/device/twin/config/neutral-module", content=raw)
    assert response.status_code == 400
    assert writes.contexts == [] and writes.patches == []


@pytest.mark.parametrize("declared_length", [True, False])
async def test_generic_post_body_limit_with_and_without_content_length(client, registry, writes, declared_length):
    raw = b" " * (1024 * 1024 + 1)

    async def stream():
        yield raw[:1024 * 1024]
        yield raw[1024 * 1024:]

    response = await client.post("/device/twin/config/neutral-module", content=raw if declared_length else stream())
    assert response.status_code == 413
    assert writes.contexts == [] and writes.patches == []


@pytest.mark.parametrize("schema,payload", [
    ({"type": "array", "items": {"type": ["object", "null"]}}, [None, {"key": None}]),
    ({"type": "null"}, None), ({"type": "string"}, "value"),
    ({"type": "number"}, 3.5), ({"type": "boolean"}, False),
])
async def test_generic_post_uses_active_root_shape(client, registry, writes, schema, payload):
    activate(registry, schema)
    response = await client.post("/device/twin/config/neutral-module", content=json.dumps(payload))
    assert response.status_code == 200
    assert response.json() == [payload, 200]
    assert json.loads(writes.uploads[0][0]) == payload


NULLABLE_SCHEMA = {
    "type": "object", "required": ["required"],
    "properties": {
        "required": {"type": ["string", "null"]},
        "optional": {"type": ["string", "null"]},
        "defaulted": {"type": "integer", "default": 12},
    },
}


@pytest.mark.parametrize("payload", [
    {"required": None},
    {"required": None, "optional": None, "extra": {"nested": None}, "items": [None]},
])
async def test_generic_post_no_defaults_pruning_or_removed_extras(client, registry, writes, payload):
    activate(registry, NULLABLE_SCHEMA)
    response = await client.post("/device/twin/config/neutral-module", json=payload)
    assert response.status_code == 200
    assert response.json() == [payload, 200]
    assert json.loads(writes.uploads[0][0]) == payload


@pytest.mark.parametrize("schema,payload,rule", [
    (NULLABLE_SCHEMA, {}, "required"),
    (NULLABLE_SCHEMA, {"required": None, "defaulted": "12"}, "type"),
    ({"type": "object", "additionalProperties": False}, {"password": "supersecret"}, "additionalProperties"),
    ({"type": "string", "enum": ["provider-secret"]}, "supersecret", "enum"),
    ({"type": "string", "pattern": "^provider-secret$"}, "supersecret", "pattern"),
    ({"type": "array", "uniqueItems": True}, ["supersecret", "supersecret"], "uniqueItems"),
])
async def test_generic_post_schema_errors_are_structured_and_safe(client, registry, writes, schema, payload, rule):
    activate(registry, schema)
    response = await client.post("/device/twin/config/neutral-module", content=json.dumps(payload))
    assert response.status_code == 422
    assert response.json()["errors"][0]["rule"] == rule
    assert "supersecret" not in response.text and "provider-secret" not in response.text
    assert writes.contexts == [] and writes.patches == []


@pytest.mark.parametrize("schema", [
    {"type": "not-a-type"}, {"$ref": "#/components/schemas/Missing"},
    {"$ref": "https://never-fetch.invalid/schema"},
    {"type": "object", "components": {"schemas": {"Unused": {"type": "not-a-type"}}}},
])
async def test_generic_post_unusable_schema_fails_closed(client, registry, writes, schema):
    activate(registry, schema)
    response = await client.post("/device/twin/config/neutral-module", json={})
    assert response.status_code == 503
    assert writes.contexts == [] and writes.patches == []
    assert "never-fetch" not in response.text


async def test_generic_post_unavailable_claim_precedes_body_buffering(client, registry, writes, monkeypatch):
    from extensions import body_validation

    registry.replace_extension_entries("neutral", [], ["neutral-module"])

    async def forbidden(*args):
        raise AssertionError("unavailable module body must not be buffered")

    monkeypatch.setattr(body_validation, "read_limited_body", forbidden)
    response = await client.post("/device/twin/config/neutral-module", content="not JSON")
    assert response.status_code == 503
    assert writes.contexts == [] and writes.patches == []


@pytest.mark.parametrize("change", ["unavailable", "replace", "disable", "enable"])
async def test_generic_post_reselects_after_awaiting_body(client, registry, writes, monkeypatch, change):
    from extensions import body_validation

    if change != "enable":
        activate(registry, {"type": "object"})
    original = body_validation.read_limited_body

    async def changed(request):
        raw = await original(request)
        if change == "unavailable":
            registry.replace_extension_entries("neutral", [], ["neutral-module"])
        elif change == "disable":
            registry.replace_extension_entries("neutral", [], [])
        else:
            activate(registry, {"type": "array"}, version="v2")
        return raw

    monkeypatch.setattr(body_validation, "read_limited_body", changed)
    response = await client.post("/device/twin/config/neutral-module", content="[]")
    expected = {"unavailable": 503, "replace": 200, "disable": 422, "enable": 200}[change]
    assert response.status_code == expected
    assert len(writes.uploads) == (1 if expected == 200 else 0)
    if expected == 200:
        assert ("neutral-module", "configuration", "v2") in body_validation._module_validator_cache


async def test_generic_post_never_selects_reported_or_other_module_schema(client, registry, writes):
    from routers.module_config.registry import ActiveModuleSchema

    registry.replace_extension_entries("neutral", [
        ActiveModuleSchema("neutral-module", "reported", "neutral", "r1", {"type": "array"}),
        ActiveModuleSchema("other-module", "configuration", "neutral", "c1", {"type": "array"}),
    ], [])
    assert (await client.post("/device/twin/config/neutral-module", content="[]")).status_code == 422
    assert (await client.post("/device/twin/config/neutral-module", json={})).status_code == 200


async def test_generic_post_validation_uses_one_exact_snapshot(client, registry, writes, monkeypatch):
    from extensions import body_validation

    active = activate(registry, {"type": "object"}, module="Module-Exact", version="exact-version")
    calls = []
    original = body_validation.validate_module_snapshot

    def validate(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(body_validation, "validate_module_snapshot", validate)
    response = await client.post("/Device-Exact/twin/config/Module-Exact", json={"keep": None})
    assert response.status_code == 200
    assert calls == [("Module-Exact", "configuration", "exact-version", active.schema, {"keep": None})]
    assert_targets(writes, "Device-Exact", "Module-Exact")


def test_generic_post_openapi_is_required_idempotent_and_configuration_only(app, registry):
    from routers.module_config.registry import ActiveModuleSchema

    router.install_module_config_openapi(app)
    core = app.openapi()
    body = core["paths"][router._CONFIG_OPERATION_PATH]["post"]["requestBody"]
    assert body == {"required": True, "content": {"application/json": {"schema": {"anyOf": [{"type": "object"}]}}}}
    nested = {"$ref": "#/components/schemas/Config", "components": {"schemas": {
        "Config": {"type": "object", "properties": {"item": {"$ref": "#/components/schemas/Item"}}},
        "Item": {"type": "integer", "minimum": 2},
    }}}
    registry.replace_extension_entries("neutral", [
        ActiveModuleSchema("neutral-module", "configuration", "neutral", "v1", nested),
        ActiveModuleSchema("neutral-module", "reported", "neutral", "r1", {"title": "ReportedOnly", "type": "array"}),
    ], [])
    first = app.openapi()
    assert first == app.openapi()
    alternatives = first["paths"][router._CONFIG_OPERATION_PATH]["post"]["requestBody"]["content"]["application/json"]["schema"]["anyOf"]
    assert len(alternatives) == 2 and "ReportedOnly" not in json.dumps(alternatives)
    config = first["components"]["schemas"][alternatives[1]["$ref"].rsplit("/", 1)[1]]
    item = first["components"]["schemas"][config["properties"]["item"]["$ref"].rsplit("/", 1)[1]]
    assert item == {"type": "integer", "minimum": 2}
    assert "Module_configuration_" not in json.dumps(app.openapi_schema)
    registry.replace_extension_entries("neutral", [], [])
    assert app.openapi() == core


async def test_generic_post_separates_two_module_validators_with_the_same_version(client, registry, writes):
    from routers.module_config.registry import ActiveModuleSchema

    registry.replace_extension_entries("neutral", [
        ActiveModuleSchema("object-module", "configuration", "neutral", "v1", {"type": "object"}),
        ActiveModuleSchema("array-module", "configuration", "neutral", "v1", {"type": "array"}),
    ], [])
    assert (await client.post("/device/twin/config/object-module", content="{}")).status_code == 200
    assert (await client.post("/device/twin/config/array-module", content="[]")).status_code == 200
    assert (await client.post("/device/twin/config/object-module", content="[]")).status_code == 422
    assert (await client.post("/device/twin/config/array-module", content="{}")).status_code == 422
    assert len(writes.uploads) == 2

    app = FastAPI()
    app.include_router(router.module_config)
    router.install_module_config_openapi(app)
    alternatives = app.openapi()["paths"][router._CONFIG_OPERATION_PATH]["post"]["requestBody"]["content"]["application/json"]["schema"]["anyOf"]
    assert len(alternatives) == 3
    assert {alt["type"] for alt in alternatives[1:]} == {"object", "array"}


@pytest.mark.parametrize("depth", [300, 900])
async def test_generic_post_round_trips_parser_accepted_deep_json(client, registry, writes, depth):
    from routers.module_config.payload import parse_module_json

    raw = b'{"x":' + b"[" * depth + b'"leaf"' + b"]" * depth + b"}"
    payload = parse_module_json(raw)
    response = await client.post("/Device-Exact/twin/config/Module-Exact", content=raw)
    assert response.status_code == 200
    assert response.json() == [payload, 200]
    assert json.loads(writes.uploads[0][0]) == payload
    assert_targets(writes, "Device-Exact", "Module-Exact")


@pytest.mark.parametrize("raw", [
    b'{"x":"\\ud800"}', b'{"\\ud800":null}', b'{"x":["\\udfff",{"\\ud800":"\\ud800"}]}',
])
async def test_generic_post_round_trips_accepted_surrogate_values_and_keys(client, registry, writes, raw):
    payload = json.loads(raw)
    response = await client.post("/Device-Exact/twin/config/Module-Exact", content=raw)
    assert response.status_code == 200
    assert response.json() == [payload, 200]
    assert json.loads(writes.uploads[0][0]) == payload
    assert writes.uploads[0][0].encode("ascii")
    assert_targets(writes, "Device-Exact", "Module-Exact")


async def test_generic_post_surrogate_error_location_is_safe_422_without_writes(client, registry, writes):
    activate(registry, {"type": "object", "additionalProperties": {"type": "integer"}})
    response = await client.post("/device/twin/config/neutral-module", content=b'{"\\ud800":"bad"}')
    assert response.status_code == 422
    assert response.json()["errors"] == [
        {"loc": ["\ud800"], "rule": "type", "msg": "must match the declared type"}
    ]
    assert "bad" not in response.text
    assert writes.contexts == [] and writes.uploads == [] and writes.patches == []


async def test_generic_post_keeps_sa_prefixed_properties_in_blob_and_http(client, registry, writes):
    payload = {"_sa_keep": None, "nested": {"_sa_name": "x"}, "_sampleRate": 5,
               "items": [{"_sa_item": None}]}
    response = await client.post("/Device-Exact/twin/config/Module-Exact", json=payload)
    assert response.status_code == 200
    assert response.json() == [payload, 200]
    assert json.loads(writes.uploads[0][0]) == payload
    assert_targets(writes, "Device-Exact", "Module-Exact")


async def test_generic_post_transports_the_actual_helper_tuple(client, registry, monkeypatch):
    supplied = {"_sa_keep": None}

    async def helper(device, module, payload):
        assert (device, module) == ("Device-Exact", "Module-Exact")
        assert json.loads(payload.model_dump_json(exclude_none=True)) == supplied
        return payload, 207

    monkeypatch.setattr(router, "_post_module_twin_config", helper)
    response = await client.post("/Device-Exact/twin/config/Module-Exact", json=supplied)
    assert response.status_code == 200
    assert response.json() == [supplied, 207]


async def test_generic_post_payload_serialization_failure_precedes_helper_and_storage(client, registry, writes, monkeypatch):
    def formatter_failure(*args, **kwargs):
        raise ValueError("synthetic formatter failure")

    monkeypatch.setattr(router, "ModuleConfigPayload", formatter_failure)
    with pytest.raises(ValueError, match="synthetic formatter failure"):
        await client.post("/Device-Exact/twin/config/Module-Exact", json={})
    assert writes.contexts == [] and writes.uploads == [] and writes.patches == []
