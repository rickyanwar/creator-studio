"""hermes strategy

Revision ID: e1a000000001
Revises: d1a000000001
Create Date: 2026-10-11 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'e1a000000001'
down_revision = 'd1a000000001'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # api_tokens
    op.create_table(
        'api_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash')
    )

    # strategy_recommendations
    op.create_table(
        'strategy_recommendations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fanpage_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('proposal', sa.JSON(), nullable=False),
        sa.Column('rationale', sa.Text(), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='proposed', nullable=False),
        sa.Column('source', sa.String(length=32), server_default='hermes', nullable=False),
        sa.Column('token_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('decided_by', sa.String(length=64), nullable=True),
        sa.Column('applied_at', sa.DateTime(), nullable=True),
        sa.Column('apply_error', sa.Text(), nullable=True),
        sa.Column('previous_values', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['fanpage_id'], ['target_fanpages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['token_id'], ['api_tokens.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_strategy_recommendations_fanpage_id'), 'strategy_recommendations', ['fanpage_id'], unique=False)
    op.create_index(op.f('ix_strategy_recommendations_status'), 'strategy_recommendations', ['status'], unique=False)

    # fanpage_content_memory
    op.create_table(
        'fanpage_content_memory',
        sa.Column('fanpage_id', sa.Integer(), nullable=False),
        sa.Column('auto', sa.JSON(), nullable=True),
        sa.Column('notes', sa.JSON(), nullable=True),
        sa.Column('auto_updated_at', sa.DateTime(), nullable=True),
        sa.Column('notes_updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['fanpage_id'], ['target_fanpages.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('fanpage_id')
    )


def downgrade() -> None:
    op.drop_table('fanpage_content_memory')
    op.drop_index(op.f('ix_strategy_recommendations_status'), table_name='strategy_recommendations')
    op.drop_index(op.f('ix_strategy_recommendations_fanpage_id'), table_name='strategy_recommendations')
    op.drop_table('strategy_recommendations')
    op.drop_table('api_tokens')
