from sqlalchemy import Boolean, Column, ForeignKey, Table, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from db.base import Base


user_context_teams = Table(
    "user_context_teams",
    Base.metadata,
    Column("user_id", Text, ForeignKey("user_context.user_id"), primary_key=True),
    Column("team_id", UUID(as_uuid=True), ForeignKey("teams.id"), primary_key=True),
)


class UserContext(Base):
    __tablename__ = "user_context"

    user_id = Column(Text, primary_key=True)
    preferred_username = Column(Text, nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False)
    is_new_user = Column(Boolean, nullable=False, default=True)

    teams = relationship("Team", secondary=user_context_teams, back_populates="users")