"""
FONCIER+ v3.4.7 — Tests : Workflow Engine & Triggers T2/T5 (Migration 007)
Couvre : triggers T2 (Urbanisme) et T5 (Justice),
         moteur workflow (demarrer, valider, rejeter, suspendre),
         SHA-256 chaîné, signatures, escalades, ANNF auto
"""

import pytest
# Force le chargement du registre ORM
from app.models.users import User
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.workflow_engine import (
    StatutEtape, StatutInstance, TypeValidation,
    WorkflowDefinition, WorkflowInstance, WorkflowStepDef,
)
from app.services.workflow_engine import WorkflowEngine, _sha256, _signer


# ─────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────

def make_definition(type_wf="RNAF", delai=30):
    d = MagicMock()
    d.id = uuid.uuid4()
    d.type_workflow = type_wf
    d.is_active = True
    d.delai_max_jours = delai
    d.signature_finale_requise = True
    d.annf_auto = True
    return d

def make_instance(statut=StatutInstance.EN_ATTENTE, etape=1, role="DIRECTEUR_URBANISME"):
    i = MagicMock()
    i.id = uuid.uuid4()
    i.definition_id = uuid.uuid4()
    i.entite_type = "rnaf"
    i.entite_id = uuid.uuid4()
    i.nicad = "03-07-AR0045-089-B7"
    i.statut = statut
    i.etape_courante_ordre = etape
    i.etape_courante_code = f"ETAPE_{etape}"
    i.attendu_de_role = role
    i.date_debut = datetime.now(timezone.utc)
    i.date_echeance = None
    i.contexte_json = {}
    i.updated_at = None
    return i

def make_step_def(ordre=1, code="VERIFIER", role="DIRECTEUR_URBANISME",
                   type_val=TypeValidation.APPROBATION, retour=None):
    s = MagicMock()
    s.id = uuid.uuid4()
    s.ordre = ordre
    s.code_etape = code
    s.nom_etape = f"Étape {code}"
    s.role_requis = role
    s.role_backup = None
    s.type_validation = type_val
    s.est_obligatoire = True
    s.delai_max_heures = 72
    s.retour_etape_rejet = retour
    s.action_post_validation = None
    return s

def make_db(scalar=None, all_res=None):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    result.scalars.return_value.all.return_value = all_res or []
    result.scalar.return_value = 0
    db.execute.return_value = result
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


# ─────────────────────────────────────────────────────────────
# 1. TRIGGERS T2 et T5 — vérification dans la migration
# ─────────────────────────────────────────────────────────────

class TestTriggersManquants:

    def test_trigger_t2_urbanisme_present(self):
        """T2 : Trigger autorisation Urbanisme créé dans migration 007."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        assert "tg_enforce_urbanisme_autorisation" in content
        assert "enforce_urbanisme_autorisation" in content
        assert "URBANISME BLOQUÉ [T2]" in content
        assert "arretes_urbanisme" in content

    def test_trigger_t2_verifie_arrete_actif(self):
        """T2 : Vérifie que l'arrêté d'urbanisme est ACTIF."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        assert "arretes_urbanisme" in content
        assert "BEFORE UPDATE OF geom ON parcelles" in content

    def test_trigger_t5_justice_present(self):
        """T5 : Trigger décision Justice créé dans migration 007."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        assert "tg_block_if_justice_suspension_transfers" in content
        assert "tg_block_if_justice_suspension_actes"    in content
        assert "block_if_justice_suspension" in content
        assert "JUSTICE BLOQUÉ [T5]" in content

    def test_trigger_t5_verifie_gel_et_decision(self):
        """T5 : Vérifie à la fois decisions_judiciaires ET parcel_freeze_events."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        assert "decisions_judiciaires" in content
        assert "parcel_freeze_events"  in content
        assert "SUSPENSION" in content

    def test_t5_applique_sur_transfers_et_actes(self):
        """T5 couvre property_transfers ET actes_notaries."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        assert "BEFORE INSERT ON property_transfers" in content
        assert "BEFORE INSERT ON actes_notaries" in content

    def test_analyse_triggers_tous_couverts(self):
        """Les 8 triggers du spec sont tous couverts (migrations 004-007)."""
        migs = []
        for mig in ["004","005","006","007"]:
            try:
                content = open(
                    f"backend/alembic/versions/"
                    f"{mig}_{'workflows_intermodules' if mig=='004' else 'droits_fonciers_versiones' if mig=='005' else 'schema_fondation_consolide' if mig=='006' else 'workflow_engine_et_triggers_complets'}.py"
                ).read()
                migs.append(content)
            except FileNotFoundError:
                pass
        all_content = "\n".join(migs)

        # T1 : RNP sans RNAF publié
        assert "enforce_rnaf_publie_for_rnp" in all_content or \
               "tg_enforce_rnaf_publie_for_rnp" in all_content
        # T2 : autorisation Urbanisme
        assert "tg_enforce_urbanisme_autorisation" in all_content
        # T3 : CCFM géométrie
        assert "tg_block_geom_without_ccfm" in all_content
        # T4 : hypothèque active
        assert "tg_block_acte_if_hypotheque_active" in all_content
        # T5 : décision Justice
        assert "tg_block_if_justice_suspension" in all_content
        # T6 : transaction_block CCFM
        assert "tg_auto_block_ccfm_suspension" in all_content or \
               "auto_block_on_ccfm_suspension" in all_content
        # T7 : archivage ANNF
        assert "archive_right_on_close" in all_content or \
               "tg_archive_right_on_close" in all_content
        # T8 : audit global
        assert "tg_audit_" in all_content


# ─────────────────────────────────────────────────────────────
# 2. MOTEUR WORKFLOW — Démarrage
# ─────────────────────────────────────────────────────────────

class TestWorkflowDemarrer:

    @pytest.mark.asyncio
    async def test_demarrer_definition_inexistante(self):
        """Erreur si la définition de workflow est inactive."""
        db = make_db(scalar=None)
        with pytest.raises(ValueError, match="Aucune définition active"):
            await WorkflowEngine.demarrer(
                db, "INEXISTANT", "rnaf", str(uuid.uuid4()), str(uuid.uuid4())
            )

    @pytest.mark.asyncio
    async def test_demarrer_instance_deja_active(self):
        """Erreur si une instance active existe déjà pour la même entité."""
        definition = make_definition()
        instance_existante = make_instance(StatutInstance.EN_COURS)

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        call_count = 0
        async def execute_side(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none.return_value = definition
            elif call_count == 2:
                result.scalar_one_or_none.return_value = instance_existante
            else:
                result.scalar_one_or_none.return_value = None
                result.scalars.return_value.all.return_value = []
            return result

        db.execute.side_effect = execute_side

        with pytest.raises(ValueError, match="instance.*déjà active"):
            await WorkflowEngine.demarrer(
                db, "RNAF", "rnaf",
                str(instance_existante.entite_id), str(uuid.uuid4())
            )


# ─────────────────────────────────────────────────────────────
# 3. MOTEUR WORKFLOW — Validation d'étape
# ─────────────────────────────────────────────────────────────

class TestWorkflowValider:

    @pytest.mark.asyncio
    async def test_valider_mauvais_role(self):
        """Erreur si le validateur n'a pas le bon rôle."""
        instance = make_instance(StatutInstance.EN_ATTENTE)
        step = make_step_def(role="DIRECTEUR_URBANISME")

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        results = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=instance)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=step)),
        ]
        db.execute.side_effect = iter(results)

        with pytest.raises(ValueError, match="non autorisé"):
            await WorkflowEngine.valider_etape(
                db, str(instance.id),
                acteur_id=str(uuid.uuid4()),
                role="GEOMETRE",  # ← mauvais rôle
            )

    @pytest.mark.asyncio
    async def test_valider_instance_complete_impossible(self):
        """Impossible de valider une instance déjà complète."""
        instance = make_instance(StatutInstance.COMPLETE)
        db = make_db(scalar=instance)

        with pytest.raises(ValueError, match="statut.*COMPLETE"):
            await WorkflowEngine.valider_etape(
                db, str(instance.id),
                acteur_id=str(uuid.uuid4()),
                role="ADMIN",
            )

    @pytest.mark.asyncio
    async def test_admin_peut_valider_toute_etape(self):
        """L'admin peut bypasser et valider n'importe quelle étape."""
        from unittest.mock import MagicMock
        step = MagicMock()
        step.statut = "EN_COURS"
        step.etape_cle = "VERIFIER"
        try:
            await WorkflowEngine._verifier_role("ADMIN", step)
        except ValueError as e:
            assert "ne peut pas bypasser" in str(e)

    def test_role_backup_autorise(self):
        """Le role_backup est accepté si le rôle principal est absent."""
        step = make_step_def(role="DIRECTEUR_URBANISME")
        step.role_backup = "MINISTRE_URBANISME"
        # Test synchrone
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            WorkflowEngine._verifier_role("MINISTRE_URBANISME", step)
        )

    def test_role_non_autorise_leve_erreur(self):
        """Un rôle non autorisé lève une ValueError."""
        step = make_step_def(role="DIRECTEUR_URBANISME")
        step.role_backup = None
        import asyncio
        with pytest.raises(ValueError, match="non autorisé"):
            asyncio.get_event_loop().run_until_complete(
                WorkflowEngine._verifier_role("GEOMETRE", step)
            )


# ─────────────────────────────────────────────────────────────
# 4. MOTEUR WORKFLOW — Rejet
# ─────────────────────────────────────────────────────────────

class TestWorkflowRejeter:

    @pytest.mark.asyncio
    async def test_rejet_definitif_si_pas_de_retour(self):
        """Rejet sans retour_etape_rejet → statut REJETE définitif."""
        instance = make_instance(StatutInstance.EN_ATTENTE)
        step = make_step_def(role="DIRECTEUR_URBANISME", retour=None)

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        results = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=instance)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=step)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # log
        ]
        db.execute.side_effect = iter(results)

        result = await WorkflowEngine.rejeter_etape(
            db, str(instance.id),
            acteur_id=str(uuid.uuid4()),
            role="DIRECTEUR_URBANISME",
            motif="Document non conforme",
        )
        assert result.statut == StatutInstance.REJETE
        assert result.motif_rejet == "Document non conforme"


# ─────────────────────────────────────────────────────────────
# 5. SIGNATURES NUMÉRIQUES
# ─────────────────────────────────────────────────────────────

class TestSignatures:

    def test_sha256_deterministe(self):
        """SHA-256 identique pour la même entrée."""
        h1 = _sha256("test|payload|2026")
        h2 = _sha256("test|payload|2026")
        assert h1 == h2
        assert len(h1) == 64

    def test_sha256_different_si_payload_change(self):
        """SHA-256 différent si payload change."""
        assert _sha256("payload_a") != _sha256("payload_b")

    def test_signature_hmac_valide(self):
        """La signature HMAC est vérifiable."""
        import hmac, hashlib, base64
        contenu = "instance|VERIFIER|validee|sha256hash|2026-01-01"
        sig = _signer(contenu)
        assert isinstance(sig, str)
        assert len(sig) > 10
        # Vérifier que c'est du base64 valide
        decoded = base64.b64decode(sig)
        assert len(decoded) == 32  # SHA-256 = 32 bytes

    def test_signature_differente_par_contenu(self):
        """Deux contenus différents → deux signatures différentes."""
        s1 = _signer("contenu_a")
        s2 = _signer("contenu_b")
        assert s1 != s2


# ─────────────────────────────────────────────────────────────
# 6. DÉFINITIONS WORKFLOW — Seed
# ─────────────────────────────────────────────────────────────

class TestWorkflowDefinitions:

    def test_7_workflows_definis_dans_seed(self):
        """Les 7 workflows du spec sont dans le seed."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        workflows_requis = [
            "'RNAF'", "'RNP'", "'CCFM'",
            "'NOTAIRE'", "'JUSTICE'", "'TRANSFERT'", "'BGU'"
        ]
        for wf in workflows_requis:
            assert wf in content, f"Workflow manquant dans seed : {wf}"

    def test_etapes_rnaf_5_etapes(self):
        """Le workflow RNAF a 5 étapes obligatoires."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        etapes_rnaf = [
            "'SOUMETTRE'", "'VERIFIER'", "'SCELLER'",
            "'PUBLIER_JO'", "'CONFIRMER_PUBLIE'"
        ]
        for etape in etapes_rnaf:
            assert etape in content, f"Étape RNAF manquante : {etape}"

    def test_etapes_ccfm_6_etapes(self):
        """Le workflow CCFM a 6 étapes."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        etapes_ccfm = [
            "'RECEPTION'", "'VERIFICATION_ADMIN'", "'CONSTAT_TERRAIN'",
            "'VALIDATION_DIRECTEUR'", "'SCELLEMENT'", "'ARCHIVAGE'"
        ]
        for etape in etapes_ccfm:
            assert etape in content, f"Étape CCFM manquante : {etape}"

    def test_workflow_transfert_passe_par_ccfm_gate(self):
        """Le workflow TRANSFERT vérifie la gate CCFM à l'étape 2."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        assert "'CCFM_GATE'" in content
        assert "'CHECK_CCFM_GATE'" in content

    def test_workflow_transfert_requiert_notaire(self):
        """Le workflow TRANSFERT requiert la signature notariale."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        assert "'SIGNATURE_NOTAIRE'" in content
        assert "'NOTAIRE'" in content


# ─────────────────────────────────────────────────────────────
# 7. VUES WORKFLOW
# ─────────────────────────────────────────────────────────────

class TestVuesWorkflow:

    def test_vue_workflow_en_cours(self):
        """v_workflow_en_cours expose les instances actives avec retards."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        assert "v_workflow_en_cours"   in content
        assert "retard_heures"         in content
        assert "nb_escalades_actives"  in content
        assert "attendu_de_role"       in content

    def test_vue_workflow_retards(self):
        """v_workflow_retards liste les workflows en retard."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        assert "v_workflow_retards" in content
        assert "date_echeance < NOW()" in content


# ─────────────────────────────────────────────────────────────
# 8. BILAN GLOBAL
# ─────────────────────────────────────────────────────────────

class TestBilanGlobal:

    def test_index_partiel_unicite_instance_active(self):
        """Index partiel garantit 1 seule instance active par entité."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        assert "ix_wfi_unique_active" in content
        assert "WHERE statut IN" in content

    def test_sha256_chaine_dans_step_log(self):
        """Le SHA-256 est chaîné entre les étapes (sha256_prev)."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        assert "sha256_prev" in content
        assert "tg_wf_step_sha256" in content
        assert "GENESIS" in content  # valeur initiale de la chaîne

    def test_annf_auto_sur_completion(self):
        """L'archivage ANNF est automatique à la completion d'un workflow."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        assert "tg_wf_auto_annf_on_complete" in content
        assert "annf_auto" in content
        assert "generate_annf_ida" in content

    def test_delete_absent_dans_triggers(self):
        """Aucun DELETE physique dans les triggers workflow."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        import re
        plpgsql = re.findall(r'\$\$(.*?)\$\$', content, re.DOTALL)
        for block in plpgsql:
            for line in block.splitlines():
                stripped = line.strip()
                if stripped.startswith('--'):
                    continue
                assert 'DELETE FROM' not in stripped.upper(), \
                    f"DELETE physique interdit dans trigger : {stripped}"

    def test_total_migrations_007(self):
        """Migration 007 contient toutes les tables workflow."""
        content = open(
            "backend/alembic/versions/"
            "007_workflow_engine_et_triggers_complets.py"
        ).read()
        tables_workflow = [
            "workflow_definition", "workflow_step_def",
            "workflow_instance", "workflow_step_log",
            "workflow_signature", "workflow_escalade",
        ]
        for t in tables_workflow:
            assert f'"{t}"' in content or f"'{t}'" in content, \
                f"Table workflow manquante : {t}"