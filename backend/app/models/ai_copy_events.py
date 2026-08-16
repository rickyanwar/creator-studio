from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from app.database import Base


class AICopyEvent(Base):
    """One record per copy-generation call (news_copywriter.generate_news_copy /
    generate_discussion_copy), tracking whether it succeeded on the first try,
    needed a fallback model/provider, or exhausted every option.

    Exists so a 9Router degradation (e.g. a model silently truncating output —
    see the 2026-08-16 incident where this dropped Mode 2's success rate to
    1.1%) shows up on the Logs dashboard immediately instead of only being
    visible by grepping worker container logs on the VPS.
    """

    __tablename__ = "ai_copy_events"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)

    context = Column(String(32), nullable=False)  # "news_copy" | "discussion_copy"
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="SET NULL"), nullable=True, index=True)
    article_id = Column(Integer, ForeignKey("scraped_articles.id", ondelete="SET NULL"), nullable=True)

    # "success"   — primary router model answered cleanly on the first attempt
    # "recovered" — primary model/provider failed, but a fallback produced a
    #               usable result (the pipeline didn't lose the post, but
    #               9Router is degraded and worth a look)
    # "failed"    — every model and every provider was exhausted; this
    #               (article, fanpage) pair produced no post
    outcome = Column(String(16), nullable=False, index=True)

    models_tried = Column(String(256), nullable=False, default="")
    # "router"/"gemini"/"groq" for text contexts, but the specific 9Router
    # model (e.g. "ag/gemini-3.5-flash-low") for vision contexts — wide enough
    # for either.
    final_provider = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=True)
