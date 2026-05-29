from typing import Any, Optional
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.action import Action
from db.models.role import Role, role_actions
from db.registry import register_repository
from db.repos.role import RoleRepository


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

    async def _fetch_role(self, role_id: UUID) -> Optional[Role]:
        result = await self._session.execute(
            select(Role)
            .options(selectinload(Role.allowed_actions))
            .where(Role.id == role_id)
        )
        return result.scalar_one_or_none()

    async def get(self, role_id: UUID) -> Optional[dict[str, Any]]:
        role = await self._fetch_role(role_id)
        if role is None:
            return None
        return RoleMapper.to_dict(role)

    async def get_by_name(self, name: str) -> Optional[dict[str, Any]]:
        result = await self._session.execute(
            select(Role)
            .options(selectinload(Role.allowed_actions))
            .where(Role.name == name)
        )
        role = result.scalar_one_or_none()
        if role is None:
            return None
        return RoleMapper.to_dict(role)

    async def list_roles(self) -> list[dict[str, Any]]:
        result = await self._session.execute(
            select(Role).options(selectinload(Role.allowed_actions)).order_by(Role.name)
        )
        roles = result.scalars().all()
        return [RoleMapper.to_dict(role) for role in roles]

    async def create_role(self, name: str, description: str | None, action_names: list[str]) -> dict[str, Any]:
        actions = []
        if action_names:
            result = await self._session.execute(
                select(Action).where(Action.name.in_(action_names))
            )
            actions = list(result.scalars().all())

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
    ) -> Optional[dict[str, Any]]:
        role = await self._fetch_role(role_id)
        if role is None:
            return None

        setattr(role, "name", name)
        setattr(role, "description", description)
        await self._session.commit()
        await self._session.refresh(role)

        return RoleMapper.to_dict(role)

    async def add_actions_to_role(
        self,
        role_id: UUID,
        action_names: list[str],
    ) -> Optional[dict[str, Any]]:
        role = await self._fetch_role(role_id)
        if role is None:
            return None

        actions = []
        if action_names:
            result = await self._session.execute(
                select(Action).where(Action.name.in_(action_names))
            )
            actions = list(result.scalars().all())

        role.allowed_actions.extend(actions)
        await self._session.commit()
        await self._session.refresh(role)

        return RoleMapper.to_dict(role)

    async def remove_action_from_role(
        self,
        role_id: UUID,
        action_name: str,
    ) -> Optional[dict[str, Any]]:
        role = await self._fetch_role(role_id)
        if role is None:
            return None

        await self._session.execute(
            delete(role_actions).where(
                role_actions.c.role_id == role_id,
                role_actions.c.action_name == action_name,
            )
        )
        await self._session.commit()

        refreshed_role = await self._fetch_role(role_id)
        if refreshed_role is None:
            return None
        return RoleMapper.to_dict(refreshed_role)

    async def delete_role(self, role_id: UUID) -> None:
        await self._session.execute(delete(Role).where(Role.id == role_id))
        await self._session.commit()

