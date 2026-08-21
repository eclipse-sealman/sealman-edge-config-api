from typing import List

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.scan_ports import DeviceScanPort, DefaultScanPort
from db.registry import register_repository
from db.repos.scan_ports import ScanPortsRepository


@register_repository(ScanPortsRepository)
class SqlAlchemyScanPortsRepository(ScanPortsRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_device_ports(self, device_id: str) -> List[int]:
        result = await self._session.execute(
            select(DeviceScanPort.port).where(DeviceScanPort.device_id == device_id)
        )
        return sorted(result.scalars().all())

    async def add_device_ports(self, device_id: str, ports: List[int]) -> None:
        if not ports:
            return
        stmt = pg_insert(DeviceScanPort).values(
            [{"device_id": device_id, "port": port} for port in set(ports)]
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=[DeviceScanPort.device_id, DeviceScanPort.port])
        await self._session.execute(stmt)
        await self._session.commit()

    async def get_default_ports(self) -> List[int]:
        result = await self._session.execute(select(DefaultScanPort.port))
        return sorted(result.scalars().all())

    async def add_default_port(self, port: int) -> None:
        stmt = pg_insert(DefaultScanPort).values(port=port)
        stmt = stmt.on_conflict_do_nothing(index_elements=[DefaultScanPort.port])
        await self._session.execute(stmt)
        await self._session.commit()

    async def remove_default_port(self, port: int) -> None:
        await self._session.execute(delete(DefaultScanPort).where(DefaultScanPort.port == port))
        await self._session.commit()
