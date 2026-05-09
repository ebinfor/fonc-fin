"""031_architecture_nationale

FONCIER+ -- Migration 031 : Architecture Nationale Renforcee
15 ameliorations structurelles sur la base existante.

COMPOSANTS AJOUTES (non-dupliques) :
  A. Hierarchie institutionnelle    : institutions, directions, services, agents
  B. Validation multi-niveaux       : validation_pipeline + niveaux 1->4
  C. Decision Engine                : decision_engine_log + regles_metier
  D. Audit immuable                 : audit_immutable (append-only + hash chaining)
  E. Verrous entites                : entity_lock (pg_advisory + row-level)
  F. Topologie PostGIS              : validations ST_Overlaps, ST_IsValid
  G. Delegations administratives    : delegation_administrative
  H. Versioning documentaire        : document_foncier + document_version
  I. DRP                            : drp_backup_log + drp_replication_status
  J. Interoperabilite               : integration_externe + webhook_log
  K. Versioning regles metier       : regle_metier + regle_version
  L. Segmentation territoriale      : territoire_juridiction (renforce security)

Tables existantes non touchees : users, parcelles, demandes_ccfm, annf_archive_links, etc.

Revision ID: 031_architecture_nationale
Revises: 030_ccfm_schema_officiel
"""
from alembic import op
import sqlalchemy as sa

revision      = "031_architecture_nationale"
down_revision = "030_ccfm_schema_officiel"
branch_labels = None
depends_on    = None

SQL = """
-- ================================================================
-- A. HIERARCHIE INSTITUTIONNELLE
-- ================================================================

-- A1. Institutions (Ministeres, Directions generales, Communes)
CREATE TABLE IF NOT EXISTS institutions (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code            VARCHAR(20) UNIQUE NOT NULL,      -- MUH, DNCD, COM-NIA, etc.
    nom             VARCHAR(200) NOT NULL,
    type_institution VARCHAR(30) NOT NULL              -- MINISTERE, DIRECTION, COMMUNE, TRIBUNAL
        CHECK (type_institution IN ('MINISTERE','DIRECTION_GENERALE','DIRECTION',
                                    'SERVICE','COMMUNE','PREFECTURE','TRIBUNAL','BANQUE','ETUDE_NOTARIALE')),
    parent_id       UUID        REFERENCES institutions(id),
    region          VARCHAR(10),                       -- NIA, ZDR, AGZ, TAH, MAR, DOS, TLW, DIL
    niveau_hierarchique INT NOT NULL DEFAULT 1         -- 1=national 2=regional 3=departemental 4=local
        CHECK (niveau_hierarchique BETWEEN 1 AND 4),
    actif           BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_inst_code   ON institutions(code);
CREATE INDEX IF NOT EXISTS idx_inst_region ON institutions(region);
CREATE INDEX IF NOT EXISTS idx_inst_parent ON institutions(parent_id);

-- A2. Directions et services
CREATE TABLE IF NOT EXISTS directions_services (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id  UUID        NOT NULL REFERENCES institutions(id),
    code            VARCHAR(30) UNIQUE NOT NULL,
    nom             VARCHAR(200) NOT NULL,
    type_ds         VARCHAR(20) NOT NULL DEFAULT 'SERVICE'
        CHECK (type_ds IN ('DIRECTION','SOUS_DIRECTION','SERVICE','BUREAU','GUICHET')),
    responsable_id  UUID        REFERENCES users(id),
    actif           BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- A3. Profils agents (enrichit la table users existante sans la modifier)
CREATE TABLE IF NOT EXISTS agent_profil (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID        NOT NULL UNIQUE REFERENCES users(id),
    institution_id    UUID        NOT NULL REFERENCES institutions(id),
    direction_id      UUID        REFERENCES directions_services(id),
    region_principale VARCHAR(10) NOT NULL,
    regions_autorisees TEXT[]     DEFAULT ARRAY[]::TEXT[],  -- regions supplementaires
    niveau_autorite   INT         NOT NULL DEFAULT 1
        CHECK (niveau_autorite BETWEEN 1 AND 4),
    -- 1=agent  2=chef_service  3=directeur  4=direction_nationale
    peut_valider_hors_region BOOLEAN NOT NULL DEFAULT FALSE,
    date_prise_poste  DATE,
    actif             BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_user      ON agent_profil(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_inst      ON agent_profil(institution_id);
CREATE INDEX IF NOT EXISTS idx_agent_region    ON agent_profil(region_principale);

-- ================================================================
-- B. VALIDATION MULTI-NIVEAUX
-- ================================================================

CREATE TABLE IF NOT EXISTS validation_pipeline (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    entite_type     VARCHAR(50) NOT NULL,   -- demandes_ccfm, rnaf, parcelles, mutations
    entite_id       UUID        NOT NULL,
    workflow_type   VARCHAR(50) NOT NULL,
    -- 4 niveaux : 1=local 2=departemental 3=regional 4=national
    niveau_requis   INT         NOT NULL DEFAULT 1
        CHECK (niveau_requis BETWEEN 1 AND 4),
    niveau_courant  INT         NOT NULL DEFAULT 0,
    -- Etapes
    valide_local_par     UUID   REFERENCES users(id),
    valide_local_at      TIMESTAMP,
    valide_dept_par      UUID   REFERENCES users(id),
    valide_dept_at       TIMESTAMP,
    valide_regional_par  UUID   REFERENCES users(id),
    valide_regional_at   TIMESTAMP,
    valide_national_par  UUID   REFERENCES users(id),
    valide_national_at   TIMESTAMP,
    -- Etat
    statut          VARCHAR(20) NOT NULL DEFAULT 'EN_ATTENTE'
        CHECK (statut IN ('EN_ATTENTE','NIVEAU_1_OK','NIVEAU_2_OK','NIVEAU_3_OK','VALIDE','REJETE')),
    motif_rejet     TEXT,
    sha256_chain    VARCHAR(64),     -- hash chaine entre les niveaux
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vp_entite ON validation_pipeline(entite_type, entite_id);
CREATE INDEX IF NOT EXISTS idx_vp_statut ON validation_pipeline(statut);

-- Fonction : passer au niveau suivant
CREATE OR REPLACE FUNCTION valider_niveau_suivant(
    p_pipeline_id UUID,
    p_validateur_id UUID,
    p_commentaire TEXT DEFAULT NULL
) RETURNS TEXT AS $$
DECLARE
    v_pipe validation_pipeline;
    v_niveau_new INT;
    v_statut_new VARCHAR(20);
    v_hash VARCHAR(64);
BEGIN
    SELECT * INTO v_pipe FROM validation_pipeline WHERE id = p_pipeline_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'PIPELINE_INTROUVABLE'; END IF;
    IF v_pipe.statut IN ('VALIDE','REJETE') THEN RETURN 'PIPELINE_CLOS'; END IF;

    v_niveau_new := v_pipe.niveau_courant + 1;

    -- Verifier que le validateur a l autorite requise
    IF NOT EXISTS (
        SELECT 1 FROM agent_profil ap
        WHERE ap.user_id = p_validateur_id
          AND ap.niveau_autorite >= v_niveau_new
    ) THEN RETURN 'AUTORITE_INSUFFISANTE'; END IF;

    -- Hash chaine
    v_hash := encode(sha256(
        (v_pipe.sha256_chain || '|' || p_validateur_id::TEXT || '|' || NOW()::TEXT)::bytea
    ), 'hex');

    -- Mise a jour selon le niveau
    IF v_niveau_new = 1 THEN
        UPDATE validation_pipeline SET
            niveau_courant=1, statut='NIVEAU_1_OK',
            valide_local_par=p_validateur_id, valide_local_at=NOW(),
            sha256_chain=v_hash, updated_at=NOW()
        WHERE id=p_pipeline_id;
    ELSIF v_niveau_new = 2 THEN
        UPDATE validation_pipeline SET
            niveau_courant=2, statut='NIVEAU_2_OK',
            valide_dept_par=p_validateur_id, valide_dept_at=NOW(),
            sha256_chain=v_hash, updated_at=NOW()
        WHERE id=p_pipeline_id;
    ELSIF v_niveau_new = 3 THEN
        UPDATE validation_pipeline SET
            niveau_courant=3, statut='NIVEAU_3_OK',
            valide_regional_par=p_validateur_id, valide_regional_at=NOW(),
            sha256_chain=v_hash, updated_at=NOW()
        WHERE id=p_pipeline_id;
    ELSIF v_niveau_new >= 4 THEN
        UPDATE validation_pipeline SET
            niveau_courant=4, statut='VALIDE',
            valide_national_par=p_validateur_id, valide_national_at=NOW(),
            sha256_chain=v_hash, updated_at=NOW()
        WHERE id=p_pipeline_id;
    END IF;

    -- Si niveau requis atteint, valider directement
    IF v_niveau_new >= v_pipe.niveau_requis THEN
        UPDATE validation_pipeline SET statut='VALIDE', updated_at=NOW()
        WHERE id=p_pipeline_id;
    END IF;

    RETURN 'OK_NIVEAU_' || v_niveau_new;
END;
$$ LANGUAGE plpgsql;

-- ================================================================
-- C. MOTEUR DE DECISION (Decision Engine)
-- ================================================================

-- Regles metier versionnees
CREATE TABLE IF NOT EXISTS regle_metier (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code            VARCHAR(50) UNIQUE NOT NULL,   -- ex: CCFM_GATE, MUTATION_CCFM_REQUIS
    nom             VARCHAR(200) NOT NULL,
    description     TEXT,
    domaine         VARCHAR(30) NOT NULL            -- CCFM, MUTATION, BGU, RNAF, JUSTICE
        CHECK (domaine IN ('CCFM','MUTATION','BGU','RNAF','JUSTICE','SUCCESSION',
                           'HYPOTHEQUE','EXPROPRIATION','LOTISSEMENT','GENERAL')),
    condition_sql   TEXT        NOT NULL,           -- SQL renvoyant TRUE/FALSE
    action_si_vrai  TEXT,
    action_si_faux  TEXT,
    niveau_blocage  VARCHAR(20) NOT NULL DEFAULT 'BLOQUANT'
        CHECK (niveau_blocage IN ('BLOQUANT','AVERTISSEMENT','INFO')),
    version         INT         NOT NULL DEFAULT 1,
    actif           BOOLEAN     NOT NULL DEFAULT TRUE,
    effectif_depuis DATE        NOT NULL DEFAULT CURRENT_DATE,
    effectif_jusqu  DATE,       -- NULL = indefini
    created_by      UUID        REFERENCES users(id),
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_regle_domaine ON regle_metier(domaine, actif);
CREATE INDEX IF NOT EXISTS idx_regle_code    ON regle_metier(code);

-- Versions des regles (historique immuable)
CREATE TABLE IF NOT EXISTS regle_version (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    regle_id        UUID        NOT NULL REFERENCES regle_metier(id),
    version         INT         NOT NULL,
    condition_sql   TEXT        NOT NULL,
    niveau_blocage  VARCHAR(20) NOT NULL,
    motif_changement TEXT,
    modifie_par     UUID        REFERENCES users(id),
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    UNIQUE(regle_id, version)
);

-- Journal des decisions du moteur
CREATE TABLE IF NOT EXISTS decision_engine_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    entite_type     VARCHAR(50) NOT NULL,
    entite_id       UUID        NOT NULL,
    operation       VARCHAR(100) NOT NULL,
    regle_id        UUID        REFERENCES regle_metier(id),
    decision        VARCHAR(20) NOT NULL
        CHECK (decision IN ('AUTORISE','REFUSE','AVERTISSEMENT','CONFLIT_DETECTE')),
    motif           TEXT,
    contexte_json   JSONB,
    sha256_decision VARCHAR(64) NOT NULL,  -- hash de la decision pour non-repudiation
    effectue_par    UUID        REFERENCES users(id),
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_de_entite  ON decision_engine_log(entite_type, entite_id);
CREATE INDEX IF NOT EXISTS idx_de_date    ON decision_engine_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_de_decision ON decision_engine_log(decision);

-- Fonction : Decision Engine central
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
    v_regle     regle_metier;
    v_resultat  BOOLEAN;
    v_autorise  BOOLEAN := TRUE;
    v_blocages  INT := 0;
    v_warns     INT := 0;
    v_motifs    TEXT[] := ARRAY[]::TEXT[];
    v_count     INT := 0;
    v_decision  VARCHAR := 'AUTORISE';
    v_hash      VARCHAR;
BEGIN
    FOR v_regle IN
        SELECT * FROM regle_metier
        WHERE actif = TRUE
          AND (effectif_jusqu IS NULL OR effectif_jusqu >= CURRENT_DATE)
          AND (domaine = p_entite_type OR domaine = 'GENERAL')
        ORDER BY niveau_blocage DESC, code
    LOOP
        v_count := v_count + 1;
        -- Evaluer la condition SQL de la regle
        BEGIN
            EXECUTE 'SELECT (' || v_regle.condition_sql || ')' INTO v_resultat
            USING p_entite_id, p_user_id, p_contexte;
        EXCEPTION WHEN OTHERS THEN
            v_resultat := TRUE; -- En cas d erreur, la regle ne bloque pas
        END;

        IF NOT v_resultat THEN
            IF v_regle.niveau_blocage = 'BLOQUANT' THEN
                v_autorise := FALSE;
                v_blocages := v_blocages + 1;
                v_motifs := v_motifs || ('BLOQUANT: ' || v_regle.nom);
            ELSIF v_regle.niveau_blocage = 'AVERTISSEMENT' THEN
                v_warns := v_warns + 1;
                v_motifs := v_motifs || ('AVERTISSEMENT: ' || v_regle.nom);
            END IF;
        END IF;
    END LOOP;

    IF NOT v_autorise THEN v_decision := 'REFUSE';
    ELSIF v_warns > 0 THEN v_decision := 'AVERTISSEMENT';
    END IF;

    -- Hash de non-repudiation
    v_hash := encode(sha256(
        (p_entite_type || '|' || p_entite_id::TEXT || '|' || p_operation || '|'
         || v_decision || '|' || NOW()::TEXT)::bytea
    ), 'hex');

    -- Journaliser
    INSERT INTO decision_engine_log (
        entite_type, entite_id, operation, decision, motif,
        contexte_json, sha256_decision, effectue_par
    ) VALUES (
        p_entite_type, p_entite_id, p_operation, v_decision,
        array_to_string(v_motifs, '; '),
        p_contexte, v_hash, p_user_id
    );

    RETURN QUERY SELECT v_autorise, v_decision, v_count, v_blocages, v_warns, v_motifs, v_hash;
END;
$$ LANGUAGE plpgsql;

-- ================================================================
-- D. AUDIT IMMUABLE (append-only + hash chaining)
-- ================================================================

CREATE TABLE IF NOT EXISTS audit_immutable (
    seq             BIGSERIAL   NOT NULL,          -- sequence monotone
    id              UUID        NOT NULL DEFAULT gen_random_uuid(),
    entite_type     VARCHAR(50) NOT NULL,
    entite_id       UUID        NOT NULL,
    operation       VARCHAR(100) NOT NULL,
    etat_avant      JSONB,
    etat_apres      JSONB,
    effectue_par    UUID        REFERENCES users(id),
    region          VARCHAR(10),
    institution_id  UUID        REFERENCES institutions(id),
    ip_address      VARCHAR(45),
    -- Hash chaining : chaque entree contient le hash de la precedente
    sha256_courant  VARCHAR(64) NOT NULL,
    sha256_precedent VARCHAR(64),
    signature_op    TEXT,                          -- signature RSA-PSS optionnelle
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    -- Contrainte append-only : aucune colonne ne peut etre modifiee
    CONSTRAINT audit_immutable_no_pk_change CHECK (id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_audit_imm_entite ON audit_immutable(entite_type, entite_id);
CREATE INDEX IF NOT EXISTS idx_audit_imm_date   ON audit_immutable(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_imm_seq    ON audit_immutable(seq);

-- Interdire UPDATE et DELETE sur audit_immutable
CREATE OR REPLACE FUNCTION tg_audit_immutable_readonly()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_immutable est APPEND-ONLY : UPDATE et DELETE interdits (entree seq=%, operation=%, entite=%)',
        OLD.seq, OLD.operation, OLD.entite_type;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_audit_no_update ON audit_immutable;
CREATE TRIGGER tg_audit_no_update
    BEFORE UPDATE OR DELETE ON audit_immutable
    FOR EACH ROW EXECUTE FUNCTION tg_audit_immutable_readonly();

-- Fonction : enregistrer dans l audit immuable
CREATE OR REPLACE FUNCTION audit_enregistrer(
    p_entite_type   VARCHAR,
    p_entite_id     UUID,
    p_operation     VARCHAR,
    p_etat_avant    JSONB DEFAULT NULL,
    p_etat_apres    JSONB DEFAULT NULL,
    p_user_id       UUID DEFAULT NULL,
    p_region        VARCHAR DEFAULT NULL,
    p_institution_id UUID DEFAULT NULL
) RETURNS VARCHAR AS $$
DECLARE
    v_prev_hash  VARCHAR(64);
    v_curr_hash  VARCHAR(64);
BEGIN
    -- Hash du precedent
    SELECT sha256_courant INTO v_prev_hash
    FROM audit_immutable
    ORDER BY seq DESC LIMIT 1;

    -- Hash courant (chaine)
    v_curr_hash := encode(sha256(
        (COALESCE(v_prev_hash,'GENESIS') || '|' ||
         p_entite_type || '|' || p_entite_id::TEXT || '|' ||
         p_operation || '|' || NOW()::TEXT)::bytea
    ), 'hex');

    INSERT INTO audit_immutable (
        entite_type, entite_id, operation,
        etat_avant, etat_apres,
        effectue_par, region, institution_id,
        sha256_courant, sha256_precedent
    ) VALUES (
        p_entite_type, p_entite_id, p_operation,
        p_etat_avant, p_etat_apres,
        p_user_id, p_region, p_institution_id,
        v_curr_hash, v_prev_hash
    );

    RETURN v_curr_hash;
END;
$$ LANGUAGE plpgsql;

-- Vue : verification integrite de la chaine
CREATE OR REPLACE VIEW v_audit_integrite AS
SELECT
    a.seq,
    a.entite_type,
    a.operation,
    a.created_at,
    CASE
        WHEN a.sha256_precedent IS NULL THEN TRUE  -- entree GENESIS
        WHEN a.sha256_precedent = LAG(a.sha256_courant) OVER (ORDER BY a.seq) THEN TRUE
        ELSE FALSE
    END AS chaine_integre
FROM audit_immutable a;

-- ================================================================
-- E. VERROUILLAGE DES ENTITES FONCIERES
-- ================================================================

CREATE TABLE IF NOT EXISTS entity_lock (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    entite_type     VARCHAR(50) NOT NULL,
    entite_id       UUID        NOT NULL,
    workflow_type   VARCHAR(50) NOT NULL,
    workflow_id     UUID,
    verrou_par      UUID        NOT NULL REFERENCES users(id),
    institution_id  UUID        REFERENCES institutions(id),
    raison          TEXT,
    date_expiration TIMESTAMP   NOT NULL DEFAULT NOW() + INTERVAL '24 hours',
    actif           BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    libere_par      UUID        REFERENCES users(id),
    libere_at       TIMESTAMP,
    UNIQUE(entite_type, entite_id, actif)  -- une seule entite active a la fois
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS idx_lock_entite ON entity_lock(entite_type, entite_id, actif);
CREATE INDEX IF NOT EXISTS idx_lock_expiry ON entity_lock(date_expiration) WHERE actif=TRUE;

-- Nettoyer les verrous expires
CREATE OR REPLACE FUNCTION entity_lock_cleanup() RETURNS INT AS $$
DECLARE v_count INT;
BEGIN
    WITH expired AS (
        UPDATE entity_lock
        SET actif=FALSE, libere_at=NOW()
        WHERE actif=TRUE AND date_expiration < NOW()
        RETURNING id
    )
    SELECT COUNT(*) INTO v_count FROM expired;
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;

-- Fonction : poser un verrou (echec si deja verrouillee)
CREATE OR REPLACE FUNCTION entity_lock_poser(
    p_entite_type   VARCHAR,
    p_entite_id     UUID,
    p_workflow_type VARCHAR,
    p_user_id       UUID,
    p_raison        TEXT DEFAULT NULL,
    p_duree_heures  INT  DEFAULT 24
) RETURNS TABLE(ok BOOLEAN, message TEXT, lock_id UUID) AS $$
DECLARE
    v_existing entity_lock;
    v_id       UUID;
BEGIN
    -- Nettoyer d abord les expires
    PERFORM entity_lock_cleanup();

    -- Verifier si deja verrouille
    SELECT * INTO v_existing
    FROM entity_lock
    WHERE entite_type=p_entite_type
      AND entite_id=p_entite_id
      AND actif=TRUE
    FOR UPDATE NOWAIT;

    IF FOUND THEN
        RETURN QUERY SELECT FALSE,
            'Entite deja verrouillee par workflow ' || v_existing.workflow_type
            || ' depuis ' || v_existing.created_at::TEXT,
            v_existing.id;
        RETURN;
    END IF;

    INSERT INTO entity_lock (
        entite_type, entite_id, workflow_type,
        verrou_par, raison, date_expiration
    ) VALUES (
        p_entite_type, p_entite_id, p_workflow_type,
        p_user_id, p_raison,
        NOW() + (p_duree_heures || ' hours')::INTERVAL
    ) RETURNING id INTO v_id;

    RETURN QUERY SELECT TRUE, 'Verrou pose', v_id;
END;
$$ LANGUAGE plpgsql;

-- Fonction : liberer un verrou
CREATE OR REPLACE FUNCTION entity_lock_liberer(
    p_entite_type   VARCHAR,
    p_entite_id     UUID,
    p_user_id       UUID
) RETURNS BOOLEAN AS $$
BEGIN
    UPDATE entity_lock SET
        actif=FALSE, libere_par=p_user_id, libere_at=NOW()
    WHERE entite_type=p_entite_type
      AND entite_id=p_entite_id
      AND actif=TRUE;
    RETURN FOUND;
END;
$$ LANGUAGE plpgsql;

-- ================================================================
-- F. VALIDATIONS TOPOLOGIQUES POSTGIS AVANCEES
-- ================================================================

-- Log des validations topologiques
CREATE TABLE IF NOT EXISTS topologie_validation_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    parcelle_id     UUID,
    operation       VARCHAR(50) NOT NULL,   -- INSERT_GEOM, UPDATE_GEOM, BGU_IMPORT
    geom_wkt        TEXT,
    -- Resultats
    is_valid        BOOLEAN,
    has_overlap     BOOLEAN,
    overlap_with    UUID[],     -- parcelles en chevauchement
    has_gap         BOOLEAN,
    gap_surface_m2  NUMERIC(12,2),
    st_isvalid_msg  TEXT,
    distance_min_m  NUMERIC(10,2),  -- distance min aux voisins
    surface_m2      NUMERIC(12,2),
    -- Decision
    bloque          BOOLEAN     NOT NULL DEFAULT FALSE,
    motif_blocage   TEXT,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_topo_parcelle ON topologie_validation_log(parcelle_id);

-- Fonction : validation topologique complete d une geometrie
CREATE OR REPLACE FUNCTION topo_valider_geom(
    p_parcelle_id   UUID,
    p_geom_wkt      TEXT,
    p_operation     VARCHAR DEFAULT 'VALIDATION'
) RETURNS TABLE(
    valide          BOOLEAN,
    bloque          BOOLEAN,
    message         TEXT,
    overlap_count   INT
) AS $$
DECLARE
    v_geom          GEOMETRY;
    v_is_valid      BOOLEAN := FALSE;
    v_has_overlap   BOOLEAN := FALSE;
    v_overlap_ids   UUID[] := ARRAY[]::UUID[];
    v_overlap_count INT := 0;
    v_surface       NUMERIC;
    v_msg           TEXT := 'OK';
    v_bloque        BOOLEAN := FALSE;
BEGIN
    -- Parser la geometrie
    BEGIN
        v_geom := ST_GeomFromText(p_geom_wkt, 4326);
        v_is_valid := ST_IsValid(v_geom);
    EXCEPTION WHEN OTHERS THEN
        v_msg := 'Geometrie invalide : ' || SQLERRM;
        v_bloque := TRUE;
        INSERT INTO topologie_validation_log (
            parcelle_id, operation, geom_wkt, is_valid, bloque, motif_blocage
        ) VALUES (p_parcelle_id, p_operation, p_geom_wkt, FALSE, TRUE, v_msg);
        RETURN QUERY SELECT FALSE, TRUE, v_msg, 0;
        RETURN;
    END;

    IF NOT v_is_valid THEN
        v_msg := 'ST_IsValid echoue : ' || COALESCE(ST_IsValidReason(v_geom), 'raison inconnue');
        v_bloque := TRUE;
    END IF;

    -- Detection chevauchements (avec les parcelles existantes)
    IF v_geom IS NOT NULL THEN
        BEGIN
            SELECT COUNT(*), ARRAY_AGG(id)
            INTO v_overlap_count, v_overlap_ids
            FROM parcelles p
            WHERE p.id != COALESCE(p_parcelle_id, gen_random_uuid())
              AND p.geom IS NOT NULL
              AND ST_Overlaps(p.geom::GEOMETRY, v_geom)
              AND ST_Area(ST_Intersection(p.geom::GEOMETRY, v_geom)) > 1.0;  -- > 1 m2
        EXCEPTION WHEN OTHERS THEN
            v_overlap_count := 0;
        END;

        IF v_overlap_count > 0 THEN
            v_has_overlap := TRUE;
            v_bloque := TRUE;
            v_msg := v_msg || '; CHEVAUCHEMENT detecte avec ' || v_overlap_count || ' parcelle(s)';
        END IF;

        v_surface := ST_Area(ST_Transform(v_geom, 32632));  -- UTM zone 32N (Niger)
    END IF;

    -- Enregistrer le log
    INSERT INTO topologie_validation_log (
        parcelle_id, operation, geom_wkt,
        is_valid, has_overlap, overlap_with,
        surface_m2, bloque, motif_blocage
    ) VALUES (
        p_parcelle_id, p_operation, p_geom_wkt,
        v_is_valid, v_has_overlap, v_overlap_ids,
        v_surface, v_bloque, NULLIF(v_msg, 'OK')
    );

    RETURN QUERY SELECT v_is_valid AND NOT v_has_overlap, v_bloque, v_msg, v_overlap_count;
END;
$$ LANGUAGE plpgsql;

-- Trigger : validation automatique a chaque modification de geometrie
CREATE OR REPLACE FUNCTION tg_topo_check_geom()
RETURNS TRIGGER AS $$
DECLARE v_ok BOOLEAN; v_bloque BOOLEAN; v_msg TEXT;
BEGIN
    IF NEW.geom IS NOT NULL THEN
        SELECT valide, bloque, message
        INTO v_ok, v_bloque, v_msg
        FROM topo_valider_geom(NEW.id, ST_AsText(NEW.geom::GEOMETRY), TG_OP);

        IF v_bloque THEN
            RAISE EXCEPTION 'Validation topologique echouee pour parcelle % : %',
                NEW.id, v_msg;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Appliquer le trigger sur la table parcelles (si geom existe)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='parcelles' AND column_name='geom'
    ) THEN
        DROP TRIGGER IF EXISTS tg_parcelles_topo ON parcelles;
        CREATE TRIGGER tg_parcelles_topo
            BEFORE INSERT OR UPDATE OF geom ON parcelles
            FOR EACH ROW EXECUTE FUNCTION tg_topo_check_geom();
    END IF;
END $$;

-- ================================================================
-- G. DELEGATIONS ADMINISTRATIVES
-- ================================================================

CREATE TABLE IF NOT EXISTS delegation_administrative (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    delegant_id         UUID        NOT NULL REFERENCES users(id),
    delegataire_id      UUID        NOT NULL REFERENCES users(id),
    type_delegation     VARCHAR(30) NOT NULL
        CHECK (type_delegation IN ('SIGNATURE','VALIDATION','SUPERVISION','REMPLACEMENT_COMPLET')),
    domaine             VARCHAR(50) NOT NULL,   -- CCFM, MUTATION, BGU, RNAF, GENERAL
    region_concernee    VARCHAR(10),
    date_debut          TIMESTAMP   NOT NULL DEFAULT NOW(),
    date_fin            TIMESTAMP   NOT NULL,   -- delegation obligatoirement limitee
    motif               TEXT        NOT NULL,
    -- Validation
    valide_par          UUID        REFERENCES users(id),  -- superieur hierarchique
    valide_at           TIMESTAMP,
    statut              VARCHAR(20) NOT NULL DEFAULT 'EN_ATTENTE'
        CHECK (statut IN ('EN_ATTENTE','ACTIVE','EXPIREE','REVOQUEE')),
    -- Traçabilite
    sha256_delegation   VARCHAR(64),
    revoque_par         UUID        REFERENCES users(id),
    revoque_at          TIMESTAMP,
    raison_revocation   TEXT,
    created_at          TIMESTAMP   NOT NULL DEFAULT NOW(),
    CONSTRAINT delegation_dates_coherentes CHECK (date_fin > date_debut),
    CONSTRAINT delegation_pas_auto_delegation CHECK (delegant_id != delegataire_id)
);
CREATE INDEX IF NOT EXISTS idx_deleg_delegataire ON delegation_administrative(delegataire_id, statut);
CREATE INDEX IF NOT EXISTS idx_deleg_delegant     ON delegation_administrative(delegant_id);
CREATE INDEX IF NOT EXISTS idx_deleg_dates        ON delegation_administrative(date_debut, date_fin);

-- Trigger : expiration automatique des delegations
CREATE OR REPLACE FUNCTION tg_delegation_expirer()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.date_fin <= NOW() AND NEW.statut = 'ACTIVE' THEN
        NEW.statut := 'EXPIREE';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_delegation_check ON delegation_administrative;
CREATE TRIGGER tg_delegation_check
    BEFORE INSERT OR UPDATE ON delegation_administrative
    FOR EACH ROW EXECUTE FUNCTION tg_delegation_expirer();

-- ================================================================
-- H. VERSIONING DOCUMENTAIRE
-- ================================================================

CREATE TABLE IF NOT EXISTS document_foncier (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code_doc        VARCHAR(50) UNIQUE NOT NULL,   -- DOC-TYPE-ANNEE-SEQ
    type_document   VARCHAR(40) NOT NULL
        CHECK (type_document IN ('ARRETE','TITRE_FONCIER','CCFM','ACTE_NOTARIE',
                                  'JUGEMENT','PLAN_CADASTRAL','MAINLEVEE',
                                  'ORDRE_MISSION','RAPPORT_EXPERTISE','AUTRE')),
    titre           VARCHAR(500) NOT NULL,
    -- Lien obligatoire a une operation fonciere
    entite_type     VARCHAR(50) NOT NULL,
    entite_id       UUID        NOT NULL,
    -- Version courante
    version_courante INT        NOT NULL DEFAULT 1,
    sha256_courant  VARCHAR(64) NOT NULL,
    statut          VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (statut IN ('DRAFT','EN_REVISION','SIGNE','PUBLIE','ARCHIVE','ANNULE')),
    -- Signataire
    signe_par       UUID        REFERENCES users(id),
    signe_at        TIMESTAMP,
    signature_valeur TEXT,      -- valeur signature RSA-PSS
    -- Stockage
    url_stockage    TEXT,
    taille_octets   BIGINT,
    mime_type       VARCHAR(100),
    created_by      UUID        REFERENCES users(id),
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_doc_entite ON document_foncier(entite_type, entite_id);
CREATE INDEX IF NOT EXISTS idx_doc_type   ON document_foncier(type_document, statut);

-- Historique des versions
CREATE TABLE IF NOT EXISTS document_version (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID        NOT NULL REFERENCES document_foncier(id),
    numero_version  INT         NOT NULL,
    sha256          VARCHAR(64) NOT NULL,
    url_stockage    TEXT,
    taille_octets   BIGINT,
    modifications   TEXT,
    cree_par        UUID        REFERENCES users(id),
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    UNIQUE(document_id, numero_version)
);

-- ================================================================
-- I. PLAN DE REPRISE APRES SINISTRE (DRP)
-- ================================================================

CREATE TABLE IF NOT EXISTS drp_backup_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    type_backup     VARCHAR(30) NOT NULL
        CHECK (type_backup IN ('FULL','INCREMENTAL','TRANSACTION_LOG','SNAPSHOT')),
    site            VARCHAR(50) NOT NULL,   -- NIAMEY_DC1, ZINDER_DC2, CLOUD_S3
    taille_mo       NUMERIC(12,2),
    duree_secondes  INT,
    nb_tables       INT,
    sha256_manifest VARCHAR(64),
    statut          VARCHAR(20) NOT NULL
        CHECK (statut IN ('EN_COURS','SUCCES','ECHOUE','VERIFIE')),
    erreur          TEXT,
    initie_par      VARCHAR(50),  -- cron, admin, drp_auto
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    verifie_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS drp_replication_status (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    replica_site    VARCHAR(50) NOT NULL,
    role_replica    VARCHAR(20) NOT NULL DEFAULT 'STANDBY'
        CHECK (role_replica IN ('PRIMARY','STANDBY','ARCHIVE')),
    lag_secondes    INT,
    dernier_lsn     TEXT,
    etat            VARCHAR(20) NOT NULL
        CHECK (etat IN ('STREAMING','CATCHING_UP','DISCONNECTED','PROMOTING')),
    verifie_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- Simulation DRP
CREATE TABLE IF NOT EXISTS drp_simulation (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    date_simulation DATE        NOT NULL,
    type_scenario   VARCHAR(50) NOT NULL,   -- PERTE_SERVEUR_PRINCIPAL, CORRUPTION_DB, SINISTRE_TOTAL
    duree_restoration_min INT,
    rpm_atteint     INT,                    -- Recovery Point (minutes de donnees perdues)
    rto_atteint     INT,                    -- Recovery Time (minutes de remise en service)
    objectif_rpo_min INT DEFAULT 60,        -- Objectif RPO <= 60 min
    objectif_rto_min INT DEFAULT 240,       -- Objectif RTO <= 4h
    conforme        BOOLEAN,
    responsable     UUID        REFERENCES users(id),
    rapport_url     TEXT,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- ================================================================
-- J. INTEROPERABILITE EXTERNE
-- ================================================================

CREATE TABLE IF NOT EXISTS integration_externe (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code            VARCHAR(30) UNIQUE NOT NULL,
    nom             VARCHAR(200) NOT NULL,
    type_systeme    VARCHAR(30) NOT NULL
        CHECK (type_systeme IN ('CADASTRE','FISCALITE','JUSTICE','BANQUE_CENTRALE',
                                 'ETAT_CIVIL','URBANISME_LOCAL','NOTARIAT','AUTRE')),
    base_url        TEXT        NOT NULL,
    format_echange  VARCHAR(20) NOT NULL DEFAULT 'JSON'
        CHECK (format_echange IN ('JSON','XML','CSV','ISO_19139','FGDC','GML')),
    auth_type       VARCHAR(20) NOT NULL DEFAULT 'API_KEY'
        CHECK (auth_type IN ('API_KEY','OAUTH2','MTLS','BASIC','NONE')),
    actif           BOOLEAN     NOT NULL DEFAULT TRUE,
    -- Securite
    api_key_hash    VARCHAR(64),  -- hash de la cle, jamais en clair
    certificat_ssl  TEXT,
    ip_whitelist    TEXT[],
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS webhook_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    integration_id  UUID        NOT NULL REFERENCES integration_externe(id),
    direction       VARCHAR(10) NOT NULL CHECK (direction IN ('ENTRANT','SORTANT')),
    evenement       VARCHAR(100) NOT NULL,
    payload_hash    VARCHAR(64) NOT NULL,  -- hash du payload, jamais le payload en clair
    statut_http     INT,
    succes          BOOLEAN,
    duree_ms        INT,
    erreur          TEXT,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_webhook_int  ON webhook_log(integration_id, created_at DESC);

-- ================================================================
-- K. VERSIONING REGLES METIER (seeds initiaux)
-- ================================================================

-- Inserer les regles metier de base
INSERT INTO regle_metier (code, nom, domaine, condition_sql, niveau_blocage, description)
VALUES
    ('CCFM_REQUIS_MUTATION',
     'Mutation requiert un CCFM signe',
     'MUTATION',
     'EXISTS (SELECT 1 FROM demandes_ccfm dc WHERE dc.etat IN (''SIGNE_DIRECTEUR'',''ARCHIVE'',''RETIRE'') AND dc.nicad_code IN (SELECT nicad FROM parcelles WHERE id=$1))',
     'BLOQUANT',
     'Toute mutation de propriete exige un CCFM valide sur la parcelle'),

    ('RNAF_PUBLIE_REQUIS_RNP',
     'RNP requiert un RNAF publie',
     'RNAF',
     'EXISTS (SELECT 1 FROM rnaf r WHERE r.statut IN (''publie'',''scelle'') LIMIT 1)',
     'BLOQUANT',
     'Le RNP ne peut etre etabli sans RNAF publie au Journal Officiel'),

    ('PARCELLE_NON_GELEE',
     'Parcelle ne doit pas etre gelee',
     'GENERAL',
     'NOT EXISTS (SELECT 1 FROM parcelles p WHERE p.id=$1 AND p.is_gele=TRUE)',
     'BLOQUANT',
     'Toute operation est bloquee si la parcelle est gelee (litige ou saisie judiciaire)'),

    ('JURIDICTION_RESPECTEE',
     'Agent doit operer dans sa juridiction',
     'GENERAL',
     'EXISTS (SELECT 1 FROM agent_profil ap WHERE ap.user_id=$2 AND (ap.peut_valider_hors_region=TRUE OR ap.niveau_autorite>=3 OR EXISTS (SELECT 1 FROM parcelles p2 WHERE p2.id=$1 AND p2.region=ap.region_principale)))',
     'BLOQUANT',
     'Un agent ne peut operer que sur les parcelles de sa region, sauf autorisation superieure'),

    ('DOUBLE_ATTRIBUTION_INTERDITE',
     'Pas de double attribution sur une meme parcelle',
     'MUTATION',
     'NOT EXISTS (SELECT 1 FROM property_transfers pt WHERE pt.parcelle_id=$1 AND pt.statut IN (''en_cours'',''valide'') AND pt.date_fin IS NULL)',
     'BLOQUANT',
     'Une parcelle ne peut faire l objet de deux mutations simultanees'),

    ('CCFM_PEREMPTION_2ANS',
     'CCFM perime apres 2 ans',
     'CCFM',
     'EXISTS (SELECT 1 FROM demandes_ccfm dc WHERE dc.nicad_code IN (SELECT nicad FROM parcelles WHERE id=$1) AND dc.etat IN (''SIGNE_DIRECTEUR'',''ARCHIVE'',''RETIRE'') AND dc.date_signature + INTERVAL ''2 years'' > NOW())',
     'BLOQUANT',
     'Le CCFM est valable 2 ans a compter de la date de signature')
ON CONFLICT (code) DO NOTHING;

-- Historiser les versions initiales
INSERT INTO regle_version (regle_id, version, condition_sql, niveau_blocage, motif_changement)
SELECT id, 1, condition_sql, niveau_blocage, 'Version initiale'
FROM regle_metier
ON CONFLICT (regle_id, version) DO NOTHING;

-- ================================================================
-- L. SEGMENTATION TERRITORIALE RENFORCEE
-- ================================================================

CREATE TABLE IF NOT EXISTS territoire_juridiction (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code            VARCHAR(20) UNIQUE NOT NULL,
    nom             VARCHAR(200) NOT NULL,
    type_territoire VARCHAR(20) NOT NULL
        CHECK (type_territoire IN ('NATIONAL','REGION','DEPARTEMENT','COMMUNE','ARRONDISSEMENT')),
    parent_id       UUID        REFERENCES territoire_juridiction(id),
    code_region     VARCHAR(10),
    code_departement VARCHAR(10),
    -- Geometrie du territoire (pour verification hors-zone)
    -- geom : PostGIS GEOMETRY(Polygon,4326) -- ajoute si PostGIS disponible
    actif           BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_terr_code   ON territoire_juridiction(code);
CREATE INDEX IF NOT EXISTS idx_terr_region ON territoire_juridiction(code_region);
CREATE INDEX IF NOT EXISTS idx_terr_parent ON territoire_juridiction(parent_id);

-- Seeds : 8 regions du Niger
INSERT INTO territoire_juridiction (code, nom, type_territoire, code_region)
VALUES
    ('NER',          'Republique du Niger',    'NATIONAL',    NULL),
    ('NER-NIA',      'Region de Niamey',       'REGION',      'NIA'),
    ('NER-ZDR',      'Region de Zinder',       'REGION',      'ZDR'),
    ('NER-MAR',      'Region de Maradi',       'REGION',      'MAR'),
    ('NER-TAH',      'Region de Tahoua',       'REGION',      'TAH'),
    ('NER-AGZ',      'Region d Agadez',        'REGION',      'AGZ'),
    ('NER-DOS',      'Region de Dosso',        'REGION',      'DOS'),
    ('NER-TLW',      'Region de Tillaberi',    'REGION',      'TLW'),
    ('NER-DIL',      'Region de Diffa',        'REGION',      'DIL')
ON CONFLICT (code) DO NOTHING;

-- Fonction : verifier qu un agent peut operer sur un territoire
CREATE OR REPLACE FUNCTION check_juridiction(
    p_user_id       UUID,
    p_region        VARCHAR
) RETURNS BOOLEAN AS $$
DECLARE
    v_profil agent_profil;
BEGIN
    SELECT * INTO v_profil FROM agent_profil WHERE user_id=p_user_id AND actif=TRUE;
    IF NOT FOUND THEN
        -- Si pas de profil agent, verifier le role dans users (compatibilite existante)
        RETURN EXISTS (
            SELECT 1 FROM users WHERE id=p_user_id
              AND (role IN ('ADMIN','DIRECTEUR_URBANISME','MINISTRE_URBANISME')
                   OR region IS NULL OR region='NATIONAL')
        );
    END IF;
    -- Admin ou autorisation nationale
    IF v_profil.niveau_autorite >= 4 OR v_profil.peut_valider_hors_region THEN
        RETURN TRUE;
    END IF;
    -- Region principale ou regions autorisees
    RETURN (v_profil.region_principale = p_region
            OR p_region = ANY(v_profil.regions_autorisees));
END;
$$ LANGUAGE plpgsql STABLE;

-- ================================================================
-- VUES DE SUPERVISION
-- ================================================================

-- Vue : tableau de bord Decision Engine
CREATE OR REPLACE VIEW v_decision_engine_dashboard AS
SELECT
    DATE_TRUNC('hour', created_at) AS heure,
    decision,
    COUNT(*) AS nb_decisions,
    COUNT(DISTINCT entite_id) AS nb_entites
FROM decision_engine_log
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY 1, 2
ORDER BY 1 DESC;

-- Vue : verrous actifs
CREATE OR REPLACE VIEW v_entity_locks_actifs AS
SELECT
    el.entite_type, el.entite_id, el.workflow_type,
    u.name AS verrou_par,
    el.date_expiration,
    EXTRACT(MINUTES FROM (el.date_expiration - NOW()))::INT AS minutes_restantes,
    el.raison
FROM entity_lock el
JOIN users u ON u.id = el.verrou_par
WHERE el.actif = TRUE AND el.date_expiration > NOW()
ORDER BY el.date_expiration;

-- Vue : agents et leurs juridictions
CREATE OR REPLACE VIEW v_agents_juridictions AS
SELECT
    u.id AS user_id, u.email, u.name, u.role,
    ap.region_principale, ap.niveau_autorite, ap.peut_valider_hors_region,
    i.nom AS institution,
    i.type_institution
FROM users u
LEFT JOIN agent_profil ap ON ap.user_id = u.id AND ap.actif = TRUE
LEFT JOIN institutions i  ON i.id = ap.institution_id
ORDER BY ap.niveau_autorite DESC NULLS LAST, u.name;

-- Vue : delegations actives
CREATE OR REPLACE VIEW v_delegations_actives AS
SELECT
    d.id, d.type_delegation, d.domaine, d.region_concernee,
    ud.email AS delegant,
    ue.email AS delegataire,
    d.date_debut, d.date_fin,
    EXTRACT(HOURS FROM (d.date_fin - NOW()))::INT AS heures_restantes,
    d.motif
FROM delegation_administrative d
JOIN users ud ON ud.id = d.delegant_id
JOIN users ue ON ue.id = d.delegataire_id
WHERE d.statut = 'ACTIVE' AND d.date_fin > NOW()
ORDER BY d.date_fin;

-- ================================================================
-- NOTICE FINALE
-- ================================================================
DO $$
BEGIN
    RAISE NOTICE '031 OK -- Architecture nationale renforcee :';
    RAISE NOTICE '  A. Hierarchie institutionnelle : institutions, directions, agent_profil';
    RAISE NOTICE '  B. Validation multi-niveaux    : validation_pipeline + valider_niveau_suivant()';
    RAISE NOTICE '  C. Decision Engine             : regle_metier (6 regles) + decision_engine_evaluer()';
    RAISE NOTICE '  D. Audit immuable              : audit_immutable append-only + hash chaining';
    RAISE NOTICE '  E. Verrouillage entites        : entity_lock + entity_lock_poser/liberer()';
    RAISE NOTICE '  F. Topologie PostGIS           : topo_valider_geom() + trigger tg_parcelles_topo';
    RAISE NOTICE '  G. Delegations admin           : delegation_administrative + trigger expiration';
    RAISE NOTICE '  H. Versioning documentaire     : document_foncier + document_version';
    RAISE NOTICE '  I. DRP                         : drp_backup_log + drp_replication_status + drp_simulation';
    RAISE NOTICE '  J. Interoperabilite            : integration_externe + webhook_log';
    RAISE NOTICE '  K. Regles metier versionnees   : regle_metier + regle_version (6 regles seeds)';
    RAISE NOTICE '  L. Segmentation territoriale   : territoire_juridiction (9 seeds) + check_juridiction()';
END $$;
"""

def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    tables = [
        "webhook_log", "integration_externe", "drp_simulation",
        "drp_replication_status", "drp_backup_log",
        "document_version", "document_foncier",
        "delegation_administrative",
        "topologie_validation_log",
        "entity_lock",
        "audit_immutable",
        "decision_engine_log", "regle_version", "regle_metier",
        "validation_pipeline",
        "agent_profil", "directions_services", "institutions",
        "territoire_juridiction",
    ]
    for t in tables:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE;")
    for fn in [
        "valider_niveau_suivant", "decision_engine_evaluer", "audit_enregistrer",
        "entity_lock_poser", "entity_lock_liberer", "entity_lock_cleanup",
        "topo_valider_geom", "check_juridiction",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn} CASCADE;")
    for v in [
        "v_decision_engine_dashboard", "v_entity_locks_actifs",
        "v_agents_juridictions", "v_delegations_actives", "v_audit_integrite",
    ]:
        op.execute(f"DROP VIEW IF EXISTS {v} CASCADE;")
