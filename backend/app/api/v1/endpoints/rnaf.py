"""
FONCIER+ — Module RNAF complet (WF1)
Routes : CRUD + cycle de vie complet + décisions judiciaires
Corrige BUG-API-URB-01 : ajout de 6 routes manquantes
Corrige BUG-MOD-URB-01 : import depuis app.models.workflows (pas parcellaire)
"""
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy import select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

# BUG-MOD-URB-01 fix : import depuis workflows, pas parcellaire
from app.models.workflows import (
    RNAFWorkflow, StatutRNAF,
    JusticeRNAFDecision, StatutDecisionJusticeRNAF,
)
from app.core.database import get_db
from app.core.security import require_role, get_current_user
from app.services.workflow_orchestrator import RNAFWorkflowService

router = APIRouter(prefix="/urbanisme/rnaf", tags=["Urbanisme — RNAF — WF1"])


# ─── Schémas ──────────────────────────────────────────────────────

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, model_validator

# 1. On déclare la constante UNE SEULE FOIS au niveau global (hors des classes)
ROLES_RNAF = ["ADMIN", "CHEF_CCFM", "AGENT_CCFM"]

class RNAFCreateIn(BaseModel):
    """Créer un workflow RNAF à partir d'un arrêté transmis au JO."""
    rnaf_id:           str           = Field(..., description="UUID de la table rnaf legacy")
    arrete_foncier_id: str           = Field(..., description="UUID de arretes_fonciers")
    commune_id:        Optional[str] = Field(None, description="UUID de la commune concernée")
    region:            Optional[str] = Field(None, description="Code région : NIA|ZDR|MAR|TAH|AGZ|DOS|TLW|DIL")
    jo_parution_id:    Optional[str] = Field(None, description="UUID de la parution JO")
    nombre_parcelles:  Optional[int] = Field(None, ge=1, description="Nombre de parcelles concernées")
    surface_totale_ha: Optional[float] = Field(None, gt=0, description="Surface totale en hectares")


class RNAFAvancerIn(BaseModel):
    """Avancer le workflow RNAF à l'étape suivante."""
    nouveau_statut:     str = Field(..., pattern="^(en_instruction|publie|rejete|archive)$", description="Nouveau statut du workflow RNAF")
    motif:              str = Field(default="", min_length=0, description="Motif de l'avancement")
    publication_jo_id:  str = Field(default="", description="UUID parution JO — obligatoire si nouveau_statut=publie")

    # Correction Pydantic V2 : Validation multi-champs (mode="after")
    @model_validator(mode="after")
    def publie_necessite_jo(self):
        if self.nouveau_statut == "publie" and not self.publication_jo_id:
            raise ValueError("publication_jo_id obligatoire pour passer au statut PUBLIE")
        return self


class JusticeDecisionIn(BaseModel):
    decision_judiciaire_id: str
    numero_decision: str
    motif_decision: str = Field(..., min_length=20)
    retablit_rnaf: bool = False
    annule_rnaf: bool = False
    degele_parcelles: bool = False

    # Correction Pydantic V2 : Validation croisée propre
    @model_validator(mode="after")
    def pas_les_deux(self):
        if self.annule_rnaf and self.retablit_rnaf:
            raise ValueError("retablit_rnaf et annule_rnaf sont mutuellement exclusifs")
        return self


class RNAFOut(BaseModel):
    id: str
    rnaf_id: str
    statut: str
    sha256_publie: Optional[str] = None
    date_soumission: Optional[datetime] = None
    date_publication: Optional[datetime] = None
    date_suspension: Optional[datetime] = None
    motif_suspension: Optional[str] = None

    # Correction Pydantic V2 : Configuration de l'ORM
    model_config = {
        "from_attributes": True
    }

# ─── CRUD de base ─────────────────────────────────────────────────

@router.post("/", status_code=201, response_model=RNAFOut)
async def creer_rnaf_workflow(
    payload: RNAFCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "DIRECTEUR_URBANISME", "CHEF_URBANISME", "AGENT_URBANISME",
    ])),
):
    """Initialise le workflow RNAF pour un arrêté foncier existant."""
    # Vérifier qu'il n'existe pas déjà
    r = await db.execute(
        select(RNAFWorkflow).where(RNAFWorkflow.rnaf_id == payload.rnaf_id)
    )
    if r.scalar_one_or_none():
        raise HTTPException(409, f"Workflow RNAF déjà existant pour rnaf_id {payload.rnaf_id}")

    wf = RNAFWorkflow(
        id=uuid.uuid4(),
        rnaf_id=payload.rnaf_id,
        arrete_foncier_id=payload.arrete_foncier_id,
        statut=StatutRNAF.BROUILLON,
    )
    db.add(wf)
    await db.commit()
    await db.refresh(wf)
    return RNAFOut(
        id=str(wf.id), rnaf_id=str(wf.rnaf_id), statut=wf.statut,
        sha256_publie=wf.sha256_publie,
        date_soumission=wf.date_soumission,
        date_publication=wf.date_publication,
        date_suspension=wf.date_suspension,
        motif_suspension=wf.motif_suspension,
    )


@router.get("/")
async def lister_rnaf(
    statut:   Optional[str] = Query(None),
    commune:  Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit:int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_RNAF)),
):
    """Liste les workflows RNAF filtres par region de l agent."""
    from app.core.scope_filter import ScopeFilter
    scope = ScopeFilter.from_user(current_user)

    where  = "WHERE 1=1"
    params: dict = {"l": limit, "o": (page - 1) * limit}

    if statut:
        where += " AND r.statut::TEXT = :st"
        params["st"] = statut

    if commune:
        where += " AND r.commune = :com"
        params["com"] = commune

    # Filtre région pour les agents urbanisme
    if not scope.peut_voir_national and scope.region:
        where += " AND r.region = :_scope_region"
        params["_scope_region"] = scope.region

    try:
        r = await db.execute(text(f"""
            SELECT r.id, r.code_rnaf, r.commune, r.region, r.statut::TEXT,
                   r.date_creation, r.date_publication,
                   r.nombre_parcelles, r.surface_totale_ha,
                   r.initiateur_id
            FROM rnaf r
            {where}
            ORDER BY r.date_creation DESC
            LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        await db.rollback()
        import logging as _l
        _l.getLogger("rnaf").warning("lister_rnaf: %s", e)
        return []


@router.get("/{rnaf_workflow_id}", response_model=RNAFOut)
async def get_rnaf(
    rnaf_workflow_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "DIRECTEUR_URBANISME", "CHEF_URBANISME",
        "AGENT_URBANISME", "EDITEUR_JO", "AUDITEUR", "NOTAIRE",
    ])),
):
    """Détail d'un workflow RNAF."""
    r = await db.execute(select(RNAFWorkflow).where(RNAFWorkflow.id == rnaf_workflow_id))
    wf = r.scalar_one_or_none()
    if not wf:
        raise HTTPException(404, "Workflow RNAF introuvable")
    return RNAFOut(
        id=str(wf.id), rnaf_id=str(wf.rnaf_id), statut=wf.statut,
        sha256_publie=wf.sha256_publie,
        date_soumission=wf.date_soumission,
        date_publication=wf.date_publication,
        date_suspension=wf.date_suspension,
        motif_suspension=wf.motif_suspension,
    )


# ─── Cycle de vie ─────────────────────────────────────────────────

@router.post("/{rnaf_workflow_id}/avancer")
async def avancer_rnaf(
    rnaf_workflow_id: str,
    payload: RNAFAvancerIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "DIRECTEUR_URBANISME", "MINISTRE_URBANISME",
        "AGENT_URBANISME", "EDITEUR_JO",
    ])),
):
    """Avance le statut RNAF selon les transitions autorisées.

    Transitions :
      BROUILLON → EN_VERIFICATION  (AGENT_URBANISME)
      EN_VERIFICATION → EN_SCELLEMENT  (DIRECTEUR_URBANISME)
      EN_SCELLEMENT → SCELLE  (MINISTRE_URBANISME)
      SCELLE → PUBLIE  (EDITEUR_JO, nécessite publication_jo_id)
      PUBLIE → SUSPENDU  (DIRECTEUR_URBANISME, nécessite motif)
      PUBLIE → ARCHIVE  (DIRECTEUR_URBANISME)
    """
    try:
        nouveau = StatutRNAF(payload.nouveau_statut)
    except ValueError:
        raise HTTPException(422, f"Statut RNAF inconnu : {payload.nouveau_statut}")
    try:
        wf = await RNAFWorkflowService.avancer_statut(
            db=db,
            rnaf_workflow_id=rnaf_workflow_id,
            nouveau_statut=nouveau,
            acteur_id=str(current_user.id),
            motif=payload.motif or None,
            publication_jo_id=payload.publication_jo_id or None,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    await db.commit()
    return {
        "id": str(wf.id),
        "statut": wf.statut,
        "sha256_publie": wf.sha256_publie,
        "date_publication": wf.date_publication.isoformat() if wf.date_publication else None,
    }


@router.post("/{rnaf_workflow_id}/suspendre")
async def suspendre_rnaf(
    rnaf_workflow_id: str,
    motif: str = Query(..., min_length=10),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "DIRECTEUR_URBANISME", "MINISTRE_URBANISME", "JUGE_FONCIER",
    ])),
):
    """Suspend un RNAF publié. Déclenche tg_sync_rnp_from_rnaf → RNP suspendu."""
    try:
        wf = await RNAFWorkflowService.avancer_statut(
            db=db,
            rnaf_workflow_id=rnaf_workflow_id,
            nouveau_statut=StatutRNAF.SUSPENDU,
            acteur_id=str(current_user.id),
            motif=motif,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    await db.commit()
    return {
        "id": str(wf.id), "statut": wf.statut,
        "motif_suspension": wf.motif_suspension,
        "date_suspension": wf.date_suspension.isoformat() if wf.date_suspension else None,
        "impact": "Les RNP liés sont automatiquement passés au statut SUSPENDU via tg_sync_rnp_from_rnaf",
    }


# ─── Décisions judiciaires sur RNAF ──────────────────────────────

@router.post("/{rnaf_workflow_id}/justice", status_code=201)
async def creer_decision_judiciaire_rnaf(
    rnaf_workflow_id: str,
    payload: JusticeDecisionIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "JUGE_FONCIER",
    ])),
):
    """Enregistre une décision judiciaire sur un RNAF suspendu.

    Si retablit_rnaf=True → le RNAF repasse à PUBLIE automatiquement.
    Si annule_rnaf=True → le RNAF passe à ANNULE définitivement + RNP annulé.
    Si degele_parcelles=True → les parcelles liées sont dégelées.
    """
    # Vérifier que le RNAF est suspendu
    r = await db.execute(select(RNAFWorkflow).where(RNAFWorkflow.id == rnaf_workflow_id))
    wf = r.scalar_one_or_none()
    if not wf:
        raise HTTPException(404, "Workflow RNAF introuvable")
    if wf.statut not in (StatutRNAF.SUSPENDU, StatutRNAF.PUBLIE):
        raise HTTPException(
            400,
            f"Statut {wf.statut} — décision judiciaire applicable uniquement sur SUSPENDU ou PUBLIE"
        )

    sha256 = hashlib.sha256(
        f"{rnaf_workflow_id}|{payload.numero_decision}|{payload.motif_decision}|{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()

    decision = JusticeRNAFDecision(
        id=uuid.uuid4(),
        decision_judiciaire_id=payload.decision_judiciaire_id,
        rnaf_workflow_id=rnaf_workflow_id,
        statut_decision=StatutDecisionJusticeRNAF.EN_INSTRUCTION,
        numero_decision=payload.numero_decision,
        motif_decision=payload.motif_decision,
        sha256_decision=sha256,
        retablit_rnaf=payload.retablit_rnaf,
        annule_rnaf=payload.annule_rnaf,
        degele_parcelles=payload.degele_parcelles,
        president_id=current_user.id,
        date_decision=datetime.now(timezone.utc),
    )
    db.add(decision)
    await db.flush()

    # Appliquer les effets immédiats
    if payload.retablit_rnaf:
        try:
            wf = await RNAFWorkflowService.avancer_statut(
                db=db, rnaf_workflow_id=rnaf_workflow_id,
                nouveau_statut=StatutRNAF.PUBLIE,
                acteur_id=str(current_user.id),
                motif=f"Rétablissement par décision judiciaire {payload.numero_decision}",
            )
        except ValueError as e:
            raise HTTPException(422, str(e))
        decision.statut_decision = StatutDecisionJusticeRNAF.VALIDEE

    elif payload.annule_rnaf:
        try:
            wf = await RNAFWorkflowService.avancer_statut(
                db=db, rnaf_workflow_id=rnaf_workflow_id,
                nouveau_statut=StatutRNAF.ANNULE,
                acteur_id=str(current_user.id),
                motif=f"Annulation par décision judiciaire {payload.numero_decision}",
            )
        except ValueError as e:
            raise HTTPException(422, str(e))
        decision.statut_decision = StatutDecisionJusticeRNAF.ANNULATION

    await db.commit()
    return {
        "decision_id": str(decision.id),
        "numero_decision": decision.numero_decision,
        "statut_decision": decision.statut_decision,
        "rnaf_statut": wf.statut,
        "sha256": sha256,
        "impact_rnp": "Synchronisation automatique via tg_sync_rnp_from_rnaf",
    }


@router.get("/{rnaf_workflow_id}/decisions")
async def lister_decisions_judiciaires(
    rnaf_workflow_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "JUGE_FONCIER", "GREFFIER", "DIRECTEUR_URBANISME", "AUDITEUR",
    ])),
):
    """Liste les décisions judiciaires sur un RNAF."""
    r = await db.execute(
        select(JusticeRNAFDecision)
        .where(JusticeRNAFDecision.rnaf_workflow_id == rnaf_workflow_id)
        .order_by(JusticeRNAFDecision.created_at.desc())
    )
    decisions = r.scalars().all()
    return [{
        "id": str(d.id),
        "numero_decision": d.numero_decision,
        "statut_decision": d.statut_decision,
        "retablit_rnaf": d.retablit_rnaf,
        "annule_rnaf": d.annule_rnaf,
        "degele_parcelles": d.degele_parcelles,
        "date_decision": d.date_decision.isoformat() if d.date_decision else None,
        "sha256": d.sha256_decision,
    } for d in decisions]
