from typing import Any, List, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.session_metric import SessionMetric
from db.models.user import User
from db.registry import register_repository
from db.repos.session_metric import SessionMetricRepository


def _display_name(preferred_username: str) -> str:
    # Only the part before the "@" is shown to other users.
    return preferred_username.split("@")[0]


@register_repository(SessionMetricRepository)
class SqlAlchemySessionMetricRepository(SessionMetricRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_personal(self, user_id: str) -> int:
        result = await self._session.execute(
            select(SessionMetric.metric_value).where(SessionMetric.user_id == user_id)
        )
        return result.scalar_one_or_none() or 0

    async def get_best(self) -> Optional[dict[str, Any]]:
        top = await self.top(1)
        return top[0] if top else None

    async def submit(self, user_id: str, value: int) -> None:
        stmt = pg_insert(SessionMetric).values(user_id=user_id, metric_value=value)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SessionMetric.user_id],
            set_={"metric_value": stmt.excluded.metric_value, "recorded_at": func.now()},
            where=SessionMetric.metric_value < stmt.excluded.metric_value,
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def top(self, limit: int) -> List[dict[str, Any]]:
        result = await self._session.execute(
            select(User.preferred_username, SessionMetric.metric_value)
            .join(User, User.id == SessionMetric.user_id)
            .where(SessionMetric.metric_value > 0)
            .order_by(SessionMetric.metric_value.desc(), SessionMetric.recorded_at.asc())
            .limit(limit)
        )
        return [{"name": _display_name(name), "value": value} for name, value in result.all()]
