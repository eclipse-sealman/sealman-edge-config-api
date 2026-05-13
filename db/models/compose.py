from sqlalchemy import TIMESTAMP, CheckConstraint, Column, ForeignKey, Text, Boolean, func
from sqlalchemy.dialects.postgresql import JSONB
from db.base import Base


class ComposeDeployment(Base):
    __tablename__ = "compose_deployments"

    name = Column(Text, primary_key=True)
    description = Column(Text)

    request = Column(JSONB, nullable=False)
    content = Column(JSONB, nullable=False)
    sems_compose = Column(JSONB, nullable=False)
    exposed_ports = Column(JSONB, nullable=False)

    landing_page = Column(Boolean, nullable=False, default=False)
    
    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
    )
    
class ActiveDeployment(Base):
    __tablename__ = "active_deployment"

    id = Column(
        Text,
        primary_key=True,
        default="active"
    )

    deployment_name = Column(
        Text,
        ForeignKey("compose_deployments.name", ondelete="CASCADE"),
        nullable=False,
        unique=True
    )

    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(id == "active", name="only_one_active_row"),
    )