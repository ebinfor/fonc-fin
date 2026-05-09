"""030_ccfm_schema_officiel

FONCIER+ -- Migration 030 : Schema CCFM officiel
Schema reglementaire Direction Generale de l Urbanisme du Niger.

7 acteurs : Guichetiere -> Systeme -> Secretaire -> Topographe -> Systeme -> Directeur -> Archiviste
15 etats   : DEMANDE_RECUE -> ... -> RETIRE / REJETE
5 tables   : demandes_ccfm (refonte) + ordres_mission + fiches_constat + rapports_appreciation + historique
+ colonne   : pdf_demande_url (formulaire A4 remis au beneficiaire)

Revision ID: 030_ccfm_schema_officiel
Revises: 029_annf_dar_liaison
"""
from alembic import op
import sqlalchemy as sa

revision      = "030_ccfm_schema_officiel"
down_revision = "029_annf_dar_liaison"
branch_labels = None
depends_on    = None

SQL_009 = """
-- ═══════════════════════════════════════════════════════════════
-- FONCIER+ v3 — Migration 009 : Module CCFM (Réorganisé)
-- Certificat de Conformité Foncière Mixte
--
-- Workflow réorganisé — 7 acteurs :
--   1. GUICHETIÈRE    → Enregistrement demande + paiement 50 000 FCFA → NUS
--   2. SYSTÈME AUTO   → Vérification RNAF + BGU → Ticket réponse (GPS inclus)
--   3. SECRÉTAIRE     → Consulte ticket → Émet ordre de mission (ticket joint)
--   4. TOPOGRAPHE     → Fiche de Vérification Administrative Foncière (formulaire officiel)
--   5. SYSTÈME AUTO   → Compile ticket + fiche → Rapport d'appréciation → Directeur
--   6. DIRECTEUR      → Consulte rapport → Signe → PDF certificat
--   7. ARCHIVISTE     → Archivage ANNF + blockchain
--
-- Le CCFM n'est NI un acte de cession, NI un titre foncier,
-- mais une CONDITION PRÉALABLE OBLIGATOIRE à tout titre foncier
-- ou transaction légale.
-- ═══════════════════════════════════════════════════════════════

-- ─── ENUMS ──────────────────────────────────────────────────

DO $$ BEGIN
    CREATE TYPE etat_ccfm AS ENUM (
        'DEMANDE_RECUE',                -- 1. Guichetière enregistre
        'VERIFICATION_AUTO',            -- 2. Système vérifie RNAF+BGU
        'TICKET_EMIS',                  -- 2b. Ticket RNAF+BGU+GPS → secrétaire
        'VERIFICATION_AUTO_KO',         -- 2b. Rejet automatique
        'ORDRE_MISSION_EMIS',           -- 3. Secrétaire émet ordre (ticket joint)
        'ATTENTE_VISITE_TERRAIN',       -- 3b. Topographe assigné
        'VISITE_EN_COURS',              -- 4. Mission terrain en cours
        'FICHE_CONSTAT_DEPOSEE',        -- 4b. Fiche de constat déposée
        'RAPPORT_APPRECIATION',         -- 5. Système génère rapport
        'ATTENTE_SIGNATURE_DIRECTEUR',  -- 5b. Rapport soumis au directeur
        'SIGNE_DIRECTEUR',              -- 6. Directeur signe → PDF
        'ARCHIVE',                      -- 7. Archivé ANNF + blockchain
        'RETRAIT_EN_ATTENTE',           -- 7b. Prêt pour retrait bénéficiaire
        'RETIRE',                       -- 7c. Retiré
        'REJETE'                        -- Rejet à tout stade
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE mode_paiement_ccfm AS ENUM (
        'AIRTEL_MONEY', 'MOOV_MONEY', 'BANQUE', 'TRESOR_PUBLIC'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE motif_rejet_ccfm AS ENUM (
        'PARCELLE_INTROUVABLE', 'ARRETE_INVALIDE', 'ARRETE_NON_PUBLIE',
        'RNAF_NON_CONFORME', 'BGU_NON_CONFORME', 'GPS_NON_CONCORDANT',
        'OCCUPATION_ILLEGALE', 'CONFLIT_TERRAIN', 'DOCUMENTS_INCOMPLETS',
        'PAIEMENT_INVALIDE', 'SUPERFICIE_NON_CONCORDANTE', 'TITRE_CONTESTABLE',
        'AUTRE'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE verdict_ccfm AS ENUM (
        'CONFORME', 'A_VERIFIER', 'NON_CONFORME'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─── TABLE PRINCIPALE : demandes_ccfm ───────────────────────

CREATE TABLE IF NOT EXISTS demandes_ccfm (
    ccfm_id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    nus                 VARCHAR(50)   UNIQUE NOT NULL,        -- NUS-{année}-{seq:05d}
    reference_ccfm      VARCHAR(100)  UNIQUE,                 -- CCFM/{année}/{mois}/{seq:04d}

    -- Identité demandeur
    nom_prenom          VARCHAR(200)  NOT NULL,
    nip_passeport       VARCHAR(50)   NOT NULL,
    date_naissance      DATE          NOT NULL,
    lieu_naissance      VARCHAR(200)  NOT NULL,
    nationalite         VARCHAR(2)    DEFAULT 'NE',
    adresse             TEXT          NOT NULL,
    telephone           VARCHAR(20)   NOT NULL,
    email               VARCHAR(200),

    -- Parcelle
    localite            VARCHAR(200)  NOT NULL,
    lot                 VARCHAR(50)   NOT NULL,
    parcelle            VARCHAR(50)   NOT NULL,
    superficie_m2       NUMERIC(12,2) NOT NULL CHECK (superficie_m2 > 0),
    geom                GEOMETRY(Point, 4326),

    -- Références foncières (optionnelles — vérifiées par le système)
    numero_arrete       VARCHAR(100),
    numero_titre_foncier VARCHAR(100),
    nicad_code          TEXT,

    -- Paiement (50 000 FCFA forfaitaire)
    mode_paiement       mode_paiement_ccfm NOT NULL,
    reference_paiement  VARCHAR(200)  NOT NULL,
    montant_paye        NUMERIC(10,2) DEFAULT 50000 CHECK (montant_paye >= 50000),
    date_paiement       TIMESTAMP,

    -- Workflow
    etat                etat_ccfm     NOT NULL DEFAULT 'DEMANDE_RECUE',
    motif_rejet         motif_rejet_ccfm,
    observations_rejet  TEXT,

    -- Acteurs
    enregistre_par      TEXT,                               -- Guichetière (étape 1)
    topographe_assigne  TEXT,                               -- Topographe (étape 4)
    directeur_validateur TEXT,                              -- Directeur (étape 6)
    archiviste          TEXT,                               -- Archiviste (étape 7)

    -- ── TICKET RNAF+BGU+GPS (étape 2) ──────────────────────
    -- Résultats vérification automatique
    rnaf_conforme       BOOLEAN,
    rnaf_anomalies      TEXT,
    bgu_conforme        BOOLEAN,
    bgu_anomalies       TEXT,
    -- Coordonnées GPS extraites de la BGU
    latitude            NUMERIC(10,7),
    longitude           NUMERIC(10,7),
    ticket_hash         VARCHAR(64),                        -- SHA-256 du ticket

    -- ── PDF et sécurité (étape 6) ──────────────────────────
    hash_ccfm           VARCHAR(64),                        -- SHA-256 certificat
    qr_code             TEXT,                               -- Données QR
    signature_directeur TEXT,
    pdf_url             TEXT,                               -- Chemin PDF généré

    -- ── Archivage ANNF (étape 7) ───────────────────────────
    annf_code           VARCHAR(50),                        -- ANNF-CCFM-{année}-{seq:05d}
    tx_blockchain       VARCHAR(100),                       -- Hash blockchain

    -- Dates jalons
    date_enregistrement     TIMESTAMP  NOT NULL DEFAULT NOW(),
    date_verification_auto  TIMESTAMP,
    date_ticket_emis        TIMESTAMP,
    date_assignation_topographe TIMESTAMP,
    date_visite_terrain     TIMESTAMP,
    date_rapport_topographe TIMESTAMP,
    date_rapport_apprec     TIMESTAMP,
    date_validation_directeur TIMESTAMP,
    date_signature          TIMESTAMP,
    date_archivage          TIMESTAMP,
    date_retrait            TIMESTAMP,
    updated_at              TIMESTAMP  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ccfm_nus     ON demandes_ccfm(nus);
CREATE INDEX IF NOT EXISTS idx_ccfm_etat    ON demandes_ccfm(etat);
CREATE INDEX IF NOT EXISTS idx_ccfm_lot     ON demandes_ccfm(lot, parcelle);
CREATE INDEX IF NOT EXISTS idx_ccfm_localite ON demandes_ccfm(localite);
CREATE INDEX IF NOT EXISTS idx_ccfm_geom    ON demandes_ccfm USING GIST(geom);

-- ─── TABLE : ordres_mission_ccfm ───────────────────────────
-- Étape 3 — Secrétaire → Topographe (ticket joint automatiquement)

CREATE TABLE IF NOT EXISTS ordres_mission_ccfm (
    ordre_id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    ccfm_id             UUID         NOT NULL REFERENCES demandes_ccfm(ccfm_id) ON DELETE CASCADE,
    numero_ordre        VARCHAR(50)  UNIQUE NOT NULL,        -- OM-{année}-{seq:05d}
    type_mission        VARCHAR(20)  DEFAULT 'TOPOGRAPHE',
    destinataire_id     TEXT         NOT NULL,
    destinataire_nom    TEXT,
    date_emission       TIMESTAMP    NOT NULL DEFAULT NOW(),
    date_prevue_visite  DATE,
    instructions        TEXT,
    -- Ticket RNAF+BGU+GPS joint automatiquement (étape 2)
    ticket_joint        JSONB,                              -- {rnaf_ok, bgu_ok, lat, lon, ticket_hash}
    emis_par            TEXT,                               -- ID secrétaire
    statut              VARCHAR(20)  DEFAULT 'EN_ATTENTE',  -- EN_ATTENTE, EXECUTE, ANNULE
    date_execution      TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ordre_ccfm    ON ordres_mission_ccfm(ccfm_id);
CREATE INDEX IF NOT EXISTS idx_ordre_topo    ON ordres_mission_ccfm(destinataire_id);

-- ─── TABLE : fiches_constat_ccfm ───────────────────────────
-- Étape 4 — Topographe : Fiche de Vérification Administrative Foncière
-- Conforme au formulaire officiel du Ministère de l'Urbanisme (9 sections)

CREATE TABLE IF NOT EXISTS fiches_constat_ccfm (
    fiche_id            UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    ccfm_id             UUID          NOT NULL REFERENCES demandes_ccfm(ccfm_id) ON DELETE CASCADE,

    -- §1 Références (pré-remplies)
    objet_demande       TEXT,

    -- §2 Arrêté de lotissement
    arrete_numero       VARCHAR(100),
    arrete_date_signature DATE,
    arrete_autorite     TEXT,
    jo_publie           BOOLEAN       DEFAULT FALSE,
    jo_reference        TEXT,

    -- §3 Assiette foncière
    parcelle_ref_titre  TEXT,
    superficie_mesuree_m2 NUMERIC(12,2),
    -- GPS mesuré sur terrain (à comparer avec GPS du ticket)
    gps_mesure_latitude  NUMERIC(10,7),
    gps_mesure_longitude NUMERIC(10,7),
    parcelle_conforme   BOOLEAN,
    concordance_gps     BOOLEAN,                            -- GPS ticket ↔ mesure terrain
    ecart_superficie_pct NUMERIC(5,2),

    -- §4 Titre foncier
    titre_foncier_numero TEXT,
    titre_foncier_emetteur TEXT,
    titre_authentique   BOOLEAN,

    -- §6 Observations
    observations        TEXT,

    -- §7 Décision
    decision            VARCHAR(20)   CHECK (decision IN ('AUTORISATION', 'REFUS')),
    motif_refus         TEXT,

    -- §8 Signatures
    topographe_nom      TEXT          NOT NULL,
    topographe_fonction TEXT          DEFAULT 'Topographe agréé',
    topographe_id       TEXT,

    -- Photos géolocalisées
    photos_terrain      JSONB         DEFAULT '[]'::jsonb,

    -- Intégrité
    hash_fiche          VARCHAR(64),                        -- SHA-256
    created_at          TIMESTAMP     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fiche_ccfm ON fiches_constat_ccfm(ccfm_id);

-- ─── TABLE : rapports_appreciation_ccfm ─────────────────────
-- Étape 5 — Système : Rapport d'appréciation compilé (ticket + fiche → directeur)

CREATE TABLE IF NOT EXISTS rapports_appreciation_ccfm (
    rapport_id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    ccfm_id             UUID          NOT NULL REFERENCES demandes_ccfm(ccfm_id) ON DELETE CASCADE,

    -- Données ticket RNAF+BGU (étape 2)
    rnaf_conforme       BOOLEAN,
    bgu_conforme        BOOLEAN,
    gps_ticket_lat      NUMERIC(10,7),
    gps_ticket_lon      NUMERIC(10,7),

    -- Données fiche constat (étape 4)
    superficie_mesuree_m2 NUMERIC(12,2),
    ecart_superficie_pct  NUMERIC(5,2),
    gps_mesure_lat      NUMERIC(10,7),
    gps_mesure_lon      NUMERIC(10,7),
    concordance_gps     BOOLEAN,                            -- ticket GPS vs mesure terrain
    jo_publie           BOOLEAN,
    titre_authentique   BOOLEAN,
    decision_topographe VARCHAR(20),                        -- AUTORISATION ou REFUS
    observations_topographe TEXT,

    -- Verdict système compilé
    verdict_global      verdict_ccfm  NOT NULL,             -- CONFORME / A_VERIFIER / NON_CONFORME
    points_conformite   JSONB         DEFAULT '[]'::jsonb,  -- Liste des points conformes
    anomalies_detectees JSONB         DEFAULT '[]'::jsonb,  -- Liste des anomalies

    -- Intégrité
    rapport_hash        VARCHAR(64),                        -- SHA-256
    created_at          TIMESTAMP     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rapport_apprec_ccfm ON rapports_appreciation_ccfm(ccfm_id);

-- ─── TABLE : historique_ccfm ────────────────────────────────

CREATE TABLE IF NOT EXISTS historique_ccfm (
    historique_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    ccfm_id             UUID          NOT NULL REFERENCES demandes_ccfm(ccfm_id) ON DELETE CASCADE,
    etat_precedent      VARCHAR(50),
    etat_nouveau        VARCHAR(50)   NOT NULL,
    action              VARCHAR(100)  NOT NULL,
    commentaire         TEXT,
    effectue_par        TEXT,
    role_effectueur     VARCHAR(50),  -- GUICHETIERE, SYSTEME, SECRETAIRE, TOPOGRAPHE, DIRECTEUR, ARCHIVISTE
    metadata            JSONB,
    created_at          TIMESTAMP     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_historique_ccfm  ON historique_ccfm(ccfm_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_historique_user  ON historique_ccfm(effectue_par);

-- ═══════════════════════════════════════════════════════════════
-- FONCTIONS
-- ═══════════════════════════════════════════════════════════════

-- Génération automatique du NUS
CREATE OR REPLACE FUNCTION generer_nus_ccfm()
RETURNS VARCHAR AS $$
DECLARE
    v_year    VARCHAR(4);
    v_counter INTEGER;
BEGIN
    v_year := TO_CHAR(NOW(), 'YYYY');
    SELECT COUNT(*) + 1 INTO v_counter
    FROM demandes_ccfm
    WHERE EXTRACT(YEAR FROM date_enregistrement) = EXTRACT(YEAR FROM NOW());
    RETURN 'NUS-' || v_year || '-' || LPAD(v_counter::TEXT, 5, '0');
END;
$$ LANGUAGE plpgsql;

-- Génération référence CCFM à la signature
CREATE OR REPLACE FUNCTION generer_reference_ccfm()
RETURNS VARCHAR AS $$
DECLARE
    v_year    VARCHAR(4);
    v_month   VARCHAR(2);
    v_counter INTEGER;
BEGIN
    v_year  := TO_CHAR(NOW(), 'YYYY');
    v_month := TO_CHAR(NOW(), 'MM');
    SELECT COUNT(*) + 1 INTO v_counter
    FROM demandes_ccfm
    WHERE date_signature IS NOT NULL
      AND EXTRACT(YEAR  FROM date_signature) = EXTRACT(YEAR  FROM NOW())
      AND EXTRACT(MONTH FROM date_signature) = EXTRACT(MONTH FROM NOW());
    RETURN 'CCFM/' || v_year || '/' || v_month || '/' || LPAD(v_counter::TEXT, 4, '0');
END;
$$ LANGUAGE plpgsql;

-- Vérification publique CCFM (gate inter-modules)
CREATE OR REPLACE FUNCTION verifier_ccfm_valide(
    p_nus        VARCHAR DEFAULT NULL,
    p_reference  VARCHAR DEFAULT NULL,
    p_lot        VARCHAR DEFAULT NULL,
    p_parcelle   VARCHAR DEFAULT NULL
)
RETURNS TABLE (
    valide           BOOLEAN,
    statut           VARCHAR,
    message          TEXT,
    ccfm_id          UUID,
    reference_ccfm   VARCHAR,
    date_signature   TIMESTAMP,
    date_expiration  TIMESTAMP,
    beneficiaire     VARCHAR
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        CASE
            WHEN c.etat IN ('ARCHIVE','RETRAIT_EN_ATTENTE','RETIRE')
                 AND c.date_signature IS NOT NULL
                 AND c.date_signature + INTERVAL '2 years' > NOW()
            THEN TRUE ELSE FALSE
        END,
        CASE
            WHEN c.etat IN ('ARCHIVE','RETRAIT_EN_ATTENTE','RETIRE')
                 AND c.date_signature + INTERVAL '2 years' > NOW()
            THEN 'VALIDE'::VARCHAR
            WHEN c.date_signature IS NOT NULL
                 AND c.date_signature + INTERVAL '2 years' <= NOW()
            THEN 'EXPIRE'::VARCHAR
            WHEN c.etat = 'REJETE' THEN 'REVOQUE'::VARCHAR
            WHEN c.date_signature IS NULL THEN 'EN_COURS'::VARCHAR
            ELSE 'INVALIDE'::VARCHAR
        END,
        CASE
            WHEN c.etat IN ('ARCHIVE','RETRAIT_EN_ATTENTE','RETIRE')
                 AND c.date_signature + INTERVAL '2 years' > NOW()
            THEN 'CCFM valide'::TEXT
            WHEN c.etat = 'REJETE' THEN 'CCFM rejeté : ' || COALESCE(c.observations_rejet,'Non conforme')
            WHEN c.date_signature IS NOT NULL
                 AND c.date_signature + INTERVAL '2 years' <= NOW()
            THEN 'CCFM expiré (validité 2 ans)'::TEXT
            ELSE 'CCFM en cours de traitement'::TEXT
        END,
        c.ccfm_id, c.reference_ccfm, c.date_signature,
        c.date_signature + INTERVAL '2 years',
        c.nom_prenom
    FROM demandes_ccfm c
    WHERE (p_nus IS NOT NULL AND c.nus = p_nus)
       OR (p_reference IS NOT NULL AND c.reference_ccfm = p_reference)
       OR (p_lot IS NOT NULL AND p_parcelle IS NOT NULL AND c.lot=p_lot AND c.parcelle=p_parcelle)
    ORDER BY c.date_signature DESC NULLS LAST
    LIMIT 1;
END;
$$ LANGUAGE plpgsql STABLE;

-- Statistiques CCFM
CREATE OR REPLACE FUNCTION stats_ccfm(p_localite VARCHAR DEFAULT NULL)
RETURNS TABLE (
    total_demandes    BIGINT,
    en_cours          BIGINT,
    signes            BIGINT,
    rejetes           BIGINT,
    retires           BIGINT,
    delai_moyen_jours NUMERIC,
    taux_conformite   NUMERIC,
    montant_total_fcfa NUMERIC
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*),
        COUNT(*) FILTER (WHERE etat NOT IN ('ARCHIVE','RETRAIT_EN_ATTENTE','RETIRE','REJETE')),
        COUNT(*) FILTER (WHERE etat IN ('SIGNE_DIRECTEUR','ARCHIVE','RETRAIT_EN_ATTENTE','RETIRE')),
        COUNT(*) FILTER (WHERE etat = 'REJETE'),
        COUNT(*) FILTER (WHERE etat = 'RETIRE'),
        ROUND(AVG(EXTRACT(DAY FROM (date_signature - date_enregistrement)))
              FILTER (WHERE date_signature IS NOT NULL), 1),
        CASE WHEN COUNT(*) FILTER (WHERE etat IN ('ARCHIVE','RETIRE','REJETE')) > 0
             THEN ROUND(COUNT(*) FILTER (WHERE etat IN ('ARCHIVE','RETIRE'))::NUMERIC * 100
                  / COUNT(*) FILTER (WHERE etat IN ('ARCHIVE','RETIRE','REJETE')), 1)
             ELSE 0 END,
        COALESCE(SUM(montant_paye), 0)
    FROM demandes_ccfm
    WHERE p_localite IS NULL OR localite = p_localite;
END;
$$ LANGUAGE plpgsql;

-- ═══════════════════════════════════════════════════════════════
-- TRIGGERS
-- ═══════════════════════════════════════════════════════════════

-- Trigger : Générer NUS + géométrie PostGIS
CREATE OR REPLACE FUNCTION trg_generer_nus_ccfm()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.nus IS NULL OR NEW.nus = '' THEN
        NEW.nus := generer_nus_ccfm();
    END IF;
    IF NEW.latitude IS NOT NULL AND NEW.longitude IS NOT NULL AND NEW.geom IS NULL THEN
        NEW.geom := ST_SetSRID(ST_MakePoint(NEW.longitude::float, NEW.latitude::float), 4326);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ccfm_nus ON demandes_ccfm;
CREATE TRIGGER trg_ccfm_nus
    BEFORE INSERT ON demandes_ccfm
    FOR EACH ROW EXECUTE FUNCTION trg_generer_nus_ccfm();

-- Trigger : Mettre à jour géométrie PostGIS quand GPS reçu (étape 2)
CREATE OR REPLACE FUNCTION trg_update_geom_ccfm()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.latitude IS NOT NULL AND NEW.longitude IS NOT NULL
       AND (OLD.latitude IS NULL OR OLD.latitude != NEW.latitude) THEN
        NEW.geom := ST_SetSRID(ST_MakePoint(NEW.longitude::float, NEW.latitude::float), 4326);
        NEW.date_ticket_emis := NOW();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ccfm_geom ON demandes_ccfm;
CREATE TRIGGER trg_ccfm_geom
    BEFORE UPDATE OF latitude, longitude ON demandes_ccfm
    FOR EACH ROW EXECUTE FUNCTION trg_update_geom_ccfm();

-- Trigger : Générer référence CCFM à la signature
CREATE OR REPLACE FUNCTION trg_generer_reference_ccfm()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.date_signature IS NOT NULL AND OLD.date_signature IS NULL THEN
        NEW.reference_ccfm := generer_reference_ccfm();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ccfm_reference ON demandes_ccfm;
CREATE TRIGGER trg_ccfm_reference
    BEFORE UPDATE OF date_signature ON demandes_ccfm
    FOR EACH ROW EXECUTE FUNCTION trg_generer_reference_ccfm();

-- Trigger : Historique automatique
CREATE OR REPLACE FUNCTION trg_log_historique_ccfm()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.etat != OLD.etat THEN
        INSERT INTO historique_ccfm (
            ccfm_id, etat_precedent, etat_nouveau, action, metadata
        ) VALUES (
            NEW.ccfm_id, OLD.etat::TEXT, NEW.etat::TEXT, 'CHANGEMENT_ETAT',
            jsonb_build_object('old_etat', OLD.etat::TEXT, 'new_etat', NEW.etat::TEXT, 'date', NOW())
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ccfm_historique ON demandes_ccfm;
CREATE TRIGGER trg_ccfm_historique
    AFTER UPDATE OF etat ON demandes_ccfm
    FOR EACH ROW EXECUTE FUNCTION trg_log_historique_ccfm();

-- Trigger : Updated_at
CREATE OR REPLACE FUNCTION trg_update_timestamp_ccfm()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ccfm_updated ON demandes_ccfm;
CREATE TRIGGER trg_ccfm_updated
    BEFORE UPDATE ON demandes_ccfm
    FOR EACH ROW EXECUTE FUNCTION trg_update_timestamp_ccfm();

-- ═══════════════════════════════════════════════════════════════
-- VUES
-- ═══════════════════════════════════════════════════════════════

-- Vue : Dashboard CCFM
CREATE OR REPLACE VIEW v_dashboard_ccfm AS
SELECT
    c.ccfm_id, c.nus, c.reference_ccfm,
    c.nom_prenom AS demandeur,
    c.localite || ' — Lot ' || c.lot || ' Parc. ' || c.parcelle AS parcelle_complete,
    c.superficie_m2, c.etat,
    c.rnaf_conforme, c.bgu_conforme,
    c.latitude AS gps_lat, c.longitude AS gps_lon,
    c.date_enregistrement, c.date_ticket_emis, c.date_signature,
    CASE WHEN c.date_signature IS NOT NULL
         THEN EXTRACT(DAY FROM (c.date_signature - c.date_enregistrement))
         ELSE EXTRACT(DAY FROM (NOW() - c.date_enregistrement))
    END AS delai_jours,
    c.topographe_assigne, c.directeur_validateur,
    CASE WHEN f.fiche_id IS NOT NULL THEN TRUE ELSE FALSE END AS fiche_deposee,
    CASE WHEN r.rapport_id IS NOT NULL THEN TRUE ELSE FALSE END AS rapport_genere,
    r.verdict_global,
    c.montant_paye
FROM demandes_ccfm c
LEFT JOIN fiches_constat_ccfm      f ON c.ccfm_id = f.ccfm_id
LEFT JOIN rapports_appreciation_ccfm r ON c.ccfm_id = r.ccfm_id;

-- Vue : Actions en attente par acteur
CREATE OR REPLACE VIEW v_ccfm_attente_action AS
SELECT
    c.ccfm_id, c.nus, c.nom_prenom,
    c.localite || ' — ' || c.lot || '/' || c.parcelle AS parcelle,
    c.etat::TEXT,
    EXTRACT(DAY FROM (NOW() - c.date_enregistrement)) AS jours_depuis_depot,
    CASE c.etat::TEXT
        WHEN 'DEMANDE_RECUE'               THEN 'VERIFICATION_RNAF_BGU'
        WHEN 'TICKET_EMIS'                 THEN 'EMISSION_ORDRE_MISSION'
        WHEN 'ORDRE_MISSION_EMIS'          THEN 'VISITE_TERRAIN'
        WHEN 'ATTENTE_VISITE_TERRAIN'      THEN 'VISITE_TERRAIN'
        WHEN 'VISITE_EN_COURS'             THEN 'DEPOT_FICHE_CONSTAT'
        WHEN 'FICHE_CONSTAT_DEPOSEE'       THEN 'GENERATION_RAPPORT'
        WHEN 'RAPPORT_APPRECIATION'        THEN 'SOUMISSION_DIRECTEUR'
        WHEN 'ATTENTE_SIGNATURE_DIRECTEUR' THEN 'SIGNATURE_DIRECTEUR'
        WHEN 'SIGNE_DIRECTEUR'             THEN 'ARCHIVAGE_ANNF'
        ELSE 'AUCUNE'
    END AS action_requise,
    CASE c.etat::TEXT
        WHEN 'DEMANDE_RECUE'               THEN 'SYSTEME'
        WHEN 'TICKET_EMIS'                 THEN 'SECRETAIRE'
        WHEN 'ORDRE_MISSION_EMIS'          THEN 'TOPOGRAPHE'
        WHEN 'ATTENTE_VISITE_TERRAIN'      THEN 'TOPOGRAPHE'
        WHEN 'VISITE_EN_COURS'             THEN 'TOPOGRAPHE'
        WHEN 'FICHE_CONSTAT_DEPOSEE'       THEN 'SYSTEME'
        WHEN 'RAPPORT_APPRECIATION'        THEN 'SYSTEME'
        WHEN 'ATTENTE_SIGNATURE_DIRECTEUR' THEN 'DIRECTEUR'
        WHEN 'SIGNE_DIRECTEUR'             THEN 'ARCHIVISTE'
        ELSE NULL
    END AS acteur_responsable
FROM demandes_ccfm c
WHERE c.etat NOT IN ('ARCHIVE','RETRAIT_EN_ATTENTE','RETIRE','REJETE')
ORDER BY c.date_enregistrement ASC;

"""

SQL_010 = """
-- ═══════════════════════════════════════════════════════════════════════════
-- FONCIER+ v3 — Patch 010 : colonne pdf_demande_url + index pdf
-- À appliquer après 009_ccfm_certificat.sql
-- ═══════════════════════════════════════════════════════════════════════════

-- Ajout colonne PDF formulaire demande A4 (généré à l'étape 1 — Guichetière)
ALTER TABLE demandes_ccfm
  ADD COLUMN IF NOT EXISTS pdf_demande_url TEXT;

COMMENT ON COLUMN demandes_ccfm.pdf_demande_url IS
  'Chemin PDF formulaire de demande A4 (généré à la création — remis au bénéficiaire)';

-- Index pour recherche rapide des PDFs disponibles
CREATE INDEX IF NOT EXISTS idx_ccfm_pdf_url
  ON demandes_ccfm (pdf_url)
  WHERE pdf_url IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ccfm_pdf_demande_url
  ON demandes_ccfm (pdf_demande_url)
  WHERE pdf_demande_url IS NOT NULL;

"""


def upgrade() -> None:
    op.execute(SQL_009)
    op.execute(SQL_010)
    op.execute("""
        DO $$
        BEGIN
            RAISE NOTICE '030 OK -- CCFM schema officiel 7 acteurs / 15 etats / 5 tables';
        END $$;
    """)


def downgrade() -> None:
    for tbl in ["historique_ccfm", "rapports_appreciation_ccfm",
                "fiches_constat_ccfm", "ordres_mission_ccfm"]:
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE;")
    op.execute("ALTER TABLE demandes_ccfm DROP COLUMN IF EXISTS pdf_demande_url;")
    op.execute("DROP VIEW IF EXISTS v_ccfm_attente_action;")
    op.execute("DROP VIEW IF EXISTS v_dashboard_ccfm;")
