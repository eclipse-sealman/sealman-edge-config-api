from typing import Any, Dict, Optional, Protocol, runtime_checkable
from uuid import uuid4

from exceptions import ValidationError
from field_validation import validate_value


@runtime_checkable
class TypedEntity(Protocol):
    fields: Dict[str, Any]
    label: str
    description: Optional[str]


def resolve_fields(
    instance_data: Dict[str, Any],
    type_fields: Dict[str, Any],
    *,
    apply_defaults: bool = True,
) -> Dict[str, Any]:
    resolved: Dict[str, Any] = {}
    for field_key, field_def in type_fields.items():
        value = instance_data.get(field_key)
        if value is None and apply_defaults:
            value = field_def.get("default")
        if field_def.get("type") == "list" and isinstance(value, list) and apply_defaults:
            value = _with_item_defaults(value, field_def.get("item_fields") or {})
        resolved[field_key] = {
            "value": value,
            "field": field_def,
        }
    for data_key, data_value in instance_data.items():
        if data_key not in type_fields:
            resolved[data_key] = {"value": data_value, "field": None}
    return resolved


def _with_item_defaults(items: list, item_fields: Dict[str, Any]) -> list:
    defaults = {
        key: definition["default"]
        for key, definition in item_fields.items()
        if definition.get("default") is not None
    }
    return [{**defaults, **item} if isinstance(item, dict) else item for item in items]


def materialize_defaults(
    instance_data: Dict[str, Any],
    type_fields: Dict[str, Any],
) -> Dict[str, Any]:
    defaults = {
        key: definition["default"]
        for key, definition in type_fields.items()
        if definition.get("default") is not None
    }
    result = {**defaults, **instance_data}
    for key, definition in type_fields.items():
        value = result.get(key)
        if definition.get("type") == "list" and isinstance(value, list):
            result[key] = [
                materialize_defaults(item, definition.get("item_fields") or {})
                if isinstance(item, dict) else item
                for item in value
            ]
    return result


def newly_defaulted_fields(
    previous: Dict[str, Any], current: Dict[str, Any]
) -> Dict[str, Any]:
    result = {}
    for key, definition in current.items():
        old = previous.get(key) or {}
        if definition.get("type") == "list":
            columns = newly_defaulted_fields(
                old.get("item_fields") or {}, definition.get("item_fields") or {}
            )
            if columns:
                result[key] = {**definition, "item_fields": columns}
        elif definition.get("default") is not None and old.get("default") is None:
            result[key] = definition
    return result


def merge_type_fields(
    base_fields: Dict[str, Any],
    override_fields: Dict[str, Any],
) -> Dict[str, Any]:
    """Merges two field-definition dicts, with `override_fields`' definitions taking precedence
    on key collisions. Used to make a device type inherit another type's field schema (e.g. the
    default type's required fields) while still letting it redefine any of those keys itself.
    """
    return {**base_fields, **override_fields}


def validate_instance_data(
    data: Dict[str, Any],
    type_fields: Dict[str, Any],
) -> None:
    """Validate submitted endpoint_data/service_data against its type's field definitions.

    Raises ValidationError (422) on the first violation: missing required field,
    wrong data type, failed validation rule, or a value outside `options`.
    """
    for field_key, field_def in type_fields.items():
        value = data.get(field_key)
        if value is None:
            if field_def.get("required"):
                raise ValidationError(f"'{field_key}' is required", 422)
            continue
        if field_def.get("type") == "list":
            _validate_list(field_key, value, field_def)
            continue
        try:
            validate_value(field_key, value, field_def)
        except ValueError as exc:
            raise ValidationError(str(exc), 422)


def _validate_list(field_key: str, value: Any, field_def: Dict[str, Any]) -> None:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValidationError(f"'{field_key}' must be a list of objects", 422)
    for item in value:
        for sub_key, sub_def in (field_def.get("item_fields") or {}).items():
            sub_value = item.get(sub_key)
            if sub_value is None:
                if sub_def.get("required"):
                    raise ValidationError(f"'{field_key}.{sub_key}' is required", 422)
                continue
            try:
                validate_value(f"{field_key}.{sub_key}", sub_value, sub_def)
            except ValueError as exc:
                raise ValidationError(str(exc), 422)


def normalize_list_values(
    data: Dict[str, Any],
    stored_data: Dict[str, Any],
    type_fields: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a copy of `data` where every list field value is checked structurally, an empty
    list is dropped, and each item without an `id` gets a generated one. An `id` must belong to
    the list stored under the same key and may appear only once.
    """
    result = dict(data)
    for field_key, field_def in type_fields.items():
        if field_def.get("type") != "list" or result.get(field_key) is None:
            continue
        items = result[field_key]
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValidationError(f"'{field_key}' must be a list of objects", 422)
        if not items:
            del result[field_key]
            continue
        stored = stored_data.get(field_key)
        known_ids = (
            {item["id"] for item in stored if isinstance(item, dict) and isinstance(item.get("id"), str)}
            if isinstance(stored, list)
            else set()
        )
        seen = set()
        normalized = []
        for item in items:
            item_id = item.get("id")
            if item_id is None:
                item = {
                    **materialize_defaults(item, field_def.get("item_fields") or {}),
                    "id": str(uuid4()),
                }
            elif not isinstance(item_id, str) or item_id not in known_ids:
                raise ValidationError(f"'{field_key}' has an item with an unknown id", 422)
            elif item_id in seen:
                raise ValidationError(f"'{field_key}' has an id more than once", 422)
            seen.add(item["id"])
            normalized.append(item)
        result[field_key] = normalized
    return result


def reject_non_changeable_updates(
    current_data: Dict[str, Any],
    data_patch: Dict[str, Any],
    type_fields: Dict[str, Any],
) -> None:
    """Raises ValidationError if the patch tries to set a different value for a field whose
    definition has `changeable: false` and which already has a value (e.g. a scanned IP
    address that must not be hand-edited once discovered).
    """
    for field_key, new_value in data_patch.items():
        field_def = type_fields.get(field_key)
        if field_def is None or field_def.get("changeable", True):
            continue
        current_value = current_data.get(field_key)
        if current_value is not None and new_value != current_value:
            raise ValidationError(f"'{field_key}' is not changeable once set", 422)


_LIST_KEPT_ON_TYPE_CHANGE = ("label", "description", "required", "show_in_list")


def with_item_order(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Return `fields` with `item_order` set to the sub-field keys of every list field."""
    result: Dict[str, Any] = {}
    for key, field in fields.items():
        if isinstance(field, dict) and field.get("type") == "list" and field.get("item_fields"):
            field = {**field, "item_order": list(field["item_fields"])}
        result[key] = field
    return result


def _patch_list_field(existing: Dict[str, Any], field_patch: Dict[str, Any]) -> Dict[str, Any]:
    new_type = field_patch.get("type")
    if new_type is not None and new_type != existing.get("type") and "list" in (new_type, existing.get("type")):
        existing = {k: v for k, v in existing.items() if k in _LIST_KEPT_ON_TYPE_CHANGE}
    stored_items = existing.get("item_fields") or {}
    order = [k for k in (existing.get("item_order") or list(stored_items)) if k in stored_items]
    items = dict(stored_items)
    for sub_key, sub_def in (field_patch.get("item_fields") or {}).items():
        if sub_def is None:
            items.pop(sub_key, None)
            if sub_key in order:
                order.remove(sub_key)
        else:
            items[sub_key] = sub_def
            if sub_key not in order:
                order.append(sub_key)
    existing.update({k: v for k, v in field_patch.items() if v is not None and k != "item_fields"})
    if "item_fields" in field_patch or items:
        existing["item_fields"] = items
        existing["item_order"] = order
    return existing


def patch_fields(
    current_fields: Dict[str, Any],
    field_patches: Dict[str, Any],
    *,
    clear_defaults: bool = False,
) -> Dict[str, Any]:
    result = dict(current_fields)
    for field_key, field_patch in field_patches.items():
        if field_patch is None:
            result.pop(field_key, None)
        else:
            existing = dict(result.get(field_key, {}))
            if "list" in (existing.get("type"), field_patch.get("type")) or field_patch.get("item_fields") is not None:
                result[field_key] = _patch_list_field(existing, field_patch)
                continue
            existing.update({k: v for k, v in field_patch.items() if v is not None})
            if clear_defaults and "default" in field_patch and field_patch["default"] is None:
                existing.pop("default", None)
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
            "mapping": entity_type.mapping or {},
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
                apply_defaults=False,
            ),
            # The values actually stored, without type defaults substituted (internal use, e.g.
            # discovery; not part of the API responses).
            f"raw_{data_field}": dict(getattr(entity, data_field) or {}),
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
        }
        if self._PARENT_ID_FIELD:
            result[self._PARENT_ID_FIELD] = getattr(entity, self._PARENT_ID_FIELD)
        return result
