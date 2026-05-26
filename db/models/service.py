from sqlalchemy import Column, Text, func, ForeignKey
from sqlalchemy.dialects.postgresql import TIMESTAMP, JSONB
from sqlalchemy.orm import relationship
from db.base import Base


class ServiceType(Base):
    """
    A description of a service representing a capabilities an endpoint might have
    """

    __tablename__ = "service_types"

    type_id = Column(Text, primary_key=True)
    label = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    fields = Column(JSONB, nullable=False, default=dict)
    created_at = Column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    services = relationship("Service", back_populates="type", lazy="noload")


class Service(Base):
    """
    A concrete service holding only its relevant data
    """

    __tablename__ = "services"

    service_id = Column(Text, primary_key=True)
    type_id = Column(Text, ForeignKey("service_types.type_id"), nullable=False)
    service_data = Column(JSONB, nullable=False, default=dict)
    created_at = Column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    type = relationship("ServiceType", back_populates="services", lazy="noload")
