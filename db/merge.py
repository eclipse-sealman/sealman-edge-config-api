from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class TypedEntity(Protocol):
    fields: Dict[str, Any]
    label: str
    description: Optional[str]


def resolve_fields(
    instance_data: Dict[str, Any],
    type_fields: Dict[str, Any],
) -> Dict[str, Any]:
    resolved: Dict[str, Any] = {}
    for field_key, field_def in type_fields.items():
        resolved[field_key] = {
            "value": instance_data.get(field_key),
            "field": field_def,
        }
    for data_key, data_value in instance_data.items():
        if data_key not in type_fields:
            resolved[data_key] = {"value": data_value, "field": None}
    return resolved


def patch_fields(
    current_fields: Dict[str, Any],
    field_patches: Dict[str, Any],
) -> Dict[str, Any]:
    result = dict(current_fields)
    for field_key, field_patch in field_patches.items():
        if field_patch is None:
            result.pop(field_key, None)
        else:
            existing = dict(result.get(field_key, {}))
            existing.update({k: v for k, v in field_patch.items() if v is not None})
            result[field_key] = existing
    return result


def patch_data(
    current_data: Dict[str, Any],
    data_patch: Dict[str, Any],
) -> Dict[str, Any]:
    result = dict(current_data)
    for key, value in data_patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result


class BlueprintResolver:
    _ENTITY_ID_FIELD: str
    _PARENT_ID_FIELD: Optional[str] = None

    def _serialize_type(self, entity_type: Any) -> Dict[str, Any]:
        return {
            "type_id": entity_type.type_id,
            "label": entity_type.label,
            "description": entity_type.description,
            "fields": entity_type.fields or {},
            "created_at": entity_type.created_at,
            "updated_at": entity_type.updated_at,
        }

    def _serialize_resolved(self, entity: Any, entity_type: Any) -> Dict[str, Any]:
        data_field = self._ENTITY_ID_FIELD.replace("_id", "_data")
        result = {
            self._ENTITY_ID_FIELD: getattr(entity, self._ENTITY_ID_FIELD),
            "type_id": entity.type_id,
            "type_label": entity_type.label,
            "type_description": entity_type.description,
            data_field: resolve_fields(
                getattr(entity, data_field) or {},
                entity_type.fields or {},
            ),
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
        }
        if self._PARENT_ID_FIELD:
            result[self._PARENT_ID_FIELD] = getattr(entity, self._PARENT_ID_FIELD)
        return result
