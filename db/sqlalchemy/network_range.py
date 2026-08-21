from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.network_range import DeviceNetworkRange
from db.registry import register_repository
from db.repos.network_range import NetworkRangeRepository


@register_repository(NetworkRangeRepository)
class SqlAlchemyNetworkRangeRepository(NetworkRangeRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_range(self, device_id: str) -> Optional[Tuple[str, int]]:
        result = await self._session.execute(
            select(DeviceNetworkRange).where(DeviceNetworkRange.device_id == device_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return row.network_definition, row.subnet_mask

    async def set_range(self, device_id: str, network_definition: str, subnet_mask: int) -> None:
        stmt = pg_insert(DeviceNetworkRange).values(
            device_id=device_id,
            network_definition=network_definition,
            subnet_mask=subnet_mask,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[DeviceNetworkRange.device_id],
            set_={
                "network_definition": stmt.excluded.network_definition,
                "subnet_mask": stmt.excluded.subnet_mask,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()
