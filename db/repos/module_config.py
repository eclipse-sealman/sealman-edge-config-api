from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from uuid import UUID


class UnknownScope(LookupError):
    """A template group references a scope that does not exist."""


class ModuleConfigRepository(ABC):
    # groups
    @abstractmethod
    async def list_groups(self) -> List[Dict]:
        """Each group with its `scopes` (id, name, attr, access_rule) and `template_count`."""

    @abstractmethod
    async def get_group(self, group_id: int) -> Optional[Dict]: ...

    @abstractmethod
    async def create_group(
        self, *, name: str, description: Optional[str], module_names: List[str], scope_ids: List[UUID],
        created_by: Optional[str],
    ) -> Optional[Dict]:
        """Returns None if the name is taken; raises UnknownScope."""

    @abstractmethod
    async def update_group(
        self, group_id: int, *, name: Optional[str] = None, description: Optional[str] = None,
        module_names: Optional[List[str]] = None, scope_ids: Optional[List[UUID]] = None,
    ) -> Optional[Dict]:
        """Returns None if not found; raises ValueError if the name is taken, UnknownScope for a bad scope."""

    @abstractmethod
    async def delete_group(self, group_id: int) -> str:
        """Returns `deleted`, `not_found` or `in_use` (it still holds templates)."""

    # templates
    @abstractmethod
    async def list_templates(
        self, module: Optional[str] = None, include_archived: bool = False, group_id: Optional[int] = None,
        kind: Optional[str] = None,
    ) -> List[Dict]:
        """Templates with `group_id`, `group_name`, `module_names` and `scopes` of their group; `kind`
        ("template" or "block") filters."""

    @abstractmethod
    async def get_template(self, template_id: int) -> Optional[Dict]: ...

    @abstractmethod
    async def create_template(
        self, *, name: str, description: Optional[str], group_id: int,
        body: Any, variables: List[Dict], comment: Optional[str], created_by: Optional[str],
        derived_from_template_id: Optional[int] = None, derived_from_version: Optional[int] = None,
        kind: str = "template", slots: Optional[List[Dict]] = None,
    ) -> Optional[Dict]:
        """Creates the template with version 1. Returns None if the name is taken."""

    @abstractmethod
    async def update_template(
        self, template_id: int, *, name: Optional[str] = None, description: Optional[str] = None,
        group_id: Optional[int] = None, archived: Optional[bool] = None,
    ) -> Optional[Dict]:
        """Updates mutable metadata. Returns None if not found; raises ValueError if the name is taken."""

    @abstractmethod
    async def add_version(
        self, template_id: int, *, body: Dict, variables: List[Dict], comment: Optional[str], created_by: Optional[str],
        slots: Optional[List[Dict]] = None,
    ) -> Optional[Dict]:
        """Adds the next version. Returns None if the template does not exist."""

    @abstractmethod
    async def list_versions(self, template_id: int) -> List[Dict]: ...

    @abstractmethod
    async def get_version(self, template_id: int, version: Optional[int] = None) -> Optional[Dict]:
        """`version=None` returns the latest version."""

    # revisions
    @abstractmethod
    async def add_revision(self, **fields: Any) -> Dict:
        """`fields` are the revision's columns; `secret_paths` (the JSON pointers whose value came from a
        secret file, with owner, field, name and sha256) defaults to []. If the config id is already
        recorded, returns that revision; an `external` one is first updated with `fields` when they carry
        secret paths."""

    @abstractmethod
    async def delete_revision(self, device_id: str, module_name: str, revision_id: int) -> None: ...

    @abstractmethod
    async def list_revisions(self, device_id: str, module_name: str, limit: int = 100) -> List[Dict]: ...

    @abstractmethod
    async def get_revision(self, device_id: str, module_name: str, revision_id: int) -> Optional[Dict]: ...

    @abstractmethod
    async def get_revision_by_config_id(self, device_id: str, module_name: str, config_id: str) -> Optional[Dict]: ...
