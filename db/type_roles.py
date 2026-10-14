"""Field roles on endpoint and service types.

A type's `mapping` column is `{field_key: role}`. A role marks the field that plays a well-known
part: network discovery reads the `ip` (endpoint) and `port` (service) roles, and the app reads
`name` for an endpoint's title. Nothing is forced; a type may have no roles at all.
"""
from typing import Any, Dict, Mapping, Optional

from exceptions import ValidationError

# role -> the field data type it needs
ENDPOINT_ROLE_TYPES: Dict[str, str] = {"ip": "string", "name": "string"}
SERVICE_ROLE_TYPES: Dict[str, str] = {"port": "integer"}


def validate_type_mapping(
    fields: Mapping[str, Any],
    mapping: Mapping[str, Any],
    role_types: Mapping[str, str],
) -> None:
    """Raises ValidationError (422) on the first violation: an unknown role, a role used on
    more than one field, a role mapped to a key that isn't a field on the type, or a field whose
    data type doesn't fit the role."""
    seen_roles: Dict[str, str] = {}
    for field_key, role in mapping.items():
        if role not in role_types:
            raise ValidationError(
                f"Unknown role '{role}' on field '{field_key}' (allowed: {sorted(role_types)})", 422
            )
        if role in seen_roles:
            raise ValidationError(
                f"Role '{role}' is assigned to more than one field "
                f"('{seen_roles[role]}', '{field_key}')",
                422,
            )
        seen_roles[role] = field_key
        field_def = fields.get(field_key)
        if field_def is None:
            raise ValidationError(
                f"Role '{role}' is mapped to '{field_key}', which is not a field on this type", 422
            )
        expected_type = role_types[role]
        actual_type = field_def.get("type")
        if actual_type != expected_type:
            raise ValidationError(
                f"Role '{role}' needs a field of type '{expected_type}', "
                f"but '{field_key}' is '{actual_type}'",
                422,
            )


def drop_roles_of_missing_fields(
    mapping: Optional[Mapping[str, Any]], fields: Mapping[str, Any]
) -> Dict[str, Any]:
    """Keeps only the roles whose field is still on the type (a removed field takes its role
    with it)."""
    return {field_key: role for field_key, role in (mapping or {}).items() if field_key in fields}
