"""
FONCIER+ — Module Workflows (moteur universel)
Expose WorkflowEngine.demarrer, valider_etape, rejeter_etape,
suspendre, get_etat pour les 33 workflows nationaux.
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, async_session_factory
from app.core.security import require_role, get_current_user
from app.models.workflow_engine import WorkflowInstance, StatutInstance
from app.services.workflow_engine import WorkflowEngine

router = APIRouter(prefix="/workflows", tags=["Workflows — Moteur"])


_ROLES_LECTURE = [
    "ADMIN", "DIRECTEUR_CADASTRE", "DIRECTEUR_URBANISME", "CHEF_CCFM", "NOTAIRE",
    "JUGE_FONCIER", "OPERATEUR_CADASTRE", "INGENIEUR_CADASTRE", "GEOMETRE_CADASTRE",
    "RECEVEUR_ENREG", "EDITEUR_JO", "AUDITEUR", "GREFFIER_TGI", "OPERATEUR_CCFM",
    "TOPOGRAPHE_CCFM"
]


class DemarrerIn(BaseModel):
    type_workflow: str = Field(..., min_length=2,
        description="Code workflow : TRANSFERT|CCFM|WF17|... ou WF1-WF33")
    entite_type: str  = Field(..., min_length=2,
        description="Type d entité : rnaf|rnp_demande|demande_ccfm|parcelle|…")
    entite_id: str
    nicad: Optional[str] = None
    parcelle_id: Optional[str] = None
    contexte: dict = {}


class ValiderIn(BaseModel):
    commentaire:     Optional[str]  = Field(None, min_length=5,
        description='Commentaire de validation — min 5 caractères si fourni')
    documents:       Optional[List[str]] = None
    snapshot_entite: Optional[dict]  = None


class RejeterIn(BaseModel):
    motif: str = Field(..., min_length=10)


class SuspendreIn(BaseModel):
    motif: str = Field(..., min_length=10)


# ─── Démarrage ────────────────────────────────────────────────────

@router.post("/demarrer", status_code=201)
async def demarrer_workflow(
    payload: DemarrerIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Lance une instance de workflow.
    L'appelant doit avoir le rôle requis pour l'étape 1 (vérifié par le moteur).
    Le moteur bloque si une instance active existe déjà pour la même entité.
    """
    try:
        instance = await WorkflowEngine.demarrer(
            db=db,
            type_workflow=payload.type_workflow,
            entite_type=payload.entite_type,
            entite_id=payload.entite_id,
            demarre_par_id=str(current_user.id),
            nicad=payload.nicad,
            parcelle_id=payload.parcelle_id,
            contexte=payload.contexte,
        )
    except ValueError as e:
        await db.rollback()
        raise HTTPException(422, str(e))
    await db.commit()
    background_tasks.add_task(WorkflowEngine.recalculer_sla_async, async_session_factory, instance.id)
    return {
        "id": str(instance.id),
        "statut": instance.statut,
        "etape_courante": instance.etape_courante_code,
        "attendu_de_role": instance.attendu_de_role,
    }


# ─── Lecture état ─────────────────────────────────────────────────

@router.get("/{instance_id}", response_model=dict)
async def get_etat_workflow(
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Retourne l'état complet d'une instance : étape, historique, escalades."""
    try:
        return await WorkflowEngine.get_etat(db, instance_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/", response_model=list)
async def lister_instances(
    type_workflow: Optional[str] = Query(None),
    statut:        Optional[str] = Query(None),
    entite_id:     Optional[str] = Query(None),
    en_retard:     Optional[bool]= Query(None),
    todo_only:     Optional[bool]= Query(None),
    page:  int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ROLES_LECTURE)),
):
    """Liste les instances de workflow filtrees par acteur."""
    from app.core.scope_filter import ScopeFilter
    scope = ScopeFilter.from_user(current_user)

    where  = "WHERE 1=1"
    params: dict = {"l": limit, "o": (page - 1) * limit}

    if type_workflow:
        where += " AND wi.workflow_code = :twf"
        params["twf"] = type_workflow

    if statut:
        where += " AND wi.statut::TEXT = :st"
        params["st"] = statut

    if entite_id:
        where += " AND wi.entite_id = :eid::uuid"
        params["eid"] = entite_id

    if en_retard is True:
        where += " AND wi.date_echeance < NOW() AND wi.statut::TEXT NOT IN ('TERMINE','ANNULE')"

    if todo_only is True:
        # Action 4 / Incohérence 4 : Filtre pour voir uniquement les dossiers en attente du rôle principal OU du rôle de secours (backup)
        where += """ AND (
            wi.attendu_de_role = :user_role
            OR EXISTS (
                SELECT 1 FROM workflow_step_def wsd
                WHERE wsd.definition_id = wi.definition_id
                  AND wsd.code_etape = wi.etape_courante
                  AND wsd.role_backup = :user_role
            )
        ) AND wi.statut::TEXT = 'EN_COURS'"""
        params["user_role"] = current_user.role

    # Filtre par acteur : chaque operateur voit ses propres workflows
    if not scope.peut_voir_national:
        uid = scope.user_id
        # L agent voit les workflows ou il est initiateur OU agent courant
        where += " AND (wi.initiateur_id = :_uid OR wi.agent_courant_id = :_uid"
        if scope.region:
            where += " OR wi.region = :_region"
            params["_region"] = scope.region
        where += ")"
        params["_uid"] = uid

    try:
        r = await db.execute(text(f"""
            SELECT wi.id, wi.workflow_code, wi.statut::TEXT, wi.etape_courante,
                   wi.entite_type, wi.entite_id, wi.region,
                   wi.initiateur_id, wi.agent_courant_id,
                   wi.date_creation, wi.date_echeance, wi.date_fin
            FROM workflow_instances wi
            {where}
            ORDER BY wi.date_creation DESC
            LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        await db.rollback()
        import logging as _l
        _l.getLogger("workflows").warning("lister_instances: %s", e)
        return []


@router.post("/{instance_id}/valider", response_model=dict)
async def valider_etape(
    instance_id: str,
    payload: ValiderIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Valide l'étape courante. Le moteur vérifie que le rôle de l'appelant est autorisé."""
    try:
        instance = await WorkflowEngine.valider_etape(
            db=db,
            instance_id=instance_id,
            acteur_id=str(current_user.id),
            role=current_user.role,
            commentaire=payload.commentaire,
            documents=payload.documents,
            snapshot_entite=payload.snapshot_entite,
        )
    except ValueError as e:
        await db.rollback()
        raise HTTPException(422, str(e))
    await db.commit()
    background_tasks.add_task(WorkflowEngine.recalculer_sla_async, async_session_factory, instance.id)
    return {
        "id": str(instance.id),
        "statut": instance.statut,
        "etape_courante": instance.etape_courante_code,
        "sha256_completion": instance.sha256_completion,
    }


@router.post("/{instance_id}/rejeter", response_model=dict)
async def rejeter_etape(
    instance_id: str,
    payload: RejeterIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Rejette l'étape courante. Le moteur vérifie le rôle."""
    try:
        instance = await WorkflowEngine.rejeter_etape(
            db=db,
            instance_id=instance_id,
            acteur_id=str(current_user.id),
            role=current_user.role,
            motif=payload.motif,
        )
    except ValueError as e:
        await db.rollback()
        raise HTTPException(422, str(e))
    await db.commit()
    background_tasks.add_task(WorkflowEngine.recalculer_sla_async, async_session_factory, instance.id)
    return {"id": str(instance.id), "statut": instance.statut,
            "motif_rejet": instance.motif_rejet}


@router.post("/{instance_id}/suspendre", response_model=dict)
async def suspendre_workflow(
    instance_id: str,
    payload: SuspendreIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "DIRECTEUR_CADASTRE", "DIRECTEUR_URBANISME",
        "CHEF_CCFM", "JUGE_FONCIER",
    ])),
):
    """Suspend un workflow EN_COURS et pose un transaction_block sur la parcelle."""
    try:
        instance = await WorkflowEngine.suspendre(
            db=db,
            instance_id=instance_id,
            acteur_id=str(current_user.id),
            motif=payload.motif,
        )
    except ValueError as e:
        await db.rollback()
        raise HTTPException(422, str(e))
    await db.commit()
    return {"id": str(instance.id), "statut": instance.statut}


@router.get("/{instance_id}/historique", response_model=list)
async def historique_workflow(
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "AUDITEUR", "DIRECTEUR_CADASTRE", "CHEF_CCFM",
        "JUGE_FONCIER", "NOTAIRE",
    ])),
):
    """Retourne l'historique des étapes (log d'audit du workflow)."""
    try:
        etat = await WorkflowEngine.get_etat(db, instance_id)
        return {"instance_id": instance_id, "historique": etat["historique"]}
    except ValueError as e:
        raise HTTPException(404, str(e))
