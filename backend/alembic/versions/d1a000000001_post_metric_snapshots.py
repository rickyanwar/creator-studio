"""post metric snapshots

Revision ID: d1a000000001
Revises: 234c3c3d6245
Create Date: 2026-10-11 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'd1a000000001'
down_revision = '234c3c3d6245'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. Create table `post_metric_snapshots`
    op.create_table(
        'post_metric_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('publish_job_id', sa.Integer(), nullable=False),
        sa.Column('fanpage_id', sa.Integer(), nullable=False),
        sa.Column('bucket', sa.String(length=16), nullable=False),
        sa.Column('captured_at', sa.DateTime(), nullable=False),
        sa.Column('age_minutes', sa.Integer(), nullable=False),
        sa.Column('likes', sa.Integer(), nullable=True),
        sa.Column('comments', sa.Integer(), nullable=True),
        sa.Column('shares', sa.Integer(), nullable=True),
        sa.Column('raw_json', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['fanpage_id'], ['target_fanpages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['publish_job_id'], ['publish_jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('publish_job_id', 'bucket', name='uq_post_metric_snapshots_job_bucket')
    )
    op.create_index('ix_post_metric_snapshots_fanpage_bucket', 'post_metric_snapshots', ['fanpage_id', 'bucket'], unique=False)
    op.create_index(op.f('ix_post_metric_snapshots_publish_job_id'), 'post_metric_snapshots', ['publish_job_id'], unique=False)

    # 2. Add columns to `settings`
    op.add_column('settings', sa.Column('metrics_ingestion_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('settings', sa.Column('metrics_plan_status', sa.String(length=32), nullable=True))
    op.add_column('settings', sa.Column('metrics_plan_checked_at', sa.DateTime(), nullable=True))
    op.add_column('settings', sa.Column('metrics_last_error', sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column('settings', 'metrics_last_error')
    op.drop_column('settings', 'metrics_plan_checked_at')
    op.drop_column('settings', 'metrics_plan_status')
    op.drop_column('settings', 'metrics_ingestion_enabled')

    op.drop_index(op.f('ix_post_metric_snapshots_publish_job_id'), table_name='post_metric_snapshots')
    op.drop_index('ix_post_metric_snapshots_fanpage_bucket', table_name='post_metric_snapshots')
    op.drop_table('post_metric_snapshots')
