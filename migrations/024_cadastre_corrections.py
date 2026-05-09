"""024_cadastre_corrections

FONCIER+ — Migration 024 : Corrections module Cadastre et CCFM

  BUG-WF-CAD-01  CHEF_DAR → ARCHIVISTE_ANNF    (WF RNP  étape 6)
  BUG-WF-CCFM-01 GUICHETIER_CCFM → AGENT_CCFM  (WF CCFM étape 1)
  BUG-WF-CCFM-02 SECRETAIRE_GENERAL → MINISTRE_URBANISME (CCFM étape 5)
  BUG-WF-CCFM-03 CHEF_DAR → ARCHIVISTE_ANNF    (WF CCFM étape 6)

Revision ID: 024_cadastre_corrections
Revises: 023_urbanisme_corrections
Create Date: 2026-04-16
"""
from alembic import op

revision      = "024_cadastre_corrections"
down_revision = "023_urbanisme_corrections"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── WF RNP : étape 6 ARCHIVER_DAR ─────────────────────────────
    op.execute("""
        UPDATE workflow_step_def wsd
        SET role_requis = 'ARCHIVISTE_ANNF'
        WHERE wsd.role_requis = 'CHEF_DAR'
          AND EXISTS (
            SELECT 1 FROM workflow_definition wd
            WHERE wd.id = wsd.definition_id
              AND wd.type_workflow = 'RNP'
          );
    """)

    # ── WF CCFM : 3 étapes corrigées ──────────────────────────────
    op.execute("""
        UPDATE workflow_step_def wsd
        SET role_requis = 'AGENT_CCFM'
        WHERE wsd.role_requis = 'GUICHETIER_CCFM'
          AND EXISTS (
            SELECT 1 FROM workflow_definition wd
            WHERE wd.id = wsd.definition_id
              AND wd.type_workflow = 'CCFM'
          );
    """)
    op.execute("""
        UPDATE workflow_step_def wsd
        SET role_requis = 'MINISTRE_URBANISME'
        WHERE wsd.role_requis = 'SECRETAIRE_GENERAL'
          AND EXISTS (
            SELECT 1 FROM workflow_definition wd
            WHERE wd.id = wsd.definition_id
              AND wd.type_workflow = 'CCFM'
          );
    """)
    op.execute("""
        UPDATE workflow_step_def wsd
        SET role_requis = 'ARCHIVISTE_ANNF'
        WHERE wsd.role_requis = 'CHEF_DAR'
          AND EXISTS (
            SELECT 1 FROM workflow_definition wd
            WHERE wd.id = wsd.definition_id
              AND wd.type_workflow = 'CCFM'
          );
    """)

    # ── WF TRANSFERT : CHEF_DAR restant ───────────────────────────
    op.execute("""
        UPDATE workflow_step_def
        SET role_requis = 'ARCHIVISTE_ANNF'
        WHERE role_requis = 'CHEF_DAR';
    """)

    # ── Rapport ───────────────────────────────────────────────────
    op.execute("""
        DO $$
        DECLARE
            v_archiviste INT; v_agent_ccfm INT; v_ministre INT;
            v_chef_dar_restants INT;
        BEGIN
            SELECT COUNT(*) INTO v_archiviste FROM workflow_step_def
                WHERE role_requis = 'ARCHIVISTE_ANNF';
            SELECT COUNT(*) INTO v_agent_ccfm FROM workflow_step_def
                WHERE role_requis = 'AGENT_CCFM';
            SELECT COUNT(*) INTO v_ministre FROM workflow_step_def
                WHERE role_requis = 'MINISTRE_URBANISME';
            SELECT COUNT(*) INTO v_chef_dar_restants FROM workflow_step_def
                WHERE role_requis = 'CHEF_DAR';
            SELECT COUNT(*) INTO v_agent_ccfm FROM workflow_step_def
                WHERE role_requis = 'GUICHETIER_CCFM';

            RAISE NOTICE '024 OK — ARCHIVISTE_ANNF:% AGENT_CCFM:% MINISTRE_URB:%',
                v_archiviste, v_agent_ccfm, v_ministre;

            IF v_chef_dar_restants > 0 THEN
                RAISE EXCEPTION 'CHEF_DAR encore présent dans % étapes', v_chef_dar_restants;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE workflow_step_def wsd
        SET role_requis = 'CHEF_DAR'
        WHERE wsd.role_requis = 'ARCHIVISTE_ANNF'
          AND wsd.code_etape IN ('ARCHIVER_DAR','ARCHIVAGE','ANNF_ARCHIVE');

        UPDATE workflow_step_def wsd
        SET role_requis = 'GUICHETIER_CCFM'
        WHERE wsd.role_requis = 'AGENT_CCFM'
          AND wsd.code_etape = 'RECEPTION';
    """)
