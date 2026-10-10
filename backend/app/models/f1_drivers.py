from sqlalchemy import Column, Integer, String, Boolean, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from app.database import Base

class F1Driver(Base):
    __tablename__ = "f1_drivers"

    id = Column(Integer, primary_key=True, index=True)
    season = Column(Integer, nullable=False)
    surname = Column(String(64), nullable=False)
    full_name = Column(String(128))
    number = Column(Integer, nullable=False)
    team_name = Column(String(64), nullable=False)
    team_colour = Column(String(7), nullable=False)
    team_logo_path = Column(String(512), nullable=True)
    verified = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("season", "surname", name="uix_season_surname"),
    )
