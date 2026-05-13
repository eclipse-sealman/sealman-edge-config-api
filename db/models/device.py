from sqlalchemy import TIMESTAMP, Column, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from db.base import Base

class Device(Base):
    __tablename__ = "devices"

    device_id = Column(Text, primary_key=True)
    device_meta = Column(JSONB, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class DeviceSnapshotCache(Base):
    __tablename__ = "device_snapshot_cache"

    cache_key = Column(Text, primary_key=True)
    devices_json = Column(JSONB, nullable=False, default=list)
    device_count = Column(Integer, nullable=False)
    cached_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)