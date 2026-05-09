"""
Filtrage acteur : module admin/public — ScopeFilter non applicable.

FONCIER+ ── Endpoints Monitoring Temps Réel

Routes :
  GET  /monitoring/stream           ── SSE — flux événements + alertes temps réel
  GET  /monitoring/tableau-bord     ── métriques instantanées
  GET  /monitoring/alertes          ── alertes actives
  GET  /monitoring/alertes/historique ── historique des alertes
  POST /monitoring/alertes/{id}/acquitter ── acquitter une alerte
  GET  /monitoring/evenements       ── événements récents (buffer mémoire)
  GET  /monitoring/sante            ── santé DB (vue v_monitoring_sante)
  GET  /monitoring/activite         ── activité récente (vue v_activite_recente)
  GET  /monitoring/stats-horaires   ── volume par heure (48h)
  GET  /monitoring/detecteurs       ── liste et état des détecteurs
  GET  /monitoring/config           ── configuration des seuils
  PATCH /monitoring/config/{cle}    ── modifier un seuil
"""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_role

_log    = logging.getLogger("foncier.monitoring_api")
router  = APIRouter(prefix="/monitoring", tags=["Monitoring temps réel"])

_SUPER  = ["ADMIN", "DIRECTEUR_URBANISME", "SECRETAIRE_GENERAL", "AUDITEUR"]
_ADMIN  = ["ADMIN"]


# ── SSE — flux temps réel ─────────────────────────────────────────


class AlerteOut(BaseModel):
    id: str; niveau: str; message: str; source: Optional[str]=None
    created_at: Optional[str]=None; resolu: bool=False
    class Config: from_attributes=True

@router.get("/stream", response_model=list,
    summary="Liste stream")
async def stream_monitoring(
    current_user=Depends(get_current_user),
):
    """
    Server-Sent Events : flux continu des événements et alertes en temps réel.

    Format des messages :
      data: {"type": "evenement", "payload": {...}}\\n\\n
      data: {"type": "alerte",    "payload": {...}}\\n\\n
      data: {"type": "ping",      "ts": "..."}\\n\\n

    Connexion maintenue jusqu'à déconnexion du client.
    Max 50 clients simultanés.
    """
    from app.services.monitoring_engine import MonitoringEngine

    engine = MonitoringEngine.instance()
    queue  = engine.alertes.abonner()

    async def generator():
        try:
            # Envoyer l'état initial
            yield f"data: {json.dumps({'type': 'connected', 'payload': {'status': 'ok'}})}\n\n"

            while True:
                try:
                    # Attendre un message avec timeout (ping keepalive)
                    item = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.TimeoutError:
                    # Ping pour maintenir la connexion
                    import datetime
                    yield f"data: {json.dumps({'type': 'ping', 'ts': datetime.datetime.utcnow().isoformat()})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            engine.alertes.desabonner(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


# ── Tableau de bord ───────────────────────────────────────────────

@router.get("/tableau-bord", response_model=list,
    summary="Liste tableau bord")
async def tableau_bord(
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Métriques temps réel : parcelles, workflows, validations, alertes, santé."""
    from app.services.monitoring_engine import MonitoringEngine
    try:
        tb = await MonitoringEngine.instance().tableau_bord(db)
        return tb.as_dict()
    except Exception as e:
        _log.warning("tableau_bord: %s", e)
        raise HTTPException(500, "Erreur lors du calcul du tableau de bord")


# ── Alertes ───────────────────────────────────────────────────────

@router.get("/alertes", response_model=list,
    summary="Liste alertes")
async def alertes_actives(
    current_user=Depends(require_role(_SUPER)),
):
    """Liste des alertes actives (non acquittées), triées par niveau."""
    from app.services.monitoring_engine import MonitoringEngine
    alertes = MonitoringEngine.instance().alertes.actives()
    # Trier CRITICAL > WARNING > INFO
    ordre = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    alertes_triees = sorted(alertes, key=lambda a: ordre.get(a.niveau.value, 9))
    return [a.as_dict() for a in alertes_triees]


@router.get("/alertes/historique", response_model=list,
    summary="Liste alertes/historique")
async def historique_alertes(
    limite: int = Query(default=50, ge=1, le=500),
    niveau: Optional[str] = Query(default=None),
    db:     AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Historique des alertes depuis la DB (alerte_monitoring)."""
    try:
        where = "WHERE 1=1"
        params: dict = {"lim": limite}
        if niveau:
            where += " AND niveau=:niv"
            params["niv"] = niveau.upper()
        r = await db.execute(text(f"""
            SELECT alerte_id, niveau, detecteur, titre, message,
                   details_json, acquittee, acquittee_par,
                   acquittee_at, sha256, created_at
            FROM alerte_monitoring
            {where}
            ORDER BY created_at DESC
            LIMIT :lim
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("historique_alertes: %s", e)
        return []


@router.post("/alertes/{alerte_id}/acquitter")
async def acquitter_alerte(
    alerte_id:    str,
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Acquitte une alerte active (mémoire + DB)."""
    from app.services.monitoring_engine import MonitoringEngine

    user_id = str((current_user.get("id","") if isinstance(current_user, dict) else getattr(current_user,"id","")))

    # Acquittement en mémoire
    ok_mem = MonitoringEngine.instance().alertes.acquitter(alerte_id, user_id)

    # Acquittement en DB
    try:
        await db.execute(text("""
            UPDATE alerte_monitoring
            SET acquittee=TRUE, acquittee_par=:uid::uuid
            WHERE alerte_id=:aid::uuid AND acquittee=FALSE
        """), {"uid": user_id, "aid": alerte_id})
        await db.commit()
    except Exception as e:
        await db.rollback()
        _log.warning("acquitter_alerte DB: %s", e)

    if not ok_mem:
        raise HTTPException(404, f"Alerte {alerte_id} introuvable ou déjà acquittée")

    # Diffuser l'acquittement aux clients SSE
    await MonitoringEngine.instance().alertes.diffuser({
        "type": "alerte_acquittee",
        "payload": {"alerte_id": alerte_id, "acquittee_par": user_id},
    })
    return {"acquittee": True, "alerte_id": alerte_id}


# ── Événements ────────────────────────────────────────────────────

@router.get("/evenements", response_model=list,
    summary="Liste evenements")
async def evenements_recents(
    limite:    int           = Query(default=50, ge=1, le=200),
    categorie: Optional[str] = Query(default=None),
    current_user=Depends(require_role(_SUPER)),
):
    """
    Événements récents depuis le buffer mémoire du monitoring engine.
    Catégories : PARCELLE | WORKFLOW | VALIDATION | REGLE | VERROU | TRANSACTION | SYSTEME
    """
    from app.services.monitoring_engine import MonitoringEngine
    evts = MonitoringEngine.instance().events_recents(limite=limite * 2)
    if categorie:
        evts = [e for e in evts if e.get("categorie") == categorie.upper()]
    return evts[:limite]


# ── Santé DB ──────────────────────────────────────────────────────

@router.get("/sante", response_model=list,
    summary="Liste sante")
async def sante_systeme(
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Santé système depuis la vue v_monitoring_sante (DB)."""
    try:
        r = await db.execute(text("SELECT * FROM v_monitoring_sante"))
        row = r.mappings().first()
        return dict(row) if row else {}
    except Exception as e:
        _log.warning("sante_systeme: %s", e)
        return {"erreur": str(e)[:100]}


@router.get("/activite", response_model=list,
    summary="Liste activite")
async def activite_recente(
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Activité récente par catégorie (vue v_activite_recente, 1h)."""
    try:
        r = await db.execute(text("SELECT * FROM v_activite_recente"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("activite_recente: %s", e)
        return []


@router.get("/stats-horaires", response_model=list,
    summary="Liste stats horaires")
async def stats_horaires(
    heures: int  = Query(default=24, ge=1, le=168),
    db:     AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Volume d'activité par heure sur les N dernières heures."""
    try:
        r = await db.execute(
            text("SELECT * FROM monitoring_stats_par_heure(:h)"),
            {"h": heures}
        )
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("stats_horaires: %s", e)
        return []


# ── Détecteurs ────────────────────────────────────────────────────

@router.get("/detecteurs", response_model=list,
    summary="Liste detecteurs")
async def lister_detecteurs(
    current_user=Depends(require_role(_SUPER)),
):
    """Liste des détecteurs d'anomalies et leur état."""
    from app.services.monitoring_engine import MonitoringEngine
    engine = MonitoringEngine.instance()
    return [
        {
            "id":          d.id,
            "description": d.description,
            "actif":       True,
        }
        for d in engine.detecteurs
    ]


# ── Configuration ─────────────────────────────────────────────────

@router.get("/config", response_model=list,
    summary="Liste config")
async def get_config(
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Configuration actuelle du moteur de monitoring."""
    try:
        r = await db.execute(text(
            "SELECT cle, valeur, description FROM monitoring_config ORDER BY cle"
        ))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("get_config: %s", e)
        return []


class ConfigUpdate(BaseModel):
    valeur: str = Field(..., min_length=1,
        description='Nouvelle valeur de la configuration')


@router.patch("/config/{cle}")
async def update_config(
    cle:          str,
    payload:      ConfigUpdate,
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Met à jour un paramètre de configuration du monitoring."""
    try:
        await db.execute(text("""
            UPDATE monitoring_config
            SET valeur=:val, modifie_par=:uid::uuid, updated_at=NOW()
            WHERE cle=:cle
        """), {"val": payload.valeur, "uid": str((current_user.get("id","") if isinstance(current_user, dict) else getattr(current_user,"id",""))), "cle": cle})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"cle": cle, "valeur": payload.valeur}


@router.get("/stats-moteur", response_model=list,
    summary="Liste stats moteur")
async def stats_moteur(
    current_user=Depends(require_role(_SUPER)),
):
    """Statistiques internes du moteur de monitoring (compteurs mémoire)."""
    from app.services.monitoring_engine import MonitoringEngine
    engine = MonitoringEngine.instance()
    return {
        "stats_par_categorie": engine.stats(),
        "nb_events_buffer":    len(engine._events),
        "nb_alertes_memoire":  len(engine.alertes._alertes),
        "nb_clients_sse":      len(engine.alertes._clients),
        "running":             engine._running,
        "latence_moy_ms":      round(sum(engine._latences) / len(engine._latences), 1)
                               if engine._latences else 0,
    }
