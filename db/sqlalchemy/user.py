from typing import Any, List, Optional, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models.user import User
from db.registry import register_repository
from db.repos.user import UserRepository
from db.sqlalchemy.team import TeamMapper


class UserMapper:
    @staticmethod
    def to_dict(user: User, include_teams: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": cast(str, user.id),
            "preferred_username": cast(str, user.preferred_username),
            "is_admin": cast(bool, user.is_admin),
            "is_new": cast(bool, user.is_new),
        }
        if include_teams:
            teams = sorted(user.teams, key=lambda item: item.name)
            payload["teams"] = [TeamMapper.to_dict(team) for team in teams]
        return payload


@register_repository(UserRepository)
class SQLAlchemyUserRepository(UserRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _get_user_with_teams(self, user_id: str) -> Optional[User]:
        result = await self._session.execute(
            select(User)
            .options(selectinload(User.teams))
            .where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def list(self, is_new: Optional[bool] = None) -> List[dict[str, Any]]:
        query = (
            select(User)
            .options(selectinload(User.teams))
            .order_by(User.preferred_username)
        )
        if is_new is not None:
            query = query.where(User.is_new == is_new)

        result = await self._session.execute(query)
        users = result.scalars().all()
        return [UserMapper.to_dict(user, include_teams=True) for user in users]

    async def get(self, user_id: str) -> Optional[dict[str, Any]]:
        user = await self._get_user_with_teams(user_id)
        if user is None:
            return None
        return UserMapper.to_dict(user, include_teams=True)

    async def get_by_ids(self, user_ids: List[str]) -> List[dict[str, Any]]:
        if not user_ids:
            return []

        result = await self._session.execute(
            select(User)
            .options(selectinload(User.teams))
            .where(User.id.in_(user_ids))
        )
        users = result.scalars().all()
        return [UserMapper.to_dict(user, include_teams=True) for user in users]

    async def create(
        self,
        user_id: str,
        preferred_username: str,
        is_admin: bool = False,
        is_new: bool = True,
    ) -> dict[str, Any]:
        user = User(
            id=user_id,
            preferred_username=preferred_username,
            is_admin=is_admin,
            is_new=is_new,
        )
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return UserMapper.to_dict(user, include_teams=False)

    async def update(
        self,
        user_id: str,
        preferred_username: str,
        is_admin: bool,
        is_new: bool,
    ) -> Optional[dict[str, Any]]:
        user = await self._session.get(User, user_id)
        if user is None:
            return None

        setattr(user, "preferred_username", preferred_username)
        setattr(user, "is_admin", is_admin)
        setattr(user, "is_new", is_new)

        await self._session.commit()
        await self._session.refresh(user)
        return UserMapper.to_dict(user, include_teams=False)

    async def delete(self, user_id: str) -> bool:
        result = await self._session.execute(delete(User).where(User.id == user_id))
        await self._session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def ensure_exists(
        self,
        user_id: str,
        preferred_username: str,
        is_admin: bool = False,
    ) -> None:
        stmt = (
            pg_insert(User)
            .values(id=user_id, preferred_username=preferred_username, is_admin=is_admin, is_new=True)
            .on_conflict_do_nothing(index_elements=["id"])
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def list_teams(self, user_id: str) -> List[dict[str, Any]]:
        user = await self._get_user_with_teams(user_id)
        if user is None:
            return []

        teams = sorted(user.teams, key=lambda item: item.name)
        return [TeamMapper.to_dict(team) for team in teams]