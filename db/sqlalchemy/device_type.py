from typing import Any, Dict, List, Optional
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from db.models.device_type import DeviceType
from db.registry import register_repository
from db.repos.device_type import DeviceTypeRepository
from db.merge import patch_fields, merge_type_fields
from exceptions import APIError

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
        type_id: str,
        label: str,
        description: Optional[str],
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        existing = await self._session.execute(
            select(DeviceType).where(DeviceType.type_id == type_id)
        )
        if existing.scalar_one_or_none() is not None:
            raise APIError(f"DeviceType '{type_id}' already exists", 409)
        await self._raise_if_label_taken(label)
        dt = DeviceType(
            type_id=type_id, label=label, description=description, fields=fields or {}
        )
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
        dt = await self._get_device_type_or_raise(type_id)
        values: Dict[str, Any] = {}
        if label is not None:
            await self._raise_if_label_taken(label, exclude_type_id=type_id)
            values["label"] = label
        if description is not None:
            values["description"] = description
        if fields is not None:
            values["fields"] = patch_fields(dt.fields or {}, fields)
        if values:
            try:
                await self._session.execute(
                    update(DeviceType)
                    .where(DeviceType.type_id == type_id)
                    .values(**values)
                )
                await self._session.commit()
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
