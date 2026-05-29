from typing import Any, List, Optional, cast

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models.user_context import UserContext
from db.registry import register_repository
from db.repos.user_context import UserContextRepository
from db.sqlalchemy.team import TeamMapper


class UserContextMapper:
    @staticmethod
    def to_dict(user: UserContext, include_teams: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": cast(str, user.id),
            "preferred_username": cast(str, user.preferred_username),
            "is_admin": cast(bool, user.is_admin),
            "is_new_user": cast(bool, user.is_new_user),
        }
        if include_teams:
            teams = sorted(user.teams, key=lambda item: item.name)
            payload["teams"] = [TeamMapper.to_dict(team) for team in teams]
        return payload


@register_repository(UserContextRepository)
class SQLAlchemyUserContextRepository(UserContextRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _get_user_with_teams(self, user_id: str) -> Optional[UserContext]:
        result = await self._session.execute(
            select(UserContext)
            .options(selectinload(UserContext.teams))
            .where(UserContext.id == user_id)
        )
        return result.scalar_one_or_none()

    async def list(self, is_new_user: Optional[bool] = None) -> List[dict[str, Any]]:
        query = (
            select(UserContext)
            .options(selectinload(UserContext.teams))
            .order_by(UserContext.preferred_username)
        )
        if is_new_user is not None:
            query = query.where(UserContext.is_new_user == is_new_user)

        result = await self._session.execute(query)
        users = result.scalars().all()
        return [UserContextMapper.to_dict(user, include_teams=True) for user in users]

    async def get(self, user_id: str) -> Optional[dict[str, Any]]:
        user = await self._get_user_with_teams(user_id)
        if user is None:
            return None
        return UserContextMapper.to_dict(user, include_teams=True)

    async def create(
        self,
        user_id: str,
        preferred_username: str,
        is_admin: bool = False,
        is_new_user: bool = True,
    ) -> dict[str, Any]:
        user = UserContext(
            id=user_id,
            preferred_username=preferred_username,
            is_admin=is_admin,
            is_new_user=is_new_user,
        )
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return UserContextMapper.to_dict(user, include_teams=False)

    async def update(
        self,
        user_id: str,
        preferred_username: str,
        is_admin: bool,
        is_new_user: bool,
    ) -> Optional[dict[str, Any]]:
        user = await self._session.get(UserContext, user_id)
        if user is None:
            return None

        setattr(user, "preferred_username", preferred_username)
        setattr(user, "is_admin", is_admin)
        setattr(user, "is_new_user", is_new_user)

        await self._session.commit()
        await self._session.refresh(user)
        return UserContextMapper.to_dict(user, include_teams=False)

    async def delete(self, user_id: str) -> bool:
        result = await self._session.execute(delete(UserContext).where(UserContext.id == user_id))
        await self._session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def list_teams(self, user_id: str) -> List[dict[str, Any]]:
        user = await self._get_user_with_teams(user_id)
        if user is None:
            return []

        teams = sorted(user.teams, key=lambda item: item.name)
        return [TeamMapper.to_dict(team) for team in teams]
