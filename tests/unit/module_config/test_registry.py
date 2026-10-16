"""Unit coverage for the in-process active module-schema registry (routers/module_config/registry.py)."""

import pytest

from routers.module_config.registry import ActiveModuleSchema, ModuleSchemaConflictError, ModuleSchemaRegistry


def _schema(extension: str, module_id: str, purpose: str, version: str = "v1") -> ActiveModuleSchema:
    return ActiveModuleSchema(
        module_id=module_id,
        purpose=purpose,
        extension_name=extension,
        version=version,
        schema={"type": "object", "title": f"{module_id}:{purpose}"},
    )


def test_configuration_and_reported_snapshots_are_looked_up_independently():
    registry = ModuleSchemaRegistry()
    configuration = _schema("widgets", "widget-module-1", "configuration")
    reported = _schema("widgets", "widget-module-1", "reported", version="v2")

    registry.replace_extension_entries("widgets", [configuration, reported], [])

    assert registry.get("widget-module-1", "configuration") is configuration
    assert registry.get("widget-module-1", "reported") is reported
    assert registry.get("widget-module-2", "configuration") is None
    assert registry.claim_owner("widget-module-1") == "widgets"
    assert [(entry.module_id, entry.purpose) for entry in registry.list_active()] == [
        ("widget-module-1", "configuration"),
        ("widget-module-1", "reported"),
    ]


def test_replacing_an_extension_drops_entries_it_no_longer_declares():
    registry = ModuleSchemaRegistry()
    registry.replace_extension_entries(
        "widgets",
        [_schema("widgets", "widget-module-1", "configuration"), _schema("widgets", "widget-module-1", "reported")],
        [],
    )

    registry.replace_extension_entries("widgets", [_schema("widgets", "widget-module-1", "configuration", "v2")], [])

    assert registry.get("widget-module-1", "reported") is None
    assert registry.get("widget-module-1", "configuration").version == "v2"


def test_disable_removes_all_entries_and_claims_of_the_extension():
    registry = ModuleSchemaRegistry()
    registry.replace_extension_entries("widgets", [_schema("widgets", "widget-module-1", "configuration")], [])
    registry.replace_extension_entries("gadgets", [_schema("gadgets", "gadget-module-1", "configuration")], [])

    registry.replace_extension_entries("widgets", [], [])

    assert registry.list_active() == [registry.get("gadget-module-1", "configuration")]
    assert registry.claim_owner("widget-module-1") is None


def test_unavailable_module_keeps_its_claim_without_an_active_schema():
    registry = ModuleSchemaRegistry()

    registry.replace_extension_entries("widgets", [], ["widget-module-1"])

    assert registry.is_unavailable("widget-module-1")
    assert registry.get("widget-module-1", "configuration") is None
    assert registry.claim_owner("widget-module-1") == "widgets"

    registry.replace_extension_entries("widgets", [_schema("widgets", "widget-module-1", "configuration")], [])

    assert not registry.is_unavailable("widget-module-1")


def test_claim_held_by_another_extension_is_refused_without_changes():
    registry = ModuleSchemaRegistry()
    registry.replace_extension_entries("widgets", [_schema("widgets", "widget-module-1", "configuration")], [])

    with pytest.raises(ModuleSchemaConflictError, match="already claimed by extension 'widgets'"):
        registry.replace_extension_entries(
            "gadgets",
            [_schema("gadgets", "gadget-module-1", "configuration"), _schema("gadgets", "widget-module-1", "configuration")],
            [],
        )

    assert registry.get("gadget-module-1", "configuration") is None
    assert registry.get("widget-module-1", "configuration").extension_name == "widgets"
