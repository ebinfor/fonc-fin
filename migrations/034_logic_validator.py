"""034_logic_validator

FONCIER+ ── Migration 034 : Moteur de validation logique des règles métier

Objets créés :
  1. regle_validation_logique_log  ── journal des validations logiques (C5)
  2. logic_heuristic_config        ── configuration des heuristiques (activer/désactiver)
  3. Trigger tg_rvll_readonly      ── journal append-only
  4. Vue v_logic_conflits_recents  ── conflits des 30 derniers jours
  5. Vue v_logic_sante             ── santé du moteur logique (24h)
  6. Fonction logic_taux_conflit() ── taux de conflit par domaine

Revision ID: 034_logic_validator
Revises: 033_sql_sandbox_complet
"""
from alembic import op
import sqlalchemy as sa

revision      = "034_logic_validator"
down_revision = "033_sql_sandbox_complet"
branch_labels = None
depends_on    = None

SQL = r"""
-- ================================================================
-- 1.  regle_validation_logique_log
--     Journal append-only de toutes les validations logiques (C5).
--     Chaque entrée représente l'analyse d'une règle par rapport
--     aux règles actives du même domaine.
-- ================================================================
CREATE TABLE IF NOT EXISTS regle_validation_logique_log (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Règle analysée
    code_regle          VARCHAR(50) NOT NULL,
    domaine             VARCHAR(30) NOT NULL,
    -- Résumé de la décision
    recommandation      VARCHAR(20) NOT NULL
        CHECK (recommandation IN ('AUTORISER','CORRIGER','REFUSER')),
    -- Compteurs de conflits par niveau
    nb_critique         INT         NOT NULL DEFAULT 0,
    nb_majeur           INT         NOT NULL DEFAULT 0,
    nb_mineur           INT         NOT NULL DEFAULT 0,
    -- Règles impactées (codes des règles en conflit)
    regles_impactees    TEXT[]      NOT NULL DEFAULT '{}',
    -- Nombre de règles analysées lors de cette validation
    regles_analysees    INT         NOT NULL DEFAULT 0,
    -- Rapport complet en JSON (conflits détaillés)
    rapport_json        JSONB,
    -- Hash du rapport (non-répudiation)
    sha256_rapport      VARCHAR(64) NOT NULL DEFAULT '',
    -- Durée de l'analyse logique
    duree_ms            INT,
    -- Résultat booléen : TRUE si la règle peut être activée
    valide_logiquement  BOOLEAN     NOT NULL DEFAULT TRUE,
    -- Auteur de la demande de validation
    soumis_par          UUID        REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- Index pour les requêtes fréquentes
CREATE INDEX IF NOT EXISTS idx_rvll_code   ON regle_validation_logique_log(code_regle);
CREATE INDEX IF NOT EXISTS idx_rvll_domaine ON regle_validation_logique_log(domaine);
CREATE INDEX IF NOT EXISTS idx_rvll_reco   ON regle_validation_logique_log(recommandation);
CREATE INDEX IF NOT EXISTS idx_rvll_date   ON regle_validation_logique_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rvll_valide ON regle_validation_logique_log(valide_logiquement)
    WHERE valide_logiquement = FALSE;


-- ================================================================
-- 2.  Trigger : append-only sur regle_validation_logique_log
-- ================================================================
CREATE OR REPLACE FUNCTION tg_rvll_readonly()
RETURNS TRIGGER AS $fn$
BEGIN
    RAISE EXCEPTION 'regle_validation_logique_log est APPEND-ONLY';
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_rvll_no_modify ON regle_validation_logique_log;
CREATE TRIGGER tg_rvll_no_modify
    BEFORE UPDATE OR DELETE ON regle_validation_logique_log
    FOR EACH ROW EXECUTE FUNCTION tg_rvll_readonly();


-- ================================================================
-- 3.  logic_heuristic_config
--     Configuration par domaine des heuristiques logiques.
--     Permet d'activer/désactiver une heuristique sans modifier le code.
-- ================================================================
CREATE TABLE IF NOT EXISTS logic_heuristic_config (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    heuristic_id    VARCHAR(30) NOT NULL,   -- H1_DUPLICATE, H2_NEGATION, etc.
    domaine         VARCHAR(30) NOT NULL DEFAULT 'ALL',   -- domaine ou 'ALL'
    actif           BOOLEAN     NOT NULL DEFAULT TRUE,
    seuil_override  JSONB,                  -- surcharger des seuils de l'heuristique
    description     TEXT,
    modifie_par     UUID        REFERENCES users(id),
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    UNIQUE (heuristic_id, domaine)
);

-- Seed : toutes les heuristiques actives par défaut
INSERT INTO logic_heuristic_config (heuristic_id, domaine, actif, description) VALUES
    ('H1_DUPLICATE',     'ALL', TRUE, 'Détection de règles en double'),
    ('H2_NEGATION',      'ALL', TRUE, 'Contradiction EXISTS / NOT EXISTS'),
    ('H3_BLOCKING',      'ALL', TRUE, 'Conflits entre règles BLOQUANT'),
    ('H4_SUBSUMPTION',   'ALL', TRUE, 'Redondance logique (subsomption)'),
    ('H5_ALWAYS_BLOCKING','ALL', TRUE, 'Combinaison créant une impossibilité totale'),
    ('HG_GLOBAL',        'ALL', TRUE, 'Cohérence globale du domaine')
ON CONFLICT (heuristic_id, domaine) DO NOTHING;


-- ================================================================
-- 4.  Colonnes additionnelles sur regle_metier
--     Traçabilité de la validation logique
-- ================================================================
ALTER TABLE regle_metier
    ADD COLUMN IF NOT EXISTS logic_valide        BOOLEAN   DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS logic_valide_at     TIMESTAMP,
    ADD COLUMN IF NOT EXISTS logic_recommandation VARCHAR(20),
    ADD COLUMN IF NOT EXISTS logic_nb_conflits   INT       DEFAULT 0;

-- Index pour filtrer les règles avec conflits logiques
CREATE INDEX IF NOT EXISTS idx_rm_logic
    ON regle_metier(domaine, logic_valide, actif)
    WHERE actif = TRUE;


-- ================================================================
-- 5.  Trigger renforcé : bloquer actif=TRUE si logic_valide=FALSE
--     Complète le trigger sandbox existant (tg_regle_activation)
-- ================================================================
CREATE OR REPLACE FUNCTION tg_regle_activation_v2()
RETURNS TRIGGER AS $fn$
BEGIN
    -- C5 : règle refusée par le moteur logique
    IF NEW.actif = TRUE
       AND NEW.logic_valide IS NOT NULL
       AND NEW.logic_valide = FALSE THEN
        RAISE EXCEPTION
            'Règle [%] bloquée par le moteur de validation logique (C5). '
            'Résoudre les conflits logiques avant activation.',
            NEW.code;
    END IF;

    -- C4 : sandbox non validée (garde existante)
    IF NEW.actif = TRUE AND NEW.sandbox_valide = FALSE THEN
        RAISE EXCEPTION
            'Règle [%] ne peut pas être activée sans validation sandbox (C4). '
            'Appeler d''abord POST /v1/admin/regles-metier/valider.',
            NEW.code;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- Remplacer le trigger v1 par v2
DROP TRIGGER IF EXISTS tg_regle_activation    ON regle_metier;
DROP TRIGGER IF EXISTS tg_regle_activation_v2 ON regle_metier;
CREATE TRIGGER tg_regle_activation_v2
    BEFORE INSERT OR UPDATE OF actif, sandbox_valide, logic_valide ON regle_metier
    FOR EACH ROW EXECUTE FUNCTION tg_regle_activation_v2();


-- ================================================================
-- 6.  Vue v_logic_conflits_recents  (30 derniers jours)
-- ================================================================
CREATE OR REPLACE VIEW v_logic_conflits_recents AS
SELECT
    code_regle,
    domaine,
    recommandation,
    nb_critique,
    nb_majeur,
    nb_mineur,
    regles_impactees,
    regles_analysees,
    duree_ms,
    valide_logiquement,
    created_at
FROM regle_validation_logique_log
WHERE created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC;


-- ================================================================
-- 7.  Vue v_logic_sante  (24h)
-- ================================================================
CREATE OR REPLACE VIEW v_logic_sante AS
WITH s AS (
    SELECT
        recommandation,
        COUNT(*)                                AS nb,
        AVG(duree_ms)::INT                      AS duree_moy,
        SUM(nb_critique)                        AS total_critique,
        SUM(nb_majeur)                          AS total_majeur,
        SUM(nb_mineur)                          AS total_mineur,
        AVG(regles_analysees)::INT              AS regles_moy
    FROM regle_validation_logique_log
    WHERE created_at >= NOW() - INTERVAL '24 hours'
    GROUP BY recommandation
),
regles_bloquees AS (
    SELECT COUNT(*) AS n
    FROM regle_metier
    WHERE actif=FALSE AND logic_valide=FALSE
)
SELECT
    COALESCE(SUM(s.nb) FILTER (WHERE s.recommandation='AUTORISER'), 0) AS autorisees_24h,
    COALESCE(SUM(s.nb) FILTER (WHERE s.recommandation='CORRIGER'),  0) AS corriger_24h,
    COALESCE(SUM(s.nb) FILTER (WHERE s.recommandation='REFUSER'),   0) AS refusees_24h,
    COALESCE(SUM(s.total_critique), 0)                                  AS conflits_critique_24h,
    COALESCE(SUM(s.total_majeur),   0)                                  AS conflits_majeur_24h,
    COALESCE(AVG(s.duree_moy),      0)::INT                            AS duree_moy_ms,
    COALESCE(AVG(s.regles_moy),     0)::INT                            AS regles_analysees_moy,
    rb.n                                                                AS regles_bloquees_logic,
    NOW()                                                               AS calcule_a
FROM s
CROSS JOIN regles_bloquees rb
GROUP BY rb.n;


-- ================================================================
-- 8.  Fonction logic_taux_conflit()  — taux de conflit par domaine
-- ================================================================
CREATE OR REPLACE FUNCTION logic_taux_conflit(
    p_jours INT DEFAULT 30
) RETURNS TABLE (
    domaine            TEXT,
    nb_analyses        BIGINT,
    nb_conflits_crit   BIGINT,
    nb_conflits_maj    BIGINT,
    taux_refus_pct     NUMERIC,
    duree_moy_ms       NUMERIC
) AS $fn$
BEGIN
    RETURN QUERY
    SELECT
        rvll.domaine::TEXT,
        COUNT(*)                                              AS nb_analyses,
        SUM(nb_critique)                                      AS nb_conflits_crit,
        SUM(nb_majeur)                                        AS nb_conflits_maj,
        ROUND(
            100.0 * COUNT(*) FILTER (WHERE recommandation='REFUSER')
            / NULLIF(COUNT(*),0), 1
        )                                                     AS taux_refus_pct,
        ROUND(AVG(duree_ms), 0)                              AS duree_moy_ms
    FROM regle_validation_logique_log rvll
    WHERE rvll.created_at >= NOW() - (p_jours || ' days')::INTERVAL
    GROUP BY rvll.domaine
    ORDER BY nb_conflits_crit DESC, nb_analyses DESC;
END;
$fn$ LANGUAGE plpgsql;


-- ================================================================
-- 9.  Vue v_regles_conflits_logiques
--     Règles actuellement bloquées par le moteur logique
-- ================================================================
CREATE OR REPLACE VIEW v_regles_conflits_logiques AS
SELECT
    rm.id, rm.code, rm.nom, rm.domaine,
    rm.niveau_blocage, rm.actif,
    rm.logic_valide, rm.logic_valide_at,
    rm.logic_recommandation, rm.logic_nb_conflits,
    LEFT(rm.condition_sql, 120) AS condition_apercu
FROM regle_metier rm
WHERE rm.logic_valide = FALSE
   OR rm.logic_recommandation IN ('REFUSER','CORRIGER')
ORDER BY rm.logic_nb_conflits DESC, rm.updated_at DESC;


-- Notice
DO $notice$
BEGIN
    RAISE NOTICE '034 OK — Moteur de validation logique (C5) :';
    RAISE NOTICE '  regle_validation_logique_log (append-only, trigger tg_rvll_readonly)';
    RAISE NOTICE '  logic_heuristic_config (% heuristiques configurées)',
        (SELECT COUNT(*) FROM logic_heuristic_config);
    RAISE NOTICE '  Colonnes logic_valide/logic_recommandation sur regle_metier';
    RAISE NOTICE '  Trigger tg_regle_activation_v2 (C4+C5 requis avant actif=TRUE)';
    RAISE NOTICE '  Vues: v_logic_conflits_recents, v_logic_sante, v_regles_conflits_logiques';
    RAISE NOTICE '  Fonction: logic_taux_conflit(jours)';
END;
$notice$;
"""

def upgrade() -> None:
    op.execute(SQL)

def downgrade() -> None:
    for obj in (
        "VIEW v_regles_conflits_logiques",
        "VIEW v_logic_sante",
        "VIEW v_logic_conflits_recents",
        "FUNCTION logic_taux_conflit CASCADE",
        "TABLE regle_validation_logique_log CASCADE",
        "TABLE logic_heuristic_config CASCADE",
    ):
        op.execute(f"DROP {obj} IF EXISTS;")
    for col in ("logic_valide","logic_valide_at","logic_recommandation","logic_nb_conflits"):
        op.execute(f"ALTER TABLE regle_metier DROP COLUMN IF EXISTS {col};")
    # Remettre le trigger v1
    op.execute("DROP TRIGGER IF EXISTS tg_regle_activation_v2 ON regle_metier;")
