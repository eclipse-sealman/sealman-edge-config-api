from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.module_config import (
    ModuleConfigTemplateGroup, ModuleConfigTemplateGroupScope, ModuleConfigRevision, ModuleConfigTemplate,
    ModuleConfigTemplateVersion,
)
from db.models.scope import Scope
from db.registry import register_repository
from db.repos.module_config import ModuleConfigRepository, UnknownScope


def module_matches(module: str, module_names: List[str]) -> bool:
    return module in (module_names or [])


def _scope_dict(scope: Scope) -> Dict[str, Any]:
    rule = scope.access_rule
    return {"id": scope.id, "name": scope.name, "attr": dict(scope.attr or {}),
            "access_rule": getattr(rule, "value", rule)}


def _group_dict(row: ModuleConfigTemplateGroup, scopes: List[Dict[str, Any]], template_count: int) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "module_names": list(row.module_names or []),
        "scopes": sorted(scopes, key=lambda s: s["name"]),
        "template_count": template_count,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _template_dict(row: ModuleConfigTemplate, group: Dict[str, Any], latest: Optional[int] = None) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "kind": row.kind,
        "group_id": row.group_id,
        "group_name": group["name"],
        "module_names": group["module_names"],
        "scopes": group["scopes"],
        "derived_from_template_id": row.derived_from_template_id,
        "derived_from_version": row.derived_from_version,
        "archived": row.archived_at is not None,
        "archived_at": row.archived_at,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "latest_version": latest,
    }


def _version_dict(row: ModuleConfigTemplateVersion) -> Dict[str, Any]:
    return {
        "template_id": row.template_id,
        "version": row.version,
        "body": row.body,
        "variables": list(row.variables or []),
        "slots": list(row.slots or []),
        "comment": row.comment,
        "created_by": row.created_by,
        "created_at": row.created_at,
    }


def _revision_dict(row: ModuleConfigRevision) -> Dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in ModuleConfigRevision.__table__.columns}


@register_repository(ModuleConfigRepository)
class SQLAlchemyModuleConfigRepository(ModuleConfigRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _latest_versions(self) -> Dict[int, int]:
        result = await self._session.execute(
            select(ModuleConfigTemplateVersion.template_id, func.max(ModuleConfigTemplateVersion.version))
            .group_by(ModuleConfigTemplateVersion.template_id)
        )
        return {template_id: version for template_id, version in result.all()}

    async def _group_map(self) -> Dict[int, Dict[str, Any]]:
        groups = (await self._session.execute(select(ModuleConfigTemplateGroup))).scalars().all()
        links = (await self._session.execute(
            select(ModuleConfigTemplateGroupScope.group_id, Scope).join(Scope, Scope.id == ModuleConfigTemplateGroupScope.scope_id)
        )).all()
        scopes: Dict[int, List[Dict[str, Any]]] = {}
        for group_id, scope in links:
            scopes.setdefault(group_id, []).append(_scope_dict(scope))
        counts = dict((await self._session.execute(
            select(ModuleConfigTemplate.group_id, func.count()).group_by(ModuleConfigTemplate.group_id)
        )).all())
        return {n.id: _group_dict(n, scopes.get(n.id, []), counts.get(n.id, 0)) for n in groups}

    async def _check_scopes(self, scope_ids: List[UUID]) -> List[UUID]:
        unique = list(dict.fromkeys(scope_ids))
        if unique:
            found = set((await self._session.execute(select(Scope.id).where(Scope.id.in_(unique)))).scalars().all())
            missing = [str(i) for i in unique if i not in found]
            if missing:
                raise UnknownScope(f"Unknown scope: {', '.join(missing)}")
        return unique

    async def list_groups(self) -> List[Dict]:
        return sorted((await self._group_map()).values(), key=lambda n: n["name"].lower())

    async def get_group(self, group_id: int) -> Optional[Dict]:
        return (await self._group_map()).get(group_id)

    async def create_group(self, *, name, description, module_names, scope_ids, created_by) -> Optional[Dict]:
        unique = await self._check_scopes(scope_ids)
        group = ModuleConfigTemplateGroup(name=name, description=description, module_names=module_names,
                                          created_by=created_by)
        try:
            self._session.add(group)
            await self._session.flush()
            self._session.add_all(ModuleConfigTemplateGroupScope(group_id=group.id, scope_id=s) for s in unique)
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            return None
        return await self.get_group(group.id)

    async def update_group(self, group_id, *, name=None, description=None, module_names=None, scope_ids=None):
        group = await self._session.get(ModuleConfigTemplateGroup, group_id)
        if group is None:
            return None
        unique = await self._check_scopes(scope_ids) if scope_ids is not None else None
        if name is not None:
            group.name = name
        if description is not None:
            group.description = description
        if module_names is not None:
            group.module_names = module_names
        group.updated_at = datetime.now(timezone.utc)
        if unique is not None:
            await self._session.execute(
                delete(ModuleConfigTemplateGroupScope).where(ModuleConfigTemplateGroupScope.group_id == group_id))
            self._session.add_all(ModuleConfigTemplateGroupScope(group_id=group_id, scope_id=s) for s in unique)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise ValueError("name taken")
        return await self.get_group(group_id)

    async def delete_group(self, group_id: int) -> str:
        group = await self._session.get(ModuleConfigTemplateGroup, group_id)
        if group is None:
            return "not_found"
        in_use = await self._session.scalar(
            select(func.count()).select_from(ModuleConfigTemplate).where(ModuleConfigTemplate.group_id == group_id))
        if in_use:
            return "in_use"
        await self._session.delete(group)
        await self._session.commit()
        return "deleted"

    async def list_templates(self, module=None, include_archived=False, group_id=None, kind=None) -> List[Dict]:
        query = select(ModuleConfigTemplate).order_by(ModuleConfigTemplate.name)
        if not include_archived:
            query = query.where(ModuleConfigTemplate.archived_at.is_(None))
        if group_id is not None:
            query = query.where(ModuleConfigTemplate.group_id == group_id)
        if kind is not None:
            query = query.where(ModuleConfigTemplate.kind == kind)
        rows = (await self._session.execute(query)).scalars().all()
        latest = await self._latest_versions()
        groups = await self._group_map()
        return [
            _template_dict(row, groups[row.group_id], latest.get(row.id))
            for row in rows
            if module is None or module_matches(module, groups[row.group_id]["module_names"])
        ]

    async def get_template(self, template_id: int) -> Optional[Dict]:
        row = await self._session.get(ModuleConfigTemplate, template_id)
        if row is None:
            return None
        latest = (await self._latest_versions()).get(template_id)
        return _template_dict(row, (await self._group_map())[row.group_id], latest)

    async def create_template(
        self, *, name, description, group_id, body, variables, comment, created_by,
        derived_from_template_id=None, derived_from_version=None, kind="template", slots=None,
    ) -> Optional[Dict]:
        template = ModuleConfigTemplate(
            name=name, description=description, kind=kind, group_id=group_id, created_by=created_by,
            derived_from_template_id=derived_from_template_id, derived_from_version=derived_from_version,
        )
        try:
            self._session.add(template)
            await self._session.flush()
            self._session.add(ModuleConfigTemplateVersion(
                template_id=template.id, version=1, body=body, variables=variables,
                slots=slots or [],
                comment=comment, created_by=created_by,
            ))
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            return None
        return await self.get_template(template.id)

    async def update_template(self, template_id, *, name=None, description=None, group_id=None, archived=None):
        row = await self._session.get(ModuleConfigTemplate, template_id)
        if row is None:
            return None
        if name is not None:
            row.name = name
        if description is not None:
            row.description = description
        if group_id is not None:
            row.group_id = group_id
        row.updated_at = datetime.now(timezone.utc)
        if archived is not None:
            row.archived_at = datetime.now(timezone.utc) if archived else None
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise ValueError("name taken")
        return await self.get_template(template_id)

    async def add_version(self, template_id, *, body, variables, comment, created_by, slots=None) -> Optional[Dict]:
        # Locks the template row so concurrent saves get consecutive versions.
        template = (await self._session.execute(
            select(ModuleConfigTemplate).where(ModuleConfigTemplate.id == template_id).with_for_update()
        )).scalar_one_or_none()
        if template is None:
            return None
        current = await self._session.scalar(
            select(func.max(ModuleConfigTemplateVersion.version))
            .where(ModuleConfigTemplateVersion.template_id == template_id)
        )
        number = (current or 0) + 1
        row = ModuleConfigTemplateVersion(
            template_id=template_id, version=number, body=body, variables=variables,
            slots=slots or [],
            comment=comment, created_by=created_by,
        )
        self._session.add(row)
        template.updated_at = datetime.now(timezone.utc)
        await self._session.commit()
        await self._session.refresh(row)
        return _version_dict(row)

    async def list_versions(self, template_id: int) -> List[Dict]:
        rows = (await self._session.execute(
            select(ModuleConfigTemplateVersion)
            .where(ModuleConfigTemplateVersion.template_id == template_id)
            .order_by(ModuleConfigTemplateVersion.version.desc())
        )).scalars().all()
        return [_version_dict(row) for row in rows]

    async def get_version(self, template_id: int, version: Optional[int] = None) -> Optional[Dict]:
        query = select(ModuleConfigTemplateVersion).where(ModuleConfigTemplateVersion.template_id == template_id)
        if version is None:
            query = query.order_by(ModuleConfigTemplateVersion.version.desc()).limit(1)
        else:
            query = query.where(ModuleConfigTemplateVersion.version == version)
        row = (await self._session.execute(query)).scalar_one_or_none()
        return _version_dict(row) if row else None

    async def add_revision(self, **fields: Any) -> Dict:
        parent = await self._session.scalar(
            select(func.max(ModuleConfigRevision.id)).where(
                ModuleConfigRevision.device_id == fields["device_id"],
                ModuleConfigRevision.module_name == fields["module_name"],
            )
        )
        row = ModuleConfigRevision(parent_revision_id=parent, **fields)
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError:
            # same configId recorded concurrently (e.g. two lazy external syncs)
            await self._session.rollback()
            existing = await self.get_revision_by_config_id(fields["device_id"], fields["module_name"], fields["config_id"])
            if existing is None:
                raise
            if existing["source"] == "external" and fields.get("secret_paths"):
                # recorded as external meanwhile (lazy sync): this write knows where its secrets are
                stored = await self._session.get(ModuleConfigRevision, existing["id"])
                for key, value in fields.items():
                    if key not in ("device_id", "module_name", "config_id"):
                        setattr(stored, key, value)
                await self._session.commit()
                await self._session.refresh(stored)
                return _revision_dict(stored)
            return existing
        await self._session.refresh(row)
        return _revision_dict(row)

    async def delete_revision(self, device_id: str, module_name: str, revision_id: int) -> None:
        await self._session.execute(delete(ModuleConfigRevision).where(
            ModuleConfigRevision.id == revision_id,
            ModuleConfigRevision.device_id == device_id,
            ModuleConfigRevision.module_name == module_name,
        ))
        await self._session.commit()

    async def list_revisions(self, device_id: str, module_name: str, limit: int = 100) -> List[Dict]:
        rows = (await self._session.execute(
            select(ModuleConfigRevision)
            .where(ModuleConfigRevision.device_id == device_id, ModuleConfigRevision.module_name == module_name)
            .order_by(ModuleConfigRevision.id.desc())
            .limit(limit)
        )).scalars().all()
        return [_revision_dict(row) for row in rows]

    async def get_revision(self, device_id: str, module_name: str, revision_id: int) -> Optional[Dict]:
        row = (await self._session.execute(
            select(ModuleConfigRevision).where(
                ModuleConfigRevision.id == revision_id,
                ModuleConfigRevision.device_id == device_id,
                ModuleConfigRevision.module_name == module_name,
            )
        )).scalar_one_or_none()
        return _revision_dict(row) if row else None

    async def get_revision_by_config_id(self, device_id: str, module_name: str, config_id: str) -> Optional[Dict]:
        row = (await self._session.execute(
            select(ModuleConfigRevision).where(
                ModuleConfigRevision.device_id == device_id,
                ModuleConfigRevision.module_name == module_name,
                ModuleConfigRevision.config_id == config_id,
            )
        )).scalar_one_or_none()
        return _revision_dict(row) if row else None
