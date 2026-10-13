"""Unit coverage for the generic GET OpenAPI hook (routers/module_config/router.py).

The registry is replaced per test, so these run without a database or any extension lifecycle.
"""

import copy
import json

import pytest
from fastapi import FastAPI

from routers.module_config import router as module_config_router
from routers.module_config.registry import ActiveModuleSchema, ModuleSchemaRegistry
from routers.module_config.router import install_module_config_openapi, module_config

CONFIG_PATH = "/{device}/twin/config/{module}"
REPORTED_PATH = "/{device}/twin/identity/{module}/reported"
CORE_CONFIG = {"$ref": "#/components/schemas/GetModuleTwinResponse"}
CORE_REPORTED = {"$ref": "#/components/schemas/GetModuleTwinIdentityResponse"}


@pytest.fixture
def registry(monkeypatch) -> ModuleSchemaRegistry:
    fresh = ModuleSchemaRegistry()
    monkeypatch.setattr(module_config_router, "module_schema_registry", fresh)
    return fresh


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(module_config)
    return app


def _snapshot(module_id: str, purpose: str, root: dict, components: dict) -> ActiveModuleSchema:
    return ActiveModuleSchema(
        module_id=module_id,
        purpose=purpose,
        extension_name="widgets",
        version="v1",
        schema={**root, "components": {"schemas": components}},
    )


def _alternatives(schema: dict, path: str) -> list:
    return schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["anyOf"]


def test_document_is_unchanged_when_no_module_schema_is_active(registry):
    app = _app()
    before = copy.deepcopy(app.openapi())

    install_module_config_openapi(app)

    assert app.openapi() == before


def test_active_schemas_are_published_as_anyof_alternatives_for_their_purpose(registry):
    registry.replace_extension_entries(
        "widgets",
        [
            _snapshot("widget-module-1", "configuration", {"$ref": "#/components/schemas/Setup"}, {
                "Setup": {"title": "SetupV1", "type": "object", "properties": {"unit": {"$ref": "#/components/schemas/Unit"}}},
                "Unit": {"title": "Unit", "type": "string", "enum": ["ms", "s"]},
            }),
            _snapshot("widget-module-1", "reported", {"$ref": "#/components/schemas/Status"}, {
                "Status": {"title": "StatusV1", "type": "object"},
            }),
        ],
        [],
    )
    app = _app()
    install_module_config_openapi(app)

    document = app.openapi()

    config = _alternatives(document, CONFIG_PATH)
    assert config[0] == CORE_CONFIG and config[-1] == {"type": "null"}
    assert len(config) == 3
    reported = _alternatives(document, REPORTED_PATH)
    assert reported[0] == CORE_REPORTED and reported[-1] == {"type": "null"}
    assert len(reported) == 3
    components = document["components"]["schemas"]
    setup_name = config[1]["$ref"].rsplit("/", 1)[1]
    assert components[setup_name]["title"] == "SetupV1"
    assert components[components[setup_name]["properties"]["unit"]["$ref"].rsplit("/", 1)[1]]["enum"] == ["ms", "s"]
    status_name = reported[1]["$ref"].rsplit("/", 1)[1]
    assert components[status_name]["title"] == "StatusV1"
    assert setup_name not in json.dumps(reported)


def test_repeated_reads_and_cached_document_are_stable(registry):
    registry.replace_extension_entries(
        "widgets", [_snapshot("widget-module-1", "reported", {"type": "object"}, {})], []
    )
    app = _app()
    install_module_config_openapi(app)

    first = app.openapi()
    second = app.openapi()

    assert first == second
    assert "Module_reported_" not in json.dumps(app.openapi_schema)


def test_removed_schema_drops_out_of_the_next_read_and_restores_the_core_document(registry):
    app = _app()
    install_module_config_openapi(app)
    core = copy.deepcopy(app.openapi())

    registry.replace_extension_entries("widgets", [_snapshot("widget-module-1", "reported", {"type": "object"}, {})], [])
    assert len(_alternatives(app.openapi(), REPORTED_PATH)) == 3

    registry.replace_extension_entries("widgets", [], [])

    assert app.openapi() == core


def test_identical_module_roots_from_two_modules_do_not_collide(registry):
    shared_component = {"Item": {"title": "Item", "type": "object"}}
    registry.replace_extension_entries(
        "widgets",
        [
            _snapshot("widget-module-1", "reported", {"$ref": "#/components/schemas/Item"}, shared_component),
            _snapshot("widget-module-2", "reported", {"$ref": "#/components/schemas/Item"}, {"Item": {"title": "Other", "type": "array"}}),
        ],
        [],
    )
    app = _app()
    install_module_config_openapi(app)

    reported = _alternatives(app.openapi(), REPORTED_PATH)
    refs = [alternative["$ref"] for alternative in reported[1:-1]]

    assert len(set(refs)) == 2
    components = app.openapi()["components"]["schemas"]
    assert {components[ref.rsplit("/", 1)[1]]["title"] for ref in refs} == {"Item", "Other"}


def test_configuration_schema_is_never_published_on_the_reported_operation(registry):
    registry.replace_extension_entries(
        "widgets", [_snapshot("widget-module-1", "configuration", {"type": "object", "title": "OnlyConfig"}, {})], []
    )
    app = _app()
    install_module_config_openapi(app)

    document = app.openapi()

    assert document["paths"][REPORTED_PATH]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == CORE_REPORTED
    assert len(_alternatives(document, CONFIG_PATH)) == 3
