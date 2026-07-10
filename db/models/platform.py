from sqlalchemy import TIMESTAMP, Column, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from db.base import Base


class PlatformSettings(Base):
    __tablename__ = "platform"

    name = Column(Text, primary_key=True)
    platform_meta = Column(JSONB, nullable=False, default=dict)
    device_template_config = Column(JSONB, nullable=False, default=dict)
    endpoint_types = Column(JSONB, nullable=False, default=list)
    service_ports = Column(JSONB, nullable=False, default=list)
    selected_templates = Column(JSONB, nullable=False, default=list)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)