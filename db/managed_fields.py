"""Rules for endpoint-type fields managed by an extension (`managed_by`: {field_key: extension}).

A managed field's definition comes from the extension's manifest. It cannot carry a role: roles
come from admin fields only. Once a field stops being managed it is a normal field again.
"""
import copy
from typing import Any, Dict, Mapping

from exceptions import ValidationError

# The only definition attributes an admin may change on a managed field, per type.
ADMIN_EDITABLE_ATTRIBUTES = ("label", "description", "show_in_list")


def reject_roles_on_managed_fields(
    mapping: Mapping[str, str], managed_by: Mapping[str, str]
) -> None:
    """Raises ValidationError (422) for the first mapping key that is a managed field."""
    for field_key in mapping:
        extension = managed_by.get(field_key)
        if extension is not None:
            raise ValidationError(
                f"'{field_key}' is managed by extension '{extension}' and cannot have a role", 422
            )


def merge_changed_definition(
    type_def: Dict[str, Any], old_def: Dict[str, Any], new_def: Dict[str, Any]
) -> Dict[str, Any]:
    """The definition a type gets when the manifest changes a field from `old_def` to `new_def`.

    The new manifest definition wins, except for an admin-editable attribute the admin changed
    on this type (it differs from `old_def`): that keeps the type's value, and is dropped when
    the admin removed it. Returns a deep copy: no nested value is shared with the inputs.
    """
    merged = copy.deepcopy(new_def)
    for attribute in ADMIN_EDITABLE_ATTRIBUTES:
        type_value = type_def.get(attribute)
        if type_value != old_def.get(attribute):
            if type_value is None:
                merged.pop(attribute, None)
            else:
                merged[attribute] = copy.deepcopy(type_value)
    return merged
