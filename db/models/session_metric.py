from sqlalchemy import TIMESTAMP, Column, ForeignKey, Integer, Text, func

from db.base import Base


class SessionMetric(Base):
    __tablename__ = "ui_session_metrics"

    user_id = Column(Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    metric_value = Column(Integer, nullable=False, default=0)
    recorded_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
