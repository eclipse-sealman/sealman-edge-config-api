"""Validation of an extension manifest's `endpoint_fields` section.

Each entry is a `FieldDefinition` an admin can later apply to endpoint types.
Contributed fields are never required. Kept free of `extensions.registry` and
`extensions.setup` imports so endpoint-type code can use it without a cycle.
"""

import copy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from pydantic import ValidationError as PydanticValidationError

from db.repos.endpoint import EndpointRepository
from db.repos.extension import ExtensionRepository
from exceptions import APIError
from routers.schemas import normalize_field_definition


def validate_endpoint_fields(
    raw: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Dict[str, Any]]]:
    """Returns the normalized definitions, or None when the section is absent.

    Raises APIError(422) for an invalid definition (including one that is not an
    object) or one with `required: true`.
    """
    if raw is None:
        return None

    normalized: Dict[str, Dict[str, Any]] = {}
    for key, definition in raw.items():
        try:
            normalized_definition = normalize_field_definition(definition)
        except PydanticValidationError as exc:
            raise APIError(f"Invalid endpoint field '{key}': {exc}", 422) from exc
        if normalized_definition.get("required"):
            raise APIError(f"Endpoint field '{key}' cannot be required", 422)
        normalized[key] = normalized_definition
    return normalized


@dataclass
class EndpointFieldDiff:
    """How a manifest update changes the declared fields. Every list is sorted."""

    removed: List[str]
    changed: List[str]
    added: List[str]


def diff_endpoint_fields(old: Dict[str, Dict[str, Any]], new: Dict[str, Dict[str, Any]]) -> EndpointFieldDiff:
    """Compares the stored and the new declared fields by key.

    A key in both is "changed" only when the normalized definitions differ, so a
    definition that just spells out defaults is not a change.
    """
    return EndpointFieldDiff(
        removed=sorted(old.keys() - new.keys()),
        changed=sorted(
            key
            for key in old.keys() & new.keys()
            if normalize_field_definition(old[key]) != normalize_field_definition(new[key])
        ),
        added=sorted(new.keys() - old.keys()),
    )


async def validate_endpoint_field_ownership(
    extension_repo: ExtensionRepository, name: str, keys: Iterable[str]
) -> None:
    """Raises APIError(409) if another extension already declares one of `keys`.

    A field key belongs to one extension; `name`'s own stored keys are not a conflict.
    """
    owners: Dict[str, str] = {}
    for row in await extension_repo.list_extensions_rows():
        if row["name"] == name:
            continue
        for key in row.get("endpoint_fields") or {}:
            owners.setdefault(key, row["name"])
    for key in keys:
        owner = owners.get(key)
        if owner is not None:
            raise APIError(f"Endpoint field '{key}' is already declared by extension '{owner}'", 409)


async def list_extension_field_sets(
    extension_repo: ExtensionRepository, endpoint_repo: EndpointRepository
) -> List[Dict[str, Any]]:
    """The extensions that declare endpoint fields, ordered by name, disabled ones included.

    `applied` maps every declared key to the sorted type_ids it is applied to ([] if none).
    """
    types = await endpoint_repo.get_endpoint_types()
    field_sets: List[Dict[str, Any]] = []
    for row in sorted(await extension_repo.list_extensions_rows(), key=lambda r: r["name"]):
        declared = row.get("endpoint_fields") or {}
        if not declared:
            continue
        name = row["name"]
        applied = {
            key: sorted(
                t["type_id"] for t in types if (t.get("managed_by") or {}).get(key) == name
            )
            for key in declared
        }
        field_sets.append(
            {"extension": name, "enabled": bool(row["enabled"]), "fields": declared, "applied": applied}
        )
    return field_sets


async def _get_enabled_extension_fields(
    extension_repo: ExtensionRepository, extension: str
) -> Dict[str, Dict[str, Any]]:
    """The stored declared fields of an enabled extension. 404 unknown, 409 disabled."""
    row = await extension_repo.get_extension_row(extension)
    if row is None:
        raise APIError(f"Extension '{extension}' not found", 404)
    if not row["enabled"]:
        raise APIError(f"Extension '{extension}' is disabled; enable it to apply its fields", 409)
    return row.get("endpoint_fields") or {}


async def resolve_extension_fields(
    extension_repo: ExtensionRepository,
    extension_fields: Dict[str, List[str]],
    own_keys: Set[str],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """Resolves a type request's `extension_fields` ({extension: [key, ...]}).

    Returns (definitions_to_add, managed_by_to_add); the definitions are copies of the
    stored manifest definitions. Errors per extension, in this order: 422 no keys listed,
    404 unknown extension, 409 disabled extension, 404 undeclared key, then 422 for a key
    that is also in the request's own `fields` (`own_keys`).
    """
    definitions: Dict[str, Dict[str, Any]] = {}
    managed_by: Dict[str, str] = {}
    for extension, keys in extension_fields.items():
        if not keys:
            raise APIError(f"extension_fields for '{extension}' lists no keys", 422)
        declared = await _get_enabled_extension_fields(extension_repo, extension)
        for key in keys:
            if key not in declared:
                raise APIError(f"Extension '{extension}' declares no endpoint field '{key}'", 404)
        for key in keys:
            if key in own_keys:
                raise APIError(f"'{key}' is in both fields and extension_fields", 422)
            definitions[key] = copy.deepcopy(declared[key])
            managed_by[key] = extension
    return definitions, managed_by


async def apply_extension_fields(
    extension_repo: ExtensionRepository,
    endpoint_repo: EndpointRepository,
    extension: str,
    type_ids: List[str],
    keys: List[str],
) -> Dict[str, List[Dict[str, str]]]:
    """Applies `keys` of `extension`'s declared fields to the endpoint types `type_ids`.

    Errors, in this order: 422 empty lists, 404 unknown extension, 409 disabled
    extension, 404 undeclared key, 404 unknown type (raised by the repository before
    anything is written). Nothing is written to endpoints.
    """
    if not type_ids or not keys:
        raise APIError("type_ids and keys must not be empty", 422)
    declared = await _get_enabled_extension_fields(extension_repo, extension)
    definitions: Dict[str, Dict[str, Any]] = {}
    for key in keys:
        if key not in declared:
            raise APIError(f"Extension '{extension}' declares no endpoint field '{key}'", 404)
        definitions.setdefault(key, declared[key])
    return await endpoint_repo.apply_managed_fields(extension, type_ids, definitions)
