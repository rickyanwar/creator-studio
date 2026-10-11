from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, Index, JSON
from app.database import Base


class PostMetricSnapshot(Base):
    """Likes/comments/shares of one published post at a fixed age bucket
    (see app/services/post_metrics.py for the buckets and their windows)."""

    __tablename__ = "post_metric_snapshots"
    __table_args__ = (
        UniqueConstraint("publish_job_id", "bucket", name="uq_post_metric_snapshots_job_bucket"),
        Index("ix_post_metric_snapshots_fanpage_bucket", "fanpage_id", "bucket"),
    )

    id = Column(Integer, primary_key=True)
    publish_job_id = Column(Integer, ForeignKey("publish_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False)
    bucket = Column(String(16), nullable=False)
    captured_at = Column(DateTime, nullable=False)
    age_minutes = Column(Integer, nullable=False)
    likes = Column(Integer, nullable=True)
    comments = Column(Integer, nullable=True)
    shares = Column(Integer, nullable=True)
    raw_json = Column(JSON, nullable=True)
