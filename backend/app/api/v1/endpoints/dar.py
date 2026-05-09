"""
FONCIER+ — Module DAR (Direction des Archives et de la Restauration)
====================================================================

Chaque module fonctionnel dispose de sa propre Direction des Archives (DAR).
Le WF30 est un sous-module de chaque DAR : il migre les archives historiques
spécifiques au module concerné vers le système numérique FONCIER+.

Structure des DAR par module :
  Urbanisme  → /urbanisme/dar  — archives arrêtés, RNAF papier, plans directeurs
  Cadastre   → /cadastre/dar   — archives NICAD papier, plans cadastraux, BD Topo
  CCFM       → /ccfm/dar       — archives certificats CCFM antérieurs à v1.0
  Domaine    → /domaine/dar    — archives expropriations, concessions, régularisations
  Notaire    → /notaire/dar    — archives actes notariés papier, mutations anciennes
  Banque     → /banque/dar     — archives hypothèques, actes de mainlevée anciens
  Justice    → /justice/dar    — archives jugements fonciers, greffes judiciaires
  Commune    → /commune/dar    — archives dépôts fonciers communaux anciens
  ANNF       → /annf/dar       — supervision archives nationales, scellement central
  BGU        → /bgu/dar        — archives plans graphiques papier, cartes topographiques
  JO         → /jo/dar         — archives parutions JO papier, journaux anciens

Chaque instance DAR partage le même pipeline WF30 (10 étapes) mais migre
des données structurellement différentes selon son module parent.

Préfixes :
  /v1/{module}/dar                 — tableau de bord DAR du module
  /v1/{module}/dar/wf30            — pipeline migration WF30 du module
  /v1/{module}/dar/wf30/{id}/...   — étapes du pipeline

Ce fichier expose le routeur DAR central.
Les modules enfants (urbanisme, cadastre, etc.) montent ce routeur
sous leur propre préfixe.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, get_current_user
from app.core.scope_filter import ScopeFilter

# ── Rôles DAR ─────────────────────────────────────────────────────────────────
_ROLES_DAR_LECTURE = [
    "ADMIN", "AUDITEUR", "RESPONSABLE_ANNF",
    "ARCHIVISTE_ANNF", "DIRECTEUR_URBANISME", "DIRECTEUR_CADASTRE",
    "DIRECTEUR_DOMAINE", "CHEF_CCFM", "NOTAIRE", "BANQ_DIRECTEUR",
    "JUGE_FONCIER", "MAIRE",
]
_ROLES_DAR_MIGRATION = [
    "ADMIN", "ARCHIVISTE_ANNF", "RESPONSABLE_ANNF",
    "DIRECTEUR_URBANISME", "DIRECTEUR_CADASTRE",
]
_ROLES_DAR_VALIDATION = [
    "ADMIN", "RESPONSABLE_ANNF",
    "DIRECTEUR_URBANISME", "DIRECTEUR_CADASTRE",
]

# ── Schémas ───────────────────────────────────────────────────────────────────

class DARMigrationIn(BaseModel):
    """Démarrer une migration WF30 depuis le DAR d'un module."""
    module_source:    str = Field(..., min_length=2,
        description="Module source : urbanisme|cadastre|ccfm|domaine|notaire|banque|justice|commune|bgu|jo")
    source:           str = Field(..., description="archives_papier|ancien_logiciel|externe|mixte")
    description:      str = Field(..., min_length=10)
    periode_couverte: Optional[str] = Field(None, description="ex: 1960-2010")
    region:           Optional[str] = Field(None, description="Code région (NIA, ZDR, …) ou null=national")
    volume_estime:    Optional[int] = Field(None, ge=1, description="Nombre estimé de documents")


class DARSourceDocIn(BaseModel):
    """Enregistrer un document source dans la DAR."""
    reference_doc:   str   = Field(..., min_length=3)
    type_doc:        str   = Field(...,
        description="plan_cadastral|arrete_foncier|acte_notarie|jugement|titre|carte|autre")
    geom_wkt:        Optional[str] = Field(None, description="WKT POLYGON SRID 4326 si géoréférencé")
    surface_m2:      Optional[float] = Field(None, gt=0)
    date_document:   Optional[str] = Field(None, description="YYYY ou YYYY-MM-DD")
    qualite_scan:    str = Field(default="bonne",
        description="excellente|bonne|acceptable|degradee")
    observations:    Optional[str] = None


class DARGCPIn(BaseModel):
    """Point d'appui géographique pour le géoréférencement."""
    source_x:    float = Field(..., ge=0,
        description='Coordonnée X source en pixels')
    source_y:    float = Field(..., ge=0,
        description='Coordonnée Y source en pixels')
    cible_lon:   float = Field(..., ge=-180, le=180,
        description='Longitude cible WGS84 — entre -180 et 180')
    cible_lat:   float = Field(..., ge=-90, le=90,
        description='Latitude cible WGS84 — entre -90 et 90')
    precision_m: Optional[float] = Field(None, ge=0,
        description='Précision estimée en mètres')


class DARValidationIn(BaseModel):
    """Valider une étape du pipeline WF30."""
    observations: Optional[str] = None
    force:        bool = Field(default=False, description="Forcer la validation même avec avertissements")


# ── Routeur central DAR ───────────────────────────────────────────────────────
# Ce routeur est importé par chaque module avec un préfixe différent.
# Ex: app.include_router(dar_router, prefix="/v1/urbanisme/dar")

router = APIRouter(tags=["DAR — Direction des Archives"])


@router.get("/tableau-de-bord", response_model=dict,
    summary="Tableau de bord DAR du module")
async def dar_tableau_bord(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ROLES_DAR_LECTURE)),
):
    """KPIs de la Direction des Archives : volumes, taux de numérisation,
    migrations en cours, archives scellées ANNF."""
    try:
        scope = ScopeFilter.from_user(current_user)
        where = "WHERE 1=1"
        params: dict = {}
        if not scope.peut_voir_national and scope.region:
            where += " AND region = :r"
            params["r"] = scope.region

        r_total = await db.execute(text(
            f"SELECT COUNT(*) FROM migration_historique {where}"), params)
        r_en_cours = await db.execute(text(
            f"SELECT COUNT(*) FROM migration_historique {where} "
            f"AND statut NOT IN ('ARCHIVE','ANNULE','REJETE')"), params)
        r_docs = await db.execute(text(
            f"SELECT COALESCE(SUM(volume_documente),0) FROM migration_historique {where}"), params)

        return {
            "migrations_total":   r_total.scalar() or 0,
            "migrations_en_cours": r_en_cours.scalar() or 0,
            "documents_migres":   r_docs.scalar() or 0,
            "scope":              scope.filtres_actifs(),
        }
    except Exception as e:
        import logging as _l
        _l.getLogger("dar").warning("tableau_bord: %s", e)
        return {"migrations_total": 0, "migrations_en_cours": 0, "documents_migres": 0}


@router.post("/wf30", status_code=201, response_model=dict,
    summary="Démarrer une migration WF30 dans ce DAR")
async def demarrer_migration_wf30(
    payload: DARMigrationIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ROLES_DAR_MIGRATION)),
):
    """Démarre le pipeline WF30 depuis la Direction des Archives du module."""
    try:
        import uuid as _uuid
        migration_id = str(_uuid.uuid4())
        agent_id = (current_user["id"] if isinstance(current_user, dict)
                    else getattr(current_user, "id", ""))
        await db.execute(text("""
            INSERT INTO migration_historique
                (id, module_source, source, description,
                 periode_couverte, region, volume_estime,
                 statut, demarre_par, date_demarrage)
            VALUES
                (:id, :mod, :src, :desc,
                 :periode, :region, :vol,
                 'EN_COURS', :agent, NOW())
        """), {
            "id":      migration_id,
            "mod":     payload.module_source,
            "src":     payload.source,
            "desc":    payload.description,
            "periode": payload.periode_couverte,
            "region":  payload.region,
            "vol":     payload.volume_estime,
            "agent":   agent_id,
        })
        await db.commit()
        return {
            "migration_id": migration_id,
            "module_source": payload.module_source,
            "statut":        "EN_COURS",
            "message":       f"Pipeline WF30 démarré pour le module {payload.module_source}",
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e)[:200])


@router.get("/wf30", response_model=list,
    summary="Lister les migrations WF30 de ce DAR")
async def lister_migrations_wf30(
    statut:  Optional[str] = Query(None),
    page:    int = Query(1, ge=1),
    limit:   int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ROLES_DAR_LECTURE)),
):
    """Liste les pipelines WF30 filtrés par région et statut."""
    try:
        scope  = ScopeFilter.from_user(current_user)
        where  = "WHERE 1=1"
        params: dict = {"l": limit, "o": (page-1)*limit}
        if statut:
            where += " AND m.statut::TEXT = :st"
            params["st"] = statut
        if not scope.peut_voir_national and scope.region:
            where += " AND m.region = :_scope_region"
            params["_scope_region"] = scope.region
        r = await db.execute(text(f"""
            SELECT m.id, m.module_source, m.source, m.description,
                   m.statut::TEXT, m.region, m.volume_estime, m.volume_documente,
                   m.date_demarrage, m.etape_courante
            FROM migration_historique m
            {where}
            ORDER BY m.date_demarrage DESC
            LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _l
        _l.getLogger("dar").warning("lister_migrations: %s", e)
        return []


@router.get("/wf30/{migration_id}", response_model=dict,
    summary="Détail d'une migration WF30")
async def get_migration_wf30(
    migration_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ROLES_DAR_LECTURE)),
):
    """Détail complet d'un pipeline WF30 : étapes, sources, GCP, rapport."""
    try:
        r = await db.execute(text("""
            SELECT m.*, COUNT(s.id) AS nb_sources, COUNT(g.id) AS nb_gcp
            FROM migration_historique m
            LEFT JOIN migration_source_doc s ON s.migration_id = m.id
            LEFT JOIN migration_gcp g ON g.migration_id = m.id
            WHERE m.id = :id
            GROUP BY m.id
        """), {"id": migration_id})
        row = r.mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Migration non trouvée")
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        import logging as _l
        _l.getLogger("dar").warning("get_migration: %s", e)
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.post("/wf30/{migration_id}/sources", status_code=201, response_model=dict,
    summary="Ajouter un document source (étape 30.1-30.2)")
async def ajouter_source_doc(
    migration_id: str,
    payload: DARSourceDocIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ROLES_DAR_MIGRATION)),
):
    """Enregistre un document source dans la DAR (archive papier à numériser)."""
    try:
        import uuid as _uuid
        doc_id = str(_uuid.uuid4())
        await db.execute(text("""
            INSERT INTO migration_source_doc
                (id, migration_id, reference_doc, type_doc, geom_wkt,
                 surface_m2, date_document, qualite_scan, observations)
            VALUES (:id, :mid, :ref, :type, :geom, :surf, :date, :qual, :obs)
        """), {
            "id":  doc_id, "mid": migration_id,
            "ref": payload.reference_doc, "type": payload.type_doc,
            "geom": payload.geom_wkt, "surf": payload.surface_m2,
            "date": payload.date_document, "qual": payload.qualite_scan,
            "obs":  payload.observations,
        })
        await db.commit()
        return {"source_id": doc_id, "statut": "enregistre"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e)[:200])


@router.post("/wf30/{migration_id}/gcps", status_code=201, response_model=dict,
    summary="Ajouter un point d'appui GCP (étape 30.4)")
async def ajouter_gcp(
    migration_id: str,
    payload: DARGCPIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ROLES_DAR_MIGRATION)),
):
    """Enregistre un point d'appui géographique pour le géoréférencement.
    Minimum 4 GCP requis pour lancer l'étape de géoréférencement."""
    try:
        import uuid as _uuid
        gcp_id = str(_uuid.uuid4())
        await db.execute(text("""
            INSERT INTO migration_gcp
                (id, migration_id, source_x, source_y,
                 cible_lon, cible_lat, precision_m)
            VALUES (:id, :mid, :sx, :sy, :lon, :lat, :prec)
        """), {
            "id": gcp_id, "mid": migration_id,
            "sx": payload.source_x, "sy": payload.source_y,
            "lon": payload.cible_lon, "lat": payload.cible_lat,
            "prec": payload.precision_m,
        })
        r_count = await db.execute(text(
            "SELECT COUNT(*) FROM migration_gcp WHERE migration_id = :mid"),
            {"mid": migration_id})
        nb_gcp = r_count.scalar() or 0
        await db.commit()
        return {
            "gcp_id": gcp_id,
            "nb_gcp_total": nb_gcp,
            "georef_possible": nb_gcp >= 4,
            "message": f"{nb_gcp} GCP enregistrés — " +
                       ("géoréférencement possible ✓" if nb_gcp >= 4
                        else f"encore {4-nb_gcp} GCP nécessaires"),
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e)[:200])


@router.post("/wf30/{migration_id}/avancer", response_model=dict,
    summary="Avancer le pipeline WF30 à l'étape suivante")
async def avancer_pipeline_wf30(
    migration_id: str,
    payload: DARValidationIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ROLES_DAR_VALIDATION)),
):
    """Avance le pipeline WF30 à l'étape suivante après validation.
    Séquence des étapes : COLLECTE → NUMERISATION → GEOREFERENCEMENT →
    VECTORISATION → IDENTIFICATION → VERIFICATION → INTEGRATION → ARCHIVE"""
    try:
        r = await db.execute(text(
            "SELECT statut::TEXT, etape_courante FROM migration_historique WHERE id = :id"),
            {"id": migration_id})
        row = r.mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Migration non trouvée")

        PIPELINE = [
            "COLLECTE", "NUMERISATION", "GEOREFERENCEMENT",
            "VECTORISATION", "IDENTIFICATION_NICAD",
            "ASSOCIATION_PROPRIETAIRES", "VERIFICATION",
            "INTEGRATION_NICAD_BGU", "ARCHIVAGE_ANNF", "CLOS",
        ]
        current = row["etape_courante"] or "COLLECTE"
        try:
            idx = PIPELINE.index(current)
            next_etape = PIPELINE[idx + 1] if idx + 1 < len(PIPELINE) else "CLOS"
        except ValueError:
            next_etape = "COLLECTE"

        new_statut = "ARCHIVE" if next_etape == "CLOS" else "EN_COURS"
        await db.execute(text("""
            UPDATE migration_historique
            SET etape_courante = :etape,
                statut         = :statut::TEXT,
                date_modification = NOW()
            WHERE id = :id
        """), {"etape": next_etape, "statut": new_statut, "id": migration_id})
        await db.commit()
        return {
            "migration_id": migration_id,
            "etape_precedente": current,
            "etape_courante":   next_etape,
            "statut":           new_statut,
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e)[:200])


@router.get("/wf30/{migration_id}/rapport", response_model=dict,
    summary="Rapport complet d'une migration WF30")
async def rapport_migration(
    migration_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ROLES_DAR_LECTURE)),
):
    """Rapport détaillé : progression, sources documentées, GCP,
    vérifications effectuées, intégration NICAD/BGU."""
    try:
        r_m = await db.execute(text(
            "SELECT * FROM migration_historique WHERE id = :id"),
            {"id": migration_id})
        mig = r_m.mappings().first()
        if not mig:
            raise HTTPException(status_code=404, detail="Migration non trouvée")

        r_s = await db.execute(text(
            "SELECT COUNT(*) FROM migration_source_doc WHERE migration_id = :id"),
            {"id": migration_id})
        r_g = await db.execute(text(
            "SELECT COUNT(*), AVG(residuel) FROM migration_gcp WHERE migration_id = :id"),
            {"id": migration_id})

        s_count = r_s.scalar() or 0
        g_row   = r_g.first()
        g_count = g_row[0] if g_row else 0
        g_resid = round(float(g_row[1]), 4) if g_row and g_row[1] else None

        return {
            "migration":    dict(mig),
            "sources":      s_count,
            "gcps":         g_count,
            "residuel_moy": g_resid,
            "georef_ok":    g_count >= 4,
        }
    except HTTPException:
        raise
    except Exception as e:
        import logging as _l
        _l.getLogger("dar").warning("rapport: %s", e)
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/archives", response_model=list,
    summary="Archives scellées dans ce DAR")
async def lister_archives_dar(
    type_archive: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ROLES_DAR_LECTURE)),
):
    """Liste les archives numériques scellées dans ce DAR,
    filtrées par type et région de l'agent."""
    try:
        scope  = ScopeFilter.from_user(current_user)
        where  = "WHERE 1=1"
        params: dict = {"l": limit, "o": (page-1)*limit}
        if type_archive:
            where += " AND a.type_archive::TEXT = :ta"
            params["ta"] = type_archive
        if not scope.peut_voir_national and scope.region:
            where += " AND a.region = :_scope_region"
            params["_scope_region"] = scope.region
        r = await db.execute(text(f"""
            SELECT a.id, a.ida, a.entite_type, a.entite_table,
                   a.type_archive::TEXT, a.scelle, a.region,
                   a.created_at
            FROM annf_archive_links a
            {where}
            ORDER BY a.created_at DESC
            LIMIT :l OFFSET :o
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        import logging as _l
        _l.getLogger("dar").warning("lister_archives: %s", e)
        return []
