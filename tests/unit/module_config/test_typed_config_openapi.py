"""Exact-ID fixed-operation publication in router.py's copy-on-generation hook."""
import copy
import json

import pytest
from fastapi import FastAPI, Security

from auth import ensure_user_exists
from authorization.abac_permission_check import ABACPermissionCheck
from routers.module_config import router
from routers.module_config.registry import ActiveModuleSchema, ModuleSchemaRegistry

MODULES = ["seal-module-opcua-client", "seal-app-net-discover"]
HANDLERS = {
    MODULES[0]: {"get": "get_opcua_client_twin_config", "post": "post_module_twin_config_opcua"},
    MODULES[1]: {"get": "get_net_discover_twin_config", "post": "post_module_twin_config_discover"},
}


@pytest.fixture
def registry(monkeypatch):
    fresh = ModuleSchemaRegistry()
    monkeypatch.setattr(router, "module_schema_registry", fresh)
    return fresh


def _app():
    app = FastAPI(dependencies=[Security(ensure_user_exists)])
    app.include_router(router.module_config)
    return app


def _path(module):
    return f"/{{device}}/twin/config/{module}"


def _snapshot(module, purpose="configuration", version="v1", title="Config"):
    return ActiveModuleSchema(module, purpose, "neutral", version, {
        "$ref": "#/components/schemas/Config",
        "components": {"schemas": {
            "Config": {"title": title, "type": "object", "properties": {"nested": {"$ref": "#/components/schemas/Nested"}}},
            "Nested": {"type": ["integer", "null"], "minimum": 2},
        }},
    })


def _get(operation):
    return operation["responses"]["200"]["content"]["application/json"]["schema"]


def _post(operation):
    assert operation["requestBody"]["required"] is True
    return operation["requestBody"]["content"]["application/json"]["schema"]


@pytest.mark.parametrize("module", MODULES)
def test_retained_operation_ids_deprecation_security_abac_and_route_order(registry, module):
    app = _app()
    document = app.openapi()
    generic = document["paths"][router._CONFIG_OPERATION_PATH]
    for method, name in HANDLERS[module].items():
        operation = document["paths"][_path(module)][method]
        assert operation["deprecated"] is True
        assert operation["operationId"] == f"{name}__device__twin_config_{module.replace('-', '_')}_{method}"
        assert operation["tags"] == ["Module Configuration"]
        assert operation["security"] == generic[method]["security"] and operation["security"]
        assert [parameter["name"] for parameter in operation["parameters"]] == ["device"]
        assert "503" in operation["description"] and "unavailable" in operation["description"]
        routes = [route for route in router.module_config.routes if method.upper() in route.methods]
        fixed = next(route for route in routes if route.path == _path(module))
        catch_all = next(route for route in routes if route.path == router._CONFIG_OPERATION_PATH)
        assert routes.index(fixed) < routes.index(catch_all)
        assert fixed.endpoint.__name__ == name
        checks = [dep.call for dep in fixed.dependant.dependencies if isinstance(dep.call, ABACPermissionCheck)]
        assert len(checks) == 1
        assert vars(checks[0]) == vars(next(dep.call for dep in catch_all.dependant.dependencies if isinstance(dep.call, ABACPermissionCheck)))
    # No API-owned protocol models remain, including FastAPI's Input/Output variants.
    assert not any(name.startswith(("OpcuaClientModuleConfigV1", "NetworkDiscoverModuleConfigV1", "GetNetDiscoverModuleConfigV1"))
                   for name in document["components"]["schemas"])


def test_fixed_schemas_use_only_exact_configuration_and_share_namespaced_nested_components(registry):
    entries = [_snapshot(module, title=f"Config-{module}") for module in MODULES]
    entries += [_snapshot(module, "reported", title=f"Reported-{module}") for module in MODULES]
    entries.append(_snapshot("other-module", title="Other"))
    registry.replace_extension_entries("neutral", entries, [])
    app = _app()
    base = copy.deepcopy(app.openapi())
    router.install_module_config_openapi(app)
    document = app.openapi()
    assert document == app.openapi() and app.openapi_schema == base
    for module in MODULES:
        operations = document["paths"][_path(module)]
        root, components = router._snapshot_alternative(next(entry for entry in entries if entry.module_id == module and entry.purpose == "configuration"))
        assert _get(operations["get"]) == {"anyOf": [root, {"type": "null"}]}
        assert _post(operations["post"]) == root
        assert components.items() <= document["components"]["schemas"].items()
        resolved = document["components"]["schemas"][root["$ref"].rsplit("/", 1)[1]]
        nested = document["components"]["schemas"][resolved["properties"]["nested"]["$ref"].rsplit("/", 1)[1]]
        assert nested == {"type": ["integer", "null"], "minimum": 2}
        generic = document["paths"][router._CONFIG_OPERATION_PATH]
        assert root in _get(generic["get"])["anyOf"] and root in _post(generic["post"])["anyOf"]
        reported = document["paths"][router._REPORTED_OPERATION_PATH]
        assert root not in _get(reported["get"])["anyOf"]
        for method in ("get", "post"):
            for key in ("operationId", "tags", "deprecated", "security", "parameters"):
                assert operations[method][key] == base["paths"][_path(module)][method][key]
    assert len([name for name in document["components"]["schemas"] if name.startswith("Module_configuration_")]) == 6
    assert "Module_configuration_" not in json.dumps(app.openapi_schema)


@pytest.mark.parametrize("module", MODULES)
def test_inactive_reported_only_unavailable_replace_and_removal_use_fallback_without_stale_refs(registry, module):
    app = _app()
    router.install_module_config_openapi(app)
    core = app.openapi()
    operations = core["paths"][_path(module)]
    generic = core["paths"][router._CONFIG_OPERATION_PATH]
    assert _get(operations["get"])["anyOf"] == _get(generic["get"])["anyOf"]
    assert _post(operations["post"]) == {"anyOf": [{"type": "object"}]}
    registry.replace_extension_entries("neutral", [_snapshot(module)], [])
    before = app.openapi()
    registry.replace_extension_entries("neutral", [_snapshot(module, version="v2", title="Replacement")], [])
    replaced = app.openapi()
    assert "Replacement" in json.dumps(replaced) and replaced != before
    assert all(component.get("title") != "Config" for component in replaced["components"]["schemas"].values())
    registry.replace_extension_entries("neutral", [_snapshot(module, "reported", title="ReportedOnly")], [])
    reported_only = app.openapi()["paths"][_path(module)]
    assert _get(reported_only["get"]) == _get(operations["get"])
    assert _post(reported_only["post"]) == _post(operations["post"])
    registry.replace_extension_entries("neutral", [], [module])
    assert app.openapi() == core
    registry.replace_extension_entries("neutral", [], [])
    assert app.openapi() == core
