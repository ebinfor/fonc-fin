from app.services.workflow_orchestrator import ANNFArchiveService
"""
FONCIER+ — Module Cadastre enrichi (BUG-API-CAD-01 à 04)
"""
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import text
import uuid
from app.core.security import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, and_, func, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user
from app.models.parcellaire import (
    Parcelle, StatutParcelle,
    NicadRegistry, ConflitParcellaire, StatutConflit,
)
from app.services.parcellaire_service import NicadService

router = APIRouter(prefix="/cadastre", tags=["Cadastre — Enrichi"])

ROLES_CADASTRE = ["ADMIN","ADMIN_CADASTRE","DIRECTEUR_CADASTRE","CHEF_SERVICE_CADASTRE","GEOMETRE"]
ROLES_DIR      = ["ADMIN","ADMIN_CADASTRE","DIRECTEUR_CADASTRE"]



class ConflitCadastreOut(BaseModel):
    id: str; parcelle_a_id: str; parcelle_b_id: Optional[str]=None
    type_conflit: Optional[str]=None; statut: str; region: Optional[str]=None
    class Config: from_attributes=True

class NicadOut(BaseModel):
    id: str; nicad: str; lot: Optional[str]=None; parcelle: Optional[str]=None
    region: Optional[str]=None; statut: Optional[str]=None
    class Config: from_attributes=True

@router.get("/tableau-de-bord", response_model=dict,
            summary="Liste tableau de bord")
async def tableau_de_bord_cadastre(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_DIR + ["CHEF_SERVICE_CADASTRE"])),
):
    stats = {}
    
    # 1. Statistiques des Parcelles
    try:
        r = await db.execute(text("SELECT statut, COUNT(*) nb FROM parcelles GROUP BY statut"))
        stats["parcelles"] = {row.statut: row.nb for row in r}
    except Exception:
        stats["parcelles"] = {}
        
    # 2. Statistiques des Conflits
    try:
        r = await db.execute(text(
            "SELECT gravite, COUNT(*) nb FROM conflits_parcellaires WHERE statut='ouvert' GROUP BY gravite"
        ))
        stats["conflits_ouverts"] = {row.gravite: row.nb for row in r}
    except Exception:
        stats["conflits_ouverts"] = {}
        
    # 3. Statistiques du Registre NICAD
    try:
        r = await db.execute(text(
            "SELECT COUNT(*) FROM nicad_registry WHERE created_at >= date_trunc('month',CURRENT_DATE)"
        ))
        stats["nicad_ce_mois"] = r.scalar() or 0
    except Exception:
        stats["nicad_ce_mois"] = 0
        
    # 4. Statistiques des Workflows RNP
    try:
        r = await db.execute(text(
            "SELECT COUNT(*) FROM workflow_instance wi JOIN workflow_definition wd ON wd.id=wi.definition_id"
            " WHERE wd.type_workflow='RNP' AND wi.statut='en_cours'"
        ))
        stats["wf_rnp_en_cours"] = r.scalar() or 0
    except Exception:
        stats["wf_rnp_en_cours"] = 0

    # Retour final de l'objet
    return {
        "acteur": current_user.id,
        "role": current_user.role,
        "module": "Cadastre",
        "stats": stats
    }
@router.post("/parcelles/{parcelle_id}/valider")
async def valider_parcelle(
    parcelle_id: str,
    motif: str = Query(..., min_length=5),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_DIR)),
):
    """Valide une parcelle EN_ATTENTE_VALIDATION → ACTIVE."""
    r = await db.execute(select(Parcelle).where(Parcelle.id == parcelle_id))
    parcelle = r.scalar_one_or_none()
    if not parcelle:
        raise HTTPException(404, "Parcelle introuvable")
    if parcelle.statut != StatutParcelle.EN_ATTENTE_VALIDATION:
        raise HTTPException(400, f"Statut {parcelle.statut} — seul EN_ATTENTE_VALIDATION validable")

    r_conf = await db.execute(
        select(func.count(ConflitParcellaire.id)).where(
            and_(ConflitParcellaire.parcelle_a_id == parcelle_id,
                 ConflitParcellaire.gravite == "critique",
                 ConflitParcellaire.statut == StatutConflit.OUVERT)
        )
    )
    if (r_conf.scalar() or 0) > 0:
        raise HTTPException(400, "Validation impossible : conflits critiques ouverts")

    r_nicad = await db.execute(
        select(NicadRegistry).where(NicadRegistry.parcelle_id == parcelle_id)
    )
    if not r_nicad.scalar_one_or_none():
        raise HTTPException(400, "Validation impossible : NICAD non enregistré")

    parcelle.statut = StatutParcelle.ACTIVE
    parcelle.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"parcelle_id": parcelle_id, "nicad": parcelle.nicad, "statut": "ACTIVE",
            "valide_par": current_user.id}


@router.get("/nicad/registre", response_model=list,
    summary="Liste nicad/registre")
async def lister_nicad_registre(
    code_region: Optional[str] = Query(None),
    code_commune: Optional[str] = Query(None),
    code_arrete: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_CADASTRE + ["AUDITEUR","NOTAIRE","CHEF_CCFM"])),
):
    """Recherche dans le registre central des NICAD."""
    filters = []
    if code_region:  filters.append(NicadRegistry.code_region  == code_region.zfill(2))
    if code_commune: filters.append(NicadRegistry.code_commune == code_commune.zfill(2))
    if code_arrete:  filters.append(NicadRegistry.code_arrete  == code_arrete.upper())
    q = select(NicadRegistry)
    if filters: q = q.where(and_(*filters))
    q = q.order_by(NicadRegistry.nicad).offset((page-1)*limit).limit(limit)
    # ── Filtre acteur (ScopeFilter) ──────────────────────────────
    try:
        from app.core.scope_filter import ScopeFilter as _SF
        _scope = _SF.from_user(current_user)
        if not _scope.peut_voir_national:
            if _scope.region:
                where += " AND n.region = :_scope_region"
                params["_scope_region"] = _scope.region
            elif _scope.entite_id:
                where += " AND n.region = :_scope_entite"
                params["_scope_entite"] = _scope.entite_id
    except Exception:
        pass  # fallback : pas de filtrage si scope indisponible
        r = await db.execute(q)
    return [{"nicad": e.nicad, "parcelle_id": str(e.parcelle_id),
             "code_region": e.code_region, "code_commune": e.code_commune,
             "code_arrete": e.code_arrete, "code_ilot": e.code_ilot,
             "code_parcelle": e.code_parcelle, "suffixe": e.suffixe,
             "created_at": e.created_at.isoformat()} for e in r.scalars().all()]


@router.get("/nicad/{nicad}/decomposer", response_model=dict)
async def decomposer_nicad(
    nicad: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Décompose un NICAD en ses segments avec enrichissement DB."""
    if not NicadService.validate(nicad):
        raise HTTPException(422, f"NICAD invalide : {nicad!r}")
    try:
        parts = NicadService.parse(nicad)
    except ValueError as e:
        raise HTTPException(422, str(e))
    enrichi = dict(parts)
    try:
        r = await db.execute(
            text("SELECT nom FROM communes WHERE code = :cc LIMIT 1"),
            {"cc": parts["code_commune"]}
        )
        row = r.first()
        enrichi["nom_commune"] = row[0] if row else None
    except Exception:
        enrichi["nom_commune"] = None
    try:
        r = await db.execute(
            text("SELECT nom FROM regions WHERE code = :rr LIMIT 1"),
            {"rr": parts["code_region"]}
        )
        row = r.first()
        enrichi["nom_region"] = row[0] if row else None
    except Exception:
        enrichi["nom_region"] = None
    return {"nicad": nicad, "decomposition": enrichi, "valide": True}


class ResolutionConflitIn(BaseModel):
    decision: str = Field(..., description="resolu | maintenu | annule_b")
    motif_resolution: str = Field(..., min_length=10)


@router.post("/conflits/{conflit_id}/resoudre")
async def resoudre_conflit(
    conflit_id: str,
    payload: ResolutionConflitIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_DIR)),
):
    """Résout un conflit antifraude ouvert."""
    if payload.decision not in ("resolu","maintenu","annule_b"):
        raise HTTPException(422, "decision : resolu | maintenu | annule_b")
    r = await db.execute(select(ConflitParcellaire).where(ConflitParcellaire.id == conflit_id))
    conflit = r.scalar_one_or_none()
    if not conflit:
        raise HTTPException(404, "Conflit introuvable")
    if conflit.statut != StatutConflit.OUVERT:
        raise HTTPException(400, f"Conflit déjà au statut {conflit.statut}")

    conflit.statut = StatutConflit.RESOLU if payload.decision == "resolu" else StatutConflit.BLOQUE
    conflit.traite_par = current_user.id
    conflit.traite_at  = datetime.now(timezone.utc)
    conflit.description = (conflit.description or "") + f"\n[{payload.decision}] {payload.motif_resolution}"

    if payload.decision == "annule_b" and conflit.parcelle_b_id:
        await db.execute(
            update(Parcelle).where(Parcelle.id == conflit.parcelle_b_id)
            .values(statut=StatutParcelle.ANNULE, updated_at=datetime.now(timezone.utc))
        )
    await db.commit()
    return {"conflit_id": conflit_id, "statut": conflit.statut,
            "decision": payload.decision, "traite_par": current_user.id}


@router.get("/conflits", response_model=list,
    summary="Liste conflits")
async def lister_conflits_cadastre(
    gravite: Optional[str] = Query(None),
    statut: str = Query(default="ouvert"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_CADASTRE + ["AUDITEUR"])),
):
    """Liste les conflits antifraude."""
    filters = [ConflitParcellaire.statut == statut]
    if gravite: filters.append(ConflitParcellaire.gravite == gravite)
    # ── Filtre acteur (ScopeFilter) ──────────────────────────────
    try:
        from app.core.scope_filter import ScopeFilter as _SF
        _scope = _SF.from_user(current_user)
        if not _scope.peut_voir_national:
            if _scope.region:
                where += " AND c.region = :_scope_region"
                params["_scope_region"] = _scope.region
            elif _scope.entite_id:
                where += " AND c.region = :_scope_entite"
                params["_scope_entite"] = _scope.entite_id
    except Exception:
        pass  # fallback : pas de filtrage si scope indisponible
        r = await db.execute(
        select(ConflitParcellaire).where(and_(*filters))
        .order_by(ConflitParcellaire.detecte_at.desc())
        .offset((page-1)*limit).limit(limit)
    )
    return [{"id": str(c.id), "parcelle_a_id": str(c.parcelle_a_id),
             "parcelle_b_id": str(c.parcelle_b_id) if c.parcelle_b_id else None,
             "type_conflit": c.type_conflit, "gravite": c.gravite,
             "statut": c.statut, "surface_m2": float(c.surface_intersection_m2 or 0),
             "detecte_at": c.detecte_at.isoformat()} for c in r.scalars().all()]


@router.get("/parcelles/{parcelle_id}/fiche-complete", response_model=dict)
async def fiche_complete_parcelle(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(
        ROLES_CADASTRE + ["NOTAIRE","CHEF_CCFM","BANQ_DIRECTEUR","AUDITEUR"]
    )),
):
    """Fiche cadastrale complète : parcelle + NICAD + conflits + droits."""
    r = await db.execute(select(Parcelle).where(Parcelle.id == parcelle_id))
    parcelle = r.scalar_one_or_none()
    if not parcelle:
        raise HTTPException(404, "Parcelle introuvable")
    r_nicad = await db.execute(
        select(NicadRegistry).where(NicadRegistry.parcelle_id == parcelle_id)
    )
    nicad_entry = r_nicad.scalar_one_or_none()
    r_conf = await db.execute(
        select(func.count(ConflitParcellaire.id)).where(
            and_(ConflitParcellaire.parcelle_a_id == parcelle_id,
                 ConflitParcellaire.statut == StatutConflit.OUVERT)
        )
    )
    nb_conflits = r_conf.scalar() or 0
    droits = []
    try:
        r_droits = await db.execute(text(
            "SELECT titulaire_id, type_droit, pourcentage_droit, statut"
            " FROM parcel_right_version WHERE parcelle_id = :pid AND statut = 'actif'"
        ), {"pid": parcelle_id})
        droits = [dict(row) for row in r_droits.mappings().all()]
    except Exception:
        pass
    return {
        "id": str(parcelle.id), "nicad": parcelle.nicad,
        "statut": parcelle.statut, "surface_m2": float(parcelle.surface_m2),
        "is_gele": parcelle.is_gele,
        "nicad_registre": {
            "code_region":   nicad_entry.code_region if nicad_entry else None,
            "code_commune":  nicad_entry.code_commune if nicad_entry else None,
            "code_arrete":   nicad_entry.code_arrete if nicad_entry else None,
            "code_ilot":     nicad_entry.code_ilot if nicad_entry else None,
            "code_parcelle": nicad_entry.code_parcelle if nicad_entry else None,
            "suffixe":       nicad_entry.suffixe if nicad_entry else None,
        } if nicad_entry else None,
        "conflits_ouverts": nb_conflits,
        "droits_actifs": droits,
        "created_at": parcelle.created_at.isoformat(),
    }


# ══════════════════════════════════════════════════════════════════
# WF6 — DIVISION PARCELLAIRE (migration 011)
# ══════════════════════════════════════════════════════════════════

@router.post("/divisions", status_code=201)
async def ouvrir_division_parcellaire(
    parcelle_mere_id: str = Query(...),
    demandeur_id: str = Query(...),
    nb_lots_cibles: int = Query(..., ge=2),
    motif: str = Query(..., min_length=5),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_CADASTRE",
        "CHEF_SERVICE_CADASTRE","GEOMETRE"])),
):
    """WF6 étape 1 — Ouvre un dossier de division parcellaire.
    Trigger tg_wl1_bloquer_parcelle_mere gèle la parcelle mère.
    """
    import hashlib
    sha = hashlib.sha256(f"{parcelle_mere_id}:{nb_lots_cibles}:{motif[:30]}".encode()).hexdigest()
    div_id = str(uuid.uuid4())
    try:
        await db.execute(text("""
            INSERT INTO demande_division
                (id,parcelle_mere_id,demandeur_id,nb_lots_cibles,
                 motif,statut,sha256_dossier,initie_par,created_at)
            VALUES
                (:id,:parc,:dem,:nb,:motif,'initie',:sha,:uid,NOW())
        """), {"id":div_id,"parc":parcelle_mere_id,"dem":demandeur_id,
               "nb":nb_lots_cibles,"motif":motif,"sha":sha,
               "uid":str(current_user.id)})
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "bloquer" in str(e).lower():
            raise HTTPException(400, f"Division bloquée (tg_wl1) : {str(e)[:300]}")
        raise HTTPException(400, str(e)[:200])
    return {"id":div_id,"statut":"initie","sha256_dossier":sha,
            "message":"Parcelle mère bloquée (tg_wl1_bloquer_parcelle_mere)"}


@router.get("/divisions", response_model=list,
    summary="Liste divisions")
async def lister_divisions(
    statut: Optional[str] = Query(None),
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_CADASTRE",
        "CHEF_SERVICE_CADASTRE","AUDITEUR"])),
):
    where = "WHERE 1=1"; params: dict = {"l":limit,"o":(page-1)*limit}
    if statut: where += " AND statut=:s"; params["s"]=statut
    try:
        r = await db.execute(text(
            f"SELECT * FROM demande_division {where} "
            "ORDER BY created_at DESC LIMIT :l OFFSET :o"), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.patch("/divisions/{div_id}/statut")
async def avancer_division(
    div_id: str,
    nouveau_statut: str = Query(...,
        description="ccfm_verif|validation_cadastre|dad_validation|creation_parcelles|archive|rejete"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_CADASTRE",
        "CHEF_SERVICE_CADASTRE","ADMIN_COMMUNE"])),
):
    """Avance le statut d'un dossier de division parcellaire (WF6)."""
    try:
        await db.execute(text(
            "UPDATE demande_division SET statut=:s WHERE id=:id"
        ), {"s":nouveau_statut,"id":div_id})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"id":div_id,"statut":nouveau_statut}


@router.post("/divisions/{div_id}/lots-projets", status_code=201)
async def ajouter_lot_projet(
    div_id: str,
    numero_lot: str = Query(...),
    surface_projetee_m2: float = Query(..., gt=0),
    geom_wkt: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","GEOMETRE","TOPOGRAPHE",
        "DIRECTEUR_CADASTRE"])),
):
    """Ajoute un lot projeté à un dossier de division (division_lot_projet)."""
    import hashlib
    sha = hashlib.sha256(f"{div_id}:{numero_lot}:{surface_projetee_m2}".encode()).hexdigest()
    try:
        await db.execute(text("""
            INSERT INTO division_lot_projet
                (id,demande_division_id,numero_lot,surface_projetee_m2,
                 geom,geometry_hash,statut)
            VALUES
                (gen_random_uuid(),:did,:num,:surf,:geom,:sha,'provisoire')
        """), {"did":div_id,"num":numero_lot,"surf":surface_projetee_m2,
               "geom":geom_wkt,"sha":sha})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"demande_division_id":div_id,"numero_lot":numero_lot,"sha256":sha}


# ── Servitudes foncières ──────────────────────────────────────────

@router.get("/parcelles/{parcelle_id}/servitudes", response_model=dict)
async def lister_servitudes_parcelle(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_CADASTRE",
        "NOTAIRE","BANQ_DIRECTEUR","AUDITEUR"])),
):
    """Servitudes actives sur une parcelle (parcel_servitude — droits_fonciers.py)."""
    try:
        r = await db.execute(text("""
            SELECT ps.id,ps.type_servitude,ps.date_debut,ps.date_fin,
                   ps.acte_reference,ps.rnaf_id
            FROM parcel_servitude ps
            WHERE ps.parcelle_id=:pid
              AND (ps.date_fin IS NULL OR ps.date_fin > NOW())
            ORDER BY ps.date_debut DESC
        """), {"pid":parcelle_id})
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


# ── Usage foncier ─────────────────────────────────────────────────

@router.get("/usage-foncier/{parcelle_id}", response_model=dict)
async def get_usage_foncier(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Usage foncier actif d'une parcelle (usage_foncier — migration 011)."""
    try:
        r = await db.execute(text("""
            SELECT type_usage,statut,usage_autorise,
                   coefficient_foncier,sha256_usage
            FROM usage_foncier WHERE parcelle_id=:pid
        """), {"pid":parcelle_id})
        row = r.mappings().first()
        if not row: raise HTTPException(404,"Usage foncier non renseigné")
        return dict(row)
    except HTTPException: raise
    except Exception as e: raise HTTPException(500, str(e)[:200])


@router.post("/usage-foncier", status_code=201)
async def enregistrer_usage_foncier(
    parcelle_id: str = Query(...),
    type_usage: str = Query(...,
        description="residentiel|commercial|industriel|agricole|mixte|"
                    "equipement_public|espace_vert|religieux|autre"),
    usage_autorise: Optional[str] = Query(None),
    arrete_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_URBANISME",
        "DIRECTEUR_CADASTRE","CHEF_SERVICE_CADASTRE"])),
):
    """Enregistre ou met à jour l'usage foncier d'une parcelle."""
    import hashlib
    sha = hashlib.sha256(f"{parcelle_id}:{type_usage}".encode()).hexdigest()
    try:
        await db.execute(text("""
            INSERT INTO usage_foncier
                (id,parcelle_id,type_usage,statut,usage_autorise,
                 arrete_id,sha256_usage,created_at)
            VALUES
                (gen_random_uuid(),:pid,:type,'actif',:ua,:arr,:sha,NOW())
            ON CONFLICT (parcelle_id) DO UPDATE
                SET type_usage=EXCLUDED.type_usage,
                    usage_autorise=EXCLUDED.usage_autorise,
                    sha256_usage=EXCLUDED.sha256_usage,
                    statut='actif'
        """), {"pid":parcelle_id,"type":type_usage,
               "ua":usage_autorise,"arr":arrete_id,"sha":sha})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"parcelle_id":parcelle_id,"type_usage":type_usage,"sha256":sha}


# ── Conflits parcellaires ─────────────────────────────────────────

@router.post("/conflits-parcellaires", status_code=201)
async def signaler_conflit_parcellaire(
    parcelle_a_id: str = Query(...),
    parcelle_b_id: Optional[str] = Query(None),
    type_conflit: str = Query(...,
        description="superposition|deplacement|surface|incoherence"),
    gravite: str = Query(..., description="critique|a_verifier"),
    description: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","DIRECTEUR_CADASTRE",
        "RESPONSABLE_BGU","GEOMETRE","AUDITEUR"])),
):
    """Signale un conflit géospatial entre parcelles (conflits_parcellaires — migration 003)."""
    import hashlib
    sha = hashlib.sha256(f"{parcelle_a_id}:{parcelle_b_id or ''}:{type_conflit}".encode()).hexdigest()
    try:
        await db.execute(text("""
            INSERT INTO conflits_parcellaires
                (id,parcelle_a_id,parcelle_b_id,type_conflit,
                 gravite,statut,sha256_geom,description,detecte_at)
            VALUES
                (gen_random_uuid(),:pa,:pb,:type,:gravite,'ouvert',:sha,:desc,NOW())
        """), {"pa":parcelle_a_id,"pb":parcelle_b_id,"type":type_conflit,
               "gravite":gravite,"sha":sha,"desc":description})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"parcelle_a_id":parcelle_a_id,"type_conflit":type_conflit,
            "gravite":gravite,"sha256":sha}


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE DAR — CADASTRE
# Direction des Archives — liaison automatique vers ANNF
# ══════════════════════════════════════════════════════════════════

@router.get("/dar/archives", response_model=list,
    summary="Liste dar/archives")
async def lister_archives_dar_cadastre(
    type_document: Optional[str] = Query(None,
        description="plan_cadastral|rnp|nicad|bornage|servitude|division"),
    scelle: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'DIRECTEUR_CADASTRE', 'CHEF_SERVICE_CADASTRE', 'GEOMETRE'] + [
        "ARCHIVISTE_ANNF","RESPONSABLE_ANNF","AUDITEUR"
    ])),
):
    """Archives du module Cadastre (sous-module DAR).
    Retourne les archives liées à ce module depuis annf_archive_links.
    Filtrables par type de document et statut de scellement.
    """
    where = "WHERE entite_table LIKE :mod"
    params: dict = {
        "mod": f"%cadastre%",
        "l": limit, "o": (page-1)*limit
    }
    if type_document:
        where += " AND type_archive=:td"; params["td"] = type_document
    if scelle is not None:
        where += " AND scelle=:sc"; params["sc"] = scelle
    try:
        r = await db.execute(text(f"""
            SELECT al.id, al.ida, al.type_archive,
                   al.entite_id, al.entite_table,
                   al.sha256_snapshot, al.scelle,
                   al.scelle_at, al.version, al.created_at
            FROM annf_archive_links al
            {where}
            ORDER BY al.created_at DESC
            LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        await db.rollback()
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


@router.post("/dar/archiver", status_code=201)
async def archiver_vers_annf_cadastre(
    type_document: str = Query(...,
        description="plan_cadastral|rnp|nicad|bornage|servitude|division"),
    entite_id: str = Query(..., description="UUID de l'entité à archiver"),
    entite_table: str = Query(..., description="Nom de la table source"),
    snapshot: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'DIRECTEUR_CADASTRE', 'ARCHIVISTE_ANNF'])),
):
    """Archive un document du module Cadastre vers l'ANNF national.
    Crée une entrée dans annf_archive_links avec IDA atomique ANNF-YYYY-NNNNNNN.
    Liaison automatique DAR → ANNF.
    """
    from app.services.workflow_orchestrator import ANNFArchiveService
    from app.models.workflows import TypeArchiveANNF

    # Mapping type_document → TypeArchiveANNF
    type_map = {
        "rnaf": TypeArchiveANNF.RNAF,
        "rnp":  TypeArchiveANNF.RNP,
        "ccfm": TypeArchiveANNF.CCFM,
        "acte_notarie": TypeArchiveANNF.ACTE_NOTARIE,
        "decision_justice": TypeArchiveANNF.DECISION_JUSTICE,
        "bgu_scelle": TypeArchiveANNF.BGU_SCELLE,
        "plan_cadastral": TypeArchiveANNF.PLAN_CADASTRAL,
    }
    type_archive = type_map.get(type_document, TypeArchiveANNF.PLAN_CADASTRAL)

    snap = snapshot or {
        "entite_id": entite_id,
        "entite_table": entite_table,
        "module_source": "cadastre",
        "type_document": type_document,
        "archive_par": current_user.id,
    }
    try:
        archive = await ANNFArchiveService.archiver(
            db=db,
            type_archive=type_archive,
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
        "module": "cadastre",
        "sous_module": "DAR",
        "type_archive": type_document,
        "scelle": False,
        "message": "Archivé vers ANNF — scellement via POST /annf/{ida}/sceller",
    }


@router.get("/dar/integrite", response_model=list,
    summary="Liste dar/integrite")
async def verifier_integrite_dar_cadastre(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'DIRECTEUR_CADASTRE', 'CHEF_SERVICE_CADASTRE', 'GEOMETRE'] + ["AUDITEUR"])),
):
    """Vérifie l'intégrité des archives DAR du module Cadastre.
    Retourne le compte d'archives scellées vs non scellées.
    """
    try:
        r = await db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE scelle=true)  AS nb_scelles,
                COUNT(*) FILTER (WHERE scelle=false) AS nb_en_attente,
                COUNT(*)                              AS nb_total
            FROM annf_archive_links
            WHERE entite_table LIKE :mod
        """), {"mod": f"%cadastre%"})
        row = r.mappings().first()
        return dict(row) if row else {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}
    except Exception:
        return {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}
