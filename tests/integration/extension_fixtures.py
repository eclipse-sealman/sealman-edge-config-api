"""Shared helpers for the extension-fields integration tests."""
from datetime import datetime
from typing import Dict, Optional, Tuple
from uuid import uuid4

from sqlalchemy import update

from db.models.endpoint import Endpoint
from db.registry import repo_registry
from db.repos.extension import ExtensionRepository
from extensions.security import hash_key

STRING_FIELD = {"type": "string", "label": "Serial number"}


def unique_extension_name(prefix: str = "inventory") -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def manifest(name: str, endpoint_fields: Optional[Dict] = None) -> dict:
    body = {"schema_version": 1, "name": name, "upstreams": {}, "routes": []}
    if endpoint_fields is not None:
        body["endpoint_fields"] = endpoint_fields
    return body


async def seed_extension(session, endpoint_fields: Dict[str, Dict], enabled: bool = True) -> str:
    """Creates an extension row directly through the repository and returns its unique name."""
    name = unique_extension_name()
    repo = repo_registry.get(ExtensionRepository)(session)
    await repo.create_extension(name, "", 1)
    await repo.set_endpoint_fields(name, endpoint_fields)
    await repo.set_enabled(name, enabled)
    return name


async def seed_keyed_extension(
    session, endpoint_fields: Dict[str, Dict], enabled: bool = True
) -> Tuple[str, str]:
    """Like `seed_extension`, but also stores an internal key. Returns (name, raw_key)."""
    name = await seed_extension(session, endpoint_fields, enabled=enabled)
    raw_key = f"key-{uuid4().hex}"
    repo = repo_registry.get(ExtensionRepository)(session)
    await repo.set_internal_key_hash(name, hash_key(raw_key))
    return name, raw_key


async def set_endpoint_created_at(session, endpoint_id: str, value: datetime) -> None:
    """Sets `created_at` explicitly: inside one test transaction every now() is the same instant."""
    await session.execute(
        update(Endpoint).where(Endpoint.endpoint_id == endpoint_id).values(created_at=value)
    )
    await session.flush()
