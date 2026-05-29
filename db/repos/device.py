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
    async def get_device_metadata(
        self,
        device_id: str,
        platform_name: str = "default",
    ) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_devices_metadata(
        self,
        platform_name: str = "default",
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def update_device_metadata(
        self,
        device_id: str,
        metadata: Dict[str, Any],
        platform_name: str = "default",
    ) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_platform_meta_keys(
        self,
        platform_name: str = "default",
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def add_platform_meta_key(
        self,
        key: str,
        platform_name: str = "default",
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def delete_platform_meta_key(
        self,
        key: str,
        platform_name: str = "default",
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_device_ids_by_metadata_filters(
        self,
        metadata_filters: Dict[str, Optional[str]],
        platform_name: str = "default",
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
        metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        pass


    @abstractmethod
    async def get_all_devices_raw(
        self
    ) -> List[Dict[str, Any]]:
        pass