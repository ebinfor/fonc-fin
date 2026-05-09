"""014_wf30_migration_donnees_foncieres

FONCIER+ v3.4.7 — Migration 014 : Workflow 30 — Migration des Données Foncières
Pipeline national de numérisation et d'intégration des archives foncières.

ANALYSE PRÉALABLE — Tables EXISTANTES utilisées (non dupliquées) :
  migration_historique   → 013 (orchestrateur parent, enrichi ici)
  migration_erreur       → 013 (anomalies import)
  bgu_geojson_master     → 003 (intégration BGU étape 30.10)
  bgu_projection         → 009 (mise à jour projection 30.10)
  annf_archive_links     → 004 (archivage ANNF 30.10)
  conflits_parcellaires  → 003 (détection conflits spatiaux 30.9)
  nicad_registry         → 003 (identifiants 30.6)
  parcelles              → 003 (résultat final pipeline)
  rnaf, rnp_demandes     → 001
  right_holders          → 005 (propriétaires 30.7)
  demandes_ccfm          → 001 (validation CCFM 30.8)
  actes_notaries         → 001 (association actes 30.7)
  parcel_right_version   → 005 (droits propriétaires 30.7)

9 NOUVELLES TABLES (étapes 30.1-30.9) :
  1. migration_source_doc      → 30.1 : sources documentaires collectées
  2. migration_plan_parcellaire→ 30.2 : inventaire des plans physiques
  3. migration_numerisation    → 30.3 : résultats numérisation PDF/A, TIFF, OCR
  4. migration_georeferencement→ 30.4 + 30.4bis : GCPs, SRID, recalage, uniformisation
  5. migration_gcp             → 30.4 : points d'appui géoréférencement (Ground Control Points)
  6. migration_vectorisation   → 30.5 : polygones vectorisés par plan
  7. migration_identifiant     → 30.6 : génération identifiants RR-CC-PR-PPPPPP
  8. migration_proprietaire    → 30.7 : association propriétaires / actes / notaires
  9. migration_conflit_spatial → 30.9 : conflits géométriques détectés lors de la migration

ÉTAPES 30.8 ET 30.10 → fonctions PL/pgSQL (pas de table dédiée) :
  30.8 : valider_ccfm_migration()    → utilise demandes_ccfm + ccfm_verification_log
  30.10: integrer_bgu_annf_migration()→ mise à jour bgu_geojson_master + annf_archive_links

FONCTIONS PL/pgSQL (7 nouvelles) :
  F_MIG1. demarrer_pipeline_migration()     → initialise le pipeline 30.1-30.10
  F_MIG2. avancer_etape_migration()         → transite vers l'étape suivante
  F_MIG3. valider_georeferencement()        → vérifie qualité GCPs (résidu RMS)
  F_MIG4. generer_nicad_migration()         → génère NICAD RR-CC-PR-PPPPPP
  F_MIG5. valider_ccfm_migration()          → lance moteur CCFM sur parcelles migrées
  F_MIG6. integrer_bgu_annf_migration()     → intégration BGU + archivage ANNF
  F_MIG7. rapport_pipeline_migration()      → rapport JSON complet de l'avancement

TRIGGERS (4 nouveaux) :
  TM1. tg_migration_etape_sha256     → SHA-256 sur chaque transition d'étape
  TM2. tg_vectorisation_check_geom   → vérifie validité géométrie vectorisée
  TM3. tg_conflit_spatial_alert      → crée une anomalie_fonciere si conflit critique
  TM4. tg_integration_bgu_auto       → déclenche intégration BGU quand pipeline = complet

Revision ID: 014_wf30_migration_donnees_foncieres
Revises: 013_workflows_strategiques_finaux
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "014_wf30_migration_donnees_foncieres"
down_revision = "013_workflows_strategiques_finaux"
branch_labels = None
depends_on = None

# Étapes du pipeline WF30 (utilisées dans les fonctions)
ETAPES_PIPELINE = [
    "30.1_collecte_sources",
    "30.2_inventaire_plans",
    "30.3_numerisation",
    "30.4_georeferencement",
    "30.4bis_uniformisation_srid",
    "30.5_vectorisation",
    "30.6_generation_identifiants",
    "30.7_association_proprietaires",
    "30.8_validation_ccfm",
    "30.9_detection_conflits",
    "30.10_integration_bgu_annf",
    "TERMINE",
]


def upgrade() -> None:

    # ── ENUMS ────────────────────────────────────────────────────────────────
    for name, values in [
        ("type_source_doc",
         "('journal_officiel','dad_commune','dar_archive','archive_centrale',"
         "'notaire','banque','cadastre_ancien','plan_topographique','autre')"),
        ("statut_source_doc",
         "('identifiee','collectee','numerisee','indexee','integree','rejetee')"),
        ("format_numerisation",
         "('pdf_a','tiff_haute_res','jpeg','png','dwg','shp','geojson','autre')"),
        ("qualite_ocr",
         "('excellente','bonne','acceptable','mediocre','echec')"),
        ("statut_georeferencement",
         "('en_attente','en_cours','valide','rejete','a_revoir')"),
        ("methode_georef",
         "('points_fixes_ign','gps_terrain','ortho_reference','triangulation','autre')"),
        ("srid_niger",
         "('wgs84_4326','utm_32n_32632','utm_33n_32633','rgn_local','autre')"),
        ("statut_vectorisation",
         "('en_attente','en_cours','valide','a_corriger','rejete')"),
        ("type_conflit_spatial",
         "('superposition','gap','topologie_invalide','surface_incoh',"
         "'nicad_doublon','proprietaire_ambigue')"),
        ("gravite_conflit_spatial",
         "('faible','moderee','critique','bloquante')"),
        ("statut_etape_pipeline",
         "('en_attente','en_cours','validee','rejetee','ignoree')"),
    ]:
        op.execute(f"""
            DO $$ BEGIN CREATE TYPE {name} AS ENUM {values};
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """)

    # Enrichir migration_historique avec l'étape courante du pipeline
    op.add_column("migration_historique",
        sa.Column("etape_courante", sa.String(60),
                  server_default="30.1_collecte_sources",
                  comment="Étape courante du pipeline 30.1-30.10"))
    op.add_column("migration_historique",
        sa.Column("srid_source",  sa.String(20),
                  comment="SRID des données sources (avant uniformisation)"))
    op.add_column("migration_historique",
        sa.Column("srid_cible",   sa.String(20), server_default="4326",
                  comment="SRID cible après uniformisation"))
    op.add_column("migration_historique",
        sa.Column("nb_plans_total",     sa.Integer, server_default="0"))
    op.add_column("migration_historique",
        sa.Column("nb_plans_traites",   sa.Integer, server_default="0"))
    op.add_column("migration_historique",
        sa.Column("nb_parcelles_cibles",sa.Integer, server_default="0"))
    op.add_column("migration_historique",
        sa.Column("nb_nicad_generes",   sa.Integer, server_default="0"))
    op.add_column("migration_historique",
        sa.Column("nb_conflits_detectes",sa.Integer, server_default="0"))
    op.add_column("migration_historique",
        sa.Column("nb_ccfm_valides",    sa.Integer, server_default="0"))
    op.add_column("migration_historique",
        sa.Column("workflow_instance_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=True))

    # ════════════════════════════════════════════════════════════════
    # ÉTAPE 30.1 — COLLECTE DES SOURCES DOCUMENTAIRES
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "migration_source_doc",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("migration_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_historique.id"), nullable=False),
        # Identification
        sa.Column("type_source",   sa.Enum(
                  "journal_officiel","dad_commune","dar_archive","archive_centrale",
                  "notaire","banque","cadastre_ancien","plan_topographique","autre",
                  name="type_source_doc"), nullable=False),
        sa.Column("reference_doc", sa.String(300), nullable=False,
                  comment="Référence officielle (numéro JO, cote archive, etc.)"),
        sa.Column("titre",         sa.String(500)),
        sa.Column("date_document", sa.DateTime(timezone=True),
                  comment="Date du document original"),
        sa.Column("periode",       sa.String(20),
                  comment="Période couverte ex: 1960-1980"),
        sa.Column("commune_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("communes.id"), nullable=True),
        sa.Column("region_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("regions.id"), nullable=True),
        # Localisation physique
        sa.Column("lieu_conservation", sa.String(300),
                  comment="Où se trouve physiquement le document"),
        sa.Column("responsable_collecte", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        # Statut
        sa.Column("statut", sa.Enum(
                  "identifiee","collectee","numerisee","indexee","integree","rejetee",
                  name="statut_source_doc"),
                  server_default="identifiee", nullable=False),
        sa.Column("nb_pages",      sa.Integer),
        sa.Column("nb_parcelles_estimees", sa.Integer,
                  comment="Estimation avant traitement"),
        # Anti-doublon
        sa.Column("sha256_document", sa.String(64),
                  comment="SHA-256 du fichier numérisé — déduplication"),
        sa.Column("collecte_at",   sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("created_at",    sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_msd_migration", "migration_source_doc", ["migration_id"])
    op.create_index("ix_msd_statut",    "migration_source_doc", ["statut"])
    op.create_index("ix_msd_type",      "migration_source_doc", ["type_source"])

    # ════════════════════════════════════════════════════════════════
    # ÉTAPE 30.2 — INVENTAIRE DES PLANS PARCELLAIRES
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "migration_plan_parcellaire",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("migration_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_historique.id"), nullable=False),
        sa.Column("source_doc_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_source_doc.id"), nullable=True),
        # Description physique du plan
        sa.Column("reference_plan",sa.String(200), nullable=False),
        sa.Column("echelle",       sa.String(30),
                  comment="ex: 1/2000, 1/5000, 1/10000"),
        sa.Column("format_physique",sa.String(20),
                  comment="A0, A1, A2, rouleau, etc."),
        sa.Column("etat_physique", sa.String(30),
                  comment="excellent|bon|degrade|tres_degrade"),
        sa.Column("date_plan",     sa.DateTime(timezone=True)),
        sa.Column("auteur_plan",   sa.String(200),
                  comment="Bureau ou personne ayant réalisé le plan"),
        sa.Column("commune_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("communes.id"), nullable=True),
        sa.Column("zone_couverte", sa.String(300),
                  comment="Description textuelle de la zone"),
        sa.Column("nb_parcelles_estimees", sa.Integer),
        # Chemin vers la numérisation (rempli à l'étape 30.3)
        sa.Column("fichier_numerise",  sa.String(500),
                  comment="Chemin de stockage du fichier numérisé"),
        sa.Column("statut_traitement", sa.String(30), server_default="en_attente"),
        sa.Column("sha256_plan",       sa.String(64)),
        sa.Column("inventorie_par",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("created_at",        sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_mpp_migration", "migration_plan_parcellaire", ["migration_id"])
    op.create_index("ix_mpp_statut",    "migration_plan_parcellaire", ["statut_traitement"])

    # ════════════════════════════════════════════════════════════════
    # ÉTAPE 30.3 — NUMÉRISATION DES PLANS
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "migration_numerisation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("plan_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_plan_parcellaire.id"),
                  nullable=False, unique=True),
        sa.Column("migration_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_historique.id"), nullable=False),
        # Résultats de numérisation
        sa.Column("format_fichier", sa.Enum(
                  "pdf_a","tiff_haute_res","jpeg","png","dwg","shp","geojson","autre",
                  name="format_numerisation"), nullable=False),
        sa.Column("resolution_dpi", sa.Integer,
                  comment="Résolution en DPI — minimum 300 pour cartographie"),
        sa.Column("nb_pages",       sa.Integer, server_default="1"),
        sa.Column("taille_octets",  sa.Numeric(15, 0)),
        sa.Column("chemin_stockage",sa.String(500), nullable=False,
                  comment="Chemin S3 ou NFS du fichier numérisé"),
        sa.Column("sha256_fichier", sa.String(64), nullable=False,
                  comment="SHA-256 du fichier — intégrité et déduplication"),
        # OCR (si applicable)
        sa.Column("ocr_applique",   sa.Boolean, server_default="false"),
        sa.Column("qualite_ocr",    sa.Enum(
                  "excellente","bonne","acceptable","mediocre","echec",
                  name="qualite_ocr"), nullable=True),
        sa.Column("texte_ocr",      sa.Text,
                  comment="Texte extrait par OCR (références, noms, dates)"),
        sa.Column("confiance_ocr",  sa.Numeric(5, 2),
                  comment="Score de confiance OCR 0-100%"),
        # Métadonnées
        sa.Column("scanner_utilise",sa.String(100)),
        sa.Column("operateur_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("verifie",        sa.Boolean, server_default="false"),
        sa.Column("verifie_par",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("numerise_at",    sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_mn_migration", "migration_numerisation", ["migration_id"])
    op.create_index("ix_mn_sha256",    "migration_numerisation", ["sha256_fichier"])

    # ════════════════════════════════════════════════════════════════
    # ÉTAPE 30.4 / 30.4bis — GÉORÉFÉRENCEMENT + UNIFORMISATION SRID
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "migration_georeferencement",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("plan_id",        postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_plan_parcellaire.id"),
                  nullable=False, unique=True),
        sa.Column("migration_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_historique.id"), nullable=False),
        # Paramètres de géoréférencement
        sa.Column("methode",        sa.Enum(
                  "points_fixes_ign","gps_terrain","ortho_reference",
                  "triangulation","autre", name="methode_georef"), nullable=False),
        sa.Column("nb_gcps",        sa.Integer, nullable=False,
                  comment="Nombre de Ground Control Points utilisés (min 4 recommandé)"),
        # Qualité géométrique
        sa.Column("rms_residuel",   sa.Numeric(10, 6),
                  comment="Résidu RMS (Root Mean Square) en mètres — seuil : < 0.5m"),
        sa.Column("rms_acceptable", sa.Boolean, server_default="false",
                  comment="True si RMS < seuil acceptable (0.5m pour 1/2000)"),
        sa.Column("seuil_rms_m",    sa.Numeric(8, 4), server_default="0.5",
                  comment="Seuil RMS en mètres selon l'échelle du plan"),
        # Systèmes de coordonnées (30.4bis)
        sa.Column("srid_original",  sa.Enum(
                  "wgs84_4326","utm_32n_32632","utm_33n_32633","rgn_local","autre",
                  name="srid_niger"), nullable=False),
        sa.Column("srid_cible",     sa.Enum(
                  "wgs84_4326","utm_32n_32632","utm_33n_32633","rgn_local","autre",
                  name="srid_niger"), server_default="wgs84_4326", nullable=False),
        sa.Column("transformation_appliquee", sa.String(200),
                  comment="Paramètres de la transformation de projection"),
        sa.Column("uniformisation_ok", sa.Boolean, server_default="false",
                  comment="True si SRID uniformisé vers cible nationale"),
        # Étendue géographique résultante
        sa.Column("bbox_xmin",      sa.Numeric(12, 8)),
        sa.Column("bbox_ymin",      sa.Numeric(12, 8)),
        sa.Column("bbox_xmax",      sa.Numeric(12, 8)),
        sa.Column("bbox_ymax",      sa.Numeric(12, 8)),
        # Statut
        sa.Column("statut",         sa.Enum(
                  "en_attente","en_cours","valide","rejete","a_revoir",
                  name="statut_georeferencement"),
                  server_default="en_attente", nullable=False),
        sa.Column("motif_rejet",    sa.Text),
        sa.Column("sha256_resultat",sa.String(64)),
        sa.Column("geo_par",        postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("valide_par",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("georef_at",      sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at",     sa.DateTime(timezone=True)),
        sa.CheckConstraint("nb_gcps >= 4",
                            name="ck_mg_nb_gcps_min"),
    )
    op.create_index("ix_mgeo_migration", "migration_georeferencement", ["migration_id"])
    op.create_index("ix_mgeo_statut",    "migration_georeferencement", ["statut"])

    # Table des points d'appui GCP (Ground Control Points)
    op.create_table(
        "migration_gcp",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("georef_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_georeferencement.id"), nullable=False),
        sa.Column("num_gcp",       sa.Integer, nullable=False,
                  comment="Numéro séquentiel du GCP (1, 2, 3...)"),
        # Coordonnées image (pixels)
        sa.Column("pixel_x",       sa.Numeric(10, 3), nullable=False),
        sa.Column("pixel_y",       sa.Numeric(10, 3), nullable=False),
        # Coordonnées terrain réelles
        sa.Column("terrain_x",     sa.Numeric(14, 8), nullable=False,
                  comment="Longitude (WGS84) ou X projeté"),
        sa.Column("terrain_y",     sa.Numeric(14, 8), nullable=False,
                  comment="Latitude (WGS84) ou Y projeté"),
        sa.Column("terrain_z",     sa.Numeric(10, 3),
                  comment="Altitude en mètres (optionnel)"),
        # Résidu sur ce GCP
        sa.Column("residuel_m",    sa.Numeric(10, 6),
                  comment="Résidu en mètres pour ce GCP"),
        sa.Column("est_valide",    sa.Boolean, server_default="true"),
        sa.Column("description",   sa.String(200),
                  comment="ex: coin parcelle, borne cadastrale, intersection route"),
        sa.UniqueConstraint("georef_id", "num_gcp", name="uq_gcp_georef_num"),
    )
    op.create_index("ix_gcp_georef", "migration_gcp", ["georef_id"])

    # ════════════════════════════════════════════════════════════════
    # ÉTAPE 30.5 — VECTORISATION DES PARCELLES
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "migration_vectorisation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("plan_id",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_plan_parcellaire.id"), nullable=False),
        sa.Column("migration_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_historique.id"), nullable=False),
        sa.Column("georef_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_georeferencement.id"), nullable=True),
        # Résultats vectorisation
        sa.Column("nb_polygones",    sa.Integer, server_default="0"),
        sa.Column("nb_valides",      sa.Integer, server_default="0"),
        sa.Column("nb_invalides",    sa.Integer, server_default="0"),
        sa.Column("surface_totale_m2",sa.Numeric(18, 4)),
        # Méthode
        sa.Column("methode_vecto",   sa.String(50),
                  comment="manuelle|semi_auto|auto_ia|import_shp|import_geojson"),
        sa.Column("logiciel",        sa.String(100),
                  comment="QGIS, ArcGIS, FME, script Python..."),
        sa.Column("chemin_fichier_vecto", sa.String(500),
                  comment="Chemin SHP, GeoJSON ou WKT du résultat"),
        sa.Column("sha256_vecto",    sa.String(64), nullable=False),
        sa.Column("statut",          sa.Enum(
                  "en_attente","en_cours","valide","a_corriger","rejete",
                  name="statut_vectorisation"),
                  server_default="en_attente", nullable=False),
        sa.Column("motif_rejet",     sa.Text),
        sa.Column("vecto_par",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("valide_par",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("vecto_at",        sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_mv_migration", "migration_vectorisation", ["migration_id"])
    op.create_index("ix_mv_statut",    "migration_vectorisation", ["statut"])

    # ════════════════════════════════════════════════════════════════
    # ÉTAPE 30.6 — GÉNÉRATION DES IDENTIFIANTS FONCIERS
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "migration_identifiant",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("migration_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_historique.id"), nullable=False),
        sa.Column("vecto_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_vectorisation.id"), nullable=True),
        # Identifiant généré
        sa.Column("nicad_genere",  sa.String(30), nullable=False,
                  comment="Format RR-CC-PR-PPPPPP généré pour cette parcelle migrée"),
        sa.Column("nicad_source",  sa.String(100),
                  comment="Identifiant dans l'ancien système (si existant)"),
        # Décomposition
        sa.Column("code_region",   sa.String(2), nullable=False),
        sa.Column("code_commune",  sa.String(2), nullable=False),
        sa.Column("code_arrete",   sa.String(2), nullable=False),
        sa.Column("code_parcelle", sa.String(6), nullable=False),
        # Validité
        sa.Column("est_unique",    sa.Boolean, server_default="false",
                  comment="True si pas de doublon dans nicad_registry"),
        sa.Column("doublon_de",    sa.String(30),
                  comment="NICAD existant si doublon détecté"),
        # Lien vers le NICAD définitif
        sa.Column("nicad_registry_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("nicad_registry.id"), nullable=True,
                  comment="Rempli quand le NICAD est inscrit dans le registre"),
        sa.Column("parcelle_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True,
                  comment="Rempli quand la parcelle est créée dans le système"),
        sa.Column("sha256_nicad",  sa.String(64), nullable=False),
        sa.Column("genere_par",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("genere_at",     sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("migration_id", "nicad_genere",
                             name="uq_mi_migration_nicad"),
    )
    op.create_index("ix_mi_migration", "migration_identifiant", ["migration_id"])
    op.create_index("ix_mi_nicad",     "migration_identifiant", ["nicad_genere"])
    op.create_index("ix_mi_unique",    "migration_identifiant", ["est_unique"])

    # ════════════════════════════════════════════════════════════════
    # ÉTAPE 30.7 — ASSOCIATION DES PROPRIÉTAIRES
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "migration_proprietaire",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("migration_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_historique.id"), nullable=False),
        sa.Column("identifiant_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_identifiant.id"), nullable=False),
        # Propriétaire identifié
        sa.Column("proprietaire_nom",  sa.String(300), nullable=False,
                  comment="Nom extrait du document source"),
        sa.Column("proprietaire_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=True,
                  comment="Rempli si correspondance trouvée dans right_holders"),
        # Sources documentaires
        sa.Column("source_acte_ref",   sa.String(300),
                  comment="Référence de l'acte notarié ou titre source"),
        sa.Column("acte_id",           postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("actes_notaries.id"), nullable=True),
        sa.Column("notaire_ref",       sa.String(200)),
        sa.Column("banque_ref",        sa.String(200)),
        sa.Column("date_titre",        sa.DateTime(timezone=True)),
        # Part de propriété
        sa.Column("part_pct",          sa.Numeric(5, 2),
                  comment="Part de propriété en % (si multi-propriétaires)"),
        # Confiance de l'association
        sa.Column("confiance_pct",     sa.Numeric(5, 2),
                  comment="Score de confiance de l'association 0-100%"),
        sa.Column("methode_association",sa.String(50),
                  comment="manuelle|ocr_auto|croisement_registre|ia"),
        sa.Column("association_validee",sa.Boolean, server_default="false"),
        # Droit créé après validation
        sa.Column("droit_id",          postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcel_right_version.id"), nullable=True),
        sa.Column("transfert_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("property_transfers.id"), nullable=True),
        sa.Column("valide_par",        postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("sha256_association",sa.String(64), nullable=False),
        sa.Column("created_at",        sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_mpr_migration",    "migration_proprietaire", ["migration_id"])
    op.create_index("ix_mpr_identifiant",  "migration_proprietaire", ["identifiant_id"])
    op.create_index("ix_mpr_proprietaire", "migration_proprietaire", ["proprietaire_id"])

    # ════════════════════════════════════════════════════════════════
    # ÉTAPE 30.9 — DÉTECTION DES CONFLITS SPATIAUX ET TOPOLOGIQUES
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "migration_conflit_spatial",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("migration_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_historique.id"), nullable=False),
        sa.Column("identifiant_a_id",postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_identifiant.id"), nullable=False),
        sa.Column("identifiant_b_id",postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_identifiant.id"), nullable=True,
                  comment="NULL si conflit avec une parcelle existante"),
        # Parcelle existante en conflit (si applicable)
        sa.Column("parcelle_existante_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("conflit_existant_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conflits_parcellaires.id"), nullable=True),
        # Type et mesure du conflit
        sa.Column("type_conflit",    sa.Enum(
                  "superposition","gap","topologie_invalide","surface_incoh",
                  "nicad_doublon","proprietaire_ambigue",
                  name="type_conflit_spatial"), nullable=False),
        sa.Column("gravite",         sa.Enum(
                  "faible","moderee","critique","bloquante",
                  name="gravite_conflit_spatial"), nullable=False),
        sa.Column("surface_conflit_m2", sa.Numeric(12, 4),
                  comment="Surface en superposition en m²"),
        sa.Column("pct_superposition",  sa.Numeric(6, 3),
                  comment="Pourcentage de la parcelle en superposition"),
        sa.Column("description",     sa.Text, nullable=False),
        # Résolution
        sa.Column("resolu",          sa.Boolean, server_default="false"),
        sa.Column("methode_resolution", sa.String(100),
                  comment="correction_geometrie|suppression_doublon|fusion|jugement|ignore"),
        sa.Column("resolu_par",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("resolu_at",       sa.DateTime(timezone=True)),
        sa.Column("sha256_conflit",  sa.String(64), nullable=False),
        sa.Column("detecte_at",      sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_mcs_migration", "migration_conflit_spatial", ["migration_id"])
    op.create_index("ix_mcs_type",      "migration_conflit_spatial", ["type_conflit"])
    op.create_index("ix_mcs_gravite",   "migration_conflit_spatial", ["gravite"])
    op.create_index("ix_mcs_resolu",    "migration_conflit_spatial", ["resolu"])

    # ════════════════════════════════════════════════════════════════
    # FONCTIONS PL/pgSQL — PIPELINE MIGRATION
    # ════════════════════════════════════════════════════════════════

    # F_MIG1 : Démarrer le pipeline ──────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION demarrer_pipeline_migration(
            p_migration_id  UUID,
            p_code_source   TEXT,
            p_commune_id    UUID,
            p_responsable   UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_mig RECORD;
        BEGIN
            SELECT * INTO v_mig FROM migration_historique WHERE id = p_migration_id;
            IF v_mig IS NULL THEN
                RETURN jsonb_build_object('ok', false, 'message', 'Migration introuvable.');
            END IF;

            UPDATE migration_historique
            SET statut = 'import'::statut_migration,
                etape_courante = '30.1_collecte_sources',
                commune_id = p_commune_id
            WHERE id = p_migration_id;

            INSERT INTO audit_sensitive_ops (
                operation, table_cible, entite_id, module_source,
                niveau_criticite, sha256_op
            ) VALUES (
                'UPDATE', 'migration_historique', p_migration_id, 'migration',
                'eleve',
                encode(sha256(('MIG_START|' || p_migration_id || '|' || NOW())::bytea), 'hex')
            );

            RETURN jsonb_build_object(
                'ok', true,
                'migration_id', p_migration_id,
                'etape_courante', '30.1_collecte_sources',
                'message', 'Pipeline WF30 démarré — étape 30.1 : collecte des sources.'
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_MIG2 : Avancer d'étape ────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION avancer_etape_migration(
            p_migration_id UUID,
            p_acteur_id    UUID DEFAULT NULL
        ) RETURNS JSONB AS $$
        DECLARE
            v_etapes   TEXT[] := ARRAY[
                '30.1_collecte_sources', '30.2_inventaire_plans',
                '30.3_numerisation', '30.4_georeferencement',
                '30.4bis_uniformisation_srid', '30.5_vectorisation',
                '30.6_generation_identifiants', '30.7_association_proprietaires',
                '30.8_validation_ccfm', '30.9_detection_conflits',
                '30.10_integration_bgu_annf', 'TERMINE'
            ];
            v_idx_courant INT;
            v_etape_courante TEXT;
            v_prochaine_etape TEXT;
            v_sha256 TEXT;
        BEGIN
            SELECT etape_courante INTO v_etape_courante
            FROM migration_historique WHERE id = p_migration_id;

            IF v_etape_courante IS NULL THEN
                RETURN jsonb_build_object('ok', false, 'message', 'Migration introuvable.');
            END IF;

            -- Trouver l'index courant
            SELECT i INTO v_idx_courant
            FROM generate_subscripts(v_etapes, 1) AS i
            WHERE v_etapes[i] = v_etape_courante;

            IF v_idx_courant IS NULL OR v_idx_courant >= array_length(v_etapes, 1) THEN
                RETURN jsonb_build_object('ok', false, 'message', 'Pipeline déjà terminé ou étape inconnue.');
            END IF;

            v_prochaine_etape := v_etapes[v_idx_courant + 1];

            -- SHA-256 de la transition
            v_sha256 := encode(
                sha256((p_migration_id::TEXT || '|' || v_etape_courante ||
                        '->' || v_prochaine_etape || '|' || NOW()::TEXT)::bytea),
                'hex'
            );

            UPDATE migration_historique
            SET etape_courante = v_prochaine_etape,
                statut = CASE v_prochaine_etape
                    WHEN 'TERMINE' THEN 'termine'::statut_migration
                    ELSE statut
                END
            WHERE id = p_migration_id;

            -- Audit
            INSERT INTO audit_sensitive_ops (
                operation, table_cible, entite_id, module_source,
                niveau_criticite, sha256_op, valeur_apres
            ) VALUES (
                'UPDATE', 'migration_historique', p_migration_id, 'migration',
                'eleve', v_sha256,
                jsonb_build_object(
                    'etape_precedente', v_etape_courante,
                    'etape_courante', v_prochaine_etape
                )
            );

            RETURN jsonb_build_object(
                'ok', true,
                'etape_precedente', v_etape_courante,
                'etape_courante',   v_prochaine_etape,
                'sha256',           v_sha256,
                'termine',          v_prochaine_etape = 'TERMINE'
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_MIG3 : Valider la qualité du géoréférencement ─────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION valider_georeferencement(
            p_georef_id UUID,
            p_valideur  UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_georef RECORD;
            v_nb_gcps INT;
            v_rms_ok  BOOLEAN;
        BEGIN
            SELECT * INTO v_georef FROM migration_georeferencement WHERE id = p_georef_id;
            IF v_georef IS NULL THEN
                RETURN jsonb_build_object('ok', false, 'message', 'Géoréférencement introuvable.');
            END IF;

            -- Vérifier nombre de GCPs
            SELECT COUNT(*) INTO v_nb_gcps
            FROM migration_gcp WHERE georef_id = p_georef_id AND est_valide = true;

            IF v_nb_gcps < 4 THEN
                RETURN jsonb_build_object(
                    'ok', false,
                    'message', format('GCPs insuffisants : %s valides (minimum 4 requis).', v_nb_gcps)
                );
            END IF;

            -- Vérifier RMS
            v_rms_ok := (v_georef.rms_residuel IS NULL OR
                         v_georef.rms_residuel <= v_georef.seuil_rms_m);

            IF NOT v_rms_ok THEN
                UPDATE migration_georeferencement
                SET statut = 'a_revoir'::statut_georeferencement
                WHERE id = p_georef_id;
                RETURN jsonb_build_object(
                    'ok', false,
                    'message', format('RMS trop élevé : %.4fm > seuil %.4fm. Revoir les GCPs.',
                                      v_georef.rms_residuel, v_georef.seuil_rms_m),
                    'rms', v_georef.rms_residuel
                );
            END IF;

            -- Valider
            UPDATE migration_georeferencement
            SET statut = 'valide'::statut_georeferencement,
                rms_acceptable = true,
                uniformisation_ok = (v_georef.srid_original != v_georef.srid_cible OR true),
                valide_par = p_valideur,
                updated_at = NOW()
            WHERE id = p_georef_id;

            RETURN jsonb_build_object(
                'ok', true,
                'nb_gcps', v_nb_gcps,
                'rms_m', v_georef.rms_residuel,
                'srid_cible', v_georef.srid_cible,
                'message', 'Géoréférencement validé — qualité acceptable.'
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_MIG4 : Générer NICAD format RR-CC-PR-PPPPPP ─────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION generer_nicad_migration(
            p_migration_id  UUID,
            p_vecto_id      UUID,
            p_code_region   TEXT,
            p_code_commune  TEXT,
            p_code_arrete   TEXT,
            p_genere_par    UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_seq        INT;
            v_nicad      TEXT;
            v_sha256     TEXT;
            v_id         UUID := gen_random_uuid();
        BEGIN
            -- Calculer le prochain numéro de séquence pour cette zone
            SELECT COALESCE(MAX(CAST(SUBSTRING(code_parcelle FROM 1) AS INT)), 0) + 1
            INTO v_seq
            FROM migration_identifiant mi
            WHERE mi.migration_id = p_migration_id
              AND mi.code_region = p_code_region
              AND mi.code_commune = p_code_commune
              AND mi.code_arrete = p_code_arrete;

            -- Format : RR-CC-PR-PPPPPP
            v_nicad := p_code_region || '-' || p_code_commune || '-' ||
                       p_code_arrete || '-' || LPAD(v_seq::TEXT, 6, '0');

            -- Vérifier unicité dans nicad_registry
            IF EXISTS (SELECT 1 FROM nicad_registry WHERE nicad = v_nicad) THEN
                -- NICAD déjà existant → marquer comme doublon
                INSERT INTO migration_identifiant (
                    id, migration_id, vecto_id, nicad_genere, nicad_source,
                    code_region, code_commune, code_arrete, code_parcelle,
                    est_unique, doublon_de, sha256_nicad, genere_par
                ) VALUES (
                    v_id, p_migration_id, p_vecto_id, v_nicad, NULL,
                    p_code_region, p_code_commune, p_code_arrete, LPAD(v_seq::TEXT, 6, '0'),
                    false, v_nicad,
                    encode(sha256(v_nicad::bytea), 'hex'), p_genere_par
                );
                RETURN jsonb_build_object(
                    'ok', false, 'nicad', v_nicad, 'est_unique', false,
                    'message', 'NICAD ' || v_nicad || ' existe déjà — doublon enregistré.'
                );
            END IF;

            v_sha256 := encode(sha256(v_nicad::bytea), 'hex');

            INSERT INTO migration_identifiant (
                id, migration_id, vecto_id, nicad_genere,
                code_region, code_commune, code_arrete, code_parcelle,
                est_unique, sha256_nicad, genere_par
            ) VALUES (
                v_id, p_migration_id, p_vecto_id, v_nicad,
                p_code_region, p_code_commune, p_code_arrete, LPAD(v_seq::TEXT, 6, '0'),
                true, v_sha256, p_genere_par
            );

            RETURN jsonb_build_object(
                'ok', true, 'nicad', v_nicad, 'est_unique', true,
                'identifiant_id', v_id,
                'message', 'NICAD ' || v_nicad || ' généré et enregistré.'
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_MIG5 : Validation CCFM sur parcelles migrées ─────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION valider_ccfm_migration(
            p_migration_id UUID,
            p_acteur_id    UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_nb_ccfm_ok   INT := 0;
            v_nb_ccfm_ko   INT := 0;
            v_ident        RECORD;
            v_rapport_ccfm JSONB;
        BEGIN
            -- Lancer le moteur CCFM sur chaque parcelle créée par la migration
            FOR v_ident IN
                SELECT mi.*, mi.parcelle_id AS pid
                FROM migration_identifiant mi
                WHERE mi.migration_id = p_migration_id
                  AND mi.parcelle_id IS NOT NULL
                  AND mi.est_unique = true
            LOOP
                -- Créer une demande CCFM pour la parcelle migrée
                INSERT INTO demandes_ccfm (
                    parcelle_id, statut, type_demande, demandeur_id
                )
                SELECT v_ident.pid, 'EN_VERIFICATION', 'migration', p_acteur_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM demandes_ccfm WHERE parcelle_id = v_ident.pid
                );

                v_nb_ccfm_ok := v_nb_ccfm_ok + 1;
            END LOOP;

            -- Mettre à jour le compteur
            UPDATE migration_historique
            SET nb_ccfm_valides = v_nb_ccfm_ok
            WHERE id = p_migration_id;

            RETURN jsonb_build_object(
                'ok', true,
                'nb_ccfm_lances', v_nb_ccfm_ok,
                'nb_erreurs', v_nb_ccfm_ko,
                'message', format('Validation CCFM lancée sur %s parcelles migrées.', v_nb_ccfm_ok)
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_MIG6 : Intégration BGU + archivage ANNF ───────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION integrer_bgu_annf_migration(
            p_migration_id UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_nb_bgu    INT := 0;
            v_nb_annf   INT := 0;
            v_ident     RECORD;
            v_ida       TEXT;
        BEGIN
            FOR v_ident IN
                SELECT mi.*, p.nicad, p.geom, p.surface_m2
                FROM migration_identifiant mi
                JOIN parcelles p ON p.id = mi.parcelle_id
                WHERE mi.migration_id = p_migration_id
                  AND mi.parcelle_id IS NOT NULL
                  AND mi.est_unique = true
            LOOP
                -- Créer entrée bgu_geojson_master (30.10)
                INSERT INTO bgu_geojson_master (
                    parcelle_id, geojson_data, statut_sync, scelle
                )
                SELECT v_ident.parcelle_id,
                       jsonb_build_object(
                           'type', 'Feature',
                           'properties', jsonb_build_object('nicad', v_ident.nicad),
                           'geometry', jsonb_build_object(
                               'type', 'Polygon',
                               'coordinates', v_ident.geom
                           )
                       ),
                       'synced', false
                ON CONFLICT DO NOTHING;
                v_nb_bgu := v_nb_bgu + 1;

                -- Archivage ANNF
                v_ida := generate_annf_ida(EXTRACT(YEAR FROM NOW())::INT);
                INSERT INTO annf_archive_links (
                    type_archive, entite_id, entite_table,
                    snapshot_json, sha256_snapshot, ida,
                    archive_par, scelle
                ) VALUES (
                    'rnp'::type_archive_annf,
                    v_ident.parcelle_id, 'parcelles',
                    jsonb_build_object(
                        'nicad', v_ident.nicad,
                        'surface_m2', v_ident.surface_m2,
                        'migration_id', p_migration_id,
                        'migration_nicad_source', v_ident.nicad_source
                    ),
                    encode(sha256((v_ident.parcelle_id::TEXT || '|MIGRATION|' || NOW()::TEXT)::bytea), 'hex'),
                    v_ida,
                    (SELECT id FROM users WHERE role = 'ADMIN' LIMIT 1),
                    false
                )
                ON CONFLICT DO NOTHING;
                v_nb_annf := v_nb_annf + 1;
            END LOOP;

            -- Passer à l'étape TERMINEE
            PERFORM avancer_etape_migration(p_migration_id);

            RETURN jsonb_build_object(
                'ok', true,
                'nb_bgu_integres', v_nb_bgu,
                'nb_annf_archives', v_nb_annf,
                'message', format('Intégration BGU+ANNF : %s parcelles intégrées.', v_nb_bgu)
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_MIG7 : Rapport complet du pipeline ─────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION rapport_pipeline_migration(
            p_migration_id UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_mig     RECORD;
            v_nb_src  INT; v_nb_plans INT; v_nb_georef INT;
            v_nb_vecto INT; v_nb_nicad INT; v_nb_prop INT;
            v_nb_conflits INT; v_nb_erreurs_bloquantes INT;
        BEGIN
            SELECT * INTO v_mig FROM migration_historique WHERE id = p_migration_id;
            IF v_mig IS NULL THEN
                RETURN jsonb_build_object('ok', false, 'message', 'Migration introuvable.');
            END IF;

            SELECT COUNT(*) INTO v_nb_src   FROM migration_source_doc WHERE migration_id = p_migration_id;
            SELECT COUNT(*) INTO v_nb_plans FROM migration_plan_parcellaire WHERE migration_id = p_migration_id;
            SELECT COUNT(*) INTO v_nb_georef FROM migration_georeferencement WHERE migration_id = p_migration_id AND statut = 'valide';
            SELECT COUNT(*) INTO v_nb_vecto FROM migration_vectorisation WHERE migration_id = p_migration_id AND statut = 'valide';
            SELECT COUNT(*) INTO v_nb_nicad FROM migration_identifiant WHERE migration_id = p_migration_id AND est_unique = true;
            SELECT COUNT(*) INTO v_nb_prop  FROM migration_proprietaire WHERE migration_id = p_migration_id AND association_validee = true;
            SELECT COUNT(*) INTO v_nb_conflits FROM migration_conflit_spatial WHERE migration_id = p_migration_id AND resolu = false;
            SELECT COUNT(*) INTO v_nb_erreurs_bloquantes
            FROM migration_erreur
            WHERE migration_id = p_migration_id AND niveau IN ('bloquant','critique') AND corrige = false;

            RETURN jsonb_build_object(
                'migration_id',    p_migration_id,
                'code',            v_mig.code_migration,
                'source',          v_mig.source,
                'statut',          v_mig.statut,
                'etape_courante',  v_mig.etape_courante,
                'avancement', jsonb_build_object(
                    '30.1_sources',       v_nb_src,
                    '30.2_plans',         v_nb_plans,
                    '30.3_georef_valides',v_nb_georef,
                    '30.5_vecto_valides', v_nb_vecto,
                    '30.6_nicads_uniques',v_nb_nicad,
                    '30.7_prop_valides',  v_nb_prop,
                    '30.9_conflits_ouverts', v_nb_conflits
                ),
                'qualite', jsonb_build_object(
                    'erreurs_bloquantes',  v_nb_erreurs_bloquantes,
                    'peut_continuer',      v_nb_erreurs_bloquantes = 0 AND v_nb_conflits = 0
                ),
                'compteurs', jsonb_build_object(
                    'nb_lignes_source', v_mig.nb_lignes_source,
                    'nb_importees',     v_mig.nb_importees,
                    'nb_valides',       v_mig.nb_valides,
                    'nb_nicad_generes', v_mig.nb_nicad_generes,
                    'nb_ccfm_valides',  v_mig.nb_ccfm_valides,
                    'nb_conflits',      v_mig.nb_conflits_detectes
                ),
                'rapport_at', NOW()
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ════════════════════════════════════════════════════════════════
    # TRIGGERS
    # ════════════════════════════════════════════════════════════════

    # TM1 : SHA-256 sur chaque avancement d'étape ───────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_migration_etape_sha256()
        RETURNS TRIGGER AS $$
        DECLARE v_sha TEXT;
        BEGIN
            IF OLD.etape_courante IS DISTINCT FROM NEW.etape_courante THEN
                v_sha := encode(
                    sha256((NEW.id::TEXT || '|' || COALESCE(OLD.etape_courante,'START') ||
                            '->' || NEW.etape_courante || '|' || NOW()::TEXT)::bytea),
                    'hex'
                );
                INSERT INTO audit_sensitive_ops (
                    operation, table_cible, entite_id, module_source,
                    niveau_criticite, sha256_op, valeur_apres
                ) VALUES (
                    'UPDATE', 'migration_historique', NEW.id, 'migration', 'normal',
                    v_sha,
                    jsonb_build_object('de', OLD.etape_courante, 'vers', NEW.etape_courante)
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_tm1_migration_etape_sha256
        AFTER UPDATE OF etape_courante ON migration_historique
        FOR EACH ROW
        WHEN (OLD.etape_courante IS DISTINCT FROM NEW.etape_courante)
        EXECUTE FUNCTION tg_fn_migration_etape_sha256();
    """)

    # TM2 : Vérification validité géométrie vectorisée ──────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_vectorisation_check_geom()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.nb_invalides > NEW.nb_polygones * 0.2 THEN
                INSERT INTO migration_erreur (
                    migration_id, description, niveau, donnee_brute
                ) VALUES (
                    NEW.migration_id,
                    format('Vectorisation : %.0f%% de polygones invalides (%s/%s). Révision requise.',
                           NEW.nb_invalides::FLOAT/NULLIF(NEW.nb_polygones,0)*100,
                           NEW.nb_invalides, NEW.nb_polygones),
                    'avertissement'::niveau_erreur_migration,
                    jsonb_build_object('vecto_id', NEW.id,
                                       'nb_invalides', NEW.nb_invalides,
                                       'nb_polygones', NEW.nb_polygones)
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_tm2_vectorisation_check_geom
        AFTER INSERT OR UPDATE OF nb_invalides ON migration_vectorisation
        FOR EACH ROW
        WHEN (NEW.nb_polygones > 0)
        EXECUTE FUNCTION tg_fn_vectorisation_check_geom();
    """)

    # TM3 : Conflit spatial critique → anomalie_fonciere ────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_conflit_spatial_alert()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.gravite IN ('critique','bloquante') AND NEW.resolu = false THEN
                INSERT INTO anomalie_fonciere (
                    type_anomalie, gravite, parcelle_id,
                    entite_type, entite_id,
                    description, valeur_mesuree, sha256_anomalie
                )
                SELECT 'geometrie_invalide'::type_anomalie,
                       NEW.gravite::TEXT::gravite_anomalie,
                       NEW.parcelle_existante_id,
                       'migration_conflit_spatial', NEW.id,
                       'Conflit spatial détecté lors migration: ' || NEW.type_conflit::TEXT ||
                           ' — ' || NEW.description,
                       jsonb_build_object(
                           'migration_id', NEW.migration_id,
                           'type_conflit', NEW.type_conflit,
                           'surface_m2', NEW.surface_conflit_m2
                       ),
                       encode(sha256(('MCS|' || NEW.id)::bytea), 'hex')
                ON CONFLICT (sha256_anomalie) WHERE corrige = false DO NOTHING;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_tm3_conflit_spatial_alert
        AFTER INSERT ON migration_conflit_spatial
        FOR EACH ROW
        WHEN (NEW.gravite IN ('critique','bloquante'))
        EXECUTE FUNCTION tg_fn_conflit_spatial_alert();
    """)

    # TM4 : Intégration BGU automatique quand pipeline terminé ──────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_integration_bgu_auto()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.etape_courante = '30.10_integration_bgu_annf'
               AND OLD.etape_courante IS DISTINCT FROM NEW.etape_courante THEN
                -- Déclencher l'intégration BGU+ANNF
                PERFORM integrer_bgu_annf_migration(NEW.id);
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_tm4_integration_bgu_auto
        AFTER UPDATE OF etape_courante ON migration_historique
        FOR EACH ROW
        WHEN (NEW.etape_courante = '30.10_integration_bgu_annf')
        EXECUTE FUNCTION tg_fn_integration_bgu_auto();
    """)

    # ════════════════════════════════════════════════════════════════
    # VUES
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        CREATE OR REPLACE VIEW v_pipeline_migration AS
        SELECT
            mh.id,
            mh.code_migration,
            mh.source,
            mh.statut,
            mh.etape_courante,
            c.nom_commune,
            -- Avancement par étape
            COUNT(DISTINCT msd.id)  AS nb_sources_collectees,
            COUNT(DISTINCT mpp.id)  AS nb_plans_inventories,
            COUNT(DISTINCT mn.id)   AS nb_plans_numerises,
            COUNT(DISTINCT mg.id) FILTER (WHERE mg.statut = 'valide')
                                    AS nb_georef_valides,
            COUNT(DISTINCT mv.id) FILTER (WHERE mv.statut = 'valide')
                                    AS nb_vecto_valides,
            COUNT(DISTINCT mi.id) FILTER (WHERE mi.est_unique = true)
                                    AS nb_nicads_generes,
            COUNT(DISTINCT mpr.id) FILTER (WHERE mpr.association_validee = true)
                                    AS nb_proprietaires_valides,
            COUNT(DISTINCT mcs.id) FILTER (WHERE mcs.resolu = false)
                                    AS nb_conflits_ouverts,
            -- Qualité globale
            mh.nb_erreurs,
            mh.nb_ccfm_valides,
            -- Taux avancement
            CASE WHEN COUNT(DISTINCT mi.id) > 0
                THEN ROUND(
                    COUNT(DISTINCT mi.id) FILTER (WHERE mi.est_unique = true)::NUMERIC
                    / NULLIF(COUNT(DISTINCT mi.id), 0) * 100, 1
                ) ELSE 0 END        AS taux_nicad_uniques_pct,
            mh.date_lancement
        FROM migration_historique mh
        LEFT JOIN communes c         ON c.id = mh.commune_id
        LEFT JOIN migration_source_doc msd ON msd.migration_id = mh.id
        LEFT JOIN migration_plan_parcellaire mpp ON mpp.migration_id = mh.id
        LEFT JOIN migration_numerisation mn      ON mn.migration_id = mh.id
        LEFT JOIN migration_georeferencement mg  ON mg.migration_id = mh.id
        LEFT JOIN migration_vectorisation mv     ON mv.migration_id = mh.id
        LEFT JOIN migration_identifiant mi       ON mi.migration_id = mh.id
        LEFT JOIN migration_proprietaire mpr     ON mpr.migration_id = mh.id
        LEFT JOIN migration_conflit_spatial mcs  ON mcs.migration_id = mh.id
        WHERE mh.statut != 'termine'
        GROUP BY mh.id, mh.code_migration, mh.source, mh.statut, mh.etape_courante,
                 c.nom_commune, mh.nb_erreurs, mh.nb_ccfm_valides, mh.date_lancement;
    """)

    op.execute("""
        CREATE OR REPLACE VIEW v_qualite_georef AS
        SELECT
            mg.id,
            mpp.reference_plan,
            mpp.echelle,
            mg.methode,
            mg.nb_gcps,
            mg.rms_residuel,
            mg.seuil_rms_m,
            mg.rms_acceptable,
            mg.srid_original,
            mg.srid_cible,
            mg.uniformisation_ok,
            mg.statut,
            -- GCPs détails
            COUNT(gcp.id)           AS nb_gcps_total,
            COUNT(gcp.id) FILTER (WHERE gcp.est_valide) AS nb_gcps_valides,
            AVG(gcp.residuel_m)     AS rms_moyen_gcps,
            MAX(gcp.residuel_m)     AS rms_max_gcp,
            -- Évaluation
            CASE
                WHEN mg.rms_residuel < 0.25 THEN 'Excellente'
                WHEN mg.rms_residuel < 0.50 THEN 'Bonne'
                WHEN mg.rms_residuel < 1.00 THEN 'Acceptable'
                WHEN mg.rms_residuel < 2.00 THEN 'Médiocre'
                ELSE                              'Insuffisante'
            END                     AS classe_qualite
        FROM migration_georeferencement mg
        JOIN migration_plan_parcellaire mpp ON mpp.id = mg.plan_id
        LEFT JOIN migration_gcp gcp ON gcp.georef_id = mg.id
        GROUP BY mg.id, mpp.reference_plan, mpp.echelle, mg.methode, mg.nb_gcps,
                 mg.rms_residuel, mg.seuil_rms_m, mg.rms_acceptable,
                 mg.srid_original, mg.srid_cible, mg.uniformisation_ok, mg.statut;
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_qualite_georef CASCADE")
    op.execute("DROP VIEW IF EXISTS v_pipeline_migration CASCADE")

    for trigger, table in [
        ("tg_tm4_integration_bgu_auto",    "migration_historique"),
        ("tg_tm3_conflit_spatial_alert",   "migration_conflit_spatial"),
        ("tg_tm2_vectorisation_check_geom","migration_vectorisation"),
        ("tg_tm1_migration_etape_sha256",  "migration_historique"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    for fn in [
        "rapport_pipeline_migration","integrer_bgu_annf_migration",
        "valider_ccfm_migration","generer_nicad_migration",
        "valider_georeferencement","avancer_etape_migration",
        "demarrer_pipeline_migration",
        "tg_fn_migration_etape_sha256","tg_fn_vectorisation_check_geom",
        "tg_fn_conflit_spatial_alert","tg_fn_integration_bgu_auto",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID,UUID) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID,TEXT,UUID,UUID) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID,TEXT,TEXT,TEXT,TEXT,UUID) CASCADE")

    # Supprimer colonnes ajoutées à migration_historique
    for col in ["etape_courante","srid_source","srid_cible","nb_plans_total",
                "nb_plans_traites","nb_parcelles_cibles","nb_nicad_generes",
                "nb_conflits_detectes","nb_ccfm_valides","workflow_instance_id"]:
        op.execute(f"ALTER TABLE migration_historique DROP COLUMN IF EXISTS {col}")

    for table in [
        "migration_conflit_spatial","migration_proprietaire",
        "migration_identifiant","migration_vectorisation",
        "migration_gcp","migration_georeferencement",
        "migration_numerisation","migration_plan_parcellaire",
        "migration_source_doc",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    for name in [
        "type_source_doc","statut_source_doc","format_numerisation","qualite_ocr",
        "statut_georeferencement","methode_georef","srid_niger",
        "statut_vectorisation","type_conflit_spatial","gravite_conflit_spatial",
        "statut_etape_pipeline",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")
