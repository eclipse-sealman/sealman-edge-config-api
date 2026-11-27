from typing import Any, Dict, List, Optional

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.base_deployment import BaseDeployment
from db.registry import register_repository
from db.repos.base_deployment import BaseDeploymentRepository, StoredBaseDeployment


@register_repository(BaseDeploymentRepository)
class SqlAlchemyBaseDeploymentRepository(BaseDeploymentRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self) -> Optional[StoredBaseDeployment]:
        row = await self._session.get(BaseDeployment, 1)
        if row is None:
            return None
        return StoredBaseDeployment(modules=row.modules, manifest=row.manifest, edge_hub_env=row.edge_hub_env)

    async def save(
        self, modules: List[Dict[str, Any]], manifest: Dict[str, Any], edge_hub_env: Dict[str, str]
    ) -> StoredBaseDeployment:
        stmt = pg_insert(BaseDeployment).values(id=1, modules=modules, manifest=manifest, edge_hub_env=edge_hub_env)
        stmt = stmt.on_conflict_do_update(
            index_elements=[BaseDeployment.id],
            set_={
                "modules": stmt.excluded.modules,
                "manifest": stmt.excluded.manifest,
                "edge_hub_env": stmt.excluded.edge_hub_env,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()
        self._session.expire_all()
        return StoredBaseDeployment(modules=modules, manifest=manifest, edge_hub_env=edge_hub_env)

    async def delete(self) -> None:
        await self._session.execute(delete(BaseDeployment))
        await self._session.commit()
