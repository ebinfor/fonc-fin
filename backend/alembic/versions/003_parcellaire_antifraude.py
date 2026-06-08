"""003_parcellaire_antifraude

FONCIER+ v3.4.7 — Migration 003 : Hiérarchie parcellaire + Règles antifraude
Ajoute : regions, communes, arretes_urbanisme, lotissements, ilots,
         parcelles, parcel_versions, parcel_lineage, nicad_registry,
         conflits_parcellaires, annulations_parcelles, refontes_lotissements,
         bgu_geojson_master

Revision ID: 003_parcellaire_antifraude
Revises: 002_plan_directeur
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import geoalchemy2

revision = "003_parcellaire_antifraude"
down_revision = "002_plan_directeur"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── PostGIS extension (idempotent) ──────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── ENUM types ──────────────────────────────────────────────────────────
  # op.execute("""
    #     DO $$ BEGIN
    #         CREATE TYPE statut_lotissement AS ENUM ('actif','refonte','archive');
    #         EXCEPTION WHEN duplicate_object THEN NULL;
    #     END $$;
    # """)
    # op.execute("""
    #     DO $$ BEGIN
    #         CREATE TYPE statut_parcelle AS ENUM
    #             ('active','subdivisee','ancienne','annule','archive','en_attente_validation');
    #         EXCEPTION WHEN duplicate_object THEN NULL;
    #     END $$;
    # """)
    # op.execute("""
    #     DO $$ BEGIN
    #         CREATE TYPE statut_version AS ENUM ('active','archive');
    #         EXCEPTION WHEN duplicate_object THEN NULL;
    #     END $$;
    # """)
    # op.execute("""
    #     DO $$ BEGIN
    #         CREATE TYPE type_conflit AS ENUM ('superposition','deplacement','surface','incoherence');
    #         EXCEPTION WHEN duplicate_object THEN NULL;
    #     END $$;
    # """)
    # op.execute("""
    #     DO $$ BEGIN
    #         CREATE TYPE gravite_conflit AS ENUM ('critique','a_verifier');
    #         EXCEPTION WHEN duplicate_object THEN NULL;
    #     END $$;
    # """)
    # op.execute("""
    #     DO $$ BEGIN
    #         CREATE TYPE statut_conflit AS ENUM ('ouvert','en_traitement','resolu','bloque');
    #         EXCEPTION WHEN duplicate_object THEN NULL;
    #     END $$;
    # """)
    # op.execute("""
    #     DO $$ BEGIN
    #         CREATE TYPE type_commune AS ENUM ('urbaine','rurale');
    #         EXCEPTION WHEN duplicate_object THEN NULL;
    #     END $$;
    # """)

    # ── 1. RÉGIONS ──────────────────────────────────────────────────────────
    op.create_table(
        "regions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code_region", sa.String(2), nullable=False),
        sa.Column("nom_region", sa.String(100), nullable=False),
        sa.Column("chef_lieu", sa.String(100)),
        sa.Column("geom", geoalchemy2.types.Geometry("POLYGON", srid=4326)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("code_region", name="uq_region_code"),
    )
    op.create_index("ix_regions_code", "regions", ["code_region"])

    # ── 2. COMMUNES ─────────────────────────────────────────────────────────
    op.create_table(
        "communes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("region_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("regions.id"), nullable=False),
        sa.Column("code_commune", sa.String(2), nullable=False),
        sa.Column("nom_commune", sa.String(150), nullable=False),
        sa.Column("geom", geoalchemy2.types.Geometry("POLYGON", srid=4326)),
        sa.Column("type_commune", sa.Enum("urbaine", "rurale", name="type_commune"),
                  default="urbaine"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("region_id", "code_commune", name="uq_commune_region"),
    )
    op.create_index("ix_communes_region", "communes", ["region_id"])

    # ── 3. ARRÊTÉS D'URBANISME ──────────────────────────────────────────────
    op.create_table(
        "arretes_urbanisme",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("commune_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("communes.id"), nullable=False),
        sa.Column("numero_arrete", sa.String(100), nullable=False),
        sa.Column("code_arrete", sa.String(7), nullable=False),
        sa.Column("date_signature", sa.DateTime(timezone=True), nullable=False),
        sa.Column("objet", sa.Text),
        sa.Column("statut", sa.String(20), server_default="actif"),
        sa.Column("sha256_document", sa.String(64)),
        sa.Column("signe_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("numero_arrete", name="uq_arrete_numero"),
        sa.UniqueConstraint("code_arrete", name="uq_arrete_code"),
    )
    op.create_index("ix_arretes_code", "arretes_urbanisme", ["code_arrete"])
    op.create_index("ix_arretes_commune", "arretes_urbanisme", ["commune_id"])

    # ── 4. LOTISSEMENTS ─────────────────────────────────────────────────────
    op.create_table(
        "lotissements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        # RÈGLE : 1 arrêté → 1 lotissement
        sa.Column("arrete_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("arretes_urbanisme.id"), nullable=False),
        sa.Column("nom_lotissement", sa.String(200), nullable=False),
        sa.Column("surface_totale_m2", sa.Numeric(15, 4), nullable=False),
        sa.Column("geom", geoalchemy2.types.Geometry("POLYGON", srid=4326)),
        sa.Column("statut", sa.Enum("actif", "refonte", "archive",
                                     name="statut_lotissement"), default="actif"),
        sa.Column("nb_ilots_attendus", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("arrete_id", name="uq_lotissement_arrete"),
        sa.CheckConstraint("nb_ilots_attendus BETWEEN 50 AND 200",
                            name="ck_ilots_range"),
    )

    # ── 5. ÎLOTS ────────────────────────────────────────────────────────────
    op.create_table(
        "ilots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("lotissement_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lotissements.id"), nullable=False),
        sa.Column("code_ilot", sa.String(3), nullable=False),
        sa.Column("geom", geoalchemy2.types.Geometry("POLYGON", srid=4326)),
        sa.Column("surface_m2", sa.Numeric(12, 4)),
        sa.Column("nb_parcelles_max", sa.Integer, server_default="30"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("lotissement_id", "code_ilot", name="uq_ilot_lotissement"),
        sa.CheckConstraint("nb_parcelles_max BETWEEN 1 AND 30",
                            name="ck_parcelles_range"),
    )
    op.create_index("ix_ilots_lotissement", "ilots", ["lotissement_id"])

    # ── 6. PARCEL_VERSIONS (avant parcelles pour la FK circulaire) ──────────
    op.create_table(
        "parcel_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("numero_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("statut_version", sa.Enum("active", "archive", name="statut_version"),
                  server_default="active"),
        sa.Column("surface_originale_m2", sa.Numeric(12, 4), nullable=False),
        sa.Column("geom_originale", geoalchemy2.types.Geometry("POLYGON", srid=4326),
                  nullable=False),
        sa.Column("motif_modification", sa.Text, nullable=False),
        sa.Column("sha256_version", sa.String(64), nullable=False),
        sa.Column("modifie_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("valide_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_parcel_versions_parcelle", "parcel_versions", ["parcelle_id"])

    # ── 7. PARCELLES ────────────────────────────────────────────────────────
    op.create_table(
        "parcelles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"),
                  comment="ID GeoJSON stable — ne change jamais"),
        sa.Column("ilot_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ilots.id"), nullable=False),
        sa.Column("nicad", sa.String(30), nullable=False),
        sa.Column("geojson_id", sa.String(100), nullable=False),
        sa.Column("statut", sa.Enum("active", "subdivisee", "ancienne", "annule",
                                     "archive", "en_attente_validation",
                                     name="statut_parcelle"),
                  server_default="en_attente_validation"),
        sa.Column("surface_m2", sa.Numeric(12, 4), nullable=False),
        sa.Column("geom", geoalchemy2.types.Geometry("POLYGON", srid=4326)),
        sa.Column("version_active_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcel_versions.id"), nullable=True),
        sa.Column("is_gele", sa.Boolean, server_default="false", nullable=False),
        sa.Column("gele_par_litige_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cree_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("nicad", name="uq_parcelle_nicad"),
        sa.UniqueConstraint("geojson_id", name="uq_parcelle_geojson_id"),
    )
    op.create_index("ix_parcelles_nicad", "parcelles", ["nicad"])
    op.create_index("ix_parcelles_ilot", "parcelles", ["ilot_id"])
    op.create_index("ix_parcelles_statut", "parcelles", ["statut"])

    # FK circulaire parcelle_id → maintenant que parcelles existe
    op.create_foreign_key(
        "fk_parcel_versions_parcelle",
        "parcel_versions", "parcelles",
        ["parcelle_id"], ["id"]
    )

    # ── Index spatial PostGIS ────────────────────────────────────────────────
    op.execute("""
        CREATE INDEX ix_parcelles_geom_gist
        ON parcelles USING GIST (geom)
    """)
    op.execute("""
        CREATE INDEX ix_ilots_geom_gist
        ON ilots USING GIST (geom)
    """)
    op.execute("""
        CREATE INDEX ix_lotissements_geom_gist
        ON lotissements USING GIST (geom)
    """)

    # ── 8. PARCEL_LINEAGE ───────────────────────────────────────────────────
    op.create_table(
        "parcel_lineage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("enfant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False, unique=True),
        sa.Column("suffixe", sa.String(1), nullable=False),
        sa.Column("niveau_subdivision", sa.Integer, nullable=False,
                  server_default="1"),
        sa.Column("autorise_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("parent_id", "suffixe", name="uq_lineage_parent_suffixe"),
        sa.CheckConstraint("suffixe IN ('a', 'b')", name="ck_suffixe_ab"),
        sa.CheckConstraint("niveau_subdivision = 1", name="ck_niveau_1"),
    )
    op.create_index("ix_lineage_parent", "parcel_lineage", ["parent_id"])

    # ── 9. NICAD_REGISTRY ───────────────────────────────────────────────────
    op.create_table(
        "nicad_registry",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("nicad", sa.String(30), nullable=False),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False, unique=True),
        sa.Column("code_region", sa.String(2), nullable=False),
        sa.Column("code_commune", sa.String(2), nullable=False),
        sa.Column("code_arrete", sa.String(7), nullable=False),
        sa.Column("code_ilot", sa.String(3), nullable=False),
        sa.Column("code_parcelle", sa.String(3), nullable=False),
        sa.Column("suffixe", sa.String(1), nullable=True),
        sa.Column("genere_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("nicad", name="uq_nicad_registry"),
    )
    op.create_index("ix_nicad_code_region", "nicad_registry", ["code_region"])
    op.create_index("ix_nicad_code_commune", "nicad_registry", ["code_commune"])
    op.create_index("ix_nicad_code_arrete", "nicad_registry", ["code_arrete"])

    # ── 10. CONFLITS_PARCELLAIRES ────────────────────────────────────────────
    op.create_table(
        "conflits_parcellaires",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcelle_a_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("parcelle_b_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("type_conflit", sa.Enum("superposition", "deplacement", "surface",
                                           "incoherence", name="type_conflit"),
                  nullable=False),
        sa.Column("gravite", sa.Enum("critique", "a_verifier", name="gravite_conflit"),
                  nullable=False),
        sa.Column("surface_intersection_m2", sa.Numeric(12, 4), server_default="0"),
        sa.Column("st_relation", sa.String(50)),
        sa.Column("seuil_applique_m2", sa.Numeric(10, 4)),
        sa.Column("statut", sa.Enum("ouvert", "en_traitement", "resolu", "bloque",
                                     name="statut_conflit"), server_default="ouvert"),
        sa.Column("sha256_geom", sa.String(64)),
        sa.Column("description", sa.Text),
        sa.Column("detecte_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("traite_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("traite_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_conflits_parcelle_a", "conflits_parcellaires", ["parcelle_a_id"])
    op.create_index("ix_conflits_gravite", "conflits_parcellaires", ["gravite"])
    op.create_index("ix_conflits_statut", "conflits_parcellaires", ["statut"])

    # ── 11. ANNULATIONS_PARCELLES ────────────────────────────────────────────
    op.create_table(
        "annulations_parcelles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False, unique=True),
        sa.Column("motif", sa.Text, nullable=False),
        sa.Column("base_juridique", sa.String(200)),
        sa.Column("preuve_document", sa.String(500)),
        sa.Column("sha256_preuve", sa.String(64), nullable=False),
        sa.Column("valide_niveau1", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("valide_niveau1_at", sa.DateTime(timezone=True)),
        sa.Column("valide_niveau2", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("valide_niveau2_at", sa.DateTime(timezone=True)),
        sa.Column("annule_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )

    # ── 12. REFONTES_LOTISSEMENTS ────────────────────────────────────────────
    op.create_table(
        "refontes_lotissements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("lotissement_ancien_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lotissements.id"), nullable=False),
        sa.Column("lotissement_nouveau_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lotissements.id"), nullable=False, unique=True),
        sa.Column("nouvel_arrete_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("arretes_urbanisme.id"), nullable=False),
        sa.Column("motif_refonte", sa.Text, nullable=False),
        sa.Column("surface_ancien_m2", sa.Numeric(15, 4), nullable=False),
        sa.Column("surface_nouveau_m2", sa.Numeric(15, 4), nullable=False),
        sa.Column("surface_coherence_ok", sa.Boolean, nullable=False),
        sa.Column("ecart_surface_pct", sa.Float),
        sa.Column("autorisee_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )

    # ── 13. BGU_GEOJSON_MASTER ───────────────────────────────────────────────
    op.create_table(
        "bgu_geojson_master",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False, unique=True),
        sa.Column("geojson_id", sa.String(100), nullable=False),
        sa.Column("geom", geoalchemy2.types.Geometry("POLYGON", srid=4326),
                  nullable=False),
        sa.Column("sha256_geom", sa.String(64), nullable=False),
        sa.Column("scelle", sa.Boolean, server_default="false", nullable=False),
        sa.Column("scelle_at", sa.DateTime(timezone=True)),
        sa.Column("scelle_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("blockchain_hash", sa.String(128)),
        sa.Column("blockchain_network", sa.String(50), server_default="internal"),
        sa.Column("blockchain_timestamp", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer, server_default="1", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("geojson_id", name="uq_bgu_geojson_id"),
    )
    op.execute("""
        CREATE INDEX ix_bgu_geojson_gist
        ON bgu_geojson_master USING GIST (geom)
    """)

    # ── Trigger antifraude : vérification superposition à l'INSERT/UPDATE ───
    op.execute("""
        CREATE OR REPLACE FUNCTION check_overlap_antifraude()
        RETURNS TRIGGER AS $$
        DECLARE
            seuil_critique NUMERIC := 1.0;  -- m² — ajustable
            rec RECORD;
        BEGIN
            -- Skip si statut ANNULE ou ARCHIVE
            IF NEW.statut IN ('annule', 'archive') THEN
                RETURN NEW;
            END IF;

            FOR rec IN
                SELECT id, nicad,
                       ST_Area(ST_Intersection(NEW.geom::geometry, geom::geometry)) AS surf
                FROM parcelles
                WHERE id != NEW.id
                  AND geom IS NOT NULL
                  AND statut NOT IN ('annule', 'archive', 'subdivisee', 'ancienne')
                  AND ST_Intersects(NEW.geom::geometry, geom::geometry)
            LOOP
                INSERT INTO conflits_parcellaires (
                    parcelle_a_id, parcelle_b_id, type_conflit, gravite,
                    surface_intersection_m2, st_relation, seuil_applique_m2,
                    statut, description
                ) VALUES (
                    NEW.id, rec.id,
                    'superposition',
                    CASE WHEN rec.surf >= seuil_critique THEN 'critique' ELSE 'a_verifier' END,
                    rec.surf,
                    'ST_Intersects',
                    seuil_critique,
                    CASE WHEN rec.surf >= seuil_critique THEN 'bloque' ELSE 'ouvert' END,
                    'Superposition automatique détectée entre ' || NEW.nicad || ' et ' || rec.nicad
                );

                IF rec.surf >= seuil_critique THEN
                    RAISE EXCEPTION
                        'ANTIFRAUDE: Superposition critique (%.4f m²) détectée entre NICAD % et %. Opération bloquée.',
                        rec.surf, NEW.nicad, rec.nicad;
                END IF;
            END LOOP;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER tg_antifraude_overlap
        BEFORE INSERT OR UPDATE OF geom
        ON parcelles
        FOR EACH ROW
        WHEN (NEW.geom IS NOT NULL)
        EXECUTE FUNCTION check_overlap_antifraude();
    """)

    # ── Trigger : archiver versions inactives ───────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION archive_old_versions()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.statut_version = 'active' THEN
                UPDATE parcel_versions
                SET statut_version = 'archive'
                WHERE parcelle_id = NEW.parcelle_id
                  AND id != NEW.id
                  AND statut_version = 'active';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER tg_archive_old_versions
        AFTER INSERT OR UPDATE OF statut_version
        ON parcel_versions
        FOR EACH ROW
        WHEN (NEW.statut_version = 'active')
        EXECUTE FUNCTION archive_old_versions();
    """)

    # ── Données seed : 8 régions du Niger ───────────────────────────────────
    op.execute("""
        INSERT INTO regions (code_region, nom_region, chef_lieu) VALUES
        ('NI', 'Niamey', 'Niamey'),
        ('AG', 'Agadez', 'Agadez'),
        ('DI', 'Diffa', 'Diffa'),
        ('DO', 'Dosso', 'Dosso'),
        ('MA', 'Maradi', 'Maradi'),
        ('TA', 'Tahoua', 'Tahoua'),
        ('TI', 'Tillabéri', 'Tillabéri'),
        ('ZI', 'Zinder', 'Zinder')
        ON CONFLICT (code_region) DO NOTHING;
    """)


def downgrade() -> None:
    # Suppression dans l'ordre inverse des dépendances
    op.execute("DROP TRIGGER IF EXISTS tg_antifraude_overlap ON parcelles")
    op.execute("DROP TRIGGER IF EXISTS tg_archive_old_versions ON parcel_versions")
    op.execute("DROP FUNCTION IF EXISTS check_overlap_antifraude()")
    op.execute("DROP FUNCTION IF EXISTS archive_old_versions()")

    for table in [
        "bgu_geojson_master", "refontes_lotissements", "annulations_parcelles",
        "conflits_parcellaires", "nicad_registry", "parcel_lineage",
        "parcelles", "parcel_versions", "ilots", "lotissements",
        "arretes_urbanisme", "communes", "regions",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    for enum_name in [
        "statut_lotissement", "statut_parcelle", "statut_version",
        "type_conflit", "gravite_conflit", "statut_conflit", "type_commune",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name} CASCADE")
