from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.platform_functions import PlatformFunctions
from db.registry import register_repository
from db.repos.platform_functions import PlatformFunctionSettings, PlatformFunctionsRepository


@register_repository(PlatformFunctionsRepository)
class SqlAlchemyPlatformFunctionsRepository(PlatformFunctionsRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_functions(self) -> PlatformFunctionSettings:
        row = await self._session.get(PlatformFunctions, 1)
        if row is None:
            return PlatformFunctionSettings()
        return PlatformFunctionSettings(network_scan_enabled=row.network_scan_enabled)

    async def save_functions(self, settings: PlatformFunctionSettings) -> PlatformFunctionSettings:
        stmt = pg_insert(PlatformFunctions).values(id=1, network_scan_enabled=settings.network_scan_enabled)
        stmt = stmt.on_conflict_do_update(
            index_elements=[PlatformFunctions.id],
            set_={
                "network_scan_enabled": stmt.excluded.network_scan_enabled,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()
        self._session.expire_all()
        return await self.get_functions()
