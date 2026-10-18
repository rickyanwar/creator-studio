from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint, func
from app.database import Base


class LogHandled(Base):
    __tablename__ = "log_handled"
    __table_args__ = (
        UniqueConstraint("entry_kind", "entry_id", name="uq_log_handled_kind_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    entry_kind = Column(String(32), nullable=False, index=True)
    entry_id = Column(Integer, nullable=False, index=True)
    handled_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
