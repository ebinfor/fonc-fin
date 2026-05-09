"""007_workflow_engine_et_triggers_complets

FONCIER+ v3.4.7 — Migration 007 : Moteur Workflow + Triggers Manquants

ANALYSE PRÉALABLE des 8 triggers du spec vs existant :
  T1 (RNP sans RNAF publié)      → tg_enforce_rnaf_publie_for_rnp (006) ✅ EXISTE
  T2 (autorisation Urbanisme)    → MANQUAIT — CRÉÉ ICI
  T3 (validation CCFM)           → tg_block_geom_without_ccfm (006) ✅ EXISTE
  T4 (hypothèque active)         → tg_block_acte_if_hypotheque_active (006) ✅ EXISTE
  T5 (décision Justice active)   → MANQUAIT — CRÉÉ ICI
  T6 (transaction_block CCFM)    → tg_auto_block_ccfm_suspension (004) ✅ EXISTE
  T7 (archivage ANNF auto)       → tg_archive_right_on_close (005) ✅ EXISTE
  T8 (audit global)              → tg_audit_* (006) ✅ EXISTE

GAPS COMBLÉS ICI :
  1. T2 : tg_enforce_urbanisme_autorisation — blocage géométrie sans autorisation Urbanisme
  2. T5 : tg_block_if_justice_suspension — blocage transfert si décision Justice SUSPENSION
  3. Moteur workflow : 6 tables + 6 triggers + seed des 7 définitions de workflow
  4. Index partiels pour unicité instance active par entité

Nouvelles tables (6) :
  workflow_definition
  workflow_step_def
  workflow_instance
  workflow_step_log
  workflow_signature
  workflow_escalade

Triggers workflow (4) :
  tg_workflow_echeance_auto       — calcule date_echeance à la création
  tg_workflow_complete_annf       — archivage ANNF automatique à completion
  tg_workflow_etape_sha256        — sha256 chaîné sur chaque step_log
  tg_workflow_escalade_auto       — escalade sur dépassement délai

Revision ID: 007_workflow_engine_et_triggers_complets
Revises: 006_schema_fondation_consolide
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "007_workflow_engine_et_triggers_complets"
down_revision = "006_schema_fondation_consolide"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ════════════════════════════════════════════════════════════════
    # TRIGGERS MANQUANTS (T2 et T5 du spec)
    # ════════════════════════════════════════════════════════════════

    # T2 : Autorisation Urbanisme pour toute modification géométrique ─
    # Complète T3 (migration 006) qui vérifie CCFM.
    # T2 vérifie spécifiquement l'autorisation Urbanisme via le RNAF workflow.
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_urbanisme_autorisation()
        RETURNS TRIGGER AS $$
        DECLARE
            v_arrete_id     UUID;
            v_arrete_statut TEXT;
        BEGIN
            -- Skip si géométrie inchangée
            IF OLD.geom IS NOT DISTINCT FROM NEW.geom THEN
                RETURN NEW;
            END IF;

            -- Vérifier qu'un arrêté d'urbanisme valide couvre cette parcelle
            -- via la chaîne : parcelle → ilot → lotissement → arretes_urbanisme
            SELECT au.id, au.statut
            INTO v_arrete_id, v_arrete_statut
            FROM parcelles p
            JOIN ilots i           ON i.id = p.ilot_id
            JOIN lotissements l    ON l.id = i.lotissement_id
            JOIN arretes_urbanisme au ON au.id = l.arrete_id
            WHERE p.id = NEW.id
            LIMIT 1;

            IF v_arrete_id IS NULL THEN
                RAISE EXCEPTION
                    'URBANISME BLOQUÉ [T2]: Parcelle % n''est rattachée à aucun '
                    'arrêté d''urbanisme. Obtenir un arrêté signé avant toute '
                    'modification géométrique.',
                    NEW.nicad;
            END IF;

            IF v_arrete_statut NOT IN ('actif', 'active') THEN
                RAISE EXCEPTION
                    'URBANISME BLOQUÉ [T2]: Arrêté d''urbanisme % au statut "%" '
                    'pour la parcelle %. Statut ACTIF requis.',
                    v_arrete_id, v_arrete_statut, NEW.nicad;
            END IF;

            -- Vérifier que le RNAF lié est bien dans un état valide (publié ou scellé)
            IF NOT EXISTS (
                SELECT 1
                FROM rnp_parcelle_link rpl
                JOIN rnaf_workflow rw ON rw.rnaf_id = rpl.rnaf_id
                WHERE rpl.parcelle_id = NEW.id
                  AND rw.statut IN ('scelle', 'publie')
            ) THEN
                -- Vérification douce : si pas de RNP encore, log seulement
                INSERT INTO audit_sensitive_ops (
                    operation, table_cible, entite_id, nicad,
                    module_source, niveau_criticite, sha256_op,
                    valeur_apres
                ) VALUES (
                    'UPDATE', 'parcelles', NEW.id, NEW.nicad,
                    'urbanisme', 'eleve',
                    encode(sha256(('T2_WARN|' || NEW.nicad || '|' || NOW()::TEXT)::bytea), 'hex'),
                    jsonb_build_object('avertissement', 'Modification géométrique sans RNAF publié')
                );
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_enforce_urbanisme_autorisation
        BEFORE UPDATE OF geom ON parcelles
        FOR EACH ROW
        EXECUTE FUNCTION enforce_urbanisme_autorisation();
    """)

    # T5 : Bloquer transfert/acte si décision Justice = SUSPENSION ───
    # Complète T4 (migration 006) sur hypothèques.
    # T5 bloque si une décision judiciaire ACTIVE suspend la parcelle.
    op.execute("""
        CREATE OR REPLACE FUNCTION block_if_justice_suspension()
        RETURNS TRIGGER AS $$
        DECLARE
            v_parcelle_id UUID;
            v_ref_decision TEXT;
        BEGIN
            -- Récupérer la parcelle cible (propriete_parcelle ou property_transfers)
            v_parcelle_id := COALESCE(NEW.parcelle_id, NEW.rnp_parcelle_id::UUID);

            IF v_parcelle_id IS NULL THEN
                RETURN NEW;
            END IF;

            -- Vérifier décision judiciaire active bloquante
            SELECT dj.id::TEXT INTO v_ref_decision
            FROM decisions_judiciaires dj
            WHERE dj.parcelle_id = v_parcelle_id
              AND dj.type_decision IN ('SUSPENSION', 'GEL', 'SAISIE_CONSERVATOIRE')
              AND dj.statut NOT IN ('levee', 'annulee', 'archive')
            ORDER BY dj.date_decision DESC
            LIMIT 1;

            IF v_ref_decision IS NOT NULL THEN
                RAISE EXCEPTION
                    'JUSTICE BLOQUÉ [T5]: Transaction sur parcelle % interdite. '
                    'Décision judiciaire active (réf: %). '
                    'Requiert mainlevée judiciaire explicite (JUST_PRESIDENT).',
                    v_parcelle_id, v_ref_decision;
            END IF;

            -- Vérifier aussi via parcel_freeze_events (005)
            IF EXISTS (
                SELECT 1 FROM parcel_freeze_events pfe
                WHERE pfe.parcelle_id = v_parcelle_id
                  AND pfe.gele = true
                  AND pfe.type_gel = 'justice'
                  AND NOT EXISTS (
                    SELECT 1 FROM parcel_freeze_events pfe2
                    WHERE pfe2.parcelle_id = v_parcelle_id
                      AND pfe2.gele = false
                      AND pfe2.created_at > pfe.gele_at
                  )
            ) THEN
                RAISE EXCEPTION
                    'JUSTICE BLOQUÉ [T5]: Parcelle % gelée ❄️ par décision judiciaire. '
                    'Aucune transaction possible jusqu''au dégel.',
                    v_parcelle_id;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    # Appliquer sur property_transfers et actes_notaries
    op.execute("""
        CREATE TRIGGER tg_block_if_justice_suspension_transfers
        BEFORE INSERT ON property_transfers
        FOR EACH ROW
        EXECUTE FUNCTION block_if_justice_suspension();
    """)
    op.execute("""
        CREATE TRIGGER tg_block_if_justice_suspension_actes
        BEFORE INSERT ON actes_notaries
        FOR EACH ROW
        EXECUTE FUNCTION block_if_justice_suspension();
    """)

    # ════════════════════════════════════════════════════════════════
    # ENUMS WORKFLOW
    # ════════════════════════════════════════════════════════════════

    for name, values in [
        ("type_workflow",
         "('RNAF','RNP','CCFM','NOTAIRE','JUSTICE','TRANSFERT','BGU','URBANISME','ANNF','DAR')"),
        ("statut_instance",
         "('en_cours','en_attente','suspendu','complete','rejete','annule','expire')"),
        ("statut_etape",
         "('en_attente','en_cours','validee','rejetee','ignoree','expiree')"),
        ("type_validation",
         "('approbation','signature','scellement','horodatage','constat','publication')"),
        ("type_escalade",
         "('relance','escalade_haut','auto_rejet')"),
    ]:
        op.execute(f"""
            DO $$ BEGIN
                CREATE TYPE {name} AS ENUM {values};
                EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)

    # ════════════════════════════════════════════════════════════════
    # TABLES WORKFLOW
    # ════════════════════════════════════════════════════════════════

    # ── workflow_definition ──────────────────────────────────────────
    op.create_table(
        "workflow_definition",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("type_workflow", sa.Enum(
                  "RNAF","RNP","CCFM","NOTAIRE","JUSTICE","TRANSFERT",
                  "BGU","URBANISME","ANNF","DAR", name="type_workflow"),
                  nullable=False),
        sa.Column("nom",         sa.String(200), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("version",     sa.Integer, server_default="1"),
        sa.Column("delai_max_jours",          sa.Integer),
        sa.Column("rejet_possible_partout",   sa.Boolean, server_default="true"),
        sa.Column("signature_finale_requise", sa.Boolean, server_default="true"),
        sa.Column("annf_auto",   sa.Boolean, server_default="true"),
        sa.Column("is_locked",   sa.Boolean, server_default="false"),
        sa.Column("is_active",   sa.Boolean, server_default="true"),
        sa.Column("cree_par",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("created_at",  sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",  sa.DateTime(timezone=True)),
        sa.UniqueConstraint("type_workflow", name="uq_wf_def_type"),
    )

    # ── workflow_step_def ────────────────────────────────────────────
    op.create_table(
        "workflow_step_def",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("definition_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_definition.id"), nullable=False),
        sa.Column("ordre",       sa.Integer, nullable=False),
        sa.Column("code_etape",  sa.String(50), nullable=False),
        sa.Column("nom_etape",   sa.String(200), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("role_requis", sa.String(100), nullable=False),
        sa.Column("role_backup", sa.String(100)),
        sa.Column("type_validation", sa.Enum(
                  "approbation","signature","scellement","horodatage",
                  "constat","publication", name="type_validation"), nullable=False),
        sa.Column("est_obligatoire",       sa.Boolean, server_default="true"),
        sa.Column("delai_max_heures",      sa.Integer),
        sa.Column("condition_entree",      sa.Text),
        sa.Column("condition_sortie",      sa.Text),
        sa.Column("documents_requis",      postgresql.JSONB),
        sa.Column("retour_etape_rejet",    sa.Integer),
        sa.Column("action_post_validation",sa.String(100)),
        sa.UniqueConstraint("definition_id", "ordre",      name="uq_step_def_ordre"),
        sa.UniqueConstraint("definition_id", "code_etape", name="uq_step_def_code"),
    )
    op.create_index("ix_wsd_definition", "workflow_step_def", ["definition_id"])

    # ── workflow_instance ────────────────────────────────────────────
    op.create_table(
        "workflow_instance",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("definition_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_definition.id"), nullable=False),
        sa.Column("entite_type",  sa.String(50), nullable=False),
        sa.Column("entite_id",    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nicad",        sa.String(30)),
        sa.Column("parcelle_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),
        sa.Column("statut", sa.Enum(
                  "en_cours","en_attente","suspendu","complete",
                  "rejete","annule","expire", name="statut_instance"),
                  server_default="en_cours", nullable=False),
        sa.Column("etape_courante_ordre", sa.Integer, server_default="1"),
        sa.Column("etape_courante_code",  sa.String(50)),
        sa.Column("contexte_json",        postgresql.JSONB),
        sa.Column("date_debut",           sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("date_echeance",        sa.DateTime(timezone=True)),
        sa.Column("date_completion",      sa.DateTime(timezone=True)),
        sa.Column("attendu_de_role",      sa.String(100)),
        sa.Column("attendu_de_user",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("motif_rejet",          sa.Text),
        sa.Column("rejete_par",           postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("rejete_at",            sa.DateTime(timezone=True)),
        sa.Column("sha256_completion",    sa.String(64)),
        sa.Column("demarre_par",          postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at",           sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",           sa.DateTime(timezone=True)),
    )
    op.create_index("ix_wfi_entite",   "workflow_instance", ["entite_id"])
    op.create_index("ix_wfi_statut",   "workflow_instance", ["statut"])
    op.create_index("ix_wfi_parcelle", "workflow_instance", ["parcelle_id"])
    # Index partiel : une seule instance active par entité
    op.execute("""
        CREATE UNIQUE INDEX ix_wfi_unique_active
        ON workflow_instance (definition_id, entite_id)
        WHERE statut IN ('en_cours', 'en_attente', 'suspendu');
    """)

    # ── workflow_step_log ────────────────────────────────────────────
    op.create_table(
        "workflow_step_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("instance_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=False),
        sa.Column("step_ordre",   sa.Integer, nullable=False),
        sa.Column("step_code",    sa.String(50), nullable=False),
        sa.Column("step_nom",     sa.String(200)),
        sa.Column("statut_etape", sa.Enum(
                  "en_attente","en_cours","validee","rejetee",
                  "ignoree","expiree", name="statut_etape"), nullable=False),
        sa.Column("acteur_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("role_acteur",  sa.String(100)),
        sa.Column("commentaire",  sa.Text),
        sa.Column("decision",     sa.String(20)),
        sa.Column("documents_soumis", postgresql.JSONB),
        sa.Column("donnees_avant",    postgresql.JSONB),
        sa.Column("donnees_apres",    postgresql.JSONB),
        sa.Column("duree_heures",     sa.Numeric(8, 2)),
        sa.Column("sha256_step",      sa.String(64), nullable=False),
        sa.Column("sha256_prev",      sa.String(64)),
        sa.Column("created_at",       sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_wsl_instance", "workflow_step_log", ["instance_id"])
    op.create_index("ix_wsl_code",     "workflow_step_log", ["step_code"])

    # ── workflow_signature ───────────────────────────────────────────
    op.create_table(
        "workflow_signature",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("instance_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=False),
        sa.Column("step_log_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_step_log.id"), nullable=False),
        sa.Column("signataire_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role_signataire",  sa.String(100), nullable=False),
        sa.Column("contenu_signe",    sa.Text, nullable=False),
        sa.Column("sha256_contenu",   sa.String(64), nullable=False),
        sa.Column("signature_b64",    sa.Text, nullable=False),
        sa.Column("algorithme",       sa.String(50), server_default="HMAC-SHA256"),
        sa.Column("certificat_ref",   sa.String(200)),
        sa.Column("horodatage_at",    sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("horodatage_tsp",   sa.String(500)),
        sa.Column("est_valide",       sa.Boolean, server_default="true"),
        sa.Column("verifie_at",       sa.DateTime(timezone=True)),
        sa.Column("verifie_par",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.UniqueConstraint("step_log_id", name="uq_sig_step_log"),
    )
    op.create_index("ix_wsig_instance",    "workflow_signature", ["instance_id"])
    op.create_index("ix_wsig_signataire",  "workflow_signature", ["signataire_id"])

    # ── workflow_escalade ────────────────────────────────────────────
    op.create_table(
        "workflow_escalade",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("instance_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=False),
        sa.Column("step_code",    sa.String(50), nullable=False),
        sa.Column("step_ordre",   sa.Integer,    nullable=False),
        sa.Column("type_escalade",sa.Enum("relance","escalade_haut","auto_rejet",
                                           name="type_escalade"), nullable=False),
        sa.Column("retard_heures",  sa.Numeric(8, 2), nullable=False),
        sa.Column("notifie_role",   sa.String(100)),
        sa.Column("notifie_user",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("message_escalade",sa.Text, nullable=False),
        sa.Column("est_traitee",    sa.Boolean, server_default="false"),
        sa.Column("traitee_at",     sa.DateTime(timezone=True)),
        sa.Column("created_at",     sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_wesc_instance", "workflow_escalade", ["instance_id"])

    # ════════════════════════════════════════════════════════════════
    # TRIGGERS WORKFLOW
    # ════════════════════════════════════════════════════════════════

    # TW1 : Calcul automatique de l'échéance à la création ───────────
    op.execute("""
        CREATE OR REPLACE FUNCTION wf_compute_echeance()
        RETURNS TRIGGER AS $$
        DECLARE
            v_delai_jours INT;
        BEGIN
            SELECT d.delai_max_jours INTO v_delai_jours
            FROM workflow_definition d
            WHERE d.id = NEW.definition_id;

            IF v_delai_jours IS NOT NULL THEN
                NEW.date_echeance := NEW.date_debut + (v_delai_jours || ' days')::INTERVAL;
            END IF;

            -- Définir le rôle attendu à l'étape 1
            SELECT s.role_requis, s.code_etape
            INTO NEW.attendu_de_role, NEW.etape_courante_code
            FROM workflow_step_def s
            WHERE s.definition_id = NEW.definition_id
              AND s.ordre = 1
            LIMIT 1;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_wf_compute_echeance
        BEFORE INSERT ON workflow_instance
        FOR EACH ROW
        EXECUTE FUNCTION wf_compute_echeance();
    """)

    # TW2 : SHA-256 chaîné sur chaque étape ──────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION wf_step_sha256()
        RETURNS TRIGGER AS $$
        DECLARE
            v_prev_sha TEXT;
            v_payload  TEXT;
        BEGIN
            -- Récupérer le SHA de l'étape précédente
            SELECT wsl.sha256_step INTO v_prev_sha
            FROM workflow_step_log wsl
            WHERE wsl.instance_id = NEW.instance_id
            ORDER BY wsl.created_at DESC
            LIMIT 1;

            NEW.sha256_prev := v_prev_sha;

            -- Calculer le SHA de cette étape
            v_payload := NEW.instance_id::TEXT || '|' ||
                         NEW.step_code || '|' ||
                         NEW.statut_etape || '|' ||
                         COALESCE(NEW.decision, '') || '|' ||
                         COALESCE(NEW.acteur_id::TEXT, '') || '|' ||
                         COALESCE(v_prev_sha, 'GENESIS') || '|' ||
                         NOW()::TEXT;

            NEW.sha256_step := encode(sha256(v_payload::bytea), 'hex');

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_wf_step_sha256
        BEFORE INSERT ON workflow_step_log
        FOR EACH ROW
        EXECUTE FUNCTION wf_step_sha256();
    """)

    # TW3 : Archivage ANNF automatique à completion du workflow ───────
    op.execute("""
        CREATE OR REPLACE FUNCTION wf_auto_annf_on_complete()
        RETURNS TRIGGER AS $$
        DECLARE
            v_do_annf     BOOLEAN;
            v_ida         TEXT;
            v_snapshot    JSONB;
            v_sha256      TEXT;
            v_admin_id    UUID;
        BEGIN
            IF NEW.statut = 'complete' AND OLD.statut != 'complete' THEN
                -- Vérifier si ANNF auto requis
                SELECT d.annf_auto INTO v_do_annf
                FROM workflow_definition d
                WHERE d.id = NEW.definition_id;

                IF v_do_annf THEN
                    SELECT id INTO v_admin_id FROM users WHERE role = 'ADMIN' LIMIT 1;
                    v_ida := generate_annf_ida(EXTRACT(YEAR FROM NOW())::INT);

                    v_snapshot := jsonb_build_object(
                        'workflow_instance_id', NEW.id,
                        'definition_id',  NEW.definition_id,
                        'entite_type',    NEW.entite_type,
                        'entite_id',      NEW.entite_id,
                        'nicad',          NEW.nicad,
                        'date_debut',     NEW.date_debut,
                        'date_completion',NOW(),
                        'sha256_completion', NEW.sha256_completion,
                        'nb_etapes',      (
                            SELECT COUNT(*) FROM workflow_step_log
                            WHERE instance_id = NEW.id
                        )
                    );

                    v_sha256 := encode(sha256(v_snapshot::TEXT::bytea), 'hex');

                    INSERT INTO annf_archive_links (
                        type_archive, entite_id, entite_table,
                        snapshot_json, sha256_snapshot, ida,
                        archive_par, scelle
                    ) VALUES (
                        CASE NEW.entite_type
                            WHEN 'rnaf'         THEN 'rnaf'::type_archive_annf
                            WHEN 'rnp_demande'  THEN 'rnp'::type_archive_annf
                            WHEN 'demande_ccfm' THEN 'ccfm'::type_archive_annf
                            WHEN 'acte_notarie' THEN 'acte_notarie'::type_archive_annf
                            WHEN 'litige'       THEN 'decision_justice'::type_archive_annf
                            ELSE                     'rnaf'::type_archive_annf
                        END,
                        NEW.id, 'workflow_instance',
                        v_snapshot, v_sha256, v_ida,
                        COALESCE(v_admin_id, NEW.demarre_par), false
                    );
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_wf_auto_annf_on_complete
        AFTER UPDATE OF statut ON workflow_instance
        FOR EACH ROW
        WHEN (NEW.statut = 'complete' AND OLD.statut IS DISTINCT FROM NEW.statut)
        EXECUTE FUNCTION wf_auto_annf_on_complete();
    """)

    # TW4 : Escalade automatique sur dépassement délai ────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION wf_check_escalade()
        RETURNS TRIGGER AS $$
        DECLARE
            v_delai_heures INT;
            v_retard       NUMERIC;
            v_role_backup  TEXT;
        BEGIN
            -- Vérifier le délai de l'étape courante
            SELECT s.delai_max_heures, s.role_backup
            INTO v_delai_heures, v_role_backup
            FROM workflow_step_def s
            WHERE s.definition_id = NEW.definition_id
              AND s.code_etape = NEW.etape_courante_code
            LIMIT 1;

            IF v_delai_heures IS NOT NULL AND NEW.statut = 'en_attente' THEN
                v_retard := EXTRACT(EPOCH FROM (NOW() - NEW.updated_at)) / 3600.0;

                IF v_retard > v_delai_heures THEN
                    -- Créer une escalade si pas encore traitée
                    INSERT INTO workflow_escalade (
                        instance_id, step_code, step_ordre,
                        type_escalade, retard_heures,
                        notifie_role, message_escalade
                    )
                    SELECT
                        NEW.id,
                        NEW.etape_courante_code,
                        NEW.etape_courante_ordre,
                        CASE
                            WHEN v_retard > v_delai_heures * 2 THEN 'escalade_haut'
                            ELSE 'relance'
                        END::type_escalade,
                        v_retard,
                        COALESCE(v_role_backup, NEW.attendu_de_role),
                        'Délai dépassé de ' || ROUND(v_retard - v_delai_heures, 1)
                        || 'h sur l''étape ' || NEW.etape_courante_code
                        || ' du workflow ' || NEW.entite_type
                    WHERE NOT EXISTS (
                        SELECT 1 FROM workflow_escalade we
                        WHERE we.instance_id = NEW.id
                          AND we.step_code = NEW.etape_courante_code
                          AND we.est_traitee = false
                          AND we.created_at > NOW() - INTERVAL '6 hours'
                    );
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_wf_check_escalade
        AFTER UPDATE ON workflow_instance
        FOR EACH ROW
        WHEN (NEW.statut = 'en_attente')
        EXECUTE FUNCTION wf_check_escalade();
    """)

    # ════════════════════════════════════════════════════════════════
    # SEED : 7 DÉFINITIONS DE WORKFLOW + ÉTAPES
    # ════════════════════════════════════════════════════════════════

    op.execute("""
        -- Insérer les définitions de workflow
        INSERT INTO workflow_definition (
            id, type_workflow, nom, description,
            delai_max_jours, signature_finale_requise, annf_auto
        ) VALUES
        (gen_random_uuid(), 'RNAF',
         'Workflow RNAF — Registre National des Arrêtés Fonciers',
         'De la rédaction de l''arrêté à la publication officielle',
         30, true, true),
        (gen_random_uuid(), 'RNP',
         'Workflow RNP — Registre National Parcellaire',
         'De la demande de numérotation à la génération du NICAD',
         15, true, true),
        (gen_random_uuid(), 'CCFM',
         'Workflow CCFM — Certificat de Conformité Foncière et Minière',
         'De la demande au certificat scellé',
         21, true, true),
        (gen_random_uuid(), 'NOTAIRE',
         'Workflow Notaire — Actes notariaux',
         'De la rédaction à l''enregistrement DAR',
         7, true, true),
        (gen_random_uuid(), 'JUSTICE',
         'Workflow Justice — Litiges fonciers',
         'De la saisine à la décision notifiée',
         90, true, true),
        (gen_random_uuid(), 'TRANSFERT',
         'Workflow Transfert — Cession de propriété',
         'De la demande au transfert enregistré en ANNF',
         14, true, true),
        (gen_random_uuid(), 'BGU',
         'Workflow BGU — Bureau de Gestion Urbaine',
         'Import, contrôle, validation et scellement géospatial',
         10, true, false)
        ON CONFLICT (type_workflow) DO NOTHING;
    """)

    # Étapes RNAF
    op.execute("""
        INSERT INTO workflow_step_def (
            id, definition_id, ordre, code_etape, nom_etape,
            role_requis, type_validation, delai_max_heures,
            est_obligatoire, action_post_validation
        )
        SELECT
            gen_random_uuid(),
            d.id,
            s.ordre, s.code_etape, s.nom_etape,
            s.role_requis, s.type_validation::type_validation,
            s.delai_max_heures, s.est_obligatoire,
            s.action_post_validation
        FROM workflow_definition d,
        (VALUES
            (1,'SOUMETTRE','Soumission du brouillon',
             'URBANISTE','approbation',48,true,NULL),
            (2,'VERIFIER','Vérification technique',
             'DIRECTEUR_URBANISME','approbation',72,true,NULL),
            (3,'SCELLER','Scellement officiel',
             'SECRETAIRE_GENERAL','scellement',24,true,NULL),
            (4,'PUBLIER_JO','Publication Journal Officiel',
             'EDITEUR_JO','publication',48,true,'PUBLIER_RNAF_JO'),
            (5,'CONFIRMER_PUBLIE','Confirmation publication',
             'DIRECTEUR_URBANISME','approbation',24,true,'ACTIVER_RNP')
        ) AS s(ordre,code_etape,nom_etape,role_requis,type_validation,
               delai_max_heures,est_obligatoire,action_post_validation)
        WHERE d.type_workflow = 'RNAF'
        ON CONFLICT (definition_id, code_etape) DO NOTHING;
    """)

    # Étapes RNP
    op.execute("""
        INSERT INTO workflow_step_def (
            id, definition_id, ordre, code_etape, nom_etape,
            role_requis, type_validation, delai_max_heures,
            est_obligatoire, action_post_validation
        )
        SELECT
            gen_random_uuid(), d.id,
            s.ordre, s.code_etape, s.nom_etape,
            s.role_requis, s.type_validation::type_validation,
            s.delai_max_heures, true, s.action
        FROM workflow_definition d,
        (VALUES
            (1,'INIT','Initialisation demande','GEOMETRE','approbation',24,NULL),
            (2,'INSTRUIRE','Instruction dossier','CHEF_SERVICE_CADASTRE','approbation',48,NULL),
            (3,'SAISIE','Saisie géométrique','GEOMETRE','constat',72,NULL),
            (4,'VALIDER','Validation cadastrale','DIRECTEUR_CADASTRE','approbation',24,NULL),
            (5,'GENERER_NICAD','Génération NICAD','DIRECTEUR_CADASTRE','scellement',8,'GENERER_NICAD_AUTO'),
            (6,'ARCHIVER_DAR','Archivage DAR','CHEF_DAR','approbation',24,'ARCHIVER_RNP_ANNF')
        ) AS s(ordre,code_etape,nom_etape,role_requis,type_validation,delai_max_heures,action)
        WHERE d.type_workflow = 'RNP'
        ON CONFLICT (definition_id, code_etape) DO NOTHING;
    """)

    # Étapes CCFM
    op.execute("""
        INSERT INTO workflow_step_def (
            id, definition_id, ordre, code_etape, nom_etape,
            role_requis, type_validation, delai_max_heures,
            est_obligatoire, action_post_validation
        )
        SELECT
            gen_random_uuid(), d.id,
            s.ordre, s.code_etape, s.nom_etape,
            s.role_requis, s.type_validation::type_validation,
            s.delai_max_heures, true, s.action
        FROM workflow_definition d,
        (VALUES
            (1,'RECEPTION','Réception demande','GUICHETIER_CCFM','approbation',24,NULL),
            (2,'VERIFICATION_ADMIN','Vérification administrative','CHEF_CCFM','approbation',48,NULL),
            (3,'CONSTAT_TERRAIN','Constat terrain','TOPOGRAPHE','constat',72,NULL),
            (4,'VALIDATION_DIRECTEUR','Validation Directeur','DIRECTEUR_URBANISME','approbation',24,NULL),
            (5,'SCELLEMENT','Scellement certificat','SECRETAIRE_GENERAL','scellement',8,'GENERER_QR_CODE'),
            (6,'ARCHIVAGE','Archivage final','CHEF_DAR','approbation',24,'ARCHIVER_CCFM_ANNF')
        ) AS s(ordre,code_etape,nom_etape,role_requis,type_validation,delai_max_heures,action)
        WHERE d.type_workflow = 'CCFM'
        ON CONFLICT (definition_id, code_etape) DO NOTHING;
    """)

    # Étapes TRANSFERT
    op.execute("""
        INSERT INTO workflow_step_def (
            id, definition_id, ordre, code_etape, nom_etape,
            role_requis, type_validation, delai_max_heures,
            est_obligatoire, action_post_validation
        )
        SELECT
            gen_random_uuid(), d.id,
            s.ordre, s.code_etape, s.nom_etape,
            s.role_requis, s.type_validation::type_validation,
            s.delai_max_heures, true, s.action
        FROM workflow_definition d,
        (VALUES
            (1,'DEMANDE','Demande de transfert','NOTAIRE','approbation',24,NULL),
            (2,'CCFM_GATE','Vérification gate CCFM','CHEF_CCFM','approbation',48,'CHECK_CCFM_GATE'),
            (3,'SIGNATURE_NOTAIRE','Signature notariale','NOTAIRE','signature',24,'CHECK_LITIGES'),
            (4,'ENREGISTREMENT','Enregistrement official','DIRECTEUR_DOMAINE','approbation',24,NULL),
            (5,'MISE_A_JOUR_DROIT','Mise à jour droits fonciers','DIRECTEUR_CADASTRE','scellement',8,'CREER_DROIT_VERSION'),
            (6,'ANNF_ARCHIVE','Archivage ANNF','CHEF_DAR','approbation',24,'ARCHIVER_TRANSFERT_ANNF')
        ) AS s(ordre,code_etape,nom_etape,role_requis,type_validation,delai_max_heures,action)
        WHERE d.type_workflow = 'TRANSFERT'
        ON CONFLICT (definition_id, code_etape) DO NOTHING;
    """)

    # ════════════════════════════════════════════════════════════════
    # VUES WORKFLOW
    # ════════════════════════════════════════════════════════════════

    op.execute("""
        CREATE OR REPLACE VIEW v_workflow_en_cours AS
        SELECT
            wi.id                   AS instance_id,
            wd.type_workflow,
            wd.nom                  AS workflow_nom,
            wi.entite_type,
            wi.entite_id,
            wi.nicad,
            wi.statut,
            wi.etape_courante_code,
            wi.etape_courante_ordre,
            wi.attendu_de_role,
            -- Prochaine étape
            wsd_next.nom_etape      AS prochaine_etape,
            wsd_next.role_requis    AS prochain_role_requis,
            wsd_next.delai_max_heures AS prochain_delai_heures,
            -- Retard
            CASE
                WHEN wi.date_echeance < NOW() AND wi.statut != 'complete'
                THEN EXTRACT(EPOCH FROM (NOW() - wi.date_echeance)) / 3600
                ELSE NULL
            END                     AS retard_heures,
            wi.date_debut,
            wi.date_echeance,
            -- Nb escalades non traitées
            (SELECT COUNT(*) FROM workflow_escalade we
             WHERE we.instance_id = wi.id AND we.est_traitee = false
            )                       AS nb_escalades_actives,
            -- Nb étapes validées
            (SELECT COUNT(*) FROM workflow_step_log wsl
             WHERE wsl.instance_id = wi.id AND wsl.statut_etape = 'validee'
            )                       AS nb_etapes_validees,
            -- Nb total étapes
            (SELECT COUNT(*) FROM workflow_step_def wsd
             WHERE wsd.definition_id = wi.definition_id AND wsd.est_obligatoire = true
            )                       AS nb_etapes_totales
        FROM workflow_instance wi
        JOIN workflow_definition wd ON wd.id = wi.definition_id
        LEFT JOIN workflow_step_def wsd_next
               ON wsd_next.definition_id = wi.definition_id
              AND wsd_next.ordre = wi.etape_courante_ordre
        WHERE wi.statut NOT IN ('complete', 'rejete', 'annule', 'expire')
        ORDER BY
            CASE WHEN wi.date_echeance < NOW() THEN 0 ELSE 1 END,
            wi.date_echeance ASC NULLS LAST;
    """)

    op.execute("""
        CREATE OR REPLACE VIEW v_workflow_retards AS
        SELECT
            wi.id               AS instance_id,
            wd.type_workflow,
            wi.entite_type,
            wi.entite_id,
            wi.nicad,
            wi.etape_courante_code,
            wi.attendu_de_role,
            EXTRACT(EPOCH FROM (NOW() - wi.date_echeance)) / 3600
                                AS retard_heures,
            wi.date_echeance,
            (SELECT COUNT(*) FROM workflow_escalade we
             WHERE we.instance_id = wi.id AND we.est_traitee = false
            )                   AS nb_escalades
        FROM workflow_instance wi
        JOIN workflow_definition wd ON wd.id = wi.definition_id
        WHERE wi.statut IN ('en_cours', 'en_attente')
          AND wi.date_echeance IS NOT NULL
          AND wi.date_echeance < NOW()
        ORDER BY retard_heures DESC;
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_workflow_retards CASCADE")
    op.execute("DROP VIEW IF EXISTS v_workflow_en_cours CASCADE")

    for trigger, table in [
        ("tg_wf_check_escalade",             "workflow_instance"),
        ("tg_wf_auto_annf_on_complete",      "workflow_instance"),
        ("tg_wf_step_sha256",                "workflow_step_log"),
        ("tg_wf_compute_echeance",           "workflow_instance"),
        ("tg_block_if_justice_suspension_actes",    "actes_notaries"),
        ("tg_block_if_justice_suspension_transfers","property_transfers"),
        ("tg_enforce_urbanisme_autorisation", "parcelles"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    for fn in [
        "wf_compute_echeance", "wf_step_sha256",
        "wf_auto_annf_on_complete", "wf_check_escalade",
        "block_if_justice_suspension", "enforce_urbanisme_autorisation",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")

    for table in [
        "workflow_escalade", "workflow_signature",
        "workflow_step_log", "workflow_instance",
        "workflow_step_def", "workflow_definition",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    for name in [
        "type_workflow", "statut_instance", "statut_etape",
        "type_validation", "type_escalade",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")
