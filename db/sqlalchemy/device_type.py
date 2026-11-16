from typing import Any, Dict, List, Optional
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from db.models.device_type import DeviceType
from db.registry import register_repository
from db.repos.device_type import DeviceTypeRepository
from db.merge import patch_fields, merge_type_fields
from db.sqlalchemy.device_defaults import effective_type_fields, backfill_device_defaults
from exceptions import APIError, ValidationError
from routers.schemas import FieldDefinition

DEFAULT_TYPE_ID = "default"


@register_repository(DeviceTypeRepository)
class SqlAlchemyDeviceTypeRepository(DeviceTypeRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _serialize(device_type: DeviceType, default_fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        own_fields = device_type.fields or {}
        fields = merge_type_fields(default_fields, own_fields) if default_fields is not None else own_fields
        return {
            "type_id": device_type.type_id,
            "label": device_type.label,
            "description": device_type.description,
            "fields": fields,
            "created_at": device_type.created_at,
            "updated_at": device_type.updated_at,
        }

    async def _get_default_type_fields(self) -> Dict[str, Any]:
        result = await self._session.execute(
            select(DeviceType).where(DeviceType.type_id == DEFAULT_TYPE_ID)
        )
        dt = result.scalar_one_or_none()
        return dt.fields or {} if dt else {}

    async def _serialize_with_mirroring(self, device_type: DeviceType) -> Dict[str, Any]:
        # The default type's fields always apply to every other device type - see
        # db/sqlalchemy/device.py:_effective_fields_for for the same rule applied to instance
        # validation. The default type itself is exempt (nothing to mirror onto itself).
        if device_type.type_id == DEFAULT_TYPE_ID:
            return self._serialize(device_type)
        default_fields = await self._get_default_type_fields()
        return self._serialize(device_type, default_fields)

    async def _get_device_type_or_raise(self, type_id: str) -> DeviceType:
        result = await self._session.execute(
            select(DeviceType).where(DeviceType.type_id == type_id)
        )
        dt = result.scalar_one_or_none()
        if dt is None:
            raise APIError(f"DeviceType '{type_id}' not found", 404)
        return dt

    async def _raise_if_label_taken(
        self, label: str, exclude_type_id: Optional[str] = None
    ) -> None:
        stmt = select(DeviceType).where(DeviceType.label == label)
        if exclude_type_id is not None:
            stmt = stmt.where(DeviceType.type_id != exclude_type_id)
        existing = await self._session.execute(stmt)
        if existing.scalar_one_or_none() is not None:
            raise APIError(f"DeviceType label '{label}' already exists", 409)

    async def get_device_types(self) -> List[Dict[str, Any]]:
        result = await self._session.execute(select(DeviceType))
        all_types = result.scalars().all()
        default_fields = next(
            (dt.fields or {} for dt in all_types if dt.type_id == DEFAULT_TYPE_ID), {}
        )
        return [
            self._serialize(dt) if dt.type_id == DEFAULT_TYPE_ID else self._serialize(dt, default_fields)
            for dt in all_types
        ]

    async def get_device_type(self, type_id: str) -> Optional[Dict[str, Any]]:
        result = await self._session.execute(
            select(DeviceType).where(DeviceType.type_id == type_id)
        )
        dt = result.scalar_one_or_none()
        if dt is None:
            return None
        return await self._serialize_with_mirroring(dt)

    async def create_device_type(
        self,
        label: str,
        description: Optional[str],
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        await self._raise_if_label_taken(label)
        dt = DeviceType(label=label, description=description, fields=fields or {})
        self._session.add(dt)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise APIError(f"DeviceType label '{label}' already exists", 409)
        await self._session.refresh(dt)
        return await self._serialize_with_mirroring(dt)

    async def update_device_type(
        self,
        type_id: str,
        label: Optional[str] = None,
        description: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        old_fields_by_type = {}
        if fields is not None:
            result = await self._session.execute(
                select(DeviceType).order_by(DeviceType.type_id).with_for_update()
                .execution_options(populate_existing=True)
            )
            types = list(result.scalars().all())
            old_fields_by_type = effective_type_fields(types)
        dt = await self._get_device_type_or_raise(type_id)
        values: Dict[str, Any] = {}
        if label is not None:
            await self._raise_if_label_taken(label, exclude_type_id=type_id)
            values["label"] = label
        if description is not None:
            values["description"] = description
        if fields is not None:
            own_fields = dt.fields or {}
            # Partial edits of mirrored fields must retain their inherited definition.
            patch_base = {
                **{key: old_fields_by_type[type_id][key] for key, patch in fields.items()
                   if patch is not None and key in old_fields_by_type[type_id]},
                **own_fields,
            }
            values["fields"] = patch_fields(patch_base, fields, clear_defaults=True)
            effective_fields = (
                values["fields"] if type_id == DEFAULT_TYPE_ID
                else merge_type_fields(old_fields_by_type.get(DEFAULT_TYPE_ID, {}), values["fields"])
            )
            for field_key, definition in effective_fields.items():
                try:
                    FieldDefinition.model_validate(definition)
                except PydanticValidationError as exc:
                    raise ValidationError(f"Invalid field '{field_key}': {exc}", 422) from exc
            dt.fields = values["fields"]
            new_fields_by_type = effective_type_fields(types)
            newly_defaulted = {
                affected_type: {
                    key: definition
                    for key, definition in new_fields.items()
                    if definition.get("default") is not None
                    and old_fields_by_type[affected_type].get(key, {}).get("default") is None
                }
                for affected_type, new_fields in new_fields_by_type.items()
            }
        if values:
            try:
                if fields is not None:
                    await backfill_device_defaults(self._session, newly_defaulted)
                await self._session.execute(
                    update(DeviceType)
                    .where(DeviceType.type_id == type_id)
                    .values(**values)
                )
                await self._session.commit()
            except APIError:
                await self._session.rollback()
                raise
            except IntegrityError:
                await self._session.rollback()
                raise APIError(f"DeviceType label '{label}' already exists", 409)
            await self._session.refresh(dt)
        return await self._serialize_with_mirroring(dt)

    async def delete_device_type(self, type_id: str) -> None:
        if type_id == DEFAULT_TYPE_ID:
            raise APIError("The default device type cannot be deleted", 400)
        await self._get_device_type_or_raise(type_id)
        await self._session.execute(
            delete(DeviceType).where(DeviceType.type_id == type_id)
        )
        await self._session.commit()
