"""
FONCIER+ v3.4.7 — WorkflowOrchestrator
Service central qui orchestre les dépendances inter-modules :
  RNAF → RNP → BGU → CCFM → Justice → Notaire/Domaine → ANNF
"""

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflows import (
    ANNFArchiveLink, BGUParcelStatus, CCFMSuspension, JusticeRNAFDecision,
    ParcelFreezeEvent, ParcelPublicHistory, RNAFWorkflow, RNPParcelleLink,
    StatutBlockage, StatutDecisionJusticeRNAF, StatutRNAF, StatutRNP,
    TransactionBlock, TypeArchiveANNF, TypeBlockage,
)
from app.models.parcellaire import (
    ConflitParcellaire, GraviteConflit, Parcelle, StatutConflit, StatutParcelle,
)


# ─────────────────────────────────────────────────────────────
# GATE TRANSACTIONNELLE
# Requise par : Notaire, Banque, Domaine
# ─────────────────────────────────────────────────────────────

class TransactionGate:
    """
    Vérifie qu'une parcelle est éligible à une transaction.
    Contrôles cumulatifs dans l'ordre d'importance :
      1. Statut ACTIVE
      2. Non gelée ❄️
      3. Aucun transaction_block ACTIF
      4. Aucune ccfm_suspension ACTIVE
      5. Aucun conflit antifraude CRITIQUE ouvert
      6. RNP lié avec statut ACTIF (RNAF PUBLIE)
    """

    @staticmethod
    async def check(
        db: AsyncSession,
        parcelle_id: str,
        include_rnp_check: bool = True,
    ) -> Tuple[bool, str, dict]:
        """
        Retourne (eligible, raison, details).
        details contient le détail de chaque contrôle.
        """
        result = await db.execute(
            select(Parcelle).where(Parcelle.id == parcelle_id)
        )
        parcelle = result.scalar_one_or_none()
        if not parcelle:
            return False, "Parcelle introuvable", {}

        details: dict = {
            "statut": parcelle.statut,
            "is_gele": parcelle.is_gele,
            "blocks_actifs": 0,
            "suspensions_ccfm": 0,
            "conflits_critiques": 0,
            "statut_rnp": None,
        }

        # 1. Statut
        if parcelle.statut != StatutParcelle.ACTIVE:
            return (False,
                    f"Statut {parcelle.statut} — transaction impossible",
                    details)

        # 2. Gel judiciaire ❄️
        if parcelle.is_gele:
            return False, f"Parcelle {parcelle.nicad} gelée ❄️ — litige en cours", details

        # 3. Transaction blocks actifs
        blocks_result = await db.execute(
            select(func.count(TransactionBlock.id)).where(
                and_(
                    TransactionBlock.parcelle_id == parcelle_id,
                    TransactionBlock.statut == StatutBlockage.ACTIF,
                )
            )
        )
        nb_blocks = blocks_result.scalar() or 0
        details["blocks_actifs"] = nb_blocks
        if nb_blocks > 0:
            # Récupérer le détail du premier block
            first_block_result = await db.execute(
                select(TransactionBlock).where(
                    and_(
                        TransactionBlock.parcelle_id == parcelle_id,
                        TransactionBlock.statut == StatutBlockage.ACTIF,
                    )
                ).limit(1)
            )
            first_block = first_block_result.scalar_one_or_none()
            motif = first_block.motif if first_block else "Blocage actif"
            return (False,
                    f"{nb_blocks} blocage(s) transactionnel(s) actif(s) : {motif}",
                    details)

        # 4. Suspensions CCFM actives
        ccfm_result = await db.execute(
            select(func.count(CCFMSuspension.id)).where(
                and_(
                    CCFMSuspension.parcelle_id == parcelle_id,
                    CCFMSuspension.est_active == True,
                )
            )
        )
        nb_ccfm = ccfm_result.scalar() or 0
        details["suspensions_ccfm"] = nb_ccfm
        if nb_ccfm > 0:
            return (False,
                    f"Parcelle {parcelle.nicad} sous suspension CCFM active ({nb_ccfm} suspension(s))",
                    details)

        # 5. Conflits critiques antifraude
        conflit_result = await db.execute(
            select(func.count(ConflitParcellaire.id)).where(
                and_(
                    ConflitParcellaire.parcelle_a_id == parcelle_id,
                    ConflitParcellaire.gravite == GraviteConflit.CRITIQUE,
                    ConflitParcellaire.statut.in_([StatutConflit.OUVERT, StatutConflit.BLOQUE]),
                )
            )
        )
        nb_conflits = conflit_result.scalar() or 0
        details["conflits_critiques"] = nb_conflits
        if nb_conflits > 0:
            return (False,
                    f"{nb_conflits} conflit(s) antifraude critique(s) non résolu(s)",
                    details)

        # 6. Statut RNP (RNAF publié requis)
        if include_rnp_check:
            rnp_result = await db.execute(
                select(RNPParcelleLink).where(
                    RNPParcelleLink.parcelle_id == parcelle_id
                )
            )
            rnp_link = rnp_result.scalar_one_or_none()
            if rnp_link:
                details["statut_rnp"] = rnp_link.statut_rnp
                if rnp_link.statut_rnp != StatutRNP.ACTIF:
                    return (False,
                            f"Statut RNP {rnp_link.statut_rnp} — RNAF doit être PUBLIÉ",
                            details)

        return True, "Parcelle éligible — aucun blocage détecté", details


# ─────────────────────────────────────────────────────────────
# RNAF WORKFLOW SERVICE
# ─────────────────────────────────────────────────────────────

class RNAFWorkflowService:
    """
    Orchestre le cycle de vie RNAF.
    RÈGLE : RNP ne peut être ACTIF sans RNAF PUBLIE.
    RÈGLE : Publication JO obligatoire avant passage à PUBLIE.
    """

    # Transitions autorisées
    TRANSITIONS = {
        StatutRNAF.BROUILLON:        [StatutRNAF.EN_VERIFICATION],
        StatutRNAF.EN_VERIFICATION:  [StatutRNAF.EN_SCELLEMENT, StatutRNAF.BROUILLON],
        StatutRNAF.EN_SCELLEMENT:    [StatutRNAF.SCELLE, StatutRNAF.EN_VERIFICATION],
        StatutRNAF.SCELLE:           [StatutRNAF.PUBLIE],
        StatutRNAF.PUBLIE:           [StatutRNAF.SUSPENDU, StatutRNAF.ARCHIVE],
        StatutRNAF.SUSPENDU:         [StatutRNAF.PUBLIE, StatutRNAF.ANNULE],
        StatutRNAF.ANNULE:           [],   # terminal
        StatutRNAF.ARCHIVE:          [],   # terminal
    }

    @classmethod
    async def avancer_statut(
        db: AsyncSession,
        rnaf_workflow_id: str,
        nouveau_statut: StatutRNAF,
        acteur_id: str,
        motif: Optional[str] = None,
        publication_jo_id: Optional[str] = None,
    ) -> RNAFWorkflow:
        """
        Avance le workflow RNAF vers le nouveau statut.
        Vérifie la transition, les pré-conditions, puis déclenche
        les effets cascades (sync RNP, BGU, blocks).
        """
        result = await db.execute(
            select(RNAFWorkflow).where(RNAFWorkflow.id == rnaf_workflow_id)
        )
        wf = result.scalar_one_or_none()
        if not wf:
            raise ValueError(f"RNAFWorkflow {rnaf_workflow_id} introuvable")

        # Valider transition
        transitions_ok = RNAFWorkflowService.TRANSITIONS.get(wf.statut, [])
        if nouveau_statut not in transitions_ok:
            raise ValueError(
                f"Transition {wf.statut} → {nouveau_statut} non autorisée. "
                f"Autorisées : {[t.value for t in transitions_ok]}"
            )

        # Pré-condition : PUBLIE nécessite publication_jo_id
        if nouveau_statut == StatutRNAF.PUBLIE:
            jo_id = publication_jo_id or wf.publication_jo_id
            if not jo_id:
                raise ValueError(
                    "Publication Journal Officiel obligatoire avant passage à PUBLIE"
                )
            wf.publication_jo_id = jo_id
            wf.date_publication = datetime.now(timezone.utc)
            wf.publie_par = acteur_id
            # SHA-256 de l'état publié
            wf.sha256_publie = hashlib.sha256(
                f"{wf.rnaf_id}|{jo_id}|{acteur_id}|{datetime.now(timezone.utc).isoformat()}".encode()
            ).hexdigest()

        # Pré-conditions pour SUSPENDU
        if nouveau_statut == StatutRNAF.SUSPENDU:
            if not motif:
                raise ValueError("Motif de suspension obligatoire")
            wf.motif_suspension = motif
            wf.date_suspension = datetime.now(timezone.utc)
            wf.suspendu_par = acteur_id

        # Appliquer le nouveau statut
        ancien_statut = wf.statut
        wf.statut = nouveau_statut
        wf.updated_at = datetime.now(timezone.utc)

        # Dater les étapes
        now = datetime.now(timezone.utc)
        if nouveau_statut == StatutRNAF.EN_VERIFICATION:
            wf.date_soumission = now
            wf.soumis_par = acteur_id
        elif nouveau_statut == StatutRNAF.SCELLE:
            wf.date_scellement = now
            wf.scelle_par = acteur_id
        elif nouveau_statut == StatutRNAF.EN_VERIFICATION and ancien_statut == StatutRNAF.EN_SCELLEMENT:
            wf.date_verification = now
            wf.verifie_par = acteur_id

        # ─── Coordination avec WorkflowEngine ─────────────────────────────────
        from app.services.workflow_engine import WorkflowEngine
        from app.models.workflow_engine import WorkflowInstance, StatutInstance
        from app.models.auth import User
        
        # Récupérer l'utilisateur pour connaître son rôle
        user_res = await db.execute(select(User).where(User.id == uuid.UUID(acteur_id) if isinstance(acteur_id, str) else acteur_id))
        user = user_res.scalar_one_or_none()
        role = user.role if user else "ADMIN"

        # Récupérer l'instance existante ou la démarrer
        inst_res = await db.execute(
            select(WorkflowInstance).where(
                and_(
                    WorkflowInstance.entite_id == str(wf.rnaf_id),
                    WorkflowInstance.entite_type == "rnaf"
                )
            )
        )
        instance = inst_res.scalar_one_or_none()
        if not instance:
            instance = await WorkflowEngine.demarrer(
                db=db,
                type_workflow="RNAF",
                entite_type="rnaf",
                entite_id=str(wf.rnaf_id),
                demarre_par_id=acteur_id,
            )

        # Déterminer s'il s'agit d'un rejet/retour en arrière
        is_rejet = False
        if (ancien_statut == StatutRNAF.EN_VERIFICATION and nouveau_statut == StatutRNAF.BROUILLON) or \
           (ancien_statut == StatutRNAF.EN_SCELLEMENT and nouveau_statut == StatutRNAF.EN_VERIFICATION):
            is_rejet = True

        if is_rejet:
            await WorkflowEngine.rejeter_etape(
                db=db,
                instance_id=instance.id,
                acteur_id=acteur_id,
                role=role,
                motif=motif or "Retour à l'étape précédente",
            )
        else:
            # Avancement (validation)
            await WorkflowEngine.valider_etape(
                db=db,
                instance_id=instance.id,
                acteur_id=acteur_id,
                role=role,
                commentaire=motif or f"Transition vers {nouveau_statut.value}",
                snapshot_entite={"rnaf_id": str(wf.rnaf_id), "statut": nouveau_statut.value}
            )

        await db.flush()
        # Note : le trigger tg_sync_rnp_from_rnaf se déclenche automatiquement
        # et synchronise les RNP liés + BGU statuts

        return wf

    @staticmethod
    async def suspendre_avec_blocage(
        db: AsyncSession,
        rnaf_workflow_id: str,
        motif: str,
        sha256_preuve: str,
        emis_par: str,
        demande_ccfm_id: str,
    ) -> Tuple[RNAFWorkflow, CCFMSuspension]:
        """
        Suspend un RNAF + crée une suspension CCFM.
        Le trigger auto_block_on_ccfm_suspension crée ensuite les transaction_blocks.
        """
        result = await db.execute(
            select(RNAFWorkflow).where(RNAFWorkflow.id == rnaf_workflow_id)
        )
        wf = result.scalar_one_or_none()
        if not wf:
            raise ValueError("RNAF introuvable")
        if wf.statut != StatutRNAF.PUBLIE:
            raise ValueError(f"Seul un RNAF PUBLIE peut être suspendu (actuel: {wf.statut})")

        # Créer la suspension CCFM (trigger auto-bloquera les transactions)
        suspension = CCFMSuspension(
            id=uuid.uuid4(),
            demande_ccfm_id=demande_ccfm_id,
            rnaf_workflow_id=rnaf_workflow_id,
            motif_suspension=motif,
            sha256_preuve=sha256_preuve,
            emise_par=emis_par,
            est_active=True,
        )
        db.add(suspension)
        await db.flush()

        # Passer RNAF à SUSPENDU
        wf.statut = StatutRNAF.SUSPENDU
        wf.motif_suspension = motif
        wf.date_suspension = datetime.now(timezone.utc)
        wf.suspendu_par = emis_par
        wf.updated_at = datetime.now(timezone.utc)
        await db.flush()

        # Coordonner avec WorkflowEngine.suspendre
        from app.services.workflow_engine import WorkflowEngine
        from app.models.workflow_engine import WorkflowInstance
        inst_res = await db.execute(
            select(WorkflowInstance).where(
                and_(
                    WorkflowInstance.entite_id == str(wf.rnaf_id),
                    WorkflowInstance.entite_type == "rnaf"
                )
            )
        )
        instance = inst_res.scalar_one_or_none()
        if instance:
            await WorkflowEngine.suspendre(
                db=db,
                instance_id=instance.id,
                acteur_id=emis_par,
                motif=motif,
            )

        return wf, suspension


# ─────────────────────────────────────────────────────────────
# ANNF ARCHIVE SERVICE
# ─────────────────────────────────────────────────────────────

class ANNFArchiveService:
    """
    Archivage WORM ANNF.
    Une fois scellé, le document ne peut jamais être modifié.
    """

    @staticmethod
    async def archiver(
        db: AsyncSession,
        type_archive: TypeArchiveANNF,
        entite_id: str,
        entite_table: str,
        snapshot: dict,
        archive_par_id: str,
        annee: Optional[int] = None,
    ) -> ANNFArchiveLink:
        """
        Crée une entrée d'archive ANNF (non scellée).
        La fonction generate_annf_ida() PostgreSQL génère l'IDA.
        """
        # Vérifier si déjà archivé (version courante)
        existing = await db.execute(
            select(ANNFArchiveLink).where(
                and_(
                    ANNFArchiveLink.entite_id == entite_id,
                    ANNFArchiveLink.entite_table == entite_table,
                )
            ).order_by(ANNFArchiveLink.version.desc()).limit(1)
        )
        derniere_version = existing.scalar_one_or_none()
        version = (derniere_version.version + 1) if derniere_version else 1

        # Générer IDA via PostgreSQL
        ida_result = await db.execute(
            sa.text("SELECT generate_annf_ida(:annee)"),
            {"annee": annee or datetime.now().year}
        )
        ida = ida_result.scalar()

        # SHA-256 du snapshot
        import json
        snapshot_str = json.dumps(snapshot, sort_keys=True, default=str)
        sha256 = hashlib.sha256(snapshot_str.encode()).hexdigest()

        archive = ANNFArchiveLink(
            id=uuid.uuid4(),
            type_archive=type_archive,
            entite_id=entite_id,
            entite_table=entite_table,
            snapshot_json=snapshot,
            sha256_snapshot=sha256,
            ida=ida,
            scelle=False,
            version=version,
            version_precedente_id=derniere_version.id if derniere_version else None,
            archive_par=archive_par_id,
        )
        db.add(archive)
        await db.flush()
        return archive

    @staticmethod
    async def sceller(
        db: AsyncSession,
        archive_id: str,
        scelle_par_id: str,
    ) -> ANNFArchiveLink:
        """
        Scelle une archive WORM.
        Après scellement : AUCUNE modification possible (WORM).
        """
        result = await db.execute(
            select(ANNFArchiveLink).where(ANNFArchiveLink.id == archive_id)
        )
        archive = result.scalar_one_or_none()
        if not archive:
            raise ValueError("Archive introuvable")
        if archive.scelle:
            raise ValueError(f"Archive {archive.ida} déjà scellée — WORM ne peut pas être modifiée")

        archive.scelle = True
        archive.scelle_at = datetime.now(timezone.utc)
        archive.scelle_par = scelle_par_id
        await db.flush()
        return archive


# ─────────────────────────────────────────────────────────────
# PORTAIL PUBLIC SERVICE
# ─────────────────────────────────────────────────────────────

class PortailPublicService:
    """
    Service lecture seule pour le portail citoyen et BGU public.
    Utilise la vue v_portail_parcelles (créée en migration 004).
    """

    @staticmethod
    async def get_statut_parcelle(db: AsyncSession, nicad: str) -> Optional[dict]:
        """Retourne le statut public d'une parcelle par NICAD."""
        result = await db.execute(
            sa.text("""
                SELECT nicad, statut_public, statut_detail,
                       est_gele, statut_rnp, derniere_maj_at,
                       qr_code_url, surface_m2
                FROM v_portail_parcelles
                WHERE nicad = :nicad
            """),
            {"nicad": nicad}
        )
        row = result.mappings().first()
        return dict(row) if row else None

    @staticmethod
    async def get_historique_statuts(
        db: AsyncSession,
        nicad: str,
        limit: int = 20,
    ) -> list:
        """Retourne l'historique public des statuts d'une parcelle."""
        result = await db.execute(
            select(ParcelPublicHistory)
            .where(ParcelPublicHistory.nicad == nicad)
            .order_by(ParcelPublicHistory.created_at.desc())
            .limit(limit)
        )
        rows = result.scalars().all()
        return [
            {
                "statut_avant": r.statut_avant,
                "statut_apres": r.statut_apres,
                "module_source": r.module_source,
                "motif_public": r.motif_public,
                "date": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    @staticmethod
    async def record_transition(
        db: AsyncSession,
        parcelle_id: str,
        nicad: str,
        statut_avant: str,
        statut_apres: str,
        module_source: str,
        entite_id: Optional[str] = None,
        motif_public: Optional[str] = None,
        role_acteur: Optional[str] = None,
    ) -> ParcelPublicHistory:
        """Enregistre manuellement une transition (complète le trigger DB)."""
        entry = ParcelPublicHistory(
            id=uuid.uuid4(),
            parcelle_id=parcelle_id,
            nicad=nicad,
            statut_avant=statut_avant,
            statut_apres=statut_apres,
            module_source=module_source,
            entite_id=entite_id,
            motif_public=motif_public,
            role_acteur=role_acteur,
        )
        db.add(entry)
        await db.flush()
        return entry


# Import nécessaire pour le service ANNF (sqlalchemy.text)
import sqlalchemy as sa
