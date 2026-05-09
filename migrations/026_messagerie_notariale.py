"""026_messagerie_notariale

FONCIER+ — Migration 026 : Messagerie sécurisée inter-notaires

Crée l'infrastructure de messagerie intra-chambre :
  - message_notarial        : messages chiffrés entre notaires
  - piece_jointe_message    : pièces jointes SHA-256 vérifiées
  - fil_discussion          : fils thématiques par dossier/parcelle
  - participant_fil         : membres d'un fil (chambre ou invité)
  - accusé_lecture          : accusés de réception horodatés

Règles métier :
  1. Un message ne peut être envoyé qu'entre notaires agréés (statut='agree')
  2. Un message dans une chambre n'est visible que par les notaires de cette chambre
  3. Les messages liés à un acte sont archivés automatiquement dans annf_archive_links
  4. Aucun DELETE physique — suppression = statut 'archive'
  5. SHA-256 chaîné sur chaque message (intégrité légale)

Revision ID: 026_messagerie_notariale
Revises: 025_bgu_workflow_etapes
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision      = "026_messagerie_notariale"
down_revision = "025_bgu_workflow_etapes"
branch_labels = None
depends_on    = None


def upgrade() -> None:

    # ── ENUMS ─────────────────────────────────────────────────────────────
    for name, values in [
        ("statut_message",
         "('envoye','lu','archive','supprime_expediteur','rappel_envoye')"),
        ("type_fil",
         "('dossier_foncier','consultation_juridique','acte_commun',"
         "'succession_complexe','general','alerte')"),
        ("priorite_message",
         "('normale','urgente','confidentielle')"),
    ]:
        op.execute(f"""
            DO $$ BEGIN
                CREATE TYPE {name} AS ENUM {values};
                EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)

    # ── 1. FIL_DISCUSSION ─────────────────────────────────────────────────
    op.create_table(
        "fil_discussion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("sujet", sa.String(300), nullable=False),
        sa.Column("type_fil", sa.Enum(
            "dossier_foncier","consultation_juridique","acte_commun",
            "succession_complexe","general","alerte",
            name="type_fil"), nullable=False),
        # Portée : chambre ou toutes chambres
        sa.Column("chambre_notariale", sa.String(200),
                  comment="NULL = accessible à toutes les chambres"),
        # Lien optionnel vers une entité métier
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("dossier_ref", sa.String(100),
                  comment="Numéro de dossier mutation/succession lié"),
        # Métadonnées
        sa.Column("cree_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("est_archive", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_fil_chambre",   "fil_discussion", ["chambre_notariale"])
    op.create_index("ix_fil_parcelle",  "fil_discussion", ["parcelle_id"])
    op.create_index("ix_fil_archive",   "fil_discussion", ["est_archive"])

    # ── 2. PARTICIPANT_FIL ────────────────────────────────────────────────
    op.create_table(
        "participant_fil",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("fil_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("fil_discussion.id"), nullable=False),
        sa.Column("notaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("notary_registry.id"), nullable=False),
        sa.Column("role_participant", sa.String(30), server_default="membre",
                  comment="initiateur|membre|observe"),
        sa.Column("actif", sa.Boolean, server_default="true"),
        sa.Column("joined_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("fil_id","notaire_id", name="uq_participant_fil_notaire"),
    )
    op.create_index("ix_pf_fil",     "participant_fil", ["fil_id"])
    op.create_index("ix_pf_notaire", "participant_fil", ["notaire_id"])

    # ── 3. MESSAGE_NOTARIAL ───────────────────────────────────────────────
    op.create_table(
        "message_notarial",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        # Fil de discussion
        sa.Column("fil_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("fil_discussion.id"), nullable=True,
                  comment="NULL = message direct (hors fil)"),
        # Expéditeur / destinataire
        sa.Column("expediteur_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("notary_registry.id"), nullable=False),
        sa.Column("destinataire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("notary_registry.id"), nullable=True,
                  comment="NULL = message au fil entier"),
        # Contenu
        sa.Column("sujet",   sa.String(300)),
        sa.Column("corps",   sa.Text, nullable=False),
        sa.Column("priorite", sa.Enum(
            "normale","urgente","confidentielle",
            name="priorite_message"),
            server_default="normale", nullable=False),
        # Chiffrement optionnel (PKI X.509)
        sa.Column("corps_chiffre",      sa.Text,
                  comment="Corps chiffré RSA-OAEP si confidentielle"),
        sa.Column("cle_publique_dest",  sa.Text,
                  comment="Empreinte clé publique du destinataire"),
        # Statut et intégrité
        sa.Column("statut", sa.Enum(
            "envoye","lu","archive","supprime_expediteur","rappel_envoye",
            name="statut_message"),
            server_default="envoye", nullable=False),
        sa.Column("sha256_message", sa.String(64), nullable=False,
                  comment="SHA-256(expediteur|destinataire|corps|timestamp)"),
        sa.Column("sha256_prev",    sa.String(64),
                  comment="SHA-256 du message précédent dans le fil — chaîne"),
        # Référence à un acte (archivage auto si renseigné)
        sa.Column("acte_ref_id", postgresql.UUID(as_uuid=True),
                  nullable=True,
                  comment="UUID de l'acte notarial concerné"),
        sa.Column("envoye_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("rappel_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_msg_fil",          "message_notarial", ["fil_id"])
    op.create_index("ix_msg_expediteur",   "message_notarial", ["expediteur_id"])
    op.create_index("ix_msg_destinataire", "message_notarial", ["destinataire_id"])
    op.create_index("ix_msg_statut",       "message_notarial", ["statut"])

    # ── 4. PIECE_JOINTE_MESSAGE ───────────────────────────────────────────
    op.create_table(
        "piece_jointe_message",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("message_notarial.id"), nullable=False),
        sa.Column("nom_fichier",  sa.String(300), nullable=False),
        sa.Column("type_mime",    sa.String(100), nullable=False),
        sa.Column("taille_bytes", sa.Integer),
        sa.Column("url_stockage", sa.Text,
                  comment="URL MinIO S3 de la pièce jointe"),
        sa.Column("sha256_fichier", sa.String(64), nullable=False,
                  comment="SHA-256 du contenu binaire — vérification intégrité"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_pj_message", "piece_jointe_message", ["message_id"])

    # ── 5. ACCUSE_LECTURE ─────────────────────────────────────────────────
    op.create_table(
        "accuse_lecture",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("message_notarial.id"), nullable=False),
        sa.Column("notaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("notary_registry.id"), nullable=False),
        sa.Column("lu_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("message_id","notaire_id", name="uq_accuse_lecture"),
    )
    op.create_index("ix_al_message", "accuse_lecture", ["message_id"])

    # ── TRIGGER : SHA-256 chaîné sur chaque message ───────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_message_sha256_chain()
        RETURNS TRIGGER AS $$
        DECLARE
            v_prev_sha TEXT;
            v_payload  TEXT;
        BEGIN
            -- SHA-256 du dernier message du même fil
            IF NEW.fil_id IS NOT NULL THEN
                SELECT sha256_message INTO v_prev_sha
                FROM message_notarial
                WHERE fil_id = NEW.fil_id
                  AND id != NEW.id
                ORDER BY envoye_at DESC
                LIMIT 1;
            END IF;

            NEW.sha256_prev := v_prev_sha;

            v_payload := COALESCE(NEW.expediteur_id::TEXT,'') || '|' ||
                         COALESCE(NEW.destinataire_id::TEXT,'') || '|' ||
                         LEFT(NEW.corps, 500) || '|' ||
                         COALESCE(v_prev_sha, 'GENESIS') || '|' ||
                         NOW()::TEXT;

            NEW.sha256_message := encode(sha256(v_payload::bytea), 'hex');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_message_sha256_chain
        BEFORE INSERT ON message_notarial
        FOR EACH ROW
        EXECUTE FUNCTION fn_message_sha256_chain();
    """)

    # ── TRIGGER : Vérifier que l'expéditeur est notaire agréé ────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_verifier_notaire_agree_message()
        RETURNS TRIGGER AS $$
        DECLARE v_statut TEXT;
        BEGIN
            SELECT statut_agrement INTO v_statut
            FROM notary_registry WHERE id = NEW.expediteur_id;

            IF v_statut IS NULL OR v_statut != 'agree' THEN
                RAISE EXCEPTION
                    'MESSAGE REFUSÉ: notaire % non agréé (statut: %)',
                    NEW.expediteur_id, COALESCE(v_statut,'introuvable');
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_verifier_notaire_agree_message
        BEFORE INSERT ON message_notarial
        FOR EACH ROW
        EXECUTE FUNCTION fn_verifier_notaire_agree_message();
    """)

    # ── TRIGGER : Marquer message comme 'lu' à la création de l'accusé ───
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_marquer_message_lu()
        RETURNS TRIGGER AS $$
        BEGIN
            UPDATE message_notarial
            SET statut = 'lu'
            WHERE id = NEW.message_id
              AND statut = 'envoye';
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_marquer_message_lu
        AFTER INSERT ON accuse_lecture
        FOR EACH ROW
        EXECUTE FUNCTION fn_marquer_message_lu();
    """)

    # ── Séquence et rapport ───────────────────────────────────────────────
    op.execute("""
        DO $$
        DECLARE v_tables INT;
        BEGIN
            SELECT COUNT(*) INTO v_tables
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN (
                'fil_discussion','participant_fil','message_notarial',
                'piece_jointe_message','accuse_lecture'
              );
            IF v_tables < 5 THEN
                RAISE EXCEPTION '026 incomplet : seulement % tables créées', v_tables;
            END IF;
            RAISE NOTICE '026 OK — % tables messagerie notariale créées', v_tables;
        END $$;
    """)


def downgrade() -> None:
    for tg, tbl in [
        ("tg_marquer_message_lu",           "accuse_lecture"),
        ("tg_verifier_notaire_agree_message","message_notarial"),
        ("tg_message_sha256_chain",          "message_notarial"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {tg} ON {tbl};")

    for fn in [
        "fn_marquer_message_lu",
        "fn_verifier_notaire_agree_message",
        "fn_message_sha256_chain",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}();")

    for tbl in ["accuse_lecture","piece_jointe_message",
                "message_notarial","participant_fil","fil_discussion"]:
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE;")

    for typ in ["statut_message","type_fil","priorite_message"]:
        op.execute(f"DROP TYPE IF EXISTS {typ};")
