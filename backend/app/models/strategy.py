from sqlalchemy import Column, Integer, String, JSON, DateTime, Text, ForeignKey, text
from sqlalchemy.orm import relationship
from app.database import Base

class StrategyRecommendation(Base):
    __tablename__ = "strategy_recommendations"

    id = Column(Integer, primary_key=True, index=True)
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(32), nullable=False)
    title = Column(String(200), nullable=False)
    proposal = Column(JSON, nullable=False)
    rationale = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=True)
    status = Column(String(16), nullable=False, server_default="proposed", index=True)
    source = Column(String(32), nullable=False, server_default="hermes")
    token_id = Column(Integer, ForeignKey("api_tokens.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text("now()"))
    decided_at = Column(DateTime, nullable=True)
    decided_by = Column(String(64), nullable=True)
    applied_at = Column(DateTime, nullable=True)
    apply_error = Column(Text, nullable=True)
    previous_values = Column(JSON, nullable=True)

    fanpage = relationship("TargetFanpage")


class FanpageContentMemory(Base):
    __tablename__ = "fanpage_content_memory"

    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="CASCADE"), primary_key=True)
    auto = Column(JSON, nullable=True)
    notes = Column(JSON, nullable=True)
    auto_updated_at = Column(DateTime, nullable=True)
    notes_updated_at = Column(DateTime, nullable=True)

    fanpage = relationship("TargetFanpage")
