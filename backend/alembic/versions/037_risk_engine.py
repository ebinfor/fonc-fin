"""037_risk_engine

FONCIER+ ── Migration 037 : Moteur d'analyse prédictive des risques

Tables :
  1. risk_score            ── scores de risque par entité (UPSERT)
  2. risk_alert            ── alertes prédictives (append-only)
  3. risk_config           ── configuration des seuils

Vues :
  4. v_risk_carte          ── données cartographiques de risque par zone
  5. v_risk_parcelles_top  ── top 50 parcelles à risque
  6. v_risk_alertes_actives── alertes prédictives non acquittées
  7. v_risk_tableau_bord   ── métriques globales de risque

Fonctions :
  8. risk_score_historique() ── évolution du score sur N jours
  9. risk_zones_stats()     ── statistiques risque par région

Révision : 037_risk_engine / Revises : 036_monitoring
"""
from alembic import op
import sqlalchemy as sa

revision      = "037_risk_engine"
down_revision = "036_monitoring"
branch_labels = None
depends_on    = None

SQL = r"""
-- ================================================================
-- 1.  risk_score  — scores courants par entité (UPSERT)
--     Mise à jour à chaque recalcul ; garde l'historique via
--     risk_score_history si besoin.
-- ================================================================
CREATE TABLE IF NOT EXISTS risk_score (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    entite_type     VARCHAR(20) NOT NULL
        CHECK (entite_type IN ('PARCELLE','ZONE','UTILISATEUR','OPERATION')),
    entite_id       VARCHAR(100) NOT NULL,
    score           NUMERIC(5,2) NOT NULL CHECK (score >= 0 AND score <= 100),
    niveau          VARCHAR(10) NOT NULL
        CHECK (niveau IN ('FAIBLE','MOYEN','ÉLEVÉ','CRITIQUE')),
    facteurs_json   JSONB,
    signaux_json    JSONB,
    sha256          VARCHAR(64),
    calcule_par     UUID        REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    UNIQUE (entite_type, entite_id)
);

CREATE INDEX IF NOT EXISTS idx_rs_type_niveau ON risk_score(entite_type, niveau);
CREATE INDEX IF NOT EXISTS idx_rs_score       ON risk_score(score DESC);
CREATE INDEX IF NOT EXISTS idx_rs_updated     ON risk_score(updated_at DESC);


-- ================================================================
-- 2.  risk_alert  — alertes prédictives (append-only)
-- ================================================================
CREATE TABLE IF NOT EXISTS risk_alert (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    alerte_id       UUID        NOT NULL UNIQUE,
    niveau          VARCHAR(10) NOT NULL
        CHECK (niveau IN ('MOYEN','ÉLEVÉ','CRITIQUE')),
    entite_type     VARCHAR(20) NOT NULL,
    entite_id       VARCHAR(100) NOT NULL,
    titre           VARCHAR(200) NOT NULL,
    message         TEXT,
    score           NUMERIC(5,2),
    facteurs_json   JSONB,
    recommandation  TEXT,
    acquittee       BOOLEAN     NOT NULL DEFAULT FALSE,
    acquittee_par   UUID        REFERENCES users(id) ON DELETE SET NULL,
    acquittee_at    TIMESTAMP,
    sha256          VARCHAR(64),
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ra_niveau    ON risk_alert(niveau);
CREATE INDEX IF NOT EXISTS idx_ra_entite    ON risk_alert(entite_type, entite_id);
CREATE INDEX IF NOT EXISTS idx_ra_acquittee ON risk_alert(acquittee) WHERE acquittee = FALSE;
CREATE INDEX IF NOT EXISTS idx_ra_date      ON risk_alert(created_at DESC);

-- Append-only (sauf acquittement)
CREATE OR REPLACE FUNCTION tg_ra_guard()
RETURNS TRIGGER AS $fn$
BEGIN
    IF OLD.titre != NEW.titre OR OLD.score != NEW.score THEN
        RAISE EXCEPTION 'risk_alert est append-only (sauf acquittement)';
    END IF;
    IF NEW.acquittee = TRUE AND OLD.acquittee = FALSE THEN
        NEW.acquittee_at := NOW();
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_ra_guard ON risk_alert;
CREATE TRIGGER tg_ra_guard
    BEFORE UPDATE ON risk_alert
    FOR EACH ROW EXECUTE FUNCTION tg_ra_guard();


-- ================================================================
-- 3.  risk_config
-- ================================================================
CREATE TABLE IF NOT EXISTS risk_config (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    cle         VARCHAR(60) NOT NULL UNIQUE,
    valeur      TEXT        NOT NULL,
    description TEXT,
    modifie_par UUID        REFERENCES users(id),
    updated_at  TIMESTAMP   NOT NULL DEFAULT NOW()
);

INSERT INTO risk_config (cle, valeur, description) VALUES
    ('seuil_faible',               '25',   'Score ≤ X → risque FAIBLE'),
    ('seuil_moyen',                '50',   'Score ≤ X → risque MOYEN'),
    ('seuil_eleve',                '75',   'Score ≤ X → risque ÉLEVÉ'),
    ('poids_conflit_zone',         '25',   'Poids A1 ConflitZone'),
    ('poids_mutation_freq',        '20',   'Poids A2 MutationFreq'),
    ('poids_utilisateur',          '20',   'Poids A3 Utilisateur'),
    ('poids_densite_mutation',     '15',   'Poids A4 DensitéMutation'),
    ('poids_historique',           '10',   'Poids A5 Historique'),
    ('poids_operation',            '10',   'Poids A6 Opération'),
    ('fenetre_courte_jours',       '30',   'Fenêtre courte d''analyse (jours)'),
    ('fenetre_longue_jours',       '365',  'Fenêtre longue d''analyse (jours)'),
    ('batch_actif',                'true', 'Activer le recalcul batch périodique'),
    ('validation_renforcee_critique', 'true', 'Niveau validation +1 si score CRITIQUE')
ON CONFLICT (cle) DO NOTHING;


-- ================================================================
-- 4.  Vue v_risk_carte  (données cartographiques par zone/région)
-- ================================================================
CREATE OR REPLACE VIEW v_risk_carte AS
SELECT
    rs.entite_id                                AS region,
    rs.score,
    rs.niveau,
    rs.facteurs_json,
    rs.updated_at,
    -- Statistiques complémentaires
    (SELECT COUNT(*) FROM parcelles p WHERE p.region = rs.entite_id) AS nb_parcelles,
    (SELECT COUNT(*) FROM litiges l
     JOIN parcelles p2 ON p2.id = l.parcelle_id
     WHERE p2.region = rs.entite_id AND l.statut = 'ACTIF')          AS litiges_actifs,
    (SELECT COUNT(*) FROM property_transfers pt
     JOIN parcelles p3 ON p3.id = pt.parcelle_id
     WHERE p3.region = rs.entite_id
       AND pt.created_at >= NOW() - INTERVAL '30 days')              AS mutations_30j
FROM risk_score rs
WHERE rs.entite_type = 'ZONE'
ORDER BY rs.score DESC;


-- ================================================================
-- 5.  Vue v_risk_parcelles_top  (top 50 parcelles à risque)
-- ================================================================
CREATE OR REPLACE VIEW v_risk_parcelles_top AS
SELECT
    rs.entite_id,
    p.nicad,
    p.statut,
    p.region,
    p.commune,
    rs.score,
    rs.niveau,
    rs.facteurs_json,
    rs.updated_at
FROM risk_score rs
JOIN parcelles p ON p.id::TEXT = rs.entite_id
WHERE rs.entite_type = 'PARCELLE'
ORDER BY rs.score DESC
LIMIT 50;


-- ================================================================
-- 6.  Vue v_risk_alertes_actives
-- ================================================================
CREATE OR REPLACE VIEW v_risk_alertes_actives AS
SELECT
    alerte_id, niveau, entite_type, entite_id,
    titre, message, score, facteurs_json,
    recommandation, sha256, created_at
FROM risk_alert
WHERE acquittee = FALSE
ORDER BY
    CASE niveau WHEN 'CRITIQUE' THEN 1 WHEN 'ÉLEVÉ' THEN 2 ELSE 3 END,
    score DESC,
    created_at DESC;


-- ================================================================
-- 7.  Vue v_risk_tableau_bord
-- ================================================================
CREATE OR REPLACE VIEW v_risk_tableau_bord AS
WITH scores_actifs AS (
    SELECT niveau, COUNT(*) AS nb
    FROM risk_score
    GROUP BY niveau
),
alertes AS (
    SELECT niveau, COUNT(*) AS nb
    FROM risk_alert
    WHERE acquittee = FALSE
    GROUP BY niveau
),
top_zone AS (
    SELECT entite_id AS region, score
    FROM risk_score
    WHERE entite_type = 'ZONE'
    ORDER BY score DESC LIMIT 1
),
top_parcelle AS (
    SELECT entite_id, score
    FROM risk_score
    WHERE entite_type = 'PARCELLE'
    ORDER BY score DESC LIMIT 1
)
SELECT
    COALESCE(SUM(s.nb) FILTER (WHERE s.niveau='CRITIQUE'), 0) AS entites_critique,
    COALESCE(SUM(s.nb) FILTER (WHERE s.niveau='ÉLEVÉ'),    0) AS entites_eleve,
    COALESCE(SUM(s.nb) FILTER (WHERE s.niveau='MOYEN'),    0) AS entites_moyen,
    COALESCE(SUM(s.nb) FILTER (WHERE s.niveau='FAIBLE'),   0) AS entites_faible,
    COALESCE(SUM(a.nb) FILTER (WHERE a.niveau='CRITIQUE'), 0) AS alertes_critique,
    COALESCE(SUM(a.nb) FILTER (WHERE a.niveau='ÉLEVÉ'),    0) AS alertes_eleve,
    COALESCE(SUM(a.nb) FILTER (WHERE a.niveau='MOYEN'),    0) AS alertes_moyen,
    COALESCE((SELECT region  FROM top_zone),               '') AS zone_risque_max,
    COALESCE((SELECT score   FROM top_zone),                0) AS score_zone_max,
    COALESCE((SELECT entite_id FROM top_parcelle),         '') AS parcelle_risque_max,
    COALESCE((SELECT score     FROM top_parcelle),          0) AS score_parcelle_max,
    NOW()                                                       AS calcule_a
FROM scores_actifs s
CROSS JOIN alertes a
GROUP BY
    (SELECT region FROM top_zone),
    (SELECT score  FROM top_zone),
    (SELECT entite_id FROM top_parcelle),
    (SELECT score     FROM top_parcelle);


-- ================================================================
-- 8.  Fonction risk_score_historique  (évolution sur N jours)
--     Note : nécessite une table risk_score_history (optionnelle)
--     Ici on retourne les scores actuels agrégés par jour (simulation)
-- ================================================================
CREATE OR REPLACE FUNCTION risk_score_historique(
    p_entite_type VARCHAR DEFAULT 'ZONE',
    p_jours       INT     DEFAULT 30
) RETURNS TABLE (
    jour        DATE,
    entite_id   TEXT,
    score_moy   NUMERIC,
    nb_critique BIGINT,
    nb_eleve    BIGINT
) AS $fn$
BEGIN
    -- Sans historique, retourner les scores actuels répétés pour chaque jour
    -- (à enrichir avec une vraie table d'historique en production)
    RETURN QUERY
    SELECT
        (NOW() - (series || ' days')::INTERVAL)::DATE AS jour,
        rs.entite_id::TEXT,
        rs.score                                       AS score_moy,
        COUNT(*) FILTER (WHERE rs.niveau='CRITIQUE')   AS nb_critique,
        COUNT(*) FILTER (WHERE rs.niveau='ÉLEVÉ')      AS nb_eleve
    FROM generate_series(0, p_jours-1) AS series
    CROSS JOIN risk_score rs
    WHERE rs.entite_type = p_entite_type
    GROUP BY 1, 2, rs.score
    ORDER BY 1 DESC, score_moy DESC;
END;
$fn$ LANGUAGE plpgsql;


-- ================================================================
-- 9.  Fonction risk_zones_stats
-- ================================================================
CREATE OR REPLACE FUNCTION risk_zones_stats()
RETURNS TABLE (
    region          TEXT,
    score           NUMERIC,
    niveau          TEXT,
    nb_parcelles    BIGINT,
    litiges_actifs  BIGINT,
    mutations_30j   BIGINT,
    updated_at      TIMESTAMP
) AS $fn$
BEGIN
    RETURN QUERY
    SELECT
        rs.entite_id::TEXT,
        rs.score,
        rs.niveau::TEXT,
        (SELECT COUNT(*) FROM parcelles p WHERE p.region = rs.entite_id),
        (SELECT COUNT(*) FROM litiges l
         JOIN parcelles p2 ON p2.id = l.parcelle_id
         WHERE p2.region = rs.entite_id AND l.statut = 'ACTIF'),
        (SELECT COUNT(*) FROM property_transfers pt
         JOIN parcelles p3 ON p3.id = pt.parcelle_id
         WHERE p3.region = rs.entite_id
           AND pt.created_at >= NOW() - INTERVAL '30 days'),
        rs.updated_at
    FROM risk_score rs
    WHERE rs.entite_type = 'ZONE'
    ORDER BY rs.score DESC;
END;
$fn$ LANGUAGE plpgsql;


-- Notice
DO $n$
BEGIN
    RAISE NOTICE '037 OK — Moteur d''analyse prédictive des risques :';
    RAISE NOTICE '  6 analyseurs : A1 ConflitZone, A2 MutationFreq, A3 Utilisateur';
    RAISE NOTICE '               A4 DensitéMutation, A5 Historique, A6 Opération';
    RAISE NOTICE '  Scores : FAIBLE(0-25) MOYEN(26-50) ÉLEVÉ(51-75) CRITIQUE(76-100)';
    RAISE NOTICE '  Tables: risk_score (UPSERT), risk_alert (append-only)';
    RAISE NOTICE '  Vues: v_risk_carte, v_risk_parcelles_top, v_risk_alertes_actives';
    RAISE NOTICE '  Fonctions: risk_zones_stats(), risk_score_historique()';
END;
$n$;
"""

def upgrade() -> None:
    op.execute(SQL)

def downgrade() -> None:
    for obj in (
        "FUNCTION risk_zones_stats CASCADE",
        "FUNCTION risk_score_historique CASCADE",
        "VIEW v_risk_tableau_bord",
        "VIEW v_risk_alertes_actives",
        "VIEW v_risk_parcelles_top",
        "VIEW v_risk_carte",
        "TABLE risk_alert CASCADE",
        "TABLE risk_score CASCADE",
        "TABLE risk_config CASCADE",
    ):
        op.execute(f"DROP {obj} IF EXISTS;")
