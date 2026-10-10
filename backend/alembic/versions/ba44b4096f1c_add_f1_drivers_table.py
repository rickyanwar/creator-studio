"""Add f1_drivers table

Revision ID: ba44b4096f1c
Revises: 58f10955ddf3
Create Date: 2026-10-10 12:00:56.683251

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'ba44b4096f1c'
down_revision: Union[str, None] = '58f10955ddf3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    f1_drivers_table = op.create_table(
        'f1_drivers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('season', sa.Integer(), nullable=False),
        sa.Column('surname', sa.String(length=64), nullable=False),
        sa.Column('full_name', sa.String(length=128), nullable=True),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('team_name', sa.String(length=64), nullable=False),
        sa.Column('team_colour', sa.String(length=7), nullable=False),
        sa.Column('team_logo_path', sa.String(length=512), nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=False, default=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('season', 'surname', name='uix_season_surname')
    )
    op.create_index(op.f('ix_f1_drivers_id'), 'f1_drivers', ['id'], unique=False)

    op.bulk_insert(f1_drivers_table, [
        {"season": 2026, "surname": "VERSTAPPEN", "number": 1, "team_name": "Red Bull Racing", "team_colour": "#1E41FF", "verified": False},
        {"season": 2026, "surname": "LECLERC", "number": 16, "team_name": "Ferrari", "team_colour": "#DC0000", "verified": False},
        {"season": 2026, "surname": "HAMILTON", "number": 44, "team_name": "Ferrari", "team_colour": "#DC0000", "verified": False},
        {"season": 2026, "surname": "PIASTRI", "number": 81, "team_name": "McLaren", "team_colour": "#FF8000", "verified": False},
        {"season": 2026, "surname": "NORRIS", "number": 4, "team_name": "McLaren", "team_colour": "#FF8000", "verified": False},
        {"season": 2026, "surname": "RUSSELL", "number": 63, "team_name": "Mercedes", "team_colour": "#00D2BE", "verified": False},
        {"season": 2026, "surname": "ANTONELLI", "number": 12, "team_name": "Mercedes", "team_colour": "#00D2BE", "verified": False},
        {"season": 2026, "surname": "ALBON", "number": 23, "team_name": "Williams", "team_colour": "#1868DB", "verified": False},
        {"season": 2026, "surname": "SAINZ", "number": 55, "team_name": "Williams", "team_colour": "#1868DB", "verified": False},
        {"season": 2026, "surname": "ALONSO", "number": 14, "team_name": "Aston Martin", "team_colour": "#00594F", "verified": False},
        {"season": 2026, "surname": "STROLL", "number": 18, "team_name": "Aston Martin", "team_colour": "#00594F", "verified": False},
        {"season": 2026, "surname": "BEARMAN", "number": 87, "team_name": "Haas", "team_colour": "#B6BABD", "verified": False},
        {"season": 2026, "surname": "OCON", "number": 31, "team_name": "Haas", "team_colour": "#B6BABD", "verified": False},
        {"season": 2026, "surname": "HADJAR", "number": 6, "team_name": "Racing Bulls", "team_colour": "#4781D7", "verified": False},
        {"season": 2026, "surname": "LAWSON", "number": 30, "team_name": "Racing Bulls", "team_colour": "#4781D7", "verified": False},
        {"season": 2026, "surname": "GASLY", "number": 10, "team_name": "Alpine", "team_colour": "#0093CC", "verified": False},
        {"season": 2026, "surname": "COLAPINTO", "number": 43, "team_name": "Alpine", "team_colour": "#0093CC", "verified": False},
        {"season": 2026, "surname": "HULKENBERG", "number": 27, "team_name": "Audi", "team_colour": "#A0A0A0", "verified": False},
        {"season": 2026, "surname": "BORTOLETO", "number": 5, "team_name": "Audi", "team_colour": "#A0A0A0", "verified": False},
        {"season": 2026, "surname": "BOTTAS", "number": 77, "team_name": "Cadillac", "team_colour": "#C0C0C0", "verified": False},
        {"season": 2026, "surname": "PEREZ", "number": 11, "team_name": "Cadillac", "team_colour": "#C0C0C0", "verified": False},
    ])


def downgrade() -> None:
    op.drop_index(op.f('ix_f1_drivers_id'), table_name='f1_drivers')
    op.drop_table('f1_drivers')
