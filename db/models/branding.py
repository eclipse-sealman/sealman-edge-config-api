from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import TIMESTAMP, JSONB
from sqlalchemy import CheckConstraint, LargeBinary, SmallInteger, Text, func
from db.base import Base


class PlatformBranding(Base):
    """Platform-wide look of the web app (theme + tab title). Always at most one row (id = 1)."""

    __tablename__ = "platform_branding"
    __table_args__ = (CheckConstraint("id = 1", name="platform_branding_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    theme_id: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[dict] = mapped_column(JSONB, nullable=False)
    app_title: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PlatformBrandingAsset(Base):
    """
    Uploaded branding image (logo / favicon). Kept apart from PlatformBranding so the settings
    query never reads image bytes; `content` is deferred and only loaded when serving the image.
    """

    __tablename__ = "platform_branding_assets"
    __table_args__ = (CheckConstraint("kind IN ('logo', 'favicon')", name="platform_branding_assets_kind"),)

    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    etag: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
