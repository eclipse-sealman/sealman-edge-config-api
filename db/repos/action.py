from abc import ABC, abstractmethod
from typing import Any, Optional


class ActionRepository(ABC):
    @abstractmethod
    async def list(self) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    async def get(self, name: str) -> Optional[dict[str, Any]]:
        pass
