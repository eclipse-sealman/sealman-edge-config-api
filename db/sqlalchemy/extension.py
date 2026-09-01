from typing import Any, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.action import Action
from db.models.extension import Extension, ExtensionAction, ExtensionDeviceKey, ExtensionRoute
from db.registry import register_repository
from db.repos.extension import ExtensionRepository


class ExtensionMapper:
    @staticmethod
    def extension_to_dict(ext: Extension) -> dict[str, Any]:
        return {
            "name": ext.name,
            "upstreams": ext.upstreams or {},
            "description": ext.description or "",
            "internal_key_hash": ext.internal_key_hash,
            "created_at": str(ext.created_at) if ext.created_at else None,
        }

    @staticmethod
    def route_to_dict(route: ExtensionRoute) -> dict[str, Any]:
        return {
            "id": str(route.id),
            "extension_name": route.extension_name,
            "upstream": route.upstream,
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
        result = await self._session.execute(select(Extension).order_by(Extension.name))
        return [ExtensionMapper.extension_to_dict(e) for e in result.scalars().all()]

    async def get_extension(self, name: str) -> Optional[dict[str, Any]]:
        result = await self._session.execute(select(Extension).where(Extension.name == name))
        ext = result.scalar_one_or_none()
        return ExtensionMapper.extension_to_dict(ext) if ext else None

    async def create_extension(
        self, name: str, upstreams: dict, description: str, internal_key_hash: Optional[str]
    ) -> dict[str, Any]:
        existing = await self._session.execute(select(Extension.name).where(Extension.name == name))
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"Extension '{name}' is already registered")

        ext = Extension(
            name=name, upstreams=upstreams, description=description, internal_key_hash=internal_key_hash
        )
        self._session.add(ext)
        await self._session.commit()
        return ExtensionMapper.extension_to_dict(ext)

    async def delete_extension(self, name: str) -> bool:
        result = await self._session.execute(delete(Extension).where(Extension.name == name))
        await self._session.commit()
        return result.rowcount > 0

    # --- routes ------------------------------------------------------------
    async def add_route(self, extension_name: str, route: dict[str, Any]) -> None:
        self._session.add(
            ExtensionRoute(
                extension_name=extension_name,
                upstream=route["upstream"],
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
                body_schema=route.get("body"),
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
            select(ExtensionRoute, Extension.upstreams)
            .join(Extension, Extension.name == ExtensionRoute.extension_name)
            .order_by(ExtensionRoute.extension_name, ExtensionRoute.path)
        )
        routes = []
        for route, upstreams in result.all():
            data = ExtensionMapper.route_to_dict(route)
            up = (upstreams or {}).get(route.upstream) or {}
            data["transport"] = up.get("type", "http")
            data["base_url"] = up.get("base_url")
            data["module_name"] = up.get("module_name")
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
