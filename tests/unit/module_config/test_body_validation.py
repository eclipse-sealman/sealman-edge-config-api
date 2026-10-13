"""Unit coverage for the keyed module-snapshot validator (extensions/body_validation.py)."""

from extensions import body_validation

_SCHEMA = {"type": "object", "required": ["interval"], "properties": {"interval": {"type": "integer"}}}


def test_valid_payload_has_no_errors():
    assert body_validation.validate_module_snapshot("widget-module-1", "configuration", "v1", _SCHEMA, {"interval": 5}) == []


def test_every_failing_field_is_reported_with_its_location_and_rule():
    errors = body_validation.validate_module_snapshot("widget-module-1", "configuration", "v1", _SCHEMA, {"interval": "x"})

    assert errors == [{"loc": ["interval"], "rule": "type", "msg": "must be of type integer"}]


def test_validator_is_cached_per_module_purpose_and_version():
    body_validation.validate_module_snapshot("widget-module-3", "reported", "v1", _SCHEMA, {})
    first = body_validation._module_validator_cache[("widget-module-3", "reported", "v1")]

    body_validation.validate_module_snapshot("widget-module-3", "reported", "v1", _SCHEMA, {"interval": 1})

    assert body_validation._module_validator_cache[("widget-module-3", "reported", "v1")] is first


def test_a_new_version_is_validated_by_its_own_schema():
    strict = {"type": "object", "required": ["unit"]}
    loose = {"type": "object"}

    assert body_validation.validate_module_snapshot("widget-module-4", "configuration", "v1", loose, {}) == []
    errors = body_validation.validate_module_snapshot("widget-module-4", "configuration", "v2", strict, {})

    assert [error["rule"] for error in errors] == ["required"]


def test_purpose_keys_do_not_share_a_validator():
    configuration = {"type": "object", "required": ["interval"]}
    reported = {"type": "object", "required": ["diagnostic"]}

    assert body_validation.validate_module_snapshot("widget-module-5", "configuration", "v1", configuration, {"interval": 1}) == []
    assert [error["rule"] for error in body_validation.validate_module_snapshot("widget-module-5", "reported", "v1", reported, {"interval": 1})] == ["required"]


def test_local_component_references_resolve_inside_the_snapshot():
    schema = {
        "$ref": "#/components/schemas/Widget",
        "components": {"schemas": {
            "Widget": {"type": "object", "properties": {"unit": {"$ref": "#/components/schemas/Unit"}}},
            "Unit": {"type": "string", "enum": ["ms", "s"]},
        }},
    }

    assert body_validation.validate_module_snapshot("widget-module-6", "configuration", "v1", schema, {"unit": "s"}) == []
    assert [error["rule"] for error in body_validation.validate_module_snapshot("widget-module-6", "configuration", "v1", schema, {"unit": "min"})] == ["enum"]
