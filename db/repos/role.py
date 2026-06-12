from abc import ABC, abstractmethod
from typing import Any, Optional
from uuid import UUID


class RoleRepository(ABC):
    @abstractmethod
    async def list_roles(self) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    async def get(self, role_id: UUID) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def get_by_name(self, name: str) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def get_by_ids(self, role_ids: list[UUID]) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    async def create_role(self, name: str, description: str | None, action_names: list[str]) -> dict[str, Any]:
        pass

    @abstractmethod
    async def update_role(
        self,
        role_id: UUID,
        name: str,
        description: str | None,
    ) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def add_actions_to_role(
        self,
        role_id: UUID,
        action_names: list[str],
    ) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def remove_action_from_role(
        self,
        role_id: UUID,
        action_name: str,
    ) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def delete_role(self, role_id: UUID) -> None:
        pass

