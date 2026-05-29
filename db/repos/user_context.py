from abc import ABC, abstractmethod
from typing import Any, List, Optional


class UserContextRepository(ABC):

    @abstractmethod
    async def list(self, is_new_user: Optional[bool] = None) -> List[dict[str, Any]]:
        pass

    @abstractmethod
    async def get(self, user_id: str) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def create(
        self,
        user_id: str,
        preferred_username: str,
        is_admin: bool = False,
        is_new_user: bool = True,
    ) -> dict[str, Any]:
        pass

    @abstractmethod
    async def update(
        self,
        user_id: str,
        preferred_username: str,
        is_admin: bool,
        is_new_user: bool,
    ) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def delete(self, user_id: str) -> bool:
        pass

    @abstractmethod
    async def list_teams(self, user_id: str) -> List[dict[str, Any]]:
        pass
