import uuid

from sqlalchemy import Column, ForeignKey, String, Table
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from db.base import Base


team_assigned_roles = Table(
    "team_assigned_roles",
    Base.metadata,
    Column("team_id", UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


team_assigned_users = Table(
    "team_assigned_users",
    Base.metadata,
    Column("user_id", String, ForeignKey("users.id"), primary_key=True),
    Column("team_id", UUID(as_uuid=True), ForeignKey("teams.id"), primary_key=True),
)


class Team(Base):
    __tablename__ = "teams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, unique=True, nullable=False)
    scope_id = Column(UUID(as_uuid=True), ForeignKey("scopes.id"), nullable=True)

    scope = relationship("Scope")
    assigned_roles = relationship("Role", secondary=team_assigned_roles, passive_deletes=True)
    users = relationship("User", secondary="team_assigned_users", back_populates="teams")