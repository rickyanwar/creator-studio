"""ig_source_niche

Revision ID: c80000000037
Revises: c80000000036
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000037"
down_revision = "c80000000036"
branch_labels = None
depends_on = None

NICHE_BACKFILL = {
    "F1": (
        "f.1interviews", "f1.unfiltered", "f1feverr", "f1gossippofficial",
        "formula.planet", "formula.room", "formula1.pitstops", "gridposition",
        "hatsoffgp", "lapbylap", "maxverstappen", "maxverstappen33",
        "paddock_formula_", "paddockpasspod", "polymarketf1", "revivef1",
        "thef1enthusiasts", "verstappen",
    ),
    "MotoGP": (
        "effettogp", "gaspoll_gp", "insidemgp", "mgp1official",
        "moto_indonesia_gp", "mundo_gp__", "overtake.gp", "peccofanclub",
        "theracemoto",
    ),
    "Motorsport": (
        "motorsportbyapex", "motorsportsclicks",
    ),
    "UFC": (
        "backyardviolence", "calfkicks", "fight.riddle", "khabibstime",
        "whathappenedmma",
    ),
    "NBA": (
        "_nba_talks", "crossesup", "dunktopic", "hoopsblazers",
        "lakers24eight", "nba.in_depth",
    ),
    "Sports": (
        "fraudsports", "staffonlysports",
    ),
}


def upgrade() -> None:
    op.add_column("ig_sources", sa.Column("niche", sa.String(64), nullable=True))

    for niche, usernames in NICHE_BACKFILL.items():
        quoted_users = ", ".join(f"'{u}'" for u in usernames)
        op.execute(
            sa.text(f"UPDATE ig_sources SET niche = '{niche}' WHERE ig_username IN ({quoted_users})")
        )


def downgrade() -> None:
    op.drop_column("ig_sources", "niche")
