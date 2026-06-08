from pydantic import Field
"""
Filtrage acteur : module admin/public — ScopeFilter non applicable.

FONCIER+ ── Endpoints Analyse Prédictive des Risques

Routes :
  POST /risk/analyser/parcelle/{id}    ── score risque parcelle
  POST /risk/analyser/zone/{region}    ── score risque zone
  POST /risk/analyser/utilisateur/{id} ── score risque utilisateur
  POST /risk/analyser/operation/{type} ── score risque opération
  GET  /risk/tableau-bord              ── métriques globales
  GET  /risk/carte                     ── données carte (toutes zones)
  GET  /risk/parcelles-top             ── top parcelles à risque
  GET  /risk/alertes                   ── alertes prédictives actives
  GET  /risk/alertes/historique        ── historique alertes
  POST /risk/alertes/{id}/acquitter    ── acquitter une alerte
  GET  /risk/zones-stats               ── statistiques par zone
  GET  /risk/config                    ── configuration seuils
  PATCH /risk/config/{cle}             ── modifier un seuil
  GET  /risk/rapport-global            ── rapport complet (toutes entités)
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_role

_log    = logging.getLogger("foncier.risk_api")
router  = APIRouter(prefix="/risk", tags=["Analyse prédictive des risques"])

_SUPER  = ["ADMIN", "DIRECTEUR_URBANISME", "SECRETAIRE_GENERAL", "AUDITEUR"]
_ADMIN  = ["ADMIN"]


# ── Analyse par entité ────────────────────────────────────────────


class RiskScoreOut(BaseModel):
    parcelle_id: Optional[str]=None; niveau_risque: str; score: Optional[float]=None
    facteurs: list=[]
    class Config: from_attributes=True

class RiskConfigOut(BaseModel):
    cle: str; valeur: Optional[str]=None; description: Optional[str]=None
    class Config: from_attributes=True

@router.post("/analyser/parcelle/{parcelle_id}")
async def analyser_parcelle(
    parcelle_id:  str,
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Calcule le score de risque prédictif d'une parcelle.

    Analyse 6 dimensions :
    - A1 Conflits judiciaires et géographiques
    - A2 Fréquence de mutations historiques
    - A3 Comportement des utilisateurs impliqués
    - A4 Densité de mutations dans la commune
    - A5 Anomalies dans l'historique (verrous, blocages)
    - A6 Types d'opérations risqués simulés

    Retourne : score [0-100], niveau (FAIBLE/MOYEN/ÉLEVÉ/CRITIQUE),
               signaux détaillés, facteurs clés, recommandations.
    """
    from app.services.risk_engine import RiskEngine
    score = await RiskEngine.analyser_parcelle(
        db, parcelle_id,
        user_id=str((current_user.get("id","") if isinstance(current_user, dict) else getattr(current_user,"id","")))
    )
    return score.as_dict()


@router.post("/analyser/zone/{region}")
async def analyser_zone(
    region:       str,
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """
    Calcule le score de risque d'une zone géographique (région NI-XX).
    Analyse : densité conflits, taux mutations, anomalies historiques.
    """
    from app.services.risk_engine import RiskEngine
    score = await RiskEngine.analyser_zone(
        db, region.upper(),
        user_id=str((current_user.get("id","") if isinstance(current_user, dict) else getattr(current_user,"id","")))
    )
    return score.as_dict()


@router.post("/analyser/utilisateur/{user_id}")
async def analyser_utilisateur(
    user_id:      str,
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """
    Évalue le profil de risque comportemental d'un utilisateur.
    Analyse : taux refus, accès inhabituels, injections SQL, simulations échouées.
    """
    from app.services.risk_engine import RiskEngine
    score = await RiskEngine.analyser_utilisateur(
        db, user_id,
        appelant_id=str((current_user.get("id","") if isinstance(current_user, dict) else getattr(current_user,"id","")))
    )
    return score.as_dict()


@router.post("/analyser/operation/{op_type}")
async def analyser_operation(
    op_type:      str,
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Calcule le risque intrinsèque d'un type d'opération foncière.
    Combine le risque de base et les statistiques historiques d'échec.
    """
    from app.services.risk_engine import RiskEngine
    score = await RiskEngine.analyser_operation(
        db, op_type.upper(),
        user_id=str((current_user.get("id","") if isinstance(current_user, dict) else getattr(current_user,"id","")))
    )
    return score.as_dict()


# ── Tableau de bord global ────────────────────────────────────────

@router.get("/tableau-bord", response_model=list,
    summary="Liste tableau bord")
async def tableau_bord(
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Métriques globales de risque (vue v_risk_tableau_bord)."""
    try:
        r = await db.execute(text("SELECT * FROM v_risk_tableau_bord"))
        row = r.mappings().first()
        return dict(row) if row else {}
    except Exception as e:
        _log.warning("tableau_bord: %s", e)
        return {}


# ── Carte de risque ───────────────────────────────────────────────

@router.get("/carte", response_model=list,
    summary="Liste carte")
async def carte_risque(
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """
    Données cartographiques de risque par zone géographique.
    Retourne : [{ region, score, niveau, nb_parcelles, litiges_actifs, mutations_30j }]
    Utilisé pour la visualisation de la carte des risques.
    """
    try:
        r = await db.execute(text("SELECT * FROM v_risk_carte"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("carte_risque: %s", e)
        # Retourner les 8 régions avec score 0 si pas encore calculé
        return [
            {"region": f"NI-0{i}", "score": 0, "niveau": "FAIBLE",
             "nb_parcelles": 0, "litiges_actifs": 0, "mutations_30j": 0}
            for i in range(1, 9)
        ]


@router.get("/carte/recalculer", response_model=list,
    summary="Liste carte/recalculer")
async def recalculer_carte(
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Déclenche le recalcul des scores pour toutes les régions Niger."""
    from app.services.risk_engine import RiskEngine
    regions = ["NI-01", "NI-02", "NI-03", "NI-04",
               "NI-05", "NI-06", "NI-07", "NI-08"]
    resultats = []
    for reg in regions:
        score = await RiskEngine.analyser_zone(db, reg)
        resultats.append({
            "region": reg,
            "score": round(score.score, 1),
            "niveau": score.niveau.value,
        })
    return {"regions_calculees": len(resultats), "resultats": resultats}


# ── Top parcelles ─────────────────────────────────────────────────

@router.get("/parcelles-top", response_model=list,
    summary="Liste parcelles top")
async def parcelles_top(
    limite: int = Query(default=20, ge=1, le=50),
    niveau: Optional[str] = Query(default=None),
    region: Optional[str] = Query(default=None),
    db:     AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Top parcelles par score de risque décroissant."""
    try:
        where = "WHERE rs.entite_type = 'PARCELLE'"
        params: dict = {"lim": limite}
        if niveau:
            where += " AND rs.niveau = :niv"; params["niv"] = niveau.upper()
        if region:
            where += " AND p.region = :reg"; params["reg"] = region
        r = await db.execute(text(f"""
            SELECT rs.entite_id, p.nicad, p.statut, p.region, p.commune,
                   rs.score, rs.niveau, rs.facteurs_json, rs.updated_at
            FROM risk_score rs
            LEFT JOIN parcelles p ON p.id::TEXT = rs.entite_id
            {where}
            ORDER BY rs.score DESC
            LIMIT :lim
        """), params)
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("parcelles_top: %s", e)
        return []


# ── Alertes prédictives ───────────────────────────────────────────

@router.get("/alertes", response_model=list,
    summary="Liste alertes")
async def alertes_actives(
    current_user=Depends(require_role(_SUPER)),
    db: AsyncSession = Depends(get_db),
):
    """Alertes prédictives actives (non acquittées)."""
    try:
        r = await db.execute(text("SELECT * FROM v_risk_alertes_actives"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("alertes_actives: %s", e)
        return []


@router.get("/alertes/historique", response_model=list,
    summary="Liste alertes/historique")
async def historique_alertes(
    limite: int = Query(default=50, ge=1, le=500),
    niveau: Optional[str] = Query(default=None),
    db:     AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Historique complet des alertes prédictives."""
    try:
        where = "WHERE 1=1"
        params: dict = {"lim": limite}
        if niveau:
            where += " AND niveau=:niv"; params["niv"] = niveau.upper()
        r = await db.execute(text(f"""
            SELECT alerte_id, niveau, entite_type, entite_id,
                   titre, message, score, facteurs_json,
                   recommandation, acquittee, acquittee_par,
                   acquittee_at, sha256, created_at
            FROM risk_alert
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
    """Acquitte une alerte prédictive."""
    try:
        r = await db.execute(text("""
            UPDATE risk_alert
            SET acquittee=TRUE, acquittee_par=:uid::uuid
            WHERE alerte_id=:aid::uuid AND acquittee=FALSE
            RETURNING alerte_id
        """), {"uid": str((current_user.get("id","") if isinstance(current_user, dict) else getattr(current_user,"id",""))), "aid": alerte_id})
        updated = r.scalar()
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])

    if not updated:
        raise HTTPException(404, "Alerte non trouvée ou déjà acquittée")
    return {"acquittee": True, "alerte_id": alerte_id}


# ── Statistiques zones ────────────────────────────────────────────

@router.get("/zones-stats", response_model=list,
    summary="Liste zones stats")
async def zones_stats(
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Statistiques de risque par zone géographique."""
    try:
        r = await db.execute(text("SELECT * FROM risk_zones_stats()"))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("zones_stats: %s", e)
        return []


# ── Rapport global ────────────────────────────────────────────────

@router.get("/rapport-global", response_model=list,
    summary="Liste rapport global")
async def rapport_global(
    limite: int = Query(default=10, ge=5, le=50),
    db:     AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER + ["AUDITEUR"])),
):
    """
    Rapport global de risque sur toutes les entités actives.
    Analyse les parcelles, zones, utilisateurs et opérations les plus actives.
    """
    from app.services.risk_engine import RiskEngine
    rapport = await RiskEngine.rapport_global(db, limite_par_type=limite)
    return rapport.as_dict()


# ── Configuration ─────────────────────────────────────────────────

@router.get("/config", response_model=list,
    summary="Liste config")
async def get_config(
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_SUPER)),
):
    """Configuration actuelle du moteur de risque."""
    try:
        r = await db.execute(text(
            "SELECT cle, valeur, description FROM risk_config ORDER BY cle"
        ))
        return [dict(row) for row in r.mappings().all()]
    except Exception as e:
        _log.warning("get_config: %s", e)
        return []


class RiskConfigUpdate(BaseModel):
    valeur: str = Field(..., min_length=1,
        description='Nouvelle valeur de la configuration')


@router.patch("/config/{cle}")
async def update_config(
    cle:          str,
    payload:      RiskConfigUpdate,
    db:           AsyncSession = Depends(get_db),
    current_user=Depends(require_role(_ADMIN)),
):
    """Met à jour un paramètre de configuration du moteur de risque."""
    try:
        await db.execute(text("""
            UPDATE risk_config
            SET valeur=:val, modifie_par=:uid::uuid, updated_at=NOW()
            WHERE cle=:cle
        """), {"val": payload.valeur, "uid": str((current_user.get("id","") if isinstance(current_user, dict) else getattr(current_user,"id",""))), "cle": cle})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e)[:200])
    return {"cle": cle, "valeur": payload.valeur}


# ── Intégration pipeline décisionnel ─────────────────────────────

@router.get("/enrichir-decision", response_model=list,
    summary="Liste enrichir decision")
async def enrichir_decision(
    parcelle_id:   str,
    operation_type: str,
    db:            AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Enrichit une décision en cours avec le score de risque prédictif.
    Utilisé par le pipeline décisionnel avant validation finale.

    Retourne : { risk_score, risk_niveau, validation_renforcee, recommandations }
    Si risk_niveau = CRITIQUE → validation_renforcee = True (niveau +1)
    """
    from app.services.risk_engine import RiskEngine, NiveauRisque

    score_parcelle = await RiskEngine.analyser_parcelle(db, parcelle_id)
    score_operation = await RiskEngine.analyser_operation(db, operation_type.upper())

    # Score combiné : 70% parcelle + 30% opération
    score_combine = score_parcelle.score * 0.7 + score_operation.score * 0.3
    from app.services.risk_engine import ScoreCalculator
    niveau_combine = ScoreCalculator.niveau(score_combine)

    validation_renforcee = (niveau_combine == NiveauRisque.CRITIQUE)

    return {
        "parcelle_id":          parcelle_id,
        "operation_type":       operation_type,
        "risk_score":           round(score_combine, 1),
        "risk_niveau":          niveau_combine.value,
        "validation_renforcee": validation_renforcee,
        "recommandations":      score_parcelle.recommandations[:3],
        "facteurs_parcelle":    score_parcelle.facteurs_cles[:3],
        "facteurs_operation":   score_operation.facteurs_cles[:2],
        "detail_parcelle":      score_parcelle.as_dict(),
        "detail_operation":     score_operation.as_dict(),
    }
