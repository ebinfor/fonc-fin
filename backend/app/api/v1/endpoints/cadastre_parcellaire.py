"""
FONCIER+ v3.4.7 — Endpoint Cadastre Parcellaire
Routes : /v1/cadastre/parcelles — CRUD + subdivision + annulation + antifraude
"""

import uuid
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, validator
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user
from app.models.parcellaire import (
    ConflitParcellaire, GraviteConflit, NicadRegistry, Parcelle,
    ParcelLineage, ParcelVersion, StatutConflit, StatutParcelle,
)
from app.services.parcellaire_service import (
    AnnulationService, AntiFraudeService, NicadService,
    SubdivisionService, VersioningService,
)

router = APIRouter(prefix="/cadastre/parcelles", tags=["Cadastre — Parcellaire"])


# ─────────────────────────────────────────────────────────────
# SCHEMAS PYDANTIC
# ─────────────────────────────────────────────────────────────

class ParcelleCreate(BaseModel):
    ilot_id: str = Field(..., description="UUID de l'îlot parent")
    surface_m2: float = Field(..., gt=0, description="Surface en m²")
    geom_wkt: str = Field(..., description="Géométrie WKT POLYGON")
    motif: str = Field(..., min_length=10, description="Motif de création")

class ParcelleOut(BaseModel):
    id: str
    nicad: str
    statut: str
    surface_m2: float
    is_gele: bool
    version_numero: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True

class SubdivisionRequest(BaseModel):
    surface_a_m2: float = Field(..., gt=0)
    surface_b_m2: float = Field(..., gt=0)
    geom_a_wkt: str
    geom_b_wkt: str
    motif: str = Field(..., min_length=10)

    @validator("surface_b_m2")
    def surfaces_positives(cls, v, values):
        if v <= 0:
            raise ValueError("surface_b_m2 doit être > 0")
        return v

class AnnulationRequest(BaseModel):
    motif: str = Field(..., min_length=20)
    base_juridique: str = Field(..., min_length=5)
    preuve_document_path: str
    valideur2_id: str = Field(..., description="UUID du valideur niveau 2 (Autorité centrale)")

class ConflitOut(BaseModel):
    id: str
    type_conflit: str
    gravite: str
    statut: str
    surface_intersection_m2: Optional[float]
    description: Optional[str]
    detecte_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────

@router.get("/", response_model=List[ParcelleOut])
async def lister_parcelles(
    ilot_id: Optional[str] = Query(None),
    statut: Optional[str] = Query(None),
    nicad: Optional[str] = Query(None),
    include_annulees: bool = Query(False),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "ADMIN_CADASTRE", "DIRECTEUR_CADASTRE",
        "CHEF_SERVICE_CADASTRE", "GEOMETRE", "SECRETARIAT_CADASTRE",
        "DIRECTEUR_URBANISME", "TOPOGRAPHE",
    ])),
):
    """Liste les parcelles avec filtres. Exclut les ANNULEES par défaut."""
    filters = []
    if ilot_id:
        filters.append(Parcelle.ilot_id == ilot_id)
    if statut:
        filters.append(Parcelle.statut == statut)
    if nicad:
        filters.append(Parcelle.nicad.ilike(f"%{nicad}%"))
    if not include_annulees:
        filters.append(Parcelle.statut != StatutParcelle.ANNULE)

    q = select(Parcelle)
    if filters:
        q = q.where(and_(*filters))
    q = q.offset((page - 1) * limit).limit(limit)

    # ── ScopeFilter : agent ne voit que sa région ──
    try:
        from app.core.scope_filter import ScopeFilter as _SF
        _sc = _SF.from_user(current_user)
        if not _sc.peut_voir_national and _sc.region:
            q = q.where(Parcelle.region == _sc.region)
    except Exception:
        pass

    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{parcelle_id}", response_model=ParcelleOut)
async def get_parcelle(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "ADMIN_CADASTRE", "DIRECTEUR_CADASTRE",
        "CHEF_SERVICE_CADASTRE", "GEOMETRE", "SECRETARIAT_CADASTRE",
        "DIRECTEUR_URBANISME", "TOPOGRAPHE", "NOTAIRE", "BANQ_DIRECTEUR",
    ])),
):
    """Récupère une parcelle par UUID ou NICAD."""
    # Chercher par UUID d'abord, puis par NICAD
    q = select(Parcelle).where(
        or_(Parcelle.id == parcelle_id, Parcelle.nicad == parcelle_id)
    )
    result = await db.execute(q)
    parcelle = result.scalar_one_or_none()
    if not parcelle:
        raise HTTPException(status_code=404, detail="Parcelle introuvable")
    return parcelle


@router.post("/", response_model=ParcelleOut, status_code=status.HTTP_201_CREATED)
async def creer_parcelle(
    payload: ParcelleCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "ADMIN_CADASTRE", "GEOMETRE", "CHEF_SERVICE_CADASTRE",
    ])),
):
    """
    Crée une nouvelle parcelle avec statut EN_ATTENTE_VALIDATION.
    NICAD généré automatiquement. Version initiale créée.
    """
    from app.models.parcellaire import Ilot, ArreteUrbanisme, Commune, Region

    # Récupérer l'îlot et construire le NICAD
    ilot_result = await db.execute(
        select(Ilot).where(Ilot.id == payload.ilot_id)
    )
    ilot = ilot_result.scalar_one_or_none()
    if not ilot:
        raise HTTPException(status_code=404, detail="Îlot introuvable")

    # Récupérer la chaîne région→commune→arrêté→lotissement→îlot
    # (simplifié — en production : jointure complète)
    code_parcelle = await NicadService.next_parcelle_code(db, payload.ilot_id)

    # TODO: récupérer codes depuis la chaîne hiérarchique
    # Pour l'exemple, codes fictifs — à remplacer par jointure complète
    nicad = NicadService.generate(
        code_region="01",    # À récupérer depuis Région
        code_commune="01",   # À récupérer depuis Commune
        code_arrete="AR0001",  # À récupérer depuis ArreteUrbanisme
        code_ilot=ilot.code_ilot,
        code_parcelle=code_parcelle,
    )

    # Vérifier unicité NICAD
    existing = await db.execute(select(Parcelle).where(Parcelle.nicad == nicad))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"NICAD {nicad} déjà existant")

    geojson_id = f"geojson-{nicad.replace('-', '_')}-{uuid.uuid4().hex[:8]}"

    parcelle = Parcelle(
        id=uuid.uuid4(),
        ilot_id=payload.ilot_id,
        nicad=nicad,
        geojson_id=geojson_id,
        statut=StatutParcelle.EN_ATTENTE_VALIDATION,
        surface_m2=payload.surface_m2,
        geom=payload.geom_wkt,
        cree_par=current_user.id,
    )
    db.add(parcelle)
    try:
        await db.flush()

        # Créer la version initiale
        await VersioningService.create_new_version(
            db, parcelle, payload.motif, payload.geom_wkt, payload.surface_m2, current_user.id
        )

        # Enregistrer dans nicad_registry
        parsed = NicadService.parse(nicad)
        db.add(NicadRegistry(
            nicad=nicad,
            parcelle_id=parcelle.id,
            genere_par=current_user.id,
            **parsed,
        ))

        await db.commit()
        await db.refresh(parcelle)
        return parcelle
    except Exception as _e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(_e)[:200])


@router.post("/{parcelle_id}/subdiviser", status_code=status.HTTP_201_CREATED)
async def subdiviser_parcelle(
    parcelle_id: str,
    payload: SubdivisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "ADMIN_CADASTRE", "CHEF_SERVICE_CADASTRE", "DIRECTEUR_CADASTRE",
    ])),
):
    """
    Subdivise une parcelle en 2 filles (a et b).
    Règles : 1 niveau max, 2 filles max, parent → SUBDIVISEE.
    """
    result = await db.execute(select(Parcelle).where(Parcelle.id == parcelle_id))
    parcelle = result.scalar_one_or_none()
    if not parcelle:
        raise HTTPException(status_code=404, detail="Parcelle introuvable")

    try:
        fille_a, fille_b = await SubdivisionService.perform_subdivision(
            db=db,
            parent=parcelle,
            surface_a_m2=payload.surface_a_m2,
            surface_b_m2=payload.surface_b_m2,
            geom_a_wkt=payload.geom_a_wkt,
            geom_b_wkt=payload.geom_b_wkt,
            autorise_par_id=current_user.id,
            motif=payload.motif,
        )
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(e))

    await db.commit()
    return {
        "message": f"Parcelle {parcelle.nicad} subdivisée avec succès",
        "parent_nicad": parcelle.nicad,
        "fille_a": {"nicad": fille_a.nicad, "id": str(fille_a.id)},
        "fille_b": {"nicad": fille_b.nicad, "id": str(fille_b.id)},
    }


@router.post("/{parcelle_id}/annuler")
async def annuler_parcelle(
    parcelle_id: str,
    payload: AnnulationRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "DIRECTEUR_URBANISME", "MINISTRE_URBANISME",
    ])),
):
    """
    Annule une parcelle (statut ANNULE). Jamais de DELETE physique.
    Requiert validation multi-niveaux : courant (niveau 1) + valideur2_id (niveau 2).
    """
    result = await db.execute(select(Parcelle).where(Parcelle.id == parcelle_id))
    parcelle = result.scalar_one_or_none()
    if not parcelle:
        raise HTTPException(status_code=404, detail="Parcelle introuvable")

    try:
        annulation = await AnnulationService.annuler_parcelle(
            db=db,
            parcelle=parcelle,
            motif=payload.motif,
            base_juridique=payload.base_juridique,
            preuve_document_path=payload.preuve_document_path,
            valideur1_id=current_user.id,
            valideur2_id=payload.valideur2_id,
        )
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(e))

    await db.commit()
    return {
        "message": f"Parcelle {parcelle.nicad} annulée (statut ANNULE — historique conservé)",
        "nicad": parcelle.nicad,
        "annulation_id": str(annulation.id),
        "sha256_preuve": annulation.sha256_preuve,
    }


@router.get("/{parcelle_id}/versions")
async def get_versions(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "ADMIN_CADASTRE", "DIRECTEUR_CADASTRE", "CHEF_SERVICE_CADASTRE",
        "DIRECTEUR_URBANISME", "AUDITEUR",
    ])),
):
    """Retourne l'historique complet des versions d'une parcelle."""
    result = await db.execute(
        select(ParcelVersion)
        .where(ParcelVersion.parcelle_id == parcelle_id)
        .order_by(ParcelVersion.numero_version.asc())
    )
    versions = result.scalars().all()
    return [
        {
            "id": str(v.id),
            "numero_version": v.numero_version,
            "statut_version": v.statut_version,
            "surface_originale_m2": float(v.surface_originale_m2),
            "motif_modification": v.motif_modification,
            "sha256_version": v.sha256_version,
            "created_at": v.created_at.isoformat(),
        }
        for v in versions
    ]


@router.get("/{parcelle_id}/conflits", response_model=List[ConflitOut])
async def get_conflits(
    parcelle_id: str,
    gravite: Optional[str] = Query(None, description="critique | a_verifier"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "ADMIN_CADASTRE", "DIRECTEUR_CADASTRE", "CHEF_SERVICE_CADASTRE",
        "DIRECTEUR_URBANISME", "AUDITEUR",
    ])),
):
    """Retourne les conflits antifraude d'une parcelle."""
    filters = [ConflitParcellaire.parcelle_a_id == parcelle_id]
    if gravite:
        filters.append(ConflitParcellaire.gravite == gravite)

    result = await db.execute(
        select(ConflitParcellaire).where(and_(*filters))
        .order_by(ConflitParcellaire.detecte_at.desc())
    )
    return result.scalars().all()


@router.get("/{parcelle_id}/check-acte")
async def check_peut_acte(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "NOTAIRE", "BANQ_DIRECTEUR", "BANQ_AGENT",
        "CHEF_CCFM", "DIRECTEUR_URBANISME",
    ])),
):
    """
    Vérifie si une parcelle peut faire l'objet d'un acte notarial ou hypothèque.
    Gate antifraude : statut ACTIVE + non gelée + aucun conflit critique.
    """
    can, reason = await AntiFraudeService.check_parcelle_can_have_acte(db, parcelle_id)
    return {
        "parcelle_id": parcelle_id,
        "peut_acte": can,
        "raison": reason if not can else "Parcelle éligible — aucun blocage détecté",
    }


@router.get("/nicad/{nicad}", response_model=dict)
async def get_by_nicad(
    nicad: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "ADMIN_CADASTRE", "DIRECTEUR_CADASTRE", "CHEF_SERVICE_CADASTRE",
        "GEOMETRE", "SECRETARIAT_CADASTRE", "DIRECTEUR_URBANISME",
        "TOPOGRAPHE", "NOTAIRE", "BANQ_DIRECTEUR", "CHEF_CCFM",
    ])),
):
    """Récupère une parcelle par son NICAD exact."""
    if not NicadService.validate(nicad):
        raise HTTPException(status_code=422, detail=f"Format NICAD invalide : {nicad!r}")

    result = await db.execute(select(Parcelle).where(Parcelle.nicad == nicad))
    parcelle = result.scalar_one_or_none()
    if not parcelle:
        raise HTTPException(status_code=404, detail=f"NICAD {nicad} introuvable")
    return parcelle


@router.get("/conflits/ouverts", response_model=list)
async def lister_conflits_ouverts(
    gravite: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "ADMIN_CADASTRE", "DIRECTEUR_CADASTRE",
        "DIRECTEUR_URBANISME", "AUDITEUR",
    ])),
):
    """Liste tous les conflits antifraude ouverts ou bloqués — tableau de bord supervision."""
    filters = [
        ConflitParcellaire.statut.in_([StatutConflit.OUVERT, StatutConflit.BLOQUE])
    ]
    if gravite:
        filters.append(ConflitParcellaire.gravite == gravite)

    result = await db.execute(
        select(ConflitParcellaire).where(and_(*filters))
        .order_by(ConflitParcellaire.detecte_at.desc())
        .offset((page - 1) * limit).limit(limit)
    )
    conflits = result.scalars().all()
    return {
        "total": len(conflits),
        "page": page,
        "conflits": [
            {
                "id": str(c.id),
                "parcelle_a_id": str(c.parcelle_a_id),
                "type_conflit": c.type_conflit,
                "gravite": c.gravite,
                "statut": c.statut,
                "surface_intersection_m2": float(c.surface_intersection_m2 or 0),
                "description": c.description,
                "detecte_at": c.detecte_at.isoformat(),
            }
            for c in conflits
        ],
    }
