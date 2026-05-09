"""011_workflows_6_a_10

FONCIER+ v3.4.7 — Migration 011 : Workflows 6 à 10
  WF6  : Division parcellaire
  WF7  : Servitudes foncières (complète parcel_servitude 005)
  WF8  : Hypothèques et garanties bancaires (complète hypotheque_dossier 008)
  WF9  : Succession foncière
  WF10 : Changement d'usage foncier

Tables existantes utilisées (non dupliquées) :
  parcelles, parcel_lineage, lotissement_lot, demandes_ccfm, nicad_registry (003)
  transaction_blocks, parcel_public_history (004)
  parcel_servitude, right_holders, parcel_right_version, property_transfers (005)
  hypotheques, mortgage_registry, hypotheque_dossier (001/005/008)
  urbanisme_conformite, arretes_urbanisme (003/009)
  workflow_instance, workflow_step_def (007)
  annf_archive_links (004)

36 tables nouvelles réparties sur migrations 011 et 012.
Cette migration crée les 18 tables des workflows 6-10.

Triggers critiques :
  TD1. tg_division_bloc_mere          — bloque parcelle mère à l'initiation
  TD2. tg_division_surface_coherence  — somme lots = surface mère ±0.1%
  TD3. tg_servitude_bloque_si_publique— servitude publique → transaction_block
  TD4. tg_hyp_autorisation_requise    — hypothèque exige autorisation bancaire
  TD5. tg_succession_somme_parts      — somme parts héritiers = 100%
  TD6. tg_usage_invalide_ccfm         — changement usage invalide CCFM existant
  TD7. tg_succession_bloc_si_indivision — gel si indivision non résolue

Revision ID: 011_workflows_6_a_10
Revises: 010_workflow_annulation_et_fusion
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "011_workflows_6_a_10"
down_revision = "010_workflow_annulation_et_fusion"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ── ENUMS ─────────────────────────────────────────────────────────────────
    for name, values in [
        ("statut_division",
         "('initie','ccfm_verif','validation_cadastre','dad_validation',"
         "'creation_parcelles','archive','rejete')"),
        ("type_servitude_publique",
         "('voirie','reseau_electrique','reseau_eau','reseau_assainissement',"
         "'couloir_transport','zone_tampon','autre_publique')"),
        ("statut_succession",
         "('ouverte','en_instruction','jugement','partage','archive','litigieuse')"),
        ("type_succession",
         "('testamentaire','ab_intestat','coutumiere','judiciaire','mixte')"),
        ("statut_usage",
         "('actif','en_cours_changement','modifie','archive')"),
        ("type_usage",
         "('residentiel','commercial','industriel','agricole','mixte',"
         "'equipement_public','espace_vert','religieux','autre')"),
        ("statut_autorisation_bancaire",
         "('demandee','accordee','refusee','expiree')"),
    ]:
        op.execute(f"""
            DO $$ BEGIN CREATE TYPE {name} AS ENUM {values};
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """)

    # ══════════════════════════════════════════════════════════════════════════
    # WF6 — DIVISION PARCELLAIRE
    # ══════════════════════════════════════════════════════════════════════════

    op.create_table(
        "demande_division",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcelle_mere_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("demandeur_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("nb_lots_cibles", sa.Integer, nullable=False),
        sa.Column("motif",           sa.Text, nullable=False),
        sa.Column("statut", sa.Enum(
                  "initie","ccfm_verif","validation_cadastre","dad_validation",
                  "creation_parcelles","archive","rejete",
                  name="statut_division"), server_default="initie", nullable=False),
        # Contrôles obligatoires
        sa.Column("ccfm_ok",           sa.Boolean, server_default="false"),
        sa.Column("contiguites_ok",    sa.Boolean, server_default="false"),
        sa.Column("proprietaire_unique", sa.Boolean, server_default="false",
                  comment="True si un seul propriétaire à 100%"),
        sa.Column("accord_bancaire",   sa.Boolean, server_default="false",
                  comment="True si hypothèque présente et banque a accordé"),
        sa.Column("dad_valide",        sa.Boolean, server_default="false"),
        # Liens
        sa.Column("ccfm_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=True),
        sa.Column("workflow_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=True),
        sa.Column("sha256_dossier",sa.String(64)),
        sa.Column("initie_par",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at",    sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",    sa.DateTime(timezone=True)),
        sa.CheckConstraint("nb_lots_cibles >= 2",
                            name="ck_division_min_2_lots"),
    )
    op.create_index("ix_dd_parcelle", "demande_division", ["parcelle_mere_id"])
    op.create_index("ix_dd_statut",   "demande_division", ["statut"])

    op.create_table(
        "division_lot_projet",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("demande_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demande_division.id"), nullable=False),
        sa.Column("numero_lot",   sa.String(50), nullable=False),
        sa.Column("surface_m2",   sa.Numeric(12, 4), nullable=False),
        sa.Column("geom",         sa.String(3000)),
        sa.Column("est_contigu",  sa.Boolean, server_default="false",
                  comment="True si contigu aux autres lots"),
        sa.Column("est_espace_public", sa.Boolean, server_default="false",
                  comment="Réservé voirie ou équipements publics"),
        sa.Column("geometry_hash",sa.String(64), nullable=False),
        sa.Column("statut",       sa.String(20), server_default="provisoire"),
        sa.UniqueConstraint("demande_id", "numero_lot",
                             name="uq_dlp_lot_demande"),
        sa.CheckConstraint("surface_m2 > 0", name="ck_dlp_surface"),
    )
    op.create_index("ix_dlp_demande", "division_lot_projet", ["demande_id"])

    op.create_table(
        "division_lot_resultat",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("demande_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demande_division.id"), nullable=False),
        sa.Column("lot_projet_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("division_lot_projet.id"), nullable=False),
        sa.Column("parcelle_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("nicad",          sa.String(30), unique=True, nullable=True),
        sa.Column("rnp_demande_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnp_demandes.id"), nullable=True),
        sa.Column("ccfm_id",        postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=True),
        sa.Column("cree_at",        sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("lot_projet_id", name="uq_dlr_lot_projet"),
    )
    op.create_index("ix_dlr_demande", "division_lot_resultat", ["demande_id"])

    # ══════════════════════════════════════════════════════════════════════════
    # WF7 — SERVITUDES FONCIÈRES (complète parcel_servitude migration 005)
    # ══════════════════════════════════════════════════════════════════════════

    op.create_table(
        "historique_servitude",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("servitude_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcel_servitude.id"), nullable=False),
        sa.Column("ancien_statut", sa.String(30)),
        sa.Column("nouveau_statut",sa.String(30), nullable=False),
        sa.Column("motif",         sa.Text),
        sa.Column("snapshot_avant",postgresql.JSONB),
        sa.Column("sha256_avant",  sa.String(64)),
        sa.Column("modifie_par",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("date_modification", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("est_reversible", sa.Boolean, default=True),
    )
    op.create_index("ix_hs_servitude", "historique_servitude", ["servitude_id"])

    op.create_table(
        "servitude_publique",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcelle_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("type_servitude_pub", sa.Enum(
                  "voirie","reseau_electrique","reseau_eau","reseau_assainissement",
                  "couloir_transport","zone_tampon","autre_publique",
                  name="type_servitude_publique"), nullable=False),
        sa.Column("autorite_gestion", sa.String(200), nullable=False,
                  comment="Entité publique responsable"),
        sa.Column("arrete_ref",   sa.String(200)),
        sa.Column("arrete_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("arretes_urbanisme.id"), nullable=True),
        sa.Column("geom",         sa.String(3000),
                  comment="WKT de la zone de servitude publique"),
        sa.Column("surface_m2",   sa.Numeric(10, 4)),
        sa.Column("date_debut",   sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_fin",     sa.DateTime(timezone=True)),
        # Bloque toujours les transactions
        sa.Column("bloque_mutation",   sa.Boolean, server_default="true"),
        sa.Column("bloque_hypotheque", sa.Boolean, server_default="false"),
        sa.Column("est_active",   sa.Boolean, server_default="true"),
        sa.Column("sha256_arrete",sa.String(64)),
        sa.Column("cree_par",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at",   sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_sp_parcelle", "servitude_publique", ["parcelle_id"])
    op.create_index("ix_sp_active",   "servitude_publique", ["est_active"])

    # ══════════════════════════════════════════════════════════════════════════
    # WF8 — HYPOTHÈQUES ET GARANTIES BANCAIRES (complète hypotheque_dossier 008)
    # ══════════════════════════════════════════════════════════════════════════

    op.create_table(
        "autorisation_bancaire",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("hypotheque_dossier_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("hypotheque_dossier.id"), nullable=False),
        sa.Column("banque_holder_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("type_operation",        sa.String(50), nullable=False,
                  comment="mutation|division|fusion|lotissement — opération autorisée malgré hypothèque"),
        sa.Column("statut", sa.Enum(
                  "demandee","accordee","refusee","expiree",
                  name="statut_autorisation_bancaire"),
                  server_default="demandee", nullable=False),
        sa.Column("conditions",  sa.Text,
                  comment="Conditions posées par la banque"),
        sa.Column("montant_garanti_fcfa", sa.Numeric(18, 2)),
        sa.Column("valide_jusqu_au",      sa.DateTime(timezone=True)),
        sa.Column("sha256_autorisation",  sa.String(64), nullable=False),
        sa.Column("accordee_par",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("accordee_at",          sa.DateTime(timezone=True)),
        sa.Column("created_at",           sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_ab_statut",   "autorisation_bancaire", ["statut"])
    op.create_index("ix_ab_dossier",  "autorisation_bancaire", ["hypotheque_dossier_id"])

    op.create_table(
        "levee_hypotheque",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("hypotheque_dossier_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("hypotheque_dossier.id"), nullable=False, unique=True),
        sa.Column("hypotheque_id",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("hypotheques.id"), nullable=False),
        sa.Column("mortgage_registry_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("mortgage_registry.id"), nullable=True),
        sa.Column("motif",              sa.String(50), nullable=False,
                  comment="remboursement|accord_amiable|decision_judiciaire|expiration"),
        sa.Column("montant_solde",      sa.Numeric(18, 2),
                  comment="Montant du solde restant dû au moment de la levée"),
        sa.Column("acte_mainlevee_ref", sa.String(200), nullable=False),
        sa.Column("notaire_id",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("notary_registry.id")),
        sa.Column("sha256_mainlevee",   sa.String(64), nullable=False),
        sa.Column("date_levee",         sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("enregistre_par",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
    )
    op.create_index("ix_lh_hyp", "levee_hypotheque", ["hypotheque_id"])

    # ══════════════════════════════════════════════════════════════════════════
    # WF9 — SUCCESSION FONCIÈRE
    # ══════════════════════════════════════════════════════════════════════════

    op.create_table(
        "succession",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("de_cujus_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False,
                  comment="Le défunt"),
        sa.Column("type_succession", sa.Enum(
                  "testamentaire","ab_intestat","coutumiere","judiciaire","mixte",
                  name="type_succession"), nullable=False),
        sa.Column("statut", sa.Enum(
                  "ouverte","en_instruction","jugement","partage","archive","litigieuse",
                  name="statut_succession"),
                  server_default="ouverte", nullable=False),
        sa.Column("date_ouverture", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False,
                  comment="Date du décès"),
        sa.Column("date_jugement",  sa.DateTime(timezone=True)),
        sa.Column("reference_acte", sa.String(200)),
        sa.Column("notaire_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("notary_registry.id")),
        sa.Column("decision_judiciaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("decisions_judiciaires.id"), nullable=True),
        # Contrôle : somme parts = 100% (vérifiée par trigger TD5)
        sa.Column("somme_parts_validee", sa.Boolean, server_default="false"),
        sa.Column("sha256_succession",   sa.String(64)),
        sa.Column("cree_par",            postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at",          sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",          sa.DateTime(timezone=True)),
    )
    op.create_index("ix_succ_statut",  "succession", ["statut"])
    op.create_index("ix_succ_decujus", "succession", ["de_cujus_id"])

    op.create_table(
        "succession_parcelle",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("succession_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("succession.id"), nullable=False),
        sa.Column("parcelle_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("rnp_id",        postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnp_demandes.id"), nullable=True),
        # Statut du bien durant la succession
        sa.Column("en_indivision", sa.Boolean, server_default="false"),
        sa.Column("est_traite",    sa.Boolean, server_default="false"),
        sa.Column("created_at",    sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("succession_id","parcelle_id",
                             name="uq_sp_succession_parcelle"),
    )
    op.create_index("ix_succp_succession","succession_parcelle",["succession_id"])

    op.create_table(
        "succession_heritier",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("succession_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("succession.id"), nullable=False),
        sa.Column("heritier_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("lien_parente",   sa.String(50), nullable=False,
                  comment="conjoint|enfant|parent|frere_soeur|autre"),
        sa.Column("part_pct",       sa.Numeric(5, 2), nullable=False,
                  comment="Part héréditaire en % — somme = 100%"),
        # Droit foncier créé après partage
        sa.Column("droit_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcel_right_version.id"), nullable=True),
        sa.Column("a_accepte",      sa.Boolean, server_default="false"),
        sa.Column("created_at",     sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint("part_pct > 0 AND part_pct <= 100",
                            name="ck_sh_part_range"),
    )
    op.create_index("ix_sh_succession","succession_heritier",["succession_id"])

    op.create_table(
        "indivision",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("succession_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("succession.id"), nullable=False),
        sa.Column("parcelle_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("date_debut",    sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("date_fin",      sa.DateTime(timezone=True),
                  comment="NULL = indivision non résolue"),
        sa.Column("est_active",    sa.Boolean, server_default="true", nullable=False),
        sa.Column("mode_sortie",   sa.String(50),
                  comment="partage|vente|jugement|accord_amiable"),
        sa.Column("created_at",    sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_indiv_parcelle", "indivision", ["parcelle_id"])
    op.create_index("ix_indiv_active",   "indivision", ["est_active"])

    # ══════════════════════════════════════════════════════════════════════════
    # WF10 — CHANGEMENT D'USAGE FONCIER
    # ══════════════════════════════════════════════════════════════════════════

    op.create_table(
        "usage_foncier",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcelle_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False, unique=True),
        sa.Column("type_usage", sa.Enum(
                  "residentiel","commercial","industriel","agricole","mixte",
                  "equipement_public","espace_vert","religieux","autre",
                  name="type_usage"), nullable=False),
        sa.Column("statut", sa.Enum(
                  "actif","en_cours_changement","modifie","archive",
                  name="statut_usage"),
                  server_default="actif", nullable=False),
        sa.Column("usage_autorise",  sa.String(200),
                  comment="Libellé de l'usage autorisé par le plan d'urbanisme"),
        sa.Column("coefficient_foncier", sa.Numeric(5, 3),
                  comment="Coefficient fiscal lié à l'usage"),
        sa.Column("arrete_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("arretes_urbanisme.id"), nullable=True),
        sa.Column("sha256_usage",    sa.String(64)),
        sa.Column("created_at",      sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",      sa.DateTime(timezone=True)),
    )
    op.create_index("ix_uf_statut", "usage_foncier", ["statut"])

    op.create_table(
        "zonage_urbain",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("commune_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("communes.id"), nullable=False),
        sa.Column("code_zone",       sa.String(20), nullable=False),
        sa.Column("nom_zone",        sa.String(200), nullable=False),
        sa.Column("type_usage_autorise", sa.Enum(
                  "residentiel","commercial","industriel","agricole","mixte",
                  "equipement_public","espace_vert","religieux","autre",
                  name="type_usage"), nullable=False),
        sa.Column("geom",            sa.String(5000),
                  comment="WKT du périmètre de la zone"),
        sa.Column("cos_max",         sa.Numeric(5, 2),
                  comment="Coefficient d'Occupation des Sols maximum"),
        sa.Column("hauteur_max_m",   sa.Numeric(6, 2)),
        sa.Column("est_active",      sa.Boolean, server_default="true"),
        sa.Column("arrete_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("arretes_urbanisme.id"), nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("commune_id","code_zone", name="uq_zu_code_commune"),
    )
    op.create_index("ix_zu_commune", "zonage_urbain", ["commune_id"])

    op.create_table(
        "demande_changement_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcelle_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("usage_actuel_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("usage_foncier.id"), nullable=False),
        sa.Column("type_usage_demande",sa.Enum(
                  "residentiel","commercial","industriel","agricole","mixte",
                  "equipement_public","espace_vert","religieux","autre",
                  name="type_usage"), nullable=False),
        sa.Column("justification",    sa.Text, nullable=False),
        sa.Column("statut",           sa.String(30), server_default="initie"),
        # Validations
        sa.Column("urbanisme_ok",     sa.Boolean, server_default="false"),
        sa.Column("commune_ok",       sa.Boolean, server_default="false"),
        sa.Column("zone_compatible",  sa.Boolean, server_default="false"),
        # Liens
        sa.Column("ccfm_nouveau_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=True),
        sa.Column("arrete_changement_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("arretes_urbanisme.id"), nullable=True),
        sa.Column("workflow_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=True),
        sa.Column("demandeur_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("sha256_dossier",   sa.String(64)),
        sa.Column("created_at",       sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_dcu_parcelle","demande_changement_usage",["parcelle_id"])

    op.create_table(
        "parcelle_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcelle_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("usage_id",        postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("usage_foncier.id"), nullable=False),
        sa.Column("zone_id",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("zonage_urbain.id"), nullable=True),
        sa.Column("date_debut",      sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("date_fin",        sa.DateTime(timezone=True)),
        sa.Column("est_actuel",      sa.Boolean, server_default="true"),
        sa.Column("created_at",      sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_pu_parcelle","parcelle_usage",["parcelle_id"])

    # ══════════════════════════════════════════════════════════════════════════
    # TRIGGERS WF6-WF10
    # ══════════════════════════════════════════════════════════════════════════

    # TD1 : Blocage parcelle mère à l'initiation de la division ──────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_division_bloc_mere()
        RETURNS TRIGGER AS $$
        DECLARE
            v_nicad TEXT;
            v_motif TEXT;
        BEGIN
            SELECT p.nicad INTO v_nicad FROM parcelles p WHERE p.id = NEW.parcelle_mere_id;
            v_motif := 'BLOQUÉ DIVISION: Demande de division en cours sur ' || COALESCE(v_nicad, 'parcelle');

            UPDATE parcelles SET is_gele = true, updated_at = NOW()
            WHERE id = NEW.parcelle_mere_id;

            INSERT INTO transaction_blocks (
                parcelle_id, type_blockage, statut, motif, sha256_motif, pose_par
            ) SELECT NEW.parcelle_mere_id,
                'admin_manuel'::type_blockage, 'actif'::statut_blockage,
                v_motif,
                encode(sha256(('DIVISION|' || NEW.parcelle_mere_id || '|' || NOW())::bytea), 'hex'),
                NEW.initie_par
            WHERE NOT EXISTS (
                SELECT 1 FROM transaction_blocks
                WHERE parcelle_id = NEW.parcelle_mere_id AND statut = 'actif'
                  AND motif LIKE '%BLOQUÉ DIVISION%'
            );

            INSERT INTO parcel_public_history (
                parcelle_id, nicad, statut_avant, statut_apres,
                module_source, motif_public, role_acteur
            ) VALUES (
                NEW.parcelle_mere_id, v_nicad,
                'active', 'bloquee_division', 'cadastre',
                'Parcelle bloquée — Demande de division en cours', 'CADASTRE'
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_td1_division_bloc_mere
        AFTER INSERT ON demande_division
        FOR EACH ROW EXECUTE FUNCTION tg_fn_division_bloc_mere();
    """)

    # TD2 : Cohérence surface division ────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_division_surface()
        RETURNS TRIGGER AS $$
        DECLARE
            v_total   NUMERIC;
            v_mere    NUMERIC;
            v_demande RECORD;
        BEGIN
            SELECT dd.*, p.surface_m2 AS mere_surface
            INTO v_demande
            FROM demande_division dd
            JOIN parcelles p ON p.id = dd.parcelle_mere_id
            WHERE dd.id = NEW.demande_id;

            SELECT COALESCE(SUM(surface_m2), 0) INTO v_total
            FROM division_lot_projet
            WHERE demande_id = NEW.demande_id AND id != NEW.id;
            v_total := v_total + NEW.surface_m2;

            IF v_total > v_demande.mere_surface * 1.001 THEN
                RAISE EXCEPTION
                    'DIVISION [TD2]: Somme des lots (%.2f m²) dépasse la surface mère (%.2f m²).',
                    v_total, v_demande.mere_surface;
            END IF;

            -- Vérifier hash géométrique
            IF NEW.geometry_hash != encode(
                sha256((NEW.numero_lot || '|' || NEW.surface_m2::TEXT ||
                        '|' || COALESCE(NEW.geom,''))::bytea), 'hex'
            ) THEN
                RAISE EXCEPTION
                    'DIVISION [TD2]: Hash géométrique invalide pour lot % — fraude détectée.',
                    NEW.numero_lot;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_td2_division_surface
        BEFORE INSERT OR UPDATE OF surface_m2, geom ON division_lot_projet
        FOR EACH ROW EXECUTE FUNCTION tg_fn_division_surface();
    """)

    # TD3 : Servitude publique → transaction_block ────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_servitude_publique_block()
        RETURNS TRIGGER AS $$
        DECLARE
            v_motif TEXT;
        BEGIN
            IF NEW.bloque_mutation = true AND NEW.est_active = true THEN
                v_motif := 'SERVITUDE PUBLIQUE (' || NEW.type_servitude_pub::TEXT || '): mutations bloquées';
                INSERT INTO transaction_blocks (
                    parcelle_id, type_blockage, statut, motif, sha256_motif, pose_par
                ) SELECT NEW.parcelle_id, 'admin_manuel'::type_blockage,
                    'actif'::statut_blockage, v_motif,
                    encode(sha256(v_motif::bytea), 'hex'), NEW.cree_par
                WHERE NOT EXISTS (
                    SELECT 1 FROM transaction_blocks
                    WHERE parcelle_id = NEW.parcelle_id AND statut = 'actif'
                      AND motif LIKE '%SERVITUDE PUBLIQUE%'
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_td3_servitude_publique_block
        AFTER INSERT ON servitude_publique
        FOR EACH ROW WHEN (NEW.bloque_mutation = true)
        EXECUTE FUNCTION tg_fn_servitude_publique_block();
    """)

    # TD4 : Hypothèque exige autorisation bancaire pour opérations ────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_hyp_autorisation_requise()
        RETURNS TRIGGER AS $$
        DECLARE
            nb_hyp INT;
        BEGIN
            SELECT COUNT(*) INTO nb_hyp
            FROM hypotheques h WHERE h.parcelle_id = NEW.parcelle_mere_id
              AND h.statut = 'active';
            SELECT COUNT(*) + nb_hyp INTO nb_hyp
            FROM mortgage_registry mr WHERE mr.parcelle_id = NEW.parcelle_mere_id
              AND mr.statut = 'active';

            IF nb_hyp > 0 AND NEW.accord_bancaire = false THEN
                RAISE EXCEPTION
                    'DIVISION [TD4]: Parcelle hypothéquée — autorisation bancaire obligatoire '
                    'avant toute division. Déposer une demande autorisation_bancaire.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_td4_hyp_autorisation
        BEFORE INSERT ON demande_division
        FOR EACH ROW EXECUTE FUNCTION tg_fn_hyp_autorisation_requise();
    """)

    # TD5 : Somme parts héritiers = 100% ──────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_succession_somme_parts()
        RETURNS TRIGGER AS $$
        DECLARE
            v_total NUMERIC;
        BEGIN
            SELECT COALESCE(SUM(part_pct), 0) INTO v_total
            FROM succession_heritier
            WHERE succession_id = NEW.succession_id AND id != COALESCE(NEW.id,
                  '00000000-0000-0000-0000-000000000000'::uuid);
            v_total := v_total + NEW.part_pct;

            IF v_total > 100.01 THEN
                RAISE EXCEPTION
                    'SUCCESSION [TD5]: Somme des parts héréditaires (%.2f%%) dépasse 100%%. '
                    'Impossible d''enregistrer cet héritier.',
                    v_total;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_td5_succession_somme_parts
        BEFORE INSERT OR UPDATE OF part_pct ON succession_heritier
        FOR EACH ROW EXECUTE FUNCTION tg_fn_succession_somme_parts();
    """)

    # TD6 : Changement usage invalide le CCFM ────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_usage_invalide_ccfm()
        RETURNS TRIGGER AS $$
        DECLARE
            nb_invalides INT;
        BEGIN
            IF NEW.statut = 'modifie' AND OLD.statut IS DISTINCT FROM NEW.statut THEN
                UPDATE demandes_ccfm SET statut = 'ANNULE'
                WHERE parcelle_id = NEW.parcelle_id
                  AND statut IN ('CERTIFIE','SCELLE','VALIDE');
                GET DIAGNOSTICS nb_invalides = ROW_COUNT;

                IF nb_invalides > 0 THEN
                    INSERT INTO parcel_public_history (
                        parcelle_id, nicad, statut_avant, statut_apres,
                        module_source, motif_public, role_acteur
                    )
                    SELECT p.id, p.nicad,
                        'usage_' || OLD.type_usage::TEXT,
                        'usage_' || NEW.type_usage::TEXT,
                        'urbanisme',
                        'Usage foncier modifié — CCFM invalidé, nouveau CCFM requis',
                        'URBANISME'
                    FROM parcelles p WHERE p.id = NEW.parcelle_id;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_td6_usage_invalide_ccfm
        AFTER UPDATE OF statut ON usage_foncier
        FOR EACH ROW
        WHEN (NEW.statut = 'modifie' AND OLD.statut IS DISTINCT FROM NEW.statut)
        EXECUTE FUNCTION tg_fn_usage_invalide_ccfm();
    """)

    # TD7 : Gel si indivision active ──────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_gel_si_indivision()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.est_active = true THEN
                UPDATE parcelles SET is_gele = true, updated_at = NOW()
                WHERE id = NEW.parcelle_id;
                INSERT INTO transaction_blocks (
                    parcelle_id, type_blockage, statut, motif, sha256_motif, pose_par
                ) SELECT NEW.parcelle_id, 'admin_manuel'::type_blockage,
                    'actif'::statut_blockage,
                    'INDIVISION SUCCESSORALE: parcelle en indivision — transactions bloquées',
                    encode(sha256(('INDIV|' || NEW.parcelle_id || '|' || NOW())::bytea), 'hex'),
                    (SELECT id FROM users WHERE role = 'ADMIN' LIMIT 1)
                WHERE NOT EXISTS (
                    SELECT 1 FROM transaction_blocks
                    WHERE parcelle_id = NEW.parcelle_id AND statut = 'actif'
                      AND motif LIKE '%INDIVISION%'
                );
            ELSE
                -- Dégel si indivision résolue
                UPDATE parcelles SET is_gele = false, updated_at = NOW()
                WHERE id = NEW.parcelle_id
                  AND NOT EXISTS (
                    SELECT 1 FROM indivision i2
                    WHERE i2.parcelle_id = NEW.parcelle_id
                      AND i2.est_active = true AND i2.id != NEW.id
                  );
                UPDATE transaction_blocks
                SET statut = 'leve'::statut_blockage, leve_at = NOW(),
                    motif_levee = 'Indivision résolue'
                WHERE parcelle_id = NEW.parcelle_id AND statut = 'actif'
                  AND motif LIKE '%INDIVISION%';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_td7_gel_si_indivision
        AFTER INSERT OR UPDATE OF est_active ON indivision
        FOR EACH ROW EXECUTE FUNCTION tg_fn_gel_si_indivision();
    """)

    # Levée d'hypothèque → lever les blocks ───────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_levee_hypotheque_deblock()
        RETURNS TRIGGER AS $$
        DECLARE
            v_parcelle_id UUID;
            v_nb_hyp_restantes INT;
        BEGIN
            SELECT hd.parcelle_id INTO v_parcelle_id
            FROM hypotheque_dossier hd WHERE hd.id = NEW.hypotheque_dossier_id;

            UPDATE hypotheques SET statut = 'levee'
            WHERE id = NEW.hypotheque_id;
            UPDATE mortgage_registry SET statut = 'levee'::statut_hypotheque, levee_at = NOW()
            WHERE hypotheque_id = NEW.hypotheque_id;
            UPDATE hypotheque_dossier SET statut = 'levee'::statut_hypotheque_dossier,
                date_levee = NOW(), motif_levee = NEW.motif
            WHERE id = NEW.hypotheque_dossier_id;

            -- Lever le block si plus d'hypothèque active
            SELECT COUNT(*) INTO v_nb_hyp_restantes
            FROM hypotheques h WHERE h.parcelle_id = v_parcelle_id AND h.statut = 'active';

            IF v_nb_hyp_restantes = 0 AND v_parcelle_id IS NOT NULL THEN
                INSERT INTO parcel_public_history (
                    parcelle_id, nicad, statut_avant, statut_apres,
                    module_source, motif_public, role_acteur
                )
                SELECT p.id, p.nicad, 'hypotheque_active', 'libre',
                    'banque', 'Hypothèque levée — parcelle libérée de tout gage', 'BANQUE'
                FROM parcelles p WHERE p.id = v_parcelle_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_td8_levee_hyp_deblock
        AFTER INSERT ON levee_hypotheque
        FOR EACH ROW EXECUTE FUNCTION tg_fn_levee_hypotheque_deblock();
    """)

    # Vues WF6-WF10 ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE VIEW v_divisions_en_cours AS
        SELECT dd.id, dd.statut, dd.nb_lots_cibles,
               p.nicad AS parcelle_mere,
               p.surface_m2,
               COUNT(dlp.id) AS nb_lots_projetes,
               SUM(dlp.surface_m2) AS surface_projete,
               dd.ccfm_ok, dd.contiguites_ok, dd.accord_bancaire, dd.dad_valide,
               wi.etape_courante_code AS wf_etape,
               wi.attendu_de_role    AS wf_role
        FROM demande_division dd
        JOIN parcelles p ON p.id = dd.parcelle_mere_id
        LEFT JOIN division_lot_projet dlp ON dlp.demande_id = dd.id
        LEFT JOIN workflow_instance wi    ON wi.id = dd.workflow_id
        WHERE dd.statut NOT IN ('archive','rejete')
        GROUP BY dd.id, dd.statut, dd.nb_lots_cibles, p.nicad, p.surface_m2,
                 dd.ccfm_ok, dd.contiguites_ok, dd.accord_bancaire, dd.dad_valide,
                 wi.etape_courante_code, wi.attendu_de_role;
    """)

    op.execute("""
        CREATE OR REPLACE VIEW v_successions_en_cours AS
        SELECT s.id, s.statut, s.type_succession,
               rh.nom || ' ' || COALESCE(rh.prenom,'') AS de_cujus,
               s.date_ouverture,
               COUNT(sh.id) AS nb_heritiers,
               SUM(sh.part_pct) AS somme_parts,
               COUNT(sp.id) AS nb_parcelles,
               SUM(CASE WHEN i.est_active THEN 1 ELSE 0 END) AS nb_indivisions_actives
        FROM succession s
        JOIN right_holders rh ON rh.id = s.de_cujus_id
        LEFT JOIN succession_heritier sh ON sh.succession_id = s.id
        LEFT JOIN succession_parcelle sp ON sp.succession_id = s.id
        LEFT JOIN indivision i ON i.succession_id = s.id AND i.est_active = true
        WHERE s.statut NOT IN ('archive')
        GROUP BY s.id, s.statut, s.type_succession, rh.nom, rh.prenom, s.date_ouverture;
    """)

    # Seed étapes workflows dans workflow_step_def (WF9 succession)
    op.execute("""
        INSERT INTO workflow_step_def (
            id, definition_id, ordre, code_etape, nom_etape,
            role_requis, type_validation, delai_max_heures, est_obligatoire, action_post_validation
        )
        SELECT gen_random_uuid(), d.id, s.ordre, s.code, s.nom,
               s.role, s.tv::type_validation, s.delai, true, s.action
        FROM workflow_definition d,
        (VALUES
            (1,'SUCC_DECLARATION','Déclaration de succession','NOTAIRE','approbation',48,NULL),
            (2,'SUCC_INVENTAIRE','Inventaire des biens fonciers','DIRECTEUR_CADASTRE','constat',72,NULL),
            (3,'SUCC_HERITIERS','Identification des héritiers','NOTAIRE','approbation',120,'CHECK_PARTS_100'),
            (4,'SUCC_CCFM_VERIF','Vérification CCFM multi-parcelles','CHEF_CCFM','approbation',48,NULL),
            (5,'SUCC_JUGEMENT','Jugement de partage','JUST_PRESIDENT','signature',336,NULL),
            (6,'SUCC_ACTES','Rédaction actes notariaux','NOTAIRE','signature',120,'CREER_DROITS_HEREDES'),
            (7,'SUCC_TRANSFERT','Transfert des droits','DIRECTEUR_CADASTRE','scellement',48,'TRANSFERER_DROITS'),
            (8,'SUCC_CCFM_MAJ','Mise à jour CCFM héritiers','CHEF_CCFM','scellement',48,'GENERER_CCFM'),
            (9,'SUCC_ANNF','Archivage ANNF','CHEF_DAR','scellement',24,'ARCHIVER_SUCCESSION_ANNF')
        ) AS s(ordre,code,nom,role,tv,delai,action)
        WHERE d.type_workflow = 'TRANSFERT'::type_workflow
          AND d.nom LIKE '%Mutation%'
          AND NOT EXISTS (SELECT 1 FROM workflow_step_def wsd
              WHERE wsd.definition_id = d.id AND wsd.code_etape = s.code)
        ON CONFLICT (definition_id, code_etape) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_successions_en_cours CASCADE")
    op.execute("DROP VIEW IF EXISTS v_divisions_en_cours CASCADE")

    for trigger, table in [
        ("tg_td8_levee_hyp_deblock",    "levee_hypotheque"),
        ("tg_td7_gel_si_indivision",    "indivision"),
        ("tg_td6_usage_invalide_ccfm",  "usage_foncier"),
        ("tg_td5_succession_somme_parts","succession_heritier"),
        ("tg_td4_hyp_autorisation",     "demande_division"),
        ("tg_td3_servitude_publique_block","servitude_publique"),
        ("tg_td2_division_surface",     "division_lot_projet"),
        ("tg_td1_division_bloc_mere",   "demande_division"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    for fn in [
        "tg_fn_division_bloc_mere","tg_fn_division_surface",
        "tg_fn_servitude_publique_block","tg_fn_hyp_autorisation_requise",
        "tg_fn_succession_somme_parts","tg_fn_usage_invalide_ccfm",
        "tg_fn_gel_si_indivision","tg_fn_levee_hypotheque_deblock",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")

    for table in [
        "parcelle_usage","demande_changement_usage","zonage_urbain","usage_foncier",
        "indivision","succession_heritier","succession_parcelle","succession",
        "levee_hypotheque","autorisation_bancaire",
        "servitude_publique","historique_servitude",
        "division_lot_resultat","division_lot_projet","demande_division",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    for name in [
        "statut_division","type_servitude_publique","statut_succession",
        "type_succession","statut_usage","type_usage","statut_autorisation_bancaire",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")
