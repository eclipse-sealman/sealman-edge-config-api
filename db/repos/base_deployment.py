from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class StoredBaseDeployment:
    modules: List[Dict[str, Any]]
    manifest: Dict[str, Any]
    edge_hub_env: Dict[str, str]


class BaseDeploymentRepository(ABC):
    @abstractmethod
    async def get(self) -> Optional[StoredBaseDeployment]:
        """The saved base deployment, or None while the shipped default is in use."""
        ...

    @abstractmethod
    async def save(
        self, modules: List[Dict[str, Any]], manifest: Dict[str, Any], edge_hub_env: Dict[str, str]
    ) -> StoredBaseDeployment:
        ...

    @abstractmethod
    async def delete(self) -> None:
        """Drops the saved base deployment so the shipped default applies again."""
        ...
