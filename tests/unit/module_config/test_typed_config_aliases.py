"""C1 fixed aliases of router.py's shared literal-JSON configuration behavior.

Neutral contracts deliberately replace legacy protocol normalization. HTTP ABAC is
bypassed only here; real non-admin coverage lives in test_module_config.py.
"""
import json

import pytest
from fastapi import HTTPException

from extensions import body_validation
from routers.module_config import router
from routers.module_config.registry import ActiveModuleSchema
from tests.unit.module_config.test_post_module_twin_config import (  # noqa: F401
    app, client, registry, writes, activate, assert_targets,
)

FIXED_MODULES = ["seal-module-opcua-client", "seal-app-net-discover"]
MODULES = FIXED_MODULES + ["neutral-module"]
NEUTRAL_SCHEMA = {
    "type": "object", "required": ["required"],
    "properties": {
        "required": {"type": ["string", "null"]},
        "defaulted": {"type": "integer", "default": 12},
        "nested": {"$ref": "#/components/schemas/Nested"},
    },
    "components": {"schemas": {"Nested": {
        "type": "object", "properties": {"value": {"type": ["integer", "null"]}},
    }}},
}


@pytest.mark.parametrize("module", MODULES)
async def test_wrappers_delegate_to_plain_shared_functions_with_exact_targets(client, monkeypatch, module):
    calls = []

    async def read(device, selected):
        calls.append(("read", device, selected))
        return {"literal": None}

    async def write(device, selected, request):
        calls.append(("write", device, selected))
        return router.ModuleJSONResponse([json.loads(await request.body()), 207])

    monkeypatch.setattr(router, "_read_module_configuration", read)
    monkeypatch.setattr(router, "_write_module_configuration", write)
    # Neither query nor body can override the module chosen by a fixed wrapper.
    path = f"/Device-Exact/twin/config/{module}?module=other-module"
    assert (await client.get(path)).json() == {"literal": None}
    assert (await client.post(path, json={"module": "other-module"})).json() == [{"module": "other-module"}, 207]
    assert calls == [("read", "Device-Exact", module), ("write", "Device-Exact", module)]


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize("active", [False, True])
async def test_literal_nested_nullable_extra_sa_fields_and_exact_helper_targets(client, registry, writes, monkeypatch, module, active):
    payload = {"required": None, "nested": {"value": None, "_sa_keep": None},
               "extra": {"ports": ["80", 80, 80]}, "_sa_keep": None, "module": "other-module"}
    if active:
        activate(registry, NEUTRAL_SCHEMA, module=module, version="exact-v1")
    response = await client.post(f"/Device-Exact/twin/config/{module}?module=other-module", json=payload)
    assert response.status_code == 200 and response.json() == [payload, 200]
    assert json.loads(writes.uploads[0][0]) == payload
    assert_targets(writes, "Device-Exact", module)

    async def read(device, selected):
        assert (device, selected) == ("Device-Exact", module)
        return payload

    monkeypatch.setattr(router, "_get_module_twin_config", read)
    response = await client.get(f"/Device-Exact/twin/config/{module}?module=other-module")
    assert response.status_code == 200 and response.json() == payload
    if active:
        assert (module, "configuration", "exact-v1") in body_validation._module_validator_cache


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize("raw,expected", [
    ("[]", 422), ("null", 422), ("true", 422), ("4", 422), ('"text"', 422),
    ("", 400), ("{", 400), ("NaN", 400), ("Infinity", 400), ("-Infinity", 400),
    ("1e9999", 400), ('{"x":-1e9999}', 400),
])
async def test_fallback_shapes_and_finite_json_errors_without_io(client, registry, writes, module, raw, expected):
    response = await client.post(f"/device/twin/config/{module}", content=raw)
    assert response.status_code == expected
    if expected == 422:
        assert response.json()["errors"] == [{"loc": [], "rule": "type", "msg": "must match the declared type"}]
    assert writes.contexts == [] and writes.uploads == [] and writes.patches == []


@pytest.mark.parametrize("module", FIXED_MODULES)
@pytest.mark.parametrize("declared_length", [True, False])
async def test_oversize_body_never_writes(client, registry, writes, module, declared_length):
    raw = b" " * (1024 * 1024 + 1)

    async def stream():
        yield raw[:-1]
        yield raw[-1:]

    assert (await client.post(f"/device/twin/config/{module}", content=raw if declared_length else stream())).status_code == 413
    assert writes.contexts == [] and writes.patches == []


@pytest.mark.parametrize("module", MODULES)
async def test_unavailable_precedes_body_and_helper_io(client, registry, writes, monkeypatch, module):
    registry.replace_extension_entries("neutral", [], [module])

    def forbidden(*args):
        raise AssertionError("unavailable modules must not buffer bodies or access helpers")

    monkeypatch.setattr(body_validation, "read_limited_body", forbidden)
    monkeypatch.setattr(router, "_get_module_twin_config", forbidden)
    for response in (await client.get(f"/device/twin/config/{module}"),
                     await client.post(f"/device/twin/config/{module}", content="bad")):
        assert response.status_code == 503
        assert response.json() == {"detail": "Module schema is unavailable"}
    assert writes.contexts == [] and writes.patches == []


@pytest.mark.parametrize("module", MODULES)
async def test_schema_errors_are_safe_bounded_and_get_mismatch_is_not_projection(client, registry, writes, monkeypatch, module):
    activate(registry, {"type": "object", "additionalProperties": {"type": "integer"}}, module=module)
    payload = {"x" * 150 + str(i): "secret" for i in range(30)}
    response = await client.post(f"/device/twin/config/{module}", json=payload)
    assert response.status_code == 422 and "secret" not in response.text
    assert len(response.json()["errors"]) == 20
    assert all(len(error["loc"][0]) == 128 for error in response.json()["errors"])
    assert writes.contexts == [] and writes.patches == []

    async def read(*args):
        return payload

    monkeypatch.setattr(router, "_get_module_twin_config", read)
    response = await client.get(f"/device/twin/config/{module}")
    assert response.status_code == 500
    assert response.json() == {"detail": "Module data could not be validated against its active schema"}


@pytest.mark.parametrize("module", FIXED_MODULES)
@pytest.mark.parametrize("schema", [
    {"type": "not-a-type"}, {"$ref": "#/components/schemas/Missing"},
    {"$ref": "https://never-fetch.invalid/schema"},
    {"type": "object", "components": {"schemas": {"Unused": {"type": "not-a-type"}}}},
])
async def test_unusable_write_schema_is_safe_503(client, registry, writes, module, schema):
    activate(registry, schema, module=module)
    response = await client.post(f"/device/twin/config/{module}", json={})
    assert response.status_code == 503 and "never-fetch" not in response.text
    assert writes.contexts == [] and writes.patches == []


@pytest.mark.parametrize("module", FIXED_MODULES)
@pytest.mark.parametrize("change", ["unavailable", "replace", "disable", "enable"])
async def test_write_reselects_exact_snapshot_after_awaiting_body(client, registry, writes, monkeypatch, module, change):
    if change != "enable":
        activate(registry, {"type": "object"}, module=module)
    original = body_validation.read_limited_body

    async def changed(request):
        raw = await original(request)
        if change == "unavailable":
            registry.replace_extension_entries("neutral", [], [module])
        elif change == "disable":
            registry.replace_extension_entries("neutral", [], [])
        else:
            activate(registry, {"type": "array"}, module=module, version="v2")
        return raw

    monkeypatch.setattr(body_validation, "read_limited_body", changed)
    response = await client.post(f"/device/twin/config/{module}", content="[]")
    expected = {"unavailable": 503, "replace": 200, "disable": 422, "enable": 200}[change]
    assert response.status_code == expected
    assert len(writes.uploads) == (1 if expected == 200 else 0)
    if expected == 200:
        assert (module, "configuration", "v2") in body_validation._module_validator_cache


@pytest.mark.parametrize("module", FIXED_MODULES)
async def test_reported_and_other_module_schemas_never_control_alias(client, registry, writes, monkeypatch, module):
    other = next(item for item in FIXED_MODULES if item != module)
    registry.replace_extension_entries("neutral", [
        ActiveModuleSchema(module, "reported", "neutral", "r1", {"type": "array"}),
        ActiveModuleSchema(other, "configuration", "neutral", "c1", {"type": "array"}),
    ], [])
    assert (await client.post(f"/device/twin/config/{module}", content="[]")).status_code == 422
    assert (await client.post(f"/device/twin/config/{module}", json={})).status_code == 200

    async def read(*args):
        return {"unvalidated": None}

    monkeypatch.setattr(router, "_get_module_twin_config", read)
    assert (await client.get(f"/device/twin/config/{module}")).json() == {"unvalidated": None}


@pytest.mark.parametrize("module", FIXED_MODULES)
@pytest.mark.parametrize("raw", [
    b'{"x":' + b'[' * 900 + b'"leaf"' + b']' * 900 + b'}',
    b'{"\\ud800":null,"x":["\\udfff",{"\\ud800":"\\ud800"}]}',
    b'{"_sa_keep":null,"nested":{"_sa_name":"x"},"items":[{"_sa_item":null}]}',
])
async def test_wp3_deep_surrogate_and_sa_transport_on_actual_helper_tuple(client, registry, writes, module, raw):
    response = await client.post(f"/Device-Exact/twin/config/{module}", content=raw)
    payload = json.loads(raw)
    assert response.status_code == 200 and response.json() == [payload, 200]
    assert json.loads(writes.uploads[0][0]) == payload
    assert writes.uploads[0][0].encode("ascii")
    assert_targets(writes, "Device-Exact", module)


@pytest.mark.parametrize("module", FIXED_MODULES)
async def test_actual_helper_status_is_not_synthesized(client, registry, monkeypatch, module):
    async def helper(device, selected, payload):
        assert (device, selected) == ("Device-Exact", module)
        assert json.loads(payload.model_dump_json(exclude_none=True)) == {"_sa_keep": None}
        return payload, 207

    monkeypatch.setattr(router, "_post_module_twin_config", helper)
    response = await client.post(f"/Device-Exact/twin/config/{module}", json={"_sa_keep": None})
    assert response.status_code == 200 and response.json() == [{"_sa_keep": None}, 207]


@pytest.mark.parametrize("module", FIXED_MODULES)
async def test_read_null_and_helper_errors_are_not_fabricated(client, registry, monkeypatch, module):
    async def missing(*args):
        return None

    monkeypatch.setattr(router, "_get_module_twin_config", missing)
    assert (await client.get(f"/device/twin/config/{module}")).json() is None

    async def failed(*args):
        raise HTTPException(404, "synthetic missing module")

    monkeypatch.setattr(router, "_get_module_twin_config", failed)
    response = await client.get(f"/device/twin/config/{module}")
    assert response.status_code == 404 and response.json() == {"detail": "synthetic missing module"}


async def test_fixed_ids_with_same_version_have_independent_configuration_and_reported_validators(client, registry, writes, monkeypatch):
    entries = []
    for module, required in zip(MODULES, ["first", "second", "neutral"], strict=True):
        entries.append(ActiveModuleSchema(module, "configuration", "neutral", "same-version", {
            "type": "object", "required": [required], "properties": {required: {"type": "integer"}},
        }))
        entries.append(ActiveModuleSchema(module, "reported", "neutral", "same-version", {"type": "array"}))
    registry.replace_extension_entries("neutral", entries, [])
    stored = {}

    async def read(device, module):
        return stored[module]

    monkeypatch.setattr(router, "_get_module_twin_config", read)
    for module, required in zip(MODULES, ["first", "second", "neutral"], strict=True):
        stored[module] = {required: 2}
        assert (await client.post(f"/device/twin/config/{module}", json=stored[module])).status_code == 200
        assert (await client.get(f"/device/twin/config/{module}")).json() == stored[module]
        assert (await client.post(f"/device/twin/config/{module}", json={"unrelated": 2})).status_code == 422
        assert (module, "configuration", "same-version") in body_validation._module_validator_cache
        assert (module, "reported", "same-version") not in body_validation._module_validator_cache
    assert len(writes.uploads) == 3
