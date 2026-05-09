"""035_simulation_engine

FONCIER+ ── Migration 035 : Moteur de simulation des opérations foncières

Tables créées :
  1. simulation_log              ── journal append-only de toutes les simulations
  2. simulation_parcelle_snapshot── snapshot de l'état d'une parcelle à la simulation
  3. simulation_config           ── paramètres de configuration du moteur

Vues créées :
  4. v_simulation_sante          ── santé du moteur (24h)
  5. v_simulation_stats_30j      ── statistiques 30 jours par opération
  6. v_simulations_bloquees      ── simulations avec impacts bloquants (7 j)

Fonctions :
  7. simulation_taux_succes()    ── taux de succès par type d'opération

Revision ID: 035_simulation_engine
Revises: 034_logic_validator
"""
from alembic import op
import sqlalchemy as sa

revision      = "035_simulation_engine"
down_revision = "034_logic_validator"
branch_labels = None
depends_on    = None

SQL = r"""
-- ================================================================
-- 1.  simulation_log  (append-only)
--     Journal de toutes les simulations effectuées.
--     Chaque simulation est immuable une fois enregistrée.
-- ================================================================
CREATE TABLE IF NOT EXISTS simulation_log (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    simulation_id       UUID        NOT NULL UNIQUE,
    operation_type      VARCHAR(30) NOT NULL
        CHECK (operation_type IN (
            'MUTATION','HYPOTHEQUE','MAINLEVEE','FUSION','SCISSION',
            'LOTISSEMENT','EXPROPRIATION','SUCCESSION','IMMATRICULATION',
            'RECTIFICATION','SERVITUDE','MISE_EN_GELE'
        )),
    -- Parcelles impliquées
    parcelle_ids        UUID[]      NOT NULL DEFAULT '{}',
    -- Résultat de la simulation
    statut              VARCHAR(20) NOT NULL
        CHECK (statut IN ('SUCCES','AVERTISSEMENT','ECHEC')),
    nb_bloquants        INT         NOT NULL DEFAULT 0,
    nb_avertissements   INT         NOT NULL DEFAULT 0,
    nb_infos            INT         NOT NULL DEFAULT 0,
    -- Rapport complet en JSON
    rapport_json        JSONB,
    sha256_rapport      VARCHAR(64) NOT NULL DEFAULT '',
    -- Performances
    duree_ms            INT,
    -- Résultat final
    autorisee           BOOLEAN     NOT NULL DEFAULT FALSE,
    -- Contexte
    user_id             UUID        REFERENCES users(id) ON DELETE SET NULL,
    region              VARCHAR(10),
    created_at          TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- Index de recherche
CREATE INDEX IF NOT EXISTS idx_sl_op       ON simulation_log(operation_type);
CREATE INDEX IF NOT EXISTS idx_sl_statut   ON simulation_log(statut);
CREATE INDEX IF NOT EXISTS idx_sl_user     ON simulation_log(user_id);
CREATE INDEX IF NOT EXISTS idx_sl_date     ON simulation_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sl_blocked  ON simulation_log(autorisee)
    WHERE autorisee = FALSE;
CREATE INDEX IF NOT EXISTS idx_sl_parcelles
    ON simulation_log USING GIN (parcelle_ids);

-- Trigger append-only
CREATE OR REPLACE FUNCTION tg_sl_readonly()
RETURNS TRIGGER AS $fn$
BEGIN
    RAISE EXCEPTION 'simulation_log est APPEND-ONLY';
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_sl_no_modify ON simulation_log;
CREATE TRIGGER tg_sl_no_modify
    BEFORE UPDATE OR DELETE ON simulation_log
    FOR EACH ROW EXECUTE FUNCTION tg_sl_readonly();


-- ================================================================
-- 2.  simulation_parcelle_snapshot
--     Capture l'état exact d'une parcelle au moment de la simulation.
--     Permet de rejouer une simulation avec les mêmes données.
-- ================================================================
CREATE TABLE IF NOT EXISTS simulation_parcelle_snapshot (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    simulation_id   UUID        NOT NULL REFERENCES simulation_log(simulation_id)
                                ON DELETE CASCADE,
    parcelle_id     UUID        NOT NULL,
    nicad           VARCHAR(30),
    statut_parcelle VARCHAR(30),
    is_gele         BOOLEAN,
    surface_m2      NUMERIC(12,2),
    region          VARCHAR(10),
    somme_droits    NUMERIC(5,2),
    nb_verrous      INT,
    nb_litiges      INT,
    snapshot_json   JSONB,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sps_sim ON simulation_parcelle_snapshot(simulation_id);
CREATE INDEX IF NOT EXISTS idx_sps_par ON simulation_parcelle_snapshot(parcelle_id);


-- ================================================================
-- 3.  simulation_config
--     Paramètres de configuration du moteur de simulation.
-- ================================================================
CREATE TABLE IF NOT EXISTS simulation_config (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    cle         VARCHAR(60) NOT NULL UNIQUE,
    valeur      TEXT        NOT NULL,
    description TEXT,
    modifie_par UUID        REFERENCES users(id),
    updated_at  TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- Configuration par défaut
INSERT INTO simulation_config (cle, valeur, description) VALUES
    ('timeout_ms',         '8000',  'Timeout global de simulation (ms)'),
    ('max_impacts',        '50',    'Nombre maximum d''impacts par simulation'),
    ('max_parcelles',      '10',    'Nombre maximum de parcelles par simulation'),
    ('activer_s6_topologie','true', 'Activer la couche S6 topologie (si BGU)'),
    ('surface_min_lot_m2', '200',   'Surface minimale par lot (m²) en S6'),
    ('simuler_regles_metier','true','Activer la couche S2 (decision_engine)')
ON CONFLICT (cle) DO NOTHING;


-- ================================================================
-- 4.  Vue v_simulation_sante  (24h)
-- ================================================================
CREATE OR REPLACE VIEW v_simulation_sante AS
WITH s AS (
    SELECT
        statut,
        COUNT(*)                            AS nb,
        AVG(duree_ms)::INT                  AS duree_moy,
        MAX(duree_ms)                       AS duree_max,
        SUM(nb_bloquants)                   AS total_bloquants,
        SUM(nb_avertissements)              AS total_avert
    FROM simulation_log
    WHERE created_at >= NOW() - INTERVAL '24 hours'
    GROUP BY statut
)
SELECT
    COALESCE(SUM(s.nb) FILTER (WHERE s.statut='SUCCES'),         0) AS succes_24h,
    COALESCE(SUM(s.nb) FILTER (WHERE s.statut='AVERTISSEMENT'),  0) AS avert_24h,
    COALESCE(SUM(s.nb) FILTER (WHERE s.statut='ECHEC'),          0) AS echecs_24h,
    COALESCE(SUM(s.total_bloquants), 0)                             AS impacts_bloquants_24h,
    COALESCE(AVG(s.duree_moy), 0)::INT                             AS duree_moy_ms,
    COALESCE(MAX(s.duree_max), 0)                                   AS duree_max_ms,
    NOW()                                                           AS calcule_a
FROM s;


-- ================================================================
-- 5.  Vue v_simulation_stats_30j  (par type d'opération)
-- ================================================================
CREATE OR REPLACE VIEW v_simulation_stats_30j AS
SELECT
    operation_type,
    COUNT(*)                                            AS nb_total,
    COUNT(*) FILTER (WHERE statut = 'SUCCES')           AS nb_succes,
    COUNT(*) FILTER (WHERE statut = 'ECHEC')            AS nb_echecs,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE autorisee = TRUE)
        / NULLIF(COUNT(*), 0), 1
    )                                                   AS taux_autorisation_pct,
    AVG(duree_ms)::INT                                  AS duree_moy_ms,
    AVG(nb_bloquants)::NUMERIC(4,1)                    AS moy_bloquants,
    MAX(created_at)                                     AS derniere_simulation
FROM simulation_log
WHERE created_at >= NOW() - INTERVAL '30 days'
GROUP BY operation_type
ORDER BY nb_total DESC;


-- ================================================================
-- 6.  Vue v_simulations_bloquees  (7 derniers jours)
-- ================================================================
CREATE OR REPLACE VIEW v_simulations_bloquees AS
SELECT
    sl.simulation_id,
    sl.operation_type,
    sl.statut,
    sl.nb_bloquants,
    sl.nb_avertissements,
    sl.parcelle_ids,
    sl.region,
    sl.duree_ms,
    sl.created_at,
    -- Extraire les messages bloquants du JSON
    (
        SELECT ARRAY_AGG(impact->>'message' ORDER BY impact->>'couche')
        FROM jsonb_array_elements(sl.rapport_json->'impacts') AS impact
        WHERE impact->>'niveau' = 'BLOQUANT'
        LIMIT 5
    ) AS messages_bloquants
FROM simulation_log sl
WHERE sl.statut = 'ECHEC'
  AND sl.created_at >= NOW() - INTERVAL '7 days'
ORDER BY sl.created_at DESC;


-- ================================================================
-- 7.  Fonction simulation_taux_succes()
-- ================================================================
CREATE OR REPLACE FUNCTION simulation_taux_succes(
    p_jours INT DEFAULT 30
) RETURNS TABLE (
    operation_type      TEXT,
    nb_simulations      BIGINT,
    taux_succes_pct     NUMERIC,
    taux_echec_pct      NUMERIC,
    impact_bloquant_moy NUMERIC,
    duree_moy_ms        NUMERIC
) AS $fn$
BEGIN
    RETURN QUERY
    SELECT
        sl.operation_type::TEXT,
        COUNT(*)                                                     AS nb_simulations,
        ROUND(100.0 * COUNT(*) FILTER (WHERE autorisee=TRUE)
              / NULLIF(COUNT(*),0), 1)                               AS taux_succes_pct,
        ROUND(100.0 * COUNT(*) FILTER (WHERE statut='ECHEC')
              / NULLIF(COUNT(*),0), 1)                               AS taux_echec_pct,
        ROUND(AVG(nb_bloquants), 1)                                  AS impact_bloquant_moy,
        ROUND(AVG(duree_ms), 0)                                      AS duree_moy_ms
    FROM simulation_log sl
    WHERE sl.created_at >= NOW() - (p_jours || ' days')::INTERVAL
    GROUP BY sl.operation_type
    ORDER BY nb_simulations DESC;
END;
$fn$ LANGUAGE plpgsql;


-- Notice
DO $notice$
BEGIN
    RAISE NOTICE '035 OK — Moteur de simulation (6 couches) :';
    RAISE NOTICE '  simulation_log (append-only)';
    RAISE NOTICE '  simulation_parcelle_snapshot (état au moment de la simulation)';
    RAISE NOTICE '  simulation_config (% paramètres)', (SELECT COUNT(*) FROM simulation_config);
    RAISE NOTICE '  Vues: v_simulation_sante, v_simulation_stats_30j, v_simulations_bloquees';
    RAISE NOTICE '  Fonction: simulation_taux_succes(jours)';
    RAISE NOTICE '  Opérations supportées: MUTATION, HYPOTHEQUE, MAINLEVEE, FUSION,';
    RAISE NOTICE '    SCISSION, LOTISSEMENT, EXPROPRIATION, SUCCESSION,';
    RAISE NOTICE '    IMMATRICULATION, RECTIFICATION, SERVITUDE, MISE_EN_GELE';
END;
$notice$;
"""

def upgrade() -> None:
    op.execute(SQL)

def downgrade() -> None:
    for obj in (
        "FUNCTION simulation_taux_succes CASCADE",
        "VIEW v_simulations_bloquees",
        "VIEW v_simulation_stats_30j",
        "VIEW v_simulation_sante",
        "TABLE simulation_parcelle_snapshot CASCADE",
        "TABLE simulation_log CASCADE",
        "TABLE simulation_config CASCADE",
    ):
        op.execute(f"DROP {obj} IF EXISTS;")
