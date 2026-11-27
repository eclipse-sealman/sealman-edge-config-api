from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.merge import materialize_defaults
from db.models.endpoint import Endpoint
from db.models.service import Service
from exceptions import ValidationError
from routers.schemas import FieldDefinition


def validate_fields(fields: dict[str, Any]) -> None:
    for key, definition in fields.items():
        try:
            FieldDefinition.model_validate(definition)
        except PydanticValidationError as exc:
            raise ValidationError(f"Invalid field '{key}': {exc}", 422) from exc


def _filled_count(data: dict[str, Any], fields: dict[str, Any], location: str) -> int:
    count = 0
    for key, definition in fields.items():
        if definition.get("type") == "list":
            items = data.get(key)
            if items is None:
                continue
            if not isinstance(items, list):
                raise ValidationError(f"{location}.{key} must be a list of objects; backfill aborted", 422)
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    raise ValidationError(
                        f"{location}.{key}[{index}] must be an object; backfill aborted", 422
                    )
                count += _filled_count(item, definition.get("item_fields") or {}, location)
        elif definition.get("default") is not None and key not in data:
            count += 1
    return count


async def backfill_asset_defaults(
    session: AsyncSession,
    model: type[Endpoint] | type[Service],
    fields_by_type: dict[str, dict[str, Any]],
) -> int:
    """Fill missing scalar keys/cells under caller-held type locks; never commit."""
    fields_by_type = {key: fields for key, fields in fields_by_type.items() if fields}
    if not fields_by_type:
        return 0
    if model is Endpoint:
        data_key, id_column = "endpoint_data", Endpoint.endpoint_id
    else:
        data_key, id_column = "service_data", Service.service_id
    rows = (await session.execute(
        select(model)
        .where(model.type_id.in_(fields_by_type))
        .order_by(id_column)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).scalars().all()
    populated = 0
    for row in rows:
        data = getattr(row, data_key)
        location = f"{model.__name__} '{getattr(row, id_column.key)}'"
        if not isinstance(data, dict):
            raise ValidationError(f"{location} has non-object data; backfill aborted", 422)
        fields = fields_by_type[row.type_id]
        count = _filled_count(data, fields, location)
        if count:
            setattr(row, data_key, materialize_defaults(data, fields))
            populated += count
    await session.flush()
    return populated
