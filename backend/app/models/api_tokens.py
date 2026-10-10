from sqlalchemy import Column, Integer, String, JSON, DateTime, text
from app.database import Base

class ApiToken(Base):
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    scopes = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=text("now()"))
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
