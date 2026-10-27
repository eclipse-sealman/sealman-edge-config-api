from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class DeviceRepository(ABC):

    @abstractmethod
    async def get_device_snapshot(self) -> Optional[List[Dict[str, Any]]]:
        pass

    @abstractmethod
    async def upsert_device_snapshot(self, devices: List[Dict[str, Any]]) -> None:
        pass

    @abstractmethod
    async def get_devices_joined_snapshot(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_device_joined_snapshot(
        self,
        device_id: str,
    ) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_device_metadata(
        self,
        device_id: str,
    ) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_devices_metadata(
        self,
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def update_device_metadata(
        self,
        device_id: str,
        metadata: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_device_ids_by_metadata_filters(
        self,
        metadata_filters: Dict[str, Optional[str]],
    ) -> List[str]:
        pass

    @abstractmethod
    async def delete_device(
        self, 
        device_id: str
    ) -> None:
        pass

    @abstractmethod
    async def device_exists(
        self, 
        device_id: str
    ) -> bool:
        pass


    @abstractmethod
    async def create_device(
        self,
        device_id: str,
        metadata: Dict[str, Any],
        type_id: str = "default",
    ) -> Dict[str, Any]:
        pass


    @abstractmethod
    async def get_all_devices_raw(
        self
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_device_meta_raw(
        self,
        device_id: str
    ) -> Optional[Dict[str, Any]]:
        """Returns raw device_data JSONB for scope evaluation, or None if device not found."""
        pass

    @abstractmethod
    async def get_devices_with_data(
        self,
        device_fields: List[str],
        endpoint_fields: List[str],
        order_by: str,
        descending: bool,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Devices with a non-null value for any `device_fields` key in `device_data`, or with
        an endpoint holding a non-null value for any `endpoint_fields` key. `order_by` is
        `created_at` or `device_data.<key>`; nulls last, ties by `device_id` asc.
        Each row: device_id, type_id, device_data, created_at, sort_value."""
        pass

    @abstractmethod
    async def count_devices_with_data(
        self,
        device_fields: List[str],
        endpoint_fields: List[str],
    ) -> int:
        """Number of devices `get_devices_with_data` matches, ignoring paging."""
        pass

    @abstractmethod
    async def get_endpoints_with_fields(
        self,
        device_ids: List[str],
        endpoint_fields: List[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Endpoints of `device_ids` with a non-null value for any `endpoint_fields` key,
        grouped by device id, ordered by created_at asc then endpoint_id.
        Each row: endpoint_id, type_id, endpoint_data, updated_at."""
        pass
