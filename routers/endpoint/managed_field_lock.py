"""The lock on extension-managed endpoint-type fields: only label, description and show_in_list
can change on a type; the rest of the definition belongs to the extension."""
from typing import Any, Dict, Mapping, Optional

from pydantic import ValidationError as PydanticValidationError

from db.managed_fields import ADMIN_EDITABLE_ATTRIBUTES
from exceptions import ValidationError
from routers.schemas import normalize_field_definition


def _locked_part(definition: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The normalized definition without the admin-editable attributes, or None if invalid."""
    try:
        normalized = normalize_field_definition(definition)
    except PydanticValidationError:
        return None
    return {k: v for k, v in normalized.items() if k not in ADMIN_EDITABLE_ATTRIBUTES}


def check_managed_field_patch(
    current_fields: Mapping[str, Any],
    merged_fields: Mapping[str, Any],
    managed_by: Mapping[str, str],
) -> None:
    """Raises ValidationError (422) if a type patch changes a locked attribute of a managed field.

    Definitions are compared in normalized form, so a client that sends back the whole
    definition (with null members) is not a change. A merged definition that is no longer valid
    counts as a locked change: changes to the admin-editable attributes cannot make it invalid.
    A removed managed field is not checked.
    """
    for field_key, extension in managed_by.items():
        if field_key not in merged_fields or field_key not in current_fields:
            continue
        merged = _locked_part(merged_fields[field_key])
        if merged is None or merged != _locked_part(current_fields[field_key]):
            raise ValidationError(
                f"'{field_key}' is managed by extension '{extension}'; "
                "only label, description and show_in_list can change",
                422,
            )
