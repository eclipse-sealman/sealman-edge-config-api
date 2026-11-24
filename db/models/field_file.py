from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, LargeBinary, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class FieldFile(Base):
    """
    A file (certificate, private key, CRL) held by a file field of an endpoint, service or device.
    The owner's data holds only a reference object. The owner is addressed by kind and id without
    a foreign key (three owner tables), so rows left behind are removed by `delete_orphans`.
    `content` is encrypted and deferred, so listing queries never read it.
    """

    __tablename__ = "field_files"
    __table_args__ = (
        UniqueConstraint("owner_kind", "owner_id", "field", name="field_files_owner_field"),
        CheckConstraint("owner_kind IN ('endpoint', 'service', 'device')", name="field_files_owner_kind"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_kind: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    secret: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    not_after: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    uploaded_by: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
