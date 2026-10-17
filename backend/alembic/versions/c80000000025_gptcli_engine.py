"""gptcli_engine

Revision ID: c80000000025
Revises: c80000000024
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000025"
down_revision = "c80000000024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("gptcli_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("settings", sa.Column("gptcli_base_url", sa.String(length=256), nullable=False, server_default="http://172.17.0.1:8099"))
    op.add_column("settings", sa.Column("gptcli_token_encrypted", sa.String(length=512), nullable=True))
    op.add_column("settings", sa.Column("gptcli_fanpage_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")))
    op.add_column("settings", sa.Column("gptcli_thread", sa.String(length=64), nullable=False, server_default="studio"))
    op.add_column("settings", sa.Column("gptcli_size", sa.String(length=16), nullable=False, server_default="1080x1350"))
    op.add_column("settings", sa.Column("ai_day_start", sa.String(length=5), nullable=False, server_default="05:30"))
    op.add_column("settings", sa.Column("codex_share_percent", sa.Integer(), nullable=False, server_default="35"))
    op.add_column("settings", sa.Column("codex_share_window_jobs", sa.Integer(), nullable=False, server_default="40"))
    op.add_column("settings", sa.Column("codex_overflow_queue_len", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("settings", sa.Column("codex_share_max_pct", sa.Integer(), nullable=False, server_default="60"))
    op.add_column("settings", sa.Column("codex_fallback_max_pct", sa.Integer(), nullable=False, server_default="85"))
    op.add_column("settings", sa.Column("codex_night_max_pct", sa.Integer(), nullable=False, server_default="70"))
    op.add_column("settings", sa.Column("codex_rest_pct", sa.Integer(), nullable=False, server_default="95"))
    op.add_column("settings", sa.Column("codex_window_capacity_est", sa.Integer(), nullable=False, server_default="60"))
    op.add_column("settings", sa.Column("codex_night_min_gap_s", sa.Integer(), nullable=False, server_default="240"))
    op.add_column("settings", sa.Column("codex_night_gap_jitter_s", sa.Integer(), nullable=False, server_default="60"))
    op.add_column("settings", sa.Column("codex_usage_url", sa.String(length=512), nullable=True))
    op.add_column("publish_jobs", sa.Column("design_not_before", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("publish_jobs", "design_not_before")
    op.drop_column("settings", "codex_usage_url")
    op.drop_column("settings", "codex_night_gap_jitter_s")
    op.drop_column("settings", "codex_night_min_gap_s")
    op.drop_column("settings", "codex_window_capacity_est")
    op.drop_column("settings", "codex_rest_pct")
    op.drop_column("settings", "codex_night_max_pct")
    op.drop_column("settings", "codex_fallback_max_pct")
    op.drop_column("settings", "codex_share_max_pct")
    op.drop_column("settings", "codex_overflow_queue_len")
    op.drop_column("settings", "codex_share_window_jobs")
    op.drop_column("settings", "codex_share_percent")
    op.drop_column("settings", "ai_day_start")
    op.drop_column("settings", "gptcli_size")
    op.drop_column("settings", "gptcli_thread")
    op.drop_column("settings", "gptcli_fanpage_ids")
    op.drop_column("settings", "gptcli_token_encrypted")
    op.drop_column("settings", "gptcli_base_url")
    op.drop_column("settings", "gptcli_enabled")
