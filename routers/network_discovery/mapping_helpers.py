from typing import Any, Dict, Optional


def role_field_key(fields: Dict[str, Any], mapping: Dict[str, Any], role: str) -> Optional[str]:
    for field_key, mapped_role in (mapping or {}).items():
        if isinstance(mapped_role, str) and mapped_role.strip().lower() == role and field_key in fields:
            return field_key
    return None


def resolved_value(resolved_data: Dict[str, Any], field_key: Optional[str]) -> Optional[Any]:
    if field_key is None:
        return None
    entry = resolved_data.get(field_key)
    return entry.get("value") if isinstance(entry, dict) else entry


def stored_role_value(raw_data: Dict[str, Any], field_key: Optional[str]) -> Optional[Any]:
    """The value actually stored for a role field (`raw_endpoint_data` / `raw_service_data`).
    Unlike `resolved_value`, a type default is never substituted: discovery must not treat a
    scan suggestion as the instance's real address. Missing, None and "" count as no value."""
    if field_key is None:
        return None
    value = raw_data.get(field_key)
    if value is None or value == "":
        return None
    return value
