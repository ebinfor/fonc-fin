"""042_tables_manquantes_constats_regularisations

FONCIER+ — Migration 042 : Création des tables manquantes
  - constats_terrain : Constats de terrain (espace_travail, WF CCFM)
  - regularisations  : Dossiers de régularisation (domaine.py)

Revision ID: 042_tables_manquantes
Revises: 041_index_composites_performance
"""
from alembic import op
from sqlalchemy import text

revision      = "042_tables_manquantes"
down_revision = "041_index_composites_performance"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS constats_terrain (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            parcelle_id     UUID REFERENCES parcelles(id) ON DELETE CASCADE,
            topographe_id   UUID,
            numero_recepisse VARCHAR(50),
            superficie_mesuree_m2  FLOAT,
            gps_latitude    FLOAT,
            gps_longitude   FLOAT,
            concordance_gps BOOLEAN DEFAULT FALSE,
            jo_publie       BOOLEAN DEFAULT FALSE,
            arrete_numero   VARCHAR(100),
            titre_foncier_numero VARCHAR(100),
            titre_authentique    BOOLEAN,
            decision        VARCHAR(20) CHECK (decision IN ('AUTORISATION','REFUS')),
            motif_refus     TEXT,
            observations    TEXT,
            photos_urls     JSONB DEFAULT '[]'::jsonb,
            region          VARCHAR(10),
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at      TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_constats_terrain_parcelle
            ON constats_terrain (parcelle_id);
        CREATE INDEX IF NOT EXISTS idx_constats_terrain_topographe
            ON constats_terrain (topographe_id);
        CREATE INDEX IF NOT EXISTS idx_constats_terrain_region
            ON constats_terrain (region);
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS regularisations (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            parcelle_id     UUID REFERENCES parcelles(id) ON DELETE CASCADE,
            demandeur_id    UUID,
            statut          VARCHAR(30) DEFAULT 'OUVERT'
                            CHECK (statut IN (
                                'OUVERT','EN_ENQUETE','ENQUETE_OK','ARRETE_EMIS',
                                'ATTRIBUTION_FAITE','CLOS','REJETE','ARCHIVE'
                            )),
            region          VARCHAR(10),
            date_demande    TIMESTAMPTZ DEFAULT NOW(),
            date_cloture    TIMESTAMPTZ,
            occupation_confirmee   BOOLEAN,
            conflit_detecte        BOOLEAN DEFAULT FALSE,
            arrete_reg_numero      VARCHAR(100),
            arrete_reg_date        DATE,
            recommandation  VARCHAR(20) CHECK (recommandation IN ('valider','rejeter','approfondir')),
            observations    TEXT,
            inspecteur_id   UUID,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at      TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_regularisations_parcelle
            ON regularisations (parcelle_id);
        CREATE INDEX IF NOT EXISTS idx_regularisations_statut
            ON regularisations (statut, region)
            WHERE statut NOT IN ('CLOS','REJETE','ARCHIVE');
    """))

    conn.execute(text("""
        DO $$
        BEGIN
            RAISE NOTICE '042 OK -- constats_terrain + regularisations créées';
        END $$;
    """))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS regularisations CASCADE;")
    op.execute("DROP TABLE IF EXISTS constats_terrain CASCADE;")
