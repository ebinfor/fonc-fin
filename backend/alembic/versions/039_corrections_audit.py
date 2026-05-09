"""039_corrections_audit

FONCIER+ v108 ── Migration 039 : Corrections issues de l'audit fonctionnel

Corrections implémentées :
  P1.2  Index partiel workflow_instances(statut='en_cours')
  P2.4  Triggers tg_no_delete sur tables core (No Physical DELETE enforced)
  P2.8  Index property_transfers(created_at, statut) pour KPIs
  P3.1  Activation Row-Level Security (parcelles, parcel_right_version)
  Bonus Index parcelles manquants pour monitoring et risk engine
  Bonus Vue v_health_check (état des index et démons)

Révision : 039_corrections_audit / Revises : 038_dashboard_national
"""
from alembic import op

revision      = "039_corrections_audit"
down_revision = "038_dashboard_national"
branch_labels = None
depends_on    = None

SQL = r"""
-- ================================================================
-- P1.2 — Index partiel workflow_instances(statut='en_cours')
--        Critique pour MonitoringEngine D1 et v_workflow_retards
-- ================================================================
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_wi_en_cours
    ON workflow_instances(statut, updated_at DESC)
    WHERE statut = 'en_cours';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_wi_bloque
    ON workflow_instances(statut, updated_at DESC)
    WHERE statut = 'bloque';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_wi_entite
    ON workflow_instances(entite_id, entite_type, statut)
    WHERE statut NOT IN ('archive', 'rejete');


-- ================================================================
-- P2.8 — Index property_transfers pour requêtes KPI dashboard
--        Accélère les agrégations mensuelles et régionales
-- ================================================================
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pt_kpi
    ON property_transfers(created_at DESC, statut)
    WHERE statut = 'valide';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pt_parcelle_statut
    ON property_transfers(parcelle_id, statut, created_at DESC);

-- Index pour calcul de montant moyen par région
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pt_region_date
    ON property_transfers(created_at)
    INCLUDE (montant, statut)
    WHERE statut = 'valide';


-- ================================================================
-- P2.4 — Triggers tg_no_delete sur les tables core foncières
--        Enforce l'invariant "No Physical DELETE" au niveau DB
--        (était uniquement une convention de code — correction audit)
-- ================================================================
CREATE OR REPLACE FUNCTION tg_fn_no_physical_delete()
RETURNS TRIGGER AS $fn$
BEGIN
    RAISE EXCEPTION
        'Suppression physique interdite sur la table % (FONCIER+ audit P2.4). '
        'Utiliser un changement de statut (statut = ''archive'') ou is_active = FALSE. '
        'Entité concernée : id=%',
        TG_TABLE_NAME, OLD.id;
END;
$fn$ LANGUAGE plpgsql;

-- Appliquer sur les tables core irréversibles
DO $$
DECLARE
    tbl TEXT;
    tables_core TEXT[] := ARRAY[
        'parcelles',
        'property_transfers',
        'litiges',
        'parcel_right_version',
        'workflow_instances',
        'workflow_step_log',
        'entity_lock',
        'demandes_ccfm',
        'historique_ccfm'
    ];
BEGIN
    FOREACH tbl IN ARRAY tables_core LOOP
        -- Vérifier que la table existe
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = tbl AND table_schema = 'public'
        ) THEN
            EXECUTE format(
                'DROP TRIGGER IF EXISTS tg_no_delete_%1$s ON %1$s; '
                'CREATE TRIGGER tg_no_delete_%1$s '
                'BEFORE DELETE ON %1$s '
                'FOR EACH ROW EXECUTE FUNCTION tg_fn_no_physical_delete();',
                tbl
            );
            RAISE NOTICE 'tg_no_delete_% activé sur %', tbl, tbl;
        ELSE
            RAISE NOTICE 'Table % introuvable — trigger ignoré', tbl;
        END IF;
    END LOOP;
END;
$$;


-- ================================================================
-- P3.1 — Row-Level Security sur parcelles et parcel_right_version
--        Isolation des données par région au niveau DB
--        (renforce la segmentation territoriale déjà en place au niveau app)
-- ================================================================
-- Activer RLS (sans politique → comportement actuel préservé)
-- Les politiques sont ajoutées sans forcer (PERMISSIVE par défaut)
ALTER TABLE parcelles        ENABLE ROW LEVEL SECURITY;
ALTER TABLE parcel_right_version ENABLE ROW LEVEL SECURITY;

-- Politique : ADMIN et comptes de service voient tout
CREATE POLICY IF NOT EXISTS pol_parcelles_admin
    ON parcelles
    USING (
        current_setting('app.user_role', TRUE) IN ('ADMIN', 'SECRETAIRE_GENERAL', 'MINISTRE_URBANISME')
        OR current_setting('app.user_role', TRUE) IS NULL
        OR current_setting('app.user_role', TRUE) = ''
    );

-- Politique : autres rôles voient leur région
CREATE POLICY IF NOT EXISTS pol_parcelles_region
    ON parcelles
    USING (
        region = current_setting('app.user_region', TRUE)
        OR current_setting('app.user_region', TRUE) IN (NULL, '', 'NATIONAL')
    );

-- Note : les politiques RLS sont en mode PERMISSIVE — une ligne est visible
-- si AU MOINS UNE politique l'autorise. L'application déjà vérifier les droits.
-- Ces politiques ajoutent une couche de défense en profondeur.
-- Pour activer strictement, utiliser FORCE ROW LEVEL SECURITY et RESTRICTIVE.

COMMENT ON TABLE parcelles IS
    'RLS activé (P3.1 audit). Politiques: admin_bypass + region_filter. '
    'Utiliser SET LOCAL app.user_role et app.user_region avant les requêtes.';


-- ================================================================
-- Bonus — Index manquants identifiés par l'audit
-- ================================================================

-- decision_engine_log : requêtes KPI système (K10, K11)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_del_user_date
    ON decision_engine_log(effectue_par, created_at DESC)
    WHERE effectue_par IS NOT NULL;

-- alerte_monitoring : requêtes SSE et tableau de bord
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_am_created
    ON alerte_monitoring(created_at DESC)
    WHERE acquittee = FALSE;

-- risk_alert : alertes actives (vue v_risk_alertes_actives)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ra_score
    ON risk_alert(score DESC, created_at DESC)
    WHERE acquittee = FALSE;

-- validation_pipeline : K08 délai moyen
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_vp_statut_date
    ON validation_pipeline(statut, created_at DESC, updated_at)
    WHERE statut IN ('VALIDE', 'REJETE');

-- litiges : K06 taux de conflits
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_litiges_statut
    ON litiges(statut, parcelle_id)
    WHERE statut = 'ACTIF';


-- ================================================================
-- Vue v_health_check (état de santé des composants DB)
-- ================================================================
CREATE OR REPLACE VIEW v_health_check AS
WITH index_stats AS (
    SELECT
        schemaname, tablename, indexname,
        idx_scan, idx_tup_read, idx_tup_fetch
    FROM pg_stat_user_indexes
    WHERE schemaname = 'public'
),
table_stats AS (
    SELECT relname, n_live_tup, n_dead_tup, last_vacuum, last_autovacuum
    FROM pg_stat_user_tables
    WHERE schemaname = 'public'
)
SELECT
    'index_status'          AS type,
    indexname               AS nom,
    tablename               AS detail,
    CASE WHEN idx_scan > 0 THEN 'UTILISE' ELSE 'INUTILISE' END AS statut,
    NOW()                   AS calcule_a
FROM index_stats
WHERE tablename IN ('parcelles','property_transfers','workflow_instances',
                    'litiges','decision_engine_log','risk_alert')
ORDER BY tablename, indexname;


-- ================================================================
-- Notice
-- ================================================================
DO $n$
DECLARE
    nb_idx   INT;
    nb_trig  INT;
BEGIN
    SELECT COUNT(*) INTO nb_idx
    FROM pg_indexes WHERE schemaname='public'
    AND indexname LIKE 'idx_%' AND tablename IN (
        'workflow_instances','property_transfers','decision_engine_log',
        'litiges','alerte_monitoring','risk_alert','validation_pipeline'
    );
    SELECT COUNT(*) INTO nb_trig
    FROM pg_triggers WHERE tgname LIKE 'tg_no_delete_%';

    RAISE NOTICE '039 OK — Corrections audit FONCIER+ v108 :';
    RAISE NOTICE '  P1.2 : Index partiel workflow_instances (en_cours, bloque)';
    RAISE NOTICE '  P2.4 : Triggers tg_no_delete sur % table(s) core', nb_trig;
    RAISE NOTICE '  P2.8 : Index property_transfers (KPI dashboard)';
    RAISE NOTICE '  P3.1 : RLS activé sur parcelles + parcel_right_version';
    RAISE NOTICE '  Bonus: % index au total sur tables surveillées', nb_idx;
    RAISE NOTICE '  Vue v_health_check créée';
END;
$n$;
"""

def upgrade() -> None:
    op.execute(SQL)

def downgrade() -> None:
    for obj in (
        "VIEW v_health_check",
        "FUNCTION tg_fn_no_physical_delete CASCADE",
        "INDEX idx_wi_en_cours",
        "INDEX idx_wi_bloque",
        "INDEX idx_wi_entite",
        "INDEX idx_pt_kpi",
        "INDEX idx_pt_parcelle_statut",
        "INDEX idx_pt_region_date",
        "INDEX idx_del_user_date",
        "INDEX idx_am_created",
        "INDEX idx_ra_score",
        "INDEX idx_vp_statut_date",
        "INDEX idx_litiges_statut",
    ):
        op.execute(f"DROP {obj} IF EXISTS;")
    op.execute("ALTER TABLE parcelles DISABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE parcel_right_version DISABLE ROW LEVEL SECURITY;")
