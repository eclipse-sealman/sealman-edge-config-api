from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy import Boolean, CheckConstraint, SmallInteger, func, true
from db.base import Base


class PlatformFunctions(Base):
    """Platform-wide on/off switches for optional functions. Always at most one row (id = 1)."""

    __tablename__ = "platform_functions"
    __table_args__ = (CheckConstraint("id = 1", name="platform_functions_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    network_scan_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
