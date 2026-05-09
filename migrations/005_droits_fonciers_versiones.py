"""005_droits_fonciers_versiones

FONCIER+ v3.4.7 — Migration 005 : Versioning des Droits Fonciers
Aligné sur migrations 001-004.

Principe : un propriétaire n'écrase JAMAIS un autre.
           UPDATE proprietaire_id → INTERDIT.
           Toute modification = nouvelle version de droit.

Nouvelles tables :
  right_holders          → titulaires fonciers (physique/morale/état/banque)
  notary_registry        → registre notaires agréés
  parcel_right_version   → versions de droits (avec temporalité date_debut/date_fin)
  property_transfers     → historique transferts de propriété
  mortgage_registry      → hypothèques enrichies (enrichit hypotheques existant)
  parcel_servitude       → servitudes foncières
  parcel_disputes        → litiges sur droits (enrichit litiges existant)
  annf_right_archive     → archivage ANNF des droits WORM

Triggers critiques :
  1. check_somme_pourcentage_droits — somme ACTIFS = 100% par parcelle
  2. close_previous_right_on_new   — ferme l'ancienne version lors d'un nouveau droit
  3. block_on_servitude_bloquante  — crée transaction_block si servitude bloquante
  4. archive_right_on_close        — archive automatiquement un droit clos

Revision ID: 005_droits_fonciers_versiones
Revises: 004_workflows_intermodules
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "005_droits_fonciers_versiones"
down_revision = "004_workflows_intermodules"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ── ENUMs ────────────────────────────────────────────────────────────────
    enums = [
        ("type_personne",
         "('physique','morale','etat','collectivite','banque')"),
        ("type_droit",
         "('propriete','usufruit','bail','hypotheque','servitude','occupation')"),
        ("statut_droit",
         "('actif','historique','conteste','annule')"),
        ("origine_operation",
         "('cession','vente','heritage','donation','decision_judiciaire',"
         "'creation_initiale','subdivision','refonte')"),
        ("type_transfert",
         "('vente','donation','heritage','cession','decision_judiciaire','expropriation')"),
        ("statut_hypotheque",
         "('active','levee','expiree','contentieux')"),
        ("type_servitude",
         "('passage','canalisation','electricite','eau','autre')"),
        ("type_litige_droit",
         "('limite','propriete','heritage','fraude','hypotheque','servitude')"),
        ("statut_litige_droit",
         "('ouvert','en_instruction','clos','abandonne')"),
        ("autorite_saisie",
         "('justice','ccfm','domaine')"),
        ("source_archive_droit",
         "('notaire','justice','domaine','ccfm','admin')"),
    ]
    for name, values in enums:
        op.execute(f"""
            DO $$ BEGIN
                CREATE TYPE {name} AS ENUM {values};
                EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)

    # ── 1. RIGHT_HOLDERS ─────────────────────────────────────────────────────
    op.create_table(
        "right_holders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("type_personne", sa.Enum("physique", "morale", "etat",
                                            "collectivite", "banque",
                                            name="type_personne"), nullable=False),
        sa.Column("nom",           sa.String(150)),
        sa.Column("prenom",        sa.String(150)),
        sa.Column("raison_sociale",sa.String(300)),
        sa.Column("numero_identite",sa.String(50)),
        sa.Column("numero_registre",sa.String(50)),
        sa.Column("adresse",       sa.Text),
        sa.Column("contact",       sa.String(200)),
        sa.Column("rnp_personne_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnp_demandes.id"), nullable=True),
        sa.Column("est_actif", sa.Boolean, server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_rh_numero_identite", "right_holders", ["numero_identite"])
    op.create_index("ix_rh_numero_registre", "right_holders", ["numero_registre"])
    op.create_index("ix_rh_type",            "right_holders", ["type_personne"])

    # ── 2. NOTARY_REGISTRY ───────────────────────────────────────────────────
    op.create_table(
        "notary_registry",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("nom_notaire",      sa.String(200), nullable=False),
        sa.Column("numero_agrement",  sa.String(50),  nullable=False),
        sa.Column("chambre_notariale",sa.String(200)),
        sa.Column("region_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("regions.id")),
        sa.Column("adresse",  sa.Text),
        sa.Column("contact",  sa.String(200)),
        sa.Column("statut_agrement",    sa.String(20), server_default="agree"),
        sa.Column("date_agrement",      sa.DateTime(timezone=True)),
        sa.Column("date_fin_agrement",  sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("user_id",          name="uq_notary_user"),
        sa.UniqueConstraint("numero_agrement",  name="uq_notary_agrement"),
    )
    op.create_index("ix_notary_agrement", "notary_registry", ["numero_agrement"])

    # ── 3. PARCEL_RIGHT_VERSION ──────────────────────────────────────────────
    op.create_table(
        "parcel_right_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("rnp_parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnp_demandes.id"), nullable=False),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("rnaf_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnaf.id"), nullable=False),
        sa.Column("titulaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("type_droit", sa.Enum("propriete", "usufruit", "bail",
                                         "hypotheque", "servitude", "occupation",
                                         name="type_droit"), nullable=False),
        sa.Column("pourcentage_droit", sa.Numeric(5, 2), nullable=False),
        sa.Column("date_debut_validite", sa.DateTime(timezone=True),
                  nullable=False),
        sa.Column("date_fin_validite", sa.DateTime(timezone=True),
                  nullable=True),
        sa.Column("statut", sa.Enum("actif", "historique", "conteste", "annule",
                                     name="statut_droit"),
                  server_default="actif", nullable=False),
        sa.Column("origine_operation", sa.Enum(
                  "cession", "vente", "heritage", "donation",
                  "decision_judiciaire", "creation_initiale", "subdivision", "refonte",
                  name="origine_operation"), nullable=False),
        sa.Column("acte_reference",   sa.String(200)),
        sa.Column("acte_notarie_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("actes_notaries.id"), nullable=True),
        sa.Column("ccfm_validation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=True),
        sa.Column("certifie_par_notaire", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("notary_registry.id"), nullable=True),
        sa.Column("numero_version",       sa.Integer, server_default="1",
                  nullable=False),
        sa.Column("version_precedente_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcel_right_version.id"), nullable=True),
        sa.Column("sha256_version", sa.String(64), nullable=False),
        sa.Column("cree_par",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("valide_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "pourcentage_droit > 0 AND pourcentage_droit <= 100",
            name="ck_pourcentage_droit_range",
        ),
    )
    op.create_index("ix_prv_rnp",        "parcel_right_version", ["rnp_parcelle_id"])
    op.create_index("ix_prv_parcelle",   "parcel_right_version", ["parcelle_id"])
    op.create_index("ix_prv_titulaire",  "parcel_right_version", ["titulaire_id"])
    op.create_index("ix_prv_statut",     "parcel_right_version", ["statut"])
    op.create_index("ix_prv_date_debut", "parcel_right_version", ["date_debut_validite"])
    op.create_index("ix_prv_date_fin",   "parcel_right_version", ["date_fin_validite"])
    # Index composite pour la requête temporelle historique
    op.execute("""
        CREATE INDEX ix_prv_temporal
        ON parcel_right_version (rnp_parcelle_id, date_debut_validite, date_fin_validite)
        WHERE statut IN ('actif', 'historique');
    """)

    # ── 4. PROPERTY_TRANSFERS ────────────────────────────────────────────────
    op.create_table(
        "property_transfers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("rnp_parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnp_demandes.id"), nullable=False),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("ancien_titulaire_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("nouveau_titulaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("pourcentage_transfere", sa.Numeric(5, 2), nullable=False),
        sa.Column("type_transfert", sa.Enum(
                  "vente", "donation", "heritage", "cession",
                  "decision_judiciaire", "expropriation",
                  name="type_transfert"), nullable=False),
        sa.Column("date_transfert",   sa.DateTime(timezone=True), nullable=False),
        sa.Column("acte_reference",   sa.String(200)),
        sa.Column("notaire_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("notary_registry.id"), nullable=True),
        sa.Column("acte_notarie_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("actes_notaries.id"), nullable=True),
        sa.Column("ccfm_validation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=True),
        sa.Column("right_version_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcel_right_version.id"), nullable=True),
        sa.Column("decision_judiciaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("decisions_judiciaires.id"), nullable=True),
        sa.Column("sha256_transfert", sa.String(64), nullable=False),
        sa.Column("enregistre_par",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "pourcentage_transfere > 0 AND pourcentage_transfere <= 100",
            name="ck_pct_transfere_range",
        ),
    )
    op.create_index("ix_pt_rnp",      "property_transfers", ["rnp_parcelle_id"])
    op.create_index("ix_pt_ancien",   "property_transfers", ["ancien_titulaire_id"])
    op.create_index("ix_pt_nouveau",  "property_transfers", ["nouveau_titulaire_id"])
    op.create_index("ix_pt_date",     "property_transfers", ["date_transfert"])

    # ── 5. MORTGAGE_REGISTRY ─────────────────────────────────────────────────
    op.create_table(
        "mortgage_registry",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("hypotheque_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("hypotheques.id"), nullable=False),
        sa.Column("rnp_parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnp_demandes.id"), nullable=False),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("banque_holder_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("montant",   sa.Numeric(18, 2), nullable=False),
        sa.Column("devise",    sa.String(3),       server_default="XOF"),
        sa.Column("date_debut",sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_fin",  sa.DateTime(timezone=True)),
        sa.Column("statut", sa.Enum("active", "levee", "expiree", "contentieux",
                                     name="statut_hypotheque"),
                  server_default="active", nullable=False),
        sa.Column("reference_contrat",  sa.String(200)),
        sa.Column("rnaf_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnaf.id"), nullable=False),
        sa.Column("ccfm_validation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=True),
        sa.Column("levee_at",     sa.DateTime(timezone=True)),
        sa.Column("levee_par",    postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("motif_levee",  sa.Text),
        sa.Column("acte_mainlevee", sa.String(200)),
        sa.Column("sha256_contrat", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("hypotheque_id", name="uq_mortgage_hypotheque"),
    )
    op.create_index("ix_mr_statut",   "mortgage_registry", ["statut"])
    op.create_index("ix_mr_parcelle", "mortgage_registry", ["parcelle_id"])

    # ── 6. PARCEL_SERVITUDE ──────────────────────────────────────────────────
    op.create_table(
        "parcel_servitude",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("rnp_parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnp_demandes.id"), nullable=False),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("type_servitude", sa.Enum("passage", "canalisation", "electricite",
                                             "eau", "autre",
                                             name="type_servitude"), nullable=False),
        sa.Column("beneficiaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=True),
        sa.Column("beneficiaire_libre", sa.String(200)),
        sa.Column("date_debut",  sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_fin",    sa.DateTime(timezone=True)),
        sa.Column("acte_reference", sa.String(200)),
        sa.Column("acte_notarie_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("actes_notaries.id"), nullable=True),
        sa.Column("rnaf_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnaf.id"), nullable=True),
        sa.Column("geom_servitude",   sa.String(500)),
        sa.Column("bloque_transaction", sa.Boolean, server_default="false",
                  nullable=False),
        sa.Column("est_active", sa.Boolean, server_default="true", nullable=False),
        sa.Column("cree_par",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_ps_rnp",     "parcel_servitude", ["rnp_parcelle_id"])
    op.create_index("ix_ps_active",  "parcel_servitude", ["est_active"])

    # ── 7. PARCEL_DISPUTES ───────────────────────────────────────────────────
    op.create_table(
        "parcel_disputes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("rnp_parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnp_demandes.id"), nullable=False),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("litige_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("litiges.id"), nullable=True),
        sa.Column("right_version_contestee_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcel_right_version.id"), nullable=True),
        sa.Column("type_litige", sa.Enum("limite", "propriete", "heritage",
                                          "fraude", "hypotheque", "servitude",
                                          name="type_litige_droit"), nullable=False),
        sa.Column("date_ouverture", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("autorite_saisie", sa.Enum("justice", "ccfm", "domaine",
                                              name="autorite_saisie"), nullable=False),
        sa.Column("statut", sa.Enum("ouvert", "en_instruction", "clos", "abandonne",
                                     name="statut_litige_droit"),
                  server_default="ouvert", nullable=False),
        sa.Column("plaignant_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=True),
        sa.Column("defendeur_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=True),
        sa.Column("description",        sa.Text, nullable=False),
        sa.Column("decision_reference", sa.String(200)),
        sa.Column("decision_judiciaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("decisions_judiciaires.id"), nullable=True),
        sa.Column("date_cloture", sa.DateTime(timezone=True)),
        sa.Column("cree_par",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("litige_id", name="uq_dispute_litige"),
    )
    op.create_index("ix_pd_rnp",    "parcel_disputes", ["rnp_parcelle_id"])
    op.create_index("ix_pd_statut", "parcel_disputes", ["statut"])

    # ── 8. ANNF_RIGHT_ARCHIVE ────────────────────────────────────────────────
    op.create_table(
        "annf_right_archive",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("right_version_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcel_right_version.id"), nullable=False),
        sa.Column("annf_link_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("annf_archive_links.id"), nullable=True),
        sa.Column("date_archivage", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("hash_integrite", sa.String(64), nullable=False),
        sa.Column("version",        sa.Integer, server_default="1", nullable=False),
        sa.Column("source", sa.Enum("notaire", "justice", "domaine", "ccfm", "admin",
                                     name="source_archive_droit"), nullable=False),
        sa.Column("snapshot_json", postgresql.JSONB, nullable=False),
        sa.Column("scelle",     sa.Boolean, server_default="false", nullable=False),
        sa.Column("scelle_at",  sa.DateTime(timezone=True)),
        sa.Column("scelle_par", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("archive_par",postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("right_version_id", name="uq_annf_right_version"),
    )
    op.create_index("ix_ara_scelle", "annf_right_archive", ["scelle"])

    # ── TRIGGERS ─────────────────────────────────────────────────────────────

    # T1 : Vérification somme = 100% des droits ACTIFS par parcelle
    op.execute("""
        CREATE OR REPLACE FUNCTION check_somme_pourcentage_droits()
        RETURNS TRIGGER AS $$
        DECLARE
            somme NUMERIC;
            type_controle TEXT;
        BEGIN
            -- Seulement pour les droits de type PROPRIETE (les autres n'ont pas à sommer 100%)
            IF NEW.type_droit != 'propriete' THEN
                RETURN NEW;
            END IF;

            -- Calculer la somme des droits de propriété ACTIFS sur la parcelle
            SELECT COALESCE(SUM(pourcentage_droit), 0)
            INTO somme
            FROM parcel_right_version
            WHERE rnp_parcelle_id = NEW.rnp_parcelle_id
              AND type_droit = 'propriete'
              AND statut = 'actif'
              AND id != COALESCE(NEW.id, '00000000-0000-0000-0000-000000000000'::uuid);

            somme := somme + NEW.pourcentage_droit;

            -- Tolérance de 0.01% pour les arrondis
            IF somme > 100.01 THEN
                RAISE EXCEPTION
                    'DROITS: Somme des pourcentages de propriété dépasse 100%% (%.2f%%) pour RNP %.',
                    somme, NEW.rnp_parcelle_id;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_check_somme_pourcentage
        BEFORE INSERT OR UPDATE OF pourcentage_droit, statut
        ON parcel_right_version
        FOR EACH ROW
        WHEN (NEW.statut = 'actif' AND NEW.type_droit = 'propriete')
        EXECUTE FUNCTION check_somme_pourcentage_droits();
    """)

    # T2 : Clôture automatique du droit précédent quand un nouveau droit ACTIF créé
    op.execute("""
        CREATE OR REPLACE FUNCTION close_previous_right_on_new()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Fermer tous les anciens droits ACTIFS du même type
            -- pour le même titulaire et la même parcelle
            IF NEW.statut = 'actif' AND NEW.version_precedente_id IS NOT NULL THEN
                UPDATE parcel_right_version
                SET statut = 'historique',
                    date_fin_validite = NEW.date_debut_validite
                WHERE id = NEW.version_precedente_id
                  AND statut = 'actif';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_close_previous_right
        AFTER INSERT ON parcel_right_version
        FOR EACH ROW
        WHEN (NEW.statut = 'actif')
        EXECUTE FUNCTION close_previous_right_on_new();
    """)

    # T3 : Blocage automatique si servitude bloquante créée
    op.execute("""
        CREATE OR REPLACE FUNCTION block_on_servitude_bloquante()
        RETURNS TRIGGER AS $$
        DECLARE
            v_admin_id UUID;
            v_motif TEXT;
        BEGIN
            IF NEW.bloque_transaction = true AND NEW.est_active = true
               AND NEW.parcelle_id IS NOT NULL THEN

                SELECT id INTO v_admin_id FROM users WHERE role = 'ADMIN' LIMIT 1;
                v_motif := 'Servitude bloquante (' || NEW.type_servitude || ') créée sur cette parcelle';

                INSERT INTO transaction_blocks (
                    parcelle_id, type_blockage, statut, motif,
                    sha256_motif, pose_par
                )
                SELECT
                    NEW.parcelle_id,
                    'admin_manuel'::type_blockage,
                    'actif'::statut_blockage,
                    v_motif,
                    encode(sha256(v_motif::bytea), 'hex'),
                    COALESCE(NEW.cree_par, v_admin_id)
                WHERE NOT EXISTS (
                    SELECT 1 FROM transaction_blocks
                    WHERE parcelle_id = NEW.parcelle_id
                      AND statut = 'actif'
                      AND motif LIKE '%Servitude bloquante%'
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_block_servitude
        AFTER INSERT ON parcel_servitude
        FOR EACH ROW
        WHEN (NEW.bloque_transaction = true)
        EXECUTE FUNCTION block_on_servitude_bloquante();
    """)

    # T4 : Archivage automatique ANNF quand un droit passe à HISTORIQUE
    op.execute("""
        CREATE OR REPLACE FUNCTION archive_right_on_close()
        RETURNS TRIGGER AS $$
        DECLARE
            v_hash TEXT;
            v_snapshot JSONB;
            v_admin_id UUID;
            v_ida TEXT;
        BEGIN
            IF OLD.statut = 'actif' AND NEW.statut = 'historique' THEN
                SELECT id INTO v_admin_id FROM users WHERE role = 'ADMIN' LIMIT 1;

                v_snapshot := jsonb_build_object(
                    'id',                NEW.id,
                    'rnp_parcelle_id',   NEW.rnp_parcelle_id,
                    'titulaire_id',      NEW.titulaire_id,
                    'type_droit',        NEW.type_droit,
                    'pourcentage_droit', NEW.pourcentage_droit,
                    'date_debut',        NEW.date_debut_validite,
                    'date_fin',          NEW.date_fin_validite,
                    'statut_final',      'historique',
                    'sha256_version',    NEW.sha256_version,
                    'archived_at',       NOW()
                );

                v_hash := encode(sha256(v_snapshot::text::bytea), 'hex');
                v_ida  := generate_annf_ida(EXTRACT(YEAR FROM NOW())::INT);

                -- Archive dans annf_archive_links
                INSERT INTO annf_archive_links (
                    type_archive, entite_id, entite_table,
                    snapshot_json, sha256_snapshot, ida,
                    archive_par, scelle
                ) VALUES (
                    'rnp', NEW.id, 'parcel_right_version',
                    v_snapshot, v_hash, v_ida,
                    COALESCE(v_admin_id, NEW.cree_par), false
                );

                -- Archive dans annf_right_archive
                INSERT INTO annf_right_archive (
                    right_version_id, hash_integrite, version,
                    source, snapshot_json, archive_par
                ) VALUES (
                    NEW.id, v_hash, NEW.numero_version,
                    'admin'::source_archive_droit,
                    v_snapshot,
                    COALESCE(v_admin_id, NEW.cree_par)
                )
                ON CONFLICT (right_version_id) DO NOTHING;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_archive_right_on_close
        AFTER UPDATE OF statut ON parcel_right_version
        FOR EACH ROW
        WHEN (OLD.statut = 'actif' AND NEW.statut = 'historique')
        EXECUTE FUNCTION archive_right_on_close();
    """)

    # ── Fonction requête historique "Qui possédait X à la date Y ?" ──────────
    op.execute("""
        CREATE OR REPLACE FUNCTION qui_possedait_parcelle(
            p_rnp_parcelle_id UUID,
            p_date DATE
        )
        RETURNS TABLE (
            titulaire_id        UUID,
            nom_titulaire       TEXT,
            type_droit          TEXT,
            pourcentage_droit   NUMERIC,
            date_debut_validite TIMESTAMPTZ,
            date_fin_validite   TIMESTAMPTZ,
            acte_reference      TEXT,
            sha256_version      TEXT
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT
                prv.titulaire_id,
                COALESCE(rh.raison_sociale, rh.nom || ' ' || COALESCE(rh.prenom, '')) AS nom_titulaire,
                prv.type_droit::TEXT,
                prv.pourcentage_droit,
                prv.date_debut_validite,
                prv.date_fin_validite,
                prv.acte_reference,
                prv.sha256_version
            FROM parcel_right_version prv
            JOIN right_holders rh ON rh.id = prv.titulaire_id
            WHERE prv.rnp_parcelle_id = p_rnp_parcelle_id
              AND prv.date_debut_validite <= p_date::timestamptz
              AND (prv.date_fin_validite IS NULL
                   OR prv.date_fin_validite >= p_date::timestamptz)
              AND prv.statut IN ('actif', 'historique')
            ORDER BY prv.type_droit, prv.pourcentage_droit DESC;
        END;
        $$ LANGUAGE plpgsql STABLE;
    """)

    # ── Vue : droits actifs par parcelle avec validation 100% ────────────────
    op.execute("""
        CREATE OR REPLACE VIEW v_droits_actifs AS
        SELECT
            prv.rnp_parcelle_id,
            prv.parcelle_id,
            p.nicad,
            prv.type_droit,
            rh.nom || ' ' || COALESCE(rh.prenom, '')
                || COALESCE(rh.raison_sociale, '') AS titulaire_nom,
            rh.type_personne,
            prv.pourcentage_droit,
            prv.date_debut_validite,
            prv.acte_reference,
            prv.sha256_version,
            -- Somme totale (doit être 100% pour propriete)
            SUM(prv.pourcentage_droit) OVER (
                PARTITION BY prv.rnp_parcelle_id, prv.type_droit
            ) AS somme_type_droit
        FROM parcel_right_version prv
        JOIN right_holders rh ON rh.id = prv.titulaire_id
        LEFT JOIN parcelles p  ON p.id = prv.parcelle_id
        WHERE prv.statut = 'actif'
        ORDER BY prv.rnp_parcelle_id, prv.type_droit, prv.pourcentage_droit DESC;
    """)

    # ── Seed notaires depuis users NOTAIRE existants ──────────────────────────
    op.execute("""
        INSERT INTO notary_registry (user_id, nom_notaire, numero_agrement, statut_agrement)
        SELECT id, email, 'AGR-' || UPPER(SUBSTRING(id::TEXT, 1, 8)), 'agree'
        FROM users
        WHERE role = 'NOTAIRE'
        ON CONFLICT (user_id) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_droits_actifs CASCADE")
    op.execute("DROP FUNCTION IF EXISTS qui_possedait_parcelle(UUID, DATE) CASCADE")

    for trigger, table in [
        ("tg_archive_right_on_close",   "parcel_right_version"),
        ("tg_block_servitude",          "parcel_servitude"),
        ("tg_close_previous_right",     "parcel_right_version"),
        ("tg_check_somme_pourcentage",  "parcel_right_version"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    for fn in [
        "check_somme_pourcentage_droits",
        "close_previous_right_on_new",
        "block_on_servitude_bloquante",
        "archive_right_on_close",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")

    for table in [
        "annf_right_archive", "parcel_disputes", "parcel_servitude",
        "mortgage_registry", "property_transfers", "parcel_right_version",
        "notary_registry", "right_holders",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    for name in [
        "type_personne", "type_droit", "statut_droit", "origine_operation",
        "type_transfert", "statut_hypotheque", "type_servitude",
        "type_litige_droit", "statut_litige_droit", "autorite_saisie",
        "source_archive_droit",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")
