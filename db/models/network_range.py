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
