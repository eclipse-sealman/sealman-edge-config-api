from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod


class DeviceTypeRepository(ABC):
    @abstractmethod
    async def get_device_types(self) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def get_device_type(self, type_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def create_device_type(
        self,
        label: str,
        description: Optional[str],
        fields: Dict[str, Any],
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def update_device_type(
        self,
        type_id: str,
        label: Optional[str] = None,
        description: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def delete_device_type(self, type_id: str) -> None: ...
