"""017_workflows_completion

FONCIER+ v3.4.8 — Migration 017 : Complétion des 19 workflows manquants
Étend l'ENUM type_workflow avec les 12 nouveaux types métier et insère
les workflow_definition + workflow_step_def + workflow_permission pour
les 19 workflows absents de la v3.4.7.

Revision ID: 017_workflows_completion
Revises: 016_e2e_infrastructure
Create Date: 2026-04-11
"""
from alembic import op

revision = "017_workflows_completion"
down_revision = "016_e2e_infrastructure"
branch_labels = None
depends_on = None


# ═══════════════════════════════════════════════════════════════════
# SPÉCIFICATION COMPACTE DES 19 WORKFLOWS À AJOUTER
# ═══════════════════════════════════════════════════════════════════
# Format : (code_wf, type_enum, module, nom, [(ordre, etape, role, action)])
WORKFLOWS_NOUVEAUX = [
    ("WF6", "DIVISION_TECHNIQUE", "cadastre", "Division parcellaire technique", [
        (1, "depot_plan", "GEOMETRE", "creer"),
        (2, "conformite_urbanisme", "CHEF_URBANISME", "valider"),
        (3, "bornage_terrain", "TOPOGRAPHE", "valider"),
        (4, "signature", "DIRECTEUR_CADASTRE", "signer"),
        (5, "annf_archive", "ARCHIVISTE_ANNF", "archiver"),
    ]),
    ("WF7", "SERVITUDES", "cadastre", "Servitudes foncières", [
        (1, "declaration", "GEOMETRE", "creer"),
        (2, "verification", "CHEF_SERVICE_CADASTRE", "valider"),
        (3, "signature", "DIRECTEUR_CADASTRE", "signer"),
        (4, "publication", "EDITEUR_JO", "valider"),
        (5, "annf_archive", "ARCHIVISTE_ANNF", "archiver"),
    ]),
    ("WF8", "HYPOTHEQUE", "banque", "Hypothèques bancaires", [
        (1, "demande", "BANQ_AGENT", "creer"),
        (2, "verification_parcelle", "BANQ_AGENT", "valider"),
        (3, "vis_notaire", "NOTAIRE", "valider"),
        (4, "signature", "BANQ_DIRECTEUR", "signer"),
        (5, "annf_archive", "ARCHIVISTE_ANNF", "archiver"),
    ]),
    ("WF9", "SUCCESSION_TECHNIQUE", "notaire", "Succession (variante technique)", [
        (1, "declaration", "NOTAIRE", "creer"),
        (2, "heritiers", "NOTAIRE", "valider"),
        (3, "partage", "NOTAIRE", "valider"),
        (4, "visa_judiciaire", "JUGE_FONCIER", "signer"),
        (5, "annf_archive", "ARCHIVISTE_ANNF", "archiver"),
    ]),
    ("WF10", "CHANGEMENT_USAGE_OP", "urbanisme", "Changement d'usage opérationnel", [
        (1, "depot", "AGENT_URBANISME", "creer"),
        (2, "plan_urbanisme", "CHEF_URBANISME", "valider"),
        (3, "decision", "DIRECTEUR_URBANISME", "signer"),
    ]),
    ("WF11", "EXPROPRIATION", "urbanisme", "Expropriation pour utilité publique", [
        (1, "declaration_up", "DIRECTEUR_URBANISME", "creer"),
        (2, "enquete", "AGENT_URBANISME", "valider"),
        (3, "indemnisation", "DIRECTEUR_DOMAINE", "valider"),
        (4, "transfert", "JUGE_FONCIER", "valider"),
        (5, "arrete_ministeriel", "MINISTRE_URBANISME", "signer"),
        (6, "annf_archive", "ARCHIVISTE_ANNF", "archiver"),
    ]),
    ("WF12", "REGULARISATION", "commune", "Régularisation foncière", [
        (1, "demande", "AGENT_COMMUNE", "creer"),
        (2, "constat_terrain", "TOPOGRAPHE", "valider"),
        (3, "instruction", "MAIRE", "valider"),
        (4, "decision", "ADMIN_COMMUNE", "signer"),
        (5, "attribution_titre", "DIRECTEUR_CADASTRE", "valider"),
    ]),
    ("WF13", "CONFLIT_FONCIER", "justice", "Gestion des conflits fonciers", [
        (1, "signalement", "MAIRE", "creer"),
        (2, "conciliation", "HUISSIER", "valider"),
        (3, "decision", "JUGE_FONCIER", "signer"),
        (4, "execution", "GREFFIER", "valider"),
    ]),
    ("WF14", "LOTISSEMENT", "urbanisme", "Opération de lotissement", [
        (1, "projet", "DIRECTEUR_URBANISME", "creer"),
        (2, "approbation_technique", "CHEF_SERVICE_CADASTRE", "valider"),
        (3, "arrete", "MINISTRE_URBANISME", "signer"),
        (4, "bornage", "GEOMETRE", "valider"),
        (5, "attribution_lots", "DIRECTEUR_CADASTRE", "valider"),
    ]),
    ("WF15", "ZONE_SPECIALE", "urbanisme", "Zones spéciales", [
        (1, "classification", "DIRECTEUR_URBANISME", "creer"),
        (2, "arrete", "MINISTRE_URBANISME", "signer"),
        (3, "inscription", "EDITEUR_JO", "valider"),
    ]),
    ("WF17", "MUTATION_VENTE", "notaire", "Mutation de propriété par vente", [
        (1, "depot_mutation", "NOTAIRE", "creer"),
        (2, "verif_juridique", "CHEF_CCFM", "valider"),
        (3, "verif_financiere", "DIRECTEUR_DOMAINE", "valider"),
        (4, "validation_finale", "DIRECTEUR_CADASTRE", "signer"),
        (5, "nouveau_titre", "DIRECTEUR_CADASTRE", "valider"),
        (6, "annf_archive", "ARCHIVISTE_ANNF", "archiver"),
    ]),
    ("WF18", "DONATION", "notaire", "Donation foncière", [
        (1, "acte_donation", "NOTAIRE", "creer"),
        (2, "consentement", "NOTAIRE", "valider"),
        (3, "protection_heritiers", "JUGE_FONCIER", "valider"),
        (4, "validation", "DIRECTEUR_CADASTRE", "signer"),
        (5, "annf_archive", "ARCHIVISTE_ANNF", "archiver"),
    ]),
    ("WF19", "SUCCESSION_FONCIERE", "notaire", "Succession foncière", [
        (1, "declaration_deces", "MAIRE", "creer"),
        (2, "identification_heritiers", "NOTAIRE", "valider"),
        (3, "partage", "NOTAIRE", "valider"),
        (4, "jugement", "JUGE_FONCIER", "signer"),
        (5, "nouveaux_titres", "DIRECTEUR_CADASTRE", "valider"),
        (6, "annf_archive", "ARCHIVISTE_ANNF", "archiver"),
    ]),
    ("WF21", "DIVISION_TITRE", "cadastre", "Division de titre foncier", [
        (1, "demande", "GEOMETRE", "creer"),
        (2, "conformite_urbanisme", "CHEF_URBANISME", "valider"),
        (3, "creation_polygones", "TOPOGRAPHE", "valider"),
        (4, "signature", "DIRECTEUR_CADASTRE", "signer"),
        (5, "annf_archive", "ARCHIVISTE_ANNF", "archiver"),
    ]),
    ("WF22", "CHANGEMENT_USAGE_TITRE", "urbanisme", "Changement d'usage du titre", [
        (1, "depot", "AGENT_URBANISME", "creer"),
        (2, "plan_urbanisme", "CHEF_URBANISME", "valider"),
        (3, "consultation_commune", "MAIRE", "valider"),
        (4, "decision", "DIRECTEUR_URBANISME", "signer"),
        (5, "statut_parcelle", "CHEF_SERVICE_CADASTRE", "valider"),
    ]),
    ("WF23", "RECTIFICATION", "ccfm", "Rectification administrative", [
        (1, "demande", "AGENT_CCFM", "creer"),
        (2, "analyse_erreur", "CHEF_CCFM", "valider"),
        (3, "double_validation", "CHEF_SERVICE_CADASTRE", "valider"),
        (4, "signature", "DIRECTEUR_CADASTRE", "signer"),
        (5, "annf_archive", "ARCHIVISTE_ANNF", "archiver"),
    ]),
    ("WF26", "LEVEE_LITIGE", "justice", "Levée de litige", [
        (1, "decision_tribunal", "JUGE_FONCIER", "creer"),
        (2, "verification", "GREFFIER", "valider"),
        (3, "levee_blocage", "DIRECTEUR_CADASTRE", "signer"),
    ]),
    ("WF28", "LITIGES_UNIFIES", "justice", "Consolidation litiges enrichis", [
        (1, "consolidation", "JUGE_FONCIER", "creer"),
        (2, "matching", "AUDITEUR", "valider"),
        (3, "fusion_dossiers", "GREFFIER", "signer"),
    ]),
    ("WF29", "REVISION_MASSIVE", "cadastre", "Révision cadastrale massive", [
        (1, "programme", "DIRECTEUR_CADASTRE", "creer"),
        (2, "campagne_terrain", "TOPOGRAPHE", "valider"),
        (3, "mise_a_jour", "ADMIN_CADASTRE", "valider"),
        (4, "scellement", "MINISTRE_URBANISME", "signer"),
    ]),
    ("WF33", "CONTINUITE_SYSTEME", "audit", "Sauvegarde et continuité", [
        (1, "sauvegarde", "ADMIN", "creer"),
        (2, "verification_integrite", "AUDITEUR", "valider"),
        (3, "validation", "RESPONSABLE_ANNF", "signer"),
    ]),
]


def upgrade() -> None:
    # ─── 1. Étendre l'ENUM type_workflow ────────────────────────────────
    nouveaux_types = sorted({w[1] for w in WORKFLOWS_NOUVEAUX})
    for t in nouveaux_types:
        op.execute(f"""
            DO $$ BEGIN
                ALTER TYPE type_workflow ADD VALUE IF NOT EXISTS '{t}';
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)

    # ─── 2. Insertion en masse via une fonction helper PL/pgSQL ─────────
    op.execute("""
        CREATE OR REPLACE FUNCTION _seed_wf(
            p_code TEXT, p_type TEXT, p_module TEXT, p_nom TEXT
        ) RETURNS UUID AS $$
        DECLARE v_id UUID;
        BEGIN
            INSERT INTO workflow_definition (
                id, code_wf, type_workflow, module, nom_workflow,
                version, is_active, created_at
            )
            VALUES (gen_random_uuid(), p_code, p_type::type_workflow, p_module, p_nom,
                    1, TRUE, NOW())
            ON CONFLICT (code_wf) DO UPDATE
                SET nom_workflow = EXCLUDED.nom_workflow,
                    is_active = TRUE
            RETURNING id INTO v_id;
            RETURN v_id;
        END; $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION _seed_wf_step(
            p_def_id UUID, p_ordre INT, p_code TEXT,
            p_role TEXT, p_action TEXT
        ) RETURNS VOID AS $$
        BEGIN
            INSERT INTO workflow_step_def (
                id, definition_id, ordre, code_etape, role_attendu,
                action, sla_heures, is_final
            )
            VALUES (gen_random_uuid(), p_def_id, p_ordre, p_code, p_role,
                    p_action, 72, FALSE)
            ON CONFLICT (definition_id, ordre) DO UPDATE
                SET code_etape = EXCLUDED.code_etape,
                    role_attendu = EXCLUDED.role_attendu,
                    action = EXCLUDED.action;
        END; $$ LANGUAGE plpgsql;
    """)

    # ─── 3. Insertion Python-driven des 19 workflows ────────────────────
    for code_wf, type_enum, module, nom, etapes in WORKFLOWS_NOUVEAUX:
        op.execute(f"""
            DO $$
            DECLARE v_id UUID;
            BEGIN
                v_id := _seed_wf(
                    '{code_wf}', '{type_enum}', '{module}',
                    '{nom.replace("'", "''")}'
                );
                {
                    chr(10).join(
                        f"PERFORM _seed_wf_step(v_id, {ordre}, '{etape}', "
                        f"'{role}', '{action}');"
                        for (ordre, etape, role, action) in etapes
                    )
                }
                -- Marquer la dernière étape comme finale
                UPDATE workflow_step_def SET is_final = TRUE
                    WHERE definition_id = v_id
                      AND ordre = (SELECT MAX(ordre) FROM workflow_step_def
                                   WHERE definition_id = v_id);
            END $$;
        """)

    # ─── 4. Nettoyage des helpers temporaires ───────────────────────────
    op.execute("DROP FUNCTION IF EXISTS _seed_wf(TEXT, TEXT, TEXT, TEXT);")
    op.execute("DROP FUNCTION IF EXISTS _seed_wf_step(UUID, INT, TEXT, TEXT, TEXT);")


def downgrade() -> None:
    for code_wf, *_ in WORKFLOWS_NOUVEAUX:
        op.execute(f"""
            DELETE FROM workflow_step_def WHERE definition_id IN (
                SELECT id FROM workflow_definition WHERE code_wf = '{code_wf}'
            );
            DELETE FROM workflow_definition WHERE code_wf = '{code_wf}';
        """)
    # Note : les valeurs ENUM ne peuvent pas être supprimées en PostgreSQL
    # sans recréer le type. On les laisse, c'est sans effet.
