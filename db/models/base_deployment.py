from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import CheckConstraint, SmallInteger, func, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base

# What the shipped manifest template sets, so rows saved before the column existed keep their behavior.
EDGE_HUB_ENV_DEFAULT = text(
    "'{\"MaxUpstreamBatchSize\": \"100\", \"RuntimeLogLevel\": \"warning\", \"UpstreamProtocol\": \"AmqpWs\"}'::jsonb"
)


class BaseDeployment(Base):
    """The IoT Hub base deployment: editable modules plus the fully rendered manifest. At most one row (id = 1)."""

    __tablename__ = "base_deployment"
    __table_args__ = (CheckConstraint("id = 1", name="base_deployment_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    modules: Mapped[List[Dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    manifest: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    edge_hub_env: Mapped[Dict[str, str]] = mapped_column(JSONB, nullable=False, server_default=EDGE_HUB_ENV_DEFAULT)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
