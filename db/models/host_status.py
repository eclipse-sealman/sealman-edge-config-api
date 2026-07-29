from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy import Integer, Text, ForeignKey
from db.base import Base


class DeviceHostStatus(Base):
    """
    Last observed online/offline/unknown status per (device, ip), so the network overview can
    report a realistic `lastStatusChange` - the time the status last actually differed between
    two scans - instead of "now" on every poll (a fresh scan runs every few seconds).
    """

    __tablename__ = "device_host_status"

    device_id: Mapped[str] = mapped_column(
        Text, ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True
    )
    ip: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class DevicePortStatus(Base):
    """Same as DeviceHostStatus, but per (device, ip, port) for individual service ports."""

    __tablename__ = "device_port_status"

    device_id: Mapped[str] = mapped_column(
        Text, ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True
    )
    ip: Mapped[str] = mapped_column(Text, primary_key=True)
    port: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
