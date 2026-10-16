"""In-process registry of the module schemas that extensions have activated.

Holds the active, validated schema JSON and its owning extension per exact IoT Hub
module ID and purpose (`configuration` or `reported`). Persisted snapshots live in the
database (see extensions/registry.py); this registry only ever holds what the lifecycle
has activated, so a disabled or deregistered extension has no entries here.

A module ID that an enabled extension claims but cannot activate stays claimed (as
unavailable), so the module never falls back to a generic response.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Literal, Optional, Set

ModuleSchemaPurpose = Literal["configuration", "reported"]


@dataclass(frozen=True)
class ActiveModuleSchema:
    module_id: str
    purpose: ModuleSchemaPurpose
    extension_name: str
    version: str
    schema: dict


class ModuleSchemaConflictError(Exception):
    """A module ID is already claimed by a different extension."""


class ModuleSchemaRegistry:
    def __init__(self) -> None:
        self._active: Dict[tuple, ActiveModuleSchema] = {}
        self._claims: Dict[str, str] = {}  # module_id -> owning extension, active or unavailable
        self._unavailable: Set[str] = set()

    def get(self, module_id: str, purpose: ModuleSchemaPurpose) -> Optional[ActiveModuleSchema]:
        return self._active.get((module_id, purpose))

    def list_active(self) -> List[ActiveModuleSchema]:
        return [self._active[key] for key in sorted(self._active)]

    def claim_owner(self, module_id: str) -> Optional[str]:
        return self._claims.get(module_id)

    def is_unavailable(self, module_id: str) -> bool:
        return module_id in self._unavailable

    def replace_extension_entries(
        self,
        extension_name: str,
        schemas: Iterable[ActiveModuleSchema],
        unavailable_modules: Iterable[str],
    ) -> None:
        """Makes `extension_name`'s entries exactly `schemas` plus the claimed-but-unavailable
        `unavailable_modules`. Passing nothing removes the extension's entries (disable,
        deregister). Refuses, changing nothing, if a module ID belongs to another extension."""
        schemas = list(schemas)
        unavailable = set(unavailable_modules)
        claimed = {schema.module_id for schema in schemas} | unavailable
        for module_id in sorted(claimed):
            owner = self._claims.get(module_id)
            if owner not in (None, extension_name):
                raise ModuleSchemaConflictError(f"Module '{module_id}' is already claimed by extension '{owner}'")

        for key in [key for key, schema in self._active.items() if schema.extension_name == extension_name]:
            del self._active[key]
        for module_id in [module_id for module_id, owner in self._claims.items() if owner == extension_name]:
            del self._claims[module_id]
        self._unavailable = {module_id for module_id in self._unavailable if module_id in self._claims}

        for schema in schemas:
            self._active[(schema.module_id, schema.purpose)] = schema
        for module_id in claimed:
            self._claims[module_id] = extension_name
        self._unavailable |= unavailable


module_schema_registry = ModuleSchemaRegistry()
