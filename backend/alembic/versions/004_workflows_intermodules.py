"""004_workflows_intermodules

FONCIER+ v3.4.7 — Migration 004 : Workflows Inter-modules
Aligné sur les tables existantes (migrations 001, 002, 003).

Nouvelles tables (jamais dupliquées) :
  rnaf_workflow          → enrichit `rnaf` existant
  rnp_parcelle_link      → enrichit `rnp_demandes` existant
  bgu_parcel_status      → enrichit `assiettes_bgu` existant
  ccfm_suspension        → enrichit `demandes_ccfm` existant
  parcel_freeze_events   → enrichit `litiges` existant + `parcelles` (003)
  transaction_blocks     → gate automatique pour actes_notaries + hypotheques + dossiers_domaine
  justice_rnaf_decisions → enrichit `decisions_judiciaires` existant
  annf_archive_links     → archivage WORM ANNF
  parcel_public_history  → historique public statuts

Triggers PL/pgSQL :
  1. sync_rnp_from_rnaf       — synchronise statut RNP quand RNAF change
  2. auto_block_on_suspension — crée transaction_block quand ccfm_suspension créée
  3. auto_unblock_on_decision — lève blocks quand décision Justice VALIDEE
  4. record_public_history    — enregistre chaque transition publique parcelle

Revision ID: 004_workflows_intermodules
Revises: 003_parcellaire_antifraude
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004_workflows_intermodules"
down_revision = "003_parcellaire_antifraude"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ── ENUMs ────────────────────────────────────────────────────────────────
    for name, values in [
        ("statut_rnaf",
         "('brouillon','en_verification','en_scellement','scelle','publie',"
         "'suspendu','annule','archive')"),
        ("statut_rnp",
         "('provisoire','actif','suspendu','annule','archive')"),
        ("type_blockage",
         "('ccfm_suspension','justice_litige','fraude_detectee','rnaf_suspendu','admin_manuel')"),
        ("statut_blockage",
         "('actif','leve','expire')"),
        ("statut_decision_justice_rnaf",
         "('en_instruction','validee','annulation','classee')"),
        ("type_archive_annf",
         "('rnaf','rnp','ccfm','acte_notarie','decision_justice','bgu_scelle','plan_cadastral')"),
    ]:
        op.execute(f"""
            DO $$ BEGIN
                CREATE TYPE {name} AS ENUM {values};
                EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)

    # ── 1. RNAF_WORKFLOW ─────────────────────────────────────────────────────
    op.create_table(
        "rnaf_workflow",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("rnaf_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnaf.id"), nullable=False),
        sa.Column("arrete_foncier_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("arretes_fonciers.id"), nullable=False),
        sa.Column("publication_jo_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("publications_jo.id"), nullable=True),
        sa.Column("statut", sa.Enum("brouillon", "en_verification", "en_scellement",
                                     "scelle", "publie", "suspendu", "annule", "archive",
                                     name="statut_rnaf"),
                  server_default="brouillon", nullable=False),
        sa.Column("date_soumission",   sa.DateTime(timezone=True)),
        sa.Column("date_verification", sa.DateTime(timezone=True)),
        sa.Column("date_scellement",   sa.DateTime(timezone=True)),
        sa.Column("date_publication",  sa.DateTime(timezone=True)),
        sa.Column("date_suspension",   sa.DateTime(timezone=True)),
        sa.Column("date_annulation",   sa.DateTime(timezone=True)),
        sa.Column("motif_suspension",  sa.Text),
        sa.Column("motif_annulation",  sa.Text),
        sa.Column("sha256_publie",     sa.String(64)),
        sa.Column("soumis_par",  postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("verifie_par", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("scelle_par",  postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("publie_par",  postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("suspendu_par",postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("rnaf_id", name="uq_rnaf_workflow_rnaf"),
    )
    op.create_index("ix_rnaf_workflow_statut", "rnaf_workflow", ["statut"])
    op.create_index("ix_rnaf_workflow_arrete", "rnaf_workflow", ["arrete_foncier_id"])

    # ── 2. RNP_PARCELLE_LINK ─────────────────────────────────────────────────
    op.create_table(
        "rnp_parcelle_link",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("rnp_demande_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnp_demandes.id"), nullable=False),
        sa.Column("rnaf_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnaf.id"), nullable=False),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("statut_rnp", sa.Enum("provisoire", "actif", "suspendu",
                                         "annule", "archive", name="statut_rnp"),
                  server_default="provisoire", nullable=False),
        sa.Column("statut_rnaf_au_moment", sa.Enum("brouillon", "en_verification",
                                                     "en_scellement", "scelle", "publie",
                                                     "suspendu", "annule", "archive",
                                                     name="statut_rnaf")),
        sa.Column("derniere_sync_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("rnp_demande_id", name="uq_rnp_link_demande"),
        # Contrainte : RNP actif nécessite RNAF publié
        sa.CheckConstraint(
            "statut_rnp != 'actif' OR statut_rnaf_au_moment = 'publie'",
            name="ck_rnp_actif_necessite_rnaf_publie",
        ),
    )
    op.create_index("ix_rnp_link_rnaf",   "rnp_parcelle_link", ["rnaf_id"])
    op.create_index("ix_rnp_link_statut", "rnp_parcelle_link", ["statut_rnp"])

    # ── 3. BGU_PARCEL_STATUS ─────────────────────────────────────────────────
    op.create_table(
        "bgu_parcel_status",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("assiette_bgu_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("assiettes_bgu.id"), nullable=False),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("statut_public", sa.String(30), server_default="actif", nullable=False),
        sa.Column("statut_detail", sa.Text),
        sa.Column("source_rnaf",    sa.Boolean, server_default="false"),
        sa.Column("source_ccfm",    sa.Boolean, server_default="false"),
        sa.Column("source_justice", sa.Boolean, server_default="false"),
        sa.Column("source_fraude",  sa.Boolean, server_default="false"),
        sa.Column("derniere_maj_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("maj_par", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("qr_code_url", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("assiette_bgu_id", name="uq_bgu_status_assiette"),
    )
    op.create_index("ix_bgu_status_public", "bgu_parcel_status", ["statut_public"])

    # ── 4. CCFM_SUSPENSION ───────────────────────────────────────────────────
    op.create_table(
        "ccfm_suspension",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("demande_ccfm_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=False),
        sa.Column("rnaf_workflow_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnaf_workflow.id"), nullable=True),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("motif_suspension", sa.Text, nullable=False),
        sa.Column("base_juridique",   sa.String(200)),
        sa.Column("sha256_preuve",    sa.String(64), nullable=False),
        sa.Column("emise_par",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("validee_par", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("est_active",  sa.Boolean, server_default="true", nullable=False),
        sa.Column("levee_par",   postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("levee_at",    sa.DateTime(timezone=True)),
        sa.Column("motif_levee", sa.Text),
        sa.Column("justice_decision_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("decisions_judiciaires.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_ccfm_susp_active",  "ccfm_suspension", ["est_active"])
    op.create_index("ix_ccfm_susp_demande", "ccfm_suspension", ["demande_ccfm_id"])

    # ── 5. PARCEL_FREEZE_EVENTS ──────────────────────────────────────────────
    op.create_table(
        "parcel_freeze_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("type_gel", sa.String(30), nullable=False),
        sa.Column("litige_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("litiges.id"), nullable=True),
        sa.Column("suspension_ccfm_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ccfm_suspension.id"), nullable=True),
        sa.Column("gele",     sa.Boolean, nullable=False),
        sa.Column("motif",    sa.Text, nullable=False),
        sa.Column("gele_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("gele_at",  sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("degele_par", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("degele_at",  sa.DateTime(timezone=True)),
    )
    op.create_index("ix_freeze_parcelle", "parcel_freeze_events", ["parcelle_id"])
    op.create_index("ix_freeze_gele",     "parcel_freeze_events", ["gele"])

    # ── 6. TRANSACTION_BLOCKS ────────────────────────────────────────────────
    op.create_table(
        "transaction_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("type_blockage", sa.Enum(
                  "ccfm_suspension", "justice_litige", "fraude_detectee",
                  "rnaf_suspendu", "admin_manuel", name="type_blockage"),
                  nullable=False),
        sa.Column("statut", sa.Enum("actif", "leve", "expire", name="statut_blockage"),
                  server_default="actif", nullable=False),
        sa.Column("motif",        sa.Text, nullable=False),
        sa.Column("sha256_motif", sa.String(64)),
        sa.Column("source_litige_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("litiges.id"), nullable=True),
        sa.Column("source_ccfm_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ccfm_suspension.id"), nullable=True),
        sa.Column("source_conflit_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conflits_parcellaires.id"), nullable=True),
        sa.Column("source_rnaf_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnaf_workflow.id"), nullable=True),
        sa.Column("pose_par",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("pose_at",    sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("leve_par",   postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("leve_at",    sa.DateTime(timezone=True)),
        sa.Column("motif_levee",sa.Text),
        sa.Column("expire_at",  sa.DateTime(timezone=True)),
    )
    op.create_index("ix_txblock_parcelle", "transaction_blocks", ["parcelle_id"])
    op.create_index("ix_txblock_statut",   "transaction_blocks", ["statut"])
    op.create_index("ix_txblock_type",     "transaction_blocks", ["type_blockage"])

    # ── 7. JUSTICE_RNAF_DECISIONS ────────────────────────────────────────────
    op.create_table(
        "justice_rnaf_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("decision_judiciaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("decisions_judiciaires.id"), nullable=False),
        sa.Column("rnaf_workflow_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnaf_workflow.id"), nullable=False),
        sa.Column("ccfm_suspension_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ccfm_suspension.id"), nullable=True),
        sa.Column("statut_decision", sa.Enum(
                  "en_instruction", "validee", "annulation", "classee",
                  name="statut_decision_justice_rnaf"),
                  server_default="en_instruction", nullable=False),
        sa.Column("numero_decision", sa.String(100)),
        sa.Column("date_decision",   sa.DateTime(timezone=True)),
        sa.Column("motif_decision",  sa.Text, nullable=False),
        sa.Column("sha256_decision", sa.String(64), nullable=False),
        sa.Column("retablit_rnaf",    sa.Boolean, server_default="false"),
        sa.Column("annule_rnaf",      sa.Boolean, server_default="false"),
        sa.Column("degele_parcelles", sa.Boolean, server_default="false"),
        sa.Column("president_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("greffier_id",  postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("notifie_at",   sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("decision_judiciaire_id", name="uq_justice_rnaf_dec"),
        sa.UniqueConstraint("numero_decision", name="uq_justice_rnaf_numero"),
    )
    op.create_index("ix_jrd_statut",  "justice_rnaf_decisions", ["statut_decision"])
    op.create_index("ix_jrd_rnaf",    "justice_rnaf_decisions", ["rnaf_workflow_id"])

    # ── 8. ANNF_ARCHIVE_LINKS ────────────────────────────────────────────────
    op.create_table(
        "annf_archive_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("type_archive", sa.Enum(
                  "rnaf", "rnp", "ccfm", "acte_notarie",
                  "decision_justice", "bgu_scelle", "plan_cadastral",
                  name="type_archive_annf"), nullable=False),
        sa.Column("entite_id",    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entite_table", sa.String(100), nullable=False),
        sa.Column("snapshot_json",   postgresql.JSONB, nullable=False),
        sa.Column("sha256_snapshot", sa.String(64), nullable=False),
        sa.Column("ida", sa.String(50), nullable=False),
        sa.Column("scelle",     sa.Boolean, server_default="false", nullable=False),
        sa.Column("scelle_at",  sa.DateTime(timezone=True)),
        sa.Column("scelle_par", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("version", sa.Integer, server_default="1", nullable=False),
        sa.Column("version_precedente_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("annf_archive_links.id"), nullable=True),
        sa.Column("archive_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("ida", name="uq_annf_ida"),
        sa.UniqueConstraint("entite_id", "entite_table", "version",
                             name="uq_annf_entite_version"),
    )
    op.create_index("ix_annf_type",     "annf_archive_links", ["type_archive"])
    op.create_index("ix_annf_entite",   "annf_archive_links", ["entite_id"])
    op.create_index("ix_annf_scelle",   "annf_archive_links", ["scelle"])

    # Séquence IDA
    op.execute("""
        CREATE SEQUENCE IF NOT EXISTS annf_ida_seq
        START WITH 1 INCREMENT BY 1 NO CYCLE;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION generate_annf_ida(annee INT DEFAULT EXTRACT(YEAR FROM NOW())::INT)
        RETURNS TEXT AS $$
        BEGIN
            RETURN 'ANNF-' || annee || '-' || LPAD(nextval('annf_ida_seq')::TEXT, 7, '0');
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── 9. PARCEL_PUBLIC_HISTORY ─────────────────────────────────────────────
    op.create_table(
        "parcel_public_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcelle_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("nicad",         sa.String(30), nullable=False),
        sa.Column("statut_avant",  sa.String(50)),
        sa.Column("statut_apres",  sa.String(50), nullable=False),
        sa.Column("module_source", sa.String(30), nullable=False),
        sa.Column("entite_id",     postgresql.UUID(as_uuid=True)),
        sa.Column("motif_public",  sa.Text),
        sa.Column("role_acteur",   sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), index=True),
    )
    op.create_index("ix_pph_parcelle", "parcel_public_history", ["parcelle_id"])
    op.create_index("ix_pph_nicad",    "parcel_public_history", ["nicad"])

    # ── TRIGGERS ─────────────────────────────────────────────────────────────

    # T1 : Sync RNP depuis RNAF — quand le statut RNAF change
    op.execute("""
        CREATE OR REPLACE FUNCTION sync_rnp_from_rnaf()
        RETURNS TRIGGER AS $$
        DECLARE
            nouveau_statut_rnp TEXT;
        BEGIN
            -- Mapping RNAF → RNP
            nouveau_statut_rnp := CASE NEW.statut
                WHEN 'publie'   THEN 'actif'
                WHEN 'suspendu' THEN 'suspendu'
                WHEN 'annule'   THEN 'annule'
                WHEN 'archive'  THEN 'archive'
                WHEN 'scelle'   THEN 'provisoire'
                ELSE 'provisoire'
            END;

            -- Mettre à jour tous les RNP liés à ce RNAF
            UPDATE rnp_parcelle_link
            SET statut_rnp = nouveau_statut_rnp::statut_rnp,
                statut_rnaf_au_moment = NEW.statut,
                derniere_sync_at = NOW(),
                updated_at = NOW()
            WHERE rnaf_id = NEW.rnaf_id;

            -- Mettre à jour le statut public BGU si suspendu ou annulé
            IF NEW.statut IN ('suspendu', 'annule') THEN
                UPDATE bgu_parcel_status bps
                SET statut_public = NEW.statut,
                    source_rnaf = true,
                    derniere_maj_at = NOW()
                FROM rnp_parcelle_link rpl
                WHERE rpl.rnaf_id = NEW.rnaf_id
                  AND bps.parcelle_id = rpl.parcelle_id;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_sync_rnp_from_rnaf
        AFTER UPDATE OF statut ON rnaf_workflow
        FOR EACH ROW
        WHEN (OLD.statut IS DISTINCT FROM NEW.statut)
        EXECUTE FUNCTION sync_rnp_from_rnaf();
    """)

    # T2 : Blocage automatique quand suspension CCFM créée
    op.execute("""
        CREATE OR REPLACE FUNCTION auto_block_on_ccfm_suspension()
        RETURNS TRIGGER AS $$
        DECLARE
            v_motif TEXT;
            v_system_user UUID;
        BEGIN
            IF NEW.est_active = true THEN
                -- Récupérer un utilisateur système (ADMIN) pour pose_par
                SELECT id INTO v_system_user FROM users WHERE role = 'ADMIN' LIMIT 1;
                v_motif := 'Blocage automatique — Suspension CCFM: ' || LEFT(NEW.motif_suspension, 200);

                INSERT INTO transaction_blocks (
                    parcelle_id, type_blockage, statut, motif,
                    sha256_motif, source_ccfm_id, pose_par
                )
                SELECT
                    NEW.parcelle_id,
                    'ccfm_suspension'::type_blockage,
                    'actif'::statut_blockage,
                    v_motif,
                    encode(sha256(v_motif::bytea), 'hex'),
                    NEW.id,
                    COALESCE(NEW.emise_par, v_system_user)
                WHERE NEW.parcelle_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM transaction_blocks
                    WHERE parcelle_id = NEW.parcelle_id
                      AND source_ccfm_id = NEW.id
                      AND statut = 'actif'
                  );

                -- Marquer parcelle comme gelée
                IF NEW.parcelle_id IS NOT NULL THEN
                    UPDATE parcelles
                    SET is_gele = true, updated_at = NOW()
                    WHERE id = NEW.parcelle_id;

                    -- Enregistrer événement de gel
                    INSERT INTO parcel_freeze_events (
                        parcelle_id, type_gel, suspension_ccfm_id,
                        gele, motif, gele_par
                    ) VALUES (
                        NEW.parcelle_id, 'ccfm', NEW.id,
                        true, v_motif, NEW.emise_par
                    );
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_auto_block_ccfm_suspension
        AFTER INSERT ON ccfm_suspension
        FOR EACH ROW EXECUTE FUNCTION auto_block_on_ccfm_suspension();
    """)

    # T3 : Dégel automatique quand décision Justice = VALIDEE
    op.execute("""
        CREATE OR REPLACE FUNCTION auto_unblock_on_justice_decision()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.statut_decision = 'validee' AND NEW.retablit_rnaf = true THEN
                -- Lever tous les blocks liés au RNAF
                UPDATE transaction_blocks
                SET statut = 'leve'::statut_blockage,
                    leve_at = NOW(),
                    motif_levee = 'Levée automatique — Décision Justice: ' || COALESCE(NEW.numero_decision, NEW.id::TEXT)
                WHERE source_rnaf_id = NEW.rnaf_workflow_id
                  AND statut = 'actif';

                -- Lever les suspensions CCFM liées
                UPDATE ccfm_suspension
                SET est_active = false,
                    levee_at = NOW(),
                    justice_decision_id = NEW.decision_judiciaire_id,
                    motif_levee = 'Levée par décision judiciaire'
                WHERE rnaf_workflow_id = NEW.rnaf_workflow_id
                  AND est_active = true;

                -- Rétablir statut RNAF à PUBLIE
                UPDATE rnaf_workflow
                SET statut = 'publie'::statut_rnaf,
                    updated_at = NOW()
                WHERE id = NEW.rnaf_workflow_id
                  AND statut = 'suspendu';
            END IF;

            IF NEW.statut_decision = 'annulation' AND NEW.annule_rnaf = true THEN
                -- Annuler le RNAF définitivement
                UPDATE rnaf_workflow
                SET statut = 'annule'::statut_rnaf,
                    date_annulation = NOW(),
                    updated_at = NOW()
                WHERE id = NEW.rnaf_workflow_id;

                -- Blocage permanent
                UPDATE transaction_blocks
                SET statut = 'actif'::statut_blockage,
                    motif_levee = NULL
                WHERE source_rnaf_id = NEW.rnaf_workflow_id;
            END IF;

            IF NEW.degele_parcelles = true THEN
                -- Dégeler toutes les parcelles liées au RNAF
                UPDATE parcelles p
                SET is_gele = false, updated_at = NOW()
                FROM rnp_parcelle_link rpl
                WHERE rpl.rnaf_id = (SELECT rnaf_id FROM rnaf_workflow WHERE id = NEW.rnaf_workflow_id)
                  AND p.id = rpl.parcelle_id;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_unblock_justice_decision
        AFTER INSERT OR UPDATE OF statut_decision ON justice_rnaf_decisions
        FOR EACH ROW
        WHEN (NEW.statut_decision IN ('validee', 'annulation'))
        EXECUTE FUNCTION auto_unblock_on_justice_decision();
    """)

    # T4 : Historique public automatique quand statut parcelle change
    op.execute("""
        CREATE OR REPLACE FUNCTION record_public_history()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.statut IS DISTINCT FROM NEW.statut THEN
                INSERT INTO parcel_public_history (
                    parcelle_id, nicad, statut_avant, statut_apres,
                    module_source, motif_public, role_acteur
                ) VALUES (
                    NEW.id, NEW.nicad,
                    OLD.statut::TEXT, NEW.statut::TEXT,
                    'cadastre',
                    'Changement de statut: ' || OLD.statut::TEXT || ' → ' || NEW.statut::TEXT,
                    'SYSTEME'
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_record_public_history
        AFTER UPDATE OF statut ON parcelles
        FOR EACH ROW
        WHEN (OLD.statut IS DISTINCT FROM NEW.statut)
        EXECUTE FUNCTION record_public_history();
    """)

    # ── Vue publique portail citoyen ─────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE VIEW v_portail_parcelles AS
        SELECT
            p.nicad,
            p.statut::TEXT                          AS statut_parcelle,
            COALESCE(bps.statut_public, p.statut::TEXT) AS statut_public,
            bps.statut_detail,
            p.is_gele                               AS est_gele,
            rpl.statut_rnp::TEXT                    AS statut_rnp,
            bps.derniere_maj_at,
            bps.qr_code_url,
            p.surface_m2
        FROM parcelles p
        LEFT JOIN bgu_parcel_status  bps ON bps.parcelle_id = p.id
        LEFT JOIN rnp_parcelle_link  rpl ON rpl.parcelle_id = p.id
        WHERE p.statut NOT IN ('annule', 'archive')
        ORDER BY p.nicad;
    """)

    # ── Vue gate transactionnelle (utilisée par Notaire + Banque) ────────────
    op.execute("""
        CREATE OR REPLACE VIEW v_transaction_gate AS
        SELECT
            p.id            AS parcelle_id,
            p.nicad,
            p.statut::TEXT  AS statut_parcelle,
            p.is_gele,
            COALESCE(
                (SELECT COUNT(*) FROM transaction_blocks tb
                 WHERE tb.parcelle_id = p.id AND tb.statut = 'actif') > 0,
                false
            )               AS est_bloque,
            (SELECT COUNT(*) FROM ccfm_suspension cs
             WHERE cs.parcelle_id = p.id AND cs.est_active = true
            )               AS nb_suspensions_actives,
            (SELECT COUNT(*) FROM conflits_parcellaires cp
             WHERE cp.parcelle_a_id = p.id AND cp.gravite = 'critique'
               AND cp.statut IN ('ouvert', 'bloque')
            )               AS nb_conflits_critiques,
            -- Eligibilité globale
            (
                p.statut = 'active'
                AND p.is_gele = false
                AND NOT EXISTS (SELECT 1 FROM transaction_blocks tb
                                WHERE tb.parcelle_id = p.id AND tb.statut = 'actif')
                AND NOT EXISTS (SELECT 1 FROM ccfm_suspension cs
                                WHERE cs.parcelle_id = p.id AND cs.est_active = true)
                AND NOT EXISTS (SELECT 1 FROM conflits_parcellaires cp
                                WHERE cp.parcelle_a_id = p.id AND cp.gravite = 'critique'
                                  AND cp.statut IN ('ouvert', 'bloque'))
            )               AS eligible_transaction
        FROM parcelles p;
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_transaction_gate CASCADE")
    op.execute("DROP VIEW IF EXISTS v_portail_parcelles CASCADE")

    for trigger, table in [
        ("tg_record_public_history",    "parcelles"),
        ("tg_unblock_justice_decision", "justice_rnaf_decisions"),
        ("tg_auto_block_ccfm_suspension","ccfm_suspension"),
        ("tg_sync_rnp_from_rnaf",       "rnaf_workflow"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    for fn in [
        "sync_rnp_from_rnaf",
        "auto_block_on_ccfm_suspension",
        "auto_unblock_on_justice_decision",
        "record_public_history",
        "generate_annf_ida",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")

    op.execute("DROP SEQUENCE IF EXISTS annf_ida_seq")

    for table in [
        "parcel_public_history", "annf_archive_links",
        "justice_rnaf_decisions", "transaction_blocks",
        "parcel_freeze_events", "ccfm_suspension",
        "bgu_parcel_status", "rnp_parcelle_link", "rnaf_workflow",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    for name in [
        "statut_rnaf", "statut_rnp", "type_blockage",
        "statut_blockage", "statut_decision_justice_rnaf", "type_archive_annf",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")
