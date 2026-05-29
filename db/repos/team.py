from abc import ABC, abstractmethod
from typing import Any, List, Optional
from uuid import UUID


class TeamRepository(ABC):

    @abstractmethod
    async def list(self) -> List[dict[str, Any]]:
        pass

    @abstractmethod
    async def get(self, team_id: UUID) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def get_with_details(self, team_id: UUID) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def create(self, name: str, scope_id: Optional[UUID] = None) -> dict[str, Any]:
        pass

    @abstractmethod
    async def update(
        self,
        team_id: UUID,
        name: str,
        scope_id: Optional[UUID] = None,
    ) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def add_user(
        self,
        team_id: UUID,
        user_id: str,
    ) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def remove_user(self, team_id: UUID, user_id: str) -> bool:
        pass

    @abstractmethod
    async def add_role(self, team_id: UUID, role_id: UUID) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def remove_role(self, team_id: UUID, role_id: UUID) -> bool:
        pass
