"""020_registres_administratifs

FONCIER+ v3.4.9 — Migration 020 : Registres officiels administratifs
Corrige les lacunes LAC-A01 à LAC-A05 de l'audit administratif :

  LAC-A01  etude_notariale          — Études notariales officielles
  LAC-A02  banque_agreee            — Banques agréées BCEAO
  LAC-A03  huissier_agree           — Huissiers titulaires de charge
  LAC-A04  geometre_expert          — Géomètres-experts inscrits à l'ONGE
  LAC-A05  mandat_elu               — Mandats des maires en fonction

Chaque table est accompagnée :
  - De ses contraintes d'unicité officielles (numéros uniques)
  - D'un trigger de validation sur les workflows concernés
  - D'un seed minimal pour démarrer l'exploitation

Revision ID: 020_registres_administratifs
Revises: 019_workflows_partiels_completion
Create Date: 2026-04-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "020_registres_administratifs"
down_revision = "019_workflows_partiels_completion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ═══════════════════════════════════════════════════════════════
    # LAC-A01 — ETUDE_NOTARIALE
    # ═══════════════════════════════════════════════════════════════
    op.create_table(
        "etude_notariale",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("numero_parquet",     sa.String(50),  nullable=False),
        sa.Column("nom_etude",          sa.String(200), nullable=False),
        sa.Column("adresse",            sa.Text,        nullable=False),
        sa.Column("commune_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("communes.id")),
        sa.Column("chambre_regionale",  sa.String(200)),
        sa.Column("statut",             sa.String(20),  server_default="active"),
        # active | suspendue | fermee | transferee
        sa.Column("date_agrement",      sa.Date, nullable=False),
        sa.Column("date_suspension",    sa.Date),
        sa.Column("motif_suspension",   sa.Text),
        sa.Column("date_fermeture",     sa.Date),
        sa.Column("etude_remplacante_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("etude_notariale.id")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("numero_parquet", name="uq_etude_parquet"),
    )
    op.create_index("ix_etude_statut", "etude_notariale", ["statut"])

    # Rattacher les notaires à leur étude
    op.execute("""
        ALTER TABLE notary_registry
            ADD COLUMN IF NOT EXISTS etude_id UUID
                REFERENCES etude_notariale(id);
    """)
    op.create_index("ix_notary_etude", "notary_registry", ["etude_id"])

    # ═══════════════════════════════════════════════════════════════
    # LAC-A02 — BANQUE_AGREEE (BCEAO)
    # ═══════════════════════════════════════════════════════════════
    op.create_table(
        "banque_agreee",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("numero_agrement_bceao", sa.String(50), nullable=False),
        sa.Column("raison_sociale",        sa.String(300), nullable=False),
        sa.Column("sigle",                 sa.String(20)),
        sa.Column("siege_social",          sa.Text),
        sa.Column("pays_origine",          sa.String(50), server_default="NER"),
        sa.Column("type_etablissement",    sa.String(30)),
        # banque_universelle | banque_affaires | microfinance | caisse_epargne
        sa.Column("date_agrement",         sa.Date, nullable=False),
        sa.Column("date_retrait_agrement", sa.Date),
        sa.Column("statut",                sa.String(20), server_default="actif"),
        # actif | suspendu | retrait | fusion
        sa.Column("liste_agences",         postgresql.JSONB,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("contact_direction",     sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("numero_agrement_bceao", name="uq_banque_bceao"),
    )
    op.create_index("ix_banque_statut", "banque_agreee", ["statut"])

    # Lier mortgage_registry à banque_agreee
    op.execute("""
        ALTER TABLE mortgage_registry
            ADD COLUMN IF NOT EXISTS banque_id UUID
                REFERENCES banque_agreee(id);
    """)
    op.create_index("ix_mortgage_banque", "mortgage_registry", ["banque_id"])

    # ═══════════════════════════════════════════════════════════════
    # LAC-A03 — HUISSIER_AGREE
    # ═══════════════════════════════════════════════════════════════
    op.create_table(
        "huissier_agree",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("numero_charge",     sa.String(50),  nullable=False),
        sa.Column("nom_huissier",      sa.String(200), nullable=False),
        sa.Column("tgi_rattachement",  sa.String(50),  nullable=False),
        # FK logique vers greffe_judiciaire.id
        sa.Column("date_prestation_serment", sa.Date, nullable=False),
        sa.Column("adresse_etude",     sa.Text),
        sa.Column("statut",            sa.String(20),  server_default="actif"),
        # actif | suspendu | retire | decede
        sa.Column("date_suspension",   sa.Date),
        sa.Column("motif_suspension",  sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("numero_charge", name="uq_huissier_charge"),
        sa.UniqueConstraint("user_id",       name="uq_huissier_user"),
        sa.ForeignKeyConstraint(["tgi_rattachement"], ["greffe_judiciaire.id"]),
    )
    op.create_index("ix_huissier_tgi", "huissier_agree", ["tgi_rattachement"])

    # ═══════════════════════════════════════════════════════════════
    # LAC-A04 — GEOMETRE_EXPERT (Ordre National des Géomètres-Experts)
    # ═══════════════════════════════════════════════════════════════
    op.create_table(
        "geometre_expert",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("numero_inscription_onge", sa.String(50), nullable=False),
        sa.Column("nom_geometre",            sa.String(200), nullable=False),
        sa.Column("specialisation",          sa.String(100)),
        # topographie | cadastre | urbanisme | genie_civil | mixte
        sa.Column("region_exercice_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("regions.id")),
        sa.Column("date_inscription",   sa.Date, nullable=False),
        sa.Column("date_suspension",    sa.Date),
        sa.Column("motif_suspension",   sa.Text),
        sa.Column("statut",             sa.String(20), server_default="actif"),
        # actif | suspendu | radie
        sa.Column("cabinet_exercice",   sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("numero_inscription_onge", name="uq_geometre_onge"),
        sa.UniqueConstraint("user_id",                 name="uq_geometre_user"),
    )
    op.create_index("ix_geometre_statut", "geometre_expert", ["statut"])

    # ═══════════════════════════════════════════════════════════════
    # LAC-A05 — MANDAT_ELU (Maires en fonction)
    # ═══════════════════════════════════════════════════════════════
    op.create_table(
        "mandat_elu",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("commune_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("communes.id"), nullable=False),
        sa.Column("fonction", sa.String(30), nullable=False,
                  server_default="maire"),
        # maire | adjoint | conseiller | president_conseil_regional
        sa.Column("date_debut_mandat", sa.Date, nullable=False),
        sa.Column("date_fin_mandat",   sa.Date, nullable=False),
        sa.Column("acte_election_ref", sa.String(100), nullable=False),
        # ex: ARR-MIN-INT-2026-042
        sa.Column("proces_verbal_url", sa.Text),
        sa.Column("statut", sa.String(20), server_default="en_cours"),
        # en_cours | termine | revoque | suspendu
        sa.Column("date_revocation",   sa.Date),
        sa.Column("motif_revocation",  sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "date_fin_mandat > date_debut_mandat",
            name="ck_mandat_dates_valides"
        ),
    )
    op.create_index("ix_mandat_user",    "mandat_elu", ["user_id"])
    op.create_index("ix_mandat_commune", "mandat_elu", ["commune_id"])
    op.create_index("ix_mandat_statut",  "mandat_elu", ["statut"])

    # Contrainte : un seul maire actif par commune
    op.execute("""
        CREATE UNIQUE INDEX uq_maire_commune_actif
            ON mandat_elu (commune_id)
            WHERE statut = 'en_cours' AND fonction = 'maire';
    """)

    # ═══════════════════════════════════════════════════════════════
    # TRIGGERS DE VALIDATION SUR LES WORKFLOWS
    # ═══════════════════════════════════════════════════════════════

    # Trigger 1 : empêcher l'usage d'un MAIRE hors mandat
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_verifier_mandat_maire()
        RETURNS TRIGGER AS $$
        DECLARE
            v_role TEXT;
            v_mandat_actif BOOLEAN;
        BEGIN
            SELECT role INTO v_role FROM users
                WHERE id = NEW.demarre_par_id;

            IF v_role != 'MAIRE' THEN RETURN NEW; END IF;

            SELECT EXISTS (
                SELECT 1 FROM mandat_elu
                WHERE user_id = NEW.demarre_par_id
                  AND statut = 'en_cours'
                  AND fonction = 'maire'
                  AND CURRENT_DATE BETWEEN date_debut_mandat AND date_fin_mandat
            ) INTO v_mandat_actif;

            IF NOT v_mandat_actif THEN
                RAISE EXCEPTION
                    'MAIRE REFUSÉ: utilisateur % ne dispose d''aucun mandat actif',
                    NEW.demarre_par_id;
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS tg_verifier_mandat_maire ON workflow_instance;
        CREATE TRIGGER tg_verifier_mandat_maire
            BEFORE INSERT ON workflow_instance
            FOR EACH ROW EXECUTE FUNCTION fn_verifier_mandat_maire();
    """)

    # Trigger 2 : empêcher l'inscription d'hypothèque par une banque non agréée
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_verifier_banque_agreee()
        RETURNS TRIGGER AS $$
        DECLARE v_statut TEXT;
        BEGIN
            IF NEW.banque_id IS NULL THEN
                RAISE EXCEPTION
                    'HYPOTHEQUE REFUSÉE: banque_id obligatoire depuis migration 020';
            END IF;
            SELECT statut INTO v_statut FROM banque_agreee WHERE id = NEW.banque_id;
            IF v_statut IS NULL THEN
                RAISE EXCEPTION
                    'HYPOTHEQUE REFUSÉE: banque % introuvable dans banque_agreee',
                    NEW.banque_id;
            END IF;
            IF v_statut != 'actif' THEN
                RAISE EXCEPTION
                    'HYPOTHEQUE REFUSÉE: banque % est au statut % (non actif)',
                    NEW.banque_id, v_statut;
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS tg_verifier_banque_agreee ON mortgage_registry;
        CREATE TRIGGER tg_verifier_banque_agreee
            BEFORE INSERT OR UPDATE OF banque_id ON mortgage_registry
            FOR EACH ROW EXECUTE FUNCTION fn_verifier_banque_agreee();
    """)

    # Trigger 3 : empêcher l'usage d'un HUISSIER non agréé sur WF25
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_verifier_huissier_agree()
        RETURNS TRIGGER AS $$
        DECLARE v_role TEXT; v_actif BOOLEAN;
        BEGIN
            SELECT role INTO v_role FROM users WHERE id = NEW.demarre_par_id;
            IF v_role != 'HUISSIER' THEN RETURN NEW; END IF;

            SELECT EXISTS (
                SELECT 1 FROM huissier_agree
                WHERE user_id = NEW.demarre_par_id AND statut = 'actif'
            ) INTO v_actif;

            IF NOT v_actif THEN
                RAISE EXCEPTION
                    'HUISSIER REFUSÉ: utilisateur % non inscrit au registre huissier_agree',
                    NEW.demarre_par_id;
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS tg_verifier_huissier_agree ON workflow_instance;
        CREATE TRIGGER tg_verifier_huissier_agree
            BEFORE INSERT ON workflow_instance
            FOR EACH ROW EXECUTE FUNCTION fn_verifier_huissier_agree();
    """)

    # Trigger 4 : empêcher l'usage d'un GEOMETRE non inscrit à l'ONGE
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_verifier_geometre_inscrit()
        RETURNS TRIGGER AS $$
        DECLARE v_role TEXT; v_actif BOOLEAN;
        BEGIN
            SELECT role INTO v_role FROM users WHERE id = NEW.demarre_par_id;
            IF v_role != 'GEOMETRE' THEN RETURN NEW; END IF;

            SELECT EXISTS (
                SELECT 1 FROM geometre_expert
                WHERE user_id = NEW.demarre_par_id AND statut = 'actif'
            ) INTO v_actif;

            IF NOT v_actif THEN
                RAISE EXCEPTION
                    'GEOMETRE REFUSÉ: utilisateur % non inscrit à l''ONGE (geometre_expert)',
                    NEW.demarre_par_id;
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS tg_verifier_geometre_inscrit ON workflow_instance;
        CREATE TRIGGER tg_verifier_geometre_inscrit
            BEFORE INSERT ON workflow_instance
            FOR EACH ROW EXECUTE FUNCTION fn_verifier_geometre_inscrit();
    """)

    # ═══════════════════════════════════════════════════════════════
    # SEEDS MINIMAUX
    # ═══════════════════════════════════════════════════════════════

    # Seed banques : les 15 principales banques commerciales du Niger
    op.execute("""
        INSERT INTO banque_agreee
            (numero_agrement_bceao, raison_sociale, sigle, type_etablissement,
             siege_social, date_agrement, statut)
        VALUES
          ('BCEAO-NE-001','Banque Internationale pour l''Afrique au Niger','BIA-Niger',
           'banque_universelle','Niamey','1980-01-01','actif'),
          ('BCEAO-NE-002','Société Nigérienne de Banque','SONIBANK',
           'banque_universelle','Niamey','1990-01-01','actif'),
          ('BCEAO-NE-003','Bank of Africa Niger','BOA-Niger',
           'banque_universelle','Niamey','1994-01-01','actif'),
          ('BCEAO-NE-004','Ecobank Niger','Ecobank',
           'banque_universelle','Niamey','1999-01-01','actif'),
          ('BCEAO-NE-005','Banque Atlantique Niger','BA-Niger',
           'banque_universelle','Niamey','2006-01-01','actif'),
          ('BCEAO-NE-006','Banque Commerciale du Niger','BCN',
           'banque_universelle','Niamey','1995-01-01','actif'),
          ('BCEAO-NE-007','Banque Sahélo-Saharienne pour l''Investissement','BSIC-Niger',
           'banque_universelle','Niamey','2003-01-01','actif'),
          ('BCEAO-NE-008','Banque Régionale de Solidarité Niger','BRS-Niger',
           'banque_universelle','Niamey','2004-01-01','actif'),
          ('BCEAO-NE-009','Crédit du Niger','CDN',
           'banque_universelle','Niamey','1985-01-01','actif'),
          ('BCEAO-NE-010','Orabank Niger','Orabank',
           'banque_universelle','Niamey','2010-01-01','actif'),
          ('BCEAO-NE-011','Banque Agricole du Niger','BAGRI',
           'banque_universelle','Niamey','2011-01-01','actif'),
          ('BCEAO-NE-012','Banque de l''Habitat du Niger','BHN',
           'banque_affaires','Niamey','2015-01-01','actif'),
          ('BCEAO-NE-013','ASUSU SA','ASUSU',
           'microfinance','Niamey','2008-01-01','actif'),
          ('BCEAO-NE-014','Caisse Nationale d''Épargne','CNE',
           'caisse_epargne','Niamey','1970-01-01','actif'),
          ('BCEAO-NE-015','KAFO JIGINEW Niger','KAFO',
           'microfinance','Niamey','2012-01-01','actif')
        ON CONFLICT DO NOTHING;
    """)

    # Seed : chambres notariales régionales (8 régions)
    op.execute("""
        INSERT INTO etude_notariale
            (numero_parquet, nom_etude, adresse, chambre_regionale, date_agrement, statut)
        VALUES
          ('PQ-NIA-001','Étude Me Diallo','Avenue du Général de Gaulle, Niamey',
           'Chambre Notariale de Niamey','2010-01-01','active'),
          ('PQ-NIA-002','Étude Me Seydou','Rue du Musée, Niamey',
           'Chambre Notariale de Niamey','2012-01-01','active'),
          ('PQ-DOS-001','Étude Me Ibrahim','Centre-ville Dosso',
           'Chambre Notariale de Dosso','2015-01-01','active'),
          ('PQ-TAH-001','Étude Me Abdoulaye','Avenue Principale Tahoua',
           'Chambre Notariale de Tahoua','2013-01-01','active'),
          ('PQ-MAR-001','Étude Me Maman','Quartier Sabon Gari Maradi',
           'Chambre Notariale de Maradi','2014-01-01','active'),
          ('PQ-ZIN-001','Étude Me Oumarou','Centre-ville Zinder',
           'Chambre Notariale de Zinder','2011-01-01','active'),
          ('PQ-TIL-001','Étude Me Hassane','Avenue Kennedy, Tillabéri',
           'Chambre Notariale de Tillabéri','2016-01-01','active'),
          ('PQ-AGA-001','Étude Me Amadou','Place de l''Indépendance, Agadez',
           'Chambre Notariale d''Agadez','2017-01-01','active')
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    # Retirer dans l'ordre inverse
    op.execute("DROP TRIGGER IF EXISTS tg_verifier_geometre_inscrit ON workflow_instance;")
    op.execute("DROP FUNCTION IF EXISTS fn_verifier_geometre_inscrit();")
    op.execute("DROP TRIGGER IF EXISTS tg_verifier_huissier_agree ON workflow_instance;")
    op.execute("DROP FUNCTION IF EXISTS fn_verifier_huissier_agree();")
    op.execute("DROP TRIGGER IF EXISTS tg_verifier_banque_agreee ON mortgage_registry;")
    op.execute("DROP FUNCTION IF EXISTS fn_verifier_banque_agreee();")
    op.execute("DROP TRIGGER IF EXISTS tg_verifier_mandat_maire ON workflow_instance;")
    op.execute("DROP FUNCTION IF EXISTS fn_verifier_mandat_maire();")

    op.execute("DROP INDEX IF EXISTS uq_maire_commune_actif;")
    op.drop_table("mandat_elu")
    op.drop_table("geometre_expert")
    op.drop_table("huissier_agree")

    op.execute("ALTER TABLE mortgage_registry DROP COLUMN IF EXISTS banque_id;")
    op.drop_table("banque_agreee")

    op.execute("ALTER TABLE notary_registry DROP COLUMN IF EXISTS etude_id;")
    op.drop_table("etude_notariale")
