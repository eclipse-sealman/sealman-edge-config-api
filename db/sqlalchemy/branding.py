from typing import Dict, Optional

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.branding import PlatformBranding, PlatformBrandingAsset
from db.registry import register_repository
from db.repos.branding import BrandingAsset, BrandingRepository, BrandingSettings


@register_repository(BrandingRepository)
class SqlAlchemyBrandingRepository(BrandingRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_branding(self) -> BrandingSettings:
        row = await self._session.get(PlatformBranding, 1)
        etags = await self._session.execute(select(PlatformBrandingAsset.kind, PlatformBrandingAsset.etag))
        settings = BrandingSettings(asset_etags={kind: etag for kind, etag in etags.all()})
        if row is not None:
            settings.theme_id = row.theme_id
            settings.variables = dict(row.variables)
            settings.app_title = row.app_title
        return settings

    async def save_branding(self, theme_id: str, variables: Dict[str, str], app_title: str) -> BrandingSettings:
        stmt = pg_insert(PlatformBranding).values(id=1, theme_id=theme_id, variables=variables, app_title=app_title)
        stmt = stmt.on_conflict_do_update(
            index_elements=[PlatformBranding.id],
            set_={
                "theme_id": stmt.excluded.theme_id,
                "variables": stmt.excluded.variables,
                "app_title": stmt.excluded.app_title,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()
        self._session.expire_all()
        return await self.get_branding()

    async def get_asset(self, kind: str) -> Optional[BrandingAsset]:
        result = await self._session.execute(
            select(PlatformBrandingAsset.content, PlatformBrandingAsset.content_type, PlatformBrandingAsset.etag)
            .where(PlatformBrandingAsset.kind == kind)
        )
        row = result.first()
        return BrandingAsset(content=row.content, content_type=row.content_type, etag=row.etag) if row else None

    async def set_asset(self, kind: str, content: bytes, content_type: str, etag: str) -> None:
        stmt = pg_insert(PlatformBrandingAsset).values(
            kind=kind, content=content, content_type=content_type, etag=etag
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[PlatformBrandingAsset.kind],
            set_={
                "content": stmt.excluded.content,
                "content_type": stmt.excluded.content_type,
                "etag": stmt.excluded.etag,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def delete_asset(self, kind: str) -> None:
        await self._session.execute(delete(PlatformBrandingAsset).where(PlatformBrandingAsset.kind == kind))
        await self._session.commit()
