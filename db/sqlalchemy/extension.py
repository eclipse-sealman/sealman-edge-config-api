from typing import Any, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.action import Action
from db.models.extension import (
    Extension,
    ExtensionAction,
    ExtensionDeviceKey,
    ExtensionRoute,
    ExtensionUpstream,
)
from db.registry import register_repository
from db.repos.extension import ExtensionRepository


class ExtensionMapper:
    @staticmethod
    def extension_to_dict(ext: Extension, upstreams: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return {
            "name": ext.name,
            "upstreams": upstreams or {},
            "description": ext.description or "",
            "internal_key_hash": ext.internal_key_hash,
            "created_at": str(ext.created_at) if ext.created_at else None,
        }

    @staticmethod
    def upstream_to_dict(up: ExtensionUpstream) -> dict[str, Any]:
        return {
            "key": up.key,
            "type": up.type,
            "base_url": up.base_url,
            "module_name": up.module_name,
            "expected_version": up.expected_version,
            "health_path": up.health_path,
            "version_field": up.version_field,
            "version_source": up.version_source,
            "twin_version_property": up.twin_version_property,
            "health_method": up.health_method,
        }

    @staticmethod
    def route_to_dict(route: ExtensionRoute) -> dict[str, Any]:
        return {
            "id": str(route.id),
            "extension_name": route.extension_name,
            "upstream": route.upstream_name,
            "upstream_name": route.upstream_name,
            "path": route.path,
            "method": route.method,
            "upstream_path": route.upstream_path,
            "method_name": route.method_name,
            "iotedge_operation": route.iotedge_operation,
            "required_action": route.required_action,
            "visibility": route.visibility,
            "scoped": route.scoped,
            "scope_param": route.scope_param,
            "scope_in": route.scope_in,
            "query_params": route.query_params or [],
            "body": route.body_schema,
            "summary": route.summary,
            "description": route.description,
        }


@register_repository(ExtensionRepository)
class SqlAlchemyExtensionRepository(ExtensionRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    # --- extensions ------------------------------------------------------
    async def list_extensions(self) -> List[dict[str, Any]]:
        ext_result = await self._session.execute(select(Extension).order_by(Extension.name))
        extensions = ext_result.scalars().all()
        if not extensions:
            return []

        ups_result = await self._session.execute(select(ExtensionUpstream))
        ups_by_ext: dict[str, dict[str, Any]] = {}
        for up in ups_result.scalars().all():
            ups_by_ext.setdefault(up.extension_name, {})[up.key] = ExtensionMapper.upstream_to_dict(up)

        return [
            ExtensionMapper.extension_to_dict(e, ups_by_ext.get(e.name, {}))
            for e in extensions
        ]

    async def get_extension(self, name: str) -> Optional[dict[str, Any]]:
        result = await self._session.execute(select(Extension).where(Extension.name == name))
        ext = result.scalar_one_or_none()
        if not ext:
            return None

        ups_result = await self._session.execute(
            select(ExtensionUpstream).where(ExtensionUpstream.extension_name == name)
        )
        upstreams = {
            up.key: ExtensionMapper.upstream_to_dict(up)
            for up in ups_result.scalars().all()
        }
        return ExtensionMapper.extension_to_dict(ext, upstreams)

    async def create_extension(
        self, name: str, upstreams: dict, description: str, internal_key_hash: Optional[str]
    ) -> dict[str, Any]:
        existing = await self._session.execute(select(Extension.name).where(Extension.name == name))
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"Extension '{name}' is already registered")

        ext = Extension(
            name=name, description=description, internal_key_hash=internal_key_hash
        )
        self._session.add(ext)

        ups_dict = {}
        for key, u in upstreams.items():
            u_data = u if isinstance(u, dict) else u.model_dump()
            up_obj = ExtensionUpstream(
                extension_name=name,
                key=key,
                type=u_data.get("type", "http"),
                base_url=u_data.get("base_url"),
                module_name=u_data.get("module_name"),
                expected_version=u_data.get("expected_version"),
                health_path=u_data.get("health_path"),
                version_field=u_data.get("version_field"),
                version_source=u_data.get("version_source"),
                twin_version_property=u_data.get("twin_version_property"),
                health_method=u_data.get("health_method"),
            )
            self._session.add(up_obj)
            ups_dict[key] = ExtensionMapper.upstream_to_dict(up_obj)

        await self._session.commit()
        return ExtensionMapper.extension_to_dict(ext, ups_dict)

    async def delete_extension(self, name: str) -> bool:
        result = await self._session.execute(delete(Extension).where(Extension.name == name))
        await self._session.commit()
        return result.rowcount > 0

    # --- upstreams ---------------------------------------------------------
    async def list_upstreams(self, extension_name: str) -> List[dict[str, Any]]:
        result = await self._session.execute(
            select(ExtensionUpstream)
            .where(ExtensionUpstream.extension_name == extension_name)
            .order_by(ExtensionUpstream.key)
        )
        return [ExtensionMapper.upstream_to_dict(u) for u in result.scalars().all()]

    async def get_upstream(self, extension_name: str, key: str) -> Optional[dict[str, Any]]:
        result = await self._session.execute(
            select(ExtensionUpstream).where(
                ExtensionUpstream.extension_name == extension_name,
                ExtensionUpstream.key == key,
            )
        )
        up = result.scalar_one_or_none()
        return ExtensionMapper.upstream_to_dict(up) if up else None

    # --- routes ------------------------------------------------------------
    async def add_route(self, extension_name: str, route: dict[str, Any]) -> None:
        upstream_name = route.get("upstream_name") or route.get("upstream")
        self._session.add(
            ExtensionRoute(
                extension_name=extension_name,
                upstream_name=upstream_name,
                path=route["path"],
                method=route["method"],
                upstream_path=route.get("upstream_path"),
                method_name=route.get("method_name"),
                iotedge_operation=route.get("iotedge_operation") or "direct_method",
                required_action=route.get("required_action"),
                visibility=route.get("visibility") or "public",
                scoped=bool(route.get("scoped")),
                scope_param=route.get("scope_param") or "device_id",
                scope_in=route.get("scope_in") or "query",
                query_params=route.get("query_params") or [],
                body_schema=route.get("body") if "body" in route else route.get("body_schema"),
                summary=route.get("summary"),
                description=route.get("description"),
            )
        )
        await self._session.commit()

    async def list_routes(self, extension_name: str) -> List[dict[str, Any]]:
        result = await self._session.execute(
            select(ExtensionRoute)
            .where(ExtensionRoute.extension_name == extension_name)
            .order_by(ExtensionRoute.path, ExtensionRoute.method)
        )
        return [ExtensionMapper.route_to_dict(r) for r in result.scalars().all()]

    async def all_routes(self) -> List[dict[str, Any]]:
        result = await self._session.execute(
            select(ExtensionRoute, ExtensionUpstream)
            .outerjoin(
                ExtensionUpstream,
                (ExtensionUpstream.extension_name == ExtensionRoute.extension_name)
                & (ExtensionUpstream.key == ExtensionRoute.upstream_name),
            )
            .order_by(ExtensionRoute.extension_name, ExtensionRoute.path)
        )
        routes = []
        for route, upstream in result.all():
            data = ExtensionMapper.route_to_dict(route)
            if upstream:
                data["transport"] = upstream.type
                data["base_url"] = upstream.base_url
                data["module_name"] = upstream.module_name
                data["expected_version"] = upstream.expected_version
                data["health_path"] = upstream.health_path
                data["version_field"] = upstream.version_field
                data["version_source"] = upstream.version_source
                data["twin_version_property"] = upstream.twin_version_property
                data["health_method"] = upstream.health_method
            else:
                data["transport"] = "http"
                data["base_url"] = None
                data["module_name"] = None
            routes.append(data)
        return routes

    # --- RBAC action provenance --------------------------------------------
    async def ensure_action(self, action_name: str, description: str, is_global: bool = False) -> None:
        existing = await self._session.execute(select(Action.name).where(Action.name == action_name))
        if existing.scalar_one_or_none() is None:
            self._session.add(Action(name=action_name, description=description, is_global=is_global))
            await self._session.commit()

    async def record_extension_action(self, extension_name: str, action_name: str) -> None:
        stmt = (
            pg_insert(ExtensionAction)
            .values(action=action_name, extension_name=extension_name)
            .on_conflict_do_nothing()
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def list_extension_actions(self, extension_name: str) -> List[str]:
        result = await self._session.execute(
            select(ExtensionAction.action)
            .where(ExtensionAction.extension_name == extension_name)
            .order_by(ExtensionAction.action)
        )
        return [row[0] for row in result.all()]

    async def delete_orphaned_actions(self, action_names: List[str]) -> None:
        if not action_names:
            return
        remaining = await self._session.execute(
            select(ExtensionAction.action).where(ExtensionAction.action.in_(action_names))
        )
        still_used = {row[0] for row in remaining.all()}
        orphaned = [a for a in action_names if a not in still_used]
        if orphaned:
            await self._session.execute(delete(Action).where(Action.name.in_(orphaned)))
            await self._session.commit()

    # --- device keys ---------------------------------------------------------
    async def upsert_device_key(
        self, extension_name: str, device_id: str, module_id: Optional[str], key_hash: str
    ) -> None:
        stmt = pg_insert(ExtensionDeviceKey).values(
            extension_name=extension_name, device_id=device_id, module_id=module_id, key_hash=key_hash
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["extension_name", "device_id"],
            set_={"key_hash": key_hash, "module_id": module_id, "created_at": stmt.excluded.created_at},
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def list_device_keys(self, extension_name: str) -> List[dict[str, Any]]:
        result = await self._session.execute(
            select(ExtensionDeviceKey)
            .where(ExtensionDeviceKey.extension_name == extension_name)
            .order_by(ExtensionDeviceKey.device_id)
        )
        return [
            {
                "device_id": k.device_id,
                "module_id": k.module_id,
                "created_at": str(k.created_at) if k.created_at else None,
            }
            for k in result.scalars().all()
        ]

    async def revoke_device_key(self, extension_name: str, device_id: str) -> bool:
        result = await self._session.execute(
            delete(ExtensionDeviceKey).where(
                ExtensionDeviceKey.extension_name == extension_name,
                ExtensionDeviceKey.device_id == device_id,
            )
        )
        await self._session.commit()
        return result.rowcount > 0

    async def resolve_device_key(self, extension_name: str, key_hash: str) -> Optional[str]:
        result = await self._session.execute(
            select(ExtensionDeviceKey.device_id).where(
                ExtensionDeviceKey.extension_name == extension_name,
                ExtensionDeviceKey.key_hash == key_hash,
            )
        )
        row = result.first()
        return row[0] if row else None
