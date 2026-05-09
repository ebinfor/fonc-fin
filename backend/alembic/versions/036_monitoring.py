"""036_monitoring

FONCIER+ ── Migration 036 : Monitoring temps réel intelligent

Tables :
  1. alerte_monitoring        ── journal append-only des alertes
  2. monitoring_config        ── seuils et paramètres configurables

Vues :
  3. v_alertes_actives        ── alertes non acquittées
  4. v_monitoring_sante       ── santé système (24h)
  5. v_activite_recente       ── événements par catégorie (1h)

Fonctions :
  6. monitoring_stats_par_heure()  ── volume par heure (48h)

Révision : 036_monitoring / Revises : 035_simulation_engine
"""
from alembic import op
import sqlalchemy as sa

revision      = "036_monitoring"
down_revision = "035_simulation_engine"
branch_labels = None
depends_on    = None

SQL = r"""
-- ================================================================
-- 1.  alerte_monitoring  (append-only)
-- ================================================================
CREATE TABLE IF NOT EXISTS alerte_monitoring (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    alerte_id       UUID        NOT NULL UNIQUE,
    niveau          VARCHAR(10) NOT NULL CHECK (niveau IN ('INFO','WARNING','CRITICAL')),
    detecteur       VARCHAR(50) NOT NULL,
    titre           VARCHAR(200) NOT NULL,
    message         TEXT        NOT NULL,
    details_json    JSONB,
    acquittee       BOOLEAN     NOT NULL DEFAULT FALSE,
    acquittee_par   UUID        REFERENCES users(id) ON DELETE SET NULL,
    acquittee_at    TIMESTAMP,
    sha256          VARCHAR(64) NOT NULL DEFAULT '',
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_am_niveau    ON alerte_monitoring(niveau);
CREATE INDEX IF NOT EXISTS idx_am_detecteur ON alerte_monitoring(detecteur);
CREATE INDEX IF NOT EXISTS idx_am_acquittee ON alerte_monitoring(acquittee) WHERE acquittee = FALSE;
CREATE INDEX IF NOT EXISTS idx_am_date      ON alerte_monitoring(created_at DESC);

-- Trigger append-only (colonnes immuables sauf acquittement)
CREATE OR REPLACE FUNCTION tg_am_guard()
RETURNS TRIGGER AS $fn$
BEGIN
    -- Seul l'acquittement est autorisé
    IF OLD.titre != NEW.titre OR OLD.message != NEW.message
       OR OLD.niveau != NEW.niveau OR OLD.detecteur != NEW.detecteur THEN
        RAISE EXCEPTION 'alerte_monitoring : seul acquittee peut être modifié';
    END IF;
    IF NEW.acquittee = TRUE AND OLD.acquittee = FALSE THEN
        NEW.acquittee_at := NOW();
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_am_guard ON alerte_monitoring;
CREATE TRIGGER tg_am_guard
    BEFORE UPDATE ON alerte_monitoring
    FOR EACH ROW EXECUTE FUNCTION tg_am_guard();


-- ================================================================
-- 2.  monitoring_config
-- ================================================================
CREATE TABLE IF NOT EXISTS monitoring_config (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    cle             VARCHAR(60) NOT NULL UNIQUE,
    valeur          TEXT        NOT NULL,
    description     TEXT,
    modifie_par     UUID        REFERENCES users(id),
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);

INSERT INTO monitoring_config (cle, valeur, description) VALUES
    ('poll_interval_sec',           '15',   'Intervalle de polling (secondes)'),
    ('seuil_workflows_bloques',     '5',    'Nb workflows bloqués → CRITICAL'),
    ('seuil_validations_refusees',  '10',   'Nb refus / 5min → WARNING'),
    ('seuil_surcharge_ops',         '200',  'Nb ops / 5min → WARNING'),
    ('seuil_ratio_echec_pct',       '40',   '% échecs règles / 1h → WARNING'),
    ('seuil_verrous_actifs',        '20',   'Nb verrous nets / 1h → WARNING'),
    ('seuil_acces_meme_user',       '50',   'Nb ops / user / 5min → WARNING'),
    ('max_clients_sse',             '50',   'Max clients SSE simultanés'),
    ('detecteur_d1_actif',          'true', 'Activer D1 WorkflowBlockage'),
    ('detecteur_d2_actif',          'true', 'Activer D2 Surcharge'),
    ('detecteur_d3_actif',          'true', 'Activer D3 ConflitValidation'),
    ('detecteur_d4_actif',          'true', 'Activer D4 AccesInhabituel'),
    ('detecteur_d5_actif',          'true', 'Activer D5 RatioEchec'),
    ('detecteur_d6_actif',          'true', 'Activer D6 VerrouCadeau')
ON CONFLICT (cle) DO NOTHING;


-- ================================================================
-- 3.  Vue v_alertes_actives
-- ================================================================
CREATE OR REPLACE VIEW v_alertes_actives AS
SELECT
    alerte_id, niveau, detecteur, titre, message,
    details_json, sha256, created_at
FROM alerte_monitoring
WHERE acquittee = FALSE
ORDER BY
    CASE niveau WHEN 'CRITICAL' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END,
    created_at DESC;


-- ================================================================
-- 4.  Vue v_monitoring_sante  (24h)
-- ================================================================
CREATE OR REPLACE VIEW v_monitoring_sante AS
WITH alertes_24h AS (
    SELECT
        niveau,
        COUNT(*) AS nb,
        COUNT(*) FILTER (WHERE acquittee = FALSE) AS nb_actives
    FROM alerte_monitoring
    WHERE created_at >= NOW() - INTERVAL '24 hours'
    GROUP BY niveau
),
systeme AS (
    SELECT
        (SELECT COUNT(*) FROM parcelles   WHERE statut='active')               AS parcelles_actives,
        (SELECT COUNT(*) FROM workflow_instances WHERE statut='en_cours')       AS wf_en_cours,
        (SELECT COUNT(*) FROM workflow_instances
         WHERE statut='en_cours' AND updated_at < NOW() - INTERVAL '2 hours')  AS wf_bloques,
        (SELECT COUNT(*) FROM entity_lock
         WHERE actif=TRUE AND (date_expiration IS NULL OR date_expiration>NOW())) AS verrous_actifs,
        (SELECT COUNT(*) FROM regle_metier WHERE actif=TRUE AND sandbox_valide=TRUE) AS regles_actives
)
SELECT
    s.parcelles_actives, s.wf_en_cours, s.wf_bloques,
    s.verrous_actifs, s.regles_actives,
    COALESCE(SUM(a.nb) FILTER (WHERE a.niveau='CRITICAL'), 0) AS alertes_critical_24h,
    COALESCE(SUM(a.nb) FILTER (WHERE a.niveau='WARNING'),  0) AS alertes_warning_24h,
    COALESCE(SUM(a.nb) FILTER (WHERE a.niveau='INFO'),     0) AS alertes_info_24h,
    COALESCE(SUM(a.nb_actives), 0)                             AS alertes_actives_total,
    NOW()                                                       AS calcule_a
FROM systeme s
CROSS JOIN alertes_24h a
GROUP BY s.parcelles_actives, s.wf_en_cours, s.wf_bloques,
         s.verrous_actifs, s.regles_actives;


-- ================================================================
-- 5.  Vue v_activite_recente  (1h, par catégorie/opération)
-- ================================================================
CREATE OR REPLACE VIEW v_activite_recente AS
WITH combined AS (
    -- audit_immutable (parcelles, etc.)
    SELECT 'PARCELLE'    AS categorie, operation, created_at, effectue_par AS user_id
    FROM audit_immutable
    WHERE created_at >= NOW() - INTERVAL '1 hour'
      AND entite_type IN ('parcelle','parcelles')
    UNION ALL
    -- decision_engine_log (validations)
    SELECT 'VALIDATION', operation, created_at, effectue_par
    FROM decision_engine_log
    WHERE created_at >= NOW() - INTERVAL '1 hour'
    UNION ALL
    -- sql_validation_log (règles)
    SELECT 'REGLE', statut, created_at, soumis_par
    FROM sql_validation_log
    WHERE created_at >= NOW() - INTERVAL '1 hour'
    UNION ALL
    -- simulation_log
    SELECT 'SIMULATION', operation_type, created_at, user_id
    FROM simulation_log
    WHERE created_at >= NOW() - INTERVAL '1 hour'
)
SELECT
    categorie,
    COUNT(*)                    AS nb_total,
    COUNT(DISTINCT user_id)     AS utilisateurs_uniques,
    MAX(created_at)             AS derniere_activite
FROM combined
GROUP BY categorie
ORDER BY nb_total DESC;


-- ================================================================
-- 6.  Fonction monitoring_stats_par_heure  (48h)
-- ================================================================
CREATE OR REPLACE FUNCTION monitoring_stats_par_heure(
    p_heures INT DEFAULT 48
) RETURNS TABLE (
    heure           TIMESTAMP,
    nb_parcelles    BIGINT,
    nb_validations  BIGINT,
    nb_regles       BIGINT,
    nb_simulations  BIGINT,
    nb_alertes      BIGINT
) AS $fn$
BEGIN
    RETURN QUERY
    WITH heures AS (
        SELECT generate_series(
            DATE_TRUNC('hour', NOW()) - ((p_heures-1) || ' hours')::INTERVAL,
            DATE_TRUNC('hour', NOW()),
            '1 hour'::INTERVAL
        ) AS h
    )
    SELECT
        h.h                                                                 AS heure,
        COUNT(DISTINCT ai.id) FILTER (WHERE ai.created_at >= h.h
            AND ai.created_at < h.h + INTERVAL '1 hour')                   AS nb_parcelles,
        COUNT(DISTINCT del.id) FILTER (WHERE del.created_at >= h.h
            AND del.created_at < h.h + INTERVAL '1 hour')                  AS nb_validations,
        COUNT(DISTINCT svl.id) FILTER (WHERE svl.created_at >= h.h
            AND svl.created_at < h.h + INTERVAL '1 hour')                  AS nb_regles,
        COUNT(DISTINCT sl.id) FILTER (WHERE sl.created_at >= h.h
            AND sl.created_at < h.h + INTERVAL '1 hour')                   AS nb_simulations,
        COUNT(DISTINCT am.id) FILTER (WHERE am.created_at >= h.h
            AND am.created_at < h.h + INTERVAL '1 hour')                   AS nb_alertes
    FROM heures h
    LEFT JOIN audit_immutable    ai  ON ai.created_at  >= h.h - INTERVAL '1 hour'
        AND ai.entite_type IN ('parcelle','parcelles')
    LEFT JOIN decision_engine_log del ON del.created_at >= h.h - INTERVAL '1 hour'
    LEFT JOIN sql_validation_log  svl ON svl.created_at >= h.h - INTERVAL '1 hour'
    LEFT JOIN simulation_log      sl  ON sl.created_at  >= h.h - INTERVAL '1 hour'
    LEFT JOIN alerte_monitoring   am  ON am.created_at  >= h.h - INTERVAL '1 hour'
    GROUP BY h.h
    ORDER BY h.h;
END;
$fn$ LANGUAGE plpgsql;


-- Notice
DO $n$
BEGIN
    RAISE NOTICE '036 OK — Monitoring temps réel :';
    RAISE NOTICE '  alerte_monitoring (append-only, acquittement contrôlé)';
    RAISE NOTICE '  monitoring_config (% paramètres)', (SELECT COUNT(*) FROM monitoring_config);
    RAISE NOTICE '  Détecteurs: D1 WorkflowBlockage, D2 Surcharge, D3 ConflitValidation';
    RAISE NOTICE '              D4 AccesInhabituel, D5 RatioEchec, D6 VerrouCadeau';
    RAISE NOTICE '  SSE: GET /monitoring/stream';
    RAISE NOTICE '  Tableau de bord: GET /monitoring/tableau-bord';
END;
$n$;
"""

def upgrade() -> None:
    op.execute(SQL)

def downgrade() -> None:
    for obj in (
        "FUNCTION monitoring_stats_par_heure CASCADE",
        "VIEW v_activite_recente",
        "VIEW v_monitoring_sante",
        "VIEW v_alertes_actives",
        "TABLE alerte_monitoring CASCADE",
        "TABLE monitoring_config CASCADE",
    ):
        op.execute(f"DROP {obj} IF EXISTS;")
