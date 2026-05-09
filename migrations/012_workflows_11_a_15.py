"""012_workflows_11_a_15

FONCIER+ v3.4.7 — Migration 012 : Workflows 11 à 15
  WF11 : Expropriation pour utilité publique
  WF12 : Régularisation foncière
  WF13 : Gestion des conflits fonciers (complète litiges 001)
  WF14 : Lotissement étendu (complète lotissements 003/008)
  WF15 : Zones spéciales

Tables existantes utilisées (non dupliquées) :
  property_transfers, right_holders, parcel_right_version (005)
  demandes_ccfm, parcelles, rnaf, rnp_demandes (001/003)
  litiges, parcel_disputes, parcel_freeze_events (001/005)
  transaction_blocks, parcel_public_history (004)
  lotissements, lotissement_lot, archive_lotissement (003/008)
  arretes_urbanisme, urbanisme_conformite (003/009)
  zonage_urbain, usage_foncier (011)
  workflow_instance, workflow_step_def (007)
  annf_archive_links (004)

18 tables nouvelles sur cette migration.

Triggers critiques :
  TE1. tg_expropriation_gel_parcelle  — gel automatique à l'ouverture
  TE2. tg_regularisation_anti_double  — bloque double régularisation même parcelle
  TE3. tg_conflit_gel_automatique     — gel à l'ouverture d'un conflit foncier
  TE4. tg_zone_speciale_regle         — applique règles de zone aux nouvelles parcelles
  TE5. tg_lotissement_reserve_pub     — vérifie les réserves publiques obligatoires

Revision ID: 012_workflows_11_a_15
Revises: 011_workflows_6_a_10
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "012_workflows_11_a_15"
down_revision = "011_workflows_6_a_10"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ── ENUMS ─────────────────────────────────────────────────────────────────
    for name, values in [
        ("statut_expropriation",
         "('initiee','evaluation','negociation','decision_officielle',"
         "'indemnisation','transfert','archive','contestee')"),
        ("statut_regularisation",
         "('depot','enquete','commission','validation','arrete','attribution','archive','rejetee')"),
        ("statut_conflit_foncier",
         "('declare','enquete','mediation','decision','resolu','archive')"),
        ("type_conflit_foncier",
         "('limite_parcelle','propriete_disputee','heritage_conteste',"
         "'occupation_illegale','fraude_cadastrale','litige_usage')"),
        ("type_zone_speciale",
         "('industrielle','agricole','miniere','forestiere','protegee',"
         "'militaire','touristique','corridor_ecologique','autre')"),
        ("statut_zone_speciale",
         "('active','suspendue','en_revision','abrogee')"),
    ]:
        op.execute(f"""
            DO $$ BEGIN CREATE TYPE {name} AS ENUM {values};
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """)

    # ══════════════════════════════════════════════════════════════════════════
    # WF11 — EXPROPRIATION POUR UTILITÉ PUBLIQUE
    # ══════════════════════════════════════════════════════════════════════════

    op.create_table(
        "projet_public",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("nom_projet",      sa.String(300), nullable=False),
        sa.Column("type_projet",     sa.String(100), nullable=False,
                  comment="route|barrage|ecole|hopital|infrastructure_autre"),
        sa.Column("autorite_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False,
                  comment="Entité publique maître d'ouvrage"),
        sa.Column("arrete_dut_ref",  sa.String(200), nullable=False,
                  comment="Arrêté Déclaratif d'Utilité Publique"),
        sa.Column("arrete_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("arretes_urbanisme.id"), nullable=True),
        sa.Column("budget_fcfa",     sa.Numeric(20, 2)),
        sa.Column("date_dut",        sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_fin_prevue", sa.DateTime(timezone=True)),
        sa.Column("statut",          sa.String(30), server_default="actif"),
        sa.Column("sha256_dut",      sa.String(64)),
        sa.Column("created_at",      sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_pp_statut", "projet_public", ["statut"])

    op.create_table(
        "expropriation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("projet_id",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projet_public.id"), nullable=False),
        sa.Column("parcelle_id",        postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("proprietaire_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("statut", sa.Enum(
                  "initiee","evaluation","negociation","decision_officielle",
                  "indemnisation","transfert","archive","contestee",
                  name="statut_expropriation"),
                  server_default="initiee", nullable=False),
        sa.Column("surface_expropriee_m2", sa.Numeric(12, 4), nullable=False,
                  comment="Surface réellement expropriée (peut être partielle)"),
        sa.Column("est_partielle",       sa.Boolean, server_default="false"),
        sa.Column("decision_judiciaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("decisions_judiciaires.id"), nullable=True),
        sa.Column("workflow_id",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=True),
        sa.Column("sha256_dossier",      sa.String(64)),
        sa.Column("cree_par",            postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at",          sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",          sa.DateTime(timezone=True)),
    )
    op.create_index("ix_exp_parcelle", "expropriation", ["parcelle_id"])
    op.create_index("ix_exp_statut",   "expropriation", ["statut"])

    op.create_table(
        "evaluation_indemnisation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("expropriation_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("expropriation.id"), nullable=False),
        sa.Column("valeur_officielle_fcfa",  sa.Numeric(18, 2), nullable=False),
        sa.Column("valeur_marche_fcfa",      sa.Numeric(18, 2)),
        sa.Column("indemnite_proposee_fcfa", sa.Numeric(18, 2), nullable=False),
        sa.Column("indemnite_acceptee",      sa.Boolean, server_default="false"),
        sa.Column("motif_refus",             sa.Text),
        sa.Column("expert_evaluateur",       sa.String(200)),
        sa.Column("date_evaluation",         sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("sha256_evaluation",       sa.String(64), nullable=False),
        sa.Column("evalue_par",              postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
    )

    op.create_table(
        "paiement_indemnisation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("expropriation_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("expropriation.id"), nullable=False),
        sa.Column("evaluation_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("evaluation_indemnisation.id"), nullable=False),
        sa.Column("montant_verse_fcfa",  sa.Numeric(18, 2), nullable=False),
        sa.Column("mode_paiement",       sa.String(50), nullable=False,
                  comment="virement|cheque|numéraire"),
        sa.Column("reference_paiement",  sa.String(200), unique=True),
        sa.Column("date_paiement",       sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("sha256_paiement",     sa.String(64), nullable=False),
        sa.Column("verse_par",           postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
    )

    op.create_table(
        "contestation_expropriation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("expropriation_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("expropriation.id"), nullable=False),
        sa.Column("contestataire_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("motif",              sa.Text, nullable=False),
        sa.Column("type_contestation",  sa.String(50), nullable=False,
                  comment="indemnisation_insuffisante|utilite_non_prouvee|procedure_irreguliere"),
        sa.Column("statut",             sa.String(30), server_default="deposee"),
        sa.Column("decision_judiciaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("decisions_judiciaires.id"), nullable=True),
        sa.Column("cree_par",           postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at",         sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_ce_expropriation","contestation_expropriation",["expropriation_id"])

    # ══════════════════════════════════════════════════════════════════════════
    # WF12 — RÉGULARISATION FONCIÈRE
    # ══════════════════════════════════════════════════════════════════════════

    op.create_table(
        "demande_regularisation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("occupant_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("surface_m2",      sa.Numeric(12, 4), nullable=False),
        sa.Column("geom",            sa.String(5000),
                  comment="WKT de la parcelle à régulariser"),
        sa.Column("date_occupation", sa.DateTime(timezone=True),
                  comment="Date de début d'occupation déclarée"),
        sa.Column("justificatifs",   postgresql.JSONB,
                  comment="Liste de documents prouvant l'occupation"),
        sa.Column("statut", sa.Enum(
                  "depot","enquete","commission","validation",
                  "arrete","attribution","archive","rejetee",
                  name="statut_regularisation"),
                  server_default="depot", nullable=False),
        # Contrôles anti-fraude
        sa.Column("occupation_verifiee",  sa.Boolean, server_default="false"),
        sa.Column("pas_double_reg",       sa.Boolean, server_default="false"),
        sa.Column("ccfm_prelim_ok",       sa.Boolean, server_default="false"),
        # Résultat
        sa.Column("parcelle_id",          postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("sha256_dossier",       sa.String(64), nullable=False),
        sa.Column("workflow_id",          postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=True),
        sa.Column("cree_par",             postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at",           sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",           sa.DateTime(timezone=True)),
    )
    op.create_index("ix_dr_statut",   "demande_regularisation", ["statut"])

    op.create_table(
        "enquete_fonciere",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("regularisation_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demande_regularisation.id"), nullable=False),
        sa.Column("enqueteur_id",        postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("date_enquete",        sa.DateTime(timezone=True), nullable=False),
        sa.Column("occupation_confirmee",sa.Boolean, nullable=False),
        sa.Column("conflit_detecte",     sa.Boolean, server_default="false"),
        sa.Column("observations",        sa.Text),
        sa.Column("recommandation",      sa.String(50),
                  comment="valider|rejeter|approfondir"),
        sa.Column("sha256_rapport",      sa.String(64), nullable=False),
        sa.Column("created_at",          sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )

    op.create_table(
        "arrete_regularisation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("regularisation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demande_regularisation.id"), nullable=False, unique=True),
        sa.Column("numero_arrete",     sa.String(100), nullable=False, unique=True),
        sa.Column("arrete_id",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("arretes_urbanisme.id"), nullable=True),
        sa.Column("date_arrete",       sa.DateTime(timezone=True), nullable=False),
        sa.Column("signe_par",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sha256_arrete",     sa.String(64), nullable=False),
    )

    op.create_table(
        "attribution_propriete",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("arrete_reg_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("arrete_regularisation.id"), nullable=False, unique=True),
        sa.Column("beneficiaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("parcelle_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("droit_id",        postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcel_right_version.id"), nullable=True),
        sa.Column("ccfm_id",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=True),
        sa.Column("date_attribution",sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("sha256_titre",    sa.String(64), nullable=False),
        sa.Column("attribue_par",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
    )
    op.create_index("ix_ap_beneficiaire","attribution_propriete",["beneficiaire_id"])

    # ══════════════════════════════════════════════════════════════════════════
    # WF13 — GESTION DES CONFLITS FONCIERS
    # ══════════════════════════════════════════════════════════════════════════

    op.create_table(
        "conflit_foncier",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("type_conflit", sa.Enum(
                  "limite_parcelle","propriete_disputee","heritage_conteste",
                  "occupation_illegale","fraude_cadastrale","litige_usage",
                  name="type_conflit_foncier"), nullable=False),
        sa.Column("statut", sa.Enum(
                  "declare","enquete","mediation","decision","resolu","archive",
                  name="statut_conflit_foncier"),
                  server_default="declare", nullable=False),
        sa.Column("description",    sa.Text, nullable=False),
        sa.Column("litige_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("litiges.id"), nullable=True,
                  comment="Lien vers litiges existant (001)"),
        sa.Column("autorite_saisie",sa.String(50), server_default="ccfm",
                  comment="ccfm|justice|domaine|commune"),
        sa.Column("date_declaration",sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("date_resolution", sa.DateTime(timezone=True)),
        sa.Column("declarant_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=True),
        sa.Column("workflow_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=True),
        sa.Column("sha256_dossier", sa.String(64)),
        sa.Column("cree_par",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at",     sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_cf_statut","conflit_foncier",["statut"])

    op.create_table(
        "parcelle_conflit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("conflit_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conflit_foncier.id"), nullable=False),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("role_dans_conflit", sa.String(50),
                  comment="contestee|adjacente|temoin"),
        sa.UniqueConstraint("conflit_id","parcelle_id", name="uq_pc_conflit_parcelle"),
    )
    op.create_index("ix_pc_conflit","parcelle_conflit",["conflit_id"])

    op.create_table(
        "mediation_conflit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("conflit_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conflit_foncier.id"), nullable=False),
        sa.Column("mediateur_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("date_mediation",  sa.DateTime(timezone=True), nullable=False),
        sa.Column("accord_trouve",   sa.Boolean, server_default="false"),
        sa.Column("termes_accord",   sa.Text),
        sa.Column("sha256_pv",       sa.String(64)),
        sa.Column("created_at",      sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )

    op.create_table(
        "decision_conflit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("conflit_id",        postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conflit_foncier.id"), nullable=False, unique=True),
        sa.Column("type_decision",     sa.String(50), nullable=False,
                  comment="mediation_reussie|jugement|classement|accord_amiable"),
        sa.Column("decision_judiciaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("decisions_judiciaires.id"), nullable=True),
        sa.Column("beneficiaire_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=True),
        sa.Column("dispositif",        sa.Text, nullable=False),
        sa.Column("date_decision",     sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("gel_leve",          sa.Boolean, server_default="false"),
        sa.Column("sha256_decision",   sa.String(64), nullable=False),
        sa.Column("rendue_par",        postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
    )

    # ══════════════════════════════════════════════════════════════════════════
    # WF14 — LOTISSEMENT ÉTENDU (complète lotissements 003/008)
    # ══════════════════════════════════════════════════════════════════════════

    op.create_table(
        "etude_technique_lotissement",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("lotissement_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lotissements.id"), nullable=False, unique=True),
        sa.Column("bureau_etudes",     sa.String(200), nullable=False),
        sa.Column("topographe_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True,
                  comment="Topographe FONCIER+ qui a réalisé le levé"),
        sa.Column("date_leve_topo",    sa.DateTime(timezone=True)),
        sa.Column("surface_nette_m2",  sa.Numeric(15, 4),
                  comment="Surface des lots attribuables (hors réserves)"),
        sa.Column("surface_voirie_m2", sa.Numeric(15, 4)),
        sa.Column("surface_equipements_m2", sa.Numeric(15, 4)),
        sa.Column("densite_prevue",    sa.Integer,
                  comment="Nombre de lots par hectare"),
        sa.Column("approbation_direction", sa.Boolean, server_default="false"),
        sa.Column("sha256_rapport",    sa.String(64), nullable=False),
        sa.Column("approuve_par",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("approuve_at",       sa.DateTime(timezone=True)),
        sa.Column("created_at",        sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )

    op.create_table(
        "reserve_publique",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("lotissement_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lotissements.id"), nullable=False),
        sa.Column("type_reserve",    sa.String(50), nullable=False,
                  comment="voirie|ecole|sante|marche|mosquee|espace_vert|autre"),
        sa.Column("surface_m2",      sa.Numeric(12, 4), nullable=False),
        sa.Column("geom",            sa.String(3000)),
        sa.Column("gestionnaire",    sa.String(200),
                  comment="Commune, État, association..."),
        sa.Column("est_affectee",    sa.Boolean, server_default="false"),
        sa.Column("sha256_reserve",  sa.String(64)),
        sa.Column("created_at",      sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint("surface_m2 > 0", name="ck_rp_surface"),
    )
    op.create_index("ix_rp_lotissement","reserve_publique",["lotissement_id"])

    op.create_table(
        "attribution_lot",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("lot_id",           postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lotissement_lot.id"), nullable=False, unique=True),
        sa.Column("beneficiaire_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("acte_communal_ref",sa.String(200), nullable=False,
                  comment="Référence de l'acte communal d'attribution"),
        sa.Column("prix_fcfa",        sa.Numeric(18, 2)),
        sa.Column("paiement_ref",     sa.String(200)),
        sa.Column("ccfm_id",          postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=True),
        sa.Column("droit_id",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcel_right_version.id"), nullable=True),
        sa.Column("sha256_acte",      sa.String(64), nullable=False),
        sa.Column("attribue_par",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("date_attribution", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_al_beneficiaire","attribution_lot",["beneficiaire_id"])

    op.create_table(
        "financement_lotissement",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("lotissement_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lotissements.id"), nullable=False),
        sa.Column("bailleur",       sa.String(200), nullable=False),
        sa.Column("type_financement",sa.String(50), nullable=False,
                  comment="budget_etat|pret_bancaire|fonds_propres|aide_internationale"),
        sa.Column("montant_fcfa",   sa.Numeric(18, 2), nullable=False),
        sa.Column("statut",         sa.String(30), server_default="accorde"),
        sa.Column("date_accord",    sa.DateTime(timezone=True)),
        sa.Column("sha256_contrat", sa.String(64)),
        sa.Column("created_at",     sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )

    # ══════════════════════════════════════════════════════════════════════════
    # WF15 — ZONES SPÉCIALES
    # ══════════════════════════════════════════════════════════════════════════

    op.create_table(
        "zone_speciale",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code_zone",       sa.String(30), nullable=False, unique=True),
        sa.Column("nom_zone",        sa.String(300), nullable=False),
        sa.Column("type_zone", sa.Enum(
                  "industrielle","agricole","miniere","forestiere","protegee",
                  "militaire","touristique","corridor_ecologique","autre",
                  name="type_zone_speciale"), nullable=False),
        sa.Column("statut", sa.Enum(
                  "active","suspendue","en_revision","abrogee",
                  name="statut_zone_speciale"),
                  server_default="active", nullable=False),
        sa.Column("autorite_gestionnaire", sa.String(200), nullable=False),
        sa.Column("geom",            sa.String(10000),
                  comment="WKT du périmètre de la zone"),
        sa.Column("surface_ha",      sa.Numeric(15, 4)),
        sa.Column("arrete_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("arretes_urbanisme.id"), nullable=True),
        sa.Column("arrete_ref",      sa.String(200), nullable=False),
        sa.Column("sha256_arrete",   sa.String(64)),
        sa.Column("created_at",      sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",      sa.DateTime(timezone=True)),
    )
    op.create_index("ix_zs_type",   "zone_speciale", ["type_zone"])
    op.create_index("ix_zs_statut", "zone_speciale", ["statut"])

    op.create_table(
        "regle_zone",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("zone_id",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("zone_speciale.id"), nullable=False),
        sa.Column("code_regle",      sa.String(50), nullable=False),
        sa.Column("libelle",         sa.String(300), nullable=False),
        sa.Column("type_regle",      sa.String(50), nullable=False,
                  comment="interdiction|obligation|restriction|autorisation_prealable"),
        sa.Column("operations_visees",postgresql.JSONB,
                  comment="[mutation, lotissement, hypotheque, changement_usage...]"),
        sa.Column("est_active",      sa.Boolean, server_default="true"),
        sa.Column("created_at",      sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("zone_id","code_regle", name="uq_rz_zone_code"),
    )
    op.create_index("ix_rz_zone","regle_zone",["zone_id"])

    op.create_table(
        "parcelle_zone",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcelle_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("zone_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("zone_speciale.id"), nullable=False),
        sa.Column("est_dans_zone",sa.Boolean, server_default="true"),
        sa.Column("pct_surface_en_zone", sa.Numeric(5, 2),
                  comment="Pourcentage de la parcelle dans la zone"),
        sa.Column("created_at",   sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("parcelle_id","zone_id", name="uq_pz_parcelle_zone"),
    )
    op.create_index("ix_pz_parcelle","parcelle_zone",["parcelle_id"])
    op.create_index("ix_pz_zone",    "parcelle_zone",["zone_id"])

    op.create_table(
        "infraction_zone",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("zone_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("zone_speciale.id"), nullable=False),
        sa.Column("parcelle_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("regle_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("regle_zone.id"), nullable=False),
        sa.Column("description",  sa.Text, nullable=False),
        sa.Column("statut",       sa.String(30), server_default="detectee"),
        sa.Column("sanction",     sa.Text),
        sa.Column("date_detection",sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("resolue_at",   sa.DateTime(timezone=True)),
        sa.Column("detecte_par",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sha256_pv",    sa.String(64)),
    )
    op.create_index("ix_iz_zone",   "infraction_zone", ["zone_id"])
    op.create_index("ix_iz_statut", "infraction_zone", ["statut"])

    op.create_table(
        "controle_zone_lotissement",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("lotissement_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lotissements.id"), nullable=False),
        sa.Column("zone_id",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("zone_speciale.id"), nullable=False),
        sa.Column("compatible",      sa.Boolean, nullable=False),
        sa.Column("motif_blocage",   sa.Text),
        sa.Column("derogation_ok",   sa.Boolean, server_default="false"),
        sa.Column("derogation_ref",  sa.String(200)),
        sa.Column("controle_at",     sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("controle_par",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("lotissement_id","zone_id", name="uq_czl_lot_zone"),
    )

    # ══════════════════════════════════════════════════════════════════════════
    # TRIGGERS WF11-WF15
    # ══════════════════════════════════════════════════════════════════════════

    # TE1 : Expropriation → gel parcelle ──────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_expropriation_gel()
        RETURNS TRIGGER AS $$
        DECLARE
            v_nicad TEXT;
        BEGIN
            SELECT p.nicad INTO v_nicad FROM parcelles p WHERE p.id = NEW.parcelle_id;
            UPDATE parcelles SET is_gele = true, updated_at = NOW()
            WHERE id = NEW.parcelle_id;
            INSERT INTO transaction_blocks (
                parcelle_id, type_blockage, statut, motif, sha256_motif, pose_par
            ) SELECT NEW.parcelle_id, 'admin_manuel'::type_blockage,
                'actif'::statut_blockage,
                'EXPROPRIATION DUP: Parcelle expropriée pour utilité publique — transactions bloquées',
                encode(sha256(('EXP|' || NEW.parcelle_id || '|' || NOW())::bytea), 'hex'),
                NEW.cree_par
            WHERE NOT EXISTS (
                SELECT 1 FROM transaction_blocks
                WHERE parcelle_id = NEW.parcelle_id AND statut = 'actif'
                  AND motif LIKE '%EXPROPRIATION%'
            );
            INSERT INTO parcel_public_history (
                parcelle_id, nicad, statut_avant, statut_apres,
                module_source, motif_public, role_acteur
            ) VALUES (
                NEW.parcelle_id, v_nicad, 'active', 'expropriee',
                'domaine', 'Expropriation pour utilité publique initiée', 'DOMAINE'
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_te1_expropriation_gel
        AFTER INSERT ON expropriation
        FOR EACH ROW EXECUTE FUNCTION tg_fn_expropriation_gel();
    """)

    # TE2 : Régularisation — anti double enregistrement ───────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_regularisation_anti_double()
        RETURNS TRIGGER AS $$
        DECLARE
            v_doublon_count INT;
        BEGIN
            -- Vérifier qu'aucune régularisation active n'existe pour cette géométrie/occupant
            SELECT COUNT(*) INTO v_doublon_count
            FROM demande_regularisation dr
            WHERE dr.occupant_id = NEW.occupant_id
              AND dr.statut NOT IN ('archive','rejetee')
              AND dr.id != NEW.id;

            IF v_doublon_count > 0 THEN
                RAISE EXCEPTION
                    'RÉGULARISATION [TE2]: Cet occupant a déjà une demande de régularisation active. '
                    'Double régularisation bloquée — risque de fraude.';
            END IF;

            -- Vérifier si une parcelle existe déjà à cet emplacement (via conflits géométriques)
            IF EXISTS (
                SELECT 1 FROM conflits_parcellaires cp
                JOIN parcelles p ON p.id = cp.parcelle_a_id
                WHERE cp.gravite = 'critique' AND cp.statut IN ('ouvert','bloque')
            ) THEN
                RAISE NOTICE 'RÉGULARISATION: Des conflits géométriques critiques existent dans la zone.';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_te2_regularisation_anti_double
        BEFORE INSERT ON demande_regularisation
        FOR EACH ROW EXECUTE FUNCTION tg_fn_regularisation_anti_double();
    """)

    # TE3 : Conflit foncier → gel automatique des parcelles concernées ─────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_conflit_gel_parcelles()
        RETURNS TRIGGER AS $$
        DECLARE
            v_parc RECORD;
        BEGIN
            FOR v_parc IN
                SELECT pc.parcelle_id, p.nicad
                FROM parcelle_conflit pc
                JOIN parcelles p ON p.id = pc.parcelle_id
                WHERE pc.conflit_id = NEW.id
            LOOP
                UPDATE parcelles SET is_gele = true, updated_at = NOW()
                WHERE id = v_parc.parcelle_id;

                INSERT INTO transaction_blocks (
                    parcelle_id, type_blockage, statut, motif, sha256_motif, pose_par
                ) SELECT v_parc.parcelle_id, 'admin_manuel'::type_blockage,
                    'actif'::statut_blockage,
                    'CONFLIT FONCIER: Parcelle concernée par un conflit — transactions bloquées',
                    encode(sha256(('CONFLIT|' || v_parc.parcelle_id || '|' || NOW())::bytea), 'hex'),
                    NEW.cree_par
                WHERE NOT EXISTS (
                    SELECT 1 FROM transaction_blocks
                    WHERE parcelle_id = v_parc.parcelle_id AND statut = 'actif'
                      AND motif LIKE '%CONFLIT FONCIER%'
                );
            END LOOP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_te3_conflit_gel_parcelles
        AFTER INSERT ON parcelle_conflit
        FOR EACH ROW EXECUTE FUNCTION tg_fn_conflit_gel_parcelles();
    """)

    # TE4 : Contrôle zone spéciale sur nouvelles parcelles ────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_zone_speciale_controle()
        RETURNS TRIGGER AS $$
        DECLARE
            v_zone RECORD;
        BEGIN
            -- Vérifier si la parcelle est dans une zone spéciale avec règles restrictives
            FOR v_zone IN
                SELECT zs.id, zs.nom_zone, zs.type_zone
                FROM zone_speciale zs
                WHERE zs.statut = 'active'
                  -- PostGIS en production : ST_Intersects(NEW.geom, zs.geom)
                  -- Ici : vérification via parcelle_zone si déjà lié
            LOOP
                -- Vérifier si une règle d'interdiction s'applique
                IF EXISTS (
                    SELECT 1 FROM regle_zone rz
                    WHERE rz.zone_id = v_zone.id
                      AND rz.type_regle IN ('interdiction','autorisation_prealable')
                      AND rz.est_active = true
                      AND rz.operations_visees ? 'creation_parcelle'
                ) THEN
                    INSERT INTO infraction_zone (
                        zone_id, parcelle_id, regle_id, description,
                        statut, detecte_par
                    )
                    SELECT v_zone.id, NEW.id, rz.id,
                        'Parcelle créée dans zone ' || v_zone.nom_zone || ' sans autorisation préalable',
                        'detectee',
                        (SELECT id FROM users WHERE role = 'ADMIN' LIMIT 1)
                    FROM regle_zone rz
                    WHERE rz.zone_id = v_zone.id AND rz.est_active = true
                      AND rz.type_regle = 'interdiction'
                    LIMIT 1;
                END IF;
            END LOOP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_te4_zone_speciale_controle
        AFTER INSERT ON parcelles
        FOR EACH ROW EXECUTE FUNCTION tg_fn_zone_speciale_controle();
    """)

    # TE5 : Réserves publiques obligatoires dans un lotissement ───────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_lotissement_reserve_pub()
        RETURNS TRIGGER AS $$
        DECLARE
            v_pct_reserve    NUMERIC;
            v_surface_lot    NUMERIC;
            v_surface_reserve NUMERIC;
        BEGIN
            -- Vérifier qu'au moins 15% est réservé aux espaces publics
            SELECT COALESCE(SUM(surface_m2), 0) INTO v_surface_reserve
            FROM reserve_publique WHERE lotissement_id = NEW.id;

            SELECT COALESCE(SUM(surface_m2), 0) INTO v_surface_lot
            FROM lotissement_lot WHERE lotissement_id = NEW.id;

            IF v_surface_lot > 0 THEN
                v_pct_reserve := v_surface_reserve / (v_surface_lot + v_surface_reserve) * 100;
                IF v_pct_reserve < 10 THEN
                    RAISE NOTICE
                        'LOTISSEMENT [TE5]: Réserves publiques insuffisantes (%.1f%% < 10%% requis). '
                        'Ajouter des réserves de voirie ou équipements.',
                        v_pct_reserve;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_te5_lotissement_reserve_pub
        AFTER INSERT ON reserve_publique
        FOR EACH ROW EXECUTE FUNCTION tg_fn_lotissement_reserve_pub();
    """)

    # Décision conflit → dégel des parcelles ─────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_decision_conflit_gel()
        RETURNS TRIGGER AS $$
        DECLARE
            v_conflit RECORD;
            v_parc    RECORD;
        BEGIN
            IF NEW.gel_leve = true THEN
                SELECT * INTO v_conflit FROM conflit_foncier WHERE id = NEW.conflit_id;
                -- Résoudre le conflit
                UPDATE conflit_foncier
                SET statut = 'resolu'::statut_conflit_foncier, date_resolution = NOW()
                WHERE id = NEW.conflit_id;
                -- Dégeler toutes les parcelles du conflit
                FOR v_parc IN
                    SELECT pc.parcelle_id FROM parcelle_conflit pc WHERE pc.conflit_id = NEW.conflit_id
                LOOP
                    UPDATE parcelles SET is_gele = false, updated_at = NOW()
                    WHERE id = v_parc.parcelle_id;
                    UPDATE transaction_blocks
                    SET statut = 'leve'::statut_blockage, leve_at = NOW(),
                        motif_levee = 'Conflit résolu — décision: ' || NEW.type_decision
                    WHERE parcelle_id = v_parc.parcelle_id AND statut = 'actif'
                      AND motif LIKE '%CONFLIT FONCIER%';
                END LOOP;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_te6_decision_conflit_gel
        AFTER INSERT ON decision_conflit
        FOR EACH ROW
        WHEN (NEW.gel_leve = true)
        EXECUTE FUNCTION tg_fn_decision_conflit_gel();
    """)

    # ══════════════════════════════════════════════════════════════════════════
    # VUES TABLEAU DE BORD GLOBAL — tous workflows
    # ══════════════════════════════════════════════════════════════════════════
    op.execute("""
        CREATE OR REPLACE VIEW v_tableau_bord_complet AS
        SELECT 'LOTISSEMENT'    AS type_op, COUNT(*) AS total,
               SUM(CASE WHEN statut='actif' THEN 1 ELSE 0 END) AS en_cours,
               0 AS rejetes
        FROM lotissements WHERE statut!='archive'
        UNION ALL
        SELECT 'MUTATION', COUNT(*),
               SUM(CASE WHEN statut NOT IN('archive','rejete') THEN 1 ELSE 0 END),
               SUM(CASE WHEN statut='rejete' THEN 1 ELSE 0 END)
        FROM mutation_dossier
        UNION ALL
        SELECT 'HYPOTHEQUE', COUNT(*),
               SUM(CASE WHEN statut NOT IN('levee','contentieux') THEN 1 ELSE 0 END),
               0 FROM hypotheque_dossier
        UNION ALL
        SELECT 'FUSION', COUNT(*),
               SUM(CASE WHEN statut NOT IN('archive','rejete') THEN 1 ELSE 0 END),
               SUM(CASE WHEN statut='rejete' THEN 1 ELSE 0 END)
        FROM fusion_parcellaire
        UNION ALL
        SELECT 'ANNULATION_JUD', COUNT(*),
               SUM(CASE WHEN statut!='archive' THEN 1 ELSE 0 END), 0
        FROM annulation_judiciaire_dossier
        UNION ALL
        SELECT 'DIVISION', COUNT(*),
               SUM(CASE WHEN statut NOT IN('archive','rejete') THEN 1 ELSE 0 END),
               SUM(CASE WHEN statut='rejete' THEN 1 ELSE 0 END)
        FROM demande_division
        UNION ALL
        SELECT 'SUCCESSION', COUNT(*),
               SUM(CASE WHEN statut NOT IN('archive') THEN 1 ELSE 0 END), 0
        FROM succession
        UNION ALL
        SELECT 'EXPROPRIATION', COUNT(*),
               SUM(CASE WHEN statut NOT IN('archive') THEN 1 ELSE 0 END),
               SUM(CASE WHEN statut='contestee' THEN 1 ELSE 0 END)
        FROM expropriation
        UNION ALL
        SELECT 'REGULARISATION', COUNT(*),
               SUM(CASE WHEN statut NOT IN('archive','rejetee') THEN 1 ELSE 0 END),
               SUM(CASE WHEN statut='rejetee' THEN 1 ELSE 0 END)
        FROM demande_regularisation
        UNION ALL
        SELECT 'CONFLIT', COUNT(*),
               SUM(CASE WHEN statut NOT IN('archive','resolu') THEN 1 ELSE 0 END), 0
        FROM conflit_foncier;
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_tableau_bord_complet CASCADE")

    for trigger, table in [
        ("tg_te6_decision_conflit_gel",     "decision_conflit"),
        ("tg_te5_lotissement_reserve_pub",  "reserve_publique"),
        ("tg_te4_zone_speciale_controle",   "parcelles"),
        ("tg_te3_conflit_gel_parcelles",    "parcelle_conflit"),
        ("tg_te2_regularisation_anti_double","demande_regularisation"),
        ("tg_te1_expropriation_gel",        "expropriation"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    for fn in [
        "tg_fn_expropriation_gel","tg_fn_regularisation_anti_double",
        "tg_fn_conflit_gel_parcelles","tg_fn_zone_speciale_controle",
        "tg_fn_lotissement_reserve_pub","tg_fn_decision_conflit_gel",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")

    for table in [
        "controle_zone_lotissement","infraction_zone","parcelle_zone",
        "regle_zone","zone_speciale",
        "financement_lotissement","attribution_lot","reserve_publique",
        "etude_technique_lotissement",
        "decision_conflit","mediation_conflit","parcelle_conflit","conflit_foncier",
        "attribution_propriete","arrete_regularisation",
        "enquete_fonciere","demande_regularisation",
        "contestation_expropriation","paiement_indemnisation",
        "evaluation_indemnisation","expropriation","projet_public",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    for name in [
        "statut_expropriation","statut_regularisation","statut_conflit_foncier",
        "type_conflit_foncier","type_zone_speciale","statut_zone_speciale",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")
