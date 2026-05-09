"""021_v35_fondation_administrative

FONCIER+ v3.5.0 — Migration 021 : Fondation administrative v3.5
Pose l'infrastructure pour les catégories B, C, D de l'audit administratif.

CATÉGORIE B — Numérotation chronologique officielle
  B01  12 séquences PostgreSQL par module producteur d'actes
  B02  Vue v_repertoire_mensuel_notaire
  B03  Table recepisse_depot (récépissés officiels)
  B04  Champ numero_dossier_maitre sur workflow_instance

CATÉGORIE C — Formulaires et templates documentaires
  C01  Table document_template avec versioning Jinja2
  C02  Table workflow_form_schema (JSON Schema par workflow)
  C03  Fonction get_template_applicable(code, date)

CATÉGORIE D — États administratifs et statistiques
  D01  Vue v_etat_trimestriel_courant
  D02  Table bordereau_envoi inter-services

Revision ID: 021_v35_fondation_administrative
Revises: 020_registres_administratifs
Create Date: 2026-04-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "021_v35_fondation_administrative"
down_revision = "020_registres_administratifs"
branch_labels = None
depends_on = None


# ═══════════════════════════════════════════════════════════════════
# Les 12 séquences officielles — une par module producteur d'actes
# ═══════════════════════════════════════════════════════════════════
SEQUENCES_OFFICIELLES = [
    ("seq_acte_notaire",       "NOT"),   # actes notariés
    ("seq_acte_huissier",      "HUI"),   # procès-verbaux d'huissier
    ("seq_jugement_foncier",   "JUG"),   # décisions judiciaires
    ("seq_certificat_ccfm",    "CCF"),   # déjà existant NUS — aligné
    ("seq_arrete_urbanisme",   "ARR"),   # arrêtés urbanisme
    ("seq_hypotheque",         "HYP"),   # inscriptions hypothécaires
    ("seq_mutation",           "MUT"),   # mutations de propriété
    ("seq_succession",         "SUC"),   # successions foncières
    ("seq_bordereau",          "BOR"),   # bordereaux d'envoi inter-services
    ("seq_recepisse",          "REC"),   # récépissés de dépôt
    ("seq_dossier_maitre",     "DOS"),   # numéro de dossier maître
    ("seq_etat_trimestriel",   "ETR"),   # états trimestriels DNCD
]


def upgrade() -> None:
    # ═══════════════════════════════════════════════════════════════
    # CATÉGORIE B — NUMÉROTATION CHRONOLOGIQUE OFFICIELLE
    # ═══════════════════════════════════════════════════════════════

    # B01 — Les 12 séquences PostgreSQL
    for seq_name, _ in SEQUENCES_OFFICIELLES:
        op.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq_name} START 1;")

    # Fonction générique de génération de numéro chronologique
    op.execute("""
        CREATE OR REPLACE FUNCTION generer_numero_officiel(
            p_prefix TEXT, p_sequence TEXT
        ) RETURNS TEXT AS $$
        DECLARE v_annee INT; v_num BIGINT;
        BEGIN
            v_annee := EXTRACT(YEAR FROM NOW())::INT;
            EXECUTE format('SELECT nextval(%L)', p_sequence) INTO v_num;
            RETURN p_prefix || '-' || v_annee || '-' || LPAD(v_num::TEXT, 7, '0');
        END; $$ LANGUAGE plpgsql;
    """)
    # Exemple d'usage : SELECT generer_numero_officiel('NOT', 'seq_acte_notaire')
    # Retourne : 'NOT-2026-0000001'

    # B02 — Vue répertoire mensuel notaire
    op.execute("""
        CREATE OR REPLACE VIEW v_repertoire_mensuel_notaire AS
        SELECT
            n.user_id                            AS notaire_id,
            nr.nom_notaire,
            nr.numero_agrement,
            EXTRACT(YEAR  FROM n.created_at)::INT AS annee,
            EXTRACT(MONTH FROM n.created_at)::INT AS mois,
            n.numero_chrono_officiel,
            n.type_acte,
            n.nicad_concerne,
            n.sha256_chain,
            n.created_at
        FROM notarial_act n
        JOIN notary_registry nr ON nr.user_id = n.notaire_id
        ORDER BY annee DESC, mois DESC, n.numero_chrono_officiel;
    """)

    # B03 — Table des récépissés de dépôt
    op.create_table(
        "recepisse_depot",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("numero_recepisse",  sa.String(30), nullable=False),
        sa.Column("module",            sa.String(30), nullable=False),
        sa.Column("type_demande",      sa.String(80), nullable=False),
        sa.Column("demandeur_nom",     sa.String(200), nullable=False),
        sa.Column("demandeur_cni",     sa.String(50)),
        sa.Column("guichet_depot_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("dossier_rattache_id", postgresql.UUID(as_uuid=True)),
        sa.Column("date_depot", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("pdf_url",   sa.Text),
        sa.UniqueConstraint("numero_recepisse", name="uq_recepisse_numero"),
    )
    op.create_index("ix_recepisse_module", "recepisse_depot", ["module"])

    # B04 — Numéro de dossier maître sur workflow_instance
    op.execute("""
        ALTER TABLE workflow_instance
            ADD COLUMN IF NOT EXISTS numero_dossier_maitre VARCHAR(30);
        CREATE INDEX IF NOT EXISTS ix_wf_dossier_maitre
            ON workflow_instance(numero_dossier_maitre);
    """)

    # Trigger : génération auto du numero_dossier_maitre à l'insertion
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_generer_dossier_maitre()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.numero_dossier_maitre IS NULL THEN
                NEW.numero_dossier_maitre := generer_numero_officiel(
                    'DOS', 'seq_dossier_maitre'
                );
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS tg_generer_dossier_maitre ON workflow_instance;
        CREATE TRIGGER tg_generer_dossier_maitre
            BEFORE INSERT ON workflow_instance
            FOR EACH ROW EXECUTE FUNCTION fn_generer_dossier_maitre();
    """)

    # ═══════════════════════════════════════════════════════════════
    # CATÉGORIE C — FORMULAIRES ET TEMPLATES DOCUMENTAIRES
    # ═══════════════════════════════════════════════════════════════

    # C01 — Table document_template versionnée
    op.create_table(
        "document_template",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code_template",      sa.String(50), nullable=False),
        sa.Column("version",            sa.Integer,    nullable=False),
        sa.Column("module",             sa.String(30), nullable=False),
        sa.Column("type_acte",          sa.String(80), nullable=False),
        sa.Column("libelle",            sa.String(200), nullable=False),
        sa.Column("contenu_jinja2",     sa.Text, nullable=False),
        sa.Column("champs_fusion",      postgresql.JSONB,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("date_entree_vigueur", sa.Date, nullable=False),
        sa.Column("date_fin_vigueur",    sa.Date),
        sa.Column("valide_par_user_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("valide_le", sa.DateTime(timezone=True)),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("code_template", "version", name="uq_template_code_version"),
    )
    op.create_index("ix_template_module_type", "document_template",
                    ["module", "type_acte"])
    op.create_index("ix_template_vigueur", "document_template",
                    ["date_entree_vigueur", "date_fin_vigueur"])

    # C03 — Fonction get_template_applicable
    op.execute("""
        CREATE OR REPLACE FUNCTION get_template_applicable(
            p_code_template TEXT, p_date DATE DEFAULT CURRENT_DATE
        ) RETURNS UUID AS $$
        DECLARE v_id UUID;
        BEGIN
            SELECT id INTO v_id FROM document_template
            WHERE code_template = p_code_template
              AND is_active = TRUE
              AND date_entree_vigueur <= p_date
              AND (date_fin_vigueur IS NULL OR date_fin_vigueur >= p_date)
            ORDER BY version DESC
            LIMIT 1;
            RETURN v_id;
        END; $$ LANGUAGE plpgsql;
    """)

    # C02 — Schémas de formulaires par workflow
    op.create_table(
        "workflow_form_schema",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("workflow_code",  sa.String(10), nullable=False),
        sa.Column("etape",          sa.String(50), nullable=False),
        sa.Column("json_schema",    postgresql.JSONB, nullable=False),
        sa.Column("required_fields", postgresql.ARRAY(sa.String),
                  server_default=sa.text("ARRAY[]::varchar[]")),
        sa.Column("validation_rules", postgresql.JSONB,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("version",  sa.Integer, server_default=sa.text("1")),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("TRUE")),
        sa.UniqueConstraint("workflow_code", "etape", "version",
                            name="uq_form_schema_wf_etape_ver"),
    )
    op.create_index("ix_form_schema_workflow", "workflow_form_schema",
                    ["workflow_code"])

    # ═══════════════════════════════════════════════════════════════
    # CATÉGORIE D — ÉTATS ADMINISTRATIFS ET STATISTIQUES
    # ═══════════════════════════════════════════════════════════════

    # D01 — Vue état trimestriel courant
    op.execute("""
        CREATE OR REPLACE VIEW v_etat_trimestriel_courant AS
        SELECT
            EXTRACT(YEAR FROM wi.created_at)::INT AS annee,
            EXTRACT(QUARTER FROM wi.created_at)::INT AS trimestre,
            wi.type_workflow,
            COUNT(*) FILTER (WHERE wi.statut = 'termine') AS nb_completes,
            COUNT(*) FILTER (WHERE wi.statut = 'en_cours') AS nb_en_cours,
            COUNT(*) FILTER (WHERE wi.statut = 'annule') AS nb_annules,
            COUNT(*) AS nb_total
        FROM workflow_instance wi
        WHERE wi.created_at >= date_trunc('quarter', CURRENT_DATE)
        GROUP BY 1, 2, 3
        ORDER BY 1 DESC, 2 DESC, 3;
    """)

    # Fonction paramétrable pour extraire un trimestre historique
    op.execute("""
        CREATE OR REPLACE FUNCTION generer_etat_trimestriel(
            p_annee INT, p_trimestre INT
        ) RETURNS JSONB AS $$
        DECLARE v_result JSONB;
        BEGIN
            SELECT jsonb_agg(jsonb_build_object(
                'type_workflow', type_workflow,
                'nb_completes', nb_completes,
                'nb_en_cours', nb_en_cours,
                'nb_annules', nb_annules,
                'nb_total', nb_total
            )) INTO v_result
            FROM (
                SELECT
                    wi.type_workflow,
                    COUNT(*) FILTER (WHERE wi.statut='termine') AS nb_completes,
                    COUNT(*) FILTER (WHERE wi.statut='en_cours') AS nb_en_cours,
                    COUNT(*) FILTER (WHERE wi.statut='annule') AS nb_annules,
                    COUNT(*) AS nb_total
                FROM workflow_instance wi
                WHERE EXTRACT(YEAR FROM wi.created_at) = p_annee
                  AND EXTRACT(QUARTER FROM wi.created_at) = p_trimestre
                GROUP BY wi.type_workflow
            ) s;
            RETURN COALESCE(v_result, '[]'::jsonb);
        END; $$ LANGUAGE plpgsql;
    """)

    # D02 — Bordereau d'envoi inter-services
    op.create_table(
        "bordereau_envoi",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("numero_bordereau", sa.String(30), nullable=False),
        sa.Column("date_envoi", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("service_expediteur",   sa.String(50), nullable=False),
        sa.Column("service_destinataire", sa.String(50), nullable=False),
        sa.Column("dossiers_transmis", postgresql.ARRAY(sa.String),
                  server_default=sa.text("ARRAY[]::varchar[]")),
        sa.Column("signataire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("signature_pki", sa.Text),
        sa.Column("recu_par_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("recu_le",   sa.DateTime(timezone=True)),
        sa.Column("pdf_url",   sa.Text),
        sa.UniqueConstraint("numero_bordereau", name="uq_bordereau_numero"),
    )
    op.create_index("ix_bordereau_exp_dest", "bordereau_envoi",
                    ["service_expediteur", "service_destinataire"])

    # ═══════════════════════════════════════════════════════════════
    # SEEDS : 3 templates exemples pour démarrer
    # ═══════════════════════════════════════════════════════════════
    op.execute("""
        INSERT INTO document_template
            (code_template, version, module, type_acte, libelle,
             contenu_jinja2, date_entree_vigueur, is_active)
        VALUES
          ('TMPL_CCFM_CERTIFICAT', 1, 'ccfm', 'certificat',
           'Certificat CCFM standard',
           '<!-- Jinja2 template à compléter par la DNCD -->
            Certificat N°{{ nus }} délivré à {{ demandeur }}
            pour la parcelle {{ nicad }} d''une superficie de {{ superficie }} m².',
           '2026-01-01', TRUE),
          ('TMPL_NOTAIRE_VENTE', 1, 'notaire', 'acte_vente',
           'Acte de vente notarié',
           '<!-- À compléter par la Chambre Notariale -->
            Par le présent acte, {{ vendeur.nom }} vend à {{ acquereur.nom }}
            la parcelle {{ nicad }} pour {{ prix_fcfa }} FCFA.',
           '2026-01-01', TRUE),
          ('TMPL_JUSTICE_ANNULATION', 1, 'justice', 'jugement_annulation',
           'Jugement d''annulation de titre',
           '<!-- À compléter par le TGI -->
            Le Tribunal, statuant sur l''affaire n°{{ num_jugement }},
            annule le titre foncier {{ nicad }} pour le motif : {{ motif }}.',
           '2026-01-01', TRUE)
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tg_generer_dossier_maitre ON workflow_instance;")
    op.execute("DROP FUNCTION IF EXISTS fn_generer_dossier_maitre();")

    op.drop_table("bordereau_envoi")
    op.execute("DROP FUNCTION IF EXISTS generer_etat_trimestriel(INT, INT);")
    op.execute("DROP VIEW IF EXISTS v_etat_trimestriel_courant;")

    op.drop_table("workflow_form_schema")
    op.execute("DROP FUNCTION IF EXISTS get_template_applicable(TEXT, DATE);")
    op.drop_table("document_template")

    op.execute("""
        ALTER TABLE workflow_instance DROP COLUMN IF EXISTS numero_dossier_maitre;
    """)
    op.drop_table("recepisse_depot")
    op.execute("DROP VIEW IF EXISTS v_repertoire_mensuel_notaire;")
    op.execute("DROP FUNCTION IF EXISTS generer_numero_officiel(TEXT, TEXT);")

    for seq_name, _ in [
        ("seq_acte_notaire", "NOT"), ("seq_acte_huissier", "HUI"),
        ("seq_jugement_foncier", "JUG"), ("seq_certificat_ccfm", "CCF"),
        ("seq_arrete_urbanisme", "ARR"), ("seq_hypotheque", "HYP"),
        ("seq_mutation", "MUT"), ("seq_succession", "SUC"),
        ("seq_bordereau", "BOR"), ("seq_recepisse", "REC"),
        ("seq_dossier_maitre", "DOS"), ("seq_etat_trimestriel", "ETR"),
    ]:
        op.execute(f"DROP SEQUENCE IF EXISTS {seq_name};")
