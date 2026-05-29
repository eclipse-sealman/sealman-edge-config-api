from abc import ABC, abstractmethod
from typing import Any, List, Optional
from uuid import UUID


class ScopeRepository(ABC):

    @abstractmethod
    async def list(self) -> List[dict[str, Any]]:
        pass

    @abstractmethod
    async def get(self, scope_id: UUID) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def create(
        self,
        name: str,
        attr: dict[str, Any],
        access_rule: str,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        pass

    @abstractmethod
    async def update(
        self,
        scope_id: UUID,
        name: str,
        attr: dict[str, Any],
        access_rule: str,
        description: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def delete(self, scope_id: UUID) -> None:
        pass

    @abstractmethod
    async def list_teams(self, scope_id: UUID) -> List[dict[str, Any]]:
        pass

    @abstractmethod
    async def list_teams(self, scope_id: UUID) -> List[dict[str, Any]]:
        pass

