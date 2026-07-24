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
