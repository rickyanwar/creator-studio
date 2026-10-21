from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String,
    Text, UniqueConstraint, func, text,
)

from app.database import Base


class GflowWorker(Base):
    __tablename__ = "gflow_workers"

    worker_id = Column(String(64), primary_key=True)
    last_seen_at = Column(DateTime, nullable=True)
    version = Column(String(32), nullable=True)
    max_concurrent = Column(Integer, nullable=True)
    mem_available_mb = Column(Integer, nullable=True)
    running_job_ids = Column(JSON, nullable=False, default=list, server_default=text("'[]'::json"))
    paused_until = Column(DateTime, nullable=True)
    phase = Column(JSON, nullable=True)


class GflowAccount(Base):
    __tablename__ = "gflow_accounts"
    __table_args__ = (UniqueConstraint("worker_id", "profile", name="uq_gflow_accounts_worker_profile"),)

    id = Column(Integer, primary_key=True)
    worker_id = Column(String(64), nullable=False)
    profile = Column(String(100), nullable=False)
    email = Column(String(200), nullable=True)
    label = Column(String(100), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    authenticated = Column(Boolean, nullable=False, default=False, server_default="false")
    status = Column(String(16), nullable=False, default="ok", server_default="ok")
    credits = Column(Integer, nullable=True)
    credits_checked_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    cooldown_until = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class VideoIdea(Base):
    __tablename__ = "video_ideas"
    __table_args__ = (
        # One research-desk pack yields at most one idea per fanpage (idempotent retries).
        Index("uq_video_ideas_client_ref_fanpage", "client_ref", "fanpage_id", unique=True,
              postgresql_where=text("client_ref IS NOT NULL")),
    )

    id = Column(Integer, primary_key=True)
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False, index=True)
    template_key = Column(String(64), nullable=False)
    topic = Column(String(200), nullable=False)
    angle = Column(Text, nullable=True)
    research_notes = Column(Text, nullable=True)
    variables = Column(JSON, nullable=True)
    clips = Column(Integer, nullable=True)
    score = Column(Integer, nullable=True)
    rationale = Column(Text, nullable=True)
    evidence = Column(JSON, nullable=True)
    source = Column(String(16), nullable=False, default="owner", server_default="owner")
    token_id = Column(Integer, ForeignKey("api_tokens.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(16), nullable=False, default="proposed", server_default="proposed", index=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    decided_at = Column(DateTime, nullable=True)
    decided_by = Column(String(64), nullable=True)
    # Research desk (Muse) packs: the pack's client_ref plus the copy Muse wrote for this fanpage.
    client_ref = Column(String(64), nullable=True)
    title = Column(Text, nullable=True)
    caption = Column(Text, nullable=True)


class VideoJob(Base):
    __tablename__ = "video_jobs"

    id = Column(Integer, primary_key=True)
    idea_id = Column(Integer, ForeignKey("video_ideas.id", ondelete="SET NULL"), nullable=True, index=True)
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False, index=True)
    template_key = Column(String(64), nullable=False)
    title = Column(Text, nullable=False)
    caption = Column(Text, nullable=False)
    model = Column(String(32), nullable=False)
    aspect = Column(String(8), nullable=False)
    clip_seconds = Column(Integer, nullable=False)
    clips = Column(JSON, nullable=False)
    credits_per_clip = Column(Integer, nullable=False)
    credits_estimate = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="queued", server_default="queued", index=True)
    account_id = Column(Integer, ForeignKey("gflow_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    worker_id = Column(String(64), nullable=True)
    generation = Column(Integer, nullable=False, default=1, server_default="1")
    flow_project_id = Column(String(128), nullable=True)
    clips_done = Column(JSON, nullable=False, default=list, server_default=text("'[]'::json"))
    clip_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    restarts = Column(Integer, nullable=False, default=0, server_default="0")
    unusual_hits = Column(Integer, nullable=False, default=0, server_default="0")
    lease_until = Column(DateTime, nullable=True)
    not_before = Column(DateTime, nullable=True)
    last_progress_at = Column(DateTime, nullable=True)
    video_path = Column(String(512), nullable=True)
    video_url = Column(String(512), nullable=True)
    thumbnail_path = Column(String(512), nullable=True)
    thumbnail_url = Column(String(512), nullable=True)
    duration_s = Column(Float, nullable=True)
    qc = Column(JSON, nullable=True)
    publish_job_id = Column(Integer, ForeignKey("publish_jobs.id", ondelete="SET NULL"), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
