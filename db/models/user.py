from sqlalchemy import Boolean, Column, Text
from sqlalchemy.orm import relationship

from db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Text, primary_key=True)
    preferred_username = Column(Text, nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False)
    is_new = Column(Boolean, nullable=False, default=True)

    teams = relationship("Team", secondary="team_assigned_users", back_populates="users")