import enum
from sqlalchemy import Column, Integer, String, DateTime, Enum, Boolean, func
from app.database import Base


class BurnerStatus(str, enum.Enum):
    active = "active"
    challenged = "challenged"
    rate_limited = "rate_limited"
    banned = "banned"


class BurnerAccount(Base):
    __tablename__ = "burner_accounts"

    id = Column(Integer, primary_key=True, index=True)
    ig_username = Column(String(64), unique=True, nullable=False, index=True)
    encrypted_password = Column(String(512), nullable=False)
    encrypted_session = Column(String(4096), nullable=True)  # JSON session blob
    proxy_url = Column(String(256), nullable=True)  # http://user:pass@host:port
    status = Column(Enum(BurnerStatus), default=BurnerStatus.active, nullable=False)
    requests_today = Column(Integer, default=0, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    cooldown_until = Column(DateTime, nullable=True)
    last_error = Column(String(512), nullable=True)
    story_enabled = Column(Boolean, default=False, nullable=False)
    last_story_at = Column(DateTime, nullable=True)
    comment_enabled = Column(Boolean, default=False, nullable=False)
    last_comment_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
