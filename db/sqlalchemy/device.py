from typing import Any, Dict, List, Optional, cast

from sqlalchemy import select, update, func, delete, text, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.device import Device, DeviceSnapshotCache
from db.models.device_type import DeviceType
from db.models.platform_config import PlatformConfig
from db.registry import register_repository
from db.repos.device import DeviceRepository
from db.merge import resolve_fields, validate_instance_data, patch_data
from exceptions import APIError


DEVICE_SNAPSHOT_CACHE_KEY = "current"


@register_repository(DeviceRepository)
class SqlAlchemyDeviceRepository(DeviceRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_device_snapshot(self) -> Optional[List[Dict[str, Any]]]:
        result = await self._session.execute(
            select(DeviceSnapshotCache).where(
                DeviceSnapshotCache.cache_key == DEVICE_SNAPSHOT_CACHE_KEY
            )
        )
        snapshot = result.scalar_one_or_none()
        if snapshot is None:
            return None
        return list(snapshot.devices_json or [])

    async def upsert_device_snapshot(self, devices: List[Dict[str, Any]]) -> None:
        stmt = pg_insert(DeviceSnapshotCache).values(
            cache_key=DEVICE_SNAPSHOT_CACHE_KEY,
            devices_json=devices,
            device_count=len(devices),
            cached_at=func.now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[DeviceSnapshotCache.cache_key],
            set_={
                "devices_json": stmt.excluded.devices_json,
                "device_count": stmt.excluded.device_count,
                "cached_at": stmt.excluded.cached_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def get_devices_joined_snapshot(self) -> List[Dict[str, Any]]:
        stmt = text(
            """
            select
                d.device_id,
                d.type_id,
                d.device_data,
                d.created_at,
                d.updated_at,
                vds.connection_state,
                vds.cached_at as state_snapshot_cached_at
            from devices d
            left join view_device_snapshot vds on d.device_id = vds.device_id
            """
        )
        result = await self._session.execute(stmt)
        return [dict(row) for row in result.mappings().all()]

    async def _get_device_type_or_raise(self, type_id: str) -> DeviceType:
        result = await self._session.execute(
            select(DeviceType).where(DeviceType.type_id == type_id)
        )
        dt = result.scalar_one_or_none()
        if dt is None:
            raise APIError(f"DeviceType '{type_id}' not found", 404)
        return dt

    async def _get_device(self, device_id: str) -> Optional[Device]:
        result = await self._session.execute(
            select(Device).where(Device.device_id == device_id)
        )
        return result.scalar_one_or_none()

    async def _get_platform_config(self, config_name: str) -> PlatformConfig:
        result = await self._session.execute(
            select(PlatformConfig).where(PlatformConfig.name == config_name)
        )
        config = result.scalar_one_or_none()
        if not config:
            raise ValueError(f"PlatformConfig '{config_name}' not found")
        return config

    async def get_device_metadata(
        self,
        device_id: str,
    ) -> Optional[Dict[str, Any]]:

        device = await self._get_device(device_id)
        if not device:
            return None

        device_type = await self._get_device_type_or_raise(device.type_id)

        return {
            "device_id": device.device_id,
            "type_id": device.type_id,
            "device_metadata": resolve_fields(
                device.device_data or {}, device_type.fields or {}
            ),
            "created_at": device.created_at,
            "updated_at": device.updated_at,
        }

    async def get_devices_metadata(
        self,
    ) -> List[Dict[str, Any]]:

        types_result = await self._session.execute(select(DeviceType))
        fields_by_type = {dt.type_id: dt.fields or {} for dt in types_result.scalars().all()}

        devices = await self.get_devices_joined_snapshot()

        response: List[Dict[str, Any]] = []

        for device in devices:
            device_data = device.get("device_data") or {}
            type_fields = fields_by_type.get(device.get("type_id"), {})
            resolved = resolve_fields(device_data, type_fields)

            response.append(
                {
                    "device_id": device.get("device_id"),
                    "type_id": device.get("type_id"),
                    "device_status": device.get("connection_state"),
                    "device_metadata": resolved,
                    "created_at": device.get("created_at"),
                    "updated_at": device.get("updated_at"),
                }
            )

        return response

    async def update_device_metadata(
        self,
        device_id: str,
        metadata: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:

        if not isinstance(metadata, dict):
            raise ValueError("deviceMetadata must be a dictionary")

        device = await self._get_device(device_id)
        if not device:
            return None

        device_type = await self._get_device_type_or_raise(device.type_id)
        merged_data = patch_data(device.device_data or {}, metadata)
        validate_instance_data(merged_data, device_type.fields or {})

        stmt = (
            update(Device)
            .where(Device.device_id == device_id)
            .values(device_data=merged_data)
        )

        await self._session.execute(stmt)
        await self._session.commit()

        return await self.get_device_metadata(device_id=device_id)

    async def get_device_ids_by_metadata_filters(
        self,
        metadata_filters: Dict[str, Optional[str]],
    ) -> List[str]:
        if not metadata_filters:
            return []

        conditions = []
        for key, expected_value in metadata_filters.items():
            meta_text = Device.device_data[key].astext

            if expected_value is None:
                # key-only filter: key exists and value is neither empty nor whitespace-only
                conditions.append(Device.device_data.has_key(key))
                conditions.append(func.btrim(func.coalesce(meta_text, "")) != "")
            else:
                conditions.append(meta_text == expected_value)

        result = await self._session.execute(
            select(Device.device_id).where(and_(*conditions))
        )
        return list(result.scalars().all())

    async def device_exists(self, device_id: str) -> bool:
        device = await self._get_device(device_id)
        return device is not None

    async def create_device(
        self, device_id: str, metadata: Dict[str, Any], type_id: str = "default"
    ):
        device_type = await self._get_device_type_or_raise(type_id)
        validate_instance_data(metadata or {}, device_type.fields or {})

        device = Device(device_id=device_id, type_id=type_id, device_data=metadata or {})
        self._session.add(device)
        await self._session.commit()
        await self._session.refresh(device)

        return {
            "device_id": device.device_id,
            "type_id": device.type_id,
            "device_data": device.device_data,
            "created_at": device.created_at,
            "updated_at": device.updated_at,
        }

    async def delete_device(self, device_id: str) -> None:
        stmt = delete(Device).where(Device.device_id == device_id)
        await self._session.execute(stmt)
        await self._session.commit()

    async def get_all_devices_raw(self) -> List[Dict[str, Any]]:
        result = await self._session.execute(select(Device))
        devices = result.scalars().all()
        return [
            {
                "device_id": device.device_id,
                "type_id": device.type_id,
                "device_data": device.device_data or {},
            }
            for device in devices
        ]

    async def get_device_meta_raw(self, device_id: str) -> Optional[Dict[str, Any]]:
        device = await self._get_device(device_id)
        if device is None:
            return None
        return dict(cast(Dict[str, Any], device.device_data) or {})


    # -------------------- Device Template Config --------------------

    async def get_device_template_config(
        self,
        config_name: str = "default",
    ) -> Dict[str, Any]:
        config = await self._get_platform_config(config_name)
        return config.device_template_config or {}

    async def update_device_template_config(
        self,
        config: Dict[str, Any],
        config_name: str = "default",
    ) -> Dict[str, Any]:
        platform_config = await self._get_platform_config(config_name)
        current = dict(platform_config.device_template_config or {})
        current.update(config)

        stmt = (
            update(PlatformConfig)
            .where(PlatformConfig.name == config_name)
            .values(device_template_config=current)
        )
        await self._session.execute(stmt)
        await self._session.commit()

        return current


    # -------------------- Endpoint Types --------------------

    async def get_endpoint_types(
        self,
        config_name: str = "default",
    ) -> List[Dict[str, Any]]:
        config = await self._get_platform_config(config_name)
        return config.endpoint_types or []

    async def save_endpoint_types(
        self,
        types: List[Dict[str, Any]],
        config_name: str = "default",
    ) -> List[Dict[str, Any]]:
        stmt = (
            update(PlatformConfig)
            .where(PlatformConfig.name == config_name)
            .values(endpoint_types=types)
        )
        await self._session.execute(stmt)
        await self._session.commit()
        return types

    # -------------------- Service Ports --------------------

    async def get_service_ports(
        self,
        config_name: str = "default",
    ) -> List[Dict[str, Any]]:
        config = await self._get_platform_config(config_name)
        return config.service_ports or []

    async def save_service_ports(
        self,
        ports: List[Dict[str, Any]],
        config_name: str = "default",
    ) -> List[Dict[str, Any]]:
        stmt = (
            update(PlatformConfig)
            .where(PlatformConfig.name == config_name)
            .values(service_ports=ports)
        )
        await self._session.execute(stmt)
        await self._session.commit()
        return ports

    # -------------------- Selected Templates --------------------

    async def get_selected_templates(
        self,
        config_name: str = "default",
    ) -> List[str]:
        config = await self._get_platform_config(config_name)
        return config.selected_templates or []

    async def save_selected_templates(
        self,
        templates: List[str],
        config_name: str = "default",
    ) -> List[str]:
        stmt = (
            update(PlatformConfig)
            .where(PlatformConfig.name == config_name)
            .values(selected_templates=templates)
        )
        await self._session.execute(stmt)
        await self._session.commit()
        return templates
