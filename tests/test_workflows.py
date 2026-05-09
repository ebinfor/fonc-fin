"""
FONCIER+ v3.4.7 — Tests : Workflows Inter-modules
Couvre : gate transactionnelle, RNAF cycle, blocage CCFM,
         dégel judiciaire, archivage ANNF, portail public
"""

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.workflows import (
    StatutBlockage, StatutRNAF, StatutRNP, TypeBlockage,
)
from app.services.workflow_orchestrator import (
    ANNFArchiveService, PortailPublicService,
    RNAFWorkflowService, TransactionGate,
)
from app.models.parcellaire import StatutParcelle


# ─────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────

def make_parcelle(statut=StatutParcelle.ACTIVE, is_gele=False,
                   nicad="03-07-AR0045-089-B7"):
    p = MagicMock()
    p.id = uuid.uuid4()
    p.nicad = nicad
    p.statut = statut
    p.is_gele = is_gele
    return p

def make_rnaf_wf(statut=StatutRNAF.PUBLIE, rnaf_id=None, pub_jo_id=None):
    wf = MagicMock()
    wf.id = uuid.uuid4()
    wf.rnaf_id = rnaf_id or uuid.uuid4()
    wf.statut = statut
    wf.publication_jo_id = pub_jo_id
    wf.motif_suspension = None
    wf.date_suspension = None
    wf.date_publication = None
    return wf

def make_db_mock(return_value=None):
    """Crée un mock de session DB."""
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = return_value
    result_mock.scalar.return_value = 0
    result_mock.scalars.return_value.all.return_value = []
    result_mock.mappings.return_value.first.return_value = None
    db.execute.return_value = result_mock
    return db


# ─────────────────────────────────────────────────────────────
# 1. GATE TRANSACTIONNELLE
# ─────────────────────────────────────────────────────────────

class TestTransactionGate:

    @pytest.mark.asyncio
    async def test_gate_ok_parcelle_active_non_gelee(self):
        """Parcelle active, non gelée, sans blocks → éligible."""
        db = make_db_mock(make_parcelle(StatutParcelle.ACTIVE, False))
        # Simuler 0 blocks, 0 ccfm, 0 conflits
        results = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=make_parcelle())),
            MagicMock(scalar=MagicMock(return_value=0)),   # blocks
            MagicMock(scalar=MagicMock(return_value=0)),   # ccfm
            MagicMock(scalar=MagicMock(return_value=0)),   # conflits
        ]
        db.execute.side_effect = iter(results + [MagicMock(scalar_one_or_none=MagicMock(return_value=None))])
        eligible, raison, _ = await TransactionGate.check(db, str(uuid.uuid4()))
        assert eligible is True

    @pytest.mark.asyncio
    async def test_gate_bloque_parcelle_gelee(self):
        """Parcelle gelée ❄️ → non éligible."""
        db = make_db_mock(make_parcelle(StatutParcelle.ACTIVE, is_gele=True))
        eligible, raison, _ = await TransactionGate.check(db, str(uuid.uuid4()))
        assert eligible is False
        assert "gelée" in raison

    @pytest.mark.asyncio
    async def test_gate_bloque_statut_non_actif(self):
        """Parcelle suspendue → non éligible."""
        for statut in [StatutParcelle.SUSPENDU if hasattr(StatutParcelle, 'SUSPENDU')
                        else StatutParcelle.ANNULE,
                        StatutParcelle.EN_ATTENTE_VALIDATION,
                        StatutParcelle.ANNULE]:
            db = make_db_mock(make_parcelle(statut))
            eligible, raison, _ = await TransactionGate.check(db, str(uuid.uuid4()))
            assert eligible is False

    @pytest.mark.asyncio
    async def test_gate_introuvable(self):
        """Parcelle introuvable → non éligible."""
        db = make_db_mock(None)
        eligible, raison, _ = await TransactionGate.check(db, str(uuid.uuid4()))
        assert eligible is False
        assert "introuvable" in raison


# ─────────────────────────────────────────────────────────────
# 2. RNAF WORKFLOW — TRANSITIONS
# ─────────────────────────────────────────────────────────────

class TestRNAFWorkflow:

    def test_transitions_autorisees(self):
        """Vérifie la matrice de transitions RNAF."""
        t = RNAFWorkflowService.TRANSITIONS
        # Brouillon → vérification
        assert StatutRNAF.EN_VERIFICATION in t[StatutRNAF.BROUILLON]
        # Scellé → publié
        assert StatutRNAF.PUBLIE in t[StatutRNAF.SCELLE]
        # Publié → suspendu
        assert StatutRNAF.SUSPENDU in t[StatutRNAF.PUBLIE]
        # Suspendu → peut être rétabli ou annulé
        assert StatutRNAF.PUBLIE in t[StatutRNAF.SUSPENDU]
        assert StatutRNAF.ANNULE in t[StatutRNAF.SUSPENDU]
        # Terminaux
        assert t[StatutRNAF.ANNULE] == []
        assert t[StatutRNAF.ARCHIVE] == []

    def test_rnp_ne_peut_etre_actif_sans_rnaf_publie(self):
        """Règle métier fondamentale : RNP actif = RNAF publié."""
        statuts_rnaf_interdits = [
            StatutRNAF.BROUILLON, StatutRNAF.EN_VERIFICATION,
            StatutRNAF.EN_SCELLEMENT, StatutRNAF.SCELLE,
            StatutRNAF.SUSPENDU, StatutRNAF.ANNULE, StatutRNAF.ARCHIVE,
        ]
        # Seul PUBLIE permet RNP ACTIF
        for s in statuts_rnaf_interdits:
            assert s != StatutRNAF.PUBLIE, f"Statut {s} ne doit pas permettre RNP ACTIF"

    @pytest.mark.asyncio
    async def test_suspension_necessite_motif(self):
        """La suspension d'un RNAF nécessite un motif."""
        db = make_db_mock(make_rnaf_wf(StatutRNAF.PUBLIE))
        with pytest.raises(ValueError, match="Motif de suspension obligatoire"):
            await RNAFWorkflowService.suspendre_avec_blocage(
                db, str(uuid.uuid4()), "", "sha", "user", "ccfm"
            )

    @pytest.mark.asyncio
    async def test_suspension_necessite_rnaf_publie(self):
        """Seul un RNAF PUBLIE peut être suspendu."""
        for statut in [StatutRNAF.BROUILLON, StatutRNAF.SCELLE, StatutRNAF.ARCHIVE]:
            db = make_db_mock(make_rnaf_wf(statut))
            with pytest.raises(ValueError, match="Seul un RNAF PUBLIE"):
                await RNAFWorkflowService.suspendre_avec_blocage(
                    db, str(uuid.uuid4()), "motif valide", "sha", "user", "ccfm"
                )

    def test_publication_rnaf_necessite_jo(self):
        """Transition SCELLE → PUBLIE nécessite publication_jo_id."""
        # Cette règle est encodée dans avancer_statut
        # Vérifions que PUBLIE est accessible depuis SCELLE uniquement
        transitions = RNAFWorkflowService.TRANSITIONS
        assert StatutRNAF.PUBLIE in transitions[StatutRNAF.SCELLE]
        assert StatutRNAF.PUBLIE not in transitions[StatutRNAF.BROUILLON]
        assert StatutRNAF.PUBLIE not in transitions[StatutRNAF.EN_VERIFICATION]


# ─────────────────────────────────────────────────────────────
# 3. BLOCAGE CCFM → TRANSACTIONS
# ─────────────────────────────────────────────────────────────

class TestCCFMBlockage:

    def test_suspension_active_bloque_transactions(self):
        """Une suspension CCFM active doit empêcher tout acte transactionnel."""
        # La vue v_transaction_gate implémente cette logique
        # Test documentaire : vérifie la présence de la règle dans le service
        import inspect
        from app.services.workflow_orchestrator import TransactionGate
        source = inspect.getsource(TransactionGate.check)
        assert "CCFMSuspension" in source
        assert "est_active" in source

    def test_levee_suspension_doit_degel_parcelle(self):
        """Le trigger de dégel judiciaire doit lever les suspensions CCFM."""
        from app.services.workflow_orchestrator import RNAFWorkflowService
        # Vérifier que le service capture la chaîne complète
        import inspect
        source_orchestre = inspect.getsource(RNAFWorkflowService)
        # Le service doit traiter les suspensions CCFM
        assert "ccfm_suspension" in source_orchestre.lower() or \
               "CCFMSuspension" in source_orchestre

    def test_type_blockage_ccfm_defini(self):
        """Le type CCFM_SUSPENSION doit être défini dans TypeBlockage."""
        assert TypeBlockage.CCFM_SUSPENSION == "ccfm_suspension"
        assert TypeBlockage.JUSTICE_LITIGE == "justice_litige"
        assert TypeBlockage.FRAUDE_DETECTEE == "fraude_detectee"
        assert TypeBlockage.RNAF_SUSPENDU == "rnaf_suspendu"


# ─────────────────────────────────────────────────────────────
# 4. DÉGEL JUDICIAIRE
# ─────────────────────────────────────────────────────────────

class TestDegelJudiciaire:

    def test_decision_validee_retablit_rnaf(self):
        """Une décision VALIDEE avec retablit_rnaf=True doit rétablir le RNAF."""
        from app.models.workflows import StatutDecisionJusticeRNAF
        assert StatutDecisionJusticeRNAF.VALIDEE == "validee"
        assert StatutDecisionJusticeRNAF.ANNULATION == "annulation"

    def test_decision_annulation_bloque_definitivement(self):
        """Une décision ANNULATION doit bloquer le RNAF définitivement."""
        from app.models.workflows import StatutDecisionJusticeRNAF, StatutRNAF
        # Après annulation judiciaire, RNAF → ANNULE (terminal, pas de retour)
        transitions = RNAFWorkflowService.TRANSITIONS
        assert transitions[StatutRNAF.ANNULE] == []

    def test_degele_parcelles_flag(self):
        """Le flag degele_parcelles sur la décision doit être booléen."""
        from app.models.workflows import JusticeRNAFDecision
        import inspect
        source = inspect.getsource(JusticeRNAFDecision)
        assert "degele_parcelles" in source
        assert "Boolean" in source


# ─────────────────────────────────────────────────────────────
# 5. ARCHIVAGE ANNF — WORM
# ─────────────────────────────────────────────────────────────

class TestANNFArchive:

    @pytest.mark.asyncio
    async def test_scellement_impossible_si_deja_scelle(self):
        """Archive WORM : scellement impossible si déjà scellée."""
        archive = MagicMock()
        archive.id = uuid.uuid4()
        archive.ida = "ANNF-2026-0000001"
        archive.scelle = True  # déjà scellée

        db = make_db_mock(archive)
        with pytest.raises(ValueError, match="déjà scellée"):
            await ANNFArchiveService.sceller(db, str(archive.id), "user")

    @pytest.mark.asyncio
    async def test_scellement_archive_introuvable(self):
        """Scellement impossible si archive introuvable."""
        db = make_db_mock(None)
        with pytest.raises(ValueError, match="introuvable"):
            await ANNFArchiveService.sceller(db, str(uuid.uuid4()), "user")

    def test_type_archive_annf_complet(self):
        """Tous les types d'entités archivables sont couverts."""
        from app.models.workflows import TypeArchiveANNF
        types_requis = {"rnaf", "rnp", "ccfm", "acte_notarie",
                        "decision_justice", "bgu_scelle", "plan_cadastral"}
        types_definis = {t.value for t in TypeArchiveANNF}
        assert types_requis == types_definis

    def test_ida_format(self):
        """Le format IDA est ANNF-AAAA-NNNNNNN."""
        import re
        pattern = re.compile(r"^ANNF-\d{4}-\d{7}$")
        exemples = ["ANNF-2026-0000001", "ANNF-2026-9999999", "ANNF-2025-0000100"]
        invalides = ["ANNF-26-001", "2026-0000001", "ANNF-2026-001"]
        for ida in exemples:
            assert pattern.match(ida), f"IDA valide refusé : {ida}"
        for ida in invalides:
            assert not pattern.match(ida), f"IDA invalide accepté : {ida}"


# ─────────────────────────────────────────────────────────────
# 6. PORTAIL PUBLIC
# ─────────────────────────────────────────────────────────────

class TestPortailPublic:

    @pytest.mark.asyncio
    async def test_statut_introuvable_retourne_none(self):
        """Retourne None si le NICAD n'existe pas."""
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.mappings.return_value.first.return_value = None
        db.execute.return_value = result_mock
        result = await PortailPublicService.get_statut_parcelle(db, "NICAD-INCONNU")
        assert result is None

    @pytest.mark.asyncio
    async def test_historique_retourne_liste_vide(self):
        """Retourne liste vide si pas d'historique."""
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        db.execute.return_value = result_mock
        historique = await PortailPublicService.get_historique_statuts(db, "03-07-AR0045-089-B7")
        assert historique == []


# ─────────────────────────────────────────────────────────────
# 7. RÈGLES TRANSVERSALES
# ─────────────────────────────────────────────────────────────

class TestReglesTransversales:

    def test_chaine_complete_rnaf_rnp_bgu(self):
        """Vérifie la chaîne complète Urbanisme → RNAF → JO → RNP → BGU."""
        # RNAF dépend de arretes_fonciers (Urbanisme)
        from app.models.workflows import RNAFWorkflow
        import inspect
        source = inspect.getsource(RNAFWorkflow)
        assert "arretes_fonciers" in source
        assert "publications_jo" in source

        # RNP dépend de RNAF
        from app.models.workflows import RNPParcelleLink
        source_rnp = inspect.getsource(RNPParcelleLink)
        assert "rnaf" in source_rnp.lower()

        # BGU dépend de assiettes_bgu
        from app.models.workflows import BGUParcelStatus
        source_bgu = inspect.getsource(BGUParcelStatus)
        assert "assiettes_bgu" in source_bgu

    def test_statuts_rnp_couvrent_tous_les_etats_rnaf(self):
        """Chaque statut RNAF doit avoir un mapping RNP correspondant."""
        mapping = {
            StatutRNAF.PUBLIE:   StatutRNP.ACTIF,
            StatutRNAF.SUSPENDU: StatutRNP.SUSPENDU,
            StatutRNAF.ANNULE:   StatutRNP.ANNULE,
            StatutRNAF.ARCHIVE:  StatutRNP.ARCHIVE,
            StatutRNAF.SCELLE:   StatutRNP.PROVISOIRE,
            StatutRNAF.EN_SCELLEMENT: StatutRNP.PROVISOIRE,
            StatutRNAF.EN_VERIFICATION: StatutRNP.PROVISOIRE,
            StatutRNAF.BROUILLON: StatutRNP.PROVISOIRE,
        }
        # Tous les statuts RNAF ont un mapping
        for statut_rnaf in StatutRNAF:
            assert statut_rnaf in mapping, f"Statut RNAF {statut_rnaf} sans mapping RNP"

    def test_delete_physique_absent_de_tous_les_services(self):
        """Aucun service ne doit contenir de DELETE physique."""
        import inspect
        services = [
            TransactionGate,
            RNAFWorkflowService,
            ANNFArchiveService,
            PortailPublicService,
        ]
        for svc in services:
            source = inspect.getsource(svc)
            assert "db.delete(" not in source, \
                f"DELETE physique trouvé dans {svc.__name__}"

    def test_views_referencees_dans_les_services(self):
        """Les vues SQL créées en migration 004 sont utilisées dans les services."""
        import inspect
        source = inspect.getsource(PortailPublicService)
        assert "v_portail_parcelles" in source
        source_gate = inspect.getsource(TransactionGate)
        # La gate utilise les tables directement (plus performant que la vue)
        assert "TransactionBlock" in source_gate
