import enum

from sqlalchemy import TIMESTAMP, Column, Enum as SQLEnum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class PlatformSettings(Base):
    __tablename__ = "platform"

    name = Column(Text, primary_key=True)
    platform_meta = Column(JSONB, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


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


class PasswordRenewalTaskStatus(str, enum.Enum):
    PENDING = "Pending"
    CANCELED = "Canceled"
    COMPLETED = "Completed"
    ERROR = "Error"


class PasswordRenewalTask(Base):
    __tablename__ = "password_renewal_tasks"

    task_id = Column(String(36), primary_key=True)
    device_id = Column(Text, ForeignKey("devices.device_id"), nullable=False)
    secret_id = Column(Integer, nullable=False)
    scheduled_time = Column(TIMESTAMP(timezone=True), nullable=False)
    status = Column(
        SQLEnum(
            PasswordRenewalTaskStatus,
            name="password_renewal_task_status",
            values_callable=lambda enum_cls: [status.value for status in enum_cls],
        ),
        nullable=False,
        default=PasswordRenewalTaskStatus.PENDING,
    )
    error_count = Column(Integer, nullable=False, default=0)
    latest_error = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_password_renewal_tasks_device_status", "device_id", "status"),
        Index("ix_password_renewal_tasks_status_scheduled_time", "status", "scheduled_time"),
        Index("ix_password_renewal_tasks_created_at", "created_at"),
    )