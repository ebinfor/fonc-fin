"""018_workflows_bugfixes

FONCIER+ v3.4.8 — Migration 018 : Correctifs des 8 bugs workflow
Applique les correctifs BUG-WF01 à BUG-WF08 identifiés lors de l'audit
du 2026-04-11 sur les workflows implémentés.

Revision ID: 018_workflows_bugfixes
Revises: 017_workflows_completion
Create Date: 2026-04-11
"""
from alembic import op

revision = "018_workflows_bugfixes"
down_revision = "017_workflows_completion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ═══════════════════════════════════════════════════════════════
    # BUG-WF01 — CCFM F8 orchestration manquante [MAJEUR]
    # ═══════════════════════════════════════════════════════════════
    op.execute("""
        CREATE OR REPLACE FUNCTION ccfm_f8_orchestrer(p_nus TEXT)
        RETURNS JSONB AS $$
        DECLARE
            v_bloquant BOOLEAN := FALSE;
            v_results JSONB := '[]'::jsonb;
            v_code TEXT;
            v_ok BOOLEAN;
        BEGIN
            FOREACH v_code IN ARRAY ARRAY['F1','F2','F3','F4','F5','F6','F7']
            LOOP
                EXECUTE format('SELECT ccfm_%s($1)', lower(v_code))
                    INTO v_ok USING p_nus;
                v_results := v_results || jsonb_build_object(
                    'code', v_code, 'ok', v_ok,
                    'bloquant', v_code IN ('F1','F2','F5','F7')
                );
                IF NOT v_ok AND v_code IN ('F1','F2','F5','F7') THEN
                    v_bloquant := TRUE;
                END IF;
            END LOOP;

            IF v_bloquant THEN
                RAISE EXCEPTION
                    'CCFM F8 REFUSÉ [NUS=%]: contrôle(s) bloquant(s) échoué(s). Détails: %',
                    p_nus, v_results::text
                    USING ERRCODE = 'P0001';
            END IF;

            UPDATE ccfm_demande
                SET f8_orchestrated_at = NOW(),
                    f_results_snapshot = v_results,
                    f8_hash = encode(digest(v_results::text, 'sha256'), 'hex')
                WHERE nus = p_nus;

            RETURN jsonb_build_object(
                'ok', TRUE,
                'nus', p_nus,
                'orchestrated_at', NOW(),
                'results', v_results
            );
        END; $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        ALTER TABLE ccfm_demande
            ADD COLUMN IF NOT EXISTS f8_orchestrated_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS f_results_snapshot JSONB,
            ADD COLUMN IF NOT EXISTS f8_hash TEXT;
    """)

    # ═══════════════════════════════════════════════════════════════
    # BUG-WF02 — Gate CCFM contournable sur TRANSFERT [CRITIQUE]
    # ═══════════════════════════════════════════════════════════════
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_block_transfert_si_charges()
        RETURNS TRIGGER AS $$
        DECLARE
            v_parcelle_id UUID;
            v_litiges INT;
            v_hypos INT;
            v_blocks INT;
        BEGIN
            IF NEW.type_workflow != 'TRANSFERT' THEN
                RETURN NEW;
            END IF;

            v_parcelle_id := (NEW.contexte->>'parcelle_id')::UUID;
            IF v_parcelle_id IS NULL THEN
                RAISE EXCEPTION 'TRANSFERT REFUSÉ: parcelle_id manquant';
            END IF;

            SELECT COUNT(*) INTO v_litiges FROM litiges
                WHERE parcelle_id = v_parcelle_id AND statut = 'en_cours';
            IF v_litiges > 0 THEN
                RAISE EXCEPTION 'TRANSFERT BLOQUÉ [WL5]: % litige(s) actif(s)', v_litiges;
            END IF;

            SELECT COUNT(*) INTO v_hypos FROM mortgage_registry
                WHERE parcelle_id = v_parcelle_id AND statut = 'actif';
            IF v_hypos > 0 THEN
                RAISE EXCEPTION 'TRANSFERT BLOQUÉ [WL5]: % hypothèque(s) active(s)', v_hypos;
            END IF;

            SELECT COUNT(*) INTO v_blocks FROM transaction_blocks
                WHERE parcelle_id = v_parcelle_id AND statut = 'actif';
            IF v_blocks > 0 THEN
                RAISE EXCEPTION 'TRANSFERT BLOQUÉ [WL5]: % blocage(s) transactionnel(s)', v_blocks;
            END IF;

            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS tg_block_transfert_si_charges ON workflow_instance;
        CREATE TRIGGER tg_block_transfert_si_charges
            BEFORE INSERT ON workflow_instance
            FOR EACH ROW EXECUTE FUNCTION fn_block_transfert_si_charges();
    """)

    # ═══════════════════════════════════════════════════════════════
    # BUG-WF03 — WF30.4 : RMS > 0.5m non bloquant [MAJEUR]
    # ═══════════════════════════════════════════════════════════════
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_check_rms_avant_vectorisation()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.etape != '30.5' THEN RETURN NEW; END IF;
            IF NOT EXISTS (
                SELECT 1 FROM migration_georeferencement
                WHERE migration_id = NEW.migration_id
                  AND rms_acceptable = TRUE
                  AND uniformisation_ok = TRUE
            ) THEN
                RAISE EXCEPTION
                    'WF30 BLOQUÉ [30.5]: étape 30.4 non validée (RMS > 0.5m ou SRID non uniformisé) '
                    'pour migration_id=%', NEW.migration_id;
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS tg_wf30_check_rms ON migration_etape_log;
        CREATE TRIGGER tg_wf30_check_rms
            BEFORE INSERT ON migration_etape_log
            FOR EACH ROW EXECUTE FUNCTION fn_check_rms_avant_vectorisation();
    """)

    # ═══════════════════════════════════════════════════════════════
    # BUG-WF04 — Annulation cascade sans verrou [CRITIQUE]
    # ═══════════════════════════════════════════════════════════════
    op.execute("""
        CREATE OR REPLACE FUNCTION appliquer_annulation_objet_safe(p_ann_id UUID)
        RETURNS JSONB AS $$
        DECLARE
            v_parcelle_id UUID;
            v_result JSONB;
        BEGIN
            -- Verrou pessimiste en premier sur le dossier d'annulation
            SELECT parcelle_id INTO v_parcelle_id
                FROM annulation_dossier WHERE id = p_ann_id FOR UPDATE;

            IF v_parcelle_id IS NULL THEN
                RAISE EXCEPTION 'Annulation % introuvable', p_ann_id;
            END IF;

            -- Verrouiller toutes les tables enfant logiques
            PERFORM 1 FROM parcelles WHERE id = v_parcelle_id FOR UPDATE;
            PERFORM 1 FROM parcel_right_version
                WHERE rnp_parcelle_id = v_parcelle_id FOR UPDATE;
            PERFORM 1 FROM property_transfers
                WHERE parcelle_id = v_parcelle_id FOR UPDATE;
            PERFORM 1 FROM mortgage_registry
                WHERE parcelle_id = v_parcelle_id FOR UPDATE;
            PERFORM 1 FROM ccfm_demande
                WHERE parcelle_id = v_parcelle_id FOR UPDATE;

            -- Appeler la fonction existante (maintenant safe car verrous posés)
            PERFORM appliquer_annulation_objet(p_ann_id);

            v_result := jsonb_build_object(
                'ok', TRUE,
                'parcelle_id', v_parcelle_id,
                'cascade_executed_at', NOW()
            );
            RETURN v_result;
        END; $$ LANGUAGE plpgsql;
    """)

    # ═══════════════════════════════════════════════════════════════
    # BUG-WF05 — RNAF : unicité commune+arrêté [MINEUR]
    # ═══════════════════════════════════════════════════════════════
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE rnaf_demandes
                ADD CONSTRAINT uq_rnaf_commune_arrete
                UNIQUE (commune_id, reference_arrete);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # ═══════════════════════════════════════════════════════════════
    # BUG-WF06 — CCFM sha256_chain sans F-results [MAJEUR]
    # ═══════════════════════════════════════════════════════════════
    op.execute("""
        CREATE OR REPLACE FUNCTION ccfm_signer_safe(
            p_nus TEXT, p_signataire_id UUID
        ) RETURNS TEXT AS $$
        DECLARE
            v_f_results JSONB;
            v_hash TEXT;
        BEGIN
            -- Exiger que F8 ait tourné avant signature
            SELECT f_results_snapshot INTO v_f_results
                FROM ccfm_demande WHERE nus = p_nus;

            IF v_f_results IS NULL THEN
                RAISE EXCEPTION
                    'CCFM SIGNATURE REFUSÉE [NUS=%]: F8 orchestration requise avant signature',
                    p_nus;
            END IF;

            v_hash := encode(digest(
                p_nus || p_signataire_id::text || NOW()::text || v_f_results::text,
                'sha256'
            ), 'hex');

            UPDATE ccfm_demande
                SET sha256_chain = v_hash,
                    signed_at = NOW(),
                    signataire_id = p_signataire_id,
                    statut = 'signe'
                WHERE nus = p_nus;

            RETURN v_hash;
        END; $$ LANGUAGE plpgsql;
    """)

    # ═══════════════════════════════════════════════════════════════
    # BUG-WF07 — ANNF chaînage inter-documents [MAJEUR]
    # ═══════════════════════════════════════════════════════════════
    op.execute("""
        ALTER TABLE annf_right_archive
            ADD COLUMN IF NOT EXISTS hash_precedent TEXT,
            ADD COLUMN IF NOT EXISTS module_origine TEXT;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_chainer_annf_archive()
        RETURNS TRIGGER AS $$
        DECLARE v_hash_prev TEXT;
        BEGIN
            SELECT sha256_document INTO v_hash_prev
                FROM annf_right_archive
                WHERE module_origine = NEW.module_origine
                  AND id != NEW.id
                ORDER BY created_at DESC LIMIT 1;

            NEW.hash_precedent := COALESCE(v_hash_prev, 'GENESIS_' || NEW.module_origine);
            NEW.sha256_document := encode(digest(
                COALESCE(NEW.contenu::text, '') || NEW.hash_precedent,
                'sha256'
            ), 'hex');
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS tg_chainer_annf ON annf_right_archive;
        CREATE TRIGGER tg_chainer_annf
            BEFORE INSERT ON annf_right_archive
            FOR EACH ROW EXECUTE FUNCTION fn_chainer_annf_archive();
    """)

    # ═══════════════════════════════════════════════════════════════
    # BUG-WF08 — Pas de dégel automatique après résolution litige [MAJEUR]
    # ═══════════════════════════════════════════════════════════════
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_conflit_degel_automatique()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.statut = 'resolu' AND OLD.statut = 'en_cours' THEN
                -- Ne dégeler QUE si aucun autre litige en cours ne couvre la parcelle
                IF NOT EXISTS (
                    SELECT 1 FROM litiges
                    WHERE parcelle_id = NEW.parcelle_id
                      AND statut = 'en_cours'
                      AND id != NEW.id
                ) THEN
                    UPDATE parcelles SET is_gele = FALSE
                        WHERE id = NEW.parcelle_id;
                END IF;
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS tg_conflit_degel_automatique ON litiges;
        CREATE TRIGGER tg_conflit_degel_automatique
            AFTER UPDATE OF statut ON litiges
            FOR EACH ROW EXECUTE FUNCTION fn_conflit_degel_automatique();
    """)


def downgrade() -> None:
    # Retirer dans l'ordre inverse
    op.execute("DROP TRIGGER IF EXISTS tg_conflit_degel_automatique ON litiges;")
    op.execute("DROP FUNCTION IF EXISTS fn_conflit_degel_automatique();")

    op.execute("DROP TRIGGER IF EXISTS tg_chainer_annf ON annf_right_archive;")
    op.execute("DROP FUNCTION IF EXISTS fn_chainer_annf_archive();")

    op.execute("DROP FUNCTION IF EXISTS ccfm_signer_safe(TEXT, UUID);")

    op.execute("""
        ALTER TABLE rnaf_demandes DROP CONSTRAINT IF EXISTS uq_rnaf_commune_arrete;
    """)

    op.execute("DROP FUNCTION IF EXISTS appliquer_annulation_objet_safe(UUID);")

    op.execute("DROP TRIGGER IF EXISTS tg_wf30_check_rms ON migration_etape_log;")
    op.execute("DROP FUNCTION IF EXISTS fn_check_rms_avant_vectorisation();")

    op.execute("DROP TRIGGER IF EXISTS tg_block_transfert_si_charges ON workflow_instance;")
    op.execute("DROP FUNCTION IF EXISTS fn_block_transfert_si_charges();")

    op.execute("DROP FUNCTION IF EXISTS ccfm_f8_orchestrer(TEXT);")
    op.execute("""
        ALTER TABLE ccfm_demande
            DROP COLUMN IF EXISTS f8_orchestrated_at,
            DROP COLUMN IF EXISTS f_results_snapshot,
            DROP COLUMN IF EXISTS f8_hash;
    """)
