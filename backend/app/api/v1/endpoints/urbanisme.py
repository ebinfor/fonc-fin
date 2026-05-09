"""
FONCIER+ — Module Urbanisme
Routes : Arrêtés d'urbanisme, Lotissements, Îlots, Refontes
Corrige BUG-API-URB-02 : premier endpoint pour la hiérarchie spatiale
"""
import uuid
from typing import Optional, List
from datetime import datetime, timezone

from sqlalchemy import text
from app.core.security import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user
from app.models.parcellaire import (
    ArreteUrbanisme, Lotissement, Ilot,
    Region, Commune, StatutLotissement, RefonteLotissement,
)

router = APIRouter(prefix="/urbanisme", tags=["Urbanisme — Arrêtés & Lotissements"])


# ─── Schémas ──────────────────────────────────────────────────────

class ArreteCreateIn(BaseModel):
    commune_id: str
    numero_arrete: str = Field(..., min_length=3)
    code_arrete: str   = Field(..., pattern=r"^AR[0-9]{4}$",
                               description="Format ARnnnnn ex: AR0045")
    date_signature: datetime
    objet: Optional[str] = None


class LotissementCreateIn(BaseModel):
    arrete_id: str
    nom_lotissement: str = Field(..., min_length=3)
    surface_totale_m2: float = Field(..., gt=0)
    nb_ilots_attendus: int = Field(default=50, ge=50, le=200)
    geom_wkt: Optional[str] = None   # POLYGON WKT


class IlotCreateIn(BaseModel):
    lotissement_id: str
    code_ilot: str = Field(..., pattern=r"^[0-9]{3}$",
                           description="Format 3 chiffres ex: 089")
    surface_m2: Optional[float] = Field(None, gt=0,
        description='Surface de l îlot en m² — doit être > 0')
    nb_parcelles_max: int = Field(default=30, ge=1, le=30)
    geom_wkt: Optional[str] = None


class RefonteCreateIn(BaseModel):
    lotissement_ancien_id: str
    lotissement_nouveau_id: str
    nouvel_arrete_id: str
    motif_refonte: str = Field(..., min_length=20)
    surface_ancien_m2: float = Field(..., gt=0)
    surface_nouveau_m2: float = Field(..., gt=0)


# ─── Référentiels ─────────────────────────────────────────────────

@router.get("/regions", response_model=list)
async def lister_regions(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Référentiel des 8 régions du Niger."""
    r = await db.execute(select(Region).order_by(Region.code))
    return [{"id": str(reg.id), "code": reg.code, "nom": reg.nom}
            for reg in r.scalars().all()]


@router.get("/communes", response_model=list)
async def lister_communes(
    region_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Référentiel des communes, filtrable par région."""
    q = select(Commune).order_by(Commune.code)
    if region_id:
        q = q.where(Commune.region_id == region_id)
    r = await db.execute(q)
    return [{"id": str(c.id), "code": c.code, "nom": c.nom,
             "region_id": str(c.region_id), "type": c.type_commune}
            for c in r.scalars().all()]


# ─── Arrêtés d'urbanisme ──────────────────────────────────────────

@router.post("/arretes", status_code=201)
async def creer_arrete(
    payload: ArreteCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "DIRECTEUR_URBANISME", "MINISTRE_URBANISME",
    ])),
):
    """Crée un arrêté d'urbanisme signé."""
    # Vérifier unicité du code
    r = await db.execute(
        select(ArreteUrbanisme).where(ArreteUrbanisme.code_arrete == payload.code_arrete)
    )
    if r.scalar_one_or_none():
        raise HTTPException(409, f"Code arrêté {payload.code_arrete} déjà utilisé")

    arrete = ArreteUrbanisme(
        id=uuid.uuid4(),
        commune_id=payload.commune_id,
        numero_arrete=payload.numero_arrete,
        code_arrete=payload.code_arrete.upper(),
        date_signature=payload.date_signature,
        objet=payload.objet,
        statut="actif",
        signe_par=current_user.id,
    )
    db.add(arrete)
    await db.commit()
    await db.refresh(arrete)
    return {"id": str(arrete.id), "code_arrete": arrete.code_arrete,
            "numero_arrete": arrete.numero_arrete, "statut": arrete.statut}


@router.get("/arretes", response_model=list)
async def lister_arretes(
    commune_id: Optional[str] = Query(None),
    statut: str = Query(default="actif"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "DIRECTEUR_URBANISME", "CHEF_URBANISME",
        "AGENT_URBANISME", "GEOMETRE", "AUDITEUR",
    ])),
):
    filters = [ArreteUrbanisme.statut == statut]
    if commune_id:
        filters.append(ArreteUrbanisme.commune_id == commune_id)
    # ── Filtre acteur (ScopeFilter) ──────────────────────────────
    try:
        from app.core.scope_filter import ScopeFilter as _SF
        _scope = _SF.from_user(current_user)
        if not _scope.peut_voir_national:
            if _scope.region:
                where += " AND a.region = :_scope_region"
                params["_scope_region"] = _scope.region
            elif _scope.entite_id:
                where += " AND a.region = :_scope_entite"
                params["_scope_entite"] = _scope.entite_id
    except Exception:
        pass  # fallback : pas de filtrage si scope indisponible
        r = await db.execute(
        select(ArreteUrbanisme).where(and_(*filters))
        .order_by(ArreteUrbanisme.date_signature.desc())
        .offset((page - 1) * limit).limit(limit)
    )
    return [{"id": str(a.id), "code_arrete": a.code_arrete,
             "numero_arrete": a.numero_arrete,
             "date_signature": a.date_signature.isoformat(),
             "statut": a.statut} for a in r.scalars().all()]


@router.get("/arretes/{arrete_id}", response_model=dict)
async def get_arrete(
    arrete_id: str, db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "DIRECTEUR_URBANISME", "CHEF_URBANISME", "AGENT_URBANISME",
        "GEOMETRE", "NOTAIRE", "CHEF_CCFM", "AUDITEUR",
    ])),
):
    r = await db.execute(
        select(ArreteUrbanisme).where(ArreteUrbanisme.id == arrete_id)
    )
    a = r.scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Arrêté introuvable")
    return {"id": str(a.id), "commune_id": str(a.commune_id),
            "code_arrete": a.code_arrete, "numero_arrete": a.numero_arrete,
            "date_signature": a.date_signature.isoformat(),
            "objet": a.objet, "statut": a.statut, "sha256": a.sha256_document}


# ─── Lotissements ─────────────────────────────────────────────────

@router.post("/lotissements", status_code=201)
async def creer_lotissement(
    payload: LotissementCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "DIRECTEUR_URBANISME", "DIRECTEUR_CADASTRE",
    ])),
):
    """Crée un lotissement rattaché à un arrêté.

    Le trigger tg_wl9_unicite_lotissement_actif bloquera si un
    lotissement actif existe déjà pour cet arrêté.
    Le trigger tg_wl1_bloquer_parcelle_mere bloquera la parcelle mère.
    """
    # Vérifier que l'arrêté existe
    r = await db.execute(
        select(ArreteUrbanisme).where(ArreteUrbanisme.id == payload.arrete_id)
    )
    if not r.scalar_one_or_none():
        raise HTTPException(404, "Arrêté introuvable")

    lotissement = Lotissement(
        id=uuid.uuid4(),
        arrete_id=payload.arrete_id,
        nom_lotissement=payload.nom_lotissement,
        surface_totale_m2=payload.surface_totale_m2,
        nb_ilots_attendus=payload.nb_ilots_attendus,
        geom=payload.geom_wkt,
        statut=StatutLotissement.ACTIF,
    )
    db.add(lotissement)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, f"Création lotissement refusée : {str(e)[:200]}")
    await db.refresh(lotissement)
    return {"id": str(lotissement.id),
            "nom": lotissement.nom_lotissement,
            "statut": lotissement.statut,
            "nb_ilots_attendus": lotissement.nb_ilots_attendus}


@router.get("/lotissements", response_model=list)
async def lister_lotissements(
    arrete_id: Optional[str] = Query(None),
    statut: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "DIRECTEUR_URBANISME", "CHEF_URBANISME", "AGENT_URBANISME",
        "GEOMETRE", "DIRECTEUR_CADASTRE", "AUDITEUR",
    ])),
):
    filters = []
    if arrete_id:
        filters.append(Lotissement.arrete_id == arrete_id)
    if statut:
        filters.append(Lotissement.statut == statut)
    q = select(Lotissement)
    if filters:
        q = q.where(and_(*filters))
    q = q.order_by(Lotissement.created_at.desc()).offset((page - 1) * limit).limit(limit)
    # ── Filtre acteur (ScopeFilter) ──────────────────────────────
    try:
        from app.core.scope_filter import ScopeFilter as _SF
        _scope = _SF.from_user(current_user)
        if not _scope.peut_voir_national:
            if _scope.region:
                where += " AND l.region = :_scope_region"
                params["_scope_region"] = _scope.region
            elif _scope.entite_id:
                where += " AND l.region = :_scope_entite"
                params["_scope_entite"] = _scope.entite_id
    except Exception:
        pass  # fallback : pas de filtrage si scope indisponible
        r = await db.execute(q)
    return [{"id": str(l.id), "nom": l.nom_lotissement,
             "surface_m2": float(l.surface_totale_m2),
             "nb_ilots_attendus": l.nb_ilots_attendus,
             "statut": l.statut} for l in r.scalars().all()]


# ─── Îlots ────────────────────────────────────────────────────────

@router.post("/ilots", status_code=201)
async def creer_ilot(
    payload: IlotCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "GEOMETRE", "DIRECTEUR_CADASTRE", "CHEF_SERVICE_CADASTRE",
    ])),
):
    """Crée un îlot dans un lotissement.
    Contrainte UNIQUE(lotissement_id, code_ilot) vérifiée en base.
    """
    ilot = Ilot(
        id=uuid.uuid4(),
        lotissement_id=payload.lotissement_id,
        code_ilot=payload.code_ilot.zfill(3),
        surface_m2=payload.surface_m2,
        nb_parcelles_max=payload.nb_parcelles_max,
        geom=payload.geom_wkt,
    )
    db.add(ilot)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, f"Création îlot refusée : {str(e)[:200]}")
    await db.refresh(ilot)
    return {"id": str(ilot.id), "code_ilot": ilot.code_ilot,
            "lotissement_id": str(ilot.lotissement_id),
            "nb_parcelles_max": ilot.nb_parcelles_max}


@router.get("/lotissements/{lotissement_id}/ilots", response_model=dict)
async def lister_ilots(
    lotissement_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "GEOMETRE", "DIRECTEUR_CADASTRE", "CHEF_SERVICE_CADASTRE",
        "DIRECTEUR_URBANISME", "AUDITEUR",
    ])),
):
    """Liste tous les îlots d'un lotissement avec le comptage de parcelles."""
    r = await db.execute(
        select(Ilot).where(Ilot.lotissement_id == lotissement_id)
        .order_by(Ilot.code_ilot)
    )
    ilots = r.scalars().all()
    result = []
    for il in ilots:
        from app.models.parcellaire import Parcelle
        r_count = await db.execute(
            select(func.count()).where(Parcelle.ilot_id == il.id)
        )
        nb_parcelles = r_count.scalar() or 0
        result.append({
            "id": str(il.id), "code_ilot": il.code_ilot,
            "surface_m2": float(il.surface_m2) if il.surface_m2 else None,
            "nb_parcelles_max": il.nb_parcelles_max,
            "nb_parcelles_actuelles": nb_parcelles,
            "capacite_restante": il.nb_parcelles_max - nb_parcelles,
        })
    return result


# ─── Refontes ─────────────────────────────────────────────────────

@router.post("/refontes", status_code=201)
async def creer_refonte(
    payload: RefonteCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role([
        "ADMIN", "DIRECTEUR_URBANISME", "MINISTRE_URBANISME",
    ])),
):
    """Enregistre une opération de refonte de lotissement.
    Vérifie la cohérence des surfaces (tolérance ±1%).
    Réservé DIRECTEUR_URBANISME ou MINISTRE.
    """
    ecart = abs(payload.surface_nouveau_m2 - payload.surface_ancien_m2)
    coherence_ok = ecart / payload.surface_ancien_m2 < 0.01
    ecart_pct = round((ecart / payload.surface_ancien_m2) * 100, 4)

    refonte = RefonteLotissement(
        id=uuid.uuid4(),
        lotissement_ancien_id=payload.lotissement_ancien_id,
        lotissement_nouveau_id=payload.lotissement_nouveau_id,
        nouvel_arrete_id=payload.nouvel_arrete_id,
        motif_refonte=payload.motif_refonte,
        surface_ancien_m2=payload.surface_ancien_m2,
        surface_nouveau_m2=payload.surface_nouveau_m2,
        surface_coherence_ok=coherence_ok,
        ecart_surface_pct=ecart_pct,
        autorisee_par=current_user.id,
    )
    db.add(refonte)
    await db.commit()
    return {
        "id": str(refonte.id),
        "coherence_ok": coherence_ok,
        "ecart_surface_pct": ecart_pct,
        "warning": None if coherence_ok else f"Écart surface {ecart_pct}% > 1% — à justifier",
    }


# ══════════════════════════════════════════════════════════════════
# LOTS DE LOTISSEMENT (migration 008)
# ══════════════════════════════════════════════════════════════════

@router.post("/lotissements/{lot_id}/lots", status_code=201)
async def ajouter_lot(
    lot_id: str,
    numero_lot: str = Query(...),
    surface_m2: float = Query(..., gt=0),
    geom_wkt: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_URBANISME","GEOMETRE","TOPOGRAPHE"])),
):
    """Ajoute un lot à un lotissement (migration 008 — lotissement_lot)."""
    import hashlib, uuid as _uuid
    sha = hashlib.sha256(f"{numero_lot}|{surface_m2}|{geom_wkt or ''}".encode()).hexdigest()
    try:
        await db.execute(text("""
            INSERT INTO lotissement_lot
                (id,lotissement_id,numero_lot,surface_m2,geom,
                 geometry_hash,statut)
            VALUES
                (gen_random_uuid(),:lid,:num,:surf,:geom,:sha,'provisoire')
        """), {"lid":lot_id,"num":numero_lot,"surf":surface_m2,
               "geom":geom_wkt,"sha":sha})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"lotissement_id":lot_id,"numero_lot":numero_lot,"sha256":sha}


@router.get("/lotissements/{lot_id}/lots", response_model=dict)
async def lister_lots(
    lot_id: str,
    statut: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Liste les lots d'un lotissement."""
    where = "WHERE lotissement_id=:lid"
    params = {"lid": lot_id}
    if statut: where += " AND statut=:s"; params["s"] = statut
    try:
        r = await db.execute(text(
            f"SELECT * FROM lotissement_lot {where} ORDER BY numero_lot"), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.post("/lotissements/{lot_id}/archiver", status_code=201)
async def archiver_lotissement(
    lot_id: str,
    document_reference: str = Query(...),
    type_document: str = Query(default="arrete_urbanisme",
        description="arrete_urbanisme|plan_cadastral|pv_bornage|publication_jo|certificat_ccfm"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_URBANISME","ARCHIVISTE_ANNF"])),
):
    """Archive un lotissement vers l'ANNF (migration 008 — archive_lotissement)."""
    import hashlib
    sha = hashlib.sha256(f"{lot_id}:{document_reference}".encode()).hexdigest()
    try:
        r_lot = await db.execute(text(
            "SELECT COUNT(*) FROM lotissement_lot WHERE lotissement_id=:lid"), {"lid":lot_id})
        nb_lots = r_lot.scalar() or 0
        await db.execute(text("""
            INSERT INTO archive_lotissement
                (id,lotissement_id,document_reference,type_document,
                 sha256_document,nb_lots_total,created_at)
            VALUES
                (gen_random_uuid(),:lid,:ref,:type,:sha,:nb,NOW())
        """), {"lid":lot_id,"ref":document_reference,"type":type_document,
               "sha":sha,"nb":nb_lots})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"lotissement_id":lot_id,"sha256_document":sha,"nb_lots_archives":nb_lots}


@router.get("/parcelles/{parcelle_id}/versions", response_model=dict)
async def historique_versions_parcelle(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_URBANISME",
        "DIRECTEUR_CADASTRE","AUDITEUR","NOTAIRE"])),
):
    """Historique des versions d'une parcelle (parcel_versions — SHA-256 chaîné)."""
    try:
        r = await db.execute(text("""
            SELECT numero_version,statut_version,surface_originale_m2,
                   motif_modification,sha256_version,created_at
            FROM parcel_versions WHERE parcelle_id=:pid
            ORDER BY numero_version DESC
        """), {"pid":parcelle_id})
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.get("/tableau-de-bord", response_model=list)
async def tableau_de_bord_urbanisme(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_URBANISME",
        "CHEF_URBANISME","AGENT_URBANISME","AUDITEUR"])),
):
    """Vue consolidée du module Urbanisme."""
    stats: dict = {}
    for key, sql in [
        ("arretes_actifs","SELECT COUNT(*) FROM arretes_urbanisme WHERE statut='actif'"),
        ("lotissements_actifs","SELECT COUNT(*) FROM lotissements WHERE statut='actif'"),
        ("lots_disponibles","SELECT COUNT(*) FROM lotissement_lot WHERE statut='provisoire'"),
        ("rnaf_en_attente","SELECT COUNT(*) FROM rnaf_workflow WHERE statut='scelle'"),
        ("rnaf_publies","SELECT COUNT(*) FROM rnaf_workflow WHERE statut='publie'"),
    ]:
        try:
            r = await db.execute(text(sql)); stats[key] = r.scalar() or 0
        except Exception: stats[key] = 0
    return {"acteur":current_user.email,"module":"Urbanisme","stats":stats}


# ── Sous-module DAR ──────────────────────────────────────────────

@router.get("/dar/archives", response_model=list)
async def lister_archives_dar_urbanisme(
    type_document: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_URBANISME",
        "ARCHIVISTE_ANNF","RESPONSABLE_ANNF","AUDITEUR"])),
):
    """Archives DAR du module Urbanisme (annf_archive_links source_module=urbanisme)."""
    where = "WHERE source_module='urbanisme'"
    params: dict = {"l":limit,"o":(page-1)*limit}
    if type_document: where += " AND type_entite=:t"; params["t"]=type_document
    try:
        r = await db.execute(text(f"""
            SELECT ida,entite_id,type_entite,sha256_contenu,scelle_at
            FROM annf_archive_links {where}
            ORDER BY scelle_at DESC LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.get("/zonage/{commune_id}", response_model=dict)
async def get_zonage_commune(
    commune_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Plan de zonage urbain d'une commune (zonage_urbain — migration 011)."""
    try:
        r = await db.execute(text("""
            SELECT code_zone,nom_zone,type_usage_autorise,
                   cos_max,hauteur_max_m,est_active
            FROM zonage_urbain WHERE commune_id=:cid AND est_active=true
            ORDER BY code_zone
        """), {"cid":commune_id})
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE DAR URBANISME — liaison ANNFArchiveService
# ══════════════════════════════════════════════════════════════════

@router.post("/dar/archiver-document", status_code=201)
async def archiver_document_dar_urbanisme(
    type_document: str = Query(...,
        description="rnaf|arrete|lotissement|plan_cadastral|autre"),
    entite_id: str = Query(...),
    entite_table: str = Query(...),
    snapshot: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ["ADMIN","DIRECTEUR_URBANISME","ARCHIVISTE_ANNF"]
    )),
):
    """Archive un document Urbanisme vers l'ANNF via ANNFArchiveService.
    Liaison DAR Urbanisme → ANNF nationale.
    """
    from app.services.workflow_orchestrator import ANNFArchiveService
    from app.models.workflows import TypeArchiveANNF
    type_map = {
        "rnaf": TypeArchiveANNF.RNAF,
        "arrete": TypeArchiveANNF.RNAF,
        "lotissement": TypeArchiveANNF.PLAN_CADASTRAL,
        "plan_cadastral": TypeArchiveANNF.PLAN_CADASTRAL,
        "autre": TypeArchiveANNF.PLAN_CADASTRAL,
    }
    snap = snapshot or {
        "entite_id": entite_id, "entite_table": entite_table,
        "module_source": "urbanisme", "type_document": type_document,
    }
    try:
        archive = await ANNFArchiveService.archiver(
            db=db,
            type_archive=type_map.get(type_document, TypeArchiveANNF.PLAN_CADASTRAL),
            entite_id=entite_id,
            entite_table=entite_table,
            snapshot=snap,
            archive_par_id=str(current_user.id),
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {
        "ida": archive.ida,
        "sha256": archive.sha256_snapshot,
        "module": "urbanisme",
        "sous_module": "DAR",
        "message": "Archivé vers ANNF — scellement via POST /annf/{ida}/sceller",
    }
