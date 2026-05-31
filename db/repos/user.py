from abc import ABC, abstractmethod
from typing import Any, List, Optional


class UserRepository(ABC):

    @abstractmethod
    async def list(self, is_new: Optional[bool] = None) -> List[dict[str, Any]]:
        pass

    @abstractmethod
    async def get(self, user_id: str) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def get_by_ids(self, user_ids: List[str]) -> List[dict[str, Any]]:
        pass

    @abstractmethod
    async def create(
        self,
        user_id: str,
        preferred_username: str,
        is_admin: bool = False,
        is_new: bool = True,
    ) -> dict[str, Any]:
        pass

    @abstractmethod
    async def update(
        self,
        user_id: str,
        preferred_username: str,
        is_admin: bool,
        is_new: bool,
    ) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def delete(self, user_id: str) -> bool:
        pass

    @abstractmethod
    async def ensure_exists(
        self,
        user_id: str,
        preferred_username: str,
        is_admin: bool = False,
    ) -> None:
        pass

    @abstractmethod
    async def list_teams(self, user_id: str) -> List[dict[str, Any]]:
        pass