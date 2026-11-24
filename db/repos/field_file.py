from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Collection, Dict, Optional


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class FieldFile:
    id: str
    owner_kind: str
    owner_id: str
    field: str
    name: str
    content: bytes  # encrypted
    secret: bool
    sha256: str
    kind: str
    subject: Optional[str]
    not_after: Optional[datetime]
    uploaded_by: str
    uploaded_at: datetime

    def reference(self) -> Dict[str, Any]:
        """The object stored as the field value in the owner's data."""
        return {
            "$file": self.id,
            "name": self.name,
            "kind": self.kind,
            "not_after": _iso_utc(self.not_after) if self.not_after else None,
            "uploaded_at": _iso_utc(self.uploaded_at),
        }


class FieldFileRepository(ABC):
    @abstractmethod
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
        """Replaces the file of the field (new id) and writes its reference into the owner's data
        in the same commit. Raises APIError(404) when the owner does not exist."""
        ...

    @abstractmethod
    async def remove(self, owner_kind: str, owner_id: str, field: str) -> bool:
        """Deletes the file and the reference in one commit. False if there was no file."""
        ...

    @abstractmethod
    async def get(self, owner_kind: str, owner_id: str, field: str) -> Optional[FieldFile]: ...

    @abstractmethod
    async def get_many(self, ids: Collection[str]) -> Dict[str, FieldFile]: ...

    @abstractmethod
    async def delete_orphans(self) -> int:
        """Deletes files whose owner is gone or whose owner's data no longer references them."""
        ...
