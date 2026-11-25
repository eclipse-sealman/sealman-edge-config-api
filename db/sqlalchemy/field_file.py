from datetime import datetime, timezone
from typing import Collection, Dict, Optional
from uuid import uuid4

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from db.models.device import Device
from db.models.endpoint import Endpoint
from db.models.field_file import FieldFile as FieldFileRow
from db.merge import is_file_ref
from db.models.service import Service
from db.registry import register_repository
from db.repos.field_file import FieldFile, FieldFileRepository
from exceptions import APIError

# owner kind -> (model, id column, data column). The kinds `template` and `module` are not here: they have
# no row to lock and no data to write, and `delete_orphans` leaves their files alone.
_OWNERS = {
    "endpoint": (Endpoint, "endpoint_id", "endpoint_data"),
    "service": (Service, "service_id", "service_data"),
    "device": (Device, "device_id", "device_data"),
}


def _to_file(row: FieldFileRow) -> FieldFile:
    return FieldFile(
        id=row.id, owner_kind=row.owner_kind, owner_id=row.owner_id, field=row.field, name=row.name,
        content=row.content, secret=row.secret, sha256=row.sha256, kind=row.kind, subject=row.subject,
        not_after=row.not_after, uploaded_by=row.uploaded_by, uploaded_at=row.uploaded_at,
    )


@register_repository(FieldFileRepository)
class SqlAlchemyFieldFileRepository(FieldFileRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _lock_owner(self, owner_kind: str, owner_id: str):
        model, id_column, _ = _OWNERS[owner_kind]
        result = await self._session.execute(
            select(model).where(getattr(model, id_column) == owner_id).with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def _lock_key(self, owner_kind: str, owner_id: str, field: str) -> None:
        """Serialises writers of one file without an owner row to lock (until the commit)."""
        await self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(f"{owner_kind}/{owner_id}/{field}", 0))))

    async def _delete_row(self, owner_kind: str, owner_id: str, field: str) -> bool:
        result = await self._session.execute(
            delete(FieldFileRow).where(
                FieldFileRow.owner_kind == owner_kind,
                FieldFileRow.owner_id == owner_id,
                FieldFileRow.field == field,
            )
        )
        return result.rowcount > 0

    async def put(
        self,
        owner_kind: str,
        owner_id: str,
        field: str,
        *,
        name: str,
        content: bytes,
        secret: bool,
        sha256: str,
        kind: str,
        subject: Optional[str],
        not_after: Optional[datetime],
        uploaded_by: str,
    ) -> FieldFile:
        owner = None
        if owner_kind in _OWNERS:
            owner = await self._lock_owner(owner_kind, owner_id)
            if owner is None:
                raise APIError(f"{owner_kind.capitalize()} '{owner_id}' not found", 404)
        else:
            await self._lock_key(owner_kind, owner_id, field)
        await self._delete_row(owner_kind, owner_id, field)
        row = FieldFileRow(
            id=str(uuid4()), owner_kind=owner_kind, owner_id=owner_id, field=field, name=name,
            content=content, secret=secret, sha256=sha256, kind=kind, subject=subject,
            not_after=not_after, uploaded_by=uploaded_by, uploaded_at=datetime.now(timezone.utc),
        )
        self._session.add(row)
        stored = _to_file(row)
        if owner is not None:
            data_column = _OWNERS[owner_kind][2]
            # A new dict, so the JSONB change is detected.
            setattr(owner, data_column, {**(getattr(owner, data_column) or {}), field: stored.reference()})
        await self._session.commit()
        return stored

    async def remove(self, owner_kind: str, owner_id: str, field: str) -> bool:
        owner = None
        if owner_kind in _OWNERS:
            owner = await self._lock_owner(owner_kind, owner_id)
        else:
            await self._lock_key(owner_kind, owner_id, field)
        removed = await self._delete_row(owner_kind, owner_id, field)
        if owner is not None:
            data_column = _OWNERS[owner_kind][2]
            data = getattr(owner, data_column) or {}
            # Only a reference is dropped, so a plain value of another field type stays.
            if removed or is_file_ref(data.get(field)):
                setattr(owner, data_column, {k: v for k, v in data.items() if k != field})
        await self._session.commit()
        return removed

    async def copy_owner(
        self, owner_kind: str, from_id: str, to_id: str, fields: Optional[Collection[str]] = None
    ) -> Dict[str, FieldFile]:
        query = (select(FieldFileRow).options(undefer(FieldFileRow.content))
                 .where(FieldFileRow.owner_kind == owner_kind, FieldFileRow.owner_id == from_id))
        if fields is not None:
            query = query.where(FieldFileRow.field.in_(list(fields)))
        result = await self._session.execute(query)
        copies: Dict[str, FieldFile] = {}
        for row in result.scalars().all():
            copy = FieldFileRow(
                id=str(uuid4()), owner_kind=owner_kind, owner_id=to_id, field=row.field, name=row.name,
                content=row.content, secret=row.secret, sha256=row.sha256, kind=row.kind, subject=row.subject,
                not_after=row.not_after, uploaded_by=row.uploaded_by, uploaded_at=row.uploaded_at,
            )
            self._session.add(copy)
            copies[row.field] = _to_file(copy)
        await self._session.commit()
        return copies

    async def mark_secret(self, owner_kind: str, owner_id: str, field: str) -> None:
        await self._session.execute(
            update(FieldFileRow).where(
                FieldFileRow.owner_kind == owner_kind,
                FieldFileRow.owner_id == owner_id,
                FieldFileRow.field == field,
            ).values(secret=True)
        )
        await self._session.commit()

    async def remove_except(self, owner_kind: str, owner_id: str, keep_fields: Collection[str]) -> int:
        result = await self._session.execute(
            delete(FieldFileRow).where(
                FieldFileRow.owner_kind == owner_kind,
                FieldFileRow.owner_id == owner_id,
                FieldFileRow.field.not_in(list(keep_fields)),
            )
        )
        await self._session.commit()
        return result.rowcount

    async def get(self, owner_kind: str, owner_id: str, field: str) -> Optional[FieldFile]:
        result = await self._session.execute(
            select(FieldFileRow)
            .options(undefer(FieldFileRow.content))
            .where(
                FieldFileRow.owner_kind == owner_kind,
                FieldFileRow.owner_id == owner_id,
                FieldFileRow.field == field,
            )
        )
        row = result.scalar_one_or_none()
        return _to_file(row) if row else None

    async def get_many(self, ids: Collection[str]) -> Dict[str, FieldFile]:
        if not ids:
            return {}
        result = await self._session.execute(
            select(FieldFileRow).options(undefer(FieldFileRow.content)).where(FieldFileRow.id.in_(list(ids)))
        )
        return {row.id: _to_file(row) for row in result.scalars().all()}

    async def delete_orphans(self) -> int:
        deleted = 0
        for owner_kind, (model, id_column, data_column) in _OWNERS.items():
            referenced = exists().where(
                getattr(model, id_column) == FieldFileRow.owner_id,
                getattr(model, data_column)[FieldFileRow.field]["$file"].astext == FieldFileRow.id,
            )
            result = await self._session.execute(
                delete(FieldFileRow).where(FieldFileRow.owner_kind == owner_kind, ~referenced)
            )
            deleted += result.rowcount
        await self._session.commit()
        return deleted
