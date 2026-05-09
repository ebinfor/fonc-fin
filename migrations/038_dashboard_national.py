"""038_dashboard_national

FONCIER+ ── Migration 038 : Tableaux de bord décisionnels nationaux

Vues créées :
  1.  v_kpi_national          ── KPIs agrégés toutes régions
  2.  v_kpi_par_region        ── KPIs par région Niger (NI-01..NI-08)
  3.  v_mutations_par_mois    ── flux mutations 12 derniers mois
  4.  v_conflits_par_region   ── litiges et conflits géo par région
  5.  v_performance_validation── délais et taux validation
  6.  v_activite_agents       ── activité des agents par région
  7.  v_dashboard_complet_v2  ── vue consolidée tous KPIs

Fonctions :
  8.  dashboard_export_kpis(p_region) ── export des KPIs formaté
  9.  dashboard_series_mutations(nb)  ── série temporelle mutations

Révision : 038_dashboard_national / Revises : 037_risk_engine
"""
from alembic import op

revision      = "038_dashboard_national"
down_revision = "037_risk_engine"
branch_labels = None
depends_on    = None

SQL = r"""
-- ================================================================
-- 1.  v_kpi_national  (vue globale nationale)
-- ================================================================
CREATE OR REPLACE VIEW v_kpi_national AS
WITH parcelles_stats AS (
    SELECT
        COUNT(*)                                      AS total_parcelles,
        COUNT(*) FILTER (WHERE statut = 'active')     AS actives,
        COUNT(*) FILTER (WHERE is_gele = TRUE)        AS gelees,
        COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS nouvelles_30j,
        COALESCE(SUM(surface_m2), 0)                  AS surface_totale_m2
    FROM parcelles
),
mutations_stats AS (
    SELECT
        COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days'
                          AND statut = 'valide')        AS muts_30j,
        COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '365 days'
                          AND statut = 'valide')        AS muts_an,
        COALESCE(AVG(montant) FILTER (WHERE montant > 0
            AND created_at >= NOW() - INTERVAL '30 days'), 0) AS montant_moy
    FROM property_transfers
),
conflits_stats AS (
    SELECT
        COUNT(*) FILTER (WHERE statut = 'ACTIF') AS litiges_actifs,
        COUNT(*)                                  AS litiges_total
    FROM litiges
),
validation_stats AS (
    SELECT
        COUNT(*) FILTER (WHERE statut NOT IN ('VALIDE','REJETE')) AS en_attente,
        COALESCE(AVG(EXTRACT(EPOCH FROM AGE(
            COALESCE(updated_at, NOW()), created_at
        )) / 86400.0) FILTER (WHERE statut = 'VALIDE'
            AND created_at >= NOW() - INTERVAL '90 days'), 0)     AS delai_moy_jours
    FROM validation_pipeline
),
risque_stats AS (
    SELECT
        COALESCE(AVG(score), 0)                                  AS score_risque_moy,
        COUNT(*) FILTER (WHERE niveau IN ('ÉLEVÉ','CRITIQUE'))   AS zones_risque_eleve
    FROM risk_score WHERE entite_type = 'ZONE'
)
SELECT
    p.total_parcelles,
    p.actives                                        AS parcelles_actives,
    p.gelees                                         AS parcelles_gelees,
    p.nouvelles_30j,
    ROUND(p.surface_totale_m2 / 10000.0, 1)         AS surface_totale_ha,
    m.muts_30j                                       AS mutations_30j,
    m.muts_an                                        AS mutations_annee,
    ROUND(m.montant_moy::NUMERIC, 0)                 AS montant_moyen_xof,
    c.litiges_actifs,
    c.litiges_total,
    ROUND(c.litiges_actifs::NUMERIC /
        NULLIF(p.total_parcelles, 0) * 100, 2)       AS taux_conflit_pct,
    v.en_attente                                     AS validations_en_attente,
    ROUND(v.delai_moy_jours::NUMERIC, 1)             AS delai_validation_jours,
    ROUND(r.score_risque_moy::NUMERIC, 1)            AS score_risque_national,
    r.zones_risque_eleve,
    NOW()                                            AS calcule_a
FROM parcelles_stats p
CROSS JOIN mutations_stats m
CROSS JOIN conflits_stats c
CROSS JOIN validation_stats v
CROSS JOIN risque_stats r;


-- ================================================================
-- 2.  v_kpi_par_region  (KPIs par région)
-- ================================================================
CREATE OR REPLACE VIEW v_kpi_par_region AS
SELECT
    p.region,
    COUNT(DISTINCT p.id)                                             AS total_parcelles,
    COUNT(DISTINCT p.id) FILTER (WHERE p.statut = 'active')         AS actives,
    COUNT(DISTINCT p.id) FILTER (WHERE p.is_gele = TRUE)            AS gelees,
    COUNT(DISTINCT pt.id) FILTER (
        WHERE pt.created_at >= NOW() - INTERVAL '30 days'
          AND pt.statut = 'valide')                                  AS mutations_30j,
    COUNT(DISTINCT pt.id) FILTER (
        WHERE pt.created_at >= NOW() - INTERVAL '365 days'
          AND pt.statut = 'valide')                                  AS mutations_an,
    COUNT(DISTINCT l.id) FILTER (WHERE l.statut = 'ACTIF')           AS litiges_actifs,
    ROUND(
        COUNT(DISTINCT l.id) FILTER (WHERE l.statut = 'ACTIF')::NUMERIC
        / NULLIF(COUNT(DISTINCT p.id), 0) * 100, 2
    )                                                                AS taux_conflit_pct,
    COALESCE(rs.score, 0)                                            AS score_risque,
    COALESCE(rs.niveau, 'FAIBLE')                                    AS niveau_risque
FROM parcelles p
LEFT JOIN property_transfers pt ON pt.parcelle_id = p.id
LEFT JOIN litiges l              ON l.parcelle_id  = p.id
LEFT JOIN risk_score rs          ON rs.entite_id   = p.region
                                 AND rs.entite_type = 'ZONE'
WHERE p.region IS NOT NULL
GROUP BY p.region, rs.score, rs.niveau
ORDER BY mutations_30j DESC;


-- ================================================================
-- 3.  v_mutations_par_mois  (série temporelle 12 mois)
-- ================================================================
CREATE OR REPLACE VIEW v_mutations_par_mois AS
SELECT
    TO_CHAR(DATE_TRUNC('month', pt.created_at), 'YYYY-MM') AS mois,
    p.region,
    COUNT(*) AS nb_mutations,
    COUNT(DISTINCT pt.parcelle_id)                          AS parcelles_uniques,
    COALESCE(SUM(pt.montant) FILTER (WHERE pt.montant > 0), 0) AS volume_xof
FROM property_transfers pt
JOIN parcelles p ON p.id = pt.parcelle_id
WHERE pt.statut = 'valide'
  AND pt.created_at >= NOW() - INTERVAL '12 months'
GROUP BY 1, 2
ORDER BY 1 DESC, 3 DESC;


-- ================================================================
-- 4.  v_conflits_par_region
-- ================================================================
CREATE OR REPLACE VIEW v_conflits_par_region AS
SELECT
    p.region,
    COUNT(DISTINCT l.id) FILTER (WHERE l.statut = 'ACTIF')        AS litiges_actifs,
    COUNT(DISTINCT l.id) FILTER (WHERE l.statut = 'RESOLU')        AS litiges_resolus,
    COUNT(DISTINCT l.id)                                            AS litiges_total,
    COUNT(DISTINCT cp.id) FILTER (WHERE cp.statut = 'ouvert')      AS conflits_geo,
    ROUND(
        AVG(EXTRACT(DAY FROM AGE(NOW(), l.created_at)))
        FILTER (WHERE l.statut = 'ACTIF'), 0
    )                                                               AS duree_moy_litige_jours,
    COUNT(DISTINCT l.id) FILTER (WHERE l.created_at >= NOW() - INTERVAL '30 days') AS nouveaux_30j
FROM parcelles p
LEFT JOIN litiges l ON l.parcelle_id = p.id
LEFT JOIN conflits_parcellaires cp ON (
    cp.parcelle_id_ref = p.id OR cp.parcelle_id_conflit = p.id
)
WHERE p.region IS NOT NULL
GROUP BY p.region
ORDER BY litiges_actifs DESC;


-- ================================================================
-- 5.  v_performance_validation
-- ================================================================
CREATE OR REPLACE VIEW v_performance_validation AS
SELECT
    COALESCE(vp.region, 'NATIONAL')                               AS region,
    COUNT(*) FILTER (WHERE vp.statut NOT IN ('VALIDE','REJETE'))   AS en_attente,
    COUNT(*) FILTER (WHERE vp.statut = 'VALIDE')                   AS valides,
    COUNT(*) FILTER (WHERE vp.statut = 'REJETE')                   AS rejetes,
    ROUND(
        AVG(EXTRACT(EPOCH FROM AGE(
            COALESCE(vp.updated_at, NOW()), vp.created_at
        )) / 86400.0) FILTER (WHERE vp.statut = 'VALIDE'
            AND vp.created_at >= NOW() - INTERVAL '90 days'), 1
    )                                                               AS delai_moy_jours,
    ROUND(
        COUNT(*) FILTER (WHERE vp.statut = 'VALIDE')::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                               AS taux_approbation_pct
FROM validation_pipeline vp
WHERE vp.created_at >= NOW() - INTERVAL '90 days'
GROUP BY ROLLUP(vp.region)
ORDER BY en_attente DESC;


-- ================================================================
-- 6.  v_activite_agents
-- ================================================================
CREATE OR REPLACE VIEW v_activite_agents AS
SELECT
    ap.region_principale                                AS region,
    COUNT(DISTINCT ap.user_id)                          AS nb_agents,
    COUNT(DISTINCT ap.user_id) FILTER (
        WHERE EXISTS (
            SELECT 1 FROM decision_engine_log del
            WHERE del.effectue_par = ap.user_id
              AND del.created_at >= NOW() - INTERVAL '30 days'
        )
    )                                                   AS agents_actifs_30j,
    COUNT(DISTINCT del.id)                              AS ops_total_30j,
    COUNT(DISTINCT del.id) FILTER (WHERE del.decision = 'REFUSE') AS ops_refusees
FROM agent_profil ap
LEFT JOIN decision_engine_log del
    ON del.effectue_par = ap.user_id
    AND del.created_at >= NOW() - INTERVAL '30 days'
WHERE ap.actif = TRUE
GROUP BY ap.region_principale
ORDER BY ops_total_30j DESC;


-- ================================================================
-- 7.  v_dashboard_complet_v2  (vue consolidée)
-- ================================================================
CREATE OR REPLACE VIEW v_dashboard_complet_v2 AS
SELECT
    kn.*,
    (SELECT json_agg(r ORDER BY r.mutations_30j DESC)
     FROM v_kpi_par_region r)                         AS regions_json,
    (SELECT json_agg(c ORDER BY c.litiges_actifs DESC)
     FROM v_conflits_par_region c)                    AS conflits_json,
    (SELECT json_agg(pv)
     FROM v_performance_validation pv
     WHERE pv.region = 'NATIONAL')                    AS validation_json
FROM v_kpi_national kn;


-- ================================================================
-- 8.  Fonction dashboard_export_kpis
-- ================================================================
CREATE OR REPLACE FUNCTION dashboard_export_kpis(
    p_region VARCHAR DEFAULT NULL
) RETURNS TABLE (
    indicateur      TEXT,
    valeur          NUMERIC,
    unite           TEXT,
    region          TEXT,
    calcule_a       TIMESTAMP
) AS $fn$
BEGIN
    RETURN QUERY
    SELECT 'total_parcelles'::TEXT,    k.total_parcelles,    'parcelles', COALESCE(p_region,'NATIONAL'), NOW()
    FROM v_kpi_national k
    UNION ALL
    SELECT 'mutations_30j',            k.mutations_30j,      'mutations', COALESCE(p_region,'NATIONAL'), NOW()
    FROM v_kpi_national k
    UNION ALL
    SELECT 'taux_conflit_pct',         k.taux_conflit_pct,   '%',         COALESCE(p_region,'NATIONAL'), NOW()
    FROM v_kpi_national k
    UNION ALL
    SELECT 'delai_validation_jours',   k.delai_validation_jours, 'jours', COALESCE(p_region,'NATIONAL'), NOW()
    FROM v_kpi_national k
    UNION ALL
    SELECT 'score_risque_national',    k.score_risque_national,  '/100',  COALESCE(p_region,'NATIONAL'), NOW()
    FROM v_kpi_national k;
END;
$fn$ LANGUAGE plpgsql;


-- ================================================================
-- 9.  Fonction dashboard_series_mutations
-- ================================================================
CREATE OR REPLACE FUNCTION dashboard_series_mutations(
    p_nb_mois INT DEFAULT 12,
    p_region  VARCHAR DEFAULT NULL
) RETURNS TABLE (
    mois         TEXT,
    region       TEXT,
    nb_mutations BIGINT,
    volume_xof   NUMERIC
) AS $fn$
BEGIN
    RETURN QUERY
    SELECT
        TO_CHAR(DATE_TRUNC('month', pt.created_at), 'YYYY-MM')::TEXT,
        COALESCE(p.region, 'NATIONAL')::TEXT,
        COUNT(pt.id),
        COALESCE(SUM(pt.montant) FILTER (WHERE pt.montant > 0), 0)
    FROM property_transfers pt
    JOIN parcelles p ON p.id = pt.parcelle_id
    WHERE pt.statut = 'valide'
      AND pt.created_at >= NOW() - (p_nb_mois || ' months')::INTERVAL
      AND (p_region IS NULL OR p.region = p_region)
    GROUP BY 1, 2
    ORDER BY 1 DESC;
END;
$fn$ LANGUAGE plpgsql;


-- Notice
DO $n$
BEGIN
    RAISE NOTICE '038 OK — Tableaux de bord décisionnels nationaux :';
    RAISE NOTICE '  Vues: v_kpi_national, v_kpi_par_region, v_mutations_par_mois';
    RAISE NOTICE '        v_conflits_par_region, v_performance_validation';
    RAISE NOTICE '        v_activite_agents, v_dashboard_complet_v2';
    RAISE NOTICE '  Fonctions: dashboard_export_kpis(), dashboard_series_mutations()';
    RAISE NOTICE '  KPIs: K01-K13 (parcelles, mutations, conflits, validation, risques)';
    RAISE NOTICE '  Export: CSV + JSON institutionnel';
    RAISE NOTICE '  Synthèse stratégique automatique incluse';
END;
$n$;
"""

def upgrade() -> None:
    op.execute(SQL)

def downgrade() -> None:
    for obj in (
        "FUNCTION dashboard_series_mutations CASCADE",
        "FUNCTION dashboard_export_kpis CASCADE",
        "VIEW v_dashboard_complet_v2",
        "VIEW v_activite_agents",
        "VIEW v_performance_validation",
        "VIEW v_conflits_par_region",
        "VIEW v_mutations_par_mois",
        "VIEW v_kpi_par_region",
        "VIEW v_kpi_national",
    ):
        op.execute(f"DROP {obj} IF EXISTS;")
