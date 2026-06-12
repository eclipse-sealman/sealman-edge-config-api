from abc import ABC, abstractmethod
from typing import Any, List, Optional


class ActionRepository(ABC):
    @abstractmethod
    async def list(self) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    async def get(self, name: str) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def get_by_names(self, names: List[str]) -> List[dict[str, Any]]:
        pass

