from sqlalchemy import Boolean, Column, Text
from sqlalchemy.orm import relationship

from db.base import Base


class Action(Base):
    __tablename__ = "actions"

    name = Column(Text, primary_key=True)
    description = Column(Text)
    is_global = Column(Boolean)
    
    roles = relationship("Role", secondary="role_actions", viewonly=True)
