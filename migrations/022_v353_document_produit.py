"""022_v353_document_produit

FONCIER+ v3.5.3 — Table des documents produits par le moteur PDF
Revision ID: 022_v353_document_produit
Revises: 021_v35_fondation_administrative
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "022_v353_document_produit"
down_revision = "021_v35_fondation_administrative"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_produit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("template_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("document_template.id"), nullable=False),
        sa.Column("template_version", sa.Integer, nullable=False),
        sa.Column("code_template", sa.String(50), nullable=False),
        sa.Column("module",      sa.String(30), nullable=False),
        sa.Column("type_acte",   sa.String(80), nullable=False),
        sa.Column("signataire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sha256_document", sa.String(64), nullable=False),
        sa.Column("signature_pki",   sa.Text),
        sa.Column("minio_url",       sa.Text, nullable=False),
        sa.Column("payload_snapshot", postgresql.JSONB),
        sa.Column("produit_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("sha256_document", name="uq_doc_produit_hash"),
    )
    op.create_index("ix_doc_produit_module", "document_produit", ["module"])
    op.create_index("ix_doc_produit_signataire", "document_produit", ["signataire_id"])
    op.create_index("ix_doc_produit_date", "document_produit", ["produit_at"])


def downgrade() -> None:
    op.drop_table("document_produit")
