from abc import ABC, abstractmethod
from typing import Any, List, Optional


class ExtensionRepository(ABC):
    """Persistence for the dynamic API extension system (extensions, their
    contributed routes, RBAC-action provenance and per-device field-ingress
    keys). Business rules (validation, key generation/hashing, RBAC role
    grants) live in :mod:`extensions.registry`, not here.
    """

    # --- extensions ----------------------------------------------------
    @abstractmethod
    async def list_extensions(self) -> List[dict[str, Any]]:
        pass

    @abstractmethod
    async def get_extension(self, name: str) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    async def create_extension(
        self, name: str, upstreams: dict, description: str, internal_key_hash: Optional[str]
    ) -> dict[str, Any]:
        """Raises ValueError if an extension with this name already exists."""
        pass

    @abstractmethod
    async def delete_extension(self, name: str) -> bool:
        pass

    # --- routes ----------------------------------------------------------
    @abstractmethod
    async def add_route(self, extension_name: str, route: dict[str, Any]) -> None:
        pass

    @abstractmethod
    async def list_routes(self, extension_name: str) -> List[dict[str, Any]]:
        pass

    @abstractmethod
    async def all_routes(self) -> List[dict[str, Any]]:
        """Every registered route across all extensions, enriched with the
        resolved upstream transport (``transport``/``base_url``/``module_name``).
        Used to re-hydrate live routes on startup."""
        pass

    # --- RBAC action provenance -------------------------------------------
    @abstractmethod
    async def ensure_action(self, action_name: str, description: str, is_global: bool = False) -> None:
        """Create the Action row if it doesn't already exist (idempotent)."""
        pass

    @abstractmethod
    async def record_extension_action(self, extension_name: str, action_name: str) -> None:
        pass

    @abstractmethod
    async def list_extension_actions(self, extension_name: str) -> List[str]:
        pass

    @abstractmethod
    async def delete_orphaned_actions(self, action_names: List[str]) -> None:
        """Delete Action rows in ``action_names`` that no longer have any
        ``extension_actions`` provenance row (called after deregistration)."""
        pass

    # --- device keys (field-ingress / 'device' channel) -------------------
    @abstractmethod
    async def upsert_device_key(
        self, extension_name: str, device_id: str, module_id: Optional[str], key_hash: str
    ) -> None:
        pass

    @abstractmethod
    async def list_device_keys(self, extension_name: str) -> List[dict[str, Any]]:
        pass

    @abstractmethod
    async def revoke_device_key(self, extension_name: str, device_id: str) -> bool:
        pass

    @abstractmethod
    async def resolve_device_key(self, extension_name: str, key_hash: str) -> Optional[str]:
        """Returns the resolved ``device_id`` for a presented key hash, or
        ``None`` if no match exists for this extension."""
        pass
