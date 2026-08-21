from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy import Integer, Text, ForeignKey, func
from db.base import Base


class DeviceScanPort(Base):
    """
    An extra port a user has added for a specific device (via the Overview page's "Scan Network"
    dialog), on top of the global default ports and this device's own configured services'
    ports. Persisted so it's included in every automatic scan for this device going forward,
    not just the one-time scan that first requested it.
    """

    __tablename__ = "device_scan_ports"

    device_id: Mapped[str] = mapped_column(
        Text, ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True
    )
    port: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class DefaultScanPort(Base):
    """
    A port that should always be scanned on every device at minimum, regardless of what's
    configured on any one of them - managed from the global Settings page (Service Types
    section). Seeded from every existing service type's configured default port, and from then
    on kept in sync whenever a new service type is created with one (see
    routers/service/router.py:create_service_type), while also remaining directly editable by an
    admin independent of any single service type.
    """

    __tablename__ = "default_scan_ports"

    port: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
