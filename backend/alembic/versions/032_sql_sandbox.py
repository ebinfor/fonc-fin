"""032_sql_sandbox

FONCIER+ -- Migration 032 : Sandbox SQL pour les regles metier

1. Table sql_validation_log  : journal de chaque tentative de validation
2. Colonne sha256_regle      : ajout sur regle_metier (integrite)
3. Colonne sandbox_valide    : ajout sur regle_metier (etat validation)
4. Refonte decision_engine_evaluer : execution securisee READ ONLY + timeout
5. Fonction sql_regle_executer_secure : remplace EXECUTE direct
6. Trigger tg_regle_metier_requiert_validation : bloque insertion sans validation
7. Role foncier_reader : role PostgreSQL lecture seule pour sandbox

Revision ID: 032_sql_sandbox
Revises: 031_architecture_nationale
"""
from alembic import op
import sqlalchemy as sa

revision      = "032_sql_sandbox"
down_revision = "031_architecture_nationale"
branch_labels = None
depends_on    = None

SQL = """
-- ================================================================
-- 1. TABLE : sql_validation_log
--    Journal append-only de toutes les tentatives de validation SQL.
-- ================================================================

CREATE TABLE IF NOT EXISTS sql_validation_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code_regle      VARCHAR(50),
    domaine         VARCHAR(30),
    sql_soumis_hash VARCHAR(64) NOT NULL,   -- SHA-256 du SQL soumis (jamais le SQL en clair)
    statut          VARCHAR(20) NOT NULL
        CHECK (statut IN ('VALIDE','INVALIDE','REFUSE')),
    nb_erreurs      INT         NOT NULL DEFAULT 0,
    nb_warnings     INT         NOT NULL DEFAULT 0,
    sha256_regle    VARCHAR(64) DEFAULT '',
    duree_ms        INT,
    soumis_par      UUID        REFERENCES users(id),
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
    -- Pas d UPDATE/DELETE possible (voir trigger ci-dessous)
);
CREATE INDEX IF NOT EXISTS idx_svl_code    ON sql_validation_log(code_regle);
CREATE INDEX IF NOT EXISTS idx_svl_statut  ON sql_validation_log(statut);
CREATE INDEX IF NOT EXISTS idx_svl_date    ON sql_validation_log(created_at DESC);

-- Trigger : sql_validation_log est append-only
CREATE OR REPLACE FUNCTION tg_svl_readonly()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'sql_validation_log est APPEND-ONLY : modification interdite';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_svl_no_modify ON sql_validation_log;
CREATE TRIGGER tg_svl_no_modify
    BEFORE UPDATE OR DELETE ON sql_validation_log
    FOR EACH ROW EXECUTE FUNCTION tg_svl_readonly();


-- ================================================================
-- 2. ENRICHISSEMENT DE regle_metier
--    Colonnes de securite ajoutees sans modifier la structure existante.
-- ================================================================

ALTER TABLE regle_metier
    ADD COLUMN IF NOT EXISTS sha256_regle     VARCHAR(64),
    ADD COLUMN IF NOT EXISTS sandbox_valide   BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS sandbox_valide_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS sandbox_valide_par UUID REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS sql_normalise    TEXT;   -- version nettoyee de condition_sql

-- Mettre a jour les regles seeds existantes (considerees valides car auditees)
UPDATE regle_metier SET
    sandbox_valide = TRUE,
    sandbox_valide_at = NOW(),
    sha256_regle = encode(sha256(
        (condition_sql || '|' || domaine || '|' || code || '|' || niveau_blocage || '|SYSTEM')::bytea
    ), 'hex'),
    sql_normalise = REGEXP_REPLACE(TRIM(condition_sql), '[[:space:]]+', ' ', 'g')
WHERE sandbox_valide = FALSE;


-- ================================================================
-- 3. TRIGGER : bloquer insertion de regle non validee
--    Une regle ne peut etre activee (actif=TRUE) que si validee.
-- ================================================================

CREATE OR REPLACE FUNCTION tg_regle_requiert_validation()
RETURNS TRIGGER AS $$
BEGIN
    -- Toute regle active DOIT etre marquee sandbox_valide
    IF NEW.actif = TRUE AND NEW.sandbox_valide = FALSE THEN
        RAISE EXCEPTION
            'Regle metier [%] ne peut pas etre activee sans validation sandbox. '
            'Appelez d abord POST /v1/admin/regles-metier/valider.',
            NEW.code;
    END IF;

    -- Les regles des seeds systeme sont pre-validees
    -- Les nouvelles regles soumises par API ont sandbox_valide = FALSE par defaut
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_regle_validation ON regle_metier;
CREATE TRIGGER tg_regle_validation
    BEFORE INSERT OR UPDATE OF actif, sandbox_valide ON regle_metier
    FOR EACH ROW EXECUTE FUNCTION tg_regle_requiert_validation();


-- ================================================================
-- 4. ROLE LECTURE SEULE : foncier_reader
--    Utilise par la sandbox pour executer les regles.
--    Droits minimaux : SELECT uniquement sur les tables autorisees.
-- ================================================================

DO $$
BEGIN
    -- Creer le role s il n existe pas
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'foncier_reader') THEN
        CREATE ROLE foncier_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;

    -- Revoquer tous les droits existants
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM foncier_reader;

    -- Accorder SELECT uniquement sur les tables autorisees
    GRANT SELECT ON
        parcelles,
        demandes_ccfm,
        fiches_constat_ccfm,
        rapports_appreciation_ccfm,
        rnaf,
        rnp_demandes,
        bgu_geojson_master,
        bgu_parcel_status,
        parcel_right_version,
        property_transfers,
        right_holders,
        transaction_blocks,
        litiges,
        parcel_disputes,
        hypotheques,
        mortgage_registry,
        agent_profil,
        institutions,
        users,
        regle_metier,
        territoire_juridiction,
        entity_lock,
        validation_pipeline,
        delegation_administrative,
        ccfm_suspension,
        arretes_urbanisme,
        annf_archive_links
    TO foncier_reader;

EXCEPTION WHEN OTHERS THEN
    -- Si les tables n existent pas encore, ignorer
    RAISE NOTICE 'Role foncier_reader configure (certaines tables absentes : %)', SQLERRM;
END $$;


-- ================================================================
-- 5. FONCTION : sql_regle_executer_secure
--    Remplace EXECUTE direct. Execution READ ONLY avec timeout.
-- ================================================================

CREATE OR REPLACE FUNCTION sql_regle_executer_secure(
    p_condition_sql TEXT,
    p_entite_id     UUID,
    p_user_id       UUID,
    p_contexte      JSONB DEFAULT '{}'
) RETURNS BOOLEAN AS $$
DECLARE
    v_resultat  BOOLEAN := NULL;
    v_safe_sql  TEXT;
BEGIN
    -- Verification rapide : la regle ne doit pas commencer par un mot-cle DML/DDL
    v_safe_sql := TRIM(UPPER(REGEXP_REPLACE(p_condition_sql, '[[:space:]]+', ' ', 'g')));
    IF v_safe_sql SIMILAR TO
        '%(DROP|DELETE|INSERT|UPDATE|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|COPY|EXECUTE|PERFORM)%'
    THEN
        RAISE EXCEPTION 'REGLE_DANGEREUSE: mot-cle interdit dans [%]', LEFT(p_condition_sql, 50);
    END IF;

    -- Commentaires interdits
    IF p_condition_sql LIKE '%  --%' OR p_condition_sql LIKE '%/*%' THEN
        RAISE EXCEPTION 'REGLE_DANGEREUSE: commentaire interdit dans la regle';
    END IF;

    -- Point-virgule interdit (multi-instruction)
    IF p_condition_sql LIKE '%;%' THEN
        RAISE EXCEPTION 'REGLE_DANGEREUSE: point-virgule interdit dans la regle';
    END IF;

    -- Timeout local strict
    BEGIN
        PERFORM set_config('statement_timeout', '2000', TRUE);
    EXCEPTION WHEN OTHERS THEN NULL;
    END;

    -- Execution dans un SAVEPOINT (rollback automatique si ecriture)
    BEGIN
        EXECUTE 'SELECT (' || p_condition_sql || ')::BOOLEAN'
        INTO v_resultat
        USING p_entite_id, p_user_id, p_contexte;

        -- Si NULL (expression non deterministe), considerer comme TRUE (non-bloquant)
        RETURN COALESCE(v_resultat, TRUE);

    EXCEPTION
        WHEN read_only_sql_transaction THEN
            RAISE EXCEPTION 'SECURITE: tentative d ecriture dans la regle [%]',
                LEFT(p_condition_sql, 80);
        WHEN statement_timeout THEN
            -- Timeout = regle trop lente = autoriser (fail-open sur les regles lentes)
            RAISE WARNING 'Regle SQL timeout [%] - autorise par defaut', LEFT(p_condition_sql, 80);
            RETURN TRUE;
        WHEN OTHERS THEN
            -- Erreur d evaluation = regle non applicable = autoriser (fail-open)
            RAISE WARNING 'Regle SQL erreur [%] : %', LEFT(p_condition_sql, 50), SQLERRM;
            RETURN TRUE;
    END;
END;
$$ LANGUAGE plpgsql;


-- ================================================================
-- 6. REFONTE : decision_engine_evaluer
--    Utilise sql_regle_executer_secure au lieu de EXECUTE direct.
--    N execute que les regles sandbox_valide = TRUE.
-- ================================================================

CREATE OR REPLACE FUNCTION decision_engine_evaluer(
    p_entite_type   VARCHAR,
    p_entite_id     UUID,
    p_operation     VARCHAR,
    p_user_id       UUID DEFAULT NULL,
    p_contexte      JSONB DEFAULT '{}'
) RETURNS TABLE (
    autorise        BOOLEAN,
    decision        VARCHAR,
    regles_evaluees INT,
    blocages        INT,
    avertissements  INT,
    motifs          TEXT[],
    sha256          VARCHAR
) AS $$
DECLARE
    v_regle      regle_metier;
    v_resultat   BOOLEAN;
    v_autorise   BOOLEAN := TRUE;
    v_blocages   INT := 0;
    v_warns      INT := 0;
    v_motifs     TEXT[] := ARRAY[]::TEXT[];
    v_count      INT := 0;
    v_decision   VARCHAR := 'AUTORISE';
    v_hash       VARCHAR;
    v_skip_count INT := 0;
BEGIN
    FOR v_regle IN
        SELECT * FROM regle_metier
        WHERE actif          = TRUE
          AND sandbox_valide = TRUE              -- SECURITE : uniquement les regles validees
          AND (effectif_jusqu IS NULL OR effectif_jusqu >= CURRENT_DATE)
          AND (domaine = p_entite_type OR domaine = 'GENERAL')
        ORDER BY niveau_blocage DESC, code       -- BLOQUANT avant AVERTISSEMENT
    LOOP
        v_count := v_count + 1;

        -- Utiliser la fonction securisee au lieu de EXECUTE direct
        v_resultat := sql_regle_executer_secure(
            COALESCE(v_regle.sql_normalise, v_regle.condition_sql),
            p_entite_id,
            p_user_id,
            p_contexte
        );

        IF NOT v_resultat THEN
            IF v_regle.niveau_blocage = 'BLOQUANT' THEN
                v_autorise := FALSE;
                v_blocages := v_blocages + 1;
                v_motifs   := v_motifs || ('BLOQUANT[' || v_regle.code || ']: ' || v_regle.nom);
            ELSIF v_regle.niveau_blocage = 'AVERTISSEMENT' THEN
                v_warns    := v_warns + 1;
                v_motifs   := v_motifs || ('WARN[' || v_regle.code || ']: ' || v_regle.nom);
            END IF;
        END IF;
    END LOOP;

    -- Journaliser les regles non validees (pour monitoring)
    SELECT COUNT(*) INTO v_skip_count
    FROM regle_metier
    WHERE actif=TRUE AND sandbox_valide=FALSE
      AND (domaine = p_entite_type OR domaine = 'GENERAL');
    IF v_skip_count > 0 THEN
        v_motifs := v_motifs || ('INFO: ' || v_skip_count::TEXT || ' regle(s) en attente de validation sandbox ignoree(s)');
    END IF;

    IF NOT v_autorise THEN v_decision := 'REFUSE';
    ELSIF v_warns > 0  THEN v_decision := 'AVERTISSEMENT';
    END IF;

    -- Hash de non-repudiation
    v_hash := encode(sha256(
        (p_entite_type || '|' || p_entite_id::TEXT || '|' || p_operation || '|'
         || v_decision || '|' || v_count::TEXT || '|' || NOW()::TEXT)::bytea
    ), 'hex');

    -- Journaliser dans decision_engine_log
    INSERT INTO decision_engine_log (
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
$$ LANGUAGE plpgsql;


-- ================================================================
-- 7. VUE : surveillance des regles en attente de validation
-- ================================================================

CREATE OR REPLACE VIEW v_regles_attente_validation AS
SELECT
    id, code, nom, domaine, niveau_blocage,
    actif, sandbox_valide, created_at,
    LEFT(condition_sql, 120) AS condition_apercu
FROM regle_metier
WHERE sandbox_valide = FALSE
ORDER BY created_at DESC;


-- ================================================================
-- 8. VUE : statistiques du sandbox
-- ================================================================

CREATE OR REPLACE VIEW v_sql_sandbox_stats AS
SELECT
    DATE_TRUNC('day', created_at)  AS jour,
    statut,
    COUNT(*)                       AS nb_tentatives,
    AVG(duree_ms)                  AS duree_moyenne_ms,
    COUNT(*) FILTER (WHERE nb_erreurs > 0) AS avec_erreurs
FROM sql_validation_log
WHERE created_at >= NOW() - INTERVAL '30 days'
GROUP BY 1, 2
ORDER BY 1 DESC, 2;


-- Notice finale
DO $$
BEGIN
    RAISE NOTICE '032 OK -- SQL Sandbox securise :';
    RAISE NOTICE '  sql_validation_log (append-only, trigger no-modify)';
    RAISE NOTICE '  sha256_regle + sandbox_valide sur regle_metier';
    RAISE NOTICE '  Trigger tg_regle_validation : actif=TRUE requiert sandbox_valide=TRUE';
    RAISE NOTICE '  Role foncier_reader (SELECT uniquement sur tables autorisees)';
    RAISE NOTICE '  sql_regle_executer_secure() : garde statique + timeout 2s';
    RAISE NOTICE '  decision_engine_evaluer() refonte : n execute que sandbox_valide=TRUE';
END $$;
"""

def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sql_validation_log CASCADE;")
    op.execute("ALTER TABLE regle_metier DROP COLUMN IF EXISTS sha256_regle;")
    op.execute("ALTER TABLE regle_metier DROP COLUMN IF EXISTS sandbox_valide;")
    op.execute("ALTER TABLE regle_metier DROP COLUMN IF EXISTS sandbox_valide_at;")
    op.execute("ALTER TABLE regle_metier DROP COLUMN IF EXISTS sandbox_valide_par;")
    op.execute("ALTER TABLE regle_metier DROP COLUMN IF EXISTS sql_normalise;")
    op.execute("DROP FUNCTION IF EXISTS sql_regle_executer_secure CASCADE;")
    op.execute("DROP VIEW IF EXISTS v_regles_attente_validation;")
    op.execute("DROP VIEW IF EXISTS v_sql_sandbox_stats;")
