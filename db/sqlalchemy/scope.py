from typing import Any, List, Optional, cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.scope import AccessRule, Scope
from db.models.team import Team
from db.registry import register_repository
from db.repos.scope import ScopeRepository


class ScopeMapper:
    @staticmethod
    def to_dict(scope: Scope, team_usage_count: int = 0) -> dict[str, Any]:
        return {
            "id": cast(UUID, scope.id),
            "name": cast(str, scope.name),
            "description": cast(Optional[str], scope.description),
            "attr": cast(dict[str, Any], scope.attr),
            "access_rule": cast(AccessRule, scope.access_rule).value,
            "team_usage_count": team_usage_count,
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
        team_counts_subquery = (
            select(
                Team.scope_id.label("scope_id"),
                func.count(Team.id).label("team_usage_count"),
            )
            .group_by(Team.scope_id)
            .subquery()
        )

        result = await self._session.execute(
            select(
                Scope,
                func.coalesce(team_counts_subquery.c.team_usage_count, 0).label(
                    "team_usage_count"
                ),
            )
            .outerjoin(team_counts_subquery, team_counts_subquery.c.scope_id == Scope.id)
            .order_by(Scope.name)
        )
        scope_rows = result.all()
        return [
            ScopeMapper.to_dict(scope, team_usage_count=cast(int, team_usage_count))
            for scope, team_usage_count in scope_rows
        ]

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

    async def delete(self, scope_id: UUID) -> None:
        await self._session.execute(delete(Scope).where(Scope.id == scope_id))
        await self._session.commit()

    async def list_teams(self, scope_id: UUID) -> List[dict[str, Any]]:
        result = await self._session.execute(
            select(Team).where(Team.scope_id == scope_id).order_by(Team.name)
        )
        teams = result.scalars().all()
        return [ScopeMapper.team_to_dict(team) for team in teams]

