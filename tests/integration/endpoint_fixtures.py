"""Seeds endpoint/service types and instances through the real repositories (not the HTTP API),
so non-admin permission tests can set up data the calling user may not be allowed to create.
Everything is rolled back with the test transaction (see conftest.py)."""
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

import db.session  # noqa: F401  - imports db/sqlalchemy/*, which registers the repositories
from db.registry import repo_registry
from db.repos.endpoint import EndpointRepository
from db.repos.service import ServiceRepository


def unique_label(prefix: str = "t") -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _endpoint_repo(session: AsyncSession) -> EndpointRepository:
    return repo_registry.get(EndpointRepository)(session)


def _service_repo(session: AsyncSession) -> ServiceRepository:
    return repo_registry.get(ServiceRepository)(session)


async def seed_endpoint_type(
    session: AsyncSession,
    fields: Optional[Dict[str, Any]] = None,
    mapping: Optional[Dict[str, str]] = None,
) -> str:
    created = await _endpoint_repo(session).create_endpoint_type(
        label=unique_label("et"), description=None, fields=fields or {}, mapping=mapping or {}
    )
    return created["type_id"]


async def seed_endpoint(
    session: AsyncSession, device_id: str, type_id: str, data: Optional[Dict[str, Any]] = None
) -> str:
    created = await _endpoint_repo(session).create_endpoint(
        device_id=device_id, type_id=type_id, endpoint_data=data or {}
    )
    return created["endpoint_id"]


async def seed_service_type(
    session: AsyncSession,
    fields: Optional[Dict[str, Any]] = None,
    mapping: Optional[Dict[str, str]] = None,
) -> str:
    created = await _service_repo(session).create_service_type(
        label=unique_label("st"), description=None, fields=fields or {}, mapping=mapping or {}
    )
    return created["type_id"]


async def seed_service(
    session: AsyncSession, endpoint_id: str, type_id: str, data: Optional[Dict[str, Any]] = None
) -> str:
    created = await _service_repo(session).create_service(
        endpoint_id=endpoint_id, type_id=type_id, service_data=data or {}
    )
    return created["service_id"]
