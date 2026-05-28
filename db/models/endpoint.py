import uuid
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy import Text, ForeignKey, func
from db.base import Base


class EndpointType(Base):
    """
    A description of an endpoint that represents a physical object connected over the network
    """

    __tablename__ = "endpoint_types"

    type_id: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fields: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Endpoint(Base):
    """
    A concrete endpoint holding only its relevant data
    """

    __tablename__ = "endpoints"

    endpoint_id: Mapped[str] = mapped_column(
        Text, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    device_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("devices.device_id", ondelete="CASCADE"),
        nullable=False,
    )
    type_id: Mapped[str] = mapped_column(
        Text, ForeignKey("endpoint_types.type_id"), nullable=False
    )
    endpoint_data: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    type: Mapped[EndpointType] = relationship("EndpointType", lazy="noload")
