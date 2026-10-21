from unittest.mock import AsyncMock

import pytest

from exceptions import APIError
from extensions.endpoint_fields import validate_endpoint_field_ownership, validate_endpoint_fields
from extensions.registry import _build_detail


def test_none_means_absent():
    assert validate_endpoint_fields(None) is None


def test_normalizes_definitions():
    result = validate_endpoint_fields({"serial_number": {"type": "string", "label": "S"}})
    assert result["serial_number"]["ui"] == "input"
    assert result["serial_number"]["required"] is False
    assert "validation" not in result["serial_number"]
    assert "options" not in result["serial_number"]
    assert "description" not in result["serial_number"]


def test_empty_object_stays_empty():
    assert validate_endpoint_fields({}) == {}


def test_required_true_is_422():
    with pytest.raises(APIError) as exc_info:
        validate_endpoint_fields({"serial_number": {"type": "string", "label": "S", "required": True}})
    assert exc_info.value.status_code == 422
    assert str(exc_info.value) == "Endpoint field 'serial_number' cannot be required"


def test_invalid_definition_is_422():
    with pytest.raises(APIError) as exc_info:
        validate_endpoint_fields({"x": {"type": "string"}})
    assert exc_info.value.status_code == 422
    assert str(exc_info.value).startswith("Invalid endpoint field 'x'")


@pytest.mark.parametrize("definition", ["string", ["string"], 1, None])
def test_non_object_definition_is_422(definition):
    with pytest.raises(APIError) as exc_info:
        validate_endpoint_fields({"serial_number": definition})
    assert exc_info.value.status_code == 422
    assert str(exc_info.value).startswith("Invalid endpoint field 'serial_number'")


@pytest.mark.asyncio
async def test_build_detail_defaults_endpoint_fields():
    extension_repo = AsyncMock()
    extension_repo.get_extension_row.return_value = {
        "name": "inventory",
        "description": "",
        "schema_version": 1,
        "enabled": False,
    }
    extension_repo.list_upstreams.return_value = []
    extension_repo.list_routes.return_value = []
    extension_repo.list_extension_action_specs.return_value = []

    detail = await _build_detail(extension_repo, "inventory")

    assert detail.endpoint_fields == {}


@pytest.mark.asyncio
async def test_ownership_conflict_names_owner():
    extension_repo = AsyncMock()
    extension_repo.list_extensions_rows.return_value = [
        {"name": "other", "endpoint_fields": {"serial_number": {}}},
    ]

    with pytest.raises(APIError) as exc_info:
        await validate_endpoint_field_ownership(extension_repo, "inventory", ["serial_number"])
    assert exc_info.value.status_code == 409
    assert str(exc_info.value) == "Endpoint field 'serial_number' is already declared by extension 'other'"


@pytest.mark.asyncio
async def test_own_keys_are_not_a_conflict():
    extension_repo = AsyncMock()
    extension_repo.list_extensions_rows.return_value = [
        {"name": "inventory", "endpoint_fields": {"serial_number": {}}},
    ]

    await validate_endpoint_field_ownership(extension_repo, "inventory", ["serial_number"])


@pytest.mark.asyncio
async def test_row_without_endpoint_fields_is_not_a_conflict():
    extension_repo = AsyncMock()
    extension_repo.list_extensions_rows.return_value = [{"name": "other"}]

    await validate_endpoint_field_ownership(extension_repo, "inventory", ["serial_number"])


def test_diff_detects_removed_changed_added():
    from extensions.endpoint_fields import EndpointFieldDiff, diff_endpoint_fields

    old = {
        "b_removed": {"type": "string", "label": "R"},
        "a_removed": {"type": "string", "label": "R"},
        "changed": {"type": "string", "label": "C"},
        "same": {"type": "string", "label": "S"},
    }
    new = {
        "changed": {"type": "string", "label": "C", "ui": "textarea"},
        "same": {"type": "string", "label": "S"},
        "z_added": {"type": "number", "label": "Z"},
        "y_added": {"type": "string", "label": "Y"},
    }

    assert diff_endpoint_fields(old, new) == EndpointFieldDiff(
        removed=["a_removed", "b_removed"], changed=["changed"], added=["y_added", "z_added"]
    )


def test_diff_ignores_equal_normalized_definitions():
    from extensions.endpoint_fields import diff_endpoint_fields

    diff = diff_endpoint_fields(
        {"serial_number": {"type": "string", "label": "S"}},
        {"serial_number": {"type": "string", "label": "S", "ui": "input"}},
    )

    assert diff.removed == [] and diff.changed == [] and diff.added == []
