"""013_workflows_strategiques_finaux

FONCIER+ v3.4.7 — Migration 013 : Workflows Stratégiques Finaux (WF28-WF33)
Migrations de clôture : cohérence nationale, supervision, résilience.

ANALYSE PRÉALABLE — Ce qui EXISTE et NE PAS dupliquer :
  WF28 LITIGES    → litiges (001) + parcel_disputes (005) + conflit_foncier (012)
                    + transaction_blocks (004) + conflits_parcellaires (003)
                    → ENRICHISSEMENT seulement : litige_urgence + suivi judiciaire unifié
  WF31 ALERTES    → alertes_systeme (001) existe mais sans catégorisation ni auto-détection
                    → ENRICHISSEMENT : anomalie_fonciere + moteur d'alertes auto

GAPS COMBLÉS (9 nouvelles tables) :
  1. revision_cadastrale          → WF29 : révision massive données cadastrales
  2. revision_parcelle            → WF29 : parcelles affectées par une révision
  3. migration_historique         → WF30 : import données foncières anciennes
  4. migration_erreur             → WF30 : anomalies détectées lors de l'import
  5. anomalie_fonciere            → WF31 : enrichit alertes_systeme (001)
  6. indicateur_national          → WF32 : KPIs fonciers nationaux
  7. historique_indicateur        → WF32 : évolution temporelle des KPIs
  8. sauvegarde_systeme           → WF33 : journalisation des sauvegardes
  9. restauration_systeme         → WF33 : traçabilité des restaurations

FONCTIONS PL/pgSQL (8 nouvelles) :
  F_NAT1. detecter_anomalies_foncières()    → moteur d'auto-détection (5 règles)
  F_NAT2. calculer_indicateurs_nationaux()  → calcul KPIs depuis les tables existantes
  F_NAT3. lancer_revision_cadastrale()      → initie une révision + bloque parcelles
  F_NAT4. valider_migration_historique()    → valide un lot de données importées
  F_NAT5. check_integrite_systeme()         → vérification globale cohérence DB
  F_NAT6. enregistrer_sauvegarde()          → journalisation sauvegarde réussie
  F_NAT7. rapport_sante_systeme()           → rapport JSON complet de l'état du système
  F_NAT8. purger_alertes_resolues()         → maintenance : archivage des alertes closes

TRIGGERS (5 nouveaux) :
  TN1. tg_alerte_parcelle_sans_rnaf        — AFTER INSERT parcelles si pas de RNAF
  TN2. tg_alerte_double_propriete          — AFTER INSERT parcel_right_version si somme > 100%
  TN3. tg_revision_bloc_parcelle           — AFTER INSERT revision_parcelle → gel
  TN4. tg_migration_detecte_doublon        — BEFORE INSERT migration_historique → anti-doublon
  TN5. tg_indicateur_auto_apres_mutation   — AFTER INSERT mutation_dossier → recalcul KPI

VUES FINALES :
  v_sante_systeme          → dashboard opérationnel complet
  v_anomalies_actives      → tableau de bord antifraude
  v_indicateurs_nationaux  → KPIs gouvernance foncière
  v_migrations_en_cours    → suivi import données historiques

Revision ID: 013_workflows_strategiques_finaux
Revises: 012_workflows_11_a_15
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "013_workflows_strategiques_finaux"
down_revision = "012_workflows_11_a_15"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ════════════════════════════════════════════════════════════════
    # ENUMS
    # ════════════════════════════════════════════════════════════════
    for name, values in [
        ("statut_revision",
         "('lancee','notification_communes','capture_geometrique','validation_urbanisme',"
         "'verification_ccfm','mise_a_jour_rnp','archivage_annf','terminee','suspendue')"),
        ("type_modification_revision",
         "('correction_geometrie','correction_surface','fusion','division',"
         "'changement_statut','mise_a_jour_nicad','suppression_erreur')"),
        ("statut_migration",
         "('initie','import','nettoyage','validation','attribution_rnaf',"
         "'creation_rnp','archivage','termine','erreur')"),
        ("niveau_erreur_migration",
         "('info','avertissement','erreur','critique','bloquant')"),
        ("type_anomalie",
         "('double_propriete','parcelle_sans_rnaf','rnaf_sans_ccfm','geometrie_invalide',"
         "'mutation_sur_parcelle_gelee','nicad_invalide','surface_incoh','droit_orphelin',"
         "'ccfm_expire','hypotheque_sans_mainlevee')"),
        ("gravite_anomalie",
         "('faible','moderee','elevee','critique','bloquante')"),
        ("type_indicateur",
         "('nb_parcelles_actives','taux_litiges','zones_saturees','delai_moyen_ccfm',"
         "'nb_mutations_mois','nb_ccfm_valides','taux_regularisation',"
         "'nb_expropriations','nb_successions_ouvertes','couverture_bgu')"),
        ("statut_sauvegarde",
         "('en_cours','reussie','echouee','corrompu','restauree')"),
    ]:
        op.execute(f"""
            DO $$ BEGIN CREATE TYPE {name} AS ENUM {values};
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """)

    # ════════════════════════════════════════════════════════════════
    # WF29 — RÉVISION CADASTRALE
    # ════════════════════════════════════════════════════════════════

    op.create_table(
        "revision_cadastrale",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code_revision",  sa.String(50), nullable=False, unique=True,
                  comment="ex: REV-2026-NIA-001"),
        sa.Column("motif",          sa.String(100), nullable=False,
                  comment="revision_urbaine|modernisation|correction_erreurs|nouveau_releve"),
        sa.Column("description",    sa.Text),
        sa.Column("zone_geographique", sa.String(200),
                  comment="Commune, région ou zone géographique concernée"),
        sa.Column("commune_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("communes.id"), nullable=True),
        sa.Column("statut", sa.Enum(
                  "lancee","notification_communes","capture_geometrique",
                  "validation_urbanisme","verification_ccfm","mise_a_jour_rnp",
                  "archivage_annf","terminee","suspendue",
                  name="statut_revision"),
                  server_default="lancee", nullable=False),
        sa.Column("nb_parcelles_cibles",   sa.Integer, server_default="0"),
        sa.Column("nb_parcelles_traitees", sa.Integer, server_default="0"),
        sa.Column("nb_erreurs_detectees",  sa.Integer, server_default="0"),
        sa.Column("date_lancement",        sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("date_fin_prevue",       sa.DateTime(timezone=True)),
        sa.Column("date_terminee",         sa.DateTime(timezone=True)),
        sa.Column("responsable_id",        postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("workflow_id",           postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=True),
        sa.Column("sha256_rapport_final",  sa.String(64)),
        sa.Column("created_at",            sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_rc_statut",   "revision_cadastrale", ["statut"])
    op.create_index("ix_rc_commune",  "revision_cadastrale", ["commune_id"])

    op.create_table(
        "revision_parcelle",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("revision_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("revision_cadastrale.id"), nullable=False),
        sa.Column("parcelle_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("modification_type", sa.Enum(
                  "correction_geometrie","correction_surface","fusion","division",
                  "changement_statut","mise_a_jour_nicad","suppression_erreur",
                  name="type_modification_revision"), nullable=False),
        sa.Column("statut",       sa.String(30), server_default="en_attente"),
        # Valeurs avant/après
        sa.Column("valeur_avant", postgresql.JSONB,
                  comment="Snapshot de la parcelle avant révision"),
        sa.Column("valeur_apres", postgresql.JSONB,
                  comment="Valeur cible après révision"),
        sa.Column("sha256_avant", sa.String(64)),
        sa.Column("sha256_apres", sa.String(64)),
        # Validation
        sa.Column("valide_par",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("valide_at",    sa.DateTime(timezone=True)),
        sa.Column("motif_rejet",  sa.Text),
        sa.Column("created_at",   sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("revision_id","parcelle_id",
                             name="uq_rp_revision_parcelle"),
    )
    op.create_index("ix_rp_revision",  "revision_parcelle", ["revision_id"])
    op.create_index("ix_rp_parcelle",  "revision_parcelle", ["parcelle_id"])

    # ════════════════════════════════════════════════════════════════
    # WF30 — MIGRATION DE DONNÉES HISTORIQUES
    # ════════════════════════════════════════════════════════════════

    op.create_table(
        "migration_historique",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code_migration",  sa.String(50), nullable=False, unique=True,
                  comment="ex: MIG-2026-ARCHIVES-001"),
        sa.Column("source",          sa.String(200), nullable=False,
                  comment="Origine des données: archives_papier|ancien_logiciel|excel|shp"),
        sa.Column("description",     sa.Text),
        sa.Column("periode_couverte",sa.String(100),
                  comment="ex: 1960-2000"),
        sa.Column("commune_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("communes.id"), nullable=True),
        sa.Column("statut", sa.Enum(
                  "initie","import","nettoyage","validation","attribution_rnaf",
                  "creation_rnp","archivage","termine","erreur",
                  name="statut_migration"),
                  server_default="initie", nullable=False),
        # Compteurs
        sa.Column("nb_lignes_source",   sa.Integer, server_default="0"),
        sa.Column("nb_importees",        sa.Integer, server_default="0"),
        sa.Column("nb_valides",          sa.Integer, server_default="0"),
        sa.Column("nb_rejetees",         sa.Integer, server_default="0"),
        sa.Column("nb_erreurs",          sa.Integer, server_default="0"),
        # Anti-doublon : sha256 de la source pour détecter réimport
        sa.Column("sha256_source",       sa.String(64), nullable=False,
                  comment="SHA-256 du fichier source — bloque tout réimport identique"),
        sa.Column("responsable_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("valide_par",          postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("date_lancement",      sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("date_termine",        sa.DateTime(timezone=True)),
        sa.Column("rapport_json",        postgresql.JSONB,
                  comment="Rapport final de la migration"),
        sa.Column("created_at",          sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_mh_statut",  "migration_historique", ["statut"])

    op.create_table(
        "migration_erreur",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("migration_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("migration_historique.id"), nullable=False),
        sa.Column("ligne_source",  sa.Integer,
                  comment="Numéro de ligne dans le fichier source"),
        sa.Column("reference_ancienne", sa.String(200),
                  comment="Identifiant dans l'ancien système"),
        sa.Column("description",   sa.Text, nullable=False),
        sa.Column("niveau", sa.Enum(
                  "info","avertissement","erreur","critique","bloquant",
                  name="niveau_erreur_migration"), nullable=False),
        sa.Column("donnee_brute",  postgresql.JSONB,
                  comment="Données originales ayant provoqué l'erreur"),
        sa.Column("corrige",       sa.Boolean, server_default="false"),
        sa.Column("correction",    sa.Text,
                  comment="Description de la correction appliquée"),
        sa.Column("corrige_par",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("corrige_at",    sa.DateTime(timezone=True)),
        sa.Column("created_at",    sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_me_migration", "migration_erreur", ["migration_id"])
    op.create_index("ix_me_niveau",    "migration_erreur", ["niveau"])
    op.create_index("ix_me_corrige",   "migration_erreur", ["corrige"])

    # ════════════════════════════════════════════════════════════════
    # WF31 — ALERTES ET ANOMALIES (enrichit alertes_systeme 001)
    # ════════════════════════════════════════════════════════════════

    op.create_table(
        "anomalie_fonciere",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("type_anomalie", sa.Enum(
                  "double_propriete","parcelle_sans_rnaf","rnaf_sans_ccfm",
                  "geometrie_invalide","mutation_sur_parcelle_gelee",
                  "nicad_invalide","surface_incoh","droit_orphelin",
                  "ccfm_expire","hypotheque_sans_mainlevee",
                  name="type_anomalie"), nullable=False),
        sa.Column("gravite", sa.Enum(
                  "faible","moderee","elevee","critique","bloquante",
                  name="gravite_anomalie"), nullable=False),
        # Entité concernée
        sa.Column("parcelle_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("entite_type",    sa.String(50)),
        sa.Column("entite_id",      postgresql.UUID(as_uuid=True)),
        sa.Column("nicad",          sa.String(30)),
        sa.Column("description",    sa.Text, nullable=False),
        # Valeurs mesurées
        sa.Column("valeur_mesuree", postgresql.JSONB,
                  comment="Données techniques de l'anomalie"),
        sa.Column("sha256_anomalie",sa.String(64),
                  comment="SHA-256 pour déduplication"),
        # Résolution
        sa.Column("corrige",        sa.Boolean, server_default="false"),
        sa.Column("action_requise", sa.Text),
        sa.Column("corrige_par",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("corrige_at",     sa.DateTime(timezone=True)),
        sa.Column("motif_correction",sa.Text),
        # Lien vers alerte_systeme existante (001)
        sa.Column("alerte_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("alertes_systeme.id"), nullable=True),
        sa.Column("detecte_at",     sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_af_type",     "anomalie_fonciere", ["type_anomalie"])
    op.create_index("ix_af_gravite",  "anomalie_fonciere", ["gravite"])
    op.create_index("ix_af_corrige",  "anomalie_fonciere", ["corrige"])
    op.create_index("ix_af_parcelle", "anomalie_fonciere", ["parcelle_id"])
    # Index pour déduplication
    op.execute("""
        CREATE UNIQUE INDEX ix_af_dedup
        ON anomalie_fonciere (sha256_anomalie)
        WHERE corrige = false;
    """)

    # ════════════════════════════════════════════════════════════════
    # WF32 — INDICATEURS NATIONAUX
    # ════════════════════════════════════════════════════════════════

    op.create_table(
        "indicateur_national",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("type_indicateur", sa.Enum(
                  "nb_parcelles_actives","taux_litiges","zones_saturees",
                  "delai_moyen_ccfm","nb_mutations_mois","nb_ccfm_valides",
                  "taux_regularisation","nb_expropriations",
                  "nb_successions_ouvertes","couverture_bgu",
                  name="type_indicateur"), nullable=False),
        sa.Column("valeur",          sa.Numeric(18, 4), nullable=False),
        sa.Column("unite",           sa.String(30),
                  comment="nombre|pourcentage|jours|m2|fcfa"),
        sa.Column("region_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("regions.id"), nullable=True,
                  comment="NULL = indicateur national"),
        sa.Column("commune_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("communes.id"), nullable=True),
        sa.Column("periode",         sa.String(20), nullable=False,
                  comment="YYYY-MM ou YYYY pour période mensuelle/annuelle"),
        sa.Column("date_calcul",     sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("methode_calcul",  sa.Text,
                  comment="Description de la requête SQL utilisée"),
        sa.Column("sha256_calcul",   sa.String(64),
                  comment="SHA-256 du résultat pour détecter manipulation"),
        sa.Column("calcule_par",     sa.String(50), server_default="SYSTEME"),
        sa.UniqueConstraint("type_indicateur","periode","region_id","commune_id",
                             name="uq_in_type_periode_geo"),
    )
    op.create_index("ix_in_type",    "indicateur_national", ["type_indicateur"])
    op.create_index("ix_in_periode", "indicateur_national", ["periode"])
    op.create_index("ix_in_region",  "indicateur_national", ["region_id"])

    op.create_table(
        "historique_indicateur",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("indicateur_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("indicateur_national.id"), nullable=False),
        sa.Column("ancienne_valeur", sa.Numeric(18, 4), nullable=False),
        sa.Column("nouvelle_valeur", sa.Numeric(18, 4), nullable=False),
        sa.Column("variation_pct",   sa.Numeric(8, 3),
                  comment="Variation en % par rapport à la période précédente"),
        sa.Column("date_modification", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("raison",          sa.Text),
    )
    op.create_index("ix_hi_indicateur","historique_indicateur",["indicateur_id"])

    # ════════════════════════════════════════════════════════════════
    # WF33 — PLAN DE CONTINUITÉ
    # ════════════════════════════════════════════════════════════════

    op.create_table(
        "sauvegarde_systeme",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("type_sauvegarde", sa.String(30), nullable=False,
                  comment="complete|incrementale|differentielle|wal"),
        sa.Column("statut", sa.Enum(
                  "en_cours","reussie","echouee","corrompu","restauree",
                  name="statut_sauvegarde"),
                  server_default="en_cours", nullable=False),
        sa.Column("emplacement",     sa.String(500), nullable=False,
                  comment="Chemin S3, NFS ou autre stockage"),
        sa.Column("emplacement_secondaire", sa.String(500),
                  comment="Réplication géographique (site DR)"),
        sa.Column("taille_octets",   sa.Numeric(18, 0)),
        sa.Column("nb_tables",       sa.Integer),
        sa.Column("nb_enregistrements", sa.Numeric(18, 0)),
        sa.Column("sha256_archive",  sa.String(64),
                  comment="SHA-256 de l'archive — vérification intégrité"),
        sa.Column("chiffrement",     sa.String(30), server_default="AES-256",
                  comment="Algorithme de chiffrement de l'archive"),
        sa.Column("duree_secondes",  sa.Integer,
                  comment="Durée de la sauvegarde"),
        sa.Column("date_sauvegarde", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("retention_jours", sa.Integer, server_default="90",
                  comment="Durée de rétention en jours"),
        sa.Column("expire_at",       sa.DateTime(timezone=True)),
        sa.Column("declenchee_par",  sa.String(50), server_default="CRON",
                  comment="CRON|MANUEL|INCIDENT"),
        sa.Column("erreur",          sa.Text,
                  comment="Message d'erreur si statut=echouee"),
        sa.Column("created_at",      sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_ss_statut",  "sauvegarde_systeme", ["statut"])
    op.create_index("ix_ss_date",    "sauvegarde_systeme", ["date_sauvegarde"])

    op.create_table(
        "restauration_systeme",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("sauvegarde_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sauvegarde_systeme.id"), nullable=False),
        sa.Column("motif",           sa.String(100), nullable=False,
                  comment="panne|cyberattaque|test_dr|corruption|erreur_humaine"),
        sa.Column("statut",          sa.String(30), server_default="en_cours"),
        sa.Column("point_restauration", sa.DateTime(timezone=True),
                  comment="Point dans le temps cible (PITR)"),
        sa.Column("duree_secondes",  sa.Integer),
        sa.Column("tables_restaurees", postgresql.JSONB,
                  comment="Liste des tables restaurées"),
        sa.Column("nb_enregistrements_restaures", sa.Numeric(18, 0)),
        sa.Column("perte_donnees_estimee", sa.Interval,
                  comment="Estimation de la perte de données (RPO effectif)"),
        sa.Column("valide_apres",    sa.Boolean, server_default="false",
                  comment="True si validation intégrité réussie après restauration"),
        sa.Column("resultat",        postgresql.JSONB,
                  comment="Rapport complet de la restauration"),
        sa.Column("autorisee_par",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("date_restauration", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("created_at",      sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_rs_sauvegarde","restauration_systeme",["sauvegarde_id"])

    # ════════════════════════════════════════════════════════════════
    # FONCTIONS PL/pgSQL — MOTEUR NATIONAL
    # ════════════════════════════════════════════════════════════════

    # F_NAT1 : Détection automatique d'anomalies ─────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION detecter_anomalies_foncieres()
        RETURNS INT AS $$
        DECLARE
            v_nb_detectees INT := 0;
            v_sha          TEXT;
            v_desc         TEXT;
        BEGIN
            -- RÈGLE 1 : Parcelles actives sans RNAF associé
            INSERT INTO anomalie_fonciere (
                type_anomalie, gravite, parcelle_id, nicad,
                description, sha256_anomalie
            )
            SELECT 'parcelle_sans_rnaf'::type_anomalie,
                   'elevee'::gravite_anomalie,
                   p.id, p.nicad,
                   'Parcelle active sans lien RNAF via rnp_parcelle_link.',
                   encode(sha256(('PSR|' || p.id)::bytea), 'hex')
            FROM parcelles p
            WHERE p.statut = 'active'
              AND NOT EXISTS (SELECT 1 FROM rnp_parcelle_link rpl WHERE rpl.parcelle_id = p.id)
              AND NOT EXISTS (
                SELECT 1 FROM anomalie_fonciere af
                WHERE af.parcelle_id = p.id
                  AND af.type_anomalie = 'parcelle_sans_rnaf'
                  AND af.corrige = false
              )
            ON CONFLICT (sha256_anomalie) WHERE corrige = false DO NOTHING;
            GET DIAGNOSTICS v_nb_detectees = ROW_COUNT;

            -- RÈGLE 2 : RNAF publiés sans CCFM valide
            INSERT INTO anomalie_fonciere (
                type_anomalie, gravite, entite_type, entite_id,
                description, sha256_anomalie
            )
            SELECT 'rnaf_sans_ccfm'::type_anomalie,
                   'moderee'::gravite_anomalie,
                   'rnaf', r.id,
                   'RNAF publié sans certification CCFM valide associée.',
                   encode(sha256(('RSC|' || r.id)::bytea), 'hex')
            FROM rnaf r
            JOIN rnaf_workflow rw ON rw.rnaf_id = r.id
            WHERE rw.statut = 'publie'
              AND NOT EXISTS (
                SELECT 1 FROM rnp_parcelle_link rpl
                JOIN demandes_ccfm dc ON dc.parcelle_id = rpl.parcelle_id
                WHERE rpl.rnaf_id = r.id
                  AND dc.statut IN ('CERTIFIE','SCELLE','VALIDE')
              )
            ON CONFLICT (sha256_anomalie) WHERE corrige = false DO NOTHING;

            -- RÈGLE 3 : Droits de propriété actifs totalisant ≠ 100%
            INSERT INTO anomalie_fonciere (
                type_anomalie, gravite, parcelle_id, nicad,
                description, valeur_mesuree, sha256_anomalie
            )
            SELECT 'double_propriete'::type_anomalie,
                   'critique'::gravite_anomalie,
                   p.id, p.nicad,
                   format('Somme des droits de propriété actifs = %.2f%% (≠ 100%%)', somme_pct),
                   jsonb_build_object('somme_pct', somme_pct, 'nb_droits', nb_droits),
                   encode(sha256(('DP|' || p.id || '|' || somme_pct::TEXT)::bytea), 'hex')
            FROM (
                SELECT prv.parcelle_id,
                       SUM(prv.pourcentage_droit) AS somme_pct,
                       COUNT(*) AS nb_droits
                FROM parcel_right_version prv
                WHERE prv.type_droit = 'propriete' AND prv.statut = 'actif'
                GROUP BY prv.parcelle_id
                HAVING ABS(SUM(prv.pourcentage_droit) - 100) > 0.5
            ) agreg
            JOIN parcelles p ON p.id = agreg.parcelle_id
            WHERE p.statut = 'active'
            ON CONFLICT (sha256_anomalie) WHERE corrige = false DO NOTHING;

            -- RÈGLE 4 : NiCAD avec format invalide sur parcelles actives
            INSERT INTO anomalie_fonciere (
                type_anomalie, gravite, parcelle_id, nicad,
                description, sha256_anomalie
            )
            SELECT 'nicad_invalide'::type_anomalie,
                   'critique'::gravite_anomalie,
                   p.id, p.nicad,
                   'Format NICAD invalide : ne respecte pas RR-CC-ARR-III-PPP[S].',
                   encode(sha256(('NI|' || p.id)::bytea), 'hex')
            FROM parcelles p
            WHERE p.statut = 'active'
              AND p.nicad !~ '^[0-9]{2}-[0-9]{2}-[A-Z]{2}[0-9]{4}-[0-9]{3}-[A-Z0-9]{1,3}[ab]?$'
            ON CONFLICT (sha256_anomalie) WHERE corrige = false DO NOTHING;

            -- RÈGLE 5 : Conflits géométriques critiques non résolus > 30 jours
            INSERT INTO anomalie_fonciere (
                type_anomalie, gravite, parcelle_id, nicad,
                description, sha256_anomalie
            )
            SELECT 'geometrie_invalide'::type_anomalie,
                   'bloquante'::gravite_anomalie,
                   cp.parcelle_a_id,
                   p.nicad,
                   format('Conflit géométrique critique non résolu depuis %s jours.',
                          EXTRACT(DAY FROM NOW() - cp.detecte_at)::INT),
                   encode(sha256(('GI|' || cp.id)::bytea), 'hex')
            FROM conflits_parcellaires cp
            JOIN parcelles p ON p.id = cp.parcelle_a_id
            WHERE cp.gravite = 'critique'
              AND cp.statut IN ('ouvert','bloque')
              AND cp.detecte_at < NOW() - INTERVAL '30 days'
            ON CONFLICT (sha256_anomalie) WHERE corrige = false DO NOTHING;

            -- Total détecté
            SELECT COUNT(*) INTO v_nb_detectees
            FROM anomalie_fonciere WHERE corrige = false;

            RETURN v_nb_detectees;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_NAT2 : Calcul des indicateurs nationaux ──────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION calculer_indicateurs_nationaux(
            p_periode TEXT DEFAULT TO_CHAR(NOW(), 'YYYY-MM')
        ) RETURNS JSONB AS $$
        DECLARE
            v_nb_parcelles     NUMERIC;
            v_nb_litiges       NUMERIC;
            v_taux_litiges     NUMERIC;
            v_nb_ccfm_valides  NUMERIC;
            v_delai_ccfm       NUMERIC;
            v_nb_mutations     NUMERIC;
            v_couverture_bgu   NUMERIC;
            v_result           JSONB;
        BEGIN
            -- KPI 1 : Parcelles actives
            SELECT COUNT(*) INTO v_nb_parcelles
            FROM parcelles WHERE statut = 'active';

            -- KPI 2 : Taux de litiges (litiges ouverts / parcelles actives)
            SELECT COUNT(*) INTO v_nb_litiges
            FROM litiges WHERE statut NOT IN ('clos','abandonne');
            v_taux_litiges := CASE WHEN v_nb_parcelles > 0
                THEN ROUND(v_nb_litiges::NUMERIC / v_nb_parcelles * 100, 3)
                ELSE 0 END;

            -- KPI 3 : CCFM valides
            SELECT COUNT(*) INTO v_nb_ccfm_valides
            FROM demandes_ccfm WHERE statut IN ('CERTIFIE','SCELLE','VALIDE');

            -- KPI 4 : Délai moyen CCFM (jours, sur les 3 derniers mois)
            SELECT COALESCE(AVG(
                EXTRACT(DAY FROM (updated_at - created_at))
            ), 0) INTO v_delai_ccfm
            FROM demandes_ccfm
            WHERE statut IN ('CERTIFIE','SCELLE','VALIDE')
              AND created_at > NOW() - INTERVAL '90 days';

            -- KPI 5 : Mutations du mois en cours
            SELECT COUNT(*) INTO v_nb_mutations
            FROM mutation_dossier
            WHERE TO_CHAR(created_at, 'YYYY-MM') = p_periode;

            -- KPI 6 : Couverture BGU (% parcelles avec projection BGU valide)
            SELECT CASE WHEN v_nb_parcelles > 0 THEN
                ROUND(COUNT(bp.parcelle_id)::NUMERIC / v_nb_parcelles * 100, 2)
                ELSE 0 END INTO v_couverture_bgu
            FROM bgu_projection bp WHERE bp.projection_valide = true;

            -- Insérer / mettre à jour les indicateurs
            INSERT INTO indicateur_national (
                type_indicateur, valeur, unite, periode, date_calcul,
                sha256_calcul, calcule_par
            ) VALUES
            ('nb_parcelles_actives',  v_nb_parcelles,    'nombre',     p_periode, NOW(),
             encode(sha256(('NPA|' || v_nb_parcelles)::bytea), 'hex'), 'SYSTEME'),
            ('taux_litiges',          v_taux_litiges,    'pourcentage',p_periode, NOW(),
             encode(sha256(('TL|' || v_taux_litiges)::bytea), 'hex'), 'SYSTEME'),
            ('nb_ccfm_valides',       v_nb_ccfm_valides, 'nombre',     p_periode, NOW(),
             encode(sha256(('NCV|' || v_nb_ccfm_valides)::bytea), 'hex'), 'SYSTEME'),
            ('delai_moyen_ccfm',      v_delai_ccfm,      'jours',      p_periode, NOW(),
             encode(sha256(('DMC|' || v_delai_ccfm)::bytea), 'hex'), 'SYSTEME'),
            ('nb_mutations_mois',     v_nb_mutations,    'nombre',     p_periode, NOW(),
             encode(sha256(('NMM|' || v_nb_mutations)::bytea), 'hex'), 'SYSTEME'),
            ('couverture_bgu',        v_couverture_bgu,  'pourcentage',p_periode, NOW(),
             encode(sha256(('CB|' || v_couverture_bgu)::bytea), 'hex'), 'SYSTEME')
            ON CONFLICT (type_indicateur, periode, region_id, commune_id)
            DO UPDATE SET valeur = EXCLUDED.valeur, date_calcul = NOW();

            v_result := jsonb_build_object(
                'periode', p_periode,
                'nb_parcelles_actives', v_nb_parcelles,
                'taux_litiges_pct', v_taux_litiges,
                'nb_ccfm_valides', v_nb_ccfm_valides,
                'delai_moyen_ccfm_jours', v_delai_ccfm,
                'nb_mutations_mois', v_nb_mutations,
                'couverture_bgu_pct', v_couverture_bgu,
                'calcule_at', NOW()
            );
            RETURN v_result;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_NAT3 : Lancer une révision cadastrale ────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION lancer_revision_cadastrale(
            p_code_revision    TEXT,
            p_motif            TEXT,
            p_commune_id       UUID,
            p_responsable_id   UUID
        ) RETURNS UUID AS $$
        DECLARE
            v_revision_id  UUID;
            v_nb_parcelles INT;
        BEGIN
            v_revision_id := gen_random_uuid();

            INSERT INTO revision_cadastrale (
                id, code_revision, motif, commune_id, statut,
                responsable_id, date_lancement
            ) VALUES (
                v_revision_id, p_code_revision, p_motif,
                p_commune_id, 'lancee'::statut_revision,
                p_responsable_id, NOW()
            );

            -- Identifier les parcelles à réviser dans la commune
            INSERT INTO revision_parcelle (
                revision_id, parcelle_id, modification_type, statut
            )
            SELECT v_revision_id, p.id, 'correction_geometrie'::type_modification_revision,
                   'en_attente'
            FROM parcelles p
            JOIN ilots i        ON i.id = p.ilot_id
            JOIN lotissements l ON l.id = i.lotissement_id
            JOIN arretes_urbanisme au ON au.id = l.arrete_id
            WHERE au.commune_id = p_commune_id
              AND p.statut = 'active'
            ON CONFLICT (revision_id, parcelle_id) DO NOTHING;

            GET DIAGNOSTICS v_nb_parcelles = ROW_COUNT;

            UPDATE revision_cadastrale
            SET nb_parcelles_cibles = v_nb_parcelles
            WHERE id = v_revision_id;

            RETURN v_revision_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_NAT4 : Validation d'une migration historique ─────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION valider_migration_historique(
            p_migration_id UUID,
            p_valideur_id  UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_mig     RECORD;
            v_nb_err_bloquants INT;
        BEGIN
            SELECT * INTO v_mig FROM migration_historique WHERE id = p_migration_id;
            IF v_mig IS NULL THEN
                RETURN jsonb_build_object('ok', false, 'message', 'Migration introuvable.');
            END IF;

            -- Vérifier absence d'erreurs bloquantes
            SELECT COUNT(*) INTO v_nb_err_bloquants
            FROM migration_erreur me
            WHERE me.migration_id = p_migration_id
              AND me.niveau IN ('bloquant','critique')
              AND me.corrige = false;

            IF v_nb_err_bloquants > 0 THEN
                RETURN jsonb_build_object(
                    'ok', false,
                    'message', format('%s erreur(s) bloquante(s) non corrigée(s) — validation impossible.',
                                      v_nb_err_bloquants),
                    'nb_erreurs_bloquantes', v_nb_err_bloquants
                );
            END IF;

            UPDATE migration_historique
            SET statut = 'validation'::statut_migration,
                valide_par = p_valideur_id
            WHERE id = p_migration_id;

            RETURN jsonb_build_object(
                'ok', true,
                'message', 'Migration validée — passage en attribution RNAF.',
                'nb_erreurs_restantes', (
                    SELECT COUNT(*) FROM migration_erreur
                    WHERE migration_id = p_migration_id AND corrige = false
                )
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_NAT5 : Vérification d'intégrité globale ──────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION check_integrite_systeme()
        RETURNS JSONB AS $$
        DECLARE
            v_nb_anomalies     INT;
            v_nb_bloquants     INT;
            v_nb_blocks_actifs INT;
            v_nb_geles         INT;
            v_nb_sans_version  INT;
            v_derniere_sauveg  TIMESTAMPTZ;
            v_heures_sans_sauveg NUMERIC;
        BEGIN
            -- Détecter anomalies
            PERFORM detecter_anomalies_foncieres();

            SELECT COUNT(*) INTO v_nb_anomalies FROM anomalie_fonciere WHERE corrige = false;
            SELECT COUNT(*) INTO v_nb_bloquants
            FROM anomalie_fonciere WHERE gravite IN ('critique','bloquante') AND corrige = false;

            -- Blocks actifs
            SELECT COUNT(*) INTO v_nb_blocks_actifs
            FROM transaction_blocks WHERE statut = 'actif';

            -- Parcelles gelées
            SELECT COUNT(*) INTO v_nb_geles FROM parcelles WHERE is_gele = true;

            -- Parcelles sans version active
            SELECT COUNT(*) INTO v_nb_sans_version
            FROM parcelles p WHERE p.statut = 'active' AND p.version_active_id IS NULL;

            -- Dernière sauvegarde réussie
            SELECT date_sauvegarde INTO v_derniere_sauveg
            FROM sauvegarde_systeme WHERE statut = 'reussie'
            ORDER BY date_sauvegarde DESC LIMIT 1;

            v_heures_sans_sauveg := CASE WHEN v_derniere_sauveg IS NOT NULL
                THEN EXTRACT(EPOCH FROM (NOW() - v_derniere_sauveg)) / 3600
                ELSE 9999 END;

            RETURN jsonb_build_object(
                'timestamp',            NOW(),
                'sante_globale',        CASE
                    WHEN v_nb_bloquants > 0 OR v_heures_sans_sauveg > 24 THEN 'CRITIQUE'
                    WHEN v_nb_anomalies > 10                               THEN 'DEGRADEE'
                    ELSE                                                        'NORMALE'
                END,
                'anomalies', jsonb_build_object(
                    'total',     v_nb_anomalies,
                    'bloquantes',v_nb_bloquants
                ),
                'parcelles', jsonb_build_object(
                    'blocks_actifs',   v_nb_blocks_actifs,
                    'gelees',          v_nb_geles,
                    'sans_version',    v_nb_sans_version
                ),
                'continuite', jsonb_build_object(
                    'derniere_sauvegarde',   v_derniere_sauveg,
                    'heures_sans_sauvegarde',v_heures_sans_sauveg,
                    'alerte_sauvegarde',     v_heures_sans_sauveg > 24
                )
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_NAT6-F_NAT8 (compactes) ───────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION enregistrer_sauvegarde(
            p_type TEXT, p_emplacement TEXT, p_sha256 TEXT,
            p_taille NUMERIC DEFAULT NULL
        ) RETURNS UUID AS $$
        DECLARE v_id UUID := gen_random_uuid();
        BEGIN
            INSERT INTO sauvegarde_systeme (
                id, type_sauvegarde, statut, emplacement,
                sha256_archive, taille_octets, date_sauvegarde
            ) VALUES (
                v_id, p_type, 'reussie'::statut_sauvegarde,
                p_emplacement, p_sha256, p_taille, NOW()
            );
            RETURN v_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION rapport_sante_systeme()
        RETURNS JSONB AS $$
        DECLARE
            v_kpis    JSONB;
            v_integrite JSONB;
        BEGIN
            v_kpis     := calculer_indicateurs_nationaux();
            v_integrite := check_integrite_systeme();
            RETURN jsonb_build_object(
                'timestamp',  NOW(),
                'integrite',  v_integrite,
                'kpis',       v_kpis,
                'version',    '3.4.7',
                'nb_migrations', (SELECT COUNT(*) FROM migration_historique),
                'nb_revisions',  (SELECT COUNT(*) FROM revision_cadastrale WHERE statut != 'terminee')
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION purger_alertes_resolues(
            p_older_than_days INT DEFAULT 90
        ) RETURNS INT AS $$
        DECLARE v_nb INT;
        BEGIN
            UPDATE anomalie_fonciere
            SET corrige = true, corrige_at = NOW(),
                motif_correction = 'Archivage automatique (délai ' || p_older_than_days || ' jours)'
            WHERE corrige = false
              AND gravite IN ('faible','moderee')
              AND detecte_at < NOW() - (p_older_than_days || ' days')::INTERVAL;
            GET DIAGNOSTICS v_nb = ROW_COUNT;
            RETURN v_nb;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ════════════════════════════════════════════════════════════════
    # TRIGGERS
    # ════════════════════════════════════════════════════════════════

    # TN1 : Alerte parcelle sans RNAF à l'insertion ──────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_alerte_parcelle_sans_rnaf()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.statut = 'active' AND NOT EXISTS (
                SELECT 1 FROM rnp_parcelle_link WHERE parcelle_id = NEW.id
            ) THEN
                INSERT INTO anomalie_fonciere (
                    type_anomalie, gravite, parcelle_id, nicad,
                    description, sha256_anomalie
                ) VALUES (
                    'parcelle_sans_rnaf'::type_anomalie,
                    'elevee'::gravite_anomalie,
                    NEW.id, NEW.nicad,
                    'Nouvelle parcelle active sans lien RNAF.',
                    encode(sha256(('PSR|' || NEW.id)::bytea), 'hex')
                ) ON CONFLICT (sha256_anomalie) WHERE corrige = false DO NOTHING;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_tn1_alerte_parcelle_sans_rnaf
        AFTER INSERT ON parcelles
        FOR EACH ROW WHEN (NEW.statut = 'active')
        EXECUTE FUNCTION tg_fn_alerte_parcelle_sans_rnaf();
    """)

    # TN2 : Alerte double propriété ──────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_alerte_double_propriete()
        RETURNS TRIGGER AS $$
        DECLARE
            v_somme NUMERIC;
            v_nicad TEXT;
        BEGIN
            IF NEW.type_droit != 'propriete' OR NEW.statut != 'actif' THEN
                RETURN NEW;
            END IF;
            SELECT COALESCE(SUM(pourcentage_droit),0) INTO v_somme
            FROM parcel_right_version
            WHERE parcelle_id = NEW.parcelle_id AND type_droit = 'propriete'
              AND statut = 'actif';
            SELECT nicad INTO v_nicad FROM parcelles WHERE id = NEW.parcelle_id;
            IF v_somme > 100.5 THEN
                INSERT INTO anomalie_fonciere (
                    type_anomalie, gravite, parcelle_id, nicad,
                    description, valeur_mesuree, sha256_anomalie
                ) VALUES (
                    'double_propriete'::type_anomalie,
                    'critique'::gravite_anomalie,
                    NEW.parcelle_id, v_nicad,
                    format('Somme droits propriété = %.2f%% > 100%%.', v_somme),
                    jsonb_build_object('somme', v_somme),
                    encode(sha256(('DP2|' || NEW.parcelle_id || '|' || v_somme)::bytea), 'hex')
                ) ON CONFLICT (sha256_anomalie) WHERE corrige = false DO NOTHING;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_tn2_alerte_double_propriete
        AFTER INSERT OR UPDATE OF pourcentage_droit, statut ON parcel_right_version
        FOR EACH ROW
        EXECUTE FUNCTION tg_fn_alerte_double_propriete();
    """)

    # TN3 : Révision → gel des parcelles ─────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_revision_bloc_parcelle()
        RETURNS TRIGGER AS $$
        DECLARE
            v_nicad TEXT;
        BEGIN
            SELECT nicad INTO v_nicad FROM parcelles WHERE id = NEW.parcelle_id;
            INSERT INTO transaction_blocks (
                parcelle_id, type_blockage, statut, motif, sha256_motif, pose_par
            ) SELECT NEW.parcelle_id, 'admin_manuel'::type_blockage,
                'actif'::statut_blockage,
                'RÉVISION CADASTRALE: parcelle en cours de révision',
                encode(sha256(('REV|' || NEW.parcelle_id)::bytea), 'hex'),
                (SELECT id FROM users WHERE role = 'ADMIN' LIMIT 1)
            WHERE NOT EXISTS (
                SELECT 1 FROM transaction_blocks
                WHERE parcelle_id = NEW.parcelle_id AND statut = 'actif'
                  AND motif LIKE '%RÉVISION%'
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_tn3_revision_bloc_parcelle
        AFTER INSERT ON revision_parcelle
        FOR EACH ROW EXECUTE FUNCTION tg_fn_revision_bloc_parcelle();
    """)

    # TN4 : Migration — anti-doublon source ──────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_migration_anti_doublon()
        RETURNS TRIGGER AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM migration_historique
                WHERE sha256_source = NEW.sha256_source AND id != NEW.id
            ) THEN
                RAISE EXCEPTION
                    'MIGRATION [TN4]: Source identique déjà importée (sha256: %). '
                    'Double import bloqué pour éviter corruption de la base.',
                    NEW.sha256_source;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_tn4_migration_anti_doublon
        BEFORE INSERT ON migration_historique
        FOR EACH ROW EXECUTE FUNCTION tg_fn_migration_anti_doublon();
    """)

    # TN5 : Calcul indicateurs après mutation ────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_indicateur_apres_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Recalcul asynchrone (en production : Redis queue)
            -- Ici : mise à jour directe du KPI mutations du mois
            INSERT INTO indicateur_national (
                type_indicateur, valeur, unite, periode, sha256_calcul, calcule_par
            )
            SELECT 'nb_mutations_mois'::type_indicateur,
                   COUNT(*),
                   'nombre',
                   TO_CHAR(NOW(), 'YYYY-MM'),
                   encode(sha256(('NMM|' || TO_CHAR(NOW(),'YYYY-MM') || '|' || COUNT(*)::TEXT)::bytea), 'hex'),
                   'SYSTEME'
            FROM mutation_dossier
            WHERE TO_CHAR(created_at, 'YYYY-MM') = TO_CHAR(NOW(), 'YYYY-MM')
            ON CONFLICT (type_indicateur, periode, region_id, commune_id)
            DO UPDATE SET valeur = EXCLUDED.valeur, date_calcul = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_tn5_indicateur_apres_mutation
        AFTER INSERT ON mutation_dossier
        FOR EACH ROW EXECUTE FUNCTION tg_fn_indicateur_apres_mutation();
    """)

    # ════════════════════════════════════════════════════════════════
    # VUES FINALES
    # ════════════════════════════════════════════════════════════════

    op.execute("""
        CREATE OR REPLACE VIEW v_sante_systeme AS
        SELECT
            NOW()                                        AS timestamp_rapport,
            -- Parcelles
            COUNT(DISTINCT p.id)                         AS total_parcelles,
            COUNT(DISTINCT p.id) FILTER (WHERE p.statut='active') AS parcelles_actives,
            COUNT(DISTINCT p.id) FILTER (WHERE p.is_gele=true)    AS parcelles_gelees,
            -- Blocks
            COUNT(DISTINCT tb.id) FILTER (WHERE tb.statut='actif') AS blocks_actifs,
            -- Anomalies
            COUNT(DISTINCT af.id) FILTER (WHERE af.corrige=false)           AS anomalies_totales,
            COUNT(DISTINCT af.id) FILTER (WHERE af.corrige=false
                                           AND af.gravite IN ('critique','bloquante'))
                                                         AS anomalies_critiques,
            -- CCFM
            COUNT(DISTINCT dc.id) FILTER (WHERE dc.statut IN ('CERTIFIE','SCELLE','VALIDE'))
                                                         AS ccfm_valides,
            -- Workflows en cours
            COUNT(DISTINCT wi.id) FILTER (WHERE wi.statut IN ('en_cours','en_attente'))
                                                         AS workflows_actifs,
            COUNT(DISTINCT wi.id) FILTER (WHERE wi.date_echeance < NOW()
                                           AND wi.statut NOT IN ('complete','rejete'))
                                                         AS workflows_en_retard,
            -- Litiges
            COUNT(DISTINCT l.id)  FILTER (WHERE l.statut NOT IN ('clos','abandonne'))
                                                         AS litiges_ouverts,
            -- Sauvegarde
            MAX(ss.date_sauvegarde) FILTER (WHERE ss.statut='reussie')
                                                         AS derniere_sauvegarde_ok,
            EXTRACT(EPOCH FROM (NOW() - MAX(ss.date_sauvegarde) FILTER (WHERE ss.statut='reussie')))
                / 3600                                   AS heures_depuis_sauvegarde
        FROM parcelles p
        CROSS JOIN (SELECT 1) x
        LEFT JOIN transaction_blocks   tb ON true
        LEFT JOIN anomalie_fonciere    af ON true
        LEFT JOIN demandes_ccfm        dc ON true
        LEFT JOIN workflow_instance    wi ON true
        LEFT JOIN litiges              l  ON true
        LEFT JOIN sauvegarde_systeme   ss ON true
        GROUP BY ();
    """)

    op.execute("""
        CREATE OR REPLACE VIEW v_anomalies_actives AS
        SELECT
            af.id,
            af.type_anomalie,
            af.gravite,
            af.nicad,
            p.statut::TEXT AS statut_parcelle,
            af.description,
            af.valeur_mesuree,
            af.detecte_at,
            -- Ancienneté
            EXTRACT(DAY FROM NOW() - af.detecte_at)::INT AS age_jours,
            -- Action requise
            af.action_requise,
            -- Priorité de traitement
            CASE af.gravite
                WHEN 'bloquante' THEN 1
                WHEN 'critique'  THEN 2
                WHEN 'elevee'    THEN 3
                WHEN 'moderee'   THEN 4
                ELSE                  5
            END AS priorite
        FROM anomalie_fonciere af
        LEFT JOIN parcelles p ON p.id = af.parcelle_id
        WHERE af.corrige = false
        ORDER BY priorite, af.detecte_at;
    """)

    op.execute("""
        CREATE OR REPLACE VIEW v_indicateurs_nationaux AS
        SELECT
            i.type_indicateur,
            i.valeur,
            i.unite,
            i.periode,
            i.date_calcul,
            r.nom_region,
            c.nom_commune,
            -- Variation vs période précédente
            LAG(i.valeur) OVER (
                PARTITION BY i.type_indicateur, i.region_id, i.commune_id
                ORDER BY i.periode
            ) AS valeur_precedente,
            CASE WHEN LAG(i.valeur) OVER (
                    PARTITION BY i.type_indicateur, i.region_id, i.commune_id
                    ORDER BY i.periode
                 ) > 0
                THEN ROUND(
                    (i.valeur - LAG(i.valeur) OVER (
                        PARTITION BY i.type_indicateur, i.region_id, i.commune_id
                        ORDER BY i.periode
                    )) / LAG(i.valeur) OVER (
                        PARTITION BY i.type_indicateur, i.region_id, i.commune_id
                        ORDER BY i.periode
                    ) * 100, 2
                )
                ELSE NULL
            END AS variation_pct
        FROM indicateur_national i
        LEFT JOIN regions  r ON r.id = i.region_id
        LEFT JOIN communes c ON c.id = i.commune_id
        ORDER BY i.periode DESC, i.type_indicateur;
    """)

    op.execute("""
        CREATE OR REPLACE VIEW v_migrations_en_cours AS
        SELECT
            mh.id,
            mh.code_migration,
            mh.source,
            mh.statut,
            mh.periode_couverte,
            c.nom_commune,
            mh.nb_lignes_source,
            mh.nb_importees,
            mh.nb_valides,
            mh.nb_rejetees,
            mh.nb_erreurs,
            -- Erreurs bloquantes non résolues
            COUNT(me.id) FILTER (WHERE me.niveau IN ('bloquant','critique')
                                   AND me.corrige = false) AS erreurs_bloquantes,
            -- Taux de succès
            CASE WHEN mh.nb_importees > 0
                THEN ROUND(mh.nb_valides::NUMERIC / mh.nb_importees * 100, 1)
                ELSE 0 END AS taux_succes_pct,
            mh.date_lancement,
            mh.date_termine
        FROM migration_historique mh
        LEFT JOIN communes c ON c.id = mh.commune_id
        LEFT JOIN migration_erreur me ON me.migration_id = mh.id
        WHERE mh.statut NOT IN ('termine','erreur')
        GROUP BY mh.id, mh.code_migration, mh.source, mh.statut,
                 mh.periode_couverte, c.nom_commune, mh.nb_lignes_source,
                 mh.nb_importees, mh.nb_valides, mh.nb_rejetees, mh.nb_erreurs,
                 mh.date_lancement, mh.date_termine;
    """)


def downgrade() -> None:
    for view in ["v_migrations_en_cours","v_indicateurs_nationaux",
                 "v_anomalies_actives","v_sante_systeme"]:
        op.execute(f"DROP VIEW IF EXISTS {view} CASCADE")

    for trigger, table in [
        ("tg_tn5_indicateur_apres_mutation",   "mutation_dossier"),
        ("tg_tn4_migration_anti_doublon",      "migration_historique"),
        ("tg_tn3_revision_bloc_parcelle",      "revision_parcelle"),
        ("tg_tn2_alerte_double_propriete",     "parcel_right_version"),
        ("tg_tn1_alerte_parcelle_sans_rnaf",   "parcelles"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    for fn in [
        "purger_alertes_resolues","rapport_sante_systeme","enregistrer_sauvegarde",
        "check_integrite_systeme","valider_migration_historique",
        "lancer_revision_cadastrale","calculer_indicateurs_nationaux",
        "detecter_anomalies_foncieres",
        "tg_fn_alerte_parcelle_sans_rnaf","tg_fn_alerte_double_propriete",
        "tg_fn_revision_bloc_parcelle","tg_fn_migration_anti_doublon",
        "tg_fn_indicateur_apres_mutation",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(INT) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(TEXT) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(TEXT,TEXT,TEXT,NUMERIC) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID,UUID) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(TEXT,TEXT,UUID,UUID) CASCADE")

    for table in [
        "restauration_systeme","sauvegarde_systeme",
        "historique_indicateur","indicateur_national",
        "anomalie_fonciere",
        "migration_erreur","migration_historique",
        "revision_parcelle","revision_cadastrale",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    for name in [
        "statut_revision","type_modification_revision","statut_migration",
        "niveau_erreur_migration","type_anomalie","gravite_anomalie",
        "type_indicateur","statut_sauvegarde",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")
