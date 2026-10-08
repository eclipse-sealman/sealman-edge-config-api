from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from datetime import datetime

from sqlalchemy import delete, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.action import Action
from db.models.extension import (
    Extension,
    ExtensionAction,
    ExtensionRoute,
    ExtensionUpstream,
)
from db.models.role import role_actions
from db.registry import register_repository
from db.repos.extension import ExtensionActionConflictError, ExtensionRepository
from db.transaction import atomic_session, commit_session, release_read_transaction


class ExtensionMapper:
    @staticmethod
    def extension_to_dict(ext: Extension) -> Dict[str, Any]:
        return {
            "name": ext.name,
            "description": ext.description or "",
            "schema_version": ext.schema_version,
            "enabled": bool(ext.enabled),
            "internal_key_hash": ext.internal_key_hash,
            "created_at": ext.created_at,
            "updated_at": ext.updated_at,
        }

    @staticmethod
    def upstream_to_dict(upstream: ExtensionUpstream) -> Dict[str, Any]:
        result = {
            "id": str(upstream.id),
            "key": upstream.key,
            "type": upstream.type,
            "last_checked_at": upstream.last_checked_at,
            "last_status": upstream.last_status,
            "last_detail": upstream.last_detail,
        }
        if upstream.type == "http":
            result.update(
                base_url=upstream.base_url,
                health_path=upstream.health_path,
                version_field=upstream.version_field,
                expected_version=upstream.expected_version,
            )
        elif upstream.type == "iotedge":
            result.update(
                module_name=upstream.module_name,
                health_device_query=upstream.health_device_query,
            )
        return result

    @staticmethod
    def route_to_dict(route: ExtensionRoute, upstream_key: str) -> Dict[str, Any]:
        return {
            "id": str(route.id),
            "upstream": upstream_key,
            "path": route.path,
            "method": route.method,
            "visibility": route.visibility,
            "summary": route.summary,
            "description": route.description,
            "tags": route.tags,
            "deprecated": bool(route.deprecated),
            "status_code": route.status_code,
            "query_params": route.query_params or [],
            "body": route.body,
            "example": route.example,
            "validation_mode": route.validation_mode,
            "body_ref": bool(route.body_ref),
            "required_action": route.required_action,
            "scoped": bool(route.scoped),
            "scope_param": route.scope_param,
            "scope_in": route.scope_in,
            "upstream_path": route.upstream_path,
            "iotedge_operation": route.iotedge_operation,
            "method_name": route.method_name,
        }


@register_repository(ExtensionRepository)
class SqlAlchemyExtensionRepository(ExtensionRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    @asynccontextmanager
    async def atomic(self):
        async with atomic_session(self._session):
            yield

    async def release_connection(self) -> None:
        await release_read_transaction(self._session)

    async def _get_extension_id(self, name: str) -> Optional[Any]:
        """Resolves an extension's surrogate UUID id from its (unique) name, or None if
        no such extension exists. Callers decide whether a missing extension is an error
        (writes) or should just yield an empty result (reads), mirroring how the old
        name-keyed queries silently matched nothing instead of raising."""
        result = await self._session.execute(select(Extension.id).where(Extension.name == name))
        return result.scalar_one_or_none()

    # --- extensions ------------------------------------------------------
    async def extension_exists(self, name: str) -> bool:
        result = await self._session.execute(select(Extension.name).where(Extension.name == name))
        return result.scalar_one_or_none() is not None

    async def create_extension(self, name: str, description: str, schema_version: int) -> Dict[str, Any]:
        if await self.extension_exists(name):
            raise ValueError(f"Extension '{name}' is already registered")

        ext = Extension(
            name=name,
            description=description,
            schema_version=schema_version,
            enabled=False,
            internal_key_hash=None,
        )
        self._session.add(ext)
        await commit_session(self._session)
        await self._session.refresh(ext)
        return ExtensionMapper.extension_to_dict(ext)

    async def get_extension_row(self, name: str) -> Optional[Dict[str, Any]]:
        result = await self._session.execute(select(Extension).where(Extension.name == name))
        ext = result.scalar_one_or_none()
        return ExtensionMapper.extension_to_dict(ext) if ext else None

    async def list_extensions_rows(self) -> List[Dict[str, Any]]:
        result = await self._session.execute(select(Extension).order_by(Extension.name))
        return [ExtensionMapper.extension_to_dict(e) for e in result.scalars().all()]

    async def set_description(self, name: str, description: str) -> None:
        result = await self._session.execute(select(Extension).where(Extension.name == name))
        ext = result.scalar_one()
        ext.description = description
        await commit_session(self._session)

    async def set_schema_version(self, name: str, schema_version: int) -> None:
        result = await self._session.execute(select(Extension).where(Extension.name == name))
        ext = result.scalar_one()
        ext.schema_version = schema_version
        await commit_session(self._session)

    async def set_enabled(self, name: str, enabled: bool) -> None:
        result = await self._session.execute(select(Extension).where(Extension.name == name))
        ext = result.scalar_one()
        ext.enabled = enabled
        await commit_session(self._session)

    async def set_internal_key_hash(self, name: str, key_hash: str) -> None:
        result = await self._session.execute(select(Extension).where(Extension.name == name))
        ext = result.scalar_one()
        ext.internal_key_hash = key_hash
        await commit_session(self._session)

    async def delete_extension(self, name: str) -> bool:
        result = await self._session.execute(delete(Extension).where(Extension.name == name))
        await commit_session(self._session)
        return result.rowcount > 0

    # --- upstreams + routes ------------------------------------------------
    async def replace_upstreams_and_routes(
        self, name: str, upstreams: List[Dict[str, Any]], routes: List[Dict[str, Any]]
    ) -> None:
        extension_id = await self._get_extension_id(name)
        if extension_id is None:
            raise ValueError(f"Extension '{name}' does not exist")

        # Routes reference upstreams by FK, so drop routes first, then upstreams.
        await self._session.execute(delete(ExtensionRoute).where(ExtensionRoute.extension_id == extension_id))
        await self._session.execute(delete(ExtensionUpstream).where(ExtensionUpstream.extension_id == extension_id))

        key_to_upstream: Dict[str, ExtensionUpstream] = {}
        for upstream in upstreams:
            row = ExtensionUpstream(
                extension_id=extension_id,
                key=upstream["key"],
                type=upstream["type"],
                base_url=upstream.get("base_url"),
                health_path=upstream.get("health_path"),
                version_field=upstream.get("version_field"),
                expected_version=upstream.get("expected_version"),
                module_name=upstream.get("module_name"),
                health_device_query=upstream.get("health_device_query"),
            )
            self._session.add(row)
            key_to_upstream[upstream["key"]] = row

        if key_to_upstream:
            await self._session.flush()  # assign generated upstream ids before routes reference them

        for route in routes:
            iotedge = route.get("iotedge") or {}
            self._session.add(
                ExtensionRoute(
                    extension_id=extension_id,
                    upstream_id=key_to_upstream[route["upstream"]].id,
                    path=route["path"],
                    method=route.get("method") or "GET",
                    visibility=route.get("visibility") or "public",
                    summary=route.get("summary"),
                    description=route.get("description"),
                    tags=route.get("tags"),
                    deprecated=bool(route.get("deprecated")),
                    status_code=route.get("status_code") or 200,
                    query_params=route.get("query_params") or [],
                    body=route.get("body"),
                    example=route.get("example"),
                    validation_mode=route.get("validation_mode"),
                    body_ref=bool(route.get("body_ref")),
                    required_action=route.get("required_action"),
                    scoped=bool(route.get("scoped")),
                    scope_param=route.get("scope_param") or "device_id",
                    scope_in=route.get("scope_in") or "query",
                    upstream_path=route.get("upstream_path"),
                    iotedge_operation=iotedge.get("operation"),
                    method_name=iotedge.get("method_name"),
                )
            )

        await commit_session(self._session)

    async def list_upstreams(self, name: str) -> List[Dict[str, Any]]:
        extension_id = await self._get_extension_id(name)
        if extension_id is None:
            return []
        result = await self._session.execute(
            select(ExtensionUpstream).where(ExtensionUpstream.extension_id == extension_id)
        )
        return [ExtensionMapper.upstream_to_dict(u) for u in result.scalars().all()]

    async def list_routes(self, name: str) -> List[Dict[str, Any]]:
        extension_id = await self._get_extension_id(name)
        if extension_id is None:
            return []
        result = await self._session.execute(
            select(ExtensionRoute, ExtensionUpstream.key)
            .join(ExtensionUpstream, ExtensionUpstream.id == ExtensionRoute.upstream_id)
            .where(ExtensionRoute.extension_id == extension_id)
        )
        return [ExtensionMapper.route_to_dict(route, upstream_key) for route, upstream_key in result.all()]

    async def update_route_validation(self, route_id: str, validation_mode: str, body: Optional[Dict[str, Any]]) -> None:
        result = await self._session.execute(select(ExtensionRoute).where(ExtensionRoute.id == route_id))
        route = result.scalar_one_or_none()
        if route is None:
            return
        route.validation_mode = validation_mode
        route.body = body
        await commit_session(self._session)

    async def record_upstream_health(
        self, upstream_id: str, status: str, detail: Optional[str], checked_at: datetime
    ) -> None:
        result = await self._session.execute(select(ExtensionUpstream).where(ExtensionUpstream.id == upstream_id))
        upstream = result.scalar_one_or_none()
        if upstream is None:
            return
        upstream.last_status = status
        upstream.last_detail = detail
        upstream.last_checked_at = checked_at
        await commit_session(self._session)

    # --- RBAC action provenance -------------------------------------------
    async def upsert_action(self, action_name: str, description: str, is_global: bool) -> None:
        result = await self._session.execute(select(Action).where(Action.name == action_name))
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.description = description
            existing.is_global = is_global
            await commit_session(self._session)
            return
        self._session.add(Action(name=action_name, description=description, is_global=is_global))
        try:
            await commit_session(self._session)
        except IntegrityError as exc:
            raise ExtensionActionConflictError(
                f"Action '{action_name}' was claimed concurrently"
            ) from exc

    async def action_exists(self, action_name: str) -> bool:
        result = await self._session.execute(select(exists().where(Action.name == action_name)))
        return bool(result.scalar())

    async def get_action_owner(self, action_name: str) -> Optional[str]:
        result = await self._session.execute(
            select(Extension.name)
            .join(ExtensionAction, ExtensionAction.extension_id == Extension.id)
            .where(ExtensionAction.action_name == action_name)
        )
        return result.scalar_one_or_none()

    async def record_extension_actions(self, extension_name: str, action_names: List[str]) -> None:
        extension_id = await self._get_extension_id(extension_name)
        if extension_id is None:
            raise ValueError(f"Extension '{extension_name}' does not exist")
        for action_name in action_names:
            self._session.add(ExtensionAction(action_name=action_name, extension_id=extension_id))
        try:
            await commit_session(self._session)
        except IntegrityError as exc:
            raise ExtensionActionConflictError(
                "One or more actions are already owned by another extension"
            ) from exc

    async def clear_extension_actions(self, extension_name: str) -> None:
        extension_id = await self._get_extension_id(extension_name)
        if extension_id is None:
            return
        await self._session.execute(
            delete(ExtensionAction).where(ExtensionAction.extension_id == extension_id)
        )
        await commit_session(self._session)

    async def list_extension_actions(self, extension_name: str) -> List[str]:
        extension_id = await self._get_extension_id(extension_name)
        if extension_id is None:
            return []
        result = await self._session.execute(
            select(ExtensionAction.action_name).where(ExtensionAction.extension_id == extension_id)
        )
        return [row[0] for row in result.all()]

    async def list_extension_action_specs(self, extension_name: str) -> List[Dict[str, str]]:
        extension_id = await self._get_extension_id(extension_name)
        if extension_id is None:
            return []
        result = await self._session.execute(
            select(Action.name, Action.description)
            .join(ExtensionAction, ExtensionAction.action_name == Action.name)
            .where(ExtensionAction.extension_id == extension_id)
        )
        return [{"name": row[0], "description": row[1] or ""} for row in result.all()]

    async def is_action_granted_to_any_role(self, action_name: str) -> bool:
        result = await self._session.execute(
            select(exists().where(role_actions.c.action_name == action_name))
        )
        return bool(result.scalar())

    async def delete_orphaned_actions(self, action_names: List[str]) -> None:
        for action_name in action_names:
            still_owned = await self._session.execute(
                select(exists().where(ExtensionAction.action_name == action_name))
            )
            if still_owned.scalar():
                continue
            if await self.is_action_granted_to_any_role(action_name):
                continue
            await self._session.execute(delete(Action).where(Action.name == action_name))
        await commit_session(self._session)
