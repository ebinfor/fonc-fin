"""025_bgu_workflow_etapes

FONCIER+ — Migration 025 : Étapes workflow BGU manquantes
Corrige BUG-WF-BGU-01 : workflow_definition BGU existante sans étapes.

Revision ID: 025_bgu_workflow_etapes
Revises: 024_cadastre_corrections
"""
from alembic import op

revision      = "025_bgu_workflow_etapes"
down_revision = "024_cadastre_corrections"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── 1. Seed des 6 étapes du workflow BGU ──────────────────────
    op.execute("""
        INSERT INTO workflow_step_def (
            id, definition_id, ordre, code_etape, nom_etape,
            role_requis, type_validation, delai_max_heures,
            est_obligatoire, action_post_validation
        )
        SELECT
            gen_random_uuid(), d.id,
            s.ordre, s.code, s.nom, s.role,
            s.tv::type_validation, s.delai, true, s.action
        FROM workflow_definition d,
        (VALUES
            (1,'IMPORT_GEOM',       'Import géométries sources',
             'RESPONSABLE_BGU',     'constat',      48, NULL),
            (2,'CONTROLE_QUALITE',  'Contrôle qualité PostGIS',
             'GEOMETRE',            'constat',      24, 'CHECK_GEOM_QUALITE'),
            (3,'VERIFICATION_RNAF', 'Vérification cohérence RNAF/RNP',
             'DIRECTEUR_URBANISME', 'approbation',  48, 'VERIFIER_COHERENCE_RNAF'),
            (4,'VALIDATION',        'Validation géospatiale',
             'DIRECTEUR_CADASTRE',  'approbation',  24, NULL),
            (5,'SCELLEMENT',        'Scellement BGU officiel',
             'RESPONSABLE_BGU',     'scellement',    8, 'SCELLER_BGU_GEOM'),
            (6,'ARCHIVAGE_ANNF',    'Archivage ANNF',
             'ARCHIVISTE_ANNF',     'scellement',   24, 'ARCHIVER_BGU_ANNF')
        ) AS s(ordre, code, nom, role, tv, delai, action)
        WHERE d.type_workflow = 'BGU'
        ON CONFLICT (definition_id, code_etape) DO NOTHING;
    """)

    # ── 2. Ajouter les nouvelles actions dans le dispatcher ────────
    # (géré côté Python dans workflow_engine._executer_action)
    # Vérification en base
    op.execute("""
        DO $$
        DECLARE v_nb INT;
        BEGIN
            SELECT COUNT(*) INTO v_nb
            FROM workflow_step_def wsd
            JOIN workflow_definition wd ON wd.id = wsd.definition_id
            WHERE wd.type_workflow = 'BGU';

            IF v_nb < 6 THEN
                RAISE EXCEPTION 'Migration 025 incomplète : seulement % étapes BGU', v_nb;
            END IF;
            RAISE NOTICE '025 OK — % étapes workflow BGU créées', v_nb;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM workflow_step_def wsd
        USING workflow_definition wd
        WHERE wd.id = wsd.definition_id
          AND wd.type_workflow = 'BGU';
    """)
