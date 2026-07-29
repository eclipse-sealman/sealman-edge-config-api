import uuid
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy import Integer, Text, ForeignKey, func
from db.base import Base


class DeviceNetworkRange(Base):
    """
    The widest network range ever computed for a device's automatic scan (see
    get_network_scan_range.py). Persisted so the range only ever widens as new endpoint IPs
    become known, never shrinks just because something that anchored it was edited or removed.
    """

    __tablename__ = "device_network_ranges"

    device_id: Mapped[str] = mapped_column(
        Text, ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True
    )
    network_definition: Mapped[str] = mapped_column(Text, nullable=False)
    subnet_mask: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class DeviceExtraScanRange(Base):
    """
    An additional network range a user explicitly added to scan for a device, on top of the
    automatic baseline above. A device can have any number of these (unlike DeviceNetworkRange,
    which is one row per device) - there's no default entry, the list starts empty.
    """

    __tablename__ = "device_extra_scan_ranges"

    range_id: Mapped[str] = mapped_column(
        Text, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    device_id: Mapped[str] = mapped_column(
        Text, ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False
    )
    network_definition: Mapped[str] = mapped_column(Text, nullable=False)
    subnet_mask: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
