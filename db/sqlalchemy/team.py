from typing import Any, List, Optional, cast
from uuid import UUID

from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models.role import Role
from db.models.team import Team
from db.models.user import User
from db.registry import register_repository
from db.repos.team import TeamRepository
from db.sqlalchemy.role import RoleMapper
from db.sqlalchemy.scope import ScopeMapper


class TeamMapper:
    @staticmethod
    def to_dict(team: Team) -> dict[str, Any]:
        return {
            "id": cast(UUID, team.id),
            "name": cast(str, team.name),
            "scope_id": cast(Optional[UUID], team.scope_id),
        }

    @staticmethod
    def user_to_dict(user: User) -> dict[str, Any]:
        return {
            "id": cast(str, user.id),
            "preferred_username": cast(str, user.preferred_username),
            "is_admin": cast(bool, user.is_admin),
            "is_new": cast(bool, user.is_new),
        }

    @staticmethod
    def to_details_dict(team: Team) -> dict[str, Any]:
        return {
            "id": cast(UUID, team.id),
            "name": cast(str, team.name),
            "scope_id": cast(Optional[UUID], team.scope_id),
            "scope": ScopeMapper.to_dict(team.scope) if team.scope else None,
            "roles": [RoleMapper.to_dict(role) for role in sorted(team.assigned_roles, key=lambda item: item.name)],
            "users": [TeamMapper.user_to_dict(user) for user in sorted(team.users, key=lambda item: item.preferred_username)],
        }


@register_repository(TeamRepository)
class SQLAlchemyTeamRepository(TeamRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _get_team_with_details(self, team_id: UUID) -> Optional[Team]:
        result = await self._session.execute(
            select(Team)
            .options(
                selectinload(Team.scope),
                selectinload(Team.users),
                selectinload(Team.assigned_roles).selectinload(Role.allowed_actions),
            )
            .where(Team.id == team_id)
        )
        return result.scalar_one_or_none()

    async def list(self) -> List[dict[str, Any]]:
        result = await self._session.execute(select(Team).order_by(Team.name))
        teams = result.scalars().all()
        return [TeamMapper.to_dict(team) for team in teams]

    async def get(self, team_id: UUID) -> Optional[dict[str, Any]]:
        team = await self._session.get(Team, team_id)
        if team is None:
            return None
        return TeamMapper.to_dict(team)

    async def get_with_details(self, team_id: UUID) -> Optional[dict[str, Any]]:
        team = await self._get_team_with_details(team_id)
        if team is None:
            return None
        return TeamMapper.to_details_dict(team)

    async def create(
        self,
        name: str,
        scope_id: Optional[UUID] = None,
        user_ids: Optional[List[str]] = None,
        role_ids: Optional[List[UUID]] = None,
    ) -> dict[str, Any]:
        team = Team(name=name, scope_id=scope_id)
        self._session.add(team)

        users: List[User] = []
        if user_ids:
            user_result = await self._session.execute(
                select(User).where(User.id.in_(user_ids))
            )
            users = list(user_result.scalars().all())

        roles: List[Role] = []
        if role_ids:
            role_result = await self._session.execute(
                select(Role).where(Role.id.in_(role_ids))
            )
            roles = list(role_result.scalars().all())

        team.users.extend(users)
        team.assigned_roles.extend(roles)

        await self._session.commit()

        refreshed = await self._get_team_with_details(cast(UUID, team.id))
        if refreshed is None:
            await self._session.refresh(team)
            return TeamMapper.to_dict(team)
        return TeamMapper.to_details_dict(refreshed)

    async def update(
        self,
        team_id: UUID,
        name: str,
        scope_id: Optional[UUID] = None,
    ) -> Optional[dict[str, Any]]:
        team = await self._session.get(Team, team_id)
        if team is None:
            return None

        setattr(team, "name", name)
        setattr(team, "scope_id", scope_id)

        await self._session.commit()
        await self._session.refresh(team)
        return TeamMapper.to_dict(team)

    async def add_user(
        self,
        team_id: UUID,
        user_id: str,
    ) -> Optional[dict[str, Any]]:
        team = await self._get_team_with_details(team_id)
        if team is None:
            return None

        user = await self._session.get(User, user_id)
        if user is None:
            return None

        team.users.append(user)

        await self._session.commit()
        refreshed = await self._get_team_with_details(team_id)
        return TeamMapper.to_details_dict(refreshed) if refreshed else None

    async def remove_user(self, team_id: UUID, user_id: str) -> bool:
        team = await self._get_team_with_details(team_id)
        if team is None:
            return False

        target_user = next((user for user in team.users if cast(str, user.id) == user_id), None)
        if target_user is None:
            return False

        team.users.remove(target_user)
        await self._session.commit()
        return True

    async def add_role(self, team_id: UUID, role_id: UUID) -> Optional[dict[str, Any]]:
        team = await self._get_team_with_details(team_id)
        if team is None:
            return None

        role = await self._session.get(Role, role_id)
        if role is None:
            return None

        team.assigned_roles.append(role)

        await self._session.commit()
        refreshed = await self._get_team_with_details(team_id)
        return TeamMapper.to_details_dict(refreshed) if refreshed else None

    async def remove_role(self, team_id: UUID, role_id: UUID) -> bool:
        team = await self._get_team_with_details(team_id)
        if team is None:
            return False

        target_role = next((role for role in team.assigned_roles if cast(UUID, role.id) == role_id), None)
        if target_role is None:
            return False

        team.assigned_roles.remove(target_role)
        await self._session.commit()
        return True

    async def delete(self, team_id: UUID) -> None:
        await self._session.execute(sa_delete(Team).where(Team.id == team_id))
        await self._session.commit()

