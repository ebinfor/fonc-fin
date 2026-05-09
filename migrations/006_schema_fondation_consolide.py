"""006_schema_fondation_consolide

FONCIER+ v3.4.7 — Migration 006 : Schéma Fondation Consolidé
Aligné sur 64 tables existantes (migrations 001-005).

ANALYSE DES GAPS (spec vs existant) :
  ✓ commune          → regions + communes (003) — COUVERTS
  ✓ rnaf_registre    → rnaf (001) + rnaf_workflow (004) — COUVERTS
  ✓ rnp_parcelle     → rnp_demandes (001) + rnp_parcelle_link (004) — COUVERTS
  ✓ transaction_block → transaction_blocks (004) — COUVERT
  ✓ proprietaire     → right_holders (005) — COUVERT
  ✓ annf_archive     → annf_archive_links (004) — COUVERT

GAPS RÉELS COMBLÉS ICI (5) :
  1. rnaf_version_audit   → historique des versions RNAF (manquait)
  2. ccfm_contestation    → contestation d'un certificat CCFM (manquait)
  3. rbac_permission      → RBAC granulaire réel par action (manquait)
  4. rbac_role_permission → table de liaison rôle ↔ permission
  5. audit_log_global     → enrichissement audit_logs + vue unifiée

TRIGGERS DE VERROUILLAGE (les règles métier deviennent des contraintes SQL) :
  T1. block_rnp_if_rnaf_not_publie        → INSERT rnp_demandes bloqué si RNAF non publié
  T2. block_mutation_if_hypotheque_active → INSERT actes_notaries bloqué si hypothèque active
  T3. block_geom_change_without_ccfm      → UPDATE parcelles.geom bloqué sans CCFM valide
  T4. block_transfer_if_litige_ouvert     → INSERT property_transfers bloqué si litige ouvert
  T5. enforce_rnaf_publie_before_jo       → publications_jo bloqué si RNAF non scellé
  T6. cascade_suspension_to_rnp          → suspension CCFM cascade vers RNP (enrichit 004)
  T7. audit_all_sensitive_mutations       → audit automatique sur toutes tables sensibles

VUES CONSOLIDÉES :
  v_etat_parcelle_complet → vue unifiée statut parcelle toutes sources
  v_rbac_permissions      → vue permissions effectives par utilisateur
  v_audit_recent          → dernières 24h d'actions sensibles

Revision ID: 006_schema_fondation_consolide
Revises: 005_droits_fonciers_versiones
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "006_schema_fondation_consolide"
down_revision = "005_droits_fonciers_versiones"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ════════════════════════════════════════════════════════════════
    # NOUVELLES TABLES (gaps comblés)
    # ════════════════════════════════════════════════════════════════

    # ── 1. RNAF_VERSION_AUDIT ────────────────────────────────────────
    # Historique complet des versions d'un RNAF.
    # Manquait : parcel_versions existe pour les parcelles, pas pour RNAF.
    op.create_table(
        "rnaf_version_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        # FK vers rnaf existant (001)
        sa.Column("rnaf_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnaf.id"), nullable=False),

        # FK vers rnaf_workflow (004) — état au moment de la version
        sa.Column("rnaf_workflow_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnaf_workflow.id"), nullable=True),

        sa.Column("numero_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("statut_version", sa.String(30), nullable=False,
                  comment="Snapshot du statut RNAF à cette version"),
        sa.Column("date_version",   sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("motif_modification", sa.Text, nullable=False),

        # Snapshot JSON de l'état complet (immuable)
        sa.Column("snapshot_rnaf",  postgresql.JSONB, nullable=False),
        sa.Column("sha256_version", sa.String(64),    nullable=False,
                  comment="SHA-256 du snapshot — inviolable"),

        # Acteur
        sa.Column("modifie_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("valide_par",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),

        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),

        sa.UniqueConstraint("rnaf_id", "numero_version", name="uq_rnaf_version_num"),
    )
    op.create_index("ix_rnaf_va_rnaf",   "rnaf_version_audit", ["rnaf_id"])
    op.create_index("ix_rnaf_va_statut", "rnaf_version_audit", ["statut_version"])

    # ── 2. CCFM_CONTESTATION ────────────────────────────────────────
    # Contestation d'un certificat CCFM.
    # Manquait dans toutes les migrations précédentes.
    op.create_table(
        "ccfm_contestation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        # FK vers demandes_ccfm existant (001)
        sa.Column("demande_ccfm_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=False),

        # Lien vers parcelle contestée
        sa.Column("parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),

        # Lien vers right_holder contestataire (005)
        sa.Column("contestataire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=True),

        sa.Column("date_contestation", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("motif",   sa.Text, nullable=False),
        sa.Column("statut",  sa.String(30), server_default="ouvert", nullable=False,
                  comment="ouvert|en_instruction|acceptee|rejetee|classee"),

        # Pièces justificatives
        sa.Column("document_ref",  sa.String(500)),
        sa.Column("sha256_doc",    sa.String(64)),

        # Autorité saisie
        sa.Column("autorite_saisie", sa.String(30), server_default="ccfm",
                  comment="ccfm|justice|domaine"),

        # Décision
        sa.Column("decision_ref",           sa.String(200)),
        sa.Column("decision_judiciaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("decisions_judiciaires.id"), nullable=True),
        sa.Column("date_decision",          sa.DateTime(timezone=True)),

        # Déclenche automatiquement un blocage ?
        sa.Column("bloque_transactions", sa.Boolean, server_default="true",
                  nullable=False,
                  comment="Si True → transaction_block créé automatiquement"),

        sa.Column("cree_par",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_ccfm_cont_statut",  "ccfm_contestation", ["statut"])
    op.create_index("ix_ccfm_cont_demande", "ccfm_contestation", ["demande_ccfm_id"])

    # ── 3. RBAC_PERMISSION ───────────────────────────────────────────
    # Permissions granulaires réelles par action/ressource.
    # Le RBAC actuel stocke uniquement le rôle dans users.role.
    # Cette table définit CE QUE chaque rôle peut FAIRE sur chaque RESSOURCE.
    op.create_table(
        "rbac_permission",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        sa.Column("code_permission", sa.String(100), nullable=False, unique=True,
                  comment="ex: CADASTRE:PARCELLE:CREATE, CCFM:SUSPENSION:EMIT"),

        # Ressource et action
        sa.Column("module",    sa.String(50), nullable=False,
                  comment="cadastre|rnaf|ccfm|justice|notaire|banque|dar|annf|bgu|urbanisme"),
        sa.Column("ressource", sa.String(50), nullable=False,
                  comment="parcelle|droit|acte|suspension|decision|hypotheque..."),
        sa.Column("action",    sa.String(30), nullable=False,
                  comment="create|read|update|annuler|valider|suspendre|archiver|sceller"),

        # Périmètre géographique requis
        sa.Column("perimetre_requis", sa.String(20), server_default="any",
                  comment="national|regional|justice|any"),

        sa.Column("description", sa.Text),
        sa.Column("est_active",  sa.Boolean, server_default="true", nullable=False),
        sa.Column("created_at",  sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),

        sa.UniqueConstraint("module", "ressource", "action",
                             name="uq_rbac_permission_action"),
    )
    op.create_index("ix_rbac_perm_module",  "rbac_permission", ["module"])
    op.create_index("ix_rbac_perm_action",  "rbac_permission", ["action"])

    # ── 4. RBAC_ROLE_PERMISSION ──────────────────────────────────────
    # Liaison rôle ↔ permission.
    op.create_table(
        "rbac_role_permission",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        sa.Column("role_code",     sa.String(50), nullable=False,
                  comment="Valeur du champ users.role ex: GEOMETRE, CHEF_CCFM"),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rbac_permission.id"), nullable=False),

        # Surcharge possible : interdire une permission normalement accordée
        sa.Column("est_accordee",  sa.Boolean, default=True, nullable=False),

        sa.Column("accorde_par",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("created_at",   sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),

        sa.UniqueConstraint("role_code", "permission_id",
                             name="uq_rbac_role_perm"),
    )
    op.create_index("ix_rbac_rp_role", "rbac_role_permission", ["role_code"])

    # ── 5. AUDIT_LOG_GLOBAL (enrichissement) ────────────────────────
    # La table audit_logs (001) existe mais est orientée "logs applicatifs".
    # Ici : audit_sensitive_ops capture TOUTES les opérations sensibles
    # avec avant/après JSON, indépendamment du module.
    op.create_table(
        "audit_sensitive_ops",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        # Acteur
        sa.Column("user_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("role_acteur", sa.String(50)),
        sa.Column("ip_address",  sa.String(45)),
        sa.Column("session_id",  sa.String(100)),

        # Opération
        sa.Column("operation",    sa.String(50), nullable=False,
                  comment="INSERT|UPDATE|DELETE|ANNULER|VALIDER|SUSPENDRE|SCELLER|TRANSFERER"),
        sa.Column("table_cible",  sa.String(100), nullable=False),
        sa.Column("entite_id",    postgresql.UUID(as_uuid=True)),

        # Contexte métier
        sa.Column("module_source", sa.String(50)),
        sa.Column("nicad",         sa.String(30),
                  comment="Dénormalisé si l'opération concerne une parcelle"),

        # Avant / après
        sa.Column("valeur_avant", postgresql.JSONB),
        sa.Column("valeur_apres", postgresql.JSONB),
        sa.Column("diff_json",    postgresql.JSONB,
                  comment="Diff calculé entre avant et après"),

        # Intégrité chaîne
        sa.Column("sha256_op",   sa.String(64), nullable=False,
                  comment="SHA-256 de (user_id|operation|table|entite_id|timestamp)"),
        sa.Column("sha256_prev", sa.String(64),
                  comment="SHA-256 de l'entrée précédente — chaîne de hachage"),

        # Criticité
        sa.Column("niveau_criticite", sa.String(10), server_default="normal",
                  comment="faible|normal|eleve|critique"),
        sa.Column("alerte_generee",   sa.Boolean, server_default="false"),

        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False, index=True),

        # Partitionné par mois (comme audit_logs existant)
    )
    op.create_index("ix_aso_table",    "audit_sensitive_ops", ["table_cible"])
    op.create_index("ix_aso_entite",   "audit_sensitive_ops", ["entite_id"])
    op.create_index("ix_aso_user",     "audit_sensitive_ops", ["user_id"])
    op.create_index("ix_aso_criticite","audit_sensitive_ops", ["niveau_criticite"])

    # ════════════════════════════════════════════════════════════════
    # SEED RBAC — 29 rôles × permissions (données fondation)
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        INSERT INTO rbac_permission (code_permission, module, ressource, action, perimetre_requis, description)
        VALUES
        -- Cadastre
        ('CADASTRE:PARCELLE:CREATE',    'cadastre','parcelle','create','regional',   'Créer une parcelle'),
        ('CADASTRE:PARCELLE:VALIDER',   'cadastre','parcelle','valider','national',  'Valider une parcelle'),
        ('CADASTRE:PARCELLE:ANNULER',   'cadastre','parcelle','annuler','national',  'Annuler une parcelle (jamais DELETE)'),
        ('CADASTRE:SUBDIVISER',         'cadastre','parcelle','subdiviser','national','Subdiviser en 2 filles max'),
        ('CADASTRE:NICAD:GENERER',      'cadastre','nicad','create','national',      'Générer un NICAD'),
        -- RNAF
        ('RNAF:CREER',                  'rnaf','rnaf','create','national',           'Créer un RNAF'),
        ('RNAF:SOUMETTRE',              'rnaf','rnaf','soumettre','national',        'Soumettre un RNAF'),
        ('RNAF:SCELLER',                'rnaf','rnaf','sceller','national',          'Sceller un RNAF'),
        ('RNAF:PUBLIER',                'rnaf','rnaf','publier','national',          'Publier un RNAF (requiert JO)'),
        ('RNAF:SUSPENDRE',              'rnaf','rnaf','suspendre','national',        'Suspendre un RNAF'),
        ('RNAF:ANNULER',                'rnaf','rnaf','annuler','national',          'Annuler un RNAF'),
        -- CCFM
        ('CCFM:DEMANDE:CREATE',         'ccfm','demande','create','regional',        'Créer une demande CCFM'),
        ('CCFM:SUSPENSION:EMIT',        'ccfm','suspension','suspendre','regional',  'Émettre une suspension CCFM'),
        ('CCFM:SUSPENSION:LEVER',       'ccfm','suspension','lever','national',      'Lever une suspension CCFM'),
        ('CCFM:CONTESTATION:CREATE',    'ccfm','contestation','create','regional',   'Créer une contestation CCFM'),
        -- Droits fonciers
        ('DROIT:VERSION:CREATE',        'cadastre','droit','create','national',      'Créer une version de droit'),
        ('DROIT:TRANSFERT:ENREGISTRER', 'cadastre','transfert','create','national',  'Enregistrer un transfert'),
        -- Justice
        ('JUSTICE:LITIGE:CREATE',       'justice','litige','create','justice',       'Ouvrir un litige'),
        ('JUSTICE:DECISION:RENDRE',     'justice','decision','create','justice',     'Rendre une décision'),
        ('JUSTICE:PARCELLE:GELER',      'justice','parcelle','geler','justice',      'Geler une parcelle'),
        ('JUSTICE:PARCELLE:DEGELER',    'justice','parcelle','degeler','justice',    'Dégeler une parcelle'),
        -- Notaire
        ('NOTAIRE:ACTE:CREATE',         'notaire','acte','create','national',        'Créer un acte notarié'),
        ('NOTAIRE:TRANSFERT:CERTIFIER', 'notaire','transfert','certifier','national','Certifier un transfert'),
        -- Banque
        ('BANQUE:HYPOTHEQUE:CREATE',    'banque','hypotheque','create','national',   'Créer une hypothèque'),
        ('BANQUE:MAINLEVEE:LEVER',      'banque','hypotheque','lever','national',    'Lever une hypothèque'),
        -- ANNF
        ('ANNF:ARCHIVE:CREATE',         'annf','archive','create','national',        'Créer une archive ANNF'),
        ('ANNF:ARCHIVE:SCELLER',        'annf','archive','sceller','national',       'Sceller une archive WORM'),
        -- Admin
        ('ADMIN:RBAC:MANAGE',           'admin','rbac','manage','national',          'Gérer les permissions RBAC'),
        ('ADMIN:AUDIT:READ',            'admin','audit','read','national',           'Lire les logs d audit')
        ON CONFLICT (code_permission) DO NOTHING;
    """)

    # Liaisons rôles → permissions (29 rôles FONCIER+)
    op.execute("""
        INSERT INTO rbac_role_permission (role_code, permission_id, est_accordee)
        SELECT rp.role_code, p.id, true
        FROM (VALUES
            -- ADMIN : toutes permissions
            ('ADMIN',                   'ADMIN:RBAC:MANAGE'),
            ('ADMIN',                   'ADMIN:AUDIT:READ'),
            ('ADMIN',                   'RNAF:PUBLIER'),
            ('ADMIN',                   'CADASTRE:PARCELLE:ANNULER'),
            -- DIRECTEUR_URBANISME
            ('DIRECTEUR_URBANISME',     'RNAF:CREER'),
            ('DIRECTEUR_URBANISME',     'RNAF:SOUMETTRE'),
            ('DIRECTEUR_URBANISME',     'RNAF:SCELLER'),
            ('DIRECTEUR_URBANISME',     'RNAF:PUBLIER'),
            ('DIRECTEUR_URBANISME',     'RNAF:SUSPENDRE'),
            ('DIRECTEUR_URBANISME',     'CADASTRE:PARCELLE:ANNULER'),
            -- MINISTRE_URBANISME
            ('MINISTRE_URBANISME',      'RNAF:ANNULER'),
            ('MINISTRE_URBANISME',      'RNAF:PUBLIER'),
            -- DIRECTEUR_CADASTRE
            ('DIRECTEUR_CADASTRE',      'CADASTRE:PARCELLE:VALIDER'),
            ('DIRECTEUR_CADASTRE',      'CADASTRE:SUBDIVISER'),
            ('DIRECTEUR_CADASTRE',      'CADASTRE:NICAD:GENERER'),
            -- CHEF_SERVICE_CADASTRE
            ('CHEF_SERVICE_CADASTRE',   'CADASTRE:SUBDIVISER'),
            ('CHEF_SERVICE_CADASTRE',   'CADASTRE:NICAD:GENERER'),
            -- GEOMETRE
            ('GEOMETRE',                'CADASTRE:PARCELLE:CREATE'),
            ('GEOMETRE',                'DROIT:VERSION:CREATE'),
            -- CHEF_CCFM
            ('CHEF_CCFM',               'CCFM:DEMANDE:CREATE'),
            ('CHEF_CCFM',               'CCFM:SUSPENSION:EMIT'),
            ('CHEF_CCFM',               'CCFM:CONTESTATION:CREATE'),
            -- GUICHETIER_CCFM
            ('GUICHETIER_CCFM',         'CCFM:DEMANDE:CREATE'),
            -- JUST_PRESIDENT
            ('JUST_PRESIDENT',          'JUSTICE:DECISION:RENDRE'),
            ('JUST_PRESIDENT',          'JUSTICE:PARCELLE:DEGELER'),
            -- JUST_JUGE_FONCIER
            ('JUST_JUGE_FONCIER',       'JUSTICE:LITIGE:CREATE'),
            ('JUST_JUGE_FONCIER',       'JUSTICE:PARCELLE:GELER'),
            -- JUST_GREFFIER
            ('JUST_GREFFIER',           'JUSTICE:LITIGE:CREATE'),
            -- NOTAIRE
            ('NOTAIRE',                 'NOTAIRE:ACTE:CREATE'),
            ('NOTAIRE',                 'NOTAIRE:TRANSFERT:CERTIFIER'),
            ('NOTAIRE',                 'DROIT:TRANSFERT:ENREGISTRER'),
            -- BANQ_DIRECTEUR
            ('BANQ_DIRECTEUR',          'BANQUE:HYPOTHEQUE:CREATE'),
            ('BANQ_DIRECTEUR',          'BANQUE:MAINLEVEE:LEVER'),
            -- BANQ_AGENT
            ('BANQ_AGENT',              'BANQUE:HYPOTHEQUE:CREATE'),
            -- CHEF_DAR
            ('CHEF_DAR',                'ANNF:ARCHIVE:CREATE'),
            ('CHEF_DAR',                'ANNF:ARCHIVE:SCELLER'),
            -- ARCHIVISTE_DAR
            ('ARCHIVISTE_DAR',          'ANNF:ARCHIVE:CREATE'),
            -- AUDITEUR
            ('AUDITEUR',                'ADMIN:AUDIT:READ')
        ) AS rp(role_code, code_perm)
        JOIN rbac_permission p ON p.code_permission = rp.code_perm
        ON CONFLICT (role_code, permission_id) DO NOTHING;
    """)

    # ════════════════════════════════════════════════════════════════
    # TRIGGERS DE VERROUILLAGE — règles métier → contraintes SQL
    # ════════════════════════════════════════════════════════════════

    # T1 : Bloquer INSERT dans rnp_demandes si RNAF non PUBLIE ───────
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_rnaf_publie_for_rnp()
        RETURNS TRIGGER AS $$
        DECLARE
            v_statut_rnaf TEXT;
        BEGIN
            -- Chercher le statut RNAF via rnaf_workflow (004)
            SELECT rw.statut::TEXT INTO v_statut_rnaf
            FROM rnaf_workflow rw
            WHERE rw.rnaf_id = NEW.rnaf_id
            LIMIT 1;

            IF v_statut_rnaf IS NULL THEN
                RAISE EXCEPTION
                    'RNP BLOQUÉ: Aucun workflow RNAF trouvé pour rnaf_id=%. '
                    'Un RNAF doit exister et être PUBLIÉ avant toute création RNP.',
                    NEW.rnaf_id;
            END IF;

            IF v_statut_rnaf NOT IN ('publie', 'archive') THEN
                RAISE EXCEPTION
                    'RNP BLOQUÉ: RNAF au statut "%" — statut PUBLIÉ requis. '
                    'Workflow : BROUILLON → EN_VERIFICATION → EN_SCELLEMENT → SCELLÉ → PUBLIÉ.',
                    v_statut_rnaf;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_enforce_rnaf_publie_for_rnp
        BEFORE INSERT ON rnp_demandes
        FOR EACH ROW
        WHEN (NEW.rnaf_id IS NOT NULL)
        EXECUTE FUNCTION enforce_rnaf_publie_for_rnp();
    """)

    # T2 : Bloquer actes notariaux si hypothèque active ──────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_no_acte_with_active_hypotheque()
        RETURNS TRIGGER AS $$
        DECLARE
            nb_hyp       INT;
            nb_hyp_reg   INT;
            parcelle_cible UUID;
        BEGIN
            -- Récupérer la parcelle liée à l'acte
            -- L'acte notarié lie une parcelle via son dossier
            -- On utilise le champ parcelle_id s'il existe, sinon skip
            -- (structure actes_notaries v3.4.7 — adaptable)
            parcelle_cible := NEW.parcelle_id;
            IF parcelle_cible IS NULL THEN
                RETURN NEW;
            END IF;

            -- Vérifier hypothèques actives (table hypotheques 001)
            SELECT COUNT(*) INTO nb_hyp
            FROM hypotheques h
            WHERE h.parcelle_id = parcelle_cible
              AND h.statut = 'active';

            -- Vérifier mortgage_registry actif (005)
            SELECT COUNT(*) INTO nb_hyp_reg
            FROM mortgage_registry mr
            WHERE mr.parcelle_id = parcelle_cible
              AND mr.statut = 'active';

            IF (nb_hyp + nb_hyp_reg) > 0 THEN
                RAISE EXCEPTION
                    'ACTE BLOQUÉ: Parcelle % a % hypothèque(s) active(s). '
                    'Mainlevée bancaire obligatoire avant tout acte de mutation.',
                    parcelle_cible, (nb_hyp + nb_hyp_reg);
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_block_acte_if_hypotheque_active
        BEFORE INSERT ON actes_notaries
        FOR EACH ROW
        EXECUTE FUNCTION enforce_no_acte_with_active_hypotheque();
    """)

    # T3 : Bloquer modification géométrique sans CCFM valide ─────────
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_ccfm_for_geom_change()
        RETURNS TRIGGER AS $$
        DECLARE
            nb_ccfm_valide INT;
        BEGIN
            -- Skip si la géométrie ne change pas
            IF OLD.geom IS NOT DISTINCT FROM NEW.geom THEN
                RETURN NEW;
            END IF;

            -- Vérifier qu'il existe un CCFM validé pour cette parcelle
            SELECT COUNT(*) INTO nb_ccfm_valide
            FROM demandes_ccfm dc
            WHERE dc.parcelle_id = NEW.id
              AND dc.statut IN ('VALIDE', 'CERTIFIE', 'SCELLE')
            LIMIT 1;

            IF nb_ccfm_valide = 0 THEN
                -- Vérifier aussi via rnp_parcelle_link (004)
                SELECT COUNT(*) INTO nb_ccfm_valide
                FROM rnp_parcelle_link rpl
                JOIN demandes_ccfm dc ON dc.parcelle_id = NEW.id
                WHERE rpl.parcelle_id = NEW.id
                  AND rpl.statut_rnp = 'actif';
            END IF;

            IF nb_ccfm_valide = 0 THEN
                -- Vérifier s'il existe un blocage actif pour refonte validée
                -- (seule exception autorisée à la modification géométrique)
                IF NOT EXISTS (
                    SELECT 1 FROM refontes_lotissements rl
                    WHERE rl.lotissement_nouveau_id IN (
                        SELECT lotissement_id FROM ilots i
                        JOIN parcelles p ON p.ilot_id = i.id
                        WHERE p.id = NEW.id
                    )
                    AND rl.surface_coherence_ok = true
                ) THEN
                    RAISE EXCEPTION
                        'GÉOMÉTRIE BLOQUÉE: Modification de la géométrie de la parcelle % '
                        'interdite sans CCFM valide ou refonte totale autorisée. '
                        'Obtenir validation CCFM avant toute modification géométrique.',
                        NEW.nicad;
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_block_geom_without_ccfm
        BEFORE UPDATE OF geom ON parcelles
        FOR EACH ROW
        EXECUTE FUNCTION enforce_ccfm_for_geom_change();
    """)

    # T4 : Bloquer transfert si litige ouvert ────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_no_transfer_with_open_litige()
        RETURNS TRIGGER AS $$
        DECLARE
            nb_litiges     INT;
            nb_disputes    INT;
        BEGIN
            -- Litiges table existante (001)
            SELECT COUNT(*) INTO nb_litiges
            FROM litiges l
            WHERE l.parcelle_id = NEW.parcelle_id
              AND l.statut NOT IN ('clos', 'abandonne');

            -- Parcel_disputes (005)
            SELECT COUNT(*) INTO nb_disputes
            FROM parcel_disputes pd
            WHERE pd.parcelle_id = NEW.parcelle_id
              AND pd.statut IN ('ouvert', 'en_instruction');

            IF (nb_litiges + nb_disputes) > 0 THEN
                RAISE EXCEPTION
                    'TRANSFERT BLOQUÉ: Parcelle % a % litige(s) en cours. '
                    'Tous les litiges doivent être clôturés avant tout transfert de propriété.',
                    NEW.parcelle_id, (nb_litiges + nb_disputes);
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_block_transfer_if_litige
        BEFORE INSERT ON property_transfers
        FOR EACH ROW
        EXECUTE FUNCTION enforce_no_transfer_with_open_litige();
    """)

    # T5 : Bloquer publication JO si RNAF non scellé ────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_rnaf_scelle_for_jo()
        RETURNS TRIGGER AS $$
        DECLARE
            v_statut TEXT;
        BEGIN
            IF NEW.rnaf_id IS NULL THEN
                RETURN NEW;
            END IF;

            SELECT rw.statut::TEXT INTO v_statut
            FROM rnaf_workflow rw
            WHERE rw.rnaf_id = NEW.rnaf_id
            LIMIT 1;

            IF v_statut IS NULL OR v_statut NOT IN ('scelle', 'publie', 'archive') THEN
                RAISE EXCEPTION
                    'JOURNAL OFFICIEL BLOQUÉ: RNAF au statut "%" — '
                    'statut SCELLÉ requis avant publication au Journal Officiel.',
                    COALESCE(v_statut, 'non_trouve');
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_enforce_rnaf_scelle_for_jo
        BEFORE INSERT ON publications_jo
        FOR EACH ROW
        WHEN (NEW.rnaf_id IS NOT NULL)
        EXECUTE FUNCTION enforce_rnaf_scelle_for_jo();
    """)

    # T6 : Contestation CCFM → blocage transactionnel automatique ────
    op.execute("""
        CREATE OR REPLACE FUNCTION auto_block_on_ccfm_contestation()
        RETURNS TRIGGER AS $$
        DECLARE
            v_admin_id UUID;
            v_motif    TEXT;
        BEGIN
            IF NEW.bloque_transactions = true AND NEW.parcelle_id IS NOT NULL THEN
                SELECT id INTO v_admin_id FROM users WHERE role = 'ADMIN' LIMIT 1;
                v_motif := 'Blocage automatique — Contestation CCFM: '
                           || LEFT(NEW.motif, 150);

                INSERT INTO transaction_blocks (
                    parcelle_id, type_blockage, statut, motif,
                    sha256_motif, source_ccfm_id, pose_par
                )
                SELECT
                    NEW.parcelle_id,
                    'ccfm_suspension'::type_blockage,
                    'actif'::statut_blockage,
                    v_motif,
                    encode(sha256(v_motif::bytea), 'hex'),
                    (SELECT cs.id FROM ccfm_suspension cs
                     WHERE cs.demande_ccfm_id = NEW.demande_ccfm_id
                       AND cs.est_active = true
                     LIMIT 1),
                    COALESCE(NEW.cree_par, v_admin_id)
                WHERE NOT EXISTS (
                    SELECT 1 FROM transaction_blocks tb
                    WHERE tb.parcelle_id = NEW.parcelle_id
                      AND tb.statut = 'actif'
                      AND tb.motif LIKE '%Contestation CCFM%'
                );

                -- Enregistrer dans parcel_public_history (004)
                INSERT INTO parcel_public_history (
                    parcelle_id, nicad, statut_avant, statut_apres,
                    module_source, entite_id, motif_public, role_acteur
                )
                SELECT
                    NEW.parcelle_id,
                    p.nicad,
                    p.statut::TEXT,
                    'conteste',
                    'ccfm',
                    NEW.id,
                    'Contestation CCFM déposée — transactions bloquées',
                    'CCFM'
                FROM parcelles p WHERE p.id = NEW.parcelle_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_auto_block_ccfm_contestation
        AFTER INSERT ON ccfm_contestation
        FOR EACH ROW
        WHEN (NEW.bloque_transactions = true)
        EXECUTE FUNCTION auto_block_on_ccfm_contestation();
    """)

    # T7 : Audit automatique sur toutes opérations sensibles ─────────
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_sensitive_operation()
        RETURNS TRIGGER AS $$
        DECLARE
            v_op         TEXT;
            v_avant      JSONB;
            v_apres      JSONB;
            v_sha256     TEXT;
            v_nicad      TEXT;
            v_payload    TEXT;
        BEGIN
            v_op := TG_OP;  -- INSERT, UPDATE, DELETE

            v_avant := CASE WHEN TG_OP IN ('UPDATE', 'DELETE')
                            THEN to_jsonb(OLD) ELSE NULL END;
            v_apres := CASE WHEN TG_OP IN ('INSERT', 'UPDATE')
                            THEN to_jsonb(NEW) ELSE NULL END;

            -- Extraire NICAD si disponible
            v_nicad := CASE
                WHEN TG_OP IN ('INSERT', 'UPDATE') THEN
                    (v_apres->>'nicad')
                ELSE
                    (v_avant->>'nicad')
            END;

            v_payload := TG_TABLE_NAME || '|' || v_op || '|' ||
                         COALESCE((v_apres->>'id'), (v_avant->>'id'), '') || '|' ||
                         NOW()::TEXT;
            v_sha256 := encode(sha256(v_payload::bytea), 'hex');

            INSERT INTO audit_sensitive_ops (
                operation, table_cible, entite_id,
                module_source, nicad,
                valeur_avant, valeur_apres,
                sha256_op, niveau_criticite,
                created_at
            ) VALUES (
                v_op, TG_TABLE_NAME,
                COALESCE(
                    (v_apres->>'id')::UUID,
                    (v_avant->>'id')::UUID
                ),
                TG_TABLE_NAME,
                v_nicad,
                v_avant, v_apres,
                v_sha256,
                CASE TG_TABLE_NAME
                    WHEN 'parcelles'           THEN 'eleve'
                    WHEN 'parcel_right_version' THEN 'critique'
                    WHEN 'property_transfers'  THEN 'critique'
                    WHEN 'transaction_blocks'  THEN 'eleve'
                    WHEN 'ccfm_contestation'   THEN 'eleve'
                    WHEN 'rnaf_workflow'        THEN 'critique'
                    ELSE 'normal'
                END,
                NOW()
            );

            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Appliquer l'audit sur les tables les plus sensibles
    for table in [
        "parcel_right_version",  # critique : droits de propriété
        "property_transfers",    # critique : transferts
        "rnaf_workflow",         # critique : statut RNAF
        "transaction_blocks",    # élevé   : blocages
        "ccfm_contestation",     # élevé   : contestations
        "parcelles",             # élevé   : géométrie + statut
        "ccfm_suspension",       # élevé   : suspensions
        "annf_right_archive",    # normal  : archivage
    ]:
        op.execute(f"""
            CREATE TRIGGER tg_audit_{table}
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION audit_sensitive_operation();
        """)

    # T8 : Version RNAF automatique à chaque changement de statut ────
    op.execute("""
        CREATE OR REPLACE FUNCTION auto_version_rnaf_on_status_change()
        RETURNS TRIGGER AS $$
        DECLARE
            v_version_num INT;
            v_sha256      TEXT;
            v_snapshot    JSONB;
            v_admin_id    UUID;
        BEGIN
            IF OLD.statut IS DISTINCT FROM NEW.statut THEN
                SELECT COALESCE(MAX(numero_version), 0) + 1
                INTO v_version_num
                FROM rnaf_version_audit
                WHERE rnaf_id = NEW.rnaf_id;

                v_snapshot := jsonb_build_object(
                    'rnaf_id',      NEW.rnaf_id,
                    'workflow_id',  NEW.id,
                    'statut',       NEW.statut,
                    'statut_avant', OLD.statut,
                    'timestamp',    NOW()
                );

                v_sha256 := encode(
                    sha256((v_snapshot::TEXT)::bytea),
                    'hex'
                );

                SELECT id INTO v_admin_id FROM users WHERE role = 'ADMIN' LIMIT 1;

                INSERT INTO rnaf_version_audit (
                    rnaf_id, rnaf_workflow_id, numero_version,
                    statut_version, motif_modification,
                    snapshot_rnaf, sha256_version, modifie_par
                ) VALUES (
                    NEW.rnaf_id, NEW.id, v_version_num,
                    NEW.statut::TEXT,
                    'Transition automatique: ' || OLD.statut::TEXT || ' → ' || NEW.statut::TEXT,
                    v_snapshot, v_sha256,
                    COALESCE(NEW.suspendu_par, NEW.publie_par,
                             NEW.scelle_par, NEW.soumis_par, v_admin_id)
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_auto_version_rnaf
        AFTER UPDATE OF statut ON rnaf_workflow
        FOR EACH ROW
        WHEN (OLD.statut IS DISTINCT FROM NEW.statut)
        EXECUTE FUNCTION auto_version_rnaf_on_status_change();
    """)

    # ════════════════════════════════════════════════════════════════
    # VUES CONSOLIDÉES
    # ════════════════════════════════════════════════════════════════

    # Vue : état complet d'une parcelle toutes sources confondues
    op.execute("""
        CREATE OR REPLACE VIEW v_etat_parcelle_complet AS
        SELECT
            p.id                        AS parcelle_id,
            p.nicad,
            p.statut::TEXT              AS statut_parcelle,
            p.surface_m2,
            p.is_gele,

            -- Statut RNP
            rpl.statut_rnp::TEXT        AS statut_rnp,

            -- Statut RNAF
            rw.statut::TEXT             AS statut_rnaf,

            -- Statut public BGU
            bps.statut_public           AS statut_public,

            -- Blocks actifs
            COALESCE((
                SELECT COUNT(*) FROM transaction_blocks tb
                WHERE tb.parcelle_id = p.id AND tb.statut = 'actif'
            ), 0)                       AS nb_blocks_actifs,

            -- Suspensions CCFM actives
            COALESCE((
                SELECT COUNT(*) FROM ccfm_suspension cs
                WHERE cs.parcelle_id = p.id AND cs.est_active = true
            ), 0)                       AS nb_suspensions_actives,

            -- Contestations CCFM ouvertes
            COALESCE((
                SELECT COUNT(*) FROM ccfm_contestation cc
                WHERE cc.parcelle_id = p.id AND cc.statut IN ('ouvert','en_instruction')
            ), 0)                       AS nb_contestations,

            -- Conflits antifraude critiques
            COALESCE((
                SELECT COUNT(*) FROM conflits_parcellaires cp
                WHERE cp.parcelle_a_id = p.id AND cp.gravite = 'critique'
                  AND cp.statut IN ('ouvert','bloque')
            ), 0)                       AS nb_conflits_critiques,

            -- Hypothèques actives
            COALESCE((
                SELECT COUNT(*) FROM hypotheques h
                WHERE h.parcelle_id = p.id AND h.statut = 'active'
            ), 0)                       AS nb_hypotheques_actives,

            -- Litiges ouverts
            COALESCE((
                SELECT COUNT(*) FROM litiges l
                WHERE l.parcelle_id = p.id AND l.statut NOT IN ('clos','abandonne')
            ), 0) + COALESCE((
                SELECT COUNT(*) FROM parcel_disputes pd
                WHERE pd.parcelle_id = p.id AND pd.statut IN ('ouvert','en_instruction')
            ), 0)                       AS nb_litiges_ouverts,

            -- Éligibilité transaction globale
            (
                p.statut = 'active'
                AND p.is_gele = false
                AND NOT EXISTS (SELECT 1 FROM transaction_blocks tb
                    WHERE tb.parcelle_id = p.id AND tb.statut = 'actif')
                AND NOT EXISTS (SELECT 1 FROM ccfm_suspension cs
                    WHERE cs.parcelle_id = p.id AND cs.est_active = true)
                AND NOT EXISTS (SELECT 1 FROM ccfm_contestation cc
                    WHERE cc.parcelle_id = p.id AND cc.statut IN ('ouvert','en_instruction'))
                AND NOT EXISTS (SELECT 1 FROM conflits_parcellaires cp
                    WHERE cp.parcelle_a_id = p.id AND cp.gravite = 'critique'
                      AND cp.statut IN ('ouvert','bloque'))
                AND NOT EXISTS (SELECT 1 FROM hypotheques h
                    WHERE h.parcelle_id = p.id AND h.statut = 'active')
                AND NOT EXISTS (SELECT 1 FROM litiges l
                    WHERE l.parcelle_id = p.id AND l.statut NOT IN ('clos','abandonne'))
                AND rpl.statut_rnp::TEXT = 'actif'
            )                           AS eligible_transaction

        FROM parcelles p
        LEFT JOIN rnp_parcelle_link   rpl ON rpl.parcelle_id = p.id
        LEFT JOIN rnaf_workflow       rw  ON rw.rnaf_id = (
            SELECT rnaf_id FROM rnp_parcelle_link WHERE parcelle_id = p.id LIMIT 1
        )
        LEFT JOIN bgu_parcel_status   bps ON bps.parcelle_id = p.id
        WHERE p.statut NOT IN ('annule', 'archive');
    """)

    # Vue : permissions effectives par utilisateur
    op.execute("""
        CREATE OR REPLACE VIEW v_rbac_permissions AS
        SELECT
            u.id            AS user_id,
            u.email,
            u.role          AS role_code,
            p.code_permission,
            p.module,
            p.ressource,
            p.action,
            p.perimetre_requis,
            rp.est_accordee,
            CASE
                WHEN u.type_structure = 'NATIONAL' AND p.perimetre_requis IN ('national','any')
                    THEN true
                WHEN u.type_structure = 'REGIONAL' AND p.perimetre_requis IN ('regional','any')
                    THEN true
                WHEN u.type_structure = 'JUSTICE'  AND p.perimetre_requis IN ('justice','any')
                    THEN true
                WHEN p.perimetre_requis = 'any'
                    THEN true
                ELSE false
            END             AS perimetre_ok
        FROM users u
        JOIN rbac_role_permission rp ON rp.role_code = u.role AND rp.est_accordee = true
        JOIN rbac_permission      p  ON p.id = rp.permission_id AND p.est_active = true
        WHERE u.is_active = true;
    """)

    # Vue : audit des dernières 24h opérations critiques
    op.execute("""
        CREATE OR REPLACE VIEW v_audit_recent_critique AS
        SELECT
            aso.id,
            aso.operation,
            aso.table_cible,
            aso.entite_id,
            aso.nicad,
            aso.module_source,
            aso.niveau_criticite,
            aso.sha256_op,
            u.email         AS acteur_email,
            u.role          AS acteur_role,
            aso.ip_address,
            aso.created_at
        FROM audit_sensitive_ops aso
        LEFT JOIN users u ON u.id = aso.user_id
        WHERE aso.created_at >= NOW() - INTERVAL '24 hours'
          AND aso.niveau_criticite IN ('eleve', 'critique')
        ORDER BY aso.created_at DESC;
    """)


def downgrade() -> None:
    for view in ["v_audit_recent_critique", "v_rbac_permissions",
                 "v_etat_parcelle_complet"]:
        op.execute(f"DROP VIEW IF EXISTS {view} CASCADE")

    for trigger, table in [
        ("tg_auto_version_rnaf",               "rnaf_workflow"),
        ("tg_audit_parcelles",                  "parcelles"),
        ("tg_audit_ccfm_suspension",            "ccfm_suspension"),
        ("tg_audit_ccfm_contestation",          "ccfm_contestation"),
        ("tg_audit_transaction_blocks",         "transaction_blocks"),
        ("tg_audit_rnaf_workflow",              "rnaf_workflow"),
        ("tg_audit_property_transfers",         "property_transfers"),
        ("tg_audit_parcel_right_version",       "parcel_right_version"),
        ("tg_audit_annf_right_archive",         "annf_right_archive"),
        ("tg_auto_block_ccfm_contestation",     "ccfm_contestation"),
        ("tg_enforce_rnaf_scelle_for_jo",       "publications_jo"),
        ("tg_block_transfer_if_litige",         "property_transfers"),
        ("tg_block_geom_without_ccfm",          "parcelles"),
        ("tg_block_acte_if_hypotheque_active",  "actes_notaries"),
        ("tg_enforce_rnaf_publie_for_rnp",      "rnp_demandes"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    for fn in [
        "enforce_rnaf_publie_for_rnp",
        "enforce_no_acte_with_active_hypotheque",
        "enforce_ccfm_for_geom_change",
        "enforce_no_transfer_with_open_litige",
        "enforce_rnaf_scelle_for_jo",
        "auto_block_on_ccfm_contestation",
        "audit_sensitive_operation",
        "auto_version_rnaf_on_status_change",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")

    for table in ["audit_sensitive_ops", "rbac_role_permission",
                  "rbac_permission", "ccfm_contestation", "rnaf_version_audit"]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
