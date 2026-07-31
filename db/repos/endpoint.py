from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod


class EndpointRepository(ABC):
    @abstractmethod
    async def get_endpoint_types(self) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def get_endpoint_type(self, type_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def create_endpoint_type(
        self,
        type_id: str,
        label: str,
        description: Optional[str],
        fields: Dict[str, Any],
        mapping: Dict[str, Any],
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def update_endpoint_type(
        self,
        type_id: str,
        label: Optional[str] = None,
        description: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
        mapping: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def delete_endpoint_type(self, type_id: str, cascade: bool = False) -> None:
        """Deletes the type. If `cascade` is False and endpoints of this type still exist,
        raises APIError(409). If `cascade` is True, deletes those endpoints (and, via FK
        cascade, their services) first."""
        ...

    @abstractmethod
    async def get_endpoints(
        self,
        device_id: str,
        type_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def get_endpoint(self, endpoint_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def create_endpoint(
        self,
        device_id: str,
        type_id: str,
        endpoint_data: Dict[str, Any],
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def update_endpoint(
        self,
        endpoint_id: str,
        endpoint_data: Optional[Dict[str, Any]] = None,
        type_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def delete_endpoint(self, endpoint_id: str) -> None: ...
