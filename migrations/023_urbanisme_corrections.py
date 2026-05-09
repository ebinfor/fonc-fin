"""023_urbanisme_corrections

FONCIER+ — Migration 023 : Corrections module Urbanisme
Ferme les bugs identifiés lors de l'audit du module Urbanisme :

  BUG-WF-URB-01 : rôle URBANISTE → AGENT_URBANISME  (WF1 étape 1)
  BUG-WF-URB-02 : rôle SECRETAIRE_GENERAL → MINISTRE_URBANISME (WF1 étape 3)
  BUG-WF-URB-03 : workflow_definition LOTISSEMENT manquant
  BUG-WF-URB-04 : rôle TECH_BGU → RESPONSABLE_BGU
  BUG-WF-URB-05 : rôle CHEF_DAD → ADMIN_COMMUNE

Revision ID: 023_urbanisme_corrections
Revises: 022_v353_document_produit
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "023_urbanisme_corrections"
down_revision = "022_v353_document_produit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─────────────────────────────────────────────────────────────
    # BUG-WF-URB-01 : URBANISTE → AGENT_URBANISME
    # WF1 RNAF étape 1 — Soumission du brouillon
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        UPDATE workflow_step_def wsd
        SET role_requis = 'AGENT_URBANISME'
        WHERE wsd.role_requis = 'URBANISTE'
          AND EXISTS (
            SELECT 1 FROM workflow_definition wd
            WHERE wd.id = wsd.definition_id
              AND wd.type_workflow = 'RNAF'
          );
    """)

    # ─────────────────────────────────────────────────────────────
    # BUG-WF-URB-02 : SECRETAIRE_GENERAL → MINISTRE_URBANISME
    # WF1 RNAF étape 3 — Scellement officiel
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        UPDATE workflow_step_def
        SET role_requis = 'MINISTRE_URBANISME'
        WHERE role_requis = 'SECRETAIRE_GENERAL';
    """)

    # ─────────────────────────────────────────────────────────────
    # BUG-WF-URB-04 : TECH_BGU → RESPONSABLE_BGU
    # WF Lotissement étape 7 — Projection BGU
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        UPDATE workflow_step_def
        SET role_requis = 'RESPONSABLE_BGU'
        WHERE role_requis = 'TECH_BGU';
    """)

    # ─────────────────────────────────────────────────────────────
    # BUG-WF-URB-05 : CHEF_DAD → ADMIN_COMMUNE
    # WF Lotissement étape 4 — Validation Commune DAD
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        UPDATE workflow_step_def
        SET role_requis = 'ADMIN_COMMUNE'
        WHERE role_requis = 'CHEF_DAD';
    """)

    # ─────────────────────────────────────────────────────────────
    # BUG-WF-URB-03 : Créer workflow_definition LOTISSEMENT
    # et migrer les 8 étapes depuis RNP (où elles étaient mal insérées)
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        -- Créer la définition LOTISSEMENT si elle n'existe pas
        INSERT INTO workflow_definition (
            id, type_workflow, nom, description,
            delai_max_jours, signature_finale_requise, annf_auto,
            is_active
        ) VALUES (
            gen_random_uuid(), 'LOTISSEMENT',
            'Workflow Lotissement — Création de zone aménagée',
            'De l''arrêté d''urbanisme à l''archivage ANNF des parcelles filles',
            45, true, true, true
        ) ON CONFLICT (type_workflow) DO NOTHING;
    """)

    op.execute("""
        -- Migrer les étapes mal attribuées à RNP vers LOTISSEMENT
        UPDATE workflow_step_def wsd
        SET definition_id = (
            SELECT id FROM workflow_definition WHERE type_workflow = 'LOTISSEMENT'
        )
        WHERE wsd.code_etape IN (
            'INIT_LOTISSEMENT', 'VALID_URBANISME', 'DECOUPE_CADASTRE',
            'VALID_COMMUNE_DAD', 'CERTIF_CCFM', 'PUBLICATION_JO',
            'PROJECTION_BGU',   'ARCHIVAGE_ANNF'
        )
        AND wsd.definition_id = (
            SELECT id FROM workflow_definition WHERE type_workflow = 'RNP'
        );
    """)

    # Vérification de cohérence : les 6 étapes RNP standards doivent rester
    op.execute("""
        DO $$
        DECLARE
            v_nb_rnp INT;
            v_nb_lot INT;
        BEGIN
            SELECT COUNT(*) INTO v_nb_rnp
            FROM workflow_step_def wsd
            JOIN workflow_definition wd ON wd.id = wsd.definition_id
            WHERE wd.type_workflow = 'RNP';

            SELECT COUNT(*) INTO v_nb_lot
            FROM workflow_step_def wsd
            JOIN workflow_definition wd ON wd.id = wsd.definition_id
            WHERE wd.type_workflow = 'LOTISSEMENT';

            RAISE NOTICE 'WF RNP : % étapes | WF LOTISSEMENT : % étapes',
                         v_nb_rnp, v_nb_lot;

            IF v_nb_lot < 8 THEN
                RAISE EXCEPTION 'Migration 023 incomplète : % étapes LOTISSEMENT (attendu ≥ 8)',
                                v_nb_lot;
            END IF;
        END $$;
    """)

    # ─────────────────────────────────────────────────────────────
    # Ajouter LOTISSEMENT dans l'ENUM type_workflow si manquant
    # (ALTER TYPE ... ADD VALUE est irréversible, donc on vérifie avant)
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_enum pe
                JOIN pg_type pt ON pt.oid = pe.enumtypid
                WHERE pt.typname = 'type_workflow'
                  AND pe.enumlabel = 'LOTISSEMENT'
            ) THEN
                ALTER TYPE type_workflow ADD VALUE 'LOTISSEMENT';
                RAISE NOTICE 'LOTISSEMENT ajouté à l''ENUM type_workflow';
            ELSE
                RAISE NOTICE 'LOTISSEMENT déjà présent dans type_workflow';
            END IF;
        END $$;
    """)

    # ─────────────────────────────────────────────────────────────
    # Rapport de migration
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        DO $$
        DECLARE
            v_urb INT; v_min INT; v_bgu INT; v_comm INT;
        BEGIN
            SELECT COUNT(*) INTO v_urb FROM workflow_step_def
                WHERE role_requis = 'AGENT_URBANISME';
            SELECT COUNT(*) INTO v_min FROM workflow_step_def
                WHERE role_requis = 'MINISTRE_URBANISME';
            SELECT COUNT(*) INTO v_bgu FROM workflow_step_def
                WHERE role_requis = 'RESPONSABLE_BGU';
            SELECT COUNT(*) INTO v_comm FROM workflow_step_def
                WHERE role_requis = 'ADMIN_COMMUNE';

            RAISE NOTICE '023 OK — AGENT_URBANISME:% MINISTRE_URBANISME:% RESPONSABLE_BGU:% ADMIN_COMMUNE:%',
                v_urb, v_min, v_bgu, v_comm;
        END $$;
    """)


def downgrade() -> None:
    # Rôles : restaurer les valeurs originales (même si erronées)
    op.execute("""
        UPDATE workflow_step_def wsd
        SET role_requis = 'URBANISTE'
        WHERE wsd.role_requis = 'AGENT_URBANISME'
          AND EXISTS (
            SELECT 1 FROM workflow_definition wd
            WHERE wd.id = wsd.definition_id AND wd.type_workflow = 'RNAF'
            AND wsd.ordre = 1
          );

        UPDATE workflow_step_def
        SET role_requis = 'SECRETAIRE_GENERAL'
        WHERE role_requis = 'MINISTRE_URBANISME'
          AND EXISTS (
            SELECT 1 FROM workflow_definition wd
            WHERE wd.id = definition_id AND wd.type_workflow = 'RNAF'
            AND ordre = 3
          );

        UPDATE workflow_step_def
        SET role_requis = 'TECH_BGU'
        WHERE role_requis = 'RESPONSABLE_BGU'
          AND code_etape = 'PROJECTION_BGU';

        UPDATE workflow_step_def
        SET role_requis = 'CHEF_DAD'
        WHERE role_requis = 'ADMIN_COMMUNE'
          AND code_etape = 'VALID_COMMUNE_DAD';
    """)
    # Note : la migration des étapes LOTISSEMENT n'est pas réversible
    # sans perdre les données. Le downgrade ne déplace pas les étapes.
