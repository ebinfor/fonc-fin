"""
FONCIER+ — WF30 legacy (DÉPRÉCIÉ depuis v1.0.9)
=================================================
Le WF30 est désormais un sous-module de chaque DAR (Direction des Archives).

Architecture correcte :
  POST /v1/urbanisme/dar/wf30       — Migration archives urbanisme (arrêtés, RNAF)
  POST /v1/cadastre/dar/wf30        — Migration archives cadastre (NICAD, plans)
  POST /v1/ccfm/dar/wf30            — Migration archives CCFM
  POST /v1/domaine/dar/wf30         — Migration archives domaine
  POST /v1/notaire/dar/wf30         — Migration archives notaire
  POST /v1/banque/dar/wf30          — Migration archives banque
  POST /v1/justice/dar/wf30         — Migration archives justice
  POST /v1/commune/dar/wf30         — Migration archives commune
  POST /v1/annf/dar/wf30            — Migration supervision ANNF
  POST /v1/bgu/dar/wf30             — Migration archives BGU
  POST /v1/jo/dar/wf30              — Migration archives Journal Officiel

Ce fichier /v1/wf30/* est conservé pour compatibilité ascendante.
Migrer vers /v1/{module}/dar/wf30/* pour les nouvelles intégrations.
"""
import warnings
warnings.warn(
    "Module WF30 standalone (/v1/wf30) est déprécié. "
    "Utiliser /v1/{module}/dar/wf30 à la place. "
    "Chaque module (urbanisme, cadastre, ccfm, domaine, notaire, banque, "
    "justice, commune, annf, bgu, jo) a son propre DAR avec WF30 local.",
    DeprecationWarning,
    stacklevel=2,
)

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user

router = APIRouter(prefix="/wf30", tags=["WF30 — Migration Données Foncières"])

ROLES_MIGRATION = [
    "ADMIN","DIRECTEUR_CADASTRE","DIRECTEUR_URBANISME",
    "ARCHIVISTE_ANNF","RESPONSABLE_ANNF","AUDITEUR"
]
ROLES_EXEC = ["ADMIN","DIRECTEUR_CADASTRE","ARCHIVISTE_ANNF"]


ETAPES_PIPELINE = [
    "30.1_collecte_sources",
    "30.2_inventaire_plans",
    "30.3_numerisation",
    "30.4_georeferencement",
    "30.4bis_uniformisation_srid",
    "30.5_vectorisation",
    "30.6_generation_identifiants",
    "30.7_association_proprietaires",
    "30.8_validation_ccfm",
    "30.9_detection_conflits",
    "30.10_integration_bgu_annf",
    "TERMINE",
]


class DemarrerMigrationIn(BaseModel):
    source: str = Field(...,
        description="archives_papier|ancien_logiciel|excel|shp|autre")
    description: str = Field(..., min_length=10)
    periode_couverte: Optional[str] = Field(None, description="ex: 1960-2000")
    commune_id: Optional[str] = None
    region_id: Optional[str] = None


class SourceDocIn(BaseModel):
    type_source: str = Field(...,
        description="journal_officiel|dad_commune|dar_archive|archive_centrale|"
                    "notaire|banque|cadastre_ancien|plan_topographique|autre")
    reference_doc: str = Field(..., min_length=3)
    titre: Optional[str] = None
    date_document: Optional[str] = None
    periode: Optional[str] = None
    lieu_conservation: Optional[str] = None


class GCPIn(BaseModel):
    x_terrain: float
    y_terrain: float
    x_image: float
    y_image: float
    description: Optional[str] = None


class VectorisationIn(BaseModel):
    plan_id: str
    geom_wkt: str = Field(..., description="WKT POLYGON SRID 4326")
    surface_m2: float = Field(..., gt=0)
    qualite_vectorisation: str = Field(default="bonne",
        description="excellente|bonne|acceptable|a_verifier")


# ── Tableau de bord ──────────────────────────────────────────────


class MigrationStatusOut(BaseModel):
    id: str; statut: str; progression: Optional[float]=None
    source_doc_count: Optional[int]=None; erreurs: list=[]
    class Config: from_attributes=True

class GCPOut(BaseModel):
    id: str; source_x: float; source_y: float; cible_x: float; cible_y: float
    residuel: Optional[float]=None
    class Config: from_attributes=True

@router.get("/tableau-de-bord", response_model=list,
    summary="Liste tableau de bord")
async def tableau_de_bord_wf30(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_MIGRATION)),
):
    """Vue consolidée du pipeline WF30 — migrations en cours et terminées."""
    stats: dict = {}
    queries = {
        "migrations_total":
            "SELECT COUNT(*) FROM migration_historique",
        "migrations_terminees":
            "SELECT COUNT(*) FROM migration_historique WHERE statut='termine'",
        "migrations_en_cours":
            "SELECT COUNT(*) FROM migration_historique "
            "WHERE statut NOT IN ('termine','erreur')",
        "sources_collectees":
            "SELECT COUNT(*) FROM migration_source_doc",
        "parcelles_migrees":
            "SELECT COUNT(*) FROM migration_identifiant WHERE nicad IS NOT NULL",
        "conflits_detectes":
            "SELECT COUNT(*) FROM migration_conflit_spatial WHERE resolu=false",
        "archives_annf_wf30":
            "SELECT COUNT(*) FROM annf_archive_links "
            "WHERE entite_table LIKE '%migration%'",
    }
    for key, sql in queries.items():
        try:
            r = await db.execute(text(sql)); stats[key] = r.scalar() or 0
        except Exception: stats[key] = 0

    # Migrations récentes
    recentes = []
    try:
        r = await db.execute(text("""
            SELECT id, code_migration, source, statut,
                   periode_couverte, nb_importees, created_at
            FROM migration_historique
            ORDER BY created_at DESC LIMIT 5
        """))
        recentes = [dict(row) for row in r.mappings().all()]
    except Exception:
        pass

    return {
        "module": "WF30 — Migration Données Foncières",
        "acteur": current_user.email,
        "etapes_pipeline": ETAPES_PIPELINE,
        "stats": stats,
        "migrations_recentes": recentes,
    }


# ── 30.0 : Démarrer un pipeline ──────────────────────────────────

@router.post("/demarrer", status_code=201)
async def demarrer_pipeline(
    payload: DemarrerMigrationIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EXEC)),
):
    """Initialise un pipeline WF30 via demarrer_pipeline_migration().
    Crée le dossier migration_historique et positionne l'étape 30.1.
    """
    SOURCES = {"archives_papier","ancien_logiciel","excel","shp","autre"}
    if payload.source not in SOURCES:
        raise HTTPException(422, f"source : {SOURCES}")

    try:
        r = await db.execute(text(
            "SELECT demarrer_pipeline_migration(:src, :desc, :per, :cid, :rid, :uid)"
        ), {
            "src": payload.source, "desc": payload.description,
            "per": payload.periode_couverte,
            "cid": payload.commune_id, "rid": payload.region_id,
            "uid": str(current_user.id),
        })
        mig_id = r.scalar()
        await db.commit()
    except Exception as e:
        await db.rollback()
        # Fallback : INSERT direct si fonction PL pas encore compilée
        try:
            mig_id = str(uuid.uuid4())
            annee = datetime.now().year
            seq_r = await db.execute(text(
                "SELECT LPAD(NEXTVAL('mig_seq')::TEXT,4,'0')"
            ))
            code = f"MIG-{annee}-{seq_r.scalar()}"
        except Exception:
            mig_id = str(uuid.uuid4())
            code = f"MIG-{datetime.now().year}-{mig_id[:4].upper()}"
        try:
            await db.execute(text("""
                INSERT INTO migration_historique
                    (id, code_migration, source, description,
                     periode_couverte, commune_id, statut, created_at)
                VALUES
                    (:id, :code, :src, :desc, :per, :cid, 'initie', NOW())
            """), {
                "id": mig_id, "code": code,
                "src": payload.source, "desc": payload.description,
                "per": payload.periode_couverte, "cid": payload.commune_id,
            })
            await db.commit()
        except Exception as e2:
            await db.rollback()
            raise HTTPException(400, str(e2)[:300])

    return {
        "migration_id": mig_id,
        "etape_courante": ETAPES_PIPELINE[0],
        "message": "Pipeline WF30 démarré — étape 30.1 : collecte des sources",
    }


@router.get("/")
async def lister_migrations(
    statut: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_MIGRATION)),
):
    """Liste toutes les migrations WF30 avec leur statut d'avancement."""
    where = "WHERE 1=1"; params: dict = {"l":limit,"o":(page-1)*limit}
    if statut: where += " AND statut=:s"; params["s"] = statut
    try:
        # ScopeFilter
        try:
            from app.core.scope_filter import ScopeFilter as _S
            _sc = _S.from_user(current_user)
            if not _sc.peut_voir_national and _sc.region:
                where += ' AND m.region = :_scope_region'
                params['_scope_region'] = _sc.region
        except Exception: pass
        r = await db.execute(text(f"""
            SELECT id, code_migration, source, statut,
                   periode_couverte, nb_lignes_source,
                   nb_importees, nb_valides, created_at
            FROM migration_historique {where}
            ORDER BY created_at DESC LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


@router.get("/{migration_id}/rapport")
async def rapport_pipeline(
    migration_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_MIGRATION)),
):
    """Rapport JSON complet d'avancement via rapport_pipeline_migration()."""
    try:
        r = await db.execute(text(
            "SELECT rapport_pipeline_migration(:mid::UUID)"
        ), {"mid": migration_id})
        rapport = r.scalar()
        if rapport:
            return rapport
        # Fallback : rapport manuel
        raise ValueError("Fonction PL non disponible")
    except Exception:
        try:
            r = await db.execute(text("""
                SELECT mh.*,
                       (SELECT COUNT(*) FROM migration_source_doc
                        WHERE migration_id=mh.id) AS nb_sources,
                       (SELECT COUNT(*) FROM migration_numerisation mn
                        JOIN migration_plan_parcellaire mp ON mp.id=mn.plan_id
                        WHERE mp.migration_id=mh.id) AS nb_numerises,
                       (SELECT COUNT(*) FROM migration_identifiant
                        WHERE migration_id=mh.id) AS nb_identifiants,
                       (SELECT COUNT(*) FROM migration_conflit_spatial
                        WHERE migration_id=mh.id) AS nb_conflits
                FROM migration_historique mh WHERE mh.id=:mid
            """), {"mid": migration_id})
            row = r.mappings().first()
            if not row:
                raise HTTPException(404, "Migration introuvable")
            return dict(row)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, str(e)[:200])


@router.post("/{migration_id}/avancer")
async def avancer_etape(
    migration_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EXEC)),
):
    """Avance le pipeline d'une étape via avancer_etape_migration()."""
    try:
        r = await db.execute(text(
            "SELECT avancer_etape_migration(:mid::UUID, :uid::UUID)"
        ), {"mid": migration_id, "uid": str(current_user.id)})
        result = r.scalar()
        await db.commit()
        return {"migration_id": migration_id, "nouvelle_etape": result}
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:300])


# ── 30.1 : Sources documentaires ─────────────────────────────────

@router.post("/{migration_id}/sources", status_code=201)
async def ajouter_source(
    migration_id: str,
    payload: SourceDocIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EXEC)),
):
    """30.1 — Enregistre une source documentaire à migrer."""
    sha = hashlib.sha256(
        f"{payload.reference_doc}:{payload.type_source}:{migration_id}".encode()
    ).hexdigest()
    src_id = str(uuid.uuid4())
    try:
        await db.execute(text("""
            INSERT INTO migration_source_doc
                (id, migration_id, type_source, reference_doc,
                 titre, date_document, periode, lieu_conservation,
                 sha256_document, responsable_collecte, statut)
            VALUES
                (:id, :mid, :type, :ref, :titre, :date, :per,
                 :lieu, :sha, :uid, 'collecte')
        """), {
            "id": src_id, "mid": migration_id,
            "type": payload.type_source, "ref": payload.reference_doc,
            "titre": payload.titre, "date": payload.date_document,
            "per": payload.periode, "lieu": payload.lieu_conservation,
            "sha": sha, "uid": str(current_user.id),
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "UNIQUE" in str(e).upper() or "unique" in str(e).lower():
            raise HTTPException(409, f"Document déjà enregistré : {payload.reference_doc}")
        raise HTTPException(400, str(e)[:200])
    return {"id": src_id, "sha256": sha, "type_source": payload.type_source}


@router.get("/{migration_id}/sources")
async def lister_sources(
    migration_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_MIGRATION)),
):
    """30.1 — Liste les sources documentaires collectées."""
    try:
        r = await db.execute(text("""
            SELECT * FROM migration_source_doc
            WHERE migration_id=:mid ORDER BY created_at DESC
        """), {"mid": migration_id})
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


# ── 30.4 : Géoréférencement ──────────────────────────────────────

@router.post("/{migration_id}/gcps", status_code=201)
async def ajouter_gcp(
    migration_id: str,
    plan_id: str = Query(...),
    payload: GCPIn = ...,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EXEC + ["GEOMETRE","TOPOGRAPHE"])),
):
    """30.4 — Ajoute un Ground Control Point (GCP) pour le géoréférencement."""
    try:
        await db.execute(text("""
            INSERT INTO migration_gcp
                (id, plan_id, x_terrain, y_terrain,
                 x_image, y_image, description, created_at)
            VALUES
                (gen_random_uuid(), :pid, :xt, :yt, :xi, :yi, :desc, NOW())
        """), {
            "pid": plan_id, "xt": payload.x_terrain, "yt": payload.y_terrain,
            "xi": payload.x_image, "yi": payload.y_image,
            "desc": payload.description,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"plan_id": plan_id, "message": "GCP enregistré"}


@router.post("/{migration_id}/valider-georef")
async def valider_georeferencement(
    migration_id: str,
    srid_cible: int = Query(default=4326,
        description="SRID de la projection cible"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EXEC + ["GEOMETRE"])),
):
    """30.4 — Valide le géoréférencement via valider_georeferencement()."""
    try:
        r = await db.execute(text(
            "SELECT valider_georeferencement(:mid::UUID, :srid)"
        ), {"mid": migration_id, "srid": srid_cible})
        result = r.scalar()
        await db.commit()
        return {"migration_id": migration_id, "validation": result}
    except Exception as e:
        await db.rollback()
        if "RMS" in str(e).upper() or "WF30 BLOQUÉ" in str(e):
            raise HTTPException(400, f"Géoréférencement insuffisant : {str(e)[:300]}")
        raise HTTPException(400, str(e)[:200])


# ── 30.5 : Vectorisation ─────────────────────────────────────────

@router.post("/{migration_id}/vectorisations", status_code=201)
async def enregistrer_vectorisation(
    migration_id: str,
    payload: VectorisationIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EXEC + ["GEOMETRE","TOPOGRAPHE"])),
):
    """30.5 — Enregistre un polygone vectorisé.
    Trigger tg_tm2_vectorisation_check_geom valide la géométrie.
    """
    sha = hashlib.sha256(payload.geom_wkt.encode()).hexdigest()
    try:
        await db.execute(text("""
            INSERT INTO migration_vectorisation
                (id, plan_id, geom_wkt, surface_m2,
                 qualite_vectorisation, sha256_geom, created_at)
            VALUES
                (gen_random_uuid(), :pid, :geom, :surf,
                 :qual, :sha, NOW())
        """), {
            "pid": payload.plan_id, "geom": payload.geom_wkt,
            "surf": payload.surface_m2, "qual": payload.qualite_vectorisation,
            "sha": sha,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "géométrie" in str(e).lower() or "WKT" in str(e):
            raise HTTPException(400,
                f"Géométrie invalide (tg_tm2_vectorisation_check_geom) : {str(e)[:300]}")
        raise HTTPException(400, str(e)[:200])
    return {"plan_id": payload.plan_id, "surface_m2": payload.surface_m2,
            "sha256_geom": sha}


# ── 30.6 : Génération NICAD ──────────────────────────────────────

@router.post("/{migration_id}/generer-nicad")
async def generer_nicad_migration(
    migration_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EXEC)),
):
    """30.6 — Lance la génération NICAD via generer_nicad_migration()."""
    try:
        r = await db.execute(text(
            "SELECT generer_nicad_migration(:mid::UUID)"
        ), {"mid": migration_id})
        nb = r.scalar()
        await db.commit()
        return {"migration_id": migration_id,
                "nicad_generes": nb,
                "message": f"{nb} NICAD générés pour cette migration"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:300])


# ── 30.7 : Propriétaires ─────────────────────────────────────────

@router.post("/{migration_id}/proprietaires", status_code=201)
async def associer_proprietaire(
    migration_id: str,
    identifiant_id: str = Query(...),
    right_holder_id: str = Query(...),
    acte_ref: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EXEC + ["NOTAIRE"])),
):
    """30.7 — Associe un propriétaire à une parcelle migrée."""
    sha = hashlib.sha256(
        f"{identifiant_id}:{right_holder_id}:{acte_ref or ''}".encode()
    ).hexdigest()
    try:
        await db.execute(text("""
            INSERT INTO migration_proprietaire
                (id, identifiant_id, right_holder_id,
                 acte_reference, sha256_association, created_at)
            VALUES
                (gen_random_uuid(), :iid, :rhid, :acte, :sha, NOW())
        """), {
            "iid": identifiant_id, "rhid": right_holder_id,
            "acte": acte_ref, "sha": sha,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"identifiant_id": identifiant_id,
            "right_holder_id": right_holder_id, "sha256": sha}


# ── 30.8 : Validation CCFM ───────────────────────────────────────

@router.post("/{migration_id}/valider-ccfm")
async def valider_ccfm_migration(
    migration_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EXEC + ["CHEF_CCFM"])),
):
    """30.8 — Lance le moteur CCFM sur les parcelles migrées."""
    try:
        r = await db.execute(text(
            "SELECT valider_ccfm_migration(:mid::UUID)"
        ), {"mid": migration_id})
        result = r.scalar()
        await db.commit()
        return {"migration_id": migration_id, "ccfm_result": result}
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:300])


# ── 30.9 : Conflits spatiaux ─────────────────────────────────────

@router.get("/{migration_id}/conflits")
async def lister_conflits_migration(
    migration_id: str,
    resolu: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_MIGRATION)),
):
    """30.9 — Conflits géométriques détectés (tg_tm3_conflit_spatial_alert)."""
    where = "WHERE migration_id=:mid"
    params: dict = {"mid": migration_id}
    if resolu is not None:
        where += " AND resolu=:r"; params["r"] = resolu
    try:
        r = await db.execute(text(
            f"SELECT * FROM migration_conflit_spatial {where} ORDER BY created_at DESC"
        ), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _log
        _log.getLogger("foncier").warning("Erreur %s: %s", request.url.path if hasattr(locals().get("request", object()), "url") else "?", e)
        return []


# ── 30.10 : Intégration BGU + ANNF ───────────────────────────────

@router.post("/{migration_id}/integrer-bgu-annf")
async def integrer_bgu_annf(
    migration_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(ROLES_EXEC + ["RESPONSABLE_BGU"])),
):
    """30.10 — Intègre les données dans BGU et archive dans ANNF.
    Appelle integrer_bgu_annf_migration() puis archive automatiquement
    dans annf_archive_links avec source_module='wf30_migration'.
    """
    try:
        r = await db.execute(text(
            "SELECT integrer_bgu_annf_migration(:mid::UUID)"
        ), {"mid": migration_id})
        result = r.scalar()
        await db.commit()
        return {
            "migration_id": migration_id,
            "integration_result": result,
            "message": "BGU mis à jour + archives créées dans ANNF",
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:300])
