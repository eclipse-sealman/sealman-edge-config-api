
from typing import Any, List, Optional, cast

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.action import Action
from db.registry import register_repository
from db.repos.action import ActionRepository


class ActionMapper:
    @staticmethod
    def to_dict(action: Action) -> dict[str, Any]:
        return {
            "name": cast(str, action.name),
            "description": cast(Optional[str], action.description),
            "is_global": cast(Optional[bool], action.is_global),
        }


@register_repository(ActionRepository)
class SQLAlchemyActionRepository(ActionRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list(self) -> List[dict[str, Any]]:
        result = await self._session.execute(select(Action).order_by(Action.name))
        actions = result.scalars().all()
        return [ActionMapper.to_dict(action) for action in actions]
    
    async def get(self, name: str) -> Optional[dict[str, Any]]:
        result = await self._session.execute(
            select(Action).where(Action.name == name)
        )
        action = result.scalar_one_or_none()
        if action is None:
            return None
        return ActionMapper.to_dict(action)