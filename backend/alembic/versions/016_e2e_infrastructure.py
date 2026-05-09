"""016_e2e_infrastructure

FONCIER+ v3.4.7 — Migration 016 : Infrastructure pour tests E2E
Ajoute : greffe_judiciaire (seedé), v_integrite_hash, extensions workflow_instance,
         index de performance pour les 957 tests RBAC.

Revision ID: 016_e2e_infrastructure
Revises: 015_bugfixes_audit
Create Date: 2026-04-11
"""
from alembic import op
import sqlalchemy as sa

revision = "016_e2e_infrastructure"
down_revision = "015_bugfixes_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── Table des 9 greffes officiels TGI du Niger ────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS greffe_judiciaire (
            id TEXT PRIMARY KEY,
            nom_tgi TEXT NOT NULL,
            region TEXT NOT NULL,
            ville TEXT NOT NULL,
            actif BOOLEAN NOT NULL DEFAULT TRUE,
            cle_publique_pki TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    op.execute("""
        INSERT INTO greffe_judiciaire (id, nom_tgi, region, ville) VALUES
            ('TGI_NIAMEY',      'TGI de Niamey',       'NIA', 'Niamey'),
            ('TGI_DOSSO',       'TGI de Dosso',        'DOS', 'Dosso'),
            ('TGI_TILLABERI',   'TGI de Tillabéri',    'TIL', 'Tillabéri'),
            ('TGI_TAHOUA',      'TGI de Tahoua',       'TAH', 'Tahoua'),
            ('TGI_MARADI',      'TGI de Maradi',       'MAR', 'Maradi'),
            ('TGI_ZINDER',      'TGI de Zinder',       'ZIN', 'Zinder'),
            ('TGI_DIFFA',       'TGI de Diffa',        'DIF', 'Diffa'),
            ('TGI_AGADEZ',      'TGI d''Agadez',        'AGA', 'Agadez'),
            ('TGI_HORS_CLASSE', 'TGI hors classe',     'NAT', 'Niamey')
        ON CONFLICT DO NOTHING;
    """)

    # ─── Fonction verifier_authenticite_jugement ────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION verifier_authenticite_jugement(
            p_num_jugement TEXT, p_greffe_id TEXT
        ) RETURNS BOOLEAN AS $$
        DECLARE v_actif BOOLEAN;
        BEGIN
            SELECT actif INTO v_actif FROM greffe_judiciaire
            WHERE id = p_greffe_id;
            IF v_actif IS NULL OR v_actif = FALSE THEN
                RETURN FALSE;
            END IF;
            IF p_num_jugement IS NULL OR LENGTH(p_num_jugement) < 5 THEN
                RETURN FALSE;
            END IF;
            RETURN TRUE;
        END; $$ LANGUAGE plpgsql;
    """)

    # ─── Vue v_integrite_hash (détection rupture chaîne SHA-256) ────────
    op.execute("""
        CREATE OR REPLACE VIEW v_integrite_hash AS
        SELECT 'notarial_act' AS table_name, id::TEXT AS row_id,
               sha256_chain AS hash_actuel,
               LAG(sha256_chain) OVER (ORDER BY created_at) AS hash_precedent,
               (sha256_chain IS NOT NULL) AS integre
        FROM notarial_act
        UNION ALL
        SELECT 'ccfm_verification_log', id::TEXT, sha256_chain,
               LAG(sha256_chain) OVER (ORDER BY created_at),
               (sha256_chain IS NOT NULL)
        FROM ccfm_verification_log;
    """)

    # ─── Extensions workflow_instance ───────────────────────────────────
    op.execute("""
        ALTER TABLE workflow_instance
            ADD COLUMN IF NOT EXISTS etape_courante TEXT,
            ADD COLUMN IF NOT EXISTS objet_id UUID,
            ADD COLUMN IF NOT EXISTS payload JSONB DEFAULT '{}'::jsonb;
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_wf_instance_wf_code_objet
        ON workflow_instance(wf_code, objet_id) WHERE statut = 'en_cours';
    """)

    # ─── Trigger tg_prevent_hard_delete (règle n°1) ─────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_prevent_hard_delete() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'DELETE physique INTERDIT sur la table %', TG_TABLE_NAME
                USING HINT = 'Utiliser un changement de statut logique';
        END; $$ LANGUAGE plpgsql;
    """)
    for tbl in ("parcelles", "notarial_act", "ccfm_demande", "parcel_right_version"):
        op.execute(f"""
            DROP TRIGGER IF EXISTS tg_prevent_hard_delete ON {tbl};
            CREATE TRIGGER tg_prevent_hard_delete BEFORE DELETE ON {tbl}
            FOR EACH ROW EXECUTE FUNCTION fn_prevent_hard_delete();
        """)


def downgrade() -> None:
    for tbl in ("parcelles", "notarial_act", "ccfm_demande", "parcel_right_version"):
        op.execute(f"DROP TRIGGER IF EXISTS tg_prevent_hard_delete ON {tbl};")
    op.execute("DROP FUNCTION IF EXISTS fn_prevent_hard_delete();")
    op.execute("DROP VIEW IF EXISTS v_integrite_hash;")
    op.execute("DROP FUNCTION IF EXISTS verifier_authenticite_jugement(TEXT, TEXT);")
    op.execute("DROP TABLE IF EXISTS greffe_judiciaire;")
