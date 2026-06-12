import uuid
from enum import Enum

from sqlalchemy import Column, Enum as SQLAlchemyEnum, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from db.base import Base


class AccessRule(str, Enum):
    ALL = "ALL"
    ANY = "ANY"


class Scope(Base):
    __tablename__ = "scopes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    attr = Column(JSONB, nullable=False)
    access_rule = Column(SQLAlchemyEnum(AccessRule, create_type=False), nullable=False)
