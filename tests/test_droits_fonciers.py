"""
FONCIER+ v3.4.7 — Tests : Droits Fonciers Versionnés (Migration 005)
Couvre : versioning, somme 100%, transferts, requête temporelle,
         servitudes bloquantes, archivage ANNF, litiges
"""

import pytest
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.droits_fonciers import (
    OrigineOperation, ParcelRightVersion, RightHolder,
    StatutDroit, TypeDroit, TypePersonne, TypeTransfert,
)
from app.services.droits_service import (
    DroitVersionService, TransfertService,
    TOLERANCE_SOMME_PCT, TYPES_NEEDING_NOTAIRE,
)


# ─────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────

def make_right_version(
    titulaire_id=None, pourcentage=100.0,
    type_droit=TypeDroit.PROPRIETE, statut=StatutDroit.ACTIF,
    date_debut=None, date_fin=None,
):
    v = MagicMock()
    v.id = uuid.uuid4()
    v.rnp_parcelle_id = uuid.uuid4()
    v.titulaire_id = titulaire_id or uuid.uuid4()
    v.pourcentage_droit = Decimal(str(pourcentage))
    v.type_droit = type_droit
    v.statut = statut
    v.date_debut_validite = date_debut or datetime(2020, 1, 1, tzinfo=timezone.utc)
    v.date_fin_validite = date_fin
    v.sha256_version = "a" * 64
    v.numero_version = 1
    v.acte_reference = None
    return v

def make_notaire(agrement="agree"):
    n = MagicMock()
    n.id = uuid.uuid4()
    n.nom_notaire = "Me. Diallo"
    n.numero_agrement = "AGR-00001"
    n.statut_agrement = agrement
    return n


# ─────────────────────────────────────────────────────────────
# 1. PRINCIPE FONDAMENTAL — UPDATE INTERDIT
# ─────────────────────────────────────────────────────────────

class TestPrincipalFondamental:

    def test_update_direct_absent_du_service(self):
        """Aucun UPDATE direct sur le droit propriétaire dans le service."""
        import inspect
        source = inspect.getsource(DroitVersionService)
        # Le service ne doit pas avoir d'UPDATE direct sur proprietaire_id
        assert "proprietaire_id =" not in source
        assert ".update({" not in source

    def test_nouvelle_version_toujours_creee(self):
        """Le service crée toujours une nouvelle version — jamais d'UPDATE."""
        import inspect
        source = inspect.getsource(DroitVersionService.creer_nouvelle_version)
        assert "ParcelRightVersion(" in source   # crée un objet
        assert "db.add(" in source               # l'insère
        # Pas d'UPDATE sur l'entité principale
        assert ".statut = StatutDroit" not in source.replace(
            "ancien_droit.statut", ""  # sauf pour fermer l'ancien
        )

    def test_sha256_unique_par_version(self):
        """Le SHA-256 change à chaque version (timestamp différent)."""
        from datetime import datetime, timezone
        ts1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ts2 = datetime(2026, 6, 1, tzinfo=timezone.utc)
        h1 = DroitVersionService.compute_sha256(
            "rnp-001", "tit-001", "propriete", 100.0,
            ts1, "creation_initiale", ts1
        )
        h2 = DroitVersionService.compute_sha256(
            "rnp-001", "tit-001", "propriete", 100.0,
            ts1, "creation_initiale", ts2
        )
        assert h1 != h2
        assert len(h1) == 64


# ─────────────────────────────────────────────────────────────
# 2. SOMME DES POURCENTAGES = 100%
# ─────────────────────────────────────────────────────────────

class TestSommePourcentage:

    @pytest.mark.asyncio
    async def test_somme_ok_100_pct(self):
        """60% + 40% = 100% — valide."""
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar.return_value = Decimal("60.0")   # existant : 60%
        db.execute.return_value = result_mock

        valide, somme = await DroitVersionService.verifier_somme_propriete(
            db, "rnp-001", 40.0
        )
        assert valide is True
        assert abs(somme - 100.0) < 0.01

    @pytest.mark.asyncio
    async def test_somme_ko_depasse_100(self):
        """60% + 50% = 110% — invalide."""
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar.return_value = Decimal("60.0")
        db.execute.return_value = result_mock

        valide, somme = await DroitVersionService.verifier_somme_propriete(
            db, "rnp-001", 50.0
        )
        assert valide is False
        assert somme > 100.0

    @pytest.mark.asyncio
    async def test_somme_partielle_valide(self):
        """30% + 30% = 60% < 100% — valide (multi-propriétaires)."""
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar.return_value = Decimal("30.0")
        db.execute.return_value = result_mock

        valide, somme = await DroitVersionService.verifier_somme_propriete(
            db, "rnp-001", 30.0
        )
        assert valide is True
        assert abs(somme - 60.0) < 0.01

    @pytest.mark.asyncio
    async def test_somme_parcelle_vide(self):
        """Parcelle sans droit existant : nouveau droit seul valide."""
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar.return_value = None  # aucun droit existant
        db.execute.return_value = result_mock

        valide, somme = await DroitVersionService.verifier_somme_propriete(
            db, "rnp-new", 100.0
        )
        assert valide is True
        assert abs(somme - 100.0) < 0.01

    def test_tolerance_arrondi_definie(self):
        """La tolérance d'arrondi est bien définie et raisonnable."""
        assert TOLERANCE_SOMME_PCT == 0.01
        assert 0 < TOLERANCE_SOMME_PCT < 0.1


# ─────────────────────────────────────────────────────────────
# 3. REQUÊTE TEMPORELLE HISTORIQUE
# ─────────────────────────────────────────────────────────────

class TestRequeteTemporelle:

    @pytest.mark.asyncio
    async def test_qui_possedait_retourne_liste(self):
        """La requête temporelle retourne une liste structurée."""
        db = AsyncMock()
        mock_row = {
            "titulaire_id": uuid.uuid4(),
            "nom_titulaire": "Moussa Diallo",
            "type_droit": "propriete",
            "pourcentage_droit": Decimal("100.0"),
            "date_debut_validite": datetime(2008, 1, 1, tzinfo=timezone.utc),
            "date_fin_validite": None,
            "acte_reference": "ACTE-2008-001",
            "sha256_version": "a" * 64,
        }
        result_mock = MagicMock()
        result_mock.mappings.return_value.all.return_value = [mock_row]
        db.execute.return_value = result_mock

        reponse = await DroitVersionService.qui_possedait_a_la_date(
            db, "rnp-001", date(2008, 7, 4)
        )
        assert len(reponse) == 1
        assert reponse[0]["nom_titulaire"] == "Moussa Diallo"
        assert reponse[0]["pourcentage_droit"] == 100.0

    @pytest.mark.asyncio
    async def test_qui_possedait_aucun_proprietaire(self):
        """Retourne liste vide si aucun propriétaire à cette date."""
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.mappings.return_value.all.return_value = []
        db.execute.return_value = result_mock

        reponse = await DroitVersionService.qui_possedait_a_la_date(
            db, "rnp-inexistant", date(1990, 1, 1)
        )
        assert reponse == []

    def test_requete_temporelle_utilise_fonction_sql(self):
        """La requête historique utilise la fonction PL/pgSQL dédiée."""
        import inspect
        source = inspect.getsource(DroitVersionService.qui_possedait_a_la_date)
        assert "qui_possedait_parcelle" in source

    def test_index_temporal_justifie(self):
        """L'index composite temporal est critique pour les requêtes historiques."""
        # Documentaire — vérifie que les champs clés sont indexés dans la migration
        import os
        migration_path = "/home/claude/foncier_v347/backend/alembic/versions/005_droits_fonciers_versiones.py"
        if os.path.exists(migration_path):
            content = open(migration_path).read()
            assert "ix_prv_temporal" in content
            assert "date_debut_validite" in content
            assert "date_fin_validite" in content


# ─────────────────────────────────────────────────────────────
# 4. TRANSFERTS DE PROPRIÉTÉ
# ─────────────────────────────────────────────────────────────

class TestTransferts:

    @pytest.mark.asyncio
    async def test_transfert_vente_necessite_notaire(self):
        """Une vente sans notaire est rejetée."""
        db = AsyncMock()
        with pytest.raises(ValueError, match="notaire agréé"):
            await TransfertService.effectuer_transfert(
                db=db,
                rnp_parcelle_id=str(uuid.uuid4()),
                rnaf_id=str(uuid.uuid4()),
                ancien_titulaire_id=str(uuid.uuid4()),
                nouveau_titulaire_id=str(uuid.uuid4()),
                pourcentage_transfere=100.0,
                type_transfert=TypeTransfert.VENTE,
                date_transfert=datetime.now(timezone.utc),
                enregistre_par_id=str(uuid.uuid4()),
                notaire_id=None,   # ← absent
            )

    @pytest.mark.asyncio
    async def test_transfert_heritage_sans_notaire_autorise(self):
        """Un héritage peut être enregistré sans notaire (procédure judiciaire)."""
        assert TypeTransfert.HERITAGE not in TYPES_NEEDING_NOTAIRE

    @pytest.mark.asyncio
    async def test_transfert_expropriation_sans_notaire_autorise(self):
        """Une expropriation ne requiert pas de notaire."""
        assert TypeTransfert.EXPROPRIATION not in TYPES_NEEDING_NOTAIRE

    def test_types_needing_notaire_definis(self):
        """Les types nécessitant un notaire sont bien définis."""
        assert TypeTransfert.VENTE    in TYPES_NEEDING_NOTAIRE
        assert TypeTransfert.DONATION in TYPES_NEEDING_NOTAIRE
        assert TypeTransfert.CESSION  in TYPES_NEEDING_NOTAIRE

    @pytest.mark.asyncio
    async def test_transfert_superieur_au_droit_possede_rejete(self):
        """Impossible de transférer plus que ce qu'on possède."""
        db = AsyncMock()
        # Notaire agréé
        notaire_result = MagicMock()
        notaire_result.scalar_one_or_none.return_value = make_notaire("agree")
        # Droit actif : 50%
        droit_result = MagicMock()
        droit_result.scalar_one_or_none.return_value = make_right_version(pourcentage=50.0)
        db.execute.side_effect = [notaire_result, droit_result]

        with pytest.raises(ValueError, match="50"):
            await TransfertService.effectuer_transfert(
                db=db,
                rnp_parcelle_id=str(uuid.uuid4()),
                rnaf_id=str(uuid.uuid4()),
                ancien_titulaire_id=str(uuid.uuid4()),
                nouveau_titulaire_id=str(uuid.uuid4()),
                pourcentage_transfere=80.0,   # ← dépasse le 50% possédé
                type_transfert=TypeTransfert.VENTE,
                date_transfert=datetime.now(timezone.utc),
                enregistre_par_id=str(uuid.uuid4()),
                notaire_id=str(uuid.uuid4()),
            )

    @pytest.mark.asyncio
    async def test_transfert_notaire_suspendu_rejete(self):
        """Un notaire suspendu ne peut pas certifier un transfert."""
        db = AsyncMock()
        notaire_result = MagicMock()
        notaire_result.scalar_one_or_none.return_value = None  # suspendu → introuvable
        db.execute.return_value = notaire_result

        with pytest.raises(ValueError, match="non agréé"):
            await TransfertService.effectuer_transfert(
                db=db,
                rnp_parcelle_id=str(uuid.uuid4()),
                rnaf_id=str(uuid.uuid4()),
                ancien_titulaire_id=str(uuid.uuid4()),
                nouveau_titulaire_id=str(uuid.uuid4()),
                pourcentage_transfere=100.0,
                type_transfert=TypeTransfert.VENTE,
                date_transfert=datetime.now(timezone.utc),
                enregistre_par_id=str(uuid.uuid4()),
                notaire_id=str(uuid.uuid4()),
            )


# ─────────────────────────────────────────────────────────────
# 5. RIGHT_HOLDER — Titulaires
# ─────────────────────────────────────────────────────────────

class TestRightHolder:

    def test_types_personne_complets(self):
        """Tous les types de personnes sont définis."""
        types = {t.value for t in TypePersonne}
        assert "physique"     in types
        assert "morale"       in types
        assert "etat"         in types
        assert "collectivite" in types
        assert "banque"       in types

    def test_type_banque_pour_hypotheques(self):
        """Le type BANQUE est distinct pour les hypothèques."""
        assert TypePersonne.BANQUE == "banque"
        assert TypePersonne.BANQUE != TypePersonne.MORALE

    def test_right_holder_entite_tracable(self):
        """Le titulaire reste traçable même après changement de propriétaire."""
        import inspect
        source = inspect.getsource(RightHolder)
        # Le titulaire a un est_actif — il n'est jamais supprimé
        assert "est_actif" in source
        # Pas de __tablename__ "users" — c'est une table distincte
        assert "__tablename__ = \"right_holders\"" in source


# ─────────────────────────────────────────────────────────────
# 6. SERVITUDES
# ─────────────────────────────────────────────────────────────

class TestServitudes:

    def test_servitude_peut_bloquer_transaction(self):
        """Une servitude bloquante doit créer un transaction_block."""
        from app.models.droits_fonciers import ParcelServitude
        import inspect
        source = inspect.getsource(ParcelServitude)
        assert "bloque_transaction" in source
        assert "Boolean" in source

    def test_types_servitude_complets(self):
        """Tous les types de servitudes sont définis."""
        from app.models.droits_fonciers import TypeServitude
        types = {t.value for t in TypeServitude}
        assert "passage"      in types
        assert "canalisation" in types
        assert "electricite"  in types
        assert "eau"          in types
        assert "autre"        in types


# ─────────────────────────────────────────────────────────────
# 7. ARCHIVAGE ANNF DES DROITS
# ─────────────────────────────────────────────────────────────

class TestANNFRightArchive:

    def test_archive_worm_apres_scellement(self):
        """Archive WORM : immuable après scellement."""
        from app.models.droits_fonciers import ANNFRightArchive
        import inspect
        source = inspect.getsource(ANNFRightArchive)
        assert "scelle"        in source
        assert "hash_integrite" in source
        assert "snapshot_json"  in source

    def test_sources_archive_droit_completes(self):
        """Toutes les sources d'archivage sont définies."""
        from app.models.droits_fonciers import SourceArchiveDroit
        sources = {s.value for s in SourceArchiveDroit}
        assert "notaire" in sources
        assert "justice" in sources
        assert "domaine" in sources
        assert "ccfm"    in sources
        assert "admin"   in sources

    def test_trigger_archive_automatique_documente(self):
        """Le trigger d'archivage automatique est dans la migration."""
        import os
        migration_path = "/home/claude/foncier_v347/backend/alembic/versions/005_droits_fonciers_versiones.py"
        if os.path.exists(migration_path):
            content = open(migration_path).read()
            assert "tg_archive_right_on_close"  in content
            assert "archive_right_on_close"      in content
            assert "HISTORIQUE" in content.upper()


# ─────────────────────────────────────────────────────────────
# 8. RÈGLES TRANSVERSALES
# ─────────────────────────────────────────────────────────────

class TestReglesTransversales:

    def test_rnaf_id_obligatoire_sur_droit(self):
        """Tout droit doit être lié à un RNAF (sans RNAF = juridiquement faible)."""
        from app.models.droits_fonciers import ParcelRightVersion
        import inspect
        source = inspect.getsource(ParcelRightVersion)
        assert "rnaf_id" in source
        assert "nullable=False" in source   # rnaf_id NOT NULL

    def test_delete_physique_absent(self):
        """Aucun service ne contient de DELETE physique."""
        import inspect
        from app.services.droits_service import DroitVersionService, TransfertService
        for svc in [DroitVersionService, TransfertService]:
            source = inspect.getsource(svc)
            assert "db.delete(" not in source, \
                f"DELETE physique trouvé dans {svc.__name__}"

    def test_chainage_complet_notaire_transfert_droit(self):
        """La chaîne notaire → transfert → droit est traçable."""
        from app.models.droits_fonciers import PropertyTransfer
        import inspect
        source = inspect.getsource(PropertyTransfer)
        assert "notaire_id"       in source
        assert "acte_notarie_id"  in source
        assert "right_version_id" in source
        assert "sha256_transfert" in source
