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
    async def get_device_template_config(
        self,
        config_name: str = "default",
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def update_device_template_config(
        self,
        config: Dict[str, Any],
        config_name: str = "default",
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_endpoint_types(
        self,
        config_name: str = "default",
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def save_endpoint_types(
        self,
        types: List[Dict[str, Any]],
        config_name: str = "default",
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_service_ports(
        self,
        config_name: str = "default",
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def save_service_ports(
        self,
        ports: List[Dict[str, Any]],
        config_name: str = "default",
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_selected_templates(
        self,
        config_name: str = "default",
    ) -> List[str]:
        pass

    @abstractmethod
    async def save_selected_templates(
        self,
        templates: List[str],
        config_name: str = "default",
    ) -> List[str]:
        pass