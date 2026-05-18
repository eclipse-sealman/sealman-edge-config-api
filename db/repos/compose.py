from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Literal

class ComposeRepository(ABC):

    @abstractmethod
    def create_or_update(
            self,
            name: str,
            request: Dict,
            content: Dict,
            landing_page: Optional[bool] = False,
            description: Optional[str] = None,
    ) -> bool:
        ...

    @abstractmethod
    def get(
            self,
            name: str,
            landing_page: Optional[bool] = None
    ) -> Optional[Dict]:
        ...

    @abstractmethod
    def delete(
            self,
            name: str,
            landing_page: Optional[bool] = None
    ) -> bool:
        ...

    @abstractmethod
    def list_names(
            self,
            prefix: Optional[str] = None
    ) -> List[str]:
        ...

    @abstractmethod
    def list(
            self,
            prefix: Optional[str] = None,
            landing_page: Optional[bool] = None
    ) -> List[Dict]:
        ...
        
    async def set_active_deployment(self, name: str) -> None:
        ...
        
    async def get_active_deployment(self) -> Optional[str]:
        ...
        
    async def delete_active_deployment(self) -> bool:
        ...