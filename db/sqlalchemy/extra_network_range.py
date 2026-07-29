from typing import List, Tuple

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.network_range import DeviceExtraScanRange
from db.registry import register_repository
from db.repos.extra_network_range import ExtraNetworkRangeRepository


@register_repository(ExtraNetworkRangeRepository)
class SqlAlchemyExtraNetworkRangeRepository(ExtraNetworkRangeRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_ranges(self, device_id: str) -> List[Tuple[str, int]]:
        result = await self._session.execute(
            select(DeviceExtraScanRange)
            .where(DeviceExtraScanRange.device_id == device_id)
            .order_by(DeviceExtraScanRange.created_at)
        )
        return [(row.network_definition, row.subnet_mask) for row in result.scalars().all()]

    async def replace_ranges(self, device_id: str, ranges: List[Tuple[str, int]]) -> None:
        await self._session.execute(
            delete(DeviceExtraScanRange).where(DeviceExtraScanRange.device_id == device_id)
        )
        for network_definition, subnet_mask in ranges:
            self._session.add(
                DeviceExtraScanRange(
                    device_id=device_id,
                    network_definition=network_definition,
                    subnet_mask=subnet_mask,
                )
            )
        await self._session.commit()
