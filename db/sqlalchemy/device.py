from typing import Any, Dict, List, Optional

from datetime import datetime, timezone
from sqlalchemy import select, update, func, delete, text, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.device import Device, DeviceSnapshotCache
from db.models.platform import PlatformSettings
from db.registry import register_repository
from db.repos.device import DeviceRepository
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
                d.device_meta,
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

    @staticmethod
    def _merge_metadata(
        platform_meta: Dict[str, Any],
        device_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}

        for key in platform_meta.keys():
            if key in device_meta:
                merged[key] = {
                    "value": device_meta[key],
                    "source": "platform",
                }
            else:
                merged[key] = {
                    "value": None,
                    "source": "platform",
                }

        for key, value in device_meta.items():
            if key not in platform_meta:
                merged[key] = {
                    "value": value,
                    "source": "device",
                }

        return merged

    async def _get_platform(self, platform_name: str):
        result = await self._session.execute(
            select(PlatformSettings).where(
                PlatformSettings.name == platform_name
            )
        )
        platform = result.scalar_one_or_none()

        if not platform:
            raise ValueError(f"Platform '{platform_name}' not found")

        return platform

    async def _get_device(self, device_id: str):
        result = await self._session.execute(
            select(Device).where(Device.device_id == device_id)
        )
        return result.scalar_one_or_none()

    async def get_device_metadata(
        self,
        device_id: str,
        platform_name: str = "default",
    ) -> Optional[Dict[str, Any]]:

        device = await self._get_device(device_id)
        if not device:
            return None

        platform_meta = await self.get_platform_meta_keys()
        device_meta = device.device_meta or {}

        merged = self._merge_metadata(platform_meta, device_meta)

        return {
            "device_id": device.device_id,
            "device_metadata": merged,
            "created_at": device.created_at,
            "updated_at": device.updated_at,
        }

    async def get_devices_metadata(
        self,
        platform_name: str = "default",
    ) -> List[Dict[str, Any]]:

        platform_meta = await self.get_platform_meta_keys()

        devices = await self.get_devices_joined_snapshot()

        response: List[Dict[str, Any]] = []

        for device in devices:
            device_meta = device.get("device_meta", {})
            merged = self._merge_metadata(platform_meta, device_meta)

            response.append(
                {
                    "device_id": device.get("device_id"),
                    "device_status": device.get("connection_state"),
                    "device_metadata": merged,
                    "created_at": device.get("created_at"),
                    "updated_at": device.get("updated_at"),
                }
            )

        return response

    async def update_device_metadata(
        self,
        device_id: str,
        metadata: Dict[str, Any],
        platform_name: str = "default",
    ) -> Optional[Dict[str, Any]]:

        if not isinstance(metadata, dict):
            raise ValueError("deviceMetadata must be a dictionary")

        device = await self._get_device(device_id)
        if not device:
            return None

        current_meta = dict(device.device_meta or {})
        for key, value in metadata.items():
            if value is None:
                current_meta.pop(key, None)
            else:
                current_meta[key] = value

        stmt = (
            update(Device)
            .where(Device.device_id == device_id)
            .values(device_meta=current_meta)
        )

        await self._session.execute(stmt)
        await self._session.commit()

        return await self.get_device_metadata(
            device_id=device_id,
            platform_name=platform_name,
        )

    # -------------------- Platform Metadata Keys --------------------

    async def get_platform_meta_keys(
        self,
        platform_name: str = "default",
    ) -> Dict[str, Any]:
        platform = await self._get_platform(platform_name)
        return platform.platform_meta or {}

    async def add_platform_meta_key(
        self,
        key: str,
        platform_name: str = "default",
    ) -> Dict[str, Any]:
        platform = await self._get_platform(platform_name)
        current_meta = dict(platform.platform_meta or {})

        if key in current_meta:
            raise APIError(f"Metadata key '{key}' already exists", 409)

        current_meta[key] = None

        stmt = (
            update(PlatformSettings)
            .where(PlatformSettings.name == platform_name)
            .values(platform_meta=current_meta)
        )
        await self._session.execute(stmt)
        await self._session.commit()

        return current_meta

    async def get_device_ids_by_metadata_filters(
        self,
        metadata_filters: Dict[str, Optional[str]],
        platform_name: str = "default",
    ) -> List[str]:
        if not metadata_filters:
            return []

        conditions = []
        for key, expected_value in metadata_filters.items():
            meta_text = Device.device_meta[key].astext

            if expected_value is None:
                # key-only filter: key exists and value is neither empty nor whitespace-only
                conditions.append(Device.device_meta.has_key(key))
                conditions.append(func.btrim(func.coalesce(meta_text, "")) != "")
            else:
                conditions.append(meta_text == expected_value)

        result = await self._session.execute(
            select(Device.device_id).where(and_(*conditions))
        )
        return list(result.scalars().all())

    async def delete_platform_meta_key(
        self,
        key: str,
        platform_name: str = "default",
    ) -> Dict[str, Any]:
        platform = await self._get_platform(platform_name)
        current_meta = dict(platform.platform_meta or {})

        if key not in current_meta:
            raise APIError(f"Metadata key '{key}' not found", 404)

        del current_meta[key]

        stmt = (
            update(PlatformSettings)
            .where(PlatformSettings.name == platform_name)
            .values(platform_meta=current_meta)
        )
        await self._session.execute(stmt)
        await self._session.commit()

        return current_meta
    async def device_exists(self, device_id: str) -> bool:
        device = await self._get_device(device_id)
        return device is not None

    async def create_device(self, device_id: str, metadata: Dict[str, Any]):
        device = Device(device_id=device_id, device_meta=metadata or {})
        self._session.add(device)
        await self._session.commit()
        await self._session.refresh(device)

        return {
            "device_id": device.device_id,
            "device_meta": device.device_meta,
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
                "device_meta": device.device_meta or {},
            }
            for device in devices
        ]