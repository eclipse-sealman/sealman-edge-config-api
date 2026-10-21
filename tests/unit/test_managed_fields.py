import pytest

from db.managed_fields import (
    ADMIN_EDITABLE_ATTRIBUTES,
    merge_changed_definition,
    reject_roles_on_managed_fields,
)
from exceptions import ValidationError
from routers.endpoint.managed_field_lock import check_managed_field_patch


class TestRejectRolesOnManagedFields:
    def test_role_on_managed_field_is_rejected_with_message(self):
        with pytest.raises(ValidationError) as exc_info:
            reject_roles_on_managed_fields(
                {"title": "name", "serial_number": "ip"}, {"serial_number": "inventory"}
            )
        assert exc_info.value.status_code == 422
        assert str(exc_info.value) == (
            "'serial_number' is managed by extension 'inventory' and cannot have a role"
        )

    def test_role_on_unmanaged_field_is_allowed(self):
        reject_roles_on_managed_fields({"title": "name"}, {"serial_number": "inventory"})

    def test_empty_mapping_is_allowed(self):
        reject_roles_on_managed_fields({}, {"serial_number": "inventory"})


SERIAL = {
    "type": "string",
    "label": "Serial",
    "description": "From the manifest",
    "required": False,
    "ui": "input",
    "changeable": True,
    "show_in_list": True,
}
MANAGED = {"serial_number": "inventory"}
LOCK_MESSAGE = (
    "'serial_number' is managed by extension 'inventory'; "
    "only label, description and show_in_list can change"
)


def _patched(**changes):
    return {**SERIAL, **changes}


class TestCheckManagedFieldPatch:
    def test_admin_editable_attributes(self):
        assert ADMIN_EDITABLE_ATTRIBUTES == ("label", "description", "show_in_list")

    def test_label_description_show_in_list_may_change(self):
        check_managed_field_patch(
            {"serial_number": SERIAL},
            {"serial_number": _patched(label="Mine", description="Ours", show_in_list=False)},
            MANAGED,
        )

    def test_type_change_is_rejected_with_message(self):
        with pytest.raises(ValidationError) as exc_info:
            check_managed_field_patch(
                {"serial_number": SERIAL},
                {"serial_number": _patched(type="integer", ui="number")},
                MANAGED,
            )
        assert exc_info.value.status_code == 422
        assert str(exc_info.value) == LOCK_MESSAGE

    def test_required_change_is_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            check_managed_field_patch(
                {"serial_number": SERIAL}, {"serial_number": _patched(required=True)}, MANAGED
            )
        assert str(exc_info.value) == LOCK_MESSAGE

    def test_invalid_merged_definition_is_a_locked_change(self):
        # A string field cannot get a numeric bound; only a locked attribute can make it invalid.
        with pytest.raises(ValidationError) as exc_info:
            check_managed_field_patch(
                {"serial_number": SERIAL},
                {"serial_number": _patched(validation={"minimum": 1})},
                MANAGED,
            )
        assert str(exc_info.value) == LOCK_MESSAGE

    def test_unmanaged_fields_are_not_checked(self):
        check_managed_field_patch(
            {"note": SERIAL}, {"note": _patched(type="integer", ui="number")}, MANAGED
        )

    def test_removed_managed_field_is_not_checked(self):
        check_managed_field_patch({"serial_number": SERIAL}, {}, MANAGED)

    def test_raw_nulls_in_validation_are_not_a_change(self):
        current = _patched(
            validation={"pattern": "^A", "exclusive_minimum": False, "exclusive_maximum": False}
        )
        merged = _patched(
            validation={
                "pattern": "^A",
                "min_length": None,
                "max_length": None,
                "minimum": None,
                "maximum": None,
                "exclusive_minimum": False,
                "exclusive_maximum": False,
                "multiple_of": None,
            },
            options=None,
            default=None,
        )
        check_managed_field_patch({"serial_number": current}, {"serial_number": merged}, MANAGED)


class TestMergeChangedDefinition:
    def test_merge_keeps_admin_label_and_follows_unchanged_ones(self):
        old_def = {"type": "string", "label": "A", "description": "d"}
        type_def = {"type": "string", "label": "Mine", "description": "d"}
        new_def = {"type": "string", "label": "B", "description": "e", "ui": "textarea"}

        merged = merge_changed_definition(type_def, old_def, new_def)

        assert merged == {"type": "string", "label": "Mine", "description": "e", "ui": "textarea"}

    def test_merge_keeps_admin_show_in_list(self):
        old_def = {"type": "string", "label": "A", "show_in_list": True}
        type_def = {"type": "string", "label": "A", "show_in_list": False}
        new_def = {"type": "string", "label": "A", "show_in_list": True}

        assert merge_changed_definition(type_def, old_def, new_def)["show_in_list"] is False

    def test_merge_drops_attribute_the_admin_cleared(self):
        old_def = {"type": "string", "label": "A", "description": "d"}
        type_def = {"type": "string", "label": "A"}
        new_def = {"type": "string", "label": "A", "description": "e"}

        assert merge_changed_definition(type_def, old_def, new_def) == {"type": "string", "label": "A"}

    def test_merge_does_not_mutate_inputs(self):
        old_def = {"type": "string", "label": "A"}
        type_def = {"type": "string", "label": "Mine"}
        new_def = {"type": "string", "label": "B"}

        merge_changed_definition(type_def, old_def, new_def)

        assert new_def == {"type": "string", "label": "B"}
        assert type_def == {"type": "string", "label": "Mine"}

    def test_merge_results_do_not_share_nested_dicts(self):
        old_def = {"type": "string", "label": "A", "validation": {"max_length": 10}, "options": ["x"]}
        type_def = {"type": "string", "label": "Mine", "validation": {"max_length": 10}, "options": ["x"]}
        new_def = {"type": "string", "label": "B", "validation": {"max_length": 20}, "options": ["x", "y"]}

        first = merge_changed_definition(type_def, old_def, new_def)
        second = merge_changed_definition(type_def, old_def, new_def)
        first["validation"]["max_length"] = 99
        first["options"].append("z")

        assert second["validation"] == {"max_length": 20}
        assert second["options"] == ["x", "y"]
        assert new_def["validation"] == {"max_length": 20}
        assert new_def["options"] == ["x", "y"]
