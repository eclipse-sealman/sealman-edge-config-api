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
        in the same commit. Raises APIError(404) when the owner does not exist. Owners of kind `template`
        and `module` have no row or data of their own (the reference lives in a template version or in
        the apply values): only the file row is written, the caller checks that the owner exists."""
        ...

    @abstractmethod
    async def remove(self, owner_kind: str, owner_id: str, field: str) -> bool:
        """Deletes the file and the reference in one commit. False if there was no file."""
        ...

    @abstractmethod
    async def copy_owner(
        self, owner_kind: str, from_id: str, to_id: str, fields: Optional[Collection[str]] = None
    ) -> Dict[str, FieldFile]:
        """Copies the files of one owner (only those of `fields`, if given) to another (new ids, same
        content), by field."""
        ...

    @abstractmethod
    async def mark_secret(self, owner_kind: str, owner_id: str, field: str) -> None:
        """Makes the file secret. A file never goes back to not secret except by a new upload."""
        ...

    @abstractmethod
    async def remove_except(self, owner_kind: str, owner_id: str, keep_fields: Collection[str]) -> int:
        """Deletes the files of the owner whose field is not in `keep_fields`. Returns how many."""
        ...

    @abstractmethod
    async def get(self, owner_kind: str, owner_id: str, field: str) -> Optional[FieldFile]: ...

    @abstractmethod
    async def get_many(self, ids: Collection[str]) -> Dict[str, FieldFile]: ...

    @abstractmethod
    async def remove_device_module_files(self, device_id: str) -> int:
        """Deletes the files uploaded when applying a template to any module of the device (owner kind
        `module`, owner id `<device>/<module>`). Returns how many."""
        ...

    @abstractmethod
    async def delete_orphans(self) -> int:
        """Deletes files whose owner is gone or whose owner's data no longer references them."""
        ...
