"""033_sql_sandbox_complet

FONCIER+ ── Migration 033 : SQL Sandbox complet (4 couches)

Tables / objets créés ou modifiés :
  1. sql_validation_log         ── journal append-only de toutes les validations
  2. sql_bypass_alert_log       ── alertes dédiées aux tentatives de bypass (C0)
  3. sql_function_allowlist     ── allowlist positive des fonctions autorisées (C2)
  4. sql_validation_rate_limit  ── rate-limit DB backup (200/h/user)
  5. regle_metier               ── ajout colonnes sandbox : sha256, valide, normalise
  6. Trigger tg_regle_activation── bloque actif=TRUE sans sandbox_valide=TRUE
  7. Trigger tg_svl_readonly    ── sql_validation_log append-only
  8. Role foncier_reader        ── SELECT uniquement sur les tables autorisées (C3)
  9. sql_regle_executer_secure()── fonction d'exécution sécurisée (sans DO block)
  10. decision_engine_evaluer() ── refonte : n'exécute que les règles sandbox_valide=TRUE
  11. Vue v_sandbox_sante        ── monitoring temps réel (24h)
  12. Vue v_regles_attente       ── règles en attente de validation
  13. Vue v_bypass_alerts_7j     ── top alertes bypass 7 derniers jours

Revision ID: 033_sql_sandbox_complet
Revises: 032_sql_sandbox
"""
from alembic import op
import sqlalchemy as sa

revision      = "033_sql_sandbox_complet"
down_revision = "032_sql_sandbox"
branch_labels = None
depends_on    = None

SQL = r"""
-- ================================================================
-- 1.  sql_validation_log  (append-only)
--     Journal de toutes les tentatives de validation via l'API.
-- ================================================================
CREATE TABLE IF NOT EXISTS sql_validation_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code_regle      VARCHAR(50),
    domaine         VARCHAR(30),
    sql_soumis_hash VARCHAR(64) NOT NULL,
    statut          VARCHAR(20) NOT NULL
        CHECK (statut IN ('VALIDE','INVALIDE','REFUSE')),
    nb_erreurs      INT         NOT NULL DEFAULT 0,
    nb_warnings     INT         NOT NULL DEFAULT 0,
    sha256_regle    VARCHAR(64) DEFAULT '',
    duree_ms        INT,
    soumis_par      UUID        REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_svl_code   ON sql_validation_log(code_regle);
CREATE INDEX IF NOT EXISTS idx_svl_statut ON sql_validation_log(statut);
CREATE INDEX IF NOT EXISTS idx_svl_date   ON sql_validation_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_svl_user   ON sql_validation_log(soumis_par, created_at DESC)
    WHERE soumis_par IS NOT NULL;

-- Append-only : aucune modification ni suppression
CREATE OR REPLACE FUNCTION tg_svl_readonly()
RETURNS TRIGGER AS $fn$
BEGIN
    RAISE EXCEPTION 'sql_validation_log est APPEND-ONLY';
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_svl_no_modify ON sql_validation_log;
CREATE TRIGGER tg_svl_no_modify
    BEFORE UPDATE OR DELETE ON sql_validation_log
    FOR EACH ROW EXECUTE FUNCTION tg_svl_readonly();


-- ================================================================
-- 2.  sql_bypass_alert_log  (append-only)
--     Alertes spécifiques aux tentatives d'obfuscation (C0).
-- ================================================================
CREATE TABLE IF NOT EXISTS sql_bypass_alert_log (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        REFERENCES users(id) ON DELETE SET NULL,
    code_regle  VARCHAR(50),
    domaine     VARCHAR(30),
    sql_hash    VARCHAR(64) NOT NULL,
    alert_types TEXT[]      NOT NULL,
    nb_alertes  INT         NOT NULL DEFAULT 1,
    created_at  TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bal_user ON sql_bypass_alert_log(user_id);
CREATE INDEX IF NOT EXISTS idx_bal_date ON sql_bypass_alert_log(created_at DESC);

-- Trigger : insérer une alerte bypass sur chaque REFUSE
CREATE OR REPLACE FUNCTION tg_svl_bypass()
RETURNS TRIGGER AS $fn$
BEGIN
    IF NEW.statut = 'REFUSE' THEN
        INSERT INTO sql_bypass_alert_log(
            user_id, code_regle, domaine, sql_hash, alert_types
        ) VALUES (
            NEW.soumis_par, NEW.code_regle, NEW.domaine,
            NEW.sql_soumis_hash,
            ARRAY['VALIDATION_REFUSEE']
        );
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_svl_bypass_alert ON sql_validation_log;
CREATE TRIGGER tg_svl_bypass_alert
    AFTER INSERT ON sql_validation_log
    FOR EACH ROW EXECUTE FUNCTION tg_svl_bypass();


-- ================================================================
-- 3.  sql_function_allowlist
--     Allowlist positive des fonctions SQL (C2 — diagramme).
-- ================================================================
CREATE TABLE IF NOT EXISTS sql_function_allowlist (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    nom_fonction VARCHAR(80) NOT NULL UNIQUE,
    categorie    VARCHAR(30) NOT NULL
        CHECK (categorie IN ('COMPARAISON','AGGREGAT','TEXTE','DATE','UUID','JSON')),
    description  TEXT,
    actif        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMP   NOT NULL DEFAULT NOW()
);

INSERT INTO sql_function_allowlist (nom_fonction, categorie, description) VALUES
    ('coalesce',             'COMPARAISON', 'Première valeur non-NULL'),
    ('nullif',               'COMPARAISON', 'NULL si valeurs égales'),
    ('greatest',             'COMPARAISON', 'Valeur maximale'),
    ('least',                'COMPARAISON', 'Valeur minimale'),
    ('count',                'AGGREGAT',    'Nombre de lignes'),
    ('sum',                  'AGGREGAT',    'Somme'),
    ('max',                  'AGGREGAT',    'Maximum'),
    ('min',                  'AGGREGAT',    'Minimum'),
    ('avg',                  'AGGREGAT',    'Moyenne'),
    ('lower',                'TEXTE',       'Mise en minuscules'),
    ('upper',                'TEXTE',       'Mise en majuscules'),
    ('trim',                 'TEXTE',       'Suppression espaces'),
    ('length',               'TEXTE',       'Longueur de chaîne'),
    ('substr',               'TEXTE',       'Sous-chaîne'),
    ('substring',            'TEXTE',       'Sous-chaîne SQL standard'),
    ('split_part',           'TEXTE',       'Découpage par séparateur'),
    ('regexp_replace',       'TEXTE',       'Remplacement regex'),
    ('now',                  'DATE',        'Horodatage courant'),
    ('current_date',         'DATE',        'Date du jour'),
    ('current_timestamp',    'DATE',        'Horodatage avec timezone'),
    ('date_trunc',           'DATE',        'Troncature de date'),
    ('extract',              'DATE',        'Extraction de champ date'),
    ('age',                  'DATE',        'Différence entre dates'),
    ('gen_random_uuid',      'UUID',        'UUID aléatoire'),
    ('jsonb_exists',         'JSON',        'Existence d''une clé JSON'),
    ('jsonb_array_elements', 'JSON',        'Éléments d''un tableau JSON')
ON CONFLICT (nom_fonction) DO NOTHING;


-- ================================================================
-- 4.  sql_validation_rate_limit
--     Rate-limit DB (backup du rate-limiter Python-layer).
--     200 tentatives / heure / user.
-- ================================================================
CREATE TABLE IF NOT EXISTS sql_validation_rate_limit (
    user_id    UUID  NOT NULL,
    fenetre_h  INT   NOT NULL,   -- EXTRACT(EPOCH FROM NOW())/3600 :: INT
    nb         INT   NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, fenetre_h)
);

CREATE OR REPLACE FUNCTION sql_rl_check(
    p_user UUID, p_max INT DEFAULT 200
) RETURNS BOOLEAN AS $fn$
DECLARE v INT;
BEGIN
    INSERT INTO sql_validation_rate_limit(user_id, fenetre_h, nb)
    VALUES(p_user, (EXTRACT(EPOCH FROM NOW())/3600)::INT, 1)
    ON CONFLICT(user_id, fenetre_h)
    DO UPDATE SET nb = sql_validation_rate_limit.nb + 1
    RETURNING nb INTO v;
    RETURN v <= p_max;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION sql_rl_cleanup() RETURNS VOID AS $fn$
BEGIN
    DELETE FROM sql_validation_rate_limit
    WHERE fenetre_h < (EXTRACT(EPOCH FROM NOW())/3600)::INT - 48;
END;
$fn$ LANGUAGE plpgsql;


-- ================================================================
-- 5.  regle_metier  — colonnes sandbox
-- ================================================================
ALTER TABLE regle_metier
    ADD COLUMN IF NOT EXISTS sha256_regle       VARCHAR(64),
    ADD COLUMN IF NOT EXISTS sandbox_valide     BOOLEAN   NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS sandbox_valide_at  TIMESTAMP,
    ADD COLUMN IF NOT EXISTS sandbox_valide_par UUID      REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS sql_normalise      TEXT;

-- Pré-valider les règles seeds existantes (déjà auditées)
UPDATE regle_metier
SET
    sandbox_valide     = TRUE,
    sandbox_valide_at  = NOW(),
    sha256_regle       = encode(sha256(
        (condition_sql || '|' || domaine || '|' || code
         || '|' || niveau_blocage || '|SYSTEM')::bytea
    ), 'hex'),
    sql_normalise      = REGEXP_REPLACE(TRIM(condition_sql), '[[:space:]]+', ' ', 'g')
WHERE sandbox_valide = FALSE;

-- Index performance
CREATE INDEX IF NOT EXISTS idx_rm_sandbox
    ON regle_metier(domaine, actif, sandbox_valide)
    WHERE actif = TRUE;


-- ================================================================
-- 6.  Trigger : tg_regle_activation
--     Bloque actif=TRUE si sandbox_valide=FALSE.
-- ================================================================
CREATE OR REPLACE FUNCTION tg_regle_activation()
RETURNS TRIGGER AS $fn$
BEGIN
    IF NEW.actif = TRUE AND NEW.sandbox_valide = FALSE THEN
        RAISE EXCEPTION
            'Règle [%] ne peut pas être activée sans validation sandbox. '
            'Appelez d''abord POST /v1/admin/regles-metier/valider.',
            NEW.code;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_regle_activation ON regle_metier;
CREATE TRIGGER tg_regle_activation
    BEFORE INSERT OR UPDATE OF actif, sandbox_valide ON regle_metier
    FOR EACH ROW EXECUTE FUNCTION tg_regle_activation();


-- ================================================================
-- 7.  Rôle foncier_reader
--     Droits minimaux pour la sandbox C3 : SELECT uniquement.
-- ================================================================
DO $blk$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'foncier_reader') THEN
        CREATE ROLE foncier_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM foncier_reader;
    GRANT SELECT ON
        parcelles, nicad_registry, lotissements,
        bgu_geojson_master, bgu_parcel_status,
        rnaf, rnaf_workflow, rnp_demandes,
        demandes_ccfm, fiches_constat_ccfm, historique_ccfm,
        parcel_right_version, property_transfers, right_holders,
        transaction_blocks, parcel_freeze_events,
        litiges, parcel_disputes, hypotheques, mortgage_registry,
        ccfm_suspension, ccfm_contestation,
        users, agent_profil, institutions,
        delegation_administrative, territoire_juridiction,
        arretes_urbanisme, urbanisme_conformite,
        annf_archive_links,
        regle_metier, validation_pipeline, entity_lock,
        conflits_parcellaires, parcel_lineage
    TO foncier_reader;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'foncier_reader: certaines tables absentes — %', SQLERRM;
END;
$blk$;


-- ================================================================
-- 8.  sql_regle_executer_secure()
--     Exécution sécurisée sans DO block (C3 — sans dollar-quoting).
-- ================================================================
CREATE OR REPLACE FUNCTION sql_regle_executer_secure(
    p_sql       TEXT,
    p_entite_id UUID,
    p_user_id   UUID,
    p_contexte  JSONB DEFAULT '{}'
) RETURNS BOOLEAN AS $fn$
DECLARE
    v_res     BOOLEAN;
    v_up      TEXT;
    v_sp      TEXT;
BEGIN
    -- Garde statique DB-side (complément des couches Python)
    v_up := TRIM(UPPER(REGEXP_REPLACE(p_sql, '[[:space:]]+', ' ', 'g')));

    IF v_up ~ '\y(DROP|DELETE|INSERT|UPDATE|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|EXECUTE|PERFORM|CALL)\y'
    THEN
        RAISE EXCEPTION 'REGLE_DANGEREUSE: mot-clé DDL/DML dans [%]', LEFT(p_sql, 60);
    END IF;
    IF p_sql LIKE '%--%' OR p_sql LIKE '%/*%' THEN
        RAISE EXCEPTION 'REGLE_DANGEREUSE: commentaire interdit';
    END IF;
    IF p_sql LIKE '%;%' THEN
        RAISE EXCEPTION 'REGLE_DANGEREUSE: point-virgule interdit';
    END IF;
    IF p_sql ~ '\$\$|\$[a-zA-Z_]\w*\$' THEN
        RAISE EXCEPTION 'REGLE_DANGEREUSE: dollar-quoting interdit';
    END IF;
    IF v_up ~ '\yWITH\s+RECURSIVE\y' THEN
        RAISE EXCEPTION 'REGLE_DANGEREUSE: WITH RECURSIVE interdit';
    END IF;

    -- Timeout strict
    v_sp := 'sp_secure_' || TRUNC(EXTRACT(EPOCH FROM clock_timestamp())*1000)::BIGINT % 99999;
    SAVEPOINT v_sp;
    BEGIN
        SET LOCAL statement_timeout = '2000';
        EXECUTE 'SELECT (' || p_sql || ')::BOOLEAN'
        INTO v_res
        USING p_entite_id, p_user_id, p_contexte;
        RETURN COALESCE(v_res, TRUE);
    EXCEPTION
        WHEN read_only_sql_transaction THEN
            ROLLBACK TO SAVEPOINT v_sp;
            RAISE EXCEPTION 'SECURITE: écriture tentée dans la règle [%]', LEFT(p_sql, 80);
        WHEN statement_timeout THEN
            ROLLBACK TO SAVEPOINT v_sp;
            RAISE WARNING 'Règle SQL timeout [%] — autorisé (fail-open)', LEFT(p_sql, 80);
            RETURN TRUE;
        WHEN OTHERS THEN
            ROLLBACK TO SAVEPOINT v_sp;
            RAISE WARNING 'Règle SQL erreur [%]: %', LEFT(p_sql, 50), SQLERRM;
            RETURN TRUE;
    END;
END;
$fn$ LANGUAGE plpgsql;


-- ================================================================
-- 9.  decision_engine_evaluer()  — refonte sandbox-aware
--     N'évalue QUE les règles sandbox_valide = TRUE.
-- ================================================================
CREATE OR REPLACE FUNCTION decision_engine_evaluer(
    p_entite_type   VARCHAR,
    p_entite_id     UUID,
    p_operation     VARCHAR,
    p_user_id       UUID    DEFAULT NULL,
    p_contexte      JSONB   DEFAULT '{}'
) RETURNS TABLE (
    autorise        BOOLEAN,
    decision        VARCHAR,
    regles_evaluees INT,
    blocages        INT,
    avertissements  INT,
    motifs          TEXT[],
    sha256          VARCHAR
) AS $fn$
DECLARE
    v_regle       regle_metier;
    v_res         BOOLEAN;
    v_autorise    BOOLEAN  := TRUE;
    v_blocages    INT      := 0;
    v_warns       INT      := 0;
    v_motifs      TEXT[]   := ARRAY[]::TEXT[];
    v_count       INT      := 0;
    v_skip        INT      := 0;
    v_decision    VARCHAR  := 'AUTORISE';
    v_hash        VARCHAR;
BEGIN
    FOR v_regle IN
        SELECT * FROM regle_metier
        WHERE actif            = TRUE
          AND sandbox_valide   = TRUE          -- ← clé : uniquement les règles validées
          AND (effectif_jusqu IS NULL OR effectif_jusqu >= CURRENT_DATE)
          AND (domaine = p_entite_type OR domaine = 'GENERAL')
        ORDER BY niveau_blocage DESC, code
    LOOP
        v_count := v_count + 1;
        v_res := sql_regle_executer_secure(
            COALESCE(v_regle.sql_normalise, v_regle.condition_sql),
            p_entite_id, p_user_id, p_contexte
        );
        IF NOT v_res THEN
            IF v_regle.niveau_blocage = 'BLOQUANT' THEN
                v_autorise := FALSE;
                v_blocages := v_blocages + 1;
                v_motifs   := v_motifs || ('BLOQUANT[' || v_regle.code || ']: ' || v_regle.nom);
            ELSIF v_regle.niveau_blocage = 'AVERTISSEMENT' THEN
                v_warns  := v_warns + 1;
                v_motifs := v_motifs || ('WARN[' || v_regle.code || ']: ' || v_regle.nom);
            END IF;
        END IF;
    END LOOP;

    -- Signalement règles non-validées ignorées
    SELECT COUNT(*) INTO v_skip
    FROM regle_metier
    WHERE actif=TRUE AND sandbox_valide=FALSE
      AND (domaine = p_entite_type OR domaine = 'GENERAL');
    IF v_skip > 0 THEN
        v_motifs := v_motifs
            || ('INFO: ' || v_skip::TEXT || ' règle(s) en attente sandbox ignorée(s)');
    END IF;

    IF NOT v_autorise        THEN v_decision := 'REFUSE';
    ELSIF v_warns > 0        THEN v_decision := 'AVERTISSEMENT';
    END IF;

    v_hash := encode(sha256((
        p_entite_type || '|' || p_entite_id::TEXT || '|' || p_operation
        || '|' || v_decision || '|' || v_count::TEXT || '|' || NOW()::TEXT
    )::bytea), 'hex');

    INSERT INTO decision_engine_log(
        entite_type, entite_id, operation, decision,
        motif, contexte_json, sha256_decision, effectue_par
    ) VALUES (
        p_entite_type, p_entite_id, p_operation, v_decision,
        array_to_string(v_motifs, '; '),
        p_contexte, v_hash, p_user_id
    );

    RETURN QUERY SELECT
        v_autorise, v_decision, v_count,
        v_blocages, v_warns, v_motifs, v_hash;
END;
$fn$ LANGUAGE plpgsql;


-- ================================================================
-- 10. Vues de monitoring
-- ================================================================

-- Santé sandbox (24 h)
CREATE OR REPLACE VIEW v_sandbox_sante AS
WITH s AS (
    SELECT statut,
           COUNT(*)                                     AS nb,
           AVG(duree_ms)::INT                           AS duree_moy,
           MAX(duree_ms)                                AS duree_max,
           COUNT(*) FILTER (WHERE nb_erreurs > 0)       AS avec_erreurs
    FROM sql_validation_log
    WHERE created_at >= NOW() - INTERVAL '24 hours'
    GROUP BY statut
),
nv AS (SELECT COUNT(*) AS n FROM regle_metier WHERE actif=TRUE AND sandbox_valide=FALSE),
bp AS (SELECT COUNT(*) AS n FROM sql_bypass_alert_log WHERE created_at >= NOW() - INTERVAL '24 hours'),
rv AS (SELECT COUNT(*) AS n FROM regle_metier WHERE created_at >= NOW() - INTERVAL '7 days' AND sandbox_valide=TRUE)
SELECT
    COALESCE(SUM(s.nb) FILTER (WHERE s.statut='VALIDE'),   0)  AS validations_ok_24h,
    COALESCE(SUM(s.nb) FILTER (WHERE s.statut='INVALIDE'), 0)  AS validations_ko_24h,
    COALESCE(SUM(s.nb) FILTER (WHERE s.statut='REFUSE'),   0)  AS validations_refuse_24h,
    COALESCE(AVG(s.duree_moy), 0)::INT                         AS duree_moy_ms,
    COALESCE(MAX(s.duree_max), 0)                              AS duree_max_ms,
    nv.n  AS nb_non_validees,
    bp.n  AS nb_bypass_24h,
    rv.n  AS regles_validees_7j,
    NOW() AS calcule_a
FROM s
CROSS JOIN nv CROSS JOIN bp CROSS JOIN rv
GROUP BY nv.n, bp.n, rv.n;

-- Règles en attente
CREATE OR REPLACE VIEW v_regles_attente AS
SELECT id, code, nom, domaine, niveau_blocage, actif, created_at,
       LEFT(condition_sql, 120) AS condition_apercu
FROM regle_metier
WHERE sandbox_valide = FALSE
ORDER BY created_at DESC;

-- Top alertes bypass (7 j)
CREATE OR REPLACE VIEW v_bypass_alerts_7j AS
SELECT
    unnest(alert_types)                      AS alert_type,
    COUNT(*)                                  AS nb,
    COUNT(DISTINCT user_id)                   AS users_uniques,
    MAX(created_at)                           AS derniere_alerte
FROM sql_bypass_alert_log
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY 1
ORDER BY 2 DESC;

-- Statistiques 30 j
CREATE OR REPLACE VIEW v_sandbox_stats_30j AS
SELECT
    DATE_TRUNC('day', created_at)             AS jour,
    statut,
    COUNT(*)                                  AS nb,
    AVG(duree_ms)::INT                        AS duree_moy_ms,
    COUNT(*) FILTER (WHERE nb_erreurs > 0)    AS avec_erreurs
FROM sql_validation_log
WHERE created_at >= NOW() - INTERVAL '30 days'
GROUP BY 1, 2
ORDER BY 1 DESC, 2;


-- Notice finale
DO $notice$
BEGIN
    RAISE NOTICE '033 OK — SQL Sandbox 4 couches opérationnel :';
    RAISE NOTICE '  C1 StaticSQLValidator  : 40+ mots-clés, commentaires, dollar-quoting';
    RAISE NOTICE '  C2 SemanticSQLValidator: whitelist % tables, pg_shadow/catalog bloqués',
        (SELECT COUNT(*) FROM sql_function_allowlist);
    RAISE NOTICE '  C3 PostgresSandbox     : SAVEPOINT + EXPLAIN READ ONLY + timeout 2s';
    RAISE NOTICE '  C4 RuleSignature       : SHA-256 non-répudiation';
    RAISE NOTICE '  Rate-limit DB : sql_rl_check() 200/h/user';
    RAISE NOTICE '  Trigger activation : sandbox_valide=TRUE requis avant actif=TRUE';
END;
$notice$;
"""

def upgrade() -> None:
    op.execute(SQL)

def downgrade() -> None:
    for obj in (
        "VIEW v_sandbox_stats_30j", "VIEW v_bypass_alerts_7j",
        "VIEW v_regles_attente",    "VIEW v_sandbox_sante",
        "TABLE sql_bypass_alert_log CASCADE",
        "TABLE sql_validation_rate_limit CASCADE",
        "TABLE sql_function_allowlist CASCADE",
        "TABLE sql_validation_log CASCADE",
        "FUNCTION sql_regle_executer_secure CASCADE",
        "FUNCTION sql_rl_check CASCADE",
        "FUNCTION sql_rl_cleanup CASCADE",
    ):
        op.execute(f"DROP {obj} IF EXISTS;")
    for col in ("sha256_regle", "sandbox_valide", "sandbox_valide_at",
                "sandbox_valide_par", "sql_normalise"):
        op.execute(
            f"ALTER TABLE regle_metier DROP COLUMN IF EXISTS {col};"
        )
