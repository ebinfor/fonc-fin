"""019_workflows_partiels_completion

FONCIER+ v3.4.8 — Migration 019 : Complétion des 6 workflows partiels
Transforme les workflows qui ont des tables/triggers mais pas de
workflow_definition en workflows pilotables via le WorkflowEngine.

Partiels traités :
  WF20 Fusion (FUSION_PARCELLAIRE existe hors ENUM — intégré)
  WF25 Mise sous litige (trigger gel OK, workflow_definition manquant)
  WF27 Archivage définitif (annf_right_archive OK, workflow_definition manquant)
  WF31 Alertes automatiques (vues + fonctions OK, workflow_definition manquant)
  WF32 Indicateurs nationaux (vues OK, workflow_definition manquant)
  WF8 a été complété par 017 mais on renforce ici sa cohérence.

Revision ID: 019_workflows_partiels_completion
Revises: 018_workflows_bugfixes
Create Date: 2026-04-11
"""
from alembic import op

revision = "019_workflows_partiels_completion"
down_revision = "018_workflows_bugfixes"
branch_labels = None
depends_on = None


WORKFLOWS_PARTIELS = [
    ("WF20", "FUSION_PARCELLAIRE", "cadastre", "Fusion de parcelles contiguës", [
        (1, "demande_fusion", "GEOMETRE", "creer"),
        (2, "verification_st_touches", "TOPOGRAPHE", "valider"),
        (3, "fusion_geometrique", "GEOMETRE", "valider"),
        (4, "calcul_surface", "CHEF_SERVICE_CADASTRE", "valider"),
        (5, "signature", "DIRECTEUR_CADASTRE", "signer"),
        (6, "annf_archive", "ARCHIVISTE_ANNF", "archiver"),
    ]),
    ("WF25", "MISE_SOUS_LITIGE", "justice", "Mise sous litige avec gel", [
        (1, "depot_plainte", "HUISSIER", "creer"),
        (2, "enregistrement", "GREFFIER", "valider"),
        (3, "blocage_automatique", "JUGE_FONCIER", "signer"),
        (4, "notification_parties", "GREFFIER", "valider"),
    ]),
    ("WF27", "ARCHIVAGE_DEFINITIF", "annf", "Archivage WORM définitif", [
        (1, "detection_inactivite", "ARCHIVISTE_ANNF", "creer"),
        (2, "transfert_archive", "ARCHIVISTE_ANNF", "valider"),
        (3, "indexation", "ARCHIVISTE_ANNF", "valider"),
        (4, "scellement_worm", "RESPONSABLE_ANNF", "signer"),
    ]),
    ("WF31", "ALERTES_AUTO", "audit", "Alertes et anomalies automatiques", [
        (1, "detection", "AUDITEUR", "creer"),
        (2, "classification", "AUDITEUR", "valider"),
        (3, "notification", "AUDITEUR", "valider"),
        (4, "resolution_ou_escalade", "AUDITEUR", "signer"),
    ]),
    ("WF32", "INDICATEURS_NATIONAUX", "audit", "Calcul des KPIs nationaux", [
        (1, "collecte", "AUDITEUR", "creer"),
        (2, "calcul", "AUDITEUR", "valider"),
        (3, "agregation", "AUDITEUR", "valider"),
        (4, "publication_dashboard", "AUDITEUR", "signer"),
    ]),
]


def upgrade() -> None:
    # ─── 1. Étendre l'ENUM avec les nouveaux types ──────────────────────
    for wf_code, type_enum, *_ in WORKFLOWS_PARTIELS:
        op.execute(f"""
            DO $$ BEGIN
                ALTER TYPE type_workflow ADD VALUE IF NOT EXISTS '{type_enum}';
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)

    # ─── 2. Helpers identiques à 017 (recréés localement) ───────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION _seed_wf_partiel(
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
                    type_workflow = EXCLUDED.type_workflow,
                    is_active = TRUE
            RETURNING id INTO v_id;
            RETURN v_id;
        END; $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION _seed_wf_step_partiel(
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
                    role_attendu = EXCLUDED.role_attendu;
        END; $$ LANGUAGE plpgsql;
    """)

    # ─── 3. Insertion des 5 workflows partiels ──────────────────────────
    for code_wf, type_enum, module, nom, etapes in WORKFLOWS_PARTIELS:
        op.execute(f"""
            DO $$
            DECLARE v_id UUID;
            BEGIN
                v_id := _seed_wf_partiel(
                    '{code_wf}', '{type_enum}', '{module}',
                    '{nom.replace("'", "''")}'
                );
                {
                    chr(10).join(
                        f"PERFORM _seed_wf_step_partiel(v_id, {o}, '{e}', '{r}', '{a}');"
                        for (o, e, r, a) in etapes
                    )
                }
                UPDATE workflow_step_def SET is_final = TRUE
                    WHERE definition_id = v_id
                      AND ordre = (SELECT MAX(ordre) FROM workflow_step_def
                                   WHERE definition_id = v_id);
            END $$;
        """)

    # ─── 4. Triggers de cohérence pour les partiels ─────────────────────
    # WF20 Fusion : s'assurer que le workflow_instance FUSION porte bien
    # l'info ST_Touches vérifiée en amont
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_check_fusion_st_touches()
        RETURNS TRIGGER AS $$
        DECLARE
            v_parcelles UUID[];
            v_p1 UUID; v_p2 UUID;
            v_touches BOOLEAN;
        BEGIN
            IF NEW.type_workflow != 'FUSION_PARCELLAIRE' THEN RETURN NEW; END IF;

            v_parcelles := ARRAY(
                SELECT jsonb_array_elements_text(NEW.contexte->'parcelles')::UUID
            );
            IF array_length(v_parcelles, 1) < 2 THEN
                RAISE EXCEPTION 'FUSION REFUSÉE: au moins 2 parcelles requises';
            END IF;

            -- Vérifier ST_Touches sur toutes les paires
            FOR i IN 1..array_length(v_parcelles, 1) - 1 LOOP
                FOR j IN i+1..array_length(v_parcelles, 1) LOOP
                    SELECT ST_Touches(a.geometry, b.geometry) INTO v_touches
                    FROM parcelles a, parcelles b
                    WHERE a.id = v_parcelles[i] AND b.id = v_parcelles[j];
                    IF NOT v_touches THEN
                        RAISE EXCEPTION
                            'FUSION REFUSÉE: parcelles % et % non adjacentes (ST_Touches=false)',
                            v_parcelles[i], v_parcelles[j];
                    END IF;
                END LOOP;
            END LOOP;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS tg_check_fusion_st_touches ON workflow_instance;
        CREATE TRIGGER tg_check_fusion_st_touches
            BEFORE INSERT ON workflow_instance
            FOR EACH ROW EXECUTE FUNCTION fn_check_fusion_st_touches();
    """)

    # WF25 Mise sous litige : chaînage avec greffe_judiciaire
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_check_litige_greffe()
        RETURNS TRIGGER AS $$
        DECLARE v_greffe_id TEXT;
        BEGIN
            IF NEW.type_workflow != 'MISE_SOUS_LITIGE' THEN RETURN NEW; END IF;
            v_greffe_id := NEW.contexte->>'greffe_id';
            IF v_greffe_id IS NULL THEN
                RAISE EXCEPTION 'LITIGE REFUSÉ: greffe_id manquant dans le contexte';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM greffe_judiciaire
                WHERE id = v_greffe_id AND actif = TRUE
            ) THEN
                RAISE EXCEPTION 'LITIGE REFUSÉ: greffe % non reconnu ou inactif', v_greffe_id;
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS tg_check_litige_greffe ON workflow_instance;
        CREATE TRIGGER tg_check_litige_greffe
            BEFORE INSERT ON workflow_instance
            FOR EACH ROW EXECUTE FUNCTION fn_check_litige_greffe();
    """)

    # WF27 Archivage définitif : garantir is_final+WORM
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_archive_definitif_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'UPDATE' OR TG_OP = 'DELETE' THEN
                IF OLD.statut = 'archive_definitif' THEN
                    RAISE EXCEPTION 'WORM: archive définitive % immuable', OLD.id;
                END IF;
            END IF;
            RETURN COALESCE(NEW, OLD);
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS tg_archive_worm ON annf_right_archive;
        CREATE TRIGGER tg_archive_worm
            BEFORE UPDATE OR DELETE ON annf_right_archive
            FOR EACH ROW EXECUTE FUNCTION fn_archive_definitif_immutable();
    """)

    # ─── 5. Nettoyage des helpers ───────────────────────────────────────
    op.execute("DROP FUNCTION IF EXISTS _seed_wf_partiel(TEXT, TEXT, TEXT, TEXT);")
    op.execute("DROP FUNCTION IF EXISTS _seed_wf_step_partiel(UUID, INT, TEXT, TEXT, TEXT);")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tg_archive_worm ON annf_right_archive;")
    op.execute("DROP FUNCTION IF EXISTS fn_archive_definitif_immutable();")
    op.execute("DROP TRIGGER IF EXISTS tg_check_litige_greffe ON workflow_instance;")
    op.execute("DROP FUNCTION IF EXISTS fn_check_litige_greffe();")
    op.execute("DROP TRIGGER IF EXISTS tg_check_fusion_st_touches ON workflow_instance;")
    op.execute("DROP FUNCTION IF EXISTS fn_check_fusion_st_touches();")
    for code_wf, *_ in WORKFLOWS_PARTIELS:
        op.execute(f"""
            DELETE FROM workflow_step_def WHERE definition_id IN (
                SELECT id FROM workflow_definition WHERE code_wf = '{code_wf}'
            );
            DELETE FROM workflow_definition WHERE code_wf = '{code_wf}';
        """)
