from abc import ABC, abstractmethod
from typing import Optional, Dict, List


class ComposeRepository(ABC):
    @abstractmethod
    async def create_or_update(
        self,
        name: str,
        request: Dict,
        content: Dict,
        landing_page: Optional[bool] = False,
        description: Optional[str] = None,
    ) -> bool: ...

    @abstractmethod
    async def get(
        self, name: str, landing_page: Optional[bool] = None
    ) -> Optional[Dict]: ...

    @abstractmethod
    async def delete(self, name: str, landing_page: Optional[bool] = None) -> bool: ...

    @abstractmethod
    async def list_names(self, prefix: Optional[str] = None) -> List[str]: ...

    @abstractmethod
    async def list(
        self, prefix: Optional[str] = None, landing_page: Optional[bool] = None
    ) -> List[Dict]: ...

    @abstractmethod
    async def set_active_deployment(self, name: str) -> None: ...

    @abstractmethod
    async def get_active_deployment(self) -> Optional[str]: ...

    @abstractmethod
    async def delete_active_deployment(self) -> bool: ...

