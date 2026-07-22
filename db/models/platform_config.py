from sqlalchemy import TIMESTAMP, Column, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from db.base import Base


class PlatformConfig(Base):
    """
    Global, singleton-row configuration blobs (device template variables,
    device-endpoint types/service ports, selected SEMS templates) that used
    to live in an env file / Azure Blob Storage.
    """

    __tablename__ = "platform_config"

    name = Column(Text, primary_key=True)
    device_template_config = Column(JSONB, nullable=False, default=dict)
    endpoint_types = Column(JSONB, nullable=False, default=list)
    service_ports = Column(JSONB, nullable=False, default=list)
    selected_templates = Column(JSONB, nullable=False, default=list)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
