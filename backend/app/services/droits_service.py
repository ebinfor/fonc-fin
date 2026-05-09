"""
FONCIER+ v3.4.7 — DroitsFonciersService
Versioning des droits de propriété.

Principe fondamental :
  UPDATE proprietaire_id → INTERDIT.
  Tout changement = nouvelle version de droit.
  La somme des pourcentages de propriété ACTIFS = 100% (trigger DB).
"""

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select, and_, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.droits_fonciers import (
    ANNFRightArchive, MortgageRegistry, NotaryRegistry,
    OrigineOperation, ParcelDispute, ParcelRightVersion,
    ParcelServitude, PropertyTransfer, RightHolder,
    SourceArchiveDroit, StatutDroit, StatutHypotheque,
    TypeDroit, TypeTransfert,
)
from app.models.workflows import TransactionBlock, TypeBlockage, StatutBlockage


# ─────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────

TOLERANCE_SOMME_PCT = 0.001  # BUG-SVC-06 fix : 0.001% max (Code foncier nigérien)
TYPES_NEEDING_NOTAIRE = {TypeTransfert.VENTE, TypeTransfert.DONATION, TypeTransfert.CESSION}


# ─────────────────────────────────────────────────────────────
# DROIT VERSION SERVICE
# ─────────────────────────────────────────────────────────────

class DroitVersionService:
    """
    Gestion des versions de droits fonciers.
    RÈGLE CENTRALE : jamais d'UPDATE sur un droit existant.
    Toute modification = nouvelle version.
    """

    @staticmethod
    def compute_sha256(
        rnp_parcelle_id: str,
        titulaire_id: str,
        type_droit: str,
        pourcentage: float,
        date_debut: datetime,
        origine: str,
        timestamp: datetime,
    ) -> str:
        payload = (
            f"{rnp_parcelle_id}|{titulaire_id}|{type_droit}|"
            f"{pourcentage:.2f}|{date_debut.isoformat()}|{origine}|{timestamp.isoformat()}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    async def get_droits_actifs(
        db: AsyncSession,
        rnp_parcelle_id: str,
        type_droit: Optional[TypeDroit] = None,
    ) -> List[ParcelRightVersion]:
        """Retourne tous les droits actifs d'une parcelle."""
        filters = [
            ParcelRightVersion.rnp_parcelle_id == rnp_parcelle_id,
            ParcelRightVersion.statut == StatutDroit.ACTIF,
        ]
        if type_droit:
            filters.append(ParcelRightVersion.type_droit == type_droit)

        result = await db.execute(
            select(ParcelRightVersion).where(and_(*filters))
            .order_by(ParcelRightVersion.pourcentage_droit.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def verifier_somme_propriete(
        db: AsyncSession,
        rnp_parcelle_id: str,
        nouveau_pourcentage: float,
        exclure_id: Optional[str] = None,
    ) -> Tuple[bool, float]:
        """
        Vérifie que la somme des droits de propriété ACTIFS ne dépasse pas 100%.
        Retourne (valide, somme_actuelle).
        """
        filters = [
            ParcelRightVersion.rnp_parcelle_id == rnp_parcelle_id,
            ParcelRightVersion.type_droit == TypeDroit.PROPRIETE,
            ParcelRightVersion.statut == StatutDroit.ACTIF,
        ]
        if exclure_id:
            filters.append(ParcelRightVersion.id != exclure_id)

        result = await db.execute(
            select(func.coalesce(func.sum(ParcelRightVersion.pourcentage_droit), 0))
            .where(and_(*filters))
        )
        somme_existante = float(result.scalar() or 0)
        somme_totale = somme_existante + nouveau_pourcentage
        return somme_totale <= (100.0 + TOLERANCE_SOMME_PCT), somme_totale

    @staticmethod
    async def creer_nouvelle_version(
        db: AsyncSession,
        rnp_parcelle_id: str,
        rnaf_id: str,
        titulaire_id: str,
        type_droit: TypeDroit,
        pourcentage: float,
        date_debut: datetime,
        origine: OrigineOperation,
        cree_par_id: str,
        parcelle_id: Optional[str] = None,
        acte_reference: Optional[str] = None,
        acte_notarie_id: Optional[str] = None,
        ccfm_validation_id: Optional[str] = None,
        certifie_par_notaire: Optional[str] = None,
        version_precedente_id: Optional[str] = None,
    ) -> ParcelRightVersion:
        """
        Crée une nouvelle version de droit.
        Ferme automatiquement la version précédente via trigger DB.
        Vérifie la somme = 100% pour les droits de propriété.
        """
        # Vérification somme pour droits de PROPRIETE
        if type_droit == TypeDroit.PROPRIETE:
            valide, somme = await DroitVersionService.verifier_somme_propriete(
                db, rnp_parcelle_id, pourcentage
            )
            if not valide:
                raise ValueError(
                    f"Somme des droits de propriété dépasserait {somme:.2f}% > 100%. "
                    f"Ajustez le pourcentage (max disponible : "
                    f"{100 - (somme - pourcentage):.2f}%)"
                )

        # BUG-SVC-07 fix : verrouiller la parcelle avant toute création
        # SELECT FOR UPDATE NOWAIT empêche deux appels concurrents de créer deux
        # versions actives simultanées sur la même (rnp_parcelle_id, titulaire, type_droit)
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        await db.execute(
            select(ParcelRightVersion.id)
            .where(ParcelRightVersion.rnp_parcelle_id == rnp_parcelle_id)
            .with_for_update(skip_locked=False)
            .limit(1)
        )

        # Numéro de version (après verrou posé)
        version_result = await db.execute(
            select(func.count(ParcelRightVersion.id)).where(
                and_(
                    ParcelRightVersion.rnp_parcelle_id == rnp_parcelle_id,
                    ParcelRightVersion.titulaire_id == titulaire_id,
                    ParcelRightVersion.type_droit == type_droit,
                )
            )
        )
        numero_version = (version_result.scalar() or 0) + 1

        now = datetime.now(timezone.utc)
        sha256 = DroitVersionService.compute_sha256(
            rnp_parcelle_id, titulaire_id, type_droit.value,
            pourcentage, date_debut, origine.value, now
        )

        version = ParcelRightVersion(
            id=uuid.uuid4(),
            rnp_parcelle_id=rnp_parcelle_id,
            parcelle_id=parcelle_id,
            rnaf_id=rnaf_id,
            titulaire_id=titulaire_id,
            type_droit=type_droit,
            pourcentage_droit=pourcentage,
            date_debut_validite=date_debut,
            date_fin_validite=None,   # Null = droit encore actif
            statut=StatutDroit.ACTIF,
            origine_operation=origine,
            acte_reference=acte_reference,
            acte_notarie_id=acte_notarie_id,
            ccfm_validation_id=ccfm_validation_id,
            certifie_par_notaire=certifie_par_notaire,
            numero_version=numero_version,
            version_precedente_id=version_precedente_id,
            sha256_version=sha256,
            cree_par=cree_par_id,
        )
        db.add(version)
        await db.flush()
        # Le trigger tg_close_previous_right ferme automatiquement la version précédente
        return version

    @staticmethod
    async def qui_possedait_a_la_date(
        db: AsyncSession,
        rnp_parcelle_id: str,
        date_query: date,
    ) -> List[dict]:
        """
        Répond à la question : "Qui possédait cette parcelle à la date X ?"
        Utilise la fonction SQL qui_possedait_parcelle() créée en migration 005.
        """
        result = await db.execute(
            text("SELECT * FROM qui_possedait_parcelle(:rnp_id, :date_query)"),
            {"rnp_id": rnp_parcelle_id, "date_query": date_query},
        )
        rows = result.mappings().all()
        return [
            {
                "titulaire_id": str(r["titulaire_id"]),
                "nom_titulaire": r["nom_titulaire"],
                "type_droit": r["type_droit"],
                "pourcentage_droit": float(r["pourcentage_droit"]),
                "date_debut_validite": r["date_debut_validite"].isoformat()
                    if r["date_debut_validite"] else None,
                "date_fin_validite": r["date_fin_validite"].isoformat()
                    if r["date_fin_validite"] else None,
                "acte_reference": r["acte_reference"],
                "sha256_version": r["sha256_version"],
            }
            for r in rows
        ]

    @staticmethod
    async def get_historique_complet(
        db: AsyncSession,
        rnp_parcelle_id: str,
    ) -> List[ParcelRightVersion]:
        """Historique complet de tous les droits (actifs + historiques + annulés)."""
        result = await db.execute(
            select(ParcelRightVersion)
            .where(ParcelRightVersion.rnp_parcelle_id == rnp_parcelle_id)
            .order_by(ParcelRightVersion.date_debut_validite.asc())
        )
        return result.scalars().all()


# ─────────────────────────────────────────────────────────────
# TRANSFER SERVICE
# ─────────────────────────────────────────────────────────────

class TransfertService:
    """
    Gestion des transferts de propriété.
    Chaque transfert est tracé sans exception.
    Vente, donation, héritage, cession, décision judiciaire.
    """

    @staticmethod
    async def effectuer_transfert(
        db: AsyncSession,
        rnp_parcelle_id: str,
        rnaf_id: str,
        ancien_titulaire_id: str,
        nouveau_titulaire_id: str,
        pourcentage_transfere: float,
        type_transfert: TypeTransfert,
        date_transfert: datetime,
        enregistre_par_id: str,
        acte_reference: Optional[str] = None,
        notaire_id: Optional[str] = None,
        acte_notarie_id: Optional[str] = None,
        ccfm_validation_id: Optional[str] = None,
        decision_judiciaire_id: Optional[str] = None,
        parcelle_id: Optional[str] = None,
    ) -> Tuple[PropertyTransfer, ParcelRightVersion]:
        """
        Effectue un transfert de propriété.
        1. Vérifie les pré-conditions (notaire, CCFM gate, etc.)
        2. Clôt le droit de l'ancien titulaire
        3. Crée le droit du nouveau titulaire
        4. Enregistre le transfert
        """
        # Pré-condition : notaire obligatoire pour vente/donation/cession
        if type_transfert in TYPES_NEEDING_NOTAIRE and not notaire_id:
            raise ValueError(
                f"Type de transfert {type_transfert.value} requiert un notaire agréé"
            )

        # Pré-condition : notaire doit être agréé
        if notaire_id:
            notaire_result = await db.execute(
                select(NotaryRegistry).where(
                    and_(
                        NotaryRegistry.id == notaire_id,
                        NotaryRegistry.statut_agrement == "agree",
                    )
                )
            )
            if not notaire_result.scalar_one_or_none():
                raise ValueError("Notaire introuvable ou non agréé")

        # Trouver le droit actif de l'ancien titulaire
        droit_result = await db.execute(
            select(ParcelRightVersion).where(
                and_(
                    ParcelRightVersion.rnp_parcelle_id == rnp_parcelle_id,
                    ParcelRightVersion.titulaire_id == ancien_titulaire_id,
                    ParcelRightVersion.type_droit == TypeDroit.PROPRIETE,
                    ParcelRightVersion.statut == StatutDroit.ACTIF,
                )
            )
        )
        ancien_droit = droit_result.scalar_one_or_none()
        if not ancien_droit:
            raise ValueError(
                f"Aucun droit de propriété actif trouvé pour le titulaire {ancien_titulaire_id}"
            )

        if float(ancien_droit.pourcentage_droit) < pourcentage_transfere - TOLERANCE_SOMME_PCT:
            raise ValueError(
                f"Titulaire ne possède que {ancien_droit.pourcentage_droit}% — "
                f"impossible de transférer {pourcentage_transfere}%"
            )

        # Créer le droit du nouveau titulaire
        nouveau_droit = await DroitVersionService.creer_nouvelle_version(
            db=db,
            rnp_parcelle_id=rnp_parcelle_id,
            rnaf_id=rnaf_id,
            titulaire_id=nouveau_titulaire_id,
            type_droit=TypeDroit.PROPRIETE,
            pourcentage=pourcentage_transfere,
            date_debut=date_transfert,
            origine=OrigineOperation(type_transfert.value)
                if type_transfert.value in OrigineOperation.__members__.values()
                else OrigineOperation.CESSION,
            cree_par_id=enregistre_par_id,
            parcelle_id=parcelle_id,
            acte_reference=acte_reference,
            acte_notarie_id=acte_notarie_id,
            ccfm_validation_id=ccfm_validation_id,
            certifie_par_notaire=notaire_id,
        )

        # Si transfert total : clôre le droit de l'ancien
        if abs(float(ancien_droit.pourcentage_droit) - pourcentage_transfere) < TOLERANCE_SOMME_PCT:
            ancien_droit.statut = StatutDroit.HISTORIQUE
            ancien_droit.date_fin_validite = date_transfert
        else:
            # Transfert partiel : réduire le pourcentage restant
            pct_restant = float(ancien_droit.pourcentage_droit) - pourcentage_transfere
            ancien_droit.statut = StatutDroit.HISTORIQUE
            ancien_droit.date_fin_validite = date_transfert
            # Créer une nouvelle version pour le solde restant
            await DroitVersionService.creer_nouvelle_version(
                db=db,
                rnp_parcelle_id=rnp_parcelle_id,
                rnaf_id=rnaf_id,
                titulaire_id=ancien_titulaire_id,
                type_droit=TypeDroit.PROPRIETE,
                pourcentage=pct_restant,
                date_debut=date_transfert,
                origine=OrigineOperation.CESSION,
                cree_par_id=enregistre_par_id,
                parcelle_id=parcelle_id,
                acte_reference=f"Solde après transfert partiel — {acte_reference}",
                version_precedente_id=str(ancien_droit.id),
            )

        # SHA-256 du transfert
        sha256_t = hashlib.sha256(
            f"{rnp_parcelle_id}|{ancien_titulaire_id}|{nouveau_titulaire_id}|"
            f"{pourcentage_transfere:.2f}|{type_transfert.value}|"
            f"{date_transfert.isoformat()}".encode()
        ).hexdigest()

        # Enregistrer le transfert
        transfert = PropertyTransfer(
            id=uuid.uuid4(),
            rnp_parcelle_id=rnp_parcelle_id,
            parcelle_id=parcelle_id,
            ancien_titulaire_id=ancien_titulaire_id,
            nouveau_titulaire_id=nouveau_titulaire_id,
            pourcentage_transfere=pourcentage_transfere,
            type_transfert=type_transfert,
            date_transfert=date_transfert,
            acte_reference=acte_reference,
            notaire_id=notaire_id,
            acte_notarie_id=acte_notarie_id,
            ccfm_validation_id=ccfm_validation_id,
            right_version_id=str(nouveau_droit.id),
            decision_judiciaire_id=decision_judiciaire_id,
            sha256_transfert=sha256_t,
            enregistre_par=enregistre_par_id,
        )
        db.add(transfert)
        await db.flush()
        return transfert, nouveau_droit
