"""040_ccfm_v3_integration

FONCIER+ v109 — Migration 040 : Intégration CCFM v3

Contenu :
  1.  Patch 010 — colonne pdf_demande_url sur demandes_ccfm
  2.  Table ordres_mission        (étape 3 workflow)
  3.  Table fiches_constat        (étape 4b — Topographe)
  4.  Table rapports_appreciation (étape 5 — rapport auto)
  5.  Table ccfm_historique       (log complet des transitions)
  6.  Index de performance CCFM v3

Révision : 040_ccfm_v3_integration / Revises : 039_corrections_audit
"""
from alembic import op

revision      = "040_ccfm_v3_integration"
down_revision = "039_corrections_audit"
branch_labels = None
depends_on    = None

SQL = r"""
-- ================================================================
-- 1. Patch 010 — colonne pdf_demande_url
-- ================================================================
ALTER TABLE demandes_ccfm
  ADD COLUMN IF NOT EXISTS pdf_demande_url TEXT;

COMMENT ON COLUMN demandes_ccfm.pdf_demande_url IS
  'Chemin PDF formulaire A4 remis au bénéficiaire à la création (étape 1)';

-- Index PDF
CREATE INDEX IF NOT EXISTS idx_ccfm_pdf_url
  ON demandes_ccfm(pdf_url)
  WHERE pdf_url IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ccfm_pdf_demande_url
  ON demandes_ccfm(pdf_demande_url)
  WHERE pdf_demande_url IS NOT NULL;


-- ================================================================
-- 2. Table ordres_mission (étape 3 — Secrétaire)
-- ================================================================
CREATE TABLE IF NOT EXISTS ordres_mission (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ccfm_id             UUID        NOT NULL REFERENCES demandes_ccfm(id) ON DELETE RESTRICT,
    ordre_numero        VARCHAR(30) UNIQUE,
    secretaire_id       UUID        REFERENCES users(id),
    topographe_id       UUID        REFERENCES users(id),
    huissier_id         UUID        REFERENCES users(id),
    date_emission       TIMESTAMP   NOT NULL DEFAULT NOW(),
    date_mission_prevue DATE,
    ticket_hash         VARCHAR(80),     -- hash ticket RNAF+BGU joint
    statut              VARCHAR(20) NOT NULL DEFAULT 'EMIS'
                            CHECK (statut IN ('EMIS','ACCEPTE','EN_COURS','TERMINE','ANNULE')),
    observations        TEXT,
    created_at          TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_om_ccfm     ON ordres_mission(ccfm_id);
CREATE INDEX IF NOT EXISTS idx_om_statut   ON ordres_mission(statut);
CREATE INDEX IF NOT EXISTS idx_om_topo     ON ordres_mission(topographe_id);


-- ================================================================
-- 3. Table fiches_constat (étape 4b — Topographe)
-- ================================================================
CREATE TABLE IF NOT EXISTS fiches_constat (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ccfm_id                     UUID        NOT NULL REFERENCES demandes_ccfm(id) ON DELETE RESTRICT,
    topographe_id               UUID        REFERENCES users(id),
    -- §2 Arrêté
    arrete_numero               VARCHAR(50),
    arrete_date_signature       DATE,
    arrete_autorite             VARCHAR(100),
    jo_publie                   BOOLEAN,
    jo_reference                VARCHAR(50),
    -- §3 GPS + superficie
    superficie_mesuree_m2       NUMERIC(10,2),
    ecart_superficie_m2         NUMERIC(10,2),
    ecart_superficie_pct        NUMERIC(5,2),
    gps_mesure_latitude         NUMERIC(10,6),
    gps_mesure_longitude        NUMERIC(10,6),
    gps_concordant              BOOLEAN,
    parcelle_conforme           BOOLEAN,
    -- §4 Titre foncier
    titre_foncier_numero        VARCHAR(50),
    titre_foncier_emetteur      VARCHAR(100),
    titre_authentique           BOOLEAN,
    -- §6 Observations
    observations                TEXT,
    -- §7 Décision
    decision                    VARCHAR(20)
                                    CHECK (decision IN ('AUTORISATION','REFUS','RESERVE')),
    motif_refus                 TEXT,
    -- Signature
    topographe_nom              VARCHAR(100),
    date_mission                DATE,
    -- Méta
    sha256                      VARCHAR(64),
    created_at                  TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fc_ccfm  ON fiches_constat(ccfm_id);
CREATE INDEX IF NOT EXISTS idx_fc_dec   ON fiches_constat(decision);


-- ================================================================
-- 4. Table rapports_appreciation (étape 5 — Système auto)
-- ================================================================
CREATE TABLE IF NOT EXISTS rapports_appreciation (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ccfm_id                 UUID        NOT NULL REFERENCES demandes_ccfm(id) ON DELETE RESTRICT,
    -- Synthèse
    rnaf_ok                 BOOLEAN,
    bgu_ok                  BOOLEAN,
    gps_concordant          BOOLEAN,
    superficie_conforme     BOOLEAN,
    ecart_pct               NUMERIC(5,2),
    verdict                 VARCHAR(20) CHECK (verdict IN ('CONFORME','A_VERIFIER','NON_CONFORME')),
    avis_systeme            VARCHAR(20) CHECK (avis_systeme IN ('FAVORABLE','DEFAVORABLE','RESERVE')),
    resume                  TEXT,
    -- Données brutes consolidées
    ticket_hash             VARCHAR(80),
    fiche_constat_id        UUID REFERENCES fiches_constat(id),
    -- Méta
    genere_automatiquement  BOOLEAN NOT NULL DEFAULT TRUE,
    sha256                  VARCHAR(64),
    created_at              TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ra_ccfm   ON rapports_appreciation(ccfm_id);
CREATE INDEX IF NOT EXISTS idx_ra_verdict ON rapports_appreciation(verdict);


-- ================================================================
-- 5. Table ccfm_historique (log complet des transitions d'état)
-- ================================================================
CREATE TABLE IF NOT EXISTS ccfm_historique (
    id          BIGSERIAL   PRIMARY KEY,
    ccfm_id     UUID        NOT NULL REFERENCES demandes_ccfm(id) ON DELETE RESTRICT,
    etat_avant  VARCHAR(40),
    etat_apres  VARCHAR(40) NOT NULL,
    acteur_id   UUID        REFERENCES users(id),
    acteur_role VARCHAR(30),
    commentaire TEXT,
    sha256      VARCHAR(64),
    created_at  TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- Trigger APPEND-ONLY
CREATE OR REPLACE FUNCTION tg_ccfm_hist_readonly()
RETURNS TRIGGER AS $fn$
BEGIN
    RAISE EXCEPTION 'ccfm_historique est append-only — aucune modification permise';
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_ccfm_hist_ro ON ccfm_historique;
CREATE TRIGGER tg_ccfm_hist_ro
    BEFORE UPDATE OR DELETE ON ccfm_historique
    FOR EACH ROW EXECUTE FUNCTION tg_ccfm_hist_readonly();

CREATE INDEX IF NOT EXISTS idx_ch_ccfm  ON ccfm_historique(ccfm_id);
CREATE INDEX IF NOT EXISTS idx_ch_date  ON ccfm_historique(created_at DESC);


-- ================================================================
-- 6. Index de performance CCFM v3
-- ================================================================
CREATE INDEX IF NOT EXISTS idx_ccfm_etat_date
    ON demandes_ccfm(etat, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ccfm_nus_etat
    ON demandes_ccfm(nus, etat);

CREATE INDEX IF NOT EXISTS idx_ccfm_lot_parcelle
    ON demandes_ccfm(lot, parcelle)
    WHERE etat IN ('SIGNE_DIRECTEUR','ARCHIVE','RETRAIT_EN_ATTENTE','RETIRE');


-- Notice
DO $n$
BEGIN
    RAISE NOTICE '040 OK — Intégration CCFM v3 :';
    RAISE NOTICE '  Patch 010 : colonne pdf_demande_url ajoutée';
    RAISE NOTICE '  Nouvelles tables : ordres_mission, fiches_constat,';
    RAISE NOTICE '    rapports_appreciation, ccfm_historique (append-only)';
    RAISE NOTICE '  Triggers : tg_ccfm_hist_readonly';
    RAISE NOTICE '  6 index de performance CCFM v3';
END;
$n$;
"""

def upgrade() -> None:
    op.execute(SQL)

def downgrade() -> None:
    for obj in (
        "INDEX idx_ccfm_lot_parcelle",
        "INDEX idx_ccfm_nus_etat",
        "INDEX idx_ccfm_etat_date",
        "TABLE ccfm_historique CASCADE",
        "TABLE rapports_appreciation CASCADE",
        "TABLE fiches_constat CASCADE",
        "TABLE ordres_mission CASCADE",
        "INDEX idx_ccfm_pdf_demande_url",
        "INDEX idx_ccfm_pdf_url",
    ):
        op.execute(f"DROP {obj} IF EXISTS;")
    op.execute("ALTER TABLE demandes_ccfm DROP COLUMN IF EXISTS pdf_demande_url;")
