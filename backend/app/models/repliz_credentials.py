from sqlalchemy import Column, Integer, String, DateTime, func
from app.database import Base


class ReplizCredentials(Base):
    __tablename__ = "repliz_credentials"

    id = Column(Integer, primary_key=True, index=True)
    access_key_encrypted = Column(String(512), nullable=False)
    secret_key_encrypted = Column(String(512), nullable=False)
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
