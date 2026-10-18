from sqlalchemy import Column, Integer, String, Boolean, DateTime, func
from app.database import Base


class AuthEvent(Base):
    __tablename__ = "auth_events"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    username = Column(String(128), nullable=True)
    success = Column(Boolean, nullable=False)
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(256), nullable=True)
