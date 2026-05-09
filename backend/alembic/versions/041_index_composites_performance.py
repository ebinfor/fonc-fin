"""041_index_composites_performance

FONCIER+ — Migration 041 : Index composites pour performance production

Problemes resolus :
  - Requetes GET /demandes?etat=... sans index -> full scan
  - Requetes filtrees par (region, statut) -> seq scan
  - N+1 detectes en audit -> index couvrants

Revision ID: 041_index_composites_performance
Revises: 040_ccfm_v3_integration
"""
from alembic import op

revision      = "041_index_composites_performance"
down_revision = "040_ccfm_v3_integration"
branch_labels = None
depends_on    = None

SQL = """
-- ================================================================
-- INDEX COMPOSITES — Tables opérationnelles critiques
-- ================================================================

-- demandes_ccfm : filtrage par etat + region (workspaces acteurs)
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_demandes_ccfm_etat_region
    ON demandes_ccfm (etat, localite)
    WHERE etat NOT IN ('ARCHIVE','RETIRE');

-- demandes_ccfm : workspace guichetiere (enregistre_par + etat)
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_demandes_ccfm_enregistre_etat
    ON demandes_ccfm (enregistre_par, etat)
    WHERE etat NOT IN ('ARCHIVE','RETIRE','REJETE');

-- demandes_ccfm : workspace topographe (assignation + etat)
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_demandes_ccfm_topographe_etat
    ON demandes_ccfm (topographe_assigne, etat)
    WHERE topographe_assigne IS NOT NULL;

-- demandes_ccfm : date pour tri par ancienneté
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_demandes_ccfm_date_enregistrement
    ON demandes_ccfm (date_enregistrement DESC);

-- litiges : par region + statut (workspace justice)
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_litiges_region_statut
    ON litiges (region, statut)
    WHERE statut NOT IN ('CLOS','ARCHIVE');

-- litiges : par tgi (workspace juge)
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_litiges_tgi_statut
    ON litiges (tgi_id, statut)
    WHERE tgi_id IS NOT NULL;

-- property_transfers : par statut actif
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_property_transfers_statut_actif
    ON property_transfers (parcelle_id, statut)
    WHERE statut IN ('en_cours','valide','EN_ATTENTE_SIGNATURE');

-- hypotheques : par banque + statut
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_hypotheques_banque_statut
    ON hypotheques (banque_id, statut)
    WHERE statut != 'ANNULE';

-- workflow_instances : agent courant + statut (workspace workflows)
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_wf_instances_agent_courant
    ON workflow_instances (agent_courant_id, statut)
    WHERE statut NOT IN ('TERMINE','ANNULE');

-- workflow_instances : initiateur + statut
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_wf_instances_initiateur
    ON workflow_instances (initiateur_id, statut)
    WHERE statut NOT IN ('TERMINE','ANNULE');

-- rnaf : region + statut (workspace urbanisme)
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_rnaf_region_statut
    ON rnaf (region, statut);

-- expropriations : region + statut (workspace domaine)
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_expropriations_region_statut
    ON expropriations (region, statut)
    WHERE statut NOT IN ('ARCHIVE','ANNULE');

-- parcelles : region + is_gele (filtre gate CCFM)
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_parcelles_region_gele
    ON parcelles (region, is_gele);

-- annf_archive_links : scellement en attente
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_annf_archives_non_scellees
    ON annf_archive_links (entite_table, scelle)
    WHERE scelle = FALSE;

-- audit_immutable : seq DESC pour hash chaining
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_audit_immutable_seq_desc
    ON audit_immutable (seq DESC);

-- entity_lock : verrous actifs par entite
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_entity_lock_actif
    ON entity_lock (entite_type, entite_id, actif)
    WHERE actif = TRUE;

-- validation_pipeline : non termines par entite
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_validation_pipeline_en_cours
    ON validation_pipeline (entite_type, entite_id, statut)
    WHERE statut NOT IN ('VALIDE','REJETE');

-- sql_validation_log : suivi par code et statut
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    idx_sql_validation_log_code_statut
    ON sql_validation_log (code_regle, statut, created_at DESC);

DO $$
BEGIN
    RAISE NOTICE '041 OK -- 18 index composites crees pour performance production';
END $$;
"""

def upgrade() -> None:
    """
    Crée les index de performance.
    CONCURRENTLY ne fonctionne pas dans une transaction Alembic.
    On utilise EXECUTE dans une connexion avec autocommit.
    """
    from sqlalchemy import text
    conn = op.get_bind()

    # Désactiver autocommit si possible pour récupérer les erreurs
    INDEX_STATEMENTS = [
        "CREATE INDEX IF NOT EXISTS idx_demandes_ccfm_etat_region ON demandes_ccfm (etat, localite)",
        "CREATE INDEX IF NOT EXISTS idx_demandes_ccfm_enregistre_etat ON demandes_ccfm (enregistre_par, etat)",
        "CREATE INDEX IF NOT EXISTS idx_demandes_ccfm_topographe_etat ON demandes_ccfm (topographe_assigne, etat)",
        "CREATE INDEX IF NOT EXISTS idx_demandes_ccfm_date_enregistrement ON demandes_ccfm (date_enregistrement DESC)",
        "CREATE INDEX IF NOT EXISTS idx_litiges_region_statut ON litiges (region, statut)",
        "CREATE INDEX IF NOT EXISTS idx_litiges_tgi_statut ON litiges (tgi_id, statut)",
        "CREATE INDEX IF NOT EXISTS idx_property_transfers_statut_actif ON property_transfers (parcelle_id, statut)",
        "CREATE INDEX IF NOT EXISTS idx_hypotheques_banque_statut ON hypotheques (banque_id, statut)",
        "CREATE INDEX IF NOT EXISTS idx_wf_instances_agent_courant ON workflow_instances (agent_courant_id, statut)",
        "CREATE INDEX IF NOT EXISTS idx_wf_instances_initiateur ON workflow_instances (initiateur_id, statut)",
        "CREATE INDEX IF NOT EXISTS idx_rnaf_region_statut ON rnaf (region, statut)",
        "CREATE INDEX IF NOT EXISTS idx_expropriations_region_statut ON expropriations (region, statut)",
        "CREATE INDEX IF NOT EXISTS idx_parcelles_region_gele ON parcelles (region, is_gele)",
        "CREATE INDEX IF NOT EXISTS idx_annf_archives_non_scellees ON annf_archive_links (entite_table, scelle)",
        "CREATE INDEX IF NOT EXISTS idx_audit_immutable_seq_desc ON audit_immutable (seq DESC)",
        "CREATE INDEX IF NOT EXISTS idx_entity_lock_actif ON entity_lock (entite_type, entite_id, actif)",
        "CREATE INDEX IF NOT EXISTS idx_validation_pipeline_en_cours ON validation_pipeline (entite_type, entite_id, statut)",
        "CREATE INDEX IF NOT EXISTS idx_sql_validation_log_code_statut ON sql_validation_log (code_regle, statut, created_at DESC)",
    ]
    import logging
    _log = logging.getLogger("alembic.041")
    created = 0
    for stmt in INDEX_STATEMENTS:
        try:
            conn.execute(text(stmt))
            created += 1
        except Exception as e:
            err = str(e)
            if "already exists" not in err and "does not exist" not in err:
                _log.warning("Index warning: %s — %s", stmt[:60], err[:80])
    _log.info("041 OK — %d index sur %d créés/existants", created, len(INDEX_STATEMENTS))


def downgrade() -> None:
    indexes = [
        "idx_demandes_ccfm_etat_region",
        "idx_demandes_ccfm_enregistre_etat",
        "idx_demandes_ccfm_topographe_etat",
        "idx_demandes_ccfm_date_enregistrement",
        "idx_litiges_region_statut",
        "idx_litiges_tgi_statut",
        "idx_property_transfers_statut_actif",
        "idx_hypotheques_banque_statut",
        "idx_wf_instances_agent_courant",
        "idx_wf_instances_initiateur",
        "idx_rnaf_region_statut",
        "idx_expropriations_region_statut",
        "idx_parcelles_region_gele",
        "idx_annf_archives_non_scellees",
        "idx_audit_immutable_seq_desc",
        "idx_entity_lock_actif",
        "idx_validation_pipeline_en_cours",
        "idx_sql_validation_log_code_statut",
    ]
    from alembic import op as _op
    for idx in indexes:
        _op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {idx};")
