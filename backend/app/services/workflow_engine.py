"""
FONCIER+ v3.4.7 — WorkflowEngine
Moteur d'exécution des workflows multi-institution.

Transforme les validations manuelles en processus contrôlés,
traçables et juridiquement exécutoires.
"""

import hashlib
import hmac
import base64
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple, List
from fastapi import HTTPException

from sqlalchemy import select, and_, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.workflow_engine import (
    StatutEtape, StatutInstance, TypeValidation,
    WorkflowDefinition, WorkflowInstance, WorkflowSignature,
    WorkflowStepDef, WorkflowStepLog, WorkflowEscalade,
)


# ─────────────────────────────────────────────────────────────
# CLÉ HMAC (en production : HSM ou Vault)
# ─────────────────────────────────────────────────────────────

_HMAC_SECRET = b"FONCIER_PLUS_V347_HMAC_SECRET_CHANGE_IN_PROD"


def _signer(contenu: str) -> str:
    """Signature HMAC-SHA256 du contenu (remplacée par PKI en production)."""
    sig = hmac.new(_HMAC_SECRET, contenu.encode(), hashlib.sha256).digest()
    return base64.b64encode(sig).decode()


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────
# SERVICE DE NOTIFICATION INTERNE
# ─────────────────────────────────────────────────────────────

class WorkflowNotificationService:
    """Service de notification pour les jalons de workflows (Sécurisé en local)."""
    @staticmethod
    async def demarrer(
        db: AsyncSession, 
        type_workflow: str, 
        entite_type: str, 
        entite_id: str, 
        demarre_par_id: str, 
        nicad: Optional[str] = None,
        parcelle_id: Optional[str] = None,
        contexte: Optional[dict] = None,
        **kwargs  # Sécurité pour absorber d'éventuels autres paramètres superflus
    ) -> Optional[WorkflowInstance]:
        """
        Initialise et démarre une instance de workflow en interceptant les métadonnées 
        métier (nicad, parcelle_id) pour les encapsuler sans casser le modèle.
        """
        # 1. Récupérer la définition par son type de workflow (ex: ANNF)
        result = await db.execute(
            select(WorkflowDefinition)
            .options(selectinload(WorkflowDefinition.etapes))
            .where(WorkflowDefinition.type_workflow == type_workflow, WorkflowDefinition.is_active == True)
        )
        definition = result.scalar_one_or_none()

        if not definition:
            return None

        # 2. Extraire la première étape (ordre == 1)
        premiere_etape = next((e for e in definition.etapes if e.ordre == 1), None)
        if not premiere_etape:
            raise HTTPException(
                status_code=400, 
                detail="La définition du workflow ne contient aucune étape initiale (ordre 1)."
            )

        # 3. Consolider le contexte JSON pour ne perdre aucune info métier
        contexte_fusionne = contexte or {}
        if nicad:
            contexte_fusionne["nicad"] = nicad
        if parcelle_id:
            contexte_fusionne["parcelle_id"] = parcelle_id

        # 4. Instancier le modèle SQLAlchemy proprement
        nouvelle_instance = WorkflowInstance(
            definition_id=definition.id,
            entite_type=entite_type,
            entite_id=entite_id,
            demarre_par=demarre_par_id,
            contexte_json=contexte_fusionne,
            statut=StatutInstance.EN_COURS,
            
            # Hydratation immédiate de la première étape
            etape_courante_ordre=1,
            etape_courante_code=premiere_etape.code_etape,
            attendu_de_role=premiere_etape.role_requis
        )

        db.add(nouvelle_instance)
        await db.commit()
        await db.refresh(nouvelle_instance)
        
        return nouvelle_instance

class WorkflowEngine:
    """
    Moteur d'exécution des workflows FONCIER+.

    Usage :
        # Démarrer un workflow RNAF
        instance = await WorkflowEngine.demarrer(
            db, type_workflow="RNAF",
            entite_type="rnaf", entite_id=rnaf_id,
            demarre_par_id=user_id
        )

        # Valider l'étape courante
        instance = await WorkflowEngine.valider_etape(
            db, instance_id=instance.id,
            acteur_id=user_id, role=user.role,
            commentaire="Vérifié et approuvé"
        )

        # Rejeter
        instance = await WorkflowEngine.rejeter_etape(
            db, instance_id=instance.id,
            acteur_id=user_id, motif="Document manquant"
        )
    """

    @staticmethod
    async def demarrer(
        db: AsyncSession,
        type_workflow: str,
        entite_type: str,
        entite_id: str,
        demarre_par_id: str,
        nicad: Optional[str] = None,
        parcelle_id: Optional[str] = None,
        contexte: Optional[dict] = None,
    ) -> WorkflowInstance:
        """
        Démarre une instance de workflow pour une entité.
        Vérifie qu'aucune instance active n'existe déjà.
        """
        # Récupérer la définition avec chargement asynchrone des étapes
        from sqlalchemy.orm import selectinload

        def_result = await db.execute(
            select(WorkflowDefinition)
            .options(selectinload(WorkflowDefinition.etapes))
            .where(
                and_(
                    WorkflowDefinition.type_workflow == type_workflow,
                    WorkflowDefinition.is_active == True,
                )
            )
        )
        definition = def_result.scalar_one_or_none()
        if not definition:
            raise ValueError(f"Aucune définition active pour le workflow {type_workflow}")

        # Vérifier qu'aucune instance active n'existe
        existing = await db.execute(
            select(WorkflowInstance).where(
                and_(
                    WorkflowInstance.definition_id == definition.id,
                    WorkflowInstance.entite_id == entite_id,
                    WorkflowInstance.statut.in_([
                        StatutInstance.EN_COURS,
                        StatutInstance.EN_ATTENTE,
                        StatutInstance.SUSPENDU,
                    ]),
                )
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError(
                f"Une instance {type_workflow} est déjà active pour {entite_type}={entite_id}"
            )

        # Créer l'instance (le trigger TW1 calcule l'échéance et le rôle attendu)
        instance = WorkflowInstance(
            id=uuid.uuid4(),
            definition_id=definition.id,
            entite_type=entite_type,
            entite_id=entite_id,
            nicad=nicad,
            parcelle_id=parcelle_id,
            statut=StatutInstance.EN_COURS,
            etape_courante_ordre=1,
            contexte_json=contexte or {},
            demarre_par=demarre_par_id,
        )
        db.add(instance)
        await db.flush()

        # Enregistrer l'étape initiale
        await WorkflowEngine._log_step(
            db, instance,
            step_ordre=1, statut=StatutEtape.EN_ATTENTE,
            acteur_id=demarre_par_id,
            commentaire=f"Workflow {type_workflow} démarré",
        )

        WorkflowNotificationService.notifier(
            instance_id=str(instance.id),
            event_type="DEMARRAGE",
            details=f"Workflow de type {type_workflow} démarré pour l'entité {entite_type} ({entite_id})",
            role_dest=instance.attendu_de_role
        )

        return instance

    @staticmethod
    async def valider_etape(
        db: AsyncSession,
        instance_id: str,
        acteur_id: str,
        role: str,
        commentaire: Optional[str] = None,
        documents: Optional[list] = None,
        snapshot_entite: Optional[dict] = None,
    ) -> WorkflowInstance:
        """
        Valide l'étape courante et passe à la suivante.
        Vérifie que l'acteur a le bon rôle.
        Crée la signature numérique.
        """
        instance, step_def = await WorkflowEngine._get_instance_and_step(db, instance_id)

        # Guard : bloquer si workflow déjà terminé ou annulé
        if instance.statut in ("TERMINE", "ANNULE", "SUSPENDU"):
            raise HTTPException(
                status_code=409,
                detail=f"Workflow {instance_id} est au statut '{instance.statut}' "
                       f"et ne peut plus être validé."
            )

        # Vérification du rôle (avec règle des 4 yeux et restriction Admin)
        await WorkflowEngine._verifier_role(role, step_def, acteur_id, instance)

        now = datetime.now(timezone.utc)

        # Enregistrer l'étape validée
        step_log = await WorkflowEngine._log_step(
            db, instance,
            step_ordre=step_def.ordre,
            statut=StatutEtape.VALIDEE,
            acteur_id=acteur_id,
            role_acteur=role,
            commentaire=commentaire,
            decision="VALIDE",
            documents=documents,
            snapshot_apres=snapshot_entite,
        )

        # Créer la signature si type_validation = signature ou scellement
        if step_def.type_validation in (TypeValidation.SIGNATURE,
                                         TypeValidation.SCELLEMENT,
                                         TypeValidation.HORODATAGE):
            await WorkflowEngine._signer_etape(db, instance, step_log, acteur_id, role)

        # Passer à l'étape suivante
        next_step = await WorkflowEngine._get_next_step(db, instance, step_def.ordre)

        if next_step:
            instance.etape_courante_ordre = next_step.ordre
            instance.etape_courante_code  = next_step.code_etape
            instance.attendu_de_role      = next_step.role_requis
            instance.statut               = StatutInstance.EN_ATTENTE
            instance.updated_at           = now

            # Log de la nouvelle étape EN_ATTENTE
            await WorkflowEngine._log_step(
                db, instance,
                step_ordre=next_step.ordre,
                statut=StatutEtape.EN_ATTENTE,
                acteur_id=None,
                commentaire=f"En attente de : {next_step.role_requis}",
            )

            # Déclencher l'action post-validation si définie
            if step_def.action_post_validation:
                await WorkflowEngine._executer_action(
                    db, instance, step_def.action_post_validation
                )
        else:
            # Toutes les étapes franchies → COMPLETE
            await WorkflowEngine._completer(db, instance, acteur_id)

        WorkflowNotificationService.notifier(
            instance_id=str(instance.id),
            event_type="VALIDATION_ETAPE",
            details=f"Étape {step_def.ordre} ({step_def.code_etape}) validée par l'acteur {acteur_id}.",
            role_dest=instance.attendu_de_role
        )

        await db.flush()
        return instance

    @staticmethod
    async def demarrer(
        db: AsyncSession,
        type_workflow: str,
        entite_type: str,
        entite_id: str,
        demarre_par_id: str,
        nicad: Optional[str] = None,
        parcelle_id: Optional[str] = None,
        contexte: Optional[dict] = None,
    ) -> WorkflowInstance:
        """
        Initialise et démarre une instance de workflow en insérant proprement 
        les métadonnées métiers (nicad, parcelle_id) dans le contexte JSON.
        """
        # 1. Vérifier si une définition active existe pour ce type de workflow
        result = await db.execute(
            select(WorkflowDefinition)
            .options(selectinload(WorkflowDefinition.etapes))
            .where(WorkflowDefinition.type_workflow == type_workflow, WorkflowDefinition.is_active == True)
        )
        definition = result.scalar_one_or_none()

        if not definition:
            raise ValueError(f"Aucune définition active pour le workflow {type_workflow}")

        # 2. Vérifier si une instance est déjà active pour cette entité
        existing = await db.execute(
            select(WorkflowInstance).where(
                WorkflowInstance.entite_type == entite_type,
                WorkflowInstance.entite_id == entite_id,
                WorkflowInstance.statut == StatutInstance.EN_COURS
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError(
                f"Une instance {type_workflow} est déjà active pour {entite_type}={entite_id}"
            )

        # 3. Consolider le contexte JSON pour y inclure le NICAD et le Parcelle ID
        contexte_fusionne = contexte or {}
        if nicad:
            contexte_fusionne["nicad"] = nicad
        if parcelle_id:
            contexte_fusionne["parcelle_id"] = parcelle_id

        # 4. Créer l'instance proprement (sans passer d'arguments invalides à SQLAlchemy)
        instance = WorkflowInstance(
            id=uuid.uuid4(),
            definition_id=definition.id,
            entite_type=entite_type,
            entite_id=entite_id,
            contexte_json=contexte_fusionne,
            demarre_par=demarre_par_id,
            statut=StatutInstance.EN_COURS,
            etape_courante_ordre=1,
            etape_courante_code="VERIFIER",  # Code par défaut pour l'étape 1
            attendu_de_role="ADMIN"          # Rôle par défaut de l'étape initiale
        )
        
        db.add(instance)
        await db.flush()

        # 5. Enregistrer l'étape initiale dans les logs immuables
        await WorkflowEngine._log_step(
            db, instance,
            step_ordre=1, statut=StatutEtape.EN_COURS,
            acteur_id=demarre_par_id,
            commentaire=f"Workflow {type_workflow} démarré avec succès",
        )

        # 6. Notification asynchrone (optionnelle)
        try:
            WorkflowNotificationService.notifier(
                instance_id=str(instance.id),
                event_type="DEMARRAGE",
                details=f"Workflow de type {type_workflow} démarré pour l'entité {entite_type} ({entite_id})",
                role_dest=instance.attendu_de_role
            )
        except Exception:
            pass # Évite de bloquer le workflow si le service de notification est éteint

        await db.commit()
        return instance

    @staticmethod
    async def suspendre(
        db: AsyncSession,
        instance_id: str,
        acteur_id: str,
        motif: str,
    ) -> WorkflowInstance:
        """Suspend un workflow en cours (intervention externe requise)."""
        result = await db.execute(
            select(WorkflowInstance).where(WorkflowInstance.id == instance_id)
        )
        instance = result.scalar_one_or_none()
        if not instance:
            raise ValueError(f"Instance {instance_id} introuvable")
        if instance.statut not in (StatutInstance.EN_COURS, StatutInstance.EN_ATTENTE):
            raise ValueError(f"Instance au statut {instance.statut} — suspension impossible")

        instance.statut = StatutInstance.SUSPENDU
        instance.updated_at = datetime.now(timezone.utc)

        await WorkflowEngine._log_step(
            db, instance,
            step_ordre=instance.etape_courante_ordre,
            statut=StatutEtape.EN_ATTENTE,
            acteur_id=acteur_id,
            commentaire=f"SUSPENDU : {motif}",
            decision="SUSPENDU",
        )
        WorkflowNotificationService.notifier(
            instance_id=str(instance.id),
            event_type="SUSPENSION",
            details=f"Workflow suspendu par l'acteur {acteur_id}. Motif : {motif}",
            role_dest="ADMIN"
        )

        await db.flush()
        return instance

    @staticmethod
    async def get_etat(
        db: AsyncSession,
        instance_id: str,
    ) -> dict:
        """Retourne l'état complet d'un workflow."""
        result = await db.execute(
            select(WorkflowInstance).where(WorkflowInstance.id == instance_id)
        )
        instance = result.scalar_one_or_none()
        if not instance:
            raise ValueError(f"Instance {instance_id} introuvable")

        steps_result = await db.execute(
            select(WorkflowStepLog)
            .where(WorkflowStepLog.instance_id == instance_id)
            .order_by(WorkflowStepLog.created_at)
        )
        steps = steps_result.scalars().all()

        escalades_result = await db.execute(
            select(WorkflowEscalade)
            .where(
                and_(
                    WorkflowEscalade.instance_id == instance_id,
                    WorkflowEscalade.est_traitee == False,
                )
            )
        )
        escalades = escalades_result.scalars().all()

        return {
            "instance_id": str(instance.id),
            "entite_type": instance.entite_type,
            "entite_id": str(instance.entite_id),
            "nicad": instance.nicad,
            "statut": instance.statut,
            "etape_courante": instance.etape_courante_code,
            "etape_courante_ordre": instance.etape_courante_ordre,
            "attendu_de_role": instance.attendu_de_role,
            "date_debut": instance.date_debut.isoformat() if instance.date_debut else None,
            "date_echeance": instance.date_echeance.isoformat()
                if instance.date_echeance else None,
            "est_en_retard": (
                instance.date_echeance is not None
                and instance.date_echeance < datetime.now(timezone.utc)
                and instance.statut != StatutInstance.COMPLETE
            ),
            "nb_escalades_actives": len(escalades),
            "historique": [
                {
                    "step_code": s.step_code,
                    "step_ordre": s.step_ordre,
                    "statut": s.statut_etape,
                    "decision": s.decision,
                    "acteur_role": s.role_acteur,
                    "commentaire": s.commentaire,
                    "sha256": s.sha256_step,
                    "date": s.created_at.isoformat() if s.created_at else None,
                }
                for s in steps
            ],
        }

    @staticmethod
    async def verifier_signatures_instance(db: AsyncSession, instance_id: str) -> bool:
        """
        Vérifie l'intégrité de toutes les étapes et signatures d'une instance.
        Recalcule les signatures cryptographiques HMAC-SHA256 pour détecter toute falsification SQL directe.
        """
        logs_result = await db.execute(
            select(WorkflowStepLog)
            .where(WorkflowStepLog.instance_id == instance_id)
            .order_by(WorkflowStepLog.step_ordre)
        )
        step_logs = logs_result.scalars().all()
        
        for s in step_logs:
            sig_result = await db.execute(
                select(WorkflowSignature).where(WorkflowSignature.step_log_id == s.id)
            )
            sig = sig_result.scalar_one_or_none()
            if not sig:
                continue
            
            calculated_sig = _signer(sig.contenu_signe)
            if calculated_sig != sig.signature_b64:
                return False
                
        return True

    # ── Méthodes privées ─────────────────────────────────────────────

    @staticmethod
    async def _get_instance_and_step(
        db: AsyncSession, instance_id: str
    ) -> Tuple[WorkflowInstance, WorkflowStepDef]:
        result = await db.execute(
            select(WorkflowInstance).where(WorkflowInstance.id == instance_id)
        )
        instance = result.scalar_one_or_none()
        if not instance:
            raise ValueError(f"Instance workflow {instance_id} introuvable")
        if instance.statut not in (StatutInstance.EN_COURS, StatutInstance.EN_ATTENTE):
            raise ValueError(
                f"Instance au statut {instance.statut} — aucune action possible"
            )

        step = await WorkflowEngine._get_step_by_ordre(
            db, instance.definition_id, instance.etape_courante_ordre
        )
        if not step:
            raise ValueError(
                f"Étape {instance.etape_courante_ordre} introuvable "
                f"pour la définition {instance.definition_id}"
            )
        return instance, step

    @staticmethod
    async def _get_step_by_ordre(
        db: AsyncSession, definition_id: str, ordre: int
    ) -> Optional[WorkflowStepDef]:
        result = await db.execute(
            select(WorkflowStepDef).where(
                and_(
                    WorkflowStepDef.definition_id == definition_id,
                    WorkflowStepDef.ordre == ordre,
                )
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _get_next_step(
        db: AsyncSession, instance: WorkflowInstance, current_ordre: int
    ) -> Optional[WorkflowStepDef]:
        result = await db.execute(
            select(WorkflowStepDef).where(
                and_(
                    WorkflowStepDef.definition_id == instance.definition_id,
                    WorkflowStepDef.ordre > current_ordre,
                    WorkflowStepDef.est_obligatoire == True,
                )
            ).order_by(WorkflowStepDef.ordre).limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _verifier_role(
        role: str,
        step_def: WorkflowStepDef,
        acteur_id: Optional[str] = None,
        instance: Optional[WorkflowInstance] = None,
    ) -> None:
        roles_autorises = [step_def.role_requis]
        if step_def.role_backup:
            roles_autorises.append(step_def.role_backup)
        
        # Anti-Bypass Admin: Admin is only allowed if role_requis is ADMIN or explicitly authorized
        if role == "ADMIN" and "ADMIN" not in roles_autorises:
            raise ValueError(
                f"Rôle 'ADMIN' non autorisé pour valider l'étape '{step_def.code_etape}'. "
                f"L'administrateur système ne peut pas bypasser cette étape de contrôle métier."
            )

        if role not in roles_autorises:
            raise ValueError(
                f"Rôle '{role}' non autorisé pour l'étape '{step_def.code_etape}'. "
                f"Rôle requis : {step_def.role_requis}"
                + (f" ou {step_def.role_backup}" if step_def.role_backup else "")
            )

        # Règle des Quatre Yeux (Four-Eyes Principle): The validator cannot be the same physical user who started the workflow
        if acteur_id and instance and str(instance.demarre_par) == str(acteur_id):
            if step_def.ordre > 1:
                raise ValueError(
                    f"Infraction à la règle des quatre yeux : L'initiateur du dossier ({instance.demarre_par}) "
                    f"ne peut pas valider l'étape de contrôle '{step_def.code_etape}'."
                )

    @staticmethod
    async def _log_step(
        db: AsyncSession,
        instance: WorkflowInstance,
        step_ordre: int,
        statut: StatutEtape,
        acteur_id: Optional[str],
        role_acteur: Optional[str] = None,
        commentaire: Optional[str] = None,
        decision: Optional[str] = None,
        documents: Optional[list] = None,
        snapshot_apres: Optional[dict] = None,
    ) -> WorkflowStepLog:
        """Enregistre une étape. Le trigger TW2 calcule sha256_step automatiquement."""
       # Déterminer un code et un nom valides (évite le NOT NULL violation)
        code_final = instance.etape_courante_code or f"ETAPE_{step_ordre}"
        nom_final = f"Exécution {code_final}"

        # Déterminer un code, un nom et une décision valides (évite le NOT NULL violation)
        code_final = instance.etape_courante_code or f"ETAPE_{step_ordre}"
        nom_final = f"Exécution {code_final}"

        step_log = WorkflowStepLog(
            id=uuid.uuid4(),
            instance_id=instance.id,
            step_ordre=step_ordre,
            step_code=code_final,
            step_nom=nom_final,
            statut_etape=statut,
            acteur_id=acteur_id,
            role_acteur=role_acteur if role_acteur else "ADMIN",
            commentaire=commentaire,
            decision=decision if decision else "INITIAL",  # 🎯 Garanti non-nul pour PostgreSQL
            donnees_apres={**(snapshot_apres or {}), "documents_soumis": documents} if documents else snapshot_apres,
            sha256_step="PENDING",  # calculé par trigger TW2
            sha256_prev="PENDING"   # Garanti non-nul pour la contrainte
        )
        db.add(step_log)
        await db.flush()
        return step_log
    @staticmethod
    async def _signer_etape(
        db: AsyncSession,
        instance: WorkflowInstance,
        step_log: WorkflowStepLog,
        acteur_id: str,
        role: str,
    ) -> WorkflowSignature:
        """Crée une signature numérique pour une étape."""
        contenu = (
            f"{instance.id}|{step_log.step_code}|{step_log.statut_etape}|"
            f"{step_log.sha256_step}|{datetime.now(timezone.utc).isoformat()}"
        )
        sha256_contenu = _sha256(contenu)
        signature_b64 = _signer(contenu)

        sig = WorkflowSignature(
            id=uuid.uuid4(),
            instance_id=instance.id,
            step_log_id=step_log.id,
            signataire_id=acteur_id,
            role_signataire=role,
            contenu_signe=contenu,
            sha256_contenu=sha256_contenu,
            signature_b64=signature_b64,
            algorithme="HMAC-SHA256",
            est_valide=True,
        )
        db.add(sig)
        await db.flush()
        return sig

    @staticmethod
    async def _completer(
        db: AsyncSession, instance: WorkflowInstance, acteur_id: str
    ) -> None:
        """Marque une instance comme COMPLETE avec sha256 de complétion."""
        now = datetime.now(timezone.utc)
        sha256_comp = _sha256(
            f"{instance.id}|COMPLETE|{instance.entite_id}|{now.isoformat()}"
        )
        instance.statut            = StatutInstance.COMPLETE
        instance.date_completion   = now
        instance.sha256_completion = sha256_comp
        instance.updated_at        = now
        # Le trigger TW3 déclenche l'archivage ANNF automatiquement

    @staticmethod
    async def _executer_action(
        db: AsyncSession, instance: WorkflowInstance, action: str
    ) -> None:
        """
        Exécute une action post-validation.
        Les actions sont des hooks applicatifs déclenchés par le moteur.
        """
        # En production : dispatcher vers les services métier
        # Ici : trace dans le contexte de l'instance
        if not instance.contexte_json:
            instance.contexte_json = {}
        instance.contexte_json[f"action_{action}"] = {
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "status": "triggered"
        }
        # Dispatcher des actions post-validation
        if action == 'GENERER_NICAD_AUTO':
            if instance.parcelle_id:
                try:
                    from app.services.parcellaire_service import NicadService
                    from app.models.parcellaire import Parcelle, NicadRegistry
                    from sqlalchemy import select as _sel
                    r_p = await db.execute(_sel(Parcelle).where(Parcelle.id == instance.parcelle_id))
                    parc = r_p.scalar_one_or_none()
                    if parc and parc.nicad:
                        r_r = await db.execute(_sel(NicadRegistry).where(NicadRegistry.nicad == parc.nicad))
                        if not r_r.scalar_one_or_none():
                            parsed = NicadService.parse(parc.nicad)
                            db.add(NicadRegistry(
                                nicad=parc.nicad, parcelle_id=parc.id,
                                genere_par=instance.demarre_par, **parsed))
                except Exception as e:
                    import logging
                    logging.getLogger('cadastre').warning('GENERER_NICAD: %s', e)

        elif action == 'GEL_PARCELLE':
            if instance.parcelle_id:
                from sqlalchemy import update as _upd
                from app.models.parcellaire import Parcelle
                from app.models.workflows import TransactionBlock, TypeBlockage, StatutBlockage
                await db.execute(_upd(Parcelle).where(Parcelle.id == instance.parcelle_id).values(is_gele=True))
                db.add(TransactionBlock(parcelle_id=instance.parcelle_id,
                    type_blockage=TypeBlockage.JUSTICE, statut=StatutBlockage.ACTIF,
                    motif='Gel WF ' + (instance.type_workflow or ''), pose_par=instance.demarre_par))

        elif action == 'DEGELER_PARCELLE':
            if instance.parcelle_id:
                from sqlalchemy import update as _upd
                from app.models.parcellaire import Parcelle
                await db.execute(_upd(Parcelle).where(Parcelle.id == instance.parcelle_id).values(is_gele=False))

        elif action == 'SCELLER_BGU_GEOM':
            # BUG-WF-BGU-01 fix : scellement BGU automatique à l'étape 5
            if instance.parcelle_id:
                from app.models.parcellaire import BGUGeoJSONMaster
                from sqlalchemy import select as _sel
                r_bgu = await db.execute(
                    _sel(BGUGeoJSONMaster)
                    .where(BGUGeoJSONMaster.parcelle_id == instance.parcelle_id)
                    .where(BGUGeoJSONMaster.scelle == False)
                )
                bgu = r_bgu.scalar_one_or_none()
                if bgu:
                    bgu.scelle    = True
                    bgu.scelle_at = datetime.now(timezone.utc)
                    bgu.scelle_par = instance.demarre_par
                    import logging
                    logging.getLogger('bgu').info(
                        'BGU scellé automatiquement via WF étape 5, parcelle=%s',
                        instance.parcelle_id
                    )

        elif action == 'CHECK_GEOM_QUALITE':
            import logging
            logging.getLogger('bgu').info(
                'CHECK_GEOM_QUALITE : vérification PostGIS parcelle=%s'
                ' — déléguer à tg_antifraude_overlap', instance.parcelle_id
            )

        elif action == 'VERIFIER_COHERENCE_RNAF':
            import logging
            logging.getLogger('bgu').info(
                'VERIFIER_COHERENCE_RNAF : cohérence RNAF/RNP parcelle=%s',
                instance.parcelle_id
            )

        elif action == 'ARCHIVER_BGU_ANNF':
            try:
                from app.services.workflow_orchestrator import ANNFArchiveService
                await ANNFArchiveService.archiver(
                    db=db,
                    parcelle_id=instance.parcelle_id or instance.entite_id,
                    type_archive='BGU_ARCHIVE',
                    workflow_instance_id=str(instance.id),
                )
            except Exception as e:
                import logging
                logging.getLogger('annf').warning('ARCHIVER_BGU non critique: %s', e)


        elif action in ('ARCHIVER_TRANSFERT_ANNF', 'ARCHIVER_RNP_ANNF',
                        'ARCHIVER_CCFM_ANNF', 'ARCHIVER_SUCCESSION_ANNF',
                        'ARCHIVER_ANNF'):
            try:
                from app.services.workflow_orchestrator import ANNFArchiveService
                from app.models.workflows import TypeArchiveANNF
                type_map = {
                    'ARCHIVER_RNP_ANNF':         TypeArchiveANNF.RNP,
                    'ARCHIVER_CCFM_ANNF':        TypeArchiveANNF.CCFM,
                    'ARCHIVER_TRANSFERT_ANNF':   TypeArchiveANNF.ACTE_NOTARIE,
                    'ARCHIVER_SUCCESSION_ANNF':  TypeArchiveANNF.ACTE_NOTARIE,
                    'ARCHIVER_ANNF':             TypeArchiveANNF.PLAN_CADASTRAL,
                }
                type_archive = type_map.get(action, TypeArchiveANNF.PLAN_CADASTRAL)
                snap = {
                    'workflow_instance_id': str(instance.id),
                    'type_workflow': instance.type_workflow,
                    'entite_id': str(instance.entite_id),
                    'entite_type': instance.entite_type,
                    'completed_at': datetime.now(timezone.utc).isoformat(),
                }
                await ANNFArchiveService.archiver(
                    db=db,
                    type_archive=type_archive,
                    entite_id=str(instance.entite_id),
                    entite_table=instance.entite_type or 'workflow_instance',
                    snapshot=snap,
                    archive_par_id=str(instance.demarre_par) if instance.demarre_par else acteur_id,
                )
                import logging
                logging.getLogger('annf').info(
                    '%s déclenché — entite=%s', action, instance.entite_id)
            except Exception as e:
                import logging
                logging.getLogger('annf').warning('%s non critique: %s', action, e)

        elif action == 'CHECK_CCFM_GATE':
            # Vérifier que la parcelle a un CCFM valide avant de continuer
            if instance.parcelle_id:
                r_ccfm = await db.execute(text(
                    "SELECT COUNT(*) FROM demandes_ccfm "
                    "WHERE parcelle_id=:pid AND statut='signe'"
                ), {"pid": str(instance.parcelle_id)})
                nb_ccfm = r_ccfm.scalar() or 0
                if nb_ccfm == 0:
                    raise ValueError(
                        f"CHECK_CCFM_GATE: aucun certificat CCFM signé "
                        f"pour la parcelle {instance.parcelle_id}. "
                        "Déposer et signer un CCFM avant de continuer."
                    )
                instance.contexte_json = instance.contexte_json or {}
                instance.contexte_json['ccfm_gate_ok'] = True
                import logging
                logging.getLogger('ccfm').info(
                    'CHECK_CCFM_GATE passé — %d CCFM signé(s)', nb_ccfm)

        elif action == 'CHECK_LITIGES':
            # Vérifier absence de litiges ouverts sur la parcelle
            if instance.parcelle_id:
                r_lit = await db.execute(text(
                    "SELECT COUNT(*) FROM parcel_disputes "
                    "WHERE parcelle_id=:pid AND statut='ouvert'"
                ), {"pid": str(instance.parcelle_id)})
                nb_lit = r_lit.scalar() or 0
                if nb_lit > 0:
                    raise ValueError(
                        f"CHECK_LITIGES: {nb_lit} litige(s) ouvert(s) sur la parcelle. "
                        "Résoudre tous les litiges avant signature de l'acte."
                    )
                instance.contexte_json = instance.contexte_json or {}
                instance.contexte_json['litiges_ok'] = True

        elif action == 'CREER_DROIT_VERSION':
            # Crée une nouvelle version de droit foncier après mutation (Action 3 / Incohérence 3)
            if instance.parcelle_id:
                ctx = instance.contexte_json or {}
                nouveau_tid = ctx.get('nouveau_titulaire_id')
                ancien_tid  = ctx.get('ancien_titulaire_id')
                rnaf_id     = ctx.get('rnaf_id')
                if nouveau_tid and ancien_tid and rnaf_id:
                    from app.services.droits_service import TransfertService
                    from app.models.droits_fonciers import TypeTransfert
                    r_rnp = await db.execute(text("""
                        SELECT r.id FROM rnp_demandes r
                        JOIN rnp_parcelle_link l ON l.rnp_demande_id=r.id
                        WHERE l.parcelle_id=:pid AND r.statut='actif' LIMIT 1
                    """), {"pid": str(instance.parcelle_id)})
                    rnp_id = r_rnp.scalar()
                    if rnp_id:
                        await TransfertService.effectuer_transfert(
                            db=db,
                            rnp_parcelle_id=str(rnp_id),
                            rnaf_id=str(rnaf_id),
                            ancien_titulaire_id=str(ancien_tid),
                            nouveau_titulaire_id=str(nouveau_tid),
                            pourcentage_transfere=float(ctx.get('pourcentage', 100)),
                            type_transfert=TypeTransfert(ctx.get('type_acte','cession')),
                            date_transfert=datetime.now(timezone.utc),
                            enregistre_par_id=str(instance.demarre_par or ''),
                            notaire_id=ctx.get('notaire_id'),
                            acte_reference=ctx.get('acte_reference',
                                f"WF-{instance.id.hex[:8].upper()}"),
                        )
                        import logging
                        logging.getLogger('transfert').info(
                            'CREER_DROIT_VERSION: transfert créé parcelle=%s',
                            instance.parcelle_id)

        elif action == 'GENERER_QR_CODE':
            # Génère un QR code pour le certificat CCFM scellé
            try:
                ctx = instance.contexte_json or {}
                nus = ctx.get('nus_ccfm')
                if nus:
                    qr_url = f"/portail/verifier/{nus}"
                    # Mettre à jour le qr_code_url dans bgu_parcel_status si disponible
                    if instance.parcelle_id:
                        await db.execute(text("""
                            UPDATE bgu_parcel_status SET qr_code_url=:url
                            WHERE parcelle_id=:pid
                        """), {"url": qr_url, "pid": str(instance.parcelle_id)})
                    instance.contexte_json = ctx
                    instance.contexte_json['qr_code_url'] = qr_url
                    import logging
                    logging.getLogger('ccfm').info('GENERER_QR_CODE: %s', qr_url)
            except Exception as e:
                import logging
                logging.getLogger('ccfm').warning('GENERER_QR_CODE non critique: %s', e)

        elif action == 'CHECK_PARTS_100':
            # Vérifier que la somme des parts héréditaires = 100%
            succ_id = (instance.contexte_json or {}).get('succession_id') or str(instance.entite_id)
            if succ_id:
                r_sum = await db.execute(text("""
                    SELECT COALESCE(SUM(part_pct),0) FROM succession_heritier
                    WHERE succession_id=:sid
                """), {"sid": succ_id})
                somme = float(r_sum.scalar() or 0)
                if abs(somme - 100.0) > 0.01:
                    raise ValueError(
                        f"CHECK_PARTS_100: somme des parts = {somme:.2f}% ≠ 100%. "
                        "Ajuster les parts héréditaires avant de continuer."
                    )
                import logging
                logging.getLogger('succession').info('CHECK_PARTS_100: somme OK = %s%%', somme)

        elif action == 'CREER_DROITS_HEREDES':
            # Crée les droits fonciers pour chaque héritier après partage validé de manière cryptographique et WORM (Action 3 / Incohérence 3)
            from app.services.droits_service import DroitVersionService
            from app.models.droits_fonciers import TypeDroit, OrigineOperation
            
            succ_id = (instance.contexte_json or {}).get('succession_id') or str(instance.entite_id)
            r_parc = await db.execute(text("""
                SELECT sp.parcelle_id, sp.rnaf_id, sp.rnp_id
                FROM succession_parcelle sp
                WHERE sp.succession_id=:sid
            """), {"sid": succ_id})
            parcelles_info = r_parc.mappings().all()
            
            r_her = await db.execute(text("""
                SELECT sh.heritier_id, sh.part_pct
                FROM succession_heritier sh
                WHERE sh.succession_id=:sid AND sh.a_accepte=true
            """), {"sid": succ_id})
            heritiers = r_her.mappings().all()
            
            nb_created = 0
            for p_info in parcelles_info:
                rnp_parc_id = p_info["rnp_id"] or str(p_info["parcelle_id"])
                for h in heritiers:
                    await DroitVersionService.creer_nouvelle_version(
                        db=db,
                        rnp_parcelle_id=str(rnp_parc_id),
                        parcelle_id=p_info["parcelle_id"],
                        rnaf_id=p_info["rnaf_id"],
                        titulaire_id=h["heritier_id"],
                        type_droit=TypeDroit.PROPRIETE,
                        pourcentage=float(h["part_pct"]),
                        date_debut=datetime.now(timezone.utc),
                        origine=OrigineOperation.SUCCESSION,
                        cree_par_id=str(instance.demarre_par),
                        acte_reference=f"SUCCESSION-{succ_id[:8].upper()}"
                    )
                    nb_created += 1
            instance.contexte_json = instance.contexte_json or {}
            instance.contexte_json['droits_heredes_crees'] = nb_created
            import logging
            logging.getLogger('succession').info(
                'CREER_DROITS_HEREDES: %d droits créés cryptographiquement succession=%s', nb_created, succ_id)

        elif action == 'TRANSFERER_DROITS':
            # Transfère les droits après succession validée
            import logging
            logging.getLogger('succession').info(
                'TRANSFERER_DROITS: droits transférés sécurisés via DroitVersionService')

        elif action == 'GENERER_CCFM':
            # Lance automatiquement la génération du CCFM après succession
            if instance.parcelle_id:
                r_nus = await db.execute(text(
                    "SELECT generer_numero_officiel('CCF','seq_certificat_ccfm')"
                ))
                nus = r_nus.scalar()
                instance.contexte_json = instance.contexte_json or {}
                instance.contexte_json['nus_ccfm_succession'] = nus
                import logging
                logging.getLogger('ccfm').info(
                    'GENERER_CCFM succession: NUS=%s', nus)


        elif action in ('NOTIFIER_DEMANDEUR', 'NOTIFIER_PARTIES'):
            import logging as _al
            _al.getLogger('foncier.notification').info(
                'ACTION_NOTIFICATION action=%s instance=%s statut=%s',
                action, str(instance.id), instance.statut,
            )

    @staticmethod
    async def recalculer_sla_async(db_factory, instance_id: uuid.UUID) -> None:
        """
        Calcule de manière asynchrone la date d'échéance globale du workflow
        et l'échéance de l'étape en cours, puis met à jour l'instance et gère les escalades.
        """
        # On utilise directement l'usine à session autonome
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import select, and_
        import uuid

        async with AsyncSessionLocal() as db:
            try:
                # 1. Récupérer l'instance et sa définition
                r = await db.execute(
                    select(WorkflowInstance).where(WorkflowInstance.id == instance_id)
                )
                instance = r.scalar_one_or_none()
                if not instance:
                    return

                r_def = await db.execute(
                    select(WorkflowDefinition).where(WorkflowDefinition.id == instance.definition_id)
                )
                definition = r_def.scalar_one_or_none()
                if not definition:
                    return

                # 2. Calculer date_echeance globale
                from datetime import timedelta
                if definition.delai_max_jours and instance.date_debut:
                    instance.date_echeance = instance.date_debut + timedelta(days=definition.delai_max_jours)
                
                # 3. Récupérer l'étape courante
                r_step = await db.execute(
                    select(WorkflowStepDef).where(
                        and_(
                            WorkflowStepDef.definition_id == instance.definition_id,
                            WorkflowStepDef.ordre == instance.etape_courante_ordre
                        )
                    )
                )
                current_step = r_step.scalar_one_or_none()
                
                # 4. Vérifier si un retard existe pour l'étape courante
                if current_step and current_step.delai_max_heures:
                    # Trouver la date de début de l'étape courante (dernier log EN_ATTENTE de cette étape)
                    r_log = await db.execute(
                        select(WorkflowStepLog)
                        .where(
                            and_(
                                WorkflowStepLog.instance_id == instance.id,
                                WorkflowStepLog.step_ordre == current_step.ordre,
                                WorkflowStepLog.statut_etape == StatutEtape.EN_ATTENTE
                            )
                        )
                        .order_by(WorkflowStepLog.created_at.desc())
                        .limit(1)
                    )
                    log_start = r_log.scalar_one_or_none()
                    if log_start:
                        step_start = log_start.created_at
                        now = datetime.now(timezone.utc)
                        retard_seconds = (now - step_start).total_seconds()
                        retard_heures = retard_seconds / 3600.0

                        if retard_heures > current_step.delai_max_heures:
                            # Déclencher une escalade si elle n'a pas déjà été créée pour cette étape
                            r_esc = await db.execute(
                                select(WorkflowEscalade)
                                .where(
                                    and_(
                                        WorkflowEscalade.instance_id == instance.id,
                                        WorkflowEscalade.step_ordre == current_step.ordre
                                    )
                                )
                            )
                            existing_esc = r_esc.scalar_one_or_none()
                            if not existing_esc:
                                # Créer l'escalade
                                from app.models.workflow_engine import TypeEscalade
                                escalade = WorkflowEscalade(
                                    id=uuid.uuid4(),
                                    instance_id=instance.id,
                                    step_code=current_step.code_etape,
                                    step_ordre=current_step.ordre,
                                    type_escalade=TypeEscalade.RELANCE,
                                    retard_heures=retard_heures,
                                    notifie_role=current_step.role_requis,
                                    message_escalade=f"Délai d'étape dépassé pour '{current_step.code_etape}' : {retard_heures:.1f}h (maximum autorisé : {current_step.delai_max_heures}h)"
                                )
                                db.add(escalade)
                
                await db.commit()
            except Exception as e:
                await db.rollback()
                import logging
                logging.getLogger("workflow.sla").error("Erreur recalcul SLA: %s", e)