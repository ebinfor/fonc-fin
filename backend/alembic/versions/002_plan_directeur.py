"""002_plan_directeur

FONCIER+ — Migration 002 : Plan directeur (stub de compatibilité)
Cette migration est un stub de compatibilité pour fermer la chaîne
Alembic. La migration 003 y fait référence comme down_revision.

Revision ID: 002_plan_directeur
Revises: 001_initial_schema
"""
from alembic import op

revision      = "002_plan_directeur"
down_revision = None
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # Stub de compatibilité — aucune modification
    op.execute("SELECT 1;")


def downgrade() -> None:
    pass
