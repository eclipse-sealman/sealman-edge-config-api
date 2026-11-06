from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.merge import merge_type_fields
from db.models.device import Device
from db.models.device_type import DeviceType
from exceptions import ValidationError


def effective_type_fields(types: list[DeviceType]) -> dict[str, dict[str, Any]]:
    default_fields = next((dt.fields or {} for dt in types if dt.type_id == "default"), {})
    return {
        dt.type_id: (
            (dt.fields or {}) if dt.type_id == "default"
            else merge_type_fields(default_fields, dt.fields or {})
        )
        for dt in types
    }


async def backfill_device_defaults(
    session: AsyncSession,
    fields_by_type: dict[str, dict[str, Any]],
) -> int:
    types_with_defaults = [
        type_id for type_id, fields in fields_by_type.items()
        if any(definition.get("default") is not None for definition in fields.values())
    ]
    if not types_with_defaults:
        return 0

    rows = (await session.execute(
        select(Device.device_id, func.jsonb_typeof(Device.device_data).label("metadata_type"))
        .where(Device.type_id.in_(types_with_defaults))
        .order_by(Device.device_id)
        .with_for_update()
    )).all()
    for row in rows:
        if row.metadata_type != "object":
            raise ValidationError(
                f"Device '{row.device_id}' has non-object metadata "
                f"({row.metadata_type or 'SQL NULL'}); defaults backfill aborted",
                422,
            )

    populated = 0
    for type_id in types_with_defaults:
        fields = fields_by_type[type_id]
        for key, definition in fields.items():
            if definition.get("default") is None:
                continue
            result = await session.execute(
                update(Device)
                .where(Device.type_id == type_id, ~Device.device_data.has_key(key))
                .values(device_data=Device.device_data.op("||")({key: definition["default"]}))
                .returning(Device.device_id)
                .execution_options(synchronize_session=False)
            )
            populated += len(result.scalars().all())
    return populated
