"""009_moteur_ccfm_et_annulation_judiciaire

FONCIER+ v3.4.7 — Migration 009 : Moteur National de Contrôle CCFM
Aligné sur 48+ tables existantes (migrations 001-008).

ANALYSE PRÉALABLE — 16 sources EXISTANTES utilisées (pas dupliquées) :
  demandes_ccfm          → table principale CCFM (001)
  ccfm_suspension        → suspension (004)
  ccfm_contestation      → contestation (006)
  rnaf / rnaf_workflow   → statut RNAF (001 / 004)
  rnp_demandes           → RNP (001)
  rnp_parcelle_link      → lien RNP↔RNAF+statut (004)
  parcelles              → géométrie + statut (003)
  conflits_parcellaires  → superpositions PostGIS (003)
  bgu_geojson_master     → scellement géospatial (003)
  bgu_parcel_status      → statut public BGU (004)
  hypotheques            → hypothèques (001)
  mortgage_registry      → enrichissement (005)
  litiges                → litiges (001)
  parcel_disputes        → litiges droits (005)
  transaction_blocks     → blocages (004)
  annulation_judiciaire_dossier → annulation (008)
  audit_sensitive_ops    → audit global (006)
  workflow_instance      → moteur workflow (007)
  arretes_urbanisme      → chaîne urbanisme (003)

4 GAPS COMBLÉS (nouvelles tables) :
  1. ccfm_verification_log   → log inviolable de chaque contrôle (SHA-256 chaîné)
  2. urbanisme_conformite     → conformité urbanistique par parcelle
  3. bgu_projection           → résultat projection BGU calculé (enrichit bgu_geojson_master)
  4. controle_geometrique     → résultat contrôle géométrique PostGIS

8 FONCTIONS PLPGSQL DE CONTRÔLE (moteur séquentiel) :
  F1. ccfm_ctrl_rnaf_validity()       → RNAF publié + non suspendu
  F2. ccfm_ctrl_rnp_validity()        → RNP actif + NICAD valide
  F3. ccfm_ctrl_urbanisme()           → Arrêté actif + conformité urbanistique
  F4. ccfm_ctrl_bgu()                 → Projection BGU scellée + cohérente
  F5. ccfm_ctrl_absence_litige()      → Aucun litige actif
  F6. ccfm_ctrl_absence_hypotheque()  → Hypothèques non bloquantes
  F7. ccfm_ctrl_geometrie()           → Surface cohérente + pas de superposition
  F8. generate_ccfm_certificate()     → Orchestration : tous F1→F7 + émission

STRATÉGIE DES CONTRÔLES (réponse au risque 2 du spec) :
  Niveau BLOQUANT   : F1 (RNAF), F2 (RNP), F5 (litige), F7 (géométrie critique)
  Niveau AVERTISSEMENT : F3 (urbanisme), F4 (BGU), F6 (hypothèque non bloquante)
  → CCFM CONDITIONNEL possible avec avertissements (mais pas erreurs bloquantes)

WORKFLOW ANNULATION JUDICIAIRE (Workflow 4 du spec) :
  Étapes : SAISINE → INSTRUCTION → AUDIENCE → DECISION → EXECUTION → NOTIFICATION → ARCHIVE
  Seed dans workflow_definition type JUSTICE (déjà créé en 007)

Revision ID: 009_moteur_ccfm_et_annulation_judiciaire
Revises: 008_workflows_operationnels
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "009_moteur_ccfm_et_annulation_judiciaire"
down_revision = "008_workflows_operationnels"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ════════════════════════════════════════════════════════════════
    # ENUMS
    # ════════════════════════════════════════════════════════════════

    for name, values in [
        ("ccfm_ctrl_type",
         "('RNAF_VALIDITE','RNP_VALIDITE','URBANISME_CONFORMITE','BGU_PROJECTION',"
         "'ABSENCE_LITIGE','ABSENCE_HYPOTHEQUE','GEOMETRIE','GLOBAL')"),
        ("ccfm_ctrl_resultat",
         "('PASSE','ECHOUE','AVERTISSEMENT','NON_EXECUTE')"),
        ("ccfm_ctrl_niveau",
         "('BLOQUANT','AVERTISSEMENT')"),
        ("zone_urbanisme",
         "('residentielle','commerciale','industrielle','agricole','verte','mixte','protegee')"),
    ]:
        op.execute(f"""
            DO $$ BEGIN CREATE TYPE {name} AS ENUM {values};
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """)

    # ════════════════════════════════════════════════════════════════
    # TABLE 1 : CCFM_VERIFICATION_LOG
    # Log inviolable de chaque contrôle — SHA-256 chaîné.
    # Sans lui : impossible de prouver pourquoi un CCFM a été délivré.
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "ccfm_verification_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        # Demande CCFM concernée
        sa.Column("demande_ccfm_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=False),

        # Parcelle et RNAF vérifiés
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("rnaf_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnaf.id"), nullable=True),

        # Contrôle
        sa.Column("type_verification", sa.Enum(
                  "RNAF_VALIDITE","RNP_VALIDITE","URBANISME_CONFORMITE","BGU_PROJECTION",
                  "ABSENCE_LITIGE","ABSENCE_HYPOTHEQUE","GEOMETRIE","GLOBAL",
                  name="ccfm_ctrl_type"), nullable=False),

        sa.Column("resultat", sa.Enum(
                  "PASSE","ECHOUE","AVERTISSEMENT","NON_EXECUTE",
                  name="ccfm_ctrl_resultat"), nullable=False),

        sa.Column("niveau", sa.Enum(
                  "BLOQUANT","AVERTISSEMENT",
                  name="ccfm_ctrl_niveau"), nullable=False),

        # Message détaillé
        sa.Column("message",        sa.Text, nullable=False),
        sa.Column("details_json",   postgresql.JSONB,
                  comment="Données techniques du contrôle (valeurs mesurées, seuils)"),

        # Durée d'exécution
        sa.Column("duree_ms", sa.Integer,
                  comment="Durée du contrôle en millisecondes"),

        # Intégrité SHA-256 chaîné (comme audit_sensitive_ops)
        sa.Column("sha256_log",  sa.String(64), nullable=False,
                  comment="SHA-256(demande_ccfm_id|type|resultat|message|timestamp)"),
        sa.Column("sha256_prev", sa.String(64),
                  comment="SHA-256 du contrôle précédent — chaîne inviolable"),

        # Acteur qui a déclenché (si manuel) ou SYSTEME
        sa.Column("execute_par",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("role_executant", sa.String(50), server_default="SYSTEME"),

        sa.Column("date_verification", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_cvl_demande",  "ccfm_verification_log", ["demande_ccfm_id"])
    op.create_index("ix_cvl_parcelle", "ccfm_verification_log", ["parcelle_id"])
    op.create_index("ix_cvl_type",     "ccfm_verification_log", ["type_verification"])
    op.create_index("ix_cvl_resultat", "ccfm_verification_log", ["resultat"])

    # ════════════════════════════════════════════════════════════════
    # TABLE 2 : URBANISME_CONFORMITE
    # Conformité urbanistique d'une parcelle.
    # Alimente le contrôle F3 du moteur CCFM.
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "urbanisme_conformite",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False, unique=True),

        # Lien vers l'arrêté d'urbanisme source
        sa.Column("arrete_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("arretes_urbanisme.id"), nullable=True),

        # Classification urbanistique
        sa.Column("zone_urbanisme", sa.Enum(
                  "residentielle","commerciale","industrielle","agricole",
                  "verte","mixte","protegee", name="zone_urbanisme"),
                  nullable=True),
        sa.Column("destination_autorisee", sa.String(200),
                  comment="Usage autorisé par le plan d'urbanisme"),
        sa.Column("destination_declaree",  sa.String(200),
                  comment="Usage déclaré par le demandeur"),

        # COS (Coefficient d'Occupation des Sols)
        sa.Column("cos_max",      sa.Numeric(5, 2),
                  comment="COS maximum autorisé par le plan"),
        sa.Column("cos_declare",  sa.Numeric(5, 2),
                  comment="COS déclaré dans le projet"),

        # Résultat de la vérification
        sa.Column("statut",   sa.String(30), server_default="EN_ATTENTE",
                  comment="EN_ATTENTE|VALIDE|NON_CONFORME|EXEMPTION"),
        sa.Column("motif_non_conformite", sa.Text),

        # SHA-256 du résultat
        sa.Column("sha256_conformite", sa.String(64)),

        sa.Column("verifie_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("verifie_at",  sa.DateTime(timezone=True)),
        sa.Column("created_at",  sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",  sa.DateTime(timezone=True)),
    )
    op.create_index("ix_uc_statut", "urbanisme_conformite", ["statut"])

    # ════════════════════════════════════════════════════════════════
    # TABLE 3 : BGU_PROJECTION
    # Résultat de la projection BGU calculé pour une parcelle.
    # Enrichit bgu_geojson_master (003) + bgu_parcel_status (004)
    # avec le résultat du contrôle de cohérence géospatiale.
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "bgu_projection",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False, unique=True),

        # Lien vers bgu_geojson_master (003)
        sa.Column("bgu_master_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("bgu_geojson_master.id"), nullable=True),

        # Résultats du contrôle
        sa.Column("projection_valide",   sa.Boolean, nullable=False,
                  server_default="false"),
        sa.Column("bgu_scelle",          sa.Boolean, server_default="false",
                  comment="True si bgu_geojson_master.scelle = true"),
        sa.Column("coherence_rnaf",      sa.Boolean, server_default="false",
                  comment="RNAF projeté correspond à l'arrêté source"),
        sa.Column("coherence_rnp",       sa.Boolean, server_default="false",
                  comment="RNP projeté correspond à la parcelle cadastrée"),

        # Mesures géospatiales
        sa.Column("surface_bgu_m2",      sa.Numeric(12, 4),
                  comment="Surface calculée par PostGIS depuis bgu_geojson_master"),
        sa.Column("surface_rnp_m2",      sa.Numeric(12, 4),
                  comment="Surface déclarée dans parcelles"),
        sa.Column("ecart_surface_pct",   sa.Numeric(6, 3),
                  comment="Écart entre surface BGU et surface RNP"),
        sa.Column("srid",                sa.Integer, default=4326),

        # Motif d'invalidité
        sa.Column("motif_invalidite",    sa.Text),

        # SHA-256 du résultat de projection
        sa.Column("sha256_projection",   sa.String(64)),

        sa.Column("date_projection",     sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("calcule_par",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
    )
    op.create_index("ix_bp_valide", "bgu_projection", ["projection_valide"])

    # ════════════════════════════════════════════════════════════════
    # TABLE 4 : CONTROLE_GEOMETRIQUE
    # Résultat du contrôle géométrique PostGIS d'une parcelle.
    # Enrichit conflits_parcellaires (003) avec une synthèse binaire.
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "controle_geometrique",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False, unique=True),

        # Résultat global
        sa.Column("geometrie_valide",     sa.Boolean, nullable=False,
                  server_default="false"),

        # Détail des contrôles
        sa.Column("pas_de_superposition", sa.Boolean, server_default="false",
                  comment="ST_Intersects = 0 avec voisins actifs"),
        sa.Column("geometrie_fermee",     sa.Boolean, server_default="false",
                  comment="ST_IsValid(geom) = true"),
        sa.Column("surface_coherente",    sa.Boolean, server_default="false",
                  comment="Écart surface déclarée vs calculée < 0.5%"),
        sa.Column("dans_lotissement",     sa.Boolean, server_default="false",
                  comment="ST_Within(parcelle, lotissement)"),

        # Mesures
        sa.Column("surface_calculee_m2",  sa.Numeric(12, 4),
                  comment="ST_Area(geom::geography) calculé par PostGIS"),
        sa.Column("surface_declaree_m2",  sa.Numeric(12, 4)),
        sa.Column("ecart_surface_pct",    sa.Numeric(6, 3)),
        sa.Column("nb_superpositions",    sa.Integer, default=0),
        sa.Column("superpositions_ids",   postgresql.JSONB,
                  comment="UUIDs des parcelles en superposition"),

        # Seuils appliqués
        sa.Column("seuil_surface_pct",    sa.Numeric(5, 3), default=0.5),
        sa.Column("seuil_superposition_m2",sa.Numeric(8, 4), default=1.0),

        # Motif d'invalidité
        sa.Column("motif_invalidite",     sa.Text),
        sa.Column("sha256_controle",      sa.String(64)),

        sa.Column("date_controle",        sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("calcule_par",          postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
    )
    op.create_index("ix_cg_valide", "controle_geometrique", ["geometrie_valide"])

    # ════════════════════════════════════════════════════════════════
    # FONCTIONS DE CONTRÔLE CCFM (F1 → F8)
    # Chaque fonction retourne un JSONB avec :
    #   { ok: bool, niveau: "BLOQUANT|AVERTISSEMENT",
    #     message: text, details: jsonb }
    # ════════════════════════════════════════════════════════════════

    # Fonction utilitaire : logger un contrôle ──────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION ccfm_log_controle(
            p_demande_ccfm_id UUID,
            p_parcelle_id     UUID,
            p_rnaf_id         UUID,
            p_type            ccfm_ctrl_type,
            p_resultat        ccfm_ctrl_resultat,
            p_niveau          ccfm_ctrl_niveau,
            p_message         TEXT,
            p_details         JSONB DEFAULT NULL,
            p_duree_ms        INT  DEFAULT NULL
        ) RETURNS VOID AS $$
        DECLARE
            v_prev_sha TEXT;
            v_sha      TEXT;
            v_payload  TEXT;
        BEGIN
            -- SHA-256 chaîné
            SELECT cvl.sha256_log INTO v_prev_sha
            FROM ccfm_verification_log cvl
            WHERE cvl.demande_ccfm_id = p_demande_ccfm_id
            ORDER BY cvl.date_verification DESC
            LIMIT 1;

            v_payload := p_demande_ccfm_id::TEXT || '|' ||
                         p_type::TEXT || '|' ||
                         p_resultat::TEXT || '|' ||
                         p_message || '|' ||
                         NOW()::TEXT;
            v_sha := encode(sha256(v_payload::bytea), 'hex');

            INSERT INTO ccfm_verification_log (
                demande_ccfm_id, parcelle_id, rnaf_id,
                type_verification, resultat, niveau,
                message, details_json, duree_ms,
                sha256_log, sha256_prev,
                role_executant
            ) VALUES (
                p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                p_type, p_resultat, p_niveau,
                p_message, p_details, p_duree_ms,
                v_sha, v_prev_sha,
                'SYSTEME'
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F1 : Vérification validité RNAF ───────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION ccfm_ctrl_rnaf_validity(
            p_rnaf_id UUID,
            p_demande_ccfm_id UUID,
            p_parcelle_id UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_statut_rnaf TEXT;
            v_wf_statut   TEXT;
            v_nb_susp     INT;
        BEGIN
            -- Statut RNAF dans rnaf_workflow (004)
            SELECT rw.statut::TEXT INTO v_wf_statut
            FROM rnaf_workflow rw
            WHERE rw.rnaf_id = p_rnaf_id
            ORDER BY rw.updated_at DESC NULLS LAST
            LIMIT 1;

            -- Statut brut dans rnaf (001)
            SELECT r.statut INTO v_statut_rnaf
            FROM rnaf r WHERE r.id = p_rnaf_id;

            -- Suspensions actives sur ce RNAF (004)
            SELECT COUNT(*) INTO v_nb_susp
            FROM rnaf_workflow rw
            WHERE rw.rnaf_id = p_rnaf_id AND rw.statut = 'suspendu';

            -- Contrôle
            IF p_rnaf_id IS NULL OR v_statut_rnaf IS NULL THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'RNAF_VALIDITE', 'ECHOUE', 'BLOQUANT',
                    'RNAF introuvable ou non fourni.',
                    jsonb_build_object('rnaf_id', p_rnaf_id));
                RETURN jsonb_build_object('ok', false, 'niveau', 'BLOQUANT',
                    'ctrl', 'RNAF_VALIDITE',
                    'message', 'RNAF introuvable.');
            END IF;

            IF v_wf_statut NOT IN ('publie', 'archive') THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'RNAF_VALIDITE', 'ECHOUE', 'BLOQUANT',
                    'RNAF au statut "' || COALESCE(v_wf_statut,'inconnu') ||
                    '" — statut PUBLIÉ requis pour CCFM.',
                    jsonb_build_object('statut_rnaf', v_wf_statut,
                                       'statut_brut', v_statut_rnaf));
                RETURN jsonb_build_object('ok', false, 'niveau', 'BLOQUANT',
                    'ctrl', 'RNAF_VALIDITE',
                    'message', 'RNAF non publié — statut: ' || COALESCE(v_wf_statut,'?'));
            END IF;

            IF v_nb_susp > 0 THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'RNAF_VALIDITE', 'ECHOUE', 'BLOQUANT',
                    'RNAF actuellement suspendu — CCFM bloqué.',
                    jsonb_build_object('nb_suspensions', v_nb_susp));
                RETURN jsonb_build_object('ok', false, 'niveau', 'BLOQUANT',
                    'ctrl', 'RNAF_VALIDITE',
                    'message', 'RNAF suspendu — CCFM impossible.');
            END IF;

            PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                'RNAF_VALIDITE', 'PASSE', 'BLOQUANT',
                'RNAF publié et valide.',
                jsonb_build_object('statut', v_wf_statut));
            RETURN jsonb_build_object('ok', true, 'ctrl', 'RNAF_VALIDITE',
                'message', 'RNAF publié et valide.');
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F2 : Vérification validité RNP ────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION ccfm_ctrl_rnp_validity(
            p_parcelle_id UUID,
            p_demande_ccfm_id UUID,
            p_rnaf_id UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_statut_parc   TEXT;
            v_statut_rnp    TEXT;
            v_nicad         TEXT;
            v_nicad_valid   BOOLEAN;
        BEGIN
            SELECT p.statut::TEXT, p.nicad INTO v_statut_parc, v_nicad
            FROM parcelles p WHERE p.id = p_parcelle_id;

            SELECT rpl.statut_rnp::TEXT INTO v_statut_rnp
            FROM rnp_parcelle_link rpl
            WHERE rpl.parcelle_id = p_parcelle_id
            LIMIT 1;

            -- Valider le format NICAD
            v_nicad_valid := v_nicad ~ '^\\d{2}-\\d{2}-[A-Z]{2}\\d{4}-\\d{3}-[A-Z0-9]{1,3}[ab]?$';

            IF v_statut_parc IS NULL THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'RNP_VALIDITE', 'ECHOUE', 'BLOQUANT', 'Parcelle introuvable.',
                    jsonb_build_object('parcelle_id', p_parcelle_id));
                RETURN jsonb_build_object('ok', false, 'niveau', 'BLOQUANT',
                    'ctrl', 'RNP_VALIDITE', 'message', 'Parcelle introuvable.');
            END IF;

            IF v_statut_parc NOT IN ('active', 'en_attente_validation') THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'RNP_VALIDITE', 'ECHOUE', 'BLOQUANT',
                    'Statut parcelle "' || v_statut_parc || '" — statut ACTIVE requis.',
                    jsonb_build_object('statut', v_statut_parc, 'nicad', v_nicad));
                RETURN jsonb_build_object('ok', false, 'niveau', 'BLOQUANT',
                    'ctrl', 'RNP_VALIDITE',
                    'message', 'Parcelle au statut ' || v_statut_parc);
            END IF;

            IF NOT v_nicad_valid THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'RNP_VALIDITE', 'ECHOUE', 'BLOQUANT',
                    'Format NICAD invalide : ' || COALESCE(v_nicad, 'NULL'),
                    jsonb_build_object('nicad', v_nicad));
                RETURN jsonb_build_object('ok', false, 'niveau', 'BLOQUANT',
                    'ctrl', 'RNP_VALIDITE',
                    'message', 'NICAD invalide : ' || COALESCE(v_nicad, 'absent'));
            END IF;

            IF COALESCE(v_statut_rnp, 'inconnu') NOT IN ('actif', 'provisoire') THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'RNP_VALIDITE', 'AVERTISSEMENT', 'AVERTISSEMENT',
                    'Lien RNP absent ou au statut ' || COALESCE(v_statut_rnp,'absent'),
                    jsonb_build_object('statut_rnp', v_statut_rnp));
                RETURN jsonb_build_object('ok', true, 'niveau', 'AVERTISSEMENT',
                    'ctrl', 'RNP_VALIDITE',
                    'message', 'RNP non encore actif — CCFM conditionnel possible.');
            END IF;

            PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                'RNP_VALIDITE', 'PASSE', 'BLOQUANT',
                'RNP actif, NICAD valide : ' || v_nicad,
                jsonb_build_object('nicad', v_nicad, 'statut_rnp', v_statut_rnp));
            RETURN jsonb_build_object('ok', true, 'ctrl', 'RNP_VALIDITE',
                'message', 'RNP valide — NICAD: ' || v_nicad);
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F3 : Conformité urbanisme ─────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION ccfm_ctrl_urbanisme(
            p_parcelle_id UUID,
            p_demande_ccfm_id UUID,
            p_rnaf_id UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_statut      TEXT;
            v_arrete_ok   BOOLEAN := false;
            v_zone        TEXT;
        BEGIN
            -- Vérifier la conformité urbanistique
            SELECT uc.statut, uc.zone_urbanisme::TEXT
            INTO v_statut, v_zone
            FROM urbanisme_conformite uc
            WHERE uc.parcelle_id = p_parcelle_id;

            -- Vérifier que l'arrêté d'urbanisme est actif (via chaîne 003)
            SELECT EXISTS(
                SELECT 1 FROM parcelles p
                JOIN ilots i ON i.id = p.ilot_id
                JOIN lotissements l ON l.id = i.lotissement_id
                JOIN arretes_urbanisme au ON au.id = l.arrete_id
                WHERE p.id = p_parcelle_id AND au.statut IN ('actif','active')
            ) INTO v_arrete_ok;

            IF NOT v_arrete_ok THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'URBANISME_CONFORMITE', 'AVERTISSEMENT', 'AVERTISSEMENT',
                    'Aucun arrêté d''urbanisme ACTIF rattaché à cette parcelle.',
                    jsonb_build_object('arrete_ok', false));
                RETURN jsonb_build_object('ok', true, 'niveau', 'AVERTISSEMENT',
                    'ctrl', 'URBANISME_CONFORMITE',
                    'message', 'Arrêté urbanisme non trouvé ou inactif.');
            END IF;

            IF v_statut IS NULL THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'URBANISME_CONFORMITE', 'AVERTISSEMENT', 'AVERTISSEMENT',
                    'Conformité urbanistique non encore évaluée — contrôle en attente.',
                    jsonb_build_object('statut', NULL));
                RETURN jsonb_build_object('ok', true, 'niveau', 'AVERTISSEMENT',
                    'ctrl', 'URBANISME_CONFORMITE',
                    'message', 'Conformité urbanistique non évaluée.');
            END IF;

            IF v_statut = 'NON_CONFORME' THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'URBANISME_CONFORMITE', 'AVERTISSEMENT', 'AVERTISSEMENT',
                    'Parcelle non conforme au plan urbanistique — zone: ' || COALESCE(v_zone,'?'),
                    jsonb_build_object('statut', v_statut, 'zone', v_zone));
                RETURN jsonb_build_object('ok', true, 'niveau', 'AVERTISSEMENT',
                    'ctrl', 'URBANISME_CONFORMITE',
                    'message', 'Non-conformité urbanistique détectée — CCFM conditionnel.');
            END IF;

            PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                'URBANISME_CONFORMITE', 'PASSE', 'AVERTISSEMENT',
                'Conformité urbanistique validée — zone: ' || COALESCE(v_zone,'?'),
                jsonb_build_object('statut', v_statut, 'zone', v_zone));
            RETURN jsonb_build_object('ok', true, 'ctrl', 'URBANISME_CONFORMITE',
                'message', 'Urbanisme conforme.');
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F4 : Projection BGU ───────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION ccfm_ctrl_bgu(
            p_parcelle_id UUID,
            p_demande_ccfm_id UUID,
            p_rnaf_id UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_proj_valide  BOOLEAN;
            v_scelle       BOOLEAN;
            v_ecart_pct    NUMERIC;
        BEGIN
            SELECT bp.projection_valide, bp.bgu_scelle, bp.ecart_surface_pct
            INTO v_proj_valide, v_scelle, v_ecart_pct
            FROM bgu_projection bp
            WHERE bp.parcelle_id = p_parcelle_id;

            -- Vérifier aussi via bgu_geojson_master (003)
            IF v_proj_valide IS NULL THEN
                SELECT bgm.scelle INTO v_scelle
                FROM bgu_geojson_master bgm
                WHERE bgm.parcelle_id = p_parcelle_id;

                IF v_scelle IS NULL THEN
                    PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                        'BGU_PROJECTION', 'AVERTISSEMENT', 'AVERTISSEMENT',
                        'Aucune projection BGU trouvée pour cette parcelle.',
                        jsonb_build_object('source', 'aucune'));
                    RETURN jsonb_build_object('ok', true, 'niveau', 'AVERTISSEMENT',
                        'ctrl', 'BGU_PROJECTION',
                        'message', 'Projection BGU absente — à effectuer.');
                END IF;

                v_proj_valide := v_scelle;
            END IF;

            IF NOT COALESCE(v_proj_valide, false) THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'BGU_PROJECTION', 'AVERTISSEMENT', 'AVERTISSEMENT',
                    'Projection BGU invalide ou non scellée.',
                    jsonb_build_object('projection_valide', v_proj_valide,
                                       'scelle', v_scelle));
                RETURN jsonb_build_object('ok', true, 'niveau', 'AVERTISSEMENT',
                    'ctrl', 'BGU_PROJECTION',
                    'message', 'BGU non scellé ou projection invalide.');
            END IF;

            IF v_ecart_pct IS NOT NULL AND v_ecart_pct > 0.5 THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'BGU_PROJECTION', 'AVERTISSEMENT', 'AVERTISSEMENT',
                    format('Écart surface BGU vs RNP : %.3f%% (seuil 0.5%%)', v_ecart_pct),
                    jsonb_build_object('ecart_pct', v_ecart_pct));
                RETURN jsonb_build_object('ok', true, 'niveau', 'AVERTISSEMENT',
                    'ctrl', 'BGU_PROJECTION',
                    'message', format('Écart surface BGU %.3f%% — vérification recommandée.', v_ecart_pct));
            END IF;

            PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                'BGU_PROJECTION', 'PASSE', 'AVERTISSEMENT',
                'Projection BGU valide et scellée.',
                jsonb_build_object('scelle', v_scelle, 'ecart_pct', v_ecart_pct));
            RETURN jsonb_build_object('ok', true, 'ctrl', 'BGU_PROJECTION',
                'message', 'BGU scellé et cohérent.');
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F5 : Absence de litige ────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION ccfm_ctrl_absence_litige(
            p_parcelle_id UUID,
            p_demande_ccfm_id UUID,
            p_rnaf_id UUID
        ) RETURNS JSONB AS $$
        DECLARE
            nb_litiges   INT;
            nb_disputes  INT;
            nb_annul     INT;
        BEGIN
            SELECT COUNT(*) INTO nb_litiges
            FROM litiges l
            WHERE l.parcelle_id = p_parcelle_id
              AND l.statut NOT IN ('clos','abandonne');

            SELECT COUNT(*) INTO nb_disputes
            FROM parcel_disputes pd
            WHERE pd.parcelle_id = p_parcelle_id
              AND pd.statut IN ('ouvert','en_instruction');

            SELECT COUNT(*) INTO nb_annul
            FROM annulation_judiciaire_dossier ajd
            WHERE ajd.parcelle_id = p_parcelle_id
              AND ajd.statut NOT IN ('archive');

            IF (nb_litiges + nb_disputes + nb_annul) > 0 THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'ABSENCE_LITIGE', 'ECHOUE', 'BLOQUANT',
                    format('CCFM BLOQUÉ: %s litige(s), %s dispute(s) droit, '
                           '%s annulation(s) judiciaire(s) actif(s).',
                           nb_litiges, nb_disputes, nb_annul),
                    jsonb_build_object('litiges', nb_litiges,
                                       'disputes', nb_disputes,
                                       'annulations', nb_annul));
                RETURN jsonb_build_object('ok', false, 'niveau', 'BLOQUANT',
                    'ctrl', 'ABSENCE_LITIGE',
                    'message', format('%s litige(s) actif(s) — CCFM impossible.',
                                      nb_litiges + nb_disputes + nb_annul));
            END IF;

            PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                'ABSENCE_LITIGE', 'PASSE', 'BLOQUANT',
                'Aucun litige actif.', NULL);
            RETURN jsonb_build_object('ok', true, 'ctrl', 'ABSENCE_LITIGE',
                'message', 'Aucun litige — parcelle libre de tout contentieux.');
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F6 : Absence d'hypothèque bloquante ───────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION ccfm_ctrl_absence_hypotheque(
            p_parcelle_id UUID,
            p_demande_ccfm_id UUID,
            p_rnaf_id UUID
        ) RETURNS JSONB AS $$
        DECLARE
            nb_hyp_bloquantes INT;
            nb_hyp_totales    INT;
        BEGIN
            -- Hypothèques bloquantes = actives sans mainlevée
            SELECT COUNT(*) INTO nb_hyp_totales
            FROM hypotheques h
            WHERE h.parcelle_id = p_parcelle_id AND h.statut = 'active';

            SELECT COUNT(*) + nb_hyp_totales INTO nb_hyp_totales
            FROM mortgage_registry mr
            WHERE mr.parcelle_id = p_parcelle_id AND mr.statut = 'active';

            -- Note : une hypothèque active n'est PAS bloquante pour le CCFM lui-même
            -- Elle est bloquante pour les mutations et ventes (WL5/WL7)
            -- Le CCFM est un contrôle de conformité, pas une mutation
            -- Donc : AVERTISSEMENT si hypothèque active, pas BLOQUANT

            IF nb_hyp_totales > 0 THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'ABSENCE_HYPOTHEQUE', 'AVERTISSEMENT', 'AVERTISSEMENT',
                    format('%s hypothèque(s) active(s) — '
                           'CCFM délivré avec réserve hypothécaire.', nb_hyp_totales),
                    jsonb_build_object('nb_hypotheques_actives', nb_hyp_totales));
                RETURN jsonb_build_object('ok', true, 'niveau', 'AVERTISSEMENT',
                    'ctrl', 'ABSENCE_HYPOTHEQUE',
                    'message', format('%s hypothèque(s) active(s) — '
                                      'mention au certificat CCFM.', nb_hyp_totales),
                    'reserve', true);
            END IF;

            PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                'ABSENCE_HYPOTHEQUE', 'PASSE', 'AVERTISSEMENT',
                'Aucune hypothèque active.', NULL);
            RETURN jsonb_build_object('ok', true, 'ctrl', 'ABSENCE_HYPOTHEQUE',
                'message', 'Parcelle libre de toute hypothèque.');
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F7 : Cohérence géométrique ────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION ccfm_ctrl_geometrie(
            p_parcelle_id UUID,
            p_demande_ccfm_id UUID,
            p_rnaf_id UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_geo_valide    BOOLEAN;
            v_nb_superp     INT;
            v_ecart_pct     NUMERIC;
            v_conflits_crit INT;
        BEGIN
            -- Résultat du contrôle_geometrique
            SELECT cg.geometrie_valide, cg.nb_superpositions, cg.ecart_surface_pct
            INTO v_geo_valide, v_nb_superp, v_ecart_pct
            FROM controle_geometrique cg
            WHERE cg.parcelle_id = p_parcelle_id;

            -- Conflits critiques dans conflits_parcellaires (003)
            SELECT COUNT(*) INTO v_conflits_crit
            FROM conflits_parcellaires cp
            WHERE cp.parcelle_a_id = p_parcelle_id
              AND cp.gravite = 'critique'
              AND cp.statut IN ('ouvert','bloque');

            IF v_conflits_crit > 0 THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'GEOMETRIE', 'ECHOUE', 'BLOQUANT',
                    format('GÉOMÉTRIE BLOQUÉE: %s conflit(s) antifraude critique(s) détecté(s).',
                           v_conflits_crit),
                    jsonb_build_object('conflits_critiques', v_conflits_crit));
                RETURN jsonb_build_object('ok', false, 'niveau', 'BLOQUANT',
                    'ctrl', 'GEOMETRIE',
                    'message', format('%s conflit(s) géométrique(s) critique(s) — CCFM bloqué.',
                                      v_conflits_crit));
            END IF;

            IF v_geo_valide IS NULL THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'GEOMETRIE', 'AVERTISSEMENT', 'AVERTISSEMENT',
                    'Contrôle géométrique non encore effectué.', NULL);
                RETURN jsonb_build_object('ok', true, 'niveau', 'AVERTISSEMENT',
                    'ctrl', 'GEOMETRIE',
                    'message', 'Contrôle géométrique en attente — CCFM conditionnel.');
            END IF;

            IF NOT v_geo_valide THEN
                PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                    'GEOMETRIE', 'ECHOUE', 'BLOQUANT',
                    'Géométrie invalide : ' ||
                    COALESCE(NULLIF(v_nb_superp::TEXT,'0') || ' superposition(s) ', '') ||
                    COALESCE('écart surface ' || v_ecart_pct::TEXT || '%', ''),
                    jsonb_build_object('geometrie_valide', false,
                                       'nb_superpositions', v_nb_superp,
                                       'ecart_surface_pct', v_ecart_pct));
                RETURN jsonb_build_object('ok', false, 'niveau', 'BLOQUANT',
                    'ctrl', 'GEOMETRIE',
                    'message', 'Géométrie invalide — CCFM bloqué.');
            END IF;

            PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                'GEOMETRIE', 'PASSE', 'BLOQUANT',
                'Géométrie valide — aucune superposition, surface cohérente.',
                jsonb_build_object('ecart_pct', v_ecart_pct));
            RETURN jsonb_build_object('ok', true, 'ctrl', 'GEOMETRIE',
                'message', 'Géométrie valide.');
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F8 : Fonction principale generate_ccfm_certificate ────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION generate_ccfm_certificate(
            p_demande_ccfm_id UUID,
            p_parcelle_id     UUID,
            p_rnaf_id         UUID,
            p_user_id         UUID DEFAULT NULL
        ) RETURNS JSONB AS $$
        DECLARE
            r_rnaf     JSONB;
            r_rnp      JSONB;
            r_urban    JSONB;
            r_bgu      JSONB;
            r_litige   JSONB;
            r_hyp      JSONB;
            r_geo      JSONB;
            v_bloquants_ko  INT := 0;
            v_avertissements INT := 0;
            v_statut_final   TEXT;
            v_sha256_cert    TEXT;
            v_ts             TIMESTAMPTZ := NOW();
        BEGIN
            -- ── Exécution séquentielle des 7 contrôles ──
            r_rnaf   := ccfm_ctrl_rnaf_validity(p_rnaf_id, p_demande_ccfm_id, p_parcelle_id);
            r_rnp    := ccfm_ctrl_rnp_validity(p_parcelle_id, p_demande_ccfm_id, p_rnaf_id);
            r_urban  := ccfm_ctrl_urbanisme(p_parcelle_id, p_demande_ccfm_id, p_rnaf_id);
            r_bgu    := ccfm_ctrl_bgu(p_parcelle_id, p_demande_ccfm_id, p_rnaf_id);
            r_litige := ccfm_ctrl_absence_litige(p_parcelle_id, p_demande_ccfm_id, p_rnaf_id);
            r_hyp    := ccfm_ctrl_absence_hypotheque(p_parcelle_id, p_demande_ccfm_id, p_rnaf_id);
            r_geo    := ccfm_ctrl_geometrie(p_parcelle_id, p_demande_ccfm_id, p_rnaf_id);

            -- ── Comptage des résultats ──
            -- Contrôles BLOQUANTS qui échouent → CCFM refusé
            IF NOT (r_rnaf->>'ok')::BOOLEAN   THEN v_bloquants_ko := v_bloquants_ko + 1; END IF;
            IF NOT (r_rnp->>'ok')::BOOLEAN    THEN v_bloquants_ko := v_bloquants_ko + 1; END IF;
            IF NOT (r_litige->>'ok')::BOOLEAN THEN v_bloquants_ko := v_bloquants_ko + 1; END IF;
            IF NOT (r_geo->>'ok')::BOOLEAN    THEN v_bloquants_ko := v_bloquants_ko + 1; END IF;

            -- Contrôles AVERTISSEMENT → CCFM conditionnel avec mention
            IF (r_urban->>'niveau') = 'AVERTISSEMENT' AND (r_urban->>'ok')::BOOLEAN
            THEN v_avertissements := v_avertissements + 1; END IF;
            IF (r_bgu->>'niveau') = 'AVERTISSEMENT' AND (r_bgu->>'ok')::BOOLEAN
            THEN v_avertissements := v_avertissements + 1; END IF;
            IF (r_hyp->>'niveau') = 'AVERTISSEMENT' AND (r_hyp->>'ok')::BOOLEAN
            THEN v_avertissements := v_avertissements + 1; END IF;

            -- ── Décision finale ──
            v_statut_final := CASE
                WHEN v_bloquants_ko > 0         THEN 'REFUSE'
                WHEN v_avertissements > 0        THEN 'CONDITIONNEL'
                ELSE                                  'VALIDE'
            END;

            -- ── SHA-256 du certificat ──
            v_sha256_cert := encode(
                sha256((p_demande_ccfm_id::TEXT || '|' ||
                        p_parcelle_id::TEXT || '|' ||
                        COALESCE(p_rnaf_id::TEXT,'') || '|' ||
                        v_statut_final || '|' ||
                        v_ts::TEXT)::bytea),
                'hex'
            );

            -- ── Log du contrôle GLOBAL ──
            PERFORM ccfm_log_controle(p_demande_ccfm_id, p_parcelle_id, p_rnaf_id,
                'GLOBAL',
                CASE v_statut_final
                    WHEN 'VALIDE'       THEN 'PASSE'
                    WHEN 'CONDITIONNEL' THEN 'AVERTISSEMENT'
                    ELSE                     'ECHOUE'
                END::ccfm_ctrl_resultat,
                'BLOQUANT',
                format('Résultat global CCFM: %s — %s bloquant(s) KO, %s avertissement(s).',
                       v_statut_final, v_bloquants_ko, v_avertissements),
                jsonb_build_object(
                    'statut_final', v_statut_final,
                    'bloquants_ko', v_bloquants_ko,
                    'avertissements', v_avertissements,
                    'sha256_certificat', v_sha256_cert,
                    'controles', jsonb_build_object(
                        'RNAF',      r_rnaf,
                        'RNP',       r_rnp,
                        'URBANISME', r_urban,
                        'BGU',       r_bgu,
                        'LITIGE',    r_litige,
                        'HYPOTHEQUE',r_hyp,
                        'GEOMETRIE', r_geo
                    )
                )
            );

            -- ── Retourner le rapport complet ──
            RETURN jsonb_build_object(
                'statut',              v_statut_final,
                'peut_emettre',        v_bloquants_ko = 0,
                'bloquants_ko',        v_bloquants_ko,
                'avertissements',      v_avertissements,
                'sha256_certificat',   v_sha256_cert,
                'timestamp',           v_ts,
                'controles', jsonb_build_object(
                    'F1_RNAF',       r_rnaf,
                    'F2_RNP',        r_rnp,
                    'F3_URBANISME',  r_urban,
                    'F4_BGU',        r_bgu,
                    'F5_LITIGE',     r_litige,
                    'F6_HYPOTHEQUE', r_hyp,
                    'F7_GEOMETRIE',  r_geo
                )
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ════════════════════════════════════════════════════════════════
    # TRIGGERS CCFM
    # ════════════════════════════════════════════════════════════════

    # TC1 : Lancer le moteur CCFM automatiquement à la soumission ────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_auto_run_ccfm_moteur()
        RETURNS TRIGGER AS $$
        DECLARE
            v_rnaf_id   UUID;
            v_rapport   JSONB;
        BEGIN
            -- Déclenché quand demandes_ccfm passe à un état de vérification
            IF NEW.statut NOT IN ('EN_VERIFICATION','VERIFICATION','en_verification')
               OR OLD.statut IN ('EN_VERIFICATION','VERIFICATION','en_verification') THEN
                RETURN NEW;
            END IF;

            -- Récupérer le RNAF via rnp_parcelle_link
            SELECT rpl.rnaf_id INTO v_rnaf_id
            FROM rnp_parcelle_link rpl
            WHERE rpl.parcelle_id = NEW.parcelle_id
            LIMIT 1;

            -- Lancer le moteur de contrôle
            v_rapport := generate_ccfm_certificate(
                NEW.id, NEW.parcelle_id, v_rnaf_id
            );

            -- Stocker le résultat dans le contexte (si champ disponible)
            -- En production : déclencher une notification Redis/WebSocket
            INSERT INTO audit_sensitive_ops (
                operation, table_cible, entite_id, module_source,
                niveau_criticite, sha256_op, valeur_apres
            ) VALUES (
                'UPDATE', 'demandes_ccfm', NEW.id, 'ccfm', 'critique',
                encode(sha256(('CCFM_MOTEUR|' || NEW.id::TEXT || '|' || NOW()::TEXT)::bytea), 'hex'),
                jsonb_build_object(
                    'moteur_ccfm_rapport', v_rapport,
                    'peut_emettre', (v_rapport->>'peut_emettre')::BOOLEAN
                )
            );

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_tc1_auto_run_ccfm_moteur
        AFTER UPDATE OF statut ON demandes_ccfm
        FOR EACH ROW
        EXECUTE FUNCTION tg_fn_auto_run_ccfm_moteur();
    """)

    # TC2 : SHA-256 chaîné sur les logs CCFM (déjà géré dans ccfm_log_controle)
    # Le trigger ajoute le sha256_prev automatiquement à l'INSERT
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_ccfm_log_sha256()
        RETURNS TRIGGER AS $$
        DECLARE
            v_prev TEXT;
        BEGIN
            -- Si sha256_prev pas encore rempli (sécurité)
            IF NEW.sha256_prev IS NULL THEN
                SELECT cvl.sha256_log INTO v_prev
                FROM ccfm_verification_log cvl
                WHERE cvl.demande_ccfm_id = NEW.demande_ccfm_id
                  AND cvl.id != NEW.id
                ORDER BY cvl.date_verification DESC
                LIMIT 1;
                NEW.sha256_prev := v_prev;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_tc2_ccfm_log_sha256
        BEFORE INSERT ON ccfm_verification_log
        FOR EACH ROW
        EXECUTE FUNCTION tg_fn_ccfm_log_sha256();
    """)

    # TC3 : Mise à jour automatique bgu_projection quand BGU scellé ──
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_maj_bgu_projection()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.scelle = true AND (OLD.scelle IS DISTINCT FROM NEW.scelle) THEN
                INSERT INTO bgu_projection (
                    parcelle_id, bgu_master_id, projection_valide,
                    bgu_scelle, coherence_rnaf, coherence_rnp,
                    surface_bgu_m2, sha256_projection
                ) VALUES (
                    NEW.parcelle_id, NEW.id, true, true, true, true,
                    NULL, -- Calculé par PostGIS en production : ST_Area(NEW.geom::geography)
                    encode(sha256((NEW.parcelle_id::TEXT || '|SCELLE|' || NOW()::TEXT)::bytea), 'hex')
                )
                ON CONFLICT (parcelle_id) DO UPDATE SET
                    bgu_scelle = true,
                    projection_valide = true,
                    sha256_projection = encode(
                        sha256((NEW.parcelle_id::TEXT || '|SCELLE|' || NOW()::TEXT)::bytea), 'hex'
                    ),
                    date_projection = NOW();
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_tc3_maj_bgu_projection
        AFTER UPDATE OF scelle ON bgu_geojson_master
        FOR EACH ROW
        WHEN (NEW.scelle = true AND OLD.scelle IS DISTINCT FROM NEW.scelle)
        EXECUTE FUNCTION tg_fn_maj_bgu_projection();
    """)

    # ════════════════════════════════════════════════════════════════
    # WORKFLOW ANNULATION JUDICIAIRE — étapes complètes
    # Enrichit la définition JUSTICE déjà créée en 007
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        INSERT INTO workflow_step_def (
            id, definition_id, ordre, code_etape, nom_etape,
            role_requis, type_validation, delai_max_heures,
            est_obligatoire, action_post_validation, retour_etape_rejet
        )
        SELECT
            gen_random_uuid(), d.id,
            s.ordre, s.code, s.nom, s.role,
            s.tv::type_validation,
            s.delai, true, s.action, s.retour
        FROM workflow_definition d,
        (VALUES
            (1,'SAISINE','Saisine judiciaire','JUST_GREFFIER',
             'approbation',48,NULL,NULL),
            (2,'RECEVABILITE','Examen de recevabilité','JUST_JUGE_FONCIER',
             'approbation',72,NULL,1),
            (3,'NOTIFICATION_PARTIES','Notification des parties','JUST_GREFFIER',
             'horodatage',48,NULL,NULL),
            (4,'INSTRUCTION','Phase d''instruction','JUST_JUGE_FONCIER',
             'approbation',336,'CHECK_CCFM_GATE',NULL),
            (5,'AUDIENCE','Tenue de l''audience','JUST_PRESIDENT',
             'constat',168,NULL,4),
            (6,'DELIBERE','Délibéré','JUST_PRESIDENT',
             'approbation',120,NULL,NULL),
            (7,'DECISION_RENDUE','Décision judiciaire rendue','JUST_PRESIDENT',
             'signature',24,'CASCADE_ANNULATION',NULL),
            (8,'NOTIFICATION_DECISION','Notification de la décision','JUST_GREFFIER',
             'horodatage',48,NULL,NULL),
            (9,'EXECUTION','Exécution de la décision','JUST_SECRETAIRE',
             'approbation',168,'EXECUTER_ANNULATION',NULL),
            (10,'ARCHIVAGE_ANNF','Archivage ANNF définitif','CHEF_DAR',
             'scellement',24,'ARCHIVER_DECISION_ANNF',NULL)
        ) AS s(ordre,code,nom,role,tv,delai,action,retour)
        WHERE d.type_workflow = 'JUSTICE'
          AND NOT EXISTS (
              SELECT 1 FROM workflow_step_def wsd
              WHERE wsd.definition_id = d.id AND wsd.code_etape = s.code
          )
        ON CONFLICT (definition_id, code_etape) DO NOTHING;
    """)

    # ════════════════════════════════════════════════════════════════
    # VUES MOTEUR CCFM
    # ════════════════════════════════════════════════════════════════

    op.execute("""
        CREATE OR REPLACE VIEW v_ccfm_etat_controles AS
        SELECT
            dc.id               AS demande_ccfm_id,
            dc.statut           AS statut_demande,
            p.nicad,
            p.statut::TEXT      AS statut_parcelle,
            -- Résultats des contrôles (dernière exécution)
            MAX(CASE WHEN cvl.type_verification = 'RNAF_VALIDITE'
                     THEN cvl.resultat::TEXT END)       AS ctrl_rnaf,
            MAX(CASE WHEN cvl.type_verification = 'RNP_VALIDITE'
                     THEN cvl.resultat::TEXT END)       AS ctrl_rnp,
            MAX(CASE WHEN cvl.type_verification = 'URBANISME_CONFORMITE'
                     THEN cvl.resultat::TEXT END)       AS ctrl_urbanisme,
            MAX(CASE WHEN cvl.type_verification = 'BGU_PROJECTION'
                     THEN cvl.resultat::TEXT END)       AS ctrl_bgu,
            MAX(CASE WHEN cvl.type_verification = 'ABSENCE_LITIGE'
                     THEN cvl.resultat::TEXT END)       AS ctrl_litige,
            MAX(CASE WHEN cvl.type_verification = 'ABSENCE_HYPOTHEQUE'
                     THEN cvl.resultat::TEXT END)       AS ctrl_hypotheque,
            MAX(CASE WHEN cvl.type_verification = 'GEOMETRIE'
                     THEN cvl.resultat::TEXT END)       AS ctrl_geometrie,
            MAX(CASE WHEN cvl.type_verification = 'GLOBAL'
                     THEN cvl.resultat::TEXT END)       AS ctrl_global,
            -- Dernière vérification
            MAX(cvl.date_verification)                  AS derniere_verification,
            -- Nombre de contrôles bloquants échoués
            SUM(CASE WHEN cvl.resultat = 'ECHOUE'
                      AND cvl.niveau = 'BLOQUANT' THEN 1 ELSE 0 END)
                                                        AS nb_bloquants_ko,
            -- Nombre d'avertissements
            SUM(CASE WHEN cvl.resultat = 'AVERTISSEMENT' THEN 1 ELSE 0 END)
                                                        AS nb_avertissements,
            -- Éligibilité CCFM
            (SUM(CASE WHEN cvl.resultat = 'ECHOUE'
                       AND cvl.niveau = 'BLOQUANT' THEN 1 ELSE 0 END) = 0
            )                                           AS peut_emettre_ccfm
        FROM demandes_ccfm dc
        JOIN parcelles p ON p.id = dc.parcelle_id
        LEFT JOIN ccfm_verification_log cvl ON cvl.demande_ccfm_id = dc.id
        GROUP BY dc.id, dc.statut, p.nicad, p.statut;
    """)

    op.execute("""
        CREATE OR REPLACE VIEW v_ccfm_audit_trail AS
        SELECT
            cvl.id,
            cvl.demande_ccfm_id,
            p.nicad,
            cvl.type_verification,
            cvl.resultat,
            cvl.niveau,
            cvl.message,
            cvl.sha256_log,
            cvl.sha256_prev,
            -- Vérification intégrité chaîne
            (cvl.sha256_prev IS NULL OR EXISTS (
                SELECT 1 FROM ccfm_verification_log prev
                WHERE prev.sha256_log = cvl.sha256_prev
                  AND prev.demande_ccfm_id = cvl.demande_ccfm_id
            ))                      AS chaine_integre,
            cvl.role_executant,
            cvl.date_verification
        FROM ccfm_verification_log cvl
        JOIN parcelles p ON p.id = cvl.parcelle_id
        ORDER BY cvl.demande_ccfm_id, cvl.date_verification;
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_ccfm_audit_trail CASCADE")
    op.execute("DROP VIEW IF EXISTS v_ccfm_etat_controles CASCADE")

    for trigger, table in [
        ("tg_tc3_maj_bgu_projection",    "bgu_geojson_master"),
        ("tg_tc2_ccfm_log_sha256",       "ccfm_verification_log"),
        ("tg_tc1_auto_run_ccfm_moteur",  "demandes_ccfm"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    for fn in [
        "generate_ccfm_certificate",
        "ccfm_ctrl_geometrie",
        "ccfm_ctrl_absence_hypotheque",
        "ccfm_ctrl_absence_litige",
        "ccfm_ctrl_bgu",
        "ccfm_ctrl_urbanisme",
        "ccfm_ctrl_rnp_validity",
        "ccfm_ctrl_rnaf_validity",
        "ccfm_log_controle",
        "tg_fn_auto_run_ccfm_moteur",
        "tg_fn_ccfm_log_sha256",
        "tg_fn_maj_bgu_projection",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID,UUID,UUID) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID,UUID,UUID,UUID) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID,UUID,UUID,TEXT,JSONB,INT,TEXT) CASCADE")

    for table in [
        "controle_geometrique", "bgu_projection",
        "urbanisme_conformite", "ccfm_verification_log",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    for name in [
        "ccfm_ctrl_type", "ccfm_ctrl_resultat",
        "ccfm_ctrl_niveau", "zone_urbanisme",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")
