from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod


class ServiceRepository(ABC):
    @abstractmethod
    async def get_service_types(self) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def get_service_type(self, type_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def create_service_type(
        self,
        type_id: str,
        label: str,
        description: Optional[str],
        fields: Dict[str, Any],
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def update_service_type(
        self,
        type_id: str,
        label: Optional[str] = None,
        description: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def delete_service_type(self, type_id: str) -> None: ...

    @abstractmethod
    async def get_services(
        self,
        endpoint_id: str,
        type_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def get_service(self, service_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def create_service(
        self,
        endpoint_id: str,
        type_id: str,
        service_data: Dict[str, Any],
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def update_service(
        self,
        service_id: str,
        service_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def delete_service(self, service_id: str) -> None: ...
