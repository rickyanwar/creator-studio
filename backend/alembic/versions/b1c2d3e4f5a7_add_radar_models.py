"""add radar models

Revision ID: b1c2d3e4f5a7
Revises: 234c3c3d6244
Create Date: 2026-10-10 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'b1c2d3e4f5a7'
down_revision = '234c3c3d6244'
branch_labels = None
depends_on = None


def upgrade():
    # ── settings new columns ──
    op.add_column('settings', sa.Column('radar_sleep_start_wib', sa.Integer(), nullable=True))
    op.add_column('settings', sa.Column('radar_sleep_end_wib', sa.Integer(), nullable=True))
    op.add_column('settings', sa.Column('radar_very_hot_interval_min', sa.Integer(), server_default='8', nullable=False))
    op.add_column('settings', sa.Column('radar_hot_interval_min', sa.Integer(), server_default='12', nullable=False))
    op.add_column('settings', sa.Column('radar_cold_interval_min', sa.Integer(), server_default='50', nullable=False))
    op.add_column('settings', sa.Column('radar_hot_window_h', sa.Integer(), server_default='2', nullable=False))
    op.add_column('settings', sa.Column('radar_track_max_age_h', sa.Integer(), server_default='48', nullable=False))
    op.add_column('settings', sa.Column('radar_story_sharing', sa.String(length=16), server_default='shared', nullable=False))
    op.add_column('settings', sa.Column('radar_share_max', sa.Integer(), server_default='0', nullable=False))
    op.add_column('settings', sa.Column('radar_news_stagger_max_min', sa.Integer(), server_default='10', nullable=False))

    # ── target_fanpages new columns ──
    op.add_column('target_fanpages', sa.Column('radar_enabled', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('target_fanpages', sa.Column('radar_niches', postgresql.ARRAY(sa.String()), server_default='{}', nullable=False))
    op.add_column('target_fanpages', sa.Column('radar_shadow', sa.Boolean(), server_default='true', nullable=False))
    op.add_column('target_fanpages', sa.Column('radar_min_likes', sa.Integer(), server_default='1000', nullable=False))
    op.add_column('target_fanpages', sa.Column('radar_confirm_ratio', sa.Float(), server_default='1.5', nullable=False))
    op.add_column('target_fanpages', sa.Column('radar_fast_ratio', sa.Float(), server_default='2.0', nullable=False))
    op.add_column('target_fanpages', sa.Column('radar_fast_window_min', sa.Integer(), server_default='60', nullable=False))
    op.add_column('target_fanpages', sa.Column('radar_shelf_news_h', sa.Integer(), server_default='72', nullable=False))
    op.add_column('target_fanpages', sa.Column('radar_shelf_evergreen_h', sa.Integer(), server_default='168', nullable=False))
    op.add_column('target_fanpages', sa.Column('radar_burst_enabled', sa.Boolean(), server_default='true', nullable=False))
    op.add_column('target_fanpages', sa.Column('radar_daily_max', sa.Integer(), server_default='3', nullable=False))
    op.add_column('target_fanpages', sa.Column('visual_engine', sa.String(length=16), server_default='off', nullable=False))

    # ── fanpage_sources new column ──
    op.add_column('fanpage_sources', sa.Column('trigger', sa.String(length=16), server_default='every_post', nullable=False))

    # ── radar_accounts ──
    op.create_table('radar_accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('niche', sa.String(length=64), nullable=False),
        sa.Column('ig_username', sa.String(length=64), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('leader_score', sa.Float(), server_default='0', nullable=False),
        sa.Column('last_checked_at', sa.DateTime(), nullable=True),
        sa.Column('last_post_seen_at', sa.DateTime(), nullable=True),
        sa.Column('avg_scrape_seconds', sa.Float(), nullable=True),
        sa.Column('last_error', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('niche', 'ig_username')
    )
    op.create_index(op.f('ix_radar_accounts_niche'), 'radar_accounts', ['niche'], unique=False)
    op.create_index(op.f('ix_radar_accounts_ig_username'), 'radar_accounts', ['ig_username'], unique=False)

    # ── radar_posts ──
    op.create_table('radar_posts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('radar_account_id', sa.Integer(), nullable=True),
        sa.Column('ig_source_id', sa.Integer(), nullable=True),
        sa.Column('ig_username', sa.String(length=64), nullable=False),
        sa.Column('shortcode', sa.String(length=64), nullable=False),
        sa.Column('taken_at', sa.DateTime(), nullable=False),
        sa.Column('caption', sa.Text(), nullable=True),
        sa.Column('media_type', sa.String(length=16), nullable=False),
        sa.Column('thumbnail_url', sa.String(length=1024), nullable=True),
        sa.Column('image_source_urls', postgresql.ARRAY(sa.String()), server_default='{}', nullable=False),
        sa.Column('phash', sa.String(length=16), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('story_id', sa.Integer(), nullable=True),
        sa.Column('latest_like_count', sa.Integer(), nullable=True),
        sa.Column('latest_comment_count', sa.Integer(), nullable=True),
        sa.Column('latest_observed_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint('radar_account_id IS NOT NULL OR ig_source_id IS NOT NULL', name='chk_radar_posts_source'),
        sa.ForeignKeyConstraint(['ig_source_id'], ['ig_sources.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['radar_account_id'], ['radar_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('shortcode')
    )
    op.create_index(op.f('ix_radar_posts_ig_username'), 'radar_posts', ['ig_username'], unique=False)
    op.create_index('ix_radar_posts_account_taken', 'radar_posts', ['radar_account_id', 'taken_at'], unique=False)

    # ── radar_snapshots ──
    op.create_table('radar_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('radar_post_id', sa.Integer(), nullable=False),
        sa.Column('observed_at', sa.DateTime(), nullable=False),
        sa.Column('age_minutes', sa.Float(), nullable=False),
        sa.Column('like_count', sa.Integer(), nullable=True),
        sa.Column('comment_count', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['radar_post_id'], ['radar_posts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_radar_snapshots_post_observed', 'radar_snapshots', ['radar_post_id', 'observed_at'], unique=False)

    # ── radar_stories ──
    op.create_table('radar_stories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('niche', sa.String(length=64), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_member_at', sa.DateTime(), nullable=False),
        sa.Column('member_count', sa.Integer(), server_default='1', nullable=False),
        sa.Column('distinct_accounts', sa.Integer(), server_default='1', nullable=False),
        sa.Column('best_post_id', sa.Integer(), nullable=True),
        sa.Column('content_hint', sa.String(length=16), server_default='unknown', nullable=False),
        sa.Column('shelf_kind', sa.String(length=16), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=32), server_default='watching', nullable=False),
        sa.Column('heat_score', sa.Float(), server_default='0', nullable=False),
        sa.Column('final_max_likes_24h', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['best_post_id'], ['radar_posts.id'], name='fk_radar_stories_best_post_id', ondelete='SET NULL', use_alter=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_radar_stories_niche'), 'radar_stories', ['niche'], unique=False)
    op.create_index('ix_radar_stories_niche_seen', 'radar_stories', ['niche', 'first_seen_at'], unique=False)

    # Add story_id foreign key back to radar_posts
    op.create_foreign_key('fk_radar_posts_story_id', 'radar_posts', 'radar_stories', ['story_id'], ['id'], ondelete='SET NULL')
    op.create_index(op.f('ix_radar_posts_story_id'), 'radar_posts', ['story_id'], unique=False)
    
    # ── radar_story_decisions ──
    op.create_table('radar_story_decisions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('story_id', sa.Integer(), nullable=False),
        sa.Column('fanpage_id', sa.Integer(), nullable=False),
        sa.Column('rule', sa.String(length=16), nullable=False),
        sa.Column('would_trigger_at', sa.DateTime(), nullable=True),
        sa.Column('shadow', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=True),
        sa.Column('publish_job_id', sa.Integer(), nullable=True),
        sa.Column('used_photo_key', sa.String(length=1024), nullable=True),
        sa.Column('caption_text', sa.Text(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['fanpage_id'], ['target_fanpages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['publish_job_id'], ['publish_jobs.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['story_id'], ['radar_stories.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('story_id', 'fanpage_id')
    )


def downgrade():
    # ── radar_story_decisions ──
    op.drop_table('radar_story_decisions')
    
    # Drop fk from radar_posts to allow dropping radar_stories
    op.drop_constraint('fk_radar_posts_story_id', 'radar_posts', type_='foreignkey')
    op.drop_index(op.f('ix_radar_posts_story_id'), table_name='radar_posts')
    
    # ── Other radar tables ──
    op.drop_table('radar_stories')
    op.drop_table('radar_snapshots')
    op.drop_table('radar_posts')
    op.drop_table('radar_accounts')

    # ── fanpage_sources columns ──
    op.drop_column('fanpage_sources', 'trigger')
    
    # ── target_fanpages columns ──
    op.drop_column('target_fanpages', 'visual_engine')
    op.drop_column('target_fanpages', 'radar_daily_max')
    op.drop_column('target_fanpages', 'radar_burst_enabled')
    op.drop_column('target_fanpages', 'radar_shelf_evergreen_h')
    op.drop_column('target_fanpages', 'radar_shelf_news_h')
    op.drop_column('target_fanpages', 'radar_fast_window_min')
    op.drop_column('target_fanpages', 'radar_fast_ratio')
    op.drop_column('target_fanpages', 'radar_confirm_ratio')
    op.drop_column('target_fanpages', 'radar_min_likes')
    op.drop_column('target_fanpages', 'radar_shadow')
    op.drop_column('target_fanpages', 'radar_niches')
    op.drop_column('target_fanpages', 'radar_enabled')

    # ── settings columns ──
    op.drop_column('settings', 'radar_news_stagger_max_min')
    op.drop_column('settings', 'radar_share_max')
    op.drop_column('settings', 'radar_story_sharing')
    op.drop_column('settings', 'radar_track_max_age_h')
    op.drop_column('settings', 'radar_hot_window_h')
    op.drop_column('settings', 'radar_cold_interval_min')
    op.drop_column('settings', 'radar_hot_interval_min')
    op.drop_column('settings', 'radar_very_hot_interval_min')
    op.drop_column('settings', 'radar_sleep_end_wib')
    op.drop_column('settings', 'radar_sleep_start_wib')
