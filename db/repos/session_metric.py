from abc import ABC, abstractmethod
from typing import Any, List, Optional


class SessionMetricRepository(ABC):

    @abstractmethod
    async def get_personal(self, user_id: str) -> int:
        pass

    @abstractmethod
    async def get_best(self) -> Optional[dict[str, Any]]:
        """The single highest entry as {"name", "value"}, or None while nobody has one."""
        pass

    @abstractmethod
    async def submit(self, user_id: str, value: int) -> None:
        """Stores the value only if it beats the user's stored one."""
        pass

    @abstractmethod
    async def top(self, limit: int) -> List[dict[str, Any]]:
        pass
