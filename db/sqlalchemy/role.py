from typing import Any, Optional, cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.action import Action
from db.models.role import Role, role_actions
from db.registry import register_repository
from db.repos.role import RoleRepository
from exceptions import APIError


class RoleMapper:
    @staticmethod
    def to_dict(role: Role) -> dict[str, Any]:
        return {
            "id": role.id,
            "name": role.name,
            "description": role.description,
            "actions": sorted([action.name for action in role.allowed_actions]),
        }


@register_repository(RoleRepository)
class SqlAlchemyRoleRepository(RoleRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, role_id: UUID) -> Optional[dict[str, Any]]:
        result = await self._session.execute(
            select(Role)
            .options(selectinload(Role.allowed_actions))
            .where(Role.id == role_id)
        )
        role = result.scalar_one_or_none()
        if role is None:
            return None
        return RoleMapper.to_dict(role)

    async def _get_role_or_raise(self, role_id: UUID) -> Role:
        result = await self._session.execute(
            select(Role)
            .options(selectinload(Role.allowed_actions))
            .where(Role.id == role_id)
        )
        role = result.scalar_one_or_none()

        if role is None:
            raise APIError("Role not found", 404)

        return role

    async def _ensure_role_name_unique(self, name: str, exclude_role_id: UUID | None = None) -> None:
        stmt = select(Role).where(Role.name == name)
        if exclude_role_id is not None:
            stmt = stmt.where(Role.id != exclude_role_id)

        existing_role = (await self._session.execute(stmt)).scalar_one_or_none()
        if existing_role is not None:
            raise APIError(f"Role name '{name}' already exists", 409)

    async def _resolve_actions(self, action_names: list[str]) -> list[Action]:
        """Validate action_names and return the corresponding Action objects.
        Raises APIError on duplicates or unknown names."""
        if len(action_names) != len(set(action_names)):
            raise APIError("Duplicate action names in request", 400)

        if not action_names:
            return []

        result = await self._session.execute(
            select(Action).where(Action.name.in_(action_names))
        )
        actions = result.scalars().all()

        resolved_action_names: set[str] = {cast(str, a.name) for a in actions}
        missing = sorted(set(action_names) - resolved_action_names)
        if missing:
            raise APIError(f"Actions not found: {', '.join(missing)}", 404)

        return list(actions)

    async def list_roles(self) -> list[dict[str, Any]]:
        result = await self._session.execute(
            select(Role).options(selectinload(Role.allowed_actions)).order_by(Role.name)
        )
        roles = result.scalars().all()
        return [RoleMapper.to_dict(role) for role in roles]

    async def create_role(self, name: str, description: str | None, action_names: list[str]) -> dict[str, Any]:
        await self._ensure_role_name_unique(name)

        actions = await self._resolve_actions(action_names)

        role = Role(name=name, description=description)
        role.allowed_actions = actions
        self._session.add(role)
        await self._session.commit()
        await self._session.refresh(role)

        result = await self._session.execute(
            select(Role)
            .options(selectinload(Role.allowed_actions))
            .where(Role.id == role.id)
        )
        return RoleMapper.to_dict(result.scalar_one())

    async def update_role(
        self,
        role_id: UUID,
        name: str,
        description: str | None,
    ) -> dict[str, Any]:
        role = await self._get_role_or_raise(role_id)
        await self._ensure_role_name_unique(name, exclude_role_id=role_id)

        setattr(role, "name", name)
        setattr(role, "description", description)
        await self._session.commit()
        await self._session.refresh(role)

        return RoleMapper.to_dict(role)

    async def add_actions_to_role(
        self,
        role_id: UUID,
        action_names: list[str],
    ) -> dict[str, Any]:
        role = await self._get_role_or_raise(role_id)

        actions = await self._resolve_actions(action_names)

        assigned_action_names: set[str] = {
            cast(str, action.name) for action in role.allowed_actions
        }
        requested_action_names: set[str] = {cast(str, a.name) for a in actions}
        conflicting_actions = sorted(requested_action_names & assigned_action_names)
        if conflicting_actions:
            raise APIError(
                f"Actions already assigned to role: {', '.join(conflicting_actions)}",
                409,
            )

        role.allowed_actions.extend(actions)
        await self._session.commit()
        await self._session.refresh(role)

        return RoleMapper.to_dict(role)

    async def remove_action_from_role(
        self,
        role_id: UUID,
        action_name: str,
    ) -> dict[str, Any]:
        role = await self._get_role_or_raise(role_id)

        assigned_action_names = {action.name for action in role.allowed_actions}
        if action_name not in assigned_action_names:
            raise APIError("Action is not assigned to role", 404)

        await self._session.execute(
            delete(role_actions).where(
                role_actions.c.role_id == role_id,
                role_actions.c.action_name == action_name,
            )
        )
        await self._session.commit()

        refreshed_role = await self._get_role_or_raise(role_id)
        return RoleMapper.to_dict(refreshed_role)
