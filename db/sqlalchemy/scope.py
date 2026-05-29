from typing import Any, List, Optional, cast
from uuid import UUID

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.scope import AccessRule, Scope
from db.models.team import Team
from db.registry import register_repository
from db.repos.scope import ScopeRepository
from exceptions import APIError


class ScopeMapper:
    @staticmethod
    def to_dict(scope: Scope) -> dict[str, Any]:
        return {
            "id": cast(UUID, scope.id),
            "name": cast(str, scope.name),
            "description": cast(Optional[str], scope.description),
            "attr": cast(dict[str, Any], scope.attr),
            "access_rule": cast(AccessRule, scope.access_rule).value,
        }

    @staticmethod
    def team_to_dict(team: Team) -> dict[str, Any]:
        return {
            "id": cast(UUID, team.id),
            "name": cast(str, team.name),
            "scope_id": cast(Optional[UUID], team.scope_id),
        }


@register_repository(ScopeRepository)
class SQLAlchemyScopeRepository(ScopeRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list(self) -> List[dict[str, Any]]:
        result = await self._session.execute(select(Scope).order_by(Scope.name))
        scopes = result.scalars().all()
        return [ScopeMapper.to_dict(scope) for scope in scopes]

    async def get(self, scope_id: UUID) -> Optional[dict[str, Any]]:
        scope = await self._session.get(Scope, scope_id)
        if scope is None:
            return None
        return ScopeMapper.to_dict(scope)

    async def create(
        self,
        name: str,
        attr: dict[str, Any],
        access_rule: str,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        scope = Scope(
            name=name,
            description=description,
            attr=attr,
            access_rule=AccessRule(access_rule),
        )
        self._session.add(scope)
        await self._session.commit()
        await self._session.refresh(scope)
        return ScopeMapper.to_dict(scope)

    async def update(
        self,
        scope_id: UUID,
        name: str,
        attr: dict[str, Any],
        access_rule: str,
        description: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        scope = await self._session.get(Scope, scope_id)
        if scope is None:
            return None

        setattr(scope, "name", name)
        setattr(scope, "description", description)
        setattr(scope, "attr", attr)
        setattr(scope, "access_rule", AccessRule(access_rule))

        await self._session.commit()
        await self._session.refresh(scope)
        return ScopeMapper.to_dict(scope)

    async def delete(self, scope_id: UUID) -> bool:
        teams = await self.list_teams(scope_id)
        if teams:
            raise APIError(
                f"Scope is assigned to {len(teams)} team(s) and cannot be deleted",
                409,
            )
        result = await self._session.execute(delete(Scope).where(Scope.id == scope_id))
        await self._session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def list_teams(self, scope_id: UUID) -> List[dict[str, Any]]:
        result = await self._session.execute(
            select(Team).where(Team.scope_id == scope_id).order_by(Team.name)
        )
        teams = result.scalars().all()
        return [ScopeMapper.team_to_dict(team) for team in teams]

