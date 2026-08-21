from datetime import datetime
from typing import Dict, Tuple

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.host_status import DeviceHostStatus, DevicePortStatus
from db.registry import register_repository
from db.repos.host_status import HostStatusRepository


@register_repository(HostStatusRepository)
class SqlAlchemyHostStatusRepository(HostStatusRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_host_statuses(self, device_id: str) -> Dict[str, Tuple[str, datetime]]:
        result = await self._session.execute(
            select(DeviceHostStatus).where(DeviceHostStatus.device_id == device_id)
        )
        return {row.ip: (row.status, row.changed_at) for row in result.scalars().all()}

    async def get_port_statuses(self, device_id: str) -> Dict[Tuple[str, int], Tuple[str, datetime]]:
        result = await self._session.execute(
            select(DevicePortStatus).where(DevicePortStatus.device_id == device_id)
        )
        return {(row.ip, row.port): (row.status, row.changed_at) for row in result.scalars().all()}

    async def set_host_statuses(self, device_id: str, statuses: Dict[str, Tuple[str, datetime]]) -> None:
        if not statuses:
            return
        stmt = pg_insert(DeviceHostStatus).values([
            {"device_id": device_id, "ip": ip, "status": status, "changed_at": changed_at}
            for ip, (status, changed_at) in statuses.items()
        ])
        stmt = stmt.on_conflict_do_update(
            index_elements=[DeviceHostStatus.device_id, DeviceHostStatus.ip],
            set_={"status": stmt.excluded.status, "changed_at": stmt.excluded.changed_at},
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def set_port_statuses(self, device_id: str, statuses: Dict[Tuple[str, int], Tuple[str, datetime]]) -> None:
        if not statuses:
            return
        stmt = pg_insert(DevicePortStatus).values([
            {"device_id": device_id, "ip": ip, "port": port, "status": status, "changed_at": changed_at}
            for (ip, port), (status, changed_at) in statuses.items()
        ])
        stmt = stmt.on_conflict_do_update(
            index_elements=[DevicePortStatus.device_id, DevicePortStatus.ip, DevicePortStatus.port],
            set_={"status": stmt.excluded.status, "changed_at": stmt.excluded.changed_at},
        )
        await self._session.execute(stmt)
        await self._session.commit()
