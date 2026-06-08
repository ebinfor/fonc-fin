from app.services.workflow_orchestrator import ANNFArchiveService
"""
FONCIER+ — Module BGU complet (Base Géospatiale Unique)
Module indépendant — base de vérité géospatiale nationale.

Corrige :
  BUG-API-BGU-01 : scellement BGUGeoJSONMaster
  BUG-API-BGU-02 : contrôle géométrique exposé
  BUG-API-BGU-03 : BGUGeoJSONMaster CRUD
  BUG-API-BGU-04 : projection BGU consultable
"""
import uuid
import hashlib
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, and_, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user
from app.models.parcellaire import BGUGeoJSONMaster, Parcelle
from app.models.workflows import BGUParcelStatus

router = APIRouter(prefix="/bgu", tags=["BGU — Base Géospatiale Unique"])

ROLES_BGU  = ["ADMIN","RESPONSABLE_BGU"]
ROLES_READ = ["ADMIN","RESPONSABLE_BGU","AUDITEUR","NOTAIRE",
              "BANQ_DIRECTEUR","DIRECTEUR_URBANISME","DIRECTEUR_CADASTRE"]


# ─── Schémas ──────────────────────────────────────────────────────

_ROLES_BGU_READ = ["ADMIN", "RESPONSABLE_BGU"]
class BGUGeomCreateIn(BaseModel):
    parcelle_id: str = Field(..., min_length=3,
        description='UUID de la parcelle')
    geom_wkt: str = Field(..., min_length=20,
        description="Géométrie WKT POLYGON SRID 4326 — ex: POLYGON((2.1 13.5, ...))")
    geojson_id: Optional[str] = None  # Généré si absent


_ROLES_BGU_READ = ["ADMIN", "RESPONSABLE_BGU"]
class BGUGeomScellerIn(BaseModel):
    """Sceller une géométrie BGU (rendu immuable)."""
    blockchain_hash:    Optional[str] = Field(None, min_length=32,
        description="Hash blockchain si réseau externe")
    blockchain_network: str           = Field(default="internal",
        pattern="^(internal|ethereum|polygon|hyperledger)$")

_ROLES_BGU_READ = ["ADMIN", "RESPONSABLE_BGU"]
class BGUStatutUpdate(BaseModel):
    statut_public: str = Field(...,
        description="actif|suspendu|gele|annule|en_verification")
    statut_detail: Optional[str] = None
    source_rnaf: bool = False
    source_ccfm: bool = False
    source_justice: bool = False
    source_fraude: bool = False


# ─── Tableau de bord RESPONSABLE_BGU ──────────────────────────────

@router.get("/tableau-de-bord", response_model=list)
async def tableau_de_bord_bgu(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_BGU + ["DIRECTEUR_URBANISME","AUDITEUR"])),
):
    """Vue consolidée du BGU national."""
    stats: dict = {}

    # Géométries scellées vs non scellées
    try:
        r = await db.execute(text("""
            SELECT scelle, COUNT(*) nb FROM bgu_geojson_master GROUP BY scelle
        """))
        geom_stats = {str(row.scelle): row.nb for row in r}
        stats["geometries"] = {
            "scellees": geom_stats.get("True", 0),
            "non_scellees": geom_stats.get("False", 0),
        }
    except Exception:
        stats["geometries"] = {"scellees": 0, "non_scellees": 0}

    # Statuts publics
    try:
        r = await db.execute(text("""
            SELECT statut_public, COUNT(*) nb
            FROM bgu_parcel_status GROUP BY statut_public
        """))
        stats["statuts_publics"] = {row.statut_public: row.nb for row in r}
    except Exception:
        stats["statuts_publics"] = {}

    # Contrôles géométriques invalides
    try:
        r = await db.execute(text("""
            SELECT COUNT(*) FROM controle_geometrique
            WHERE geometrie_valide = false
        """))
        stats["controles_invalides"] = r.scalar() or 0
    except Exception:
        stats["controles_invalides"] = 0

    # Projections en attente (projection_valide = false)
    try:
        r = await db.execute(text("""
            SELECT COUNT(*) FROM bgu_projection WHERE projection_valide = false
        """))
        stats["projections_invalides"] = r.scalar() or 0
    except Exception:
        stats["projections_invalides"] = 0

    return {
        "acteur": current_user.email,
        "role": current_user.role,
        "module": "BGU — Base Géospatiale Unique",
        "stats": stats,
        "horodatage": datetime.now(timezone.utc).isoformat(),
    }


# ─── BGUGeoJSONMaster — CRUD + scellement ─────────────────────────

@router.post("/geometries", status_code=201)
async def enregistrer_geometrie(
    payload: BGUGeomCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_BGU + ["GEOMETRE"])),
):
    """Enregistre une géométrie dans le BGU (avant scellement).

    La géométrie est stockée non scellée. Elle sera scellée après
    validation par le RESPONSABLE_BGU via POST /bgu/geometries/{id}/sceller.
    Le SHA-256 est calculé immédiatement sur le WKT normalisé.
    """
    try:
        # Vérifier que la parcelle existe
        r = await db.execute(select(Parcelle).where(Parcelle.id == payload.parcelle_id))
        if not r.scalar_one_or_none():
            raise HTTPException(404, "Parcelle introuvable")

        # Vérifier unicité
        r_exist = await db.execute(
            select(BGUGeoJSONMaster).where(BGUGeoJSONMaster.parcelle_id == payload.parcelle_id)
        )
        if r_exist.scalar_one_or_none():
            raise HTTPException(409, "Géométrie BGU déjà enregistrée pour cette parcelle")

        # SHA-256 du WKT normalisé (trim + uppercase)
        geom_normalized = payload.geom_wkt.strip().upper()
        sha256_geom = hashlib.sha256(geom_normalized.encode()).hexdigest()

        geojson_id = payload.geojson_id or f"geojson-bgu-{uuid.uuid4().hex[:12]}"

        bgu = BGUGeoJSONMaster(
            id=uuid.uuid4(),
            parcelle_id=payload.parcelle_id,
            geojson_id=geojson_id,
            geom=payload.geom_wkt,
            sha256_geom=sha256_geom,
            scelle=False,
            version=1,
        )
        db.add(bgu)
        await db.commit()
        await db.refresh(bgu)
        return {
            "id": str(bgu.id),
            "geojson_id": bgu.geojson_id,
            "parcelle_id": payload.parcelle_id,
            "sha256_geom": sha256_geom,
            "scelle": False,
            "message": "Géométrie enregistrée — en attente de scellement",
        }

    except Exception as e:
        await db.rollback()
        import logging as _l
        _l.getLogger('bgu').warning('enregistrer_geometrie: %s', e)
        raise HTTPException(status_code=500,
            detail=str(e)[:200])

@router.post("/geometries/{bgu_id}/sceller")
async def sceller_geometrie(
    bgu_id: str,
    payload: BGUGeomScellerIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_BGU)),
):
    """Scelle une géométrie BGU — opération IRRÉVERSIBLE.

    Après scellement :
    - La géométrie ne peut plus être modifiée
    - Le trigger tg_tc3_maj_bgu_projection met à jour bgu_projection
    - La parcelle reçoit un statut public BGU 'actif'
    """
    try:
        r = await db.execute(select(BGUGeoJSONMaster).where(BGUGeoJSONMaster.id == bgu_id))
        bgu = r.scalar_one_or_none()
        if not bgu:
            raise HTTPException(404, "Géométrie BGU introuvable")
        if bgu.scelle:
            raise HTTPException(400,
                f"Géométrie déjà scellée le {bgu.scelle_at.isoformat()} — irréversible")

        now = datetime.now(timezone.utc)
        bgu.scelle             = True
        bgu.scelle_at          = now
        bgu.scelle_par         = current_user.id
        bgu.blockchain_hash    = payload.blockchain_hash
        bgu.blockchain_network = payload.blockchain_network
        if payload.blockchain_hash:
            bgu.blockchain_timestamp = now

        await db.commit()
        # Le trigger tg_tc3_maj_bgu_projection se déclenche automatiquement
        return {
            "id": bgu_id,
            "geojson_id": bgu.geojson_id,
            "scelle": True,
            "scelle_at": now.isoformat(),
            "sha256_geom": bgu.sha256_geom,
            "blockchain_hash": bgu.blockchain_hash,
            "message": "Scellement définitif effectué — tg_tc3_maj_bgu_projection déclenché",
        }

    except Exception as e:
        await db.rollback()
        import logging as _l
        _l.getLogger('bgu').warning('sceller_geometrie: %s', e)
        raise HTTPException(status_code=500,
            detail=str(e)[:200])

@router.get("/geometries/{parcelle_id}", response_model=dict)
async def get_geometrie_bgu(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Retourne la géométrie BGU scellée d'une parcelle."""
    try:
        r = await db.execute(
            select(BGUGeoJSONMaster).where(BGUGeoJSONMaster.parcelle_id == parcelle_id)
        )
        bgu = r.scalar_one_or_none()
        if not bgu:
            raise HTTPException(404, "Géométrie BGU introuvable pour cette parcelle")
        return {
            "id": str(bgu.id),
            "geojson_id": bgu.geojson_id,
            "parcelle_id": parcelle_id,
            "sha256_geom": bgu.sha256_geom,
            "scelle": bgu.scelle,
            "scelle_at": bgu.scelle_at.isoformat() if bgu.scelle_at else None,
            "version": bgu.version,
            "blockchain_hash": bgu.blockchain_hash,
            "blockchain_network": bgu.blockchain_network,
        }


    # ─── Contrôle géométrique PostGIS ─────────────────────────────────
    except Exception as e:
        import logging as _l
        _l.getLogger('bgu').warning('get_geometrie_bgu: %s', e)
        raise HTTPException(status_code=500,
            detail=str(e)[:200])

@router.get("/controles/{parcelle_id}", response_model=dict)
async def get_controle_geometrique(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Retourne les résultats du contrôle géométrique PostGIS d'une parcelle."""
    try:
        r = await db.execute(text("""
            SELECT *
            FROM controle_geometrique
            WHERE parcelle_id = :pid
        """), {"pid": parcelle_id})
        row = r.mappings().first()
        if not row:
            raise HTTPException(404, "Aucun contrôle géométrique pour cette parcelle")
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Erreur contrôle géométrique : {str(e)[:200]}")


@router.get("/controles/invalides", response_model=list)
async def lister_controles_invalides(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_BGU + ["DIRECTEUR_CADASTRE","AUDITEUR"])),
):
    """Liste les parcelles dont la géométrie BGU est invalide."""
    try:
        r = await db.execute(text("""
            SELECT cg.parcelle_id, cg.motif_invalidite,
                   cg.pas_de_superposition, cg.geometrie_fermee,
                   cg.surface_coherente, cg.dans_lotissement,
                   cg.ecart_surface_pct, p.nicad
            FROM controle_geometrique cg
            JOIN parcelles p ON p.id = cg.parcelle_id
            WHERE cg.geometrie_valide = false
            ORDER BY cg.parcelle_id
            LIMIT :limit OFFSET :offset
        """), {"limit": limit, "offset": (page-1)*limit})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


# ─── Projection BGU ───────────────────────────────────────────────

@router.get("/projections/{parcelle_id}", response_model=dict)
async def get_projection_bgu(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Résultats de la projection BGU : cohérence RNAF/RNP et surface calculée."""
    try:
        r = await db.execute(text("""
            SELECT bp.*, p.nicad
            FROM bgu_projection bp
            JOIN parcelles p ON p.id = bp.parcelle_id
            WHERE bp.parcelle_id = :pid
        """), {"pid": parcelle_id})
        row = r.mappings().first()
        if not row:
            raise HTTPException(404, "Projection BGU introuvable")
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@router.get("/projections/invalides", response_model=list)
async def lister_projections_invalides(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_BGU + ["DIRECTEUR_CADASTRE"])),
):
    """Liste les projections BGU non validées."""
    try:
        r = await db.execute(text("""
            SELECT bp.parcelle_id, bp.ecart_surface_pct,
                   bp.coherence_rnaf, bp.coherence_rnp,
                   bp.motif_invalidite, p.nicad
            FROM bgu_projection bp
            JOIN parcelles p ON p.id = bp.parcelle_id
            WHERE bp.projection_valide = false
            ORDER BY bp.date_projection DESC
            LIMIT :limit OFFSET :offset
        """), {"limit": limit, "offset": (page-1)*limit})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        return []


# ─── Statut public ────────────────────────────────────────────────

@router.get("/statut/{parcelle_id}", response_model=dict)
async def get_statut_bgu(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ)),
):
    """Retourne le statut public BGU d'une parcelle."""
    r = await db.execute(
        select(BGUParcelStatus).where(BGUParcelStatus.parcelle_id == parcelle_id)
    )
    statut = r.scalar_one_or_none()
    if not statut:
        raise HTTPException(404, "Statut BGU introuvable")
    return {
        "parcelle_id": parcelle_id,
        "statut_public": statut.statut_public,
        "statut_detail": statut.statut_detail,
        "source_rnaf": statut.source_rnaf,
        "source_ccfm": statut.source_ccfm,
        "source_justice": statut.source_justice,
        "source_fraude": statut.source_fraude,
        "derniere_maj_at": statut.derniere_maj_at.isoformat(),
        "qr_code_url": statut.qr_code_url,
    }


@router.put("/statut/{parcelle_id}")
async def mettre_a_jour_statut_bgu(
    parcelle_id: str,
    payload: BGUStatutUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_BGU)),
):
    """Met à jour le statut public d'une parcelle dans le BGU."""
    STATUTS_VALIDES = {"actif","suspendu","gele","annule","en_verification"}
    if payload.statut_public not in STATUTS_VALIDES:
        raise HTTPException(422, f"statut_public invalide. Valeurs : {STATUTS_VALIDES}")

    r = await db.execute(
        select(BGUParcelStatus).where(BGUParcelStatus.parcelle_id == parcelle_id)
    )
    statut = r.scalar_one_or_none()
    if not statut:
        raise HTTPException(404,
            "Statut BGU introuvable — créer d'abord via assiettes_bgu")

    statut.statut_public   = payload.statut_public
    statut.statut_detail   = payload.statut_detail
    statut.source_rnaf     = payload.source_rnaf
    statut.source_ccfm     = payload.source_ccfm
    statut.source_justice  = payload.source_justice
    statut.source_fraude   = payload.source_fraude
    statut.derniere_maj_at = datetime.now(timezone.utc)
    statut.maj_par         = current_user.id
    try:
        await db.commit()
        return {"parcelle_id": parcelle_id, "statut_public": statut.statut_public}
    except Exception as _e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(_e)[:200])


@router.get("/statuts", response_model=list)
async def lister_statuts_bgu(
    statut_public: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_READ + ["RESPONSABLE_BGU"])),
):
    """Liste les statuts BGU (tableau de bord supervision)."""
    where = "WHERE 1=1"
    params: dict = {"limit": limit, "offset": (page-1)*limit}
    if statut_public:
        where += " AND statut_public = :sp"
        params["sp"] = statut_public
    try:
        r = await db.execute(
            text(f"SELECT parcelle_id, statut_public, derniere_maj_at, source_rnaf, "
                 f"source_ccfm, source_justice, source_fraude "
                 f"FROM bgu_parcel_status {where} "
                 f"ORDER BY derniere_maj_at DESC LIMIT :limit OFFSET :offset"),
            params,
        )
        return r.mappings().all()
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


# ─── Vue consolidée publique ──────────────────────────────────────

@router.get("/parcelle/{parcelle_id}/etat-complet", response_model=dict)
async def etat_complet_bgu(
    parcelle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """État BGU complet d'une parcelle : géom + contrôle + projection + statut.
    Accessible à tous les acteurs authentifiés.
    """
    try:
        r = await db.execute(text("""
            SELECT
                p.nicad, p.statut AS statut_cadastral,
                bgm.sha256_geom, bgm.scelle, bgm.scelle_at,
                bgm.blockchain_hash, bgm.version AS geom_version,
                bps.statut_public, bps.derniere_maj_at,
                bp.projection_valide, bp.ecart_surface_pct,
                bp.coherence_rnaf, bp.coherence_rnp,
                cg.geometrie_valide, cg.motif_invalidite
            FROM parcelles p
            LEFT JOIN bgu_geojson_master bgm ON bgm.parcelle_id = p.id
            LEFT JOIN bgu_parcel_status  bps ON bps.parcelle_id = p.id
            LEFT JOIN bgu_projection     bp  ON bp.parcelle_id  = p.id
            LEFT JOIN controle_geometrique cg ON cg.parcelle_id = p.id
            WHERE p.id = :pid
        """), {"pid": parcelle_id})
        row = r.mappings().first()
        if not row:
            raise HTTPException(404, "Parcelle introuvable")
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


# ══════════════════════════════════════════════════════════════════
# CONFLITS PARCELLAIRES BGU (migration 003)
# ══════════════════════════════════════════════════════════════════

@router.get("/conflits-parcellaires", response_model=list)
async def lister_conflits_bgu(
    statut: str = Query(default="ouvert"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ROLES_BGU_READ)),
):
    """Liste les conflits BGU filtres par region."""
    from app.core.scope_filter import ScopeFilter
    scope = ScopeFilter.from_user(current_user)

    where  = "WHERE c.statut::TEXT = :s"
    params: dict = {"s": statut}

    if not scope.peut_voir_national and scope.region:
        where += " AND c.region = :_scope_region"
        params["_scope_region"] = scope.region

    try:
        r = await db.execute(text(f"""
            SELECT c.id, c.parcelle_a_id, c.parcelle_b_id, c.statut::TEXT,
                   c.type_conflit, c.surface_litigieuse_m2,
                   c.region, c.date_detection
            FROM conflits_parcellaires c
            {where}
            ORDER BY c.date_detection DESC
            LIMIT 200
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _l
        _l.getLogger("bgu").warning("lister_conflits_bgu: %s", e)
        return []


@router.patch("/conflits-parcellaires/{conf_id}/resoudre")
async def resoudre_conflit_bgu(
    conf_id: str,
    resolution: str = Query(..., min_length=5),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","RESPONSABLE_BGU",
        "DIRECTEUR_CADASTRE"])),
):
    """Résout un conflit géospatial BGU."""
    try:
        await db.execute(text("""
            UPDATE conflits_parcellaires
            SET statut='resolu',traite_par=:uid,traite_at=NOW(),
                description=COALESCE(description,'')||' [RÉSOLUTION: '||:res||']'
            WHERE id=:id
        """), {"id":conf_id,"uid":str(current_user.id),"res":resolution[:200]})
        await db.commit()
    except Exception as e:
        await db.rollback(); raise HTTPException(400, str(e)[:200])
    return {"id":conf_id,"statut":"resolu"}


@router.get("/conflits-parcellaires/critiques", response_model=list)
async def conflits_critiques_bgu(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","RESPONSABLE_BGU",
        "DIRECTEUR_CADASTRE","AUDITEUR"])),
):
    """Conflits critiques BGU non résolus — priorité maximale."""
    try:
        r = await db.execute(text("""
            SELECT cp.id,cp.type_conflit,cp.surface_intersection_m2,
                   cp.detecte_at,pa.nicad AS nicad_a,pb.nicad AS nicad_b,
                   EXTRACT(DAY FROM NOW()-cp.detecte_at)::INT AS jours_ouvert
            FROM conflits_parcellaires cp
            JOIN parcelles pa ON pa.id=cp.parcelle_a_id
            LEFT JOIN parcelles pb ON pb.id=cp.parcelle_b_id
            WHERE cp.gravite='critique' AND cp.statut='ouvert'
            ORDER BY cp.detecte_at ASC
        """))
        return [dict(row) for row in r.mappings().all()]
    except Exception as _e:
            import logging as _log
            _log.getLogger("foncier").warning("Requête %s échouée: %s", __name__, _e)
            return []


@router.get("/stats-nationales", response_model=list)
async def stats_nationales_bgu(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(["ADMIN","RESPONSABLE_BGU",
        "DIRECTEUR_URBANISME","AUDITEUR","MINISTRE_URBANISME"])),
):
    """Statistiques nationales de la base géospatiale."""
    stats: dict = {}
    for key, sql in [
        ("geometries_scellees",
         "SELECT COUNT(*) FROM bgu_geojson_master WHERE scelle=true"),
        ("projections_valides",
         "SELECT COUNT(*) FROM bgu_projection WHERE projection_valide=true"),
        ("conflits_ouverts",
         "SELECT COUNT(*) FROM conflits_parcellaires WHERE statut='ouvert'"),
        ("conflits_critiques",
         "SELECT COUNT(*) FROM conflits_parcellaires "
         "WHERE gravite='critique' AND statut='ouvert'"),
        ("parcelles_geom_invalide",
         "SELECT COUNT(*) FROM controle_geometrique WHERE geometrie_valide=false"),
        ("taux_couverture_pct",
         "SELECT ROUND(100.0*COUNT(CASE WHEN bgm.id IS NOT NULL THEN 1 END)"
         "/NULLIF(COUNT(p.id),0),2) "
         "FROM parcelles p LEFT JOIN bgu_geojson_master bgm ON bgm.parcelle_id=p.id"),
    ]:
        try:
            r = await db.execute(text(sql)); stats[key] = r.scalar() or 0
        except Exception: stats[key] = 0
    return {"module":"BGU — Base Géospatiale Unique","stats":stats}


# ══════════════════════════════════════════════════════════════════
# SOUS-MODULE DAR — BGU
# Direction des Archives — liaison automatique vers ANNF
# ══════════════════════════════════════════════════════════════════

@router.get("/dar/archives", response_model=list)
async def lister_archives_dar_bgu(
    type_document: Optional[str] = Query(None,
        description="bgu_scelle|plan_cadastral|geometrie_validee"),
    scelle: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'RESPONSABLE_BGU', 'DIRECTEUR_CADASTRE', 'AUDITEUR'] + [
        "ARCHIVISTE_ANNF","RESPONSABLE_ANNF","AUDITEUR"
    ])),
):
    """Archives du module Bgu (sous-module DAR).
    Retourne les archives liées à ce module depuis annf_archive_links.
    Filtrables par type de document et statut de scellement.
    """
    where = "WHERE entite_table LIKE :mod"
    params: dict = {
        "mod": f"%bgu%",
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
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


@router.post("/dar/archiver", status_code=201)
async def archiver_vers_annf_bgu(
    type_document: str = Query(...,
        description="bgu_scelle|plan_cadastral|geometrie_validee"),
    entite_id: str = Query(..., description="UUID de l'entité à archiver"),
    entite_table: str = Query(..., description="Nom de la table source"),
    snapshot: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'RESPONSABLE_BGU', 'ARCHIVISTE_ANNF'])),
):
    """Archive un document du module Bgu vers l'ANNF national.
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
        "module_source": "bgu",
        "type_document": type_document,
        "archive_par": current_user.email,
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
        "module": "bgu",
        "sous_module": "DAR",
        "type_archive": type_document,
        "scelle": False,
        "message": "Archivé vers ANNF — scellement via POST /annf/{ida}/sceller",
    }


@router.get("/dar/integrite", response_model=list)
async def verifier_integrite_dar_bgu(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(['ADMIN', 'RESPONSABLE_BGU', 'DIRECTEUR_CADASTRE', 'AUDITEUR'] + ["AUDITEUR"])),
):
    """Vérifie l'intégrité des archives DAR du module Bgu.
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
        """), {"mod": f"%bgu%"})
        row = r.mappings().first()
        return dict(row) if row else {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}
    except Exception:
        return {"nb_scelles":0,"nb_en_attente":0,"nb_total":0}
