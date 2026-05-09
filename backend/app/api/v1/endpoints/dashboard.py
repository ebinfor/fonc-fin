from pydantic import BaseModel, Field
from typing import Optional, List
"""
Filtrage acteur : module admin/public — ScopeFilter non applicable.

FONCIER+ ── Endpoints Tableaux de Bord Décisionnels Nationaux

Routes :
  GET  /dashboard/national              ── tableau de bord complet (KPIs+zones+séries+synthèse)
  GET  /dashboard/kpis                  ── KPIs uniquement (polling rapide)
  GET  /dashboard/zones                 ── données cartographiques par région
  GET  /dashboard/series                ── séries temporelles pour graphiques
  GET  /dashboard/synthese              ── synthèse stratégique automatique
  GET  /dashboard/region/{code}         ── tableau de bord d'une région
  GET  /dashboard/conflits              ── vue conflits par région
  GET  /dashboard/validation            ── performance validation par région
  GET  /dashboard/agents                ── activité agents par région
  GET  /dashboard/export/csv            ── export CSV institutionnel
  GET  /dashboard/export/json           ── export JSON institutionnel
  POST /dashboard/cache/invalider       ── forcer recalcul (admin)
  GET  /dashboard/db/kpis               ── KPIs depuis vues DB (v_kpi_national)
  GET  /dashboard/db/par-region         ── v_kpi_par_region
  GET  /dashboard/db/mutations-series   ── dashboard_series_mutations()
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_role

_log    = logging.getLogger("foncier.dashboard_api")
router  = APIRouter(prefix="/dashboard", tags=["Tableaux de bord nationaux"])

_SUPER  = ["ADMIN", "DIRECTEUR_URBANISME", "SECRETAIRE_GENERAL",
           "AUDITEUR", "DIRECTEUR_CADASTRE"]
_ADMIN  = ["ADMIN"]


# ── Tableau de bord complet ───────────────────────────────────────


class KPIOut(BaseModel):
    cle: str; valeur: Optional[float]=None; unite: Optional[str]=None
    region: Optional[str]=None; periode: Optional[str]=None
    class Config: from_attributes=True

@router.get("/national", response_model=list,
    summary="Liste national")
async def tableau_bord_national(
    region:   Optional[str] = Query(default=None, description="NI-01 à NI-08 ou None pour national"),
    nb_mois:  int           = Query(default=12, ge=1, le=60),
    no_cache: bool          = Query(default=False),
    db:       AsyncSession  = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """
    Tableau de bord décisionnel national complet.

    Retourne :
    - **kpis** : 13 indicateurs clés (K01-K13)
    - **zones** : données par région Niger avec KPIs géographiques
    - **series** : 4 séries temporelles (mutations, conflits, validations, risques)
    - **synthese** : analyse stratégique automatique avec recommandations
    - **filtres** : paramètres de la requête

    Cache de 5 minutes (invalider avec POST /cache/invalider si nécessaire).
    """
    from app.services.dashboard_engine import DashboardEngine
    tb = await DashboardEngine.tableau_bord_national(
        db,
        region=region.upper() if region else None,
        nb_mois=nb_mois,
        avec_cache=not no_cache,
    )
    return tb.as_dict()


@router.get("/kpis", response_model=list,
    summary="Liste kpis")
async def kpis(
    region:  Optional[str] = Query(default=None),
    db:      AsyncSession  = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """
    KPIs uniquement — plus rapide pour les mises à jour fréquentes (polling).
    Retourne la liste des 13 KPIs avec valeur, variation et tendance.
    """
    from app.services.dashboard_engine import DashboardEngine
    kpis = await DashboardEngine.kpis_seulement(
        db, region=region.upper() if region else None
    )
    return [k.as_dict() for k in kpis]


@router.get("/zones", response_model=list,
    summary="Liste zones")
async def zones(
    db:          AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Données cartographiques par région (score risque, parcelles, mutations, conflits)."""
    from app.services.dashboard_engine import DashboardEngine
    zones = await DashboardEngine.zones_stats(db)
    return [z.as_dict() for z in zones]


@router.get("/series", response_model=list,
    summary="Liste series")
async def series_temporelles(
    region:  Optional[str] = Query(default=None),
    nb_mois: int           = Query(default=12, ge=1, le=60),
    db:      AsyncSession  = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """4 séries temporelles : mutations, conflits, validations, risques zones."""
    from app.services.dashboard_engine import DashboardEngine
    series = await DashboardEngine.series_temporelles(
        db, region=region.upper() if region else None, nb_mois=nb_mois
    )
    return [s.as_dict() for s in series]


@router.get("/synthese", response_model=list,
    summary="Liste synthese")
async def synthese_strategique(
    region:  Optional[str] = Query(default=None),
    db:      AsyncSession  = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """
    Synthèse stratégique automatique.
    Génère un rapport exécutif avec indicateurs clés, alertes, tendances
    et recommandations prioritaires basés sur les KPIs calculés.
    """
    from app.services.dashboard_engine import DashboardEngine
    tb = await DashboardEngine.tableau_bord_national(
        db, region=region.upper() if region else None
    )
    return tb.synthese.as_dict() if tb.synthese else {}


@router.get("/region/{code}", response_model=dict)
async def tableau_bord_regional(
    code:    str,
    nb_mois: int          = Query(default=12, ge=1, le=60),
    db:      AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Tableau de bord complet pour une région spécifique (NI-01 à NI-08)."""
    region = code.upper().strip()
    if region not in {f"NI-0{i}" for i in range(1, 9)}:
        raise HTTPException(400, f"Code région invalide : {region}. Attendu : NI-01 à NI-08")
    from app.services.dashboard_engine import DashboardEngine
    tb = await DashboardEngine.tableau_bord_national(db, region=region, nb_mois=nb_mois)
    return tb.as_dict()


# ── Vues spécialisées ─────────────────────────────────────────────

@router.get("/conflits", response_model=list,
    summary="Liste conflits")
async def conflits_par_region(
    db:          AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Conflits fonciers et litiges par région (vue v_conflits_par_region)."""
    try:
        r = await db.execute(text("SELECT * FROM v_conflits_par_region"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("conflits_par_region: %s", e)
        return []


@router.get("/validation", response_model=list,
    summary="Liste validation")
async def performance_validation(
    db:          AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Performance de validation par région (délais, taux approbation)."""
    try:
        r = await db.execute(text("SELECT * FROM v_performance_validation"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("performance_validation: %s", e)
        return []


@router.get("/agents", response_model=list,
    summary="Liste agents")
async def activite_agents(
    db:          AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Activité des agents par région (nb agents actifs, opérations)."""
    try:
        r = await db.execute(text("SELECT * FROM v_activite_agents"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("activite_agents: %s", e)
        return []


# ── Exports institutionnels ───────────────────────────────────────

@router.get("/export/csv", response_model=list,
    summary="Liste export/csv")
async def export_csv(
    region:      Optional[str] = Query(default=None),
    db:          AsyncSession  = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """
    Export CSV pour rapports institutionnels.
    Contient : KPIs, données régionales, synthèse stratégique.
    Encodage UTF-8 avec BOM pour compatibilité Excel.
    """
    from app.services.dashboard_engine import DashboardEngine
    contenu, ctype = await DashboardEngine.exporter(
        db, format="csv",
        region=region.upper() if region else None,
    )
    # BOM UTF-8 pour Excel
    contenu_bom = "\ufeff" + contenu
    return Response(
        content=contenu_bom.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="foncier_dashboard_'
                f'{(region or "national").lower()}'
                f'_{__import__("datetime").date.today().isoformat()}.csv"'
            )
        },
    )


@router.get("/export/json", response_model=list,
    summary="Liste export/json")
async def export_json(
    region:      Optional[str] = Query(default=None),
    db:          AsyncSession  = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """
    Export JSON structuré pour intégrations institutionnelles.
    Format : rapport complet avec métadonnées FONCIER+ v107.
    """
    from app.services.dashboard_engine import DashboardEngine
    contenu, ctype = await DashboardEngine.exporter(
        db, format="json",
        region=region.upper() if region else None,
    )
    return Response(
        content=contenu.encode("utf-8"),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="foncier_rapport_'
                f'{(region or "national").lower()}'
                f'_{__import__("datetime").date.today().isoformat()}.json"'
            )
        },
    )


# ── Administration ────────────────────────────────────────────────

@router.post("/cache/invalider")
async def invalider_cache(
    current_user=Depends(require_role(_ADMIN)),
):
    """Force le recalcul de tous les tableaux de bord (invalide le cache)."""
    from app.services.dashboard_engine import DashboardEngine
    DashboardEngine.invalider_cache()
    return {"invalide": True, "message": "Cache tableau de bord réinitialisé"}


# ── Accès direct aux vues DB ─────────────────────────────────────

@router.get("/db/kpis", response_model=list,
    summary="Liste db/kpis")
async def db_kpi_national(
    db:          AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """KPIs nationaux depuis la vue DB v_kpi_national (sans cache engine)."""
    try:
        r = await db.execute(text("SELECT * FROM v_kpi_national"))
        row = r.mappings().first()
        return dict(row) if row else {}
    except Exception as e:
        _log.warning("db_kpi_national: %s", e)
        return {}


@router.get("/db/par-region", response_model=list,
    summary="Liste db/par region")
async def db_kpi_par_region(
    db:          AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """KPIs par région depuis v_kpi_par_region."""
    try:
        r = await db.execute(text("SELECT * FROM v_kpi_par_region"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("db_kpi_par_region: %s", e)
        return []


@router.get("/db/mutations-series", response_model=list,
    summary="Liste db/mutations series")
async def db_mutations_series(
    nb_mois: int           = Query(default=12, ge=1, le=60),
    region:  Optional[str] = Query(default=None),
    db:      AsyncSession  = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Série temporelle des mutations via dashboard_series_mutations()."""
    try:
        r = await db.execute(
            text("SELECT * FROM dashboard_series_mutations(:nb, :reg)"),
            {"nb": nb_mois, "reg": region.upper() if region else None}
        )
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("db_mutations_series: %s", e)
        return []
