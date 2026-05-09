"""
FONCIER+ ── Moteur de Tableaux de Bord Décisionnels Nationaux
Vision globale, analytique et stratégique du foncier à l'échelle nationale.

Responsabilités :
  • Calcul des KPIs en temps réel par dimension géographique
  • Agrégation multi-dimensionnelle (national → région → département → commune)
  • Séries temporelles comparatives (mois, trimestre, année)
  • Synthèse stratégique automatique (texte + recommandations)
  • Export institutionnel (CSV, JSON structuré)
  • Cache en mémoire pour les requêtes fréquentes (TTL 5 min)

Dimensions d'analyse :
  NATIONAL  — vue d'ensemble République du Niger (8 régions)
  REGION    — NI-01 à NI-08 (Agadez, Diffa, Dosso, Maradi,
                              Tahoua, Tillabéri, Zinder, Niamey)
  COMMUNE   — filtrage par commune (nom libre)
  TYPE_PARC — résidentielle, commerciale, agricole, industrielle
  OPERATION — MUTATION, HYPOTHEQUE, FUSION, SCISSION, LOTISSEMENT…

KPIs calculés :
  K01  Nombre total de parcelles enregistrées
  K02  Parcelles actives / archivées / en litige
  K03  Mutations par région (30j / trimestre / an)
  K04  Taux de conflits fonciers actifs
  K05  Délai moyen de validation (days)
  K06  Taux d'approbation des décisions
  K07  Activité utilisateurs uniques (30j)
  K08  Performance sandbox (taux de validation)
  K09  Score de risque moyen par zone
  K10  Flux financiers (montant moyen des transactions)
  K11  Taux de complétion des workflows
  K12  Alertes prédictives actives par niveau

Synthèse stratégique automatique :
  Génère un texte de synthèse exécutive basé sur les KPIs calculés,
  identifiant les tendances, alertes et recommandations prioritaires.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("foncier.dashboard")

# ── Stub SQLAlchemy ────────────────────────────────────────────────
try:
    from sqlalchemy import text as _sa_text
    def _text(q): return _sa_text(q)
except ImportError:
    def _text(q): return q  # type: ignore


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TYPES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class KPI:
    """Un indicateur clé de performance."""
    code:        str
    label:       str
    valeur:      float
    unite:       str    = ""
    variation:   Optional[float] = None  # % vs période précédente
    tendance:    str    = "stable"       # hausse | baisse | stable
    alerte:      bool   = False
    detail:      Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "code":       self.code,
            "label":      self.label,
            "valeur":     round(self.valeur, 2) if self.valeur else 0,
            "unite":      self.unite,
            "variation":  round(self.variation, 1) if self.variation is not None else None,
            "tendance":   self.tendance,
            "alerte":     self.alerte,
            "detail":     self.detail,
        }


@dataclass
class SerieTemporelle:
    """Une série temporelle pour les graphiques."""
    label:      str
    unite:      str
    periodes:   List[str]   = field(default_factory=list)
    valeurs:    List[float] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "label":    self.label,
            "unite":    self.unite,
            "periodes": self.periodes,
            "valeurs":  [round(v, 1) if v else 0 for v in self.valeurs],
        }


@dataclass
class ZoneData:
    """Données d'une zone géographique pour la carte."""
    code:           str
    nom:            str
    niveau:         str    # NATIONAL | REGION | DEPARTEMENT | COMMUNE
    parcelles:      int    = 0
    mutations_30j:  int    = 0
    litiges_actifs: int    = 0
    taux_conflit:   float  = 0.0
    delai_validation_jours: float = 0.0
    score_risque:   float  = 0.0
    niveau_risque:  str    = "FAIBLE"
    mutations_an:   int    = 0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class SyntheseStrategique:
    """Synthèse exécutive automatique du tableau de bord."""
    titre:           str
    date_calcul:     str
    periode:         str
    indicateurs_cles: List[str]  = field(default_factory=list)
    alertes:          List[str]  = field(default_factory=list)
    tendances:        List[str]  = field(default_factory=list)
    recommandations:  List[str]  = field(default_factory=list)
    zones_prioritaires: List[str]= field(default_factory=list)
    score_sante:      float = 0.0  # 0-100

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class TableauBordNational:
    """Tableau de bord national complet."""
    kpis:         List[KPI]             = field(default_factory=list)
    zones:        List[ZoneData]        = field(default_factory=list)
    series:       List[SerieTemporelle] = field(default_factory=list)
    synthese:     Optional[SyntheseStrategique] = None
    filtres:      Dict[str, str]        = field(default_factory=dict)
    duree_ms:     int   = 0
    calcule_a:    str   = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return {
            "kpis":      [k.as_dict() for k in self.kpis],
            "zones":     [z.as_dict() for z in self.zones],
            "series":    [s.as_dict() for s in self.series],
            "synthese":  self.synthese.as_dict() if self.synthese else None,
            "filtres":   self.filtres,
            "duree_ms":  self.duree_ms,
            "calcule_a": self.calcule_a,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CACHE EN MÉMOIRE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DashboardCache:
    """Cache simple en mémoire avec TTL de 5 minutes."""
    TTL_SEC = 300

    def __init__(self):
        self._store: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        if key in self._store:
            ts, val = self._store[key]
            if time.monotonic() - ts < self.TTL_SEC:
                return val
            del self._store[key]
        return None

    def set(self, key: str, val: Any) -> None:
        self._store[key] = (time.monotonic(), val)

    def invalidate(self, prefix: str = "") -> None:
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            del self._store[k]


_cache = DashboardCache()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COLLECTEURS DE KPIs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _kpi_parcelles(db, region: Optional[str] = None) -> List[KPI]:
    """K01 Parcelles totales, K02 Répartition par statut."""
    kpis: List[KPI] = []
    where = "WHERE p.region = :reg" if region else "WHERE TRUE"
    params = {"reg": region} if region else {}
    try:
        r = await db.execute(_text(f"""
            SELECT
                COUNT(*)                                            AS total,
                COUNT(*) FILTER (WHERE p.statut = 'active')        AS actives,
                COUNT(*) FILTER (WHERE p.statut = 'archivee')      AS archivees,
                COUNT(*) FILTER (WHERE p.is_gele = TRUE)           AS gelees,
                COALESCE(SUM(p.surface_m2), 0)                     AS surface_totale,
                COUNT(*) FILTER (WHERE p.created_at >= NOW() - INTERVAL '30 days') AS nouvelles_30j,
                COUNT(*) FILTER (WHERE p.created_at >= NOW() - INTERVAL '60 days'
                               AND p.created_at < NOW() - INTERVAL '30 days')      AS nouvelles_prev
            FROM parcelles p
            {where}
        """), params)
        row = r.mappings().first() or {}
        total   = int(row.get("total") or 0)
        actives = int(row.get("actives") or 0)
        arch    = int(row.get("archivees") or 0)
        gelees  = int(row.get("gelees") or 0)
        surf    = float(row.get("surface_totale") or 0)
        n30     = int(row.get("nouvelles_30j") or 0)
        nprev   = int(row.get("nouvelles_prev") or 1)
        var_n   = (n30 - nprev) / max(nprev, 1) * 100

        kpis.append(KPI("K01", "Parcelles enregistrées", total, "",
                        detail={"actives": actives, "archivees": arch,
                                "gelees": gelees, "surface_ha": round(surf / 10000, 1)}))
        kpis.append(KPI("K02", "Nouvelles parcelles (30j)", n30, "parcelles",
                        variation=round(var_n, 1),
                        tendance="hausse" if var_n > 5 else "baisse" if var_n < -5 else "stable"))
        kpis.append(KPI("K03", "Taux de parcelles actives",
                        round(actives / max(total, 1) * 100, 1), "%",
                        alerte=(actives / max(total, 1) < 0.7)))
    except Exception as exc:
        _log.debug("kpi_parcelles: %s", exc)
    return kpis


async def _kpi_mutations(db, region: Optional[str] = None) -> List[KPI]:
    """K04 Mutations et flux de transferts."""
    kpis: List[KPI] = []
    where = "JOIN parcelles p ON p.id = pt.parcelle_id WHERE p.region = :reg" if region \
            else "WHERE TRUE"
    params = {"reg": region} if region else {}
    try:
        r = await db.execute(_text(f"""
            SELECT
                COUNT(*) FILTER (WHERE pt.created_at >= NOW() - INTERVAL '30 days')   AS muts_30j,
                COUNT(*) FILTER (WHERE pt.created_at >= NOW() - INTERVAL '60 days'
                               AND pt.created_at < NOW() - INTERVAL '30 days')         AS muts_prev,
                COUNT(*) FILTER (WHERE pt.created_at >= NOW() - INTERVAL '365 days')   AS muts_an,
                COUNT(*) FILTER (WHERE pt.created_at >= NOW() - INTERVAL '90 days')    AS muts_tri,
                AVG(pt.montant) FILTER (WHERE pt.montant > 0
                    AND pt.created_at >= NOW() - INTERVAL '30 days')                    AS montant_moy,
                COUNT(DISTINCT pt.parcelle_id) FILTER (
                    WHERE pt.created_at >= NOW() - INTERVAL '30 days')                  AS parcelles_mutees,
                SUM(pt.montant) FILTER (WHERE pt.created_at >= NOW() - INTERVAL '30 days'
                    AND pt.montant > 0)                                                  AS volume_financier
            FROM property_transfers pt
            {where}
              AND pt.statut = 'valide'
        """), params)
        row = r.mappings().first() or {}
        m30  = int(row.get("muts_30j") or 0)
        mprev= int(row.get("muts_prev") or 1)
        man  = int(row.get("muts_an") or 0)
        mtri = int(row.get("muts_tri") or 0)
        moy  = float(row.get("montant_moy") or 0)
        vol  = float(row.get("volume_financier") or 0)
        pm   = int(row.get("parcelles_mutees") or 0)
        var  = (m30 - mprev) / max(mprev, 1) * 100

        kpis.append(KPI("K04", "Mutations foncières (30j)", m30, "mutations",
                        variation=round(var, 1),
                        tendance="hausse" if var > 10 else "baisse" if var < -10 else "stable",
                        alerte=(m30 > 500),
                        detail={"muts_trimestre": mtri, "muts_annee": man,
                                "parcelles_mutees": pm}))
        kpis.append(KPI("K05", "Montant moyen des mutations", round(moy),
                        "XOF", detail={"volume_total_xof": round(vol)}))
    except Exception as exc:
        _log.debug("kpi_mutations: %s", exc)
    return kpis


async def _kpi_conflits(db, region: Optional[str] = None) -> List[KPI]:
    """K05 Conflits fonciers et litiges."""
    kpis: List[KPI] = []
    where_p = "AND p.region = :reg" if region else ""
    params  = {"reg": region} if region else {}
    try:
        r = await db.execute(_text(f"""
            SELECT
                COUNT(DISTINCT l.id) FILTER (WHERE l.statut = 'ACTIF')       AS litiges_actifs,
                COUNT(DISTINCT l.id)                                           AS litiges_total,
                COUNT(DISTINCT cp.id) FILTER (WHERE cp.statut = 'ouvert')     AS conflits_geo,
                COUNT(DISTINCT cp.id)                                          AS conflits_geo_total,
                COUNT(DISTINCT p.id)                                           AS total_parcelles,
                AVG(EXTRACT(DAY FROM AGE(NOW(), l.created_at)))
                    FILTER (WHERE l.statut = 'ACTIF')                         AS duree_moy_litige_jours,
                COUNT(DISTINCT l.id) FILTER (WHERE l.created_at >= NOW() - INTERVAL '30 days') AS litiges_nouveaux
            FROM parcelles p
            LEFT JOIN litiges l ON l.parcelle_id = p.id
            LEFT JOIN conflits_parcellaires cp
                ON (cp.parcelle_id_ref = p.id OR cp.parcelle_id_conflit = p.id)
            WHERE TRUE {where_p}
        """), params)
        row = r.mappings().first() or {}
        la   = int(row.get("litiges_actifs") or 0)
        lt   = int(row.get("litiges_total") or 0)
        cg   = int(row.get("conflits_geo") or 0)
        tp   = int(row.get("total_parcelles") or 1)
        dur  = float(row.get("duree_moy_litige_jours") or 0)
        lnew = int(row.get("litiges_nouveaux") or 0)
        taux = la / tp * 100 if tp > 0 else 0

        kpis.append(KPI("K06", "Taux de conflits fonciers", round(taux, 2), "%",
                        alerte=(taux > 5),
                        tendance="hausse" if lnew > 3 else "stable",
                        detail={"litiges_actifs": la, "litiges_total": lt,
                                "conflits_geographiques": cg, "nouveaux_30j": lnew}))
        kpis.append(KPI("K07", "Durée moy. litiges actifs", round(dur, 0), "jours",
                        alerte=(dur > 180),
                        detail={"litiges_resolus": lt - la}))
    except Exception as exc:
        _log.debug("kpi_conflits: %s", exc)
    return kpis


async def _kpi_validations(db, region: Optional[str] = None) -> List[KPI]:
    """K06 Délais et taux de validation des décisions."""
    kpis: List[KPI] = []
    try:
        r = await db.execute(_text("""
            SELECT
                COUNT(*) FILTER (WHERE vp.statut NOT IN ('VALIDE','REJETE'))     AS en_attente,
                COUNT(*) FILTER (WHERE vp.statut = 'VALIDE')                     AS valides,
                COUNT(*) FILTER (WHERE vp.statut = 'REJETE')                     AS rejetes,
                COUNT(*)                                                           AS total,
                AVG(EXTRACT(EPOCH FROM AGE(
                    COALESCE(vp.updated_at, NOW()),
                    vp.created_at
                )) / 86400.0) FILTER (WHERE vp.statut = 'VALIDE')                AS delai_moy_jours,
                COUNT(*) FILTER (WHERE vp.created_at >= NOW() - INTERVAL '30 days'
                               AND vp.statut NOT IN ('VALIDE','REJETE'))          AS en_retard_30j
            FROM validation_pipeline vp
            WHERE vp.created_at >= NOW() - INTERVAL '90 days'
        """), {})
        row = r.mappings().first() or {}
        att  = int(row.get("en_attente") or 0)
        val  = int(row.get("valides") or 0)
        rej  = int(row.get("rejetes") or 0)
        tot  = int(row.get("total") or 1)
        delai= float(row.get("delai_moy_jours") or 0)
        ret30= int(row.get("en_retard_30j") or 0)
        taux_app = val / max(tot, 1) * 100

        kpis.append(KPI("K08", "Délai moy. validation", round(delai, 1), "jours",
                        alerte=(delai > 30),
                        detail={"en_attente": att, "valides_90j": val,
                                "rejetes_90j": rej, "en_retard": ret30}))
        kpis.append(KPI("K09", "Taux d'approbation", round(taux_app, 1), "%",
                        alerte=(taux_app < 70),
                        tendance="baisse" if taux_app < 70 else "stable"))
    except Exception as exc:
        _log.debug("kpi_validations: %s", exc)
    return kpis


async def _kpi_systeme(db) -> List[KPI]:
    """K07 Indicateurs de performance système."""
    kpis: List[KPI] = []
    try:
        r = await db.execute(_text("""
            SELECT
                COUNT(DISTINCT del.effectue_par)
                    FILTER (WHERE del.created_at >= NOW() - INTERVAL '30 days')  AS users_actifs,
                COUNT(DISTINCT del.effectue_par)
                    FILTER (WHERE del.created_at >= NOW() - INTERVAL '7 days')   AS users_7j,
                COUNT(*) FILTER (WHERE del.decision = 'AUTORISE'
                    AND del.created_at >= NOW() - INTERVAL '30 days')            AS decisions_ok,
                COUNT(*) FILTER (WHERE del.created_at >= NOW() - INTERVAL '30 days') AS decisions_total,
                COUNT(DISTINCT wi.id) FILTER (WHERE wi.statut = 'en_cours')      AS wf_actifs,
                COUNT(DISTINCT wi.id) FILTER (WHERE wi.statut = 'bloque')        AS wf_bloques
            FROM decision_engine_log del
            LEFT JOIN workflow_instances wi ON TRUE
        """), {})
        row = r.mappings().first() or {}
        ua   = int(row.get("users_actifs") or 0)
        u7   = int(row.get("users_7j") or 0)
        dok  = int(row.get("decisions_ok") or 0)
        dtot = int(row.get("decisions_total") or 1)
        wfa  = int(row.get("wf_actifs") or 0)
        wfb  = int(row.get("wf_bloques") or 0)
        taux_dec = dok / max(dtot, 1) * 100

        kpis.append(KPI("K10", "Utilisateurs actifs (30j)", ua, "agents",
                        detail={"actifs_7j": u7}))
        kpis.append(KPI("K11", "Taux décisions automatisées", round(taux_dec, 1), "%",
                        detail={"workflows_actifs": wfa, "workflows_bloques": wfb}))

        # Sandbox performance
        r2 = await db.execute(_text("""
            SELECT
                COUNT(*) FILTER (WHERE statut = 'VALIDE')  AS valides,
                COUNT(*)                                     AS total
            FROM sql_validation_log
            WHERE created_at >= NOW() - INTERVAL '30 days'
        """), {})
        row2 = r2.mappings().first() or {}
        sv  = int(row2.get("valides") or 0)
        st  = int(row2.get("total") or 1)
        taux_sb = sv / max(st, 1) * 100

        kpis.append(KPI("K12", "Taux validation règles SQL", round(taux_sb, 1), "%",
                        alerte=(taux_sb < 60),
                        detail={"valides_30j": sv, "soumises_30j": st}))

        # Alertes prédictives
        r3 = await db.execute(_text("""
            SELECT
                COUNT(*) FILTER (WHERE niveau = 'CRITIQUE') AS crit,
                COUNT(*) FILTER (WHERE niveau = 'ÉLEVÉ')    AS elev,
                COUNT(*) FILTER (WHERE niveau = 'MOYEN')    AS moyen
            FROM risk_alert
            WHERE acquittee = FALSE
        """), {})
        row3 = r3.mappings().first() or {}
        crit = int(row3.get("crit") or 0)
        elev = int(row3.get("elev") or 0)

        kpis.append(KPI("K13", "Alertes prédictives actives",
                        crit + elev, "alertes",
                        alerte=(crit > 0),
                        tendance="hausse" if crit > 0 else "stable",
                        detail={"critique": crit, "eleve": elev, "moyen": int(row3.get("moyen") or 0)}))
    except Exception as exc:
        _log.debug("kpi_systeme: %s", exc)
    return kpis


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SÉRIES TEMPORELLES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _serie_mutations_mensuelle(
    db, region: Optional[str] = None, nb_mois: int = 12
) -> SerieTemporelle:
    """Mutations par mois sur les N derniers mois."""
    periodes: List[str] = []
    valeurs: List[float] = []
    where = "JOIN parcelles p ON p.id = pt.parcelle_id AND p.region = :reg" if region else ""
    params = {"reg": region, "nb": nb_mois} if region else {"nb": nb_mois}
    try:
        r = await db.execute(_text(f"""
            SELECT
                TO_CHAR(DATE_TRUNC('month', pt.created_at), 'YYYY-MM') AS mois,
                COUNT(*) AS nb_mutations
            FROM property_transfers pt
            {where}
            WHERE pt.statut = 'valide'
              AND pt.created_at >= NOW() - (:nb || ' months')::INTERVAL
            GROUP BY 1
            ORDER BY 1
        """), params)
        rows = r.mappings().all()
        for row in rows:
            periodes.append(str(row.get("mois", "")))
            valeurs.append(float(row.get("nb_mutations") or 0))
    except Exception as exc:
        _log.debug("serie_mutations: %s", exc)
    return SerieTemporelle("Mutations mensuelles", "mutations", periodes, valeurs)


async def _serie_conflits_mensuelle(db, nb_mois: int = 12) -> SerieTemporelle:
    """Nouveaux litiges par mois."""
    periodes: List[str] = []
    valeurs: List[float] = []
    try:
        r = await db.execute(_text("""
            SELECT
                TO_CHAR(DATE_TRUNC('month', l.created_at), 'YYYY-MM') AS mois,
                COUNT(*) AS nb
            FROM litiges l
            WHERE l.created_at >= NOW() - (:nb || ' months')::INTERVAL
            GROUP BY 1 ORDER BY 1
        """), {"nb": nb_mois})
        rows = r.mappings().all()
        for row in rows:
            periodes.append(str(row.get("mois", "")))
            valeurs.append(float(row.get("nb") or 0))
    except Exception as exc:
        _log.debug("serie_conflits: %s", exc)
    return SerieTemporelle("Nouveaux litiges mensuels", "litiges", periodes, valeurs)


async def _serie_validations_mensuelle(db, nb_mois: int = 12) -> SerieTemporelle:
    """Validations par mois."""
    periodes: List[str] = []
    valeurs: List[float] = []
    try:
        r = await db.execute(_text("""
            SELECT
                TO_CHAR(DATE_TRUNC('month', vp.created_at), 'YYYY-MM') AS mois,
                COUNT(*) FILTER (WHERE vp.statut = 'VALIDE') AS nb_valides
            FROM validation_pipeline vp
            WHERE vp.created_at >= NOW() - (:nb || ' months')::INTERVAL
            GROUP BY 1 ORDER BY 1
        """), {"nb": nb_mois})
        rows = r.mappings().all()
        for row in rows:
            periodes.append(str(row.get("mois", "")))
            valeurs.append(float(row.get("nb_valides") or 0))
    except Exception as exc:
        _log.debug("serie_validations: %s", exc)
    return SerieTemporelle("Validations mensuelles", "validations", periodes, valeurs)


async def _serie_risques_zones(db) -> SerieTemporelle:
    """Score de risque par région Niger."""
    regions = ["NI-01", "NI-02", "NI-03", "NI-04", "NI-05", "NI-06", "NI-07", "NI-08"]
    noms    = ["Agadez","Diffa","Dosso","Maradi","Tahoua","Tillabéri","Zinder","Niamey"]
    scores: List[float] = []
    try:
        r = await db.execute(_text("""
            SELECT entite_id, score
            FROM risk_score
            WHERE entite_type = 'ZONE'
        """), {})
        score_map = {row["entite_id"]: float(row["score"]) for row in r.mappings().all()}
        scores = [score_map.get(reg, 0.0) for reg in regions]
    except Exception:
        scores = [0.0] * 8
    return SerieTemporelle("Score de risque par région", "score/100", noms, scores)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DONNÉES GÉOGRAPHIQUES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

REGIONS_NIGER = {
    "NI-01": "Agadez", "NI-02": "Diffa",      "NI-03": "Dosso",
    "NI-04": "Maradi", "NI-05": "Tahoua",     "NI-06": "Tillabéri",
    "NI-07": "Zinder", "NI-08": "Niamey (CU)",
}


async def _donnees_zones(db) -> List[ZoneData]:
    """Calcule les données KPI pour chaque région."""
    zones: List[ZoneData] = []
    try:
        r = await db.execute(_text("""
            SELECT
                p.region,
                COUNT(DISTINCT p.id)                                          AS parcelles,
                COUNT(DISTINCT pt.id) FILTER (
                    WHERE pt.created_at >= NOW() - INTERVAL '30 days'
                    AND pt.statut = 'valide')                                 AS mutations_30j,
                COUNT(DISTINCT pt.id) FILTER (
                    WHERE pt.statut = 'valide'
                    AND pt.created_at >= NOW() - INTERVAL '365 days')         AS mutations_an,
                COUNT(DISTINCT l.id) FILTER (WHERE l.statut = 'ACTIF')        AS litiges_actifs,
                COALESCE(AVG(rs.score), 0)                                    AS score_risque,
                COALESCE(MAX(rs.niveau), 'FAIBLE')                            AS niveau_risque
            FROM parcelles p
            LEFT JOIN property_transfers pt ON pt.parcelle_id = p.id
            LEFT JOIN litiges l             ON l.parcelle_id  = p.id
            LEFT JOIN risk_score rs         ON rs.entite_id   = p.region
                                            AND rs.entite_type = 'ZONE'
            WHERE p.region IS NOT NULL
            GROUP BY p.region
            ORDER BY p.region
        """), {})
        rows = r.mappings().all()
        for row in rows:
            reg  = str(row.get("region") or "")
            parc = int(row.get("parcelles") or 0)
            la   = int(row.get("litiges_actifs") or 0)
            taux = la / max(parc, 1) * 100

            # Délai moyen validation pour cette région
            try:
                r2 = await db.execute(_text("""
                    SELECT AVG(EXTRACT(EPOCH FROM AGE(
                        COALESCE(vp.updated_at, NOW()), vp.created_at
                    )) / 86400.0) FILTER (WHERE vp.statut = 'VALIDE') AS delai
                    FROM validation_pipeline vp
                    WHERE vp.region = :reg
                      AND vp.created_at >= NOW() - INTERVAL '90 days'
                """), {"reg": reg})
                dr = r2.mappings().first()
                delai = float(dr.get("delai") or 0) if dr else 0.0
            except Exception:
                delai = 0.0

            zones.append(ZoneData(
                code=reg,
                nom=REGIONS_NIGER.get(reg, reg),
                niveau="REGION",
                parcelles=parc,
                mutations_30j=int(row.get("mutations_30j") or 0),
                mutations_an=int(row.get("mutations_an") or 0),
                litiges_actifs=la,
                taux_conflit=round(taux, 2),
                delai_validation_jours=round(delai, 1),
                score_risque=round(float(row.get("score_risque") or 0), 1),
                niveau_risque=str(row.get("niveau_risque") or "FAIBLE"),
            ))
    except Exception as exc:
        _log.debug("donnees_zones: %s", exc)
        # Fallback : retourner les zones avec score 0
        zones = [
            ZoneData(code=code, nom=nom, niveau="REGION")
            for code, nom in REGIONS_NIGER.items()
        ]
    return zones


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SYNTHÈSE STRATÉGIQUE AUTOMATIQUE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _generer_synthese(
    kpis: List[KPI], zones: List[ZoneData], periode: str
) -> SyntheseStrategique:
    """
    Génère une synthèse stratégique exécutive à partir des KPIs calculés.
    Produit un texte structuré pour les décideurs institutionnels.
    """
    kpi_map = {k.code: k for k in kpis}
    indicateurs_cles: List[str] = []
    alertes:           List[str] = []
    tendances:         List[str] = []
    recommandations:   List[str] = []
    zones_prio:        List[str] = []

    # ── Analyse parcelles ─────────────────────────────────────
    k01 = kpi_map.get("K01")
    if k01:
        indicateurs_cles.append(
            f"Le Registre Foncier National recense {int(k01.valeur):,} parcelles, "
            f"dont {k01.detail.get('actives', 0):,} actives "
            f"({k01.detail.get('actives', 0) / max(int(k01.valeur), 1) * 100:.0f}%)."
        )

    k02 = kpi_map.get("K02")
    if k02 and k02.variation is not None:
        if k02.variation > 10:
            tendances.append(
                f"Enregistrement en forte hausse : +{k02.variation:.0f}% "
                f"de nouvelles parcelles vs mois précédent ({int(k02.valeur)} nouvelles)."
            )
        elif k02.variation < -10:
            tendances.append(
                f"Ralentissement des enregistrements : {k02.variation:.0f}% "
                f"vs mois précédent."
            )

    # ── Analyse mutations ─────────────────────────────────────
    k04 = kpi_map.get("K04")
    if k04:
        indicateurs_cles.append(
            f"Flux mutations foncières : {int(k04.valeur)} transactions validées ce mois "
            f"({k04.detail.get('muts_annee', 0)} sur l'année)."
        )
        if k04.tendance == "hausse":
            tendances.append(
                f"Accélération des mutations foncières (+{k04.variation:.0f}%) — "
                "surveiller les zones à forte densité spéculative."
            )

    k05 = kpi_map.get("K05")
    if k05 and k05.valeur > 0:
        indicateurs_cles.append(
            f"Valeur moyenne des transactions : {int(k05.valeur):,} XOF "
            f"(volume total : {k05.detail.get('volume_total_xof', 0):,.0f} XOF)."
        )

    # ── Analyse conflits ──────────────────────────────────────
    k06 = kpi_map.get("K06")
    if k06:
        if k06.valeur > 5:
            alertes.append(
                f"ALERTE : Taux de conflits fonciers élevé ({k06.valeur:.1f}%) — "
                f"{k06.detail.get('litiges_actifs', 0)} litiges actifs. "
                "Renforcement des mesures préventives recommandé."
            )
        else:
            indicateurs_cles.append(
                f"Stabilité judiciaire : taux de conflits à {k06.valeur:.1f}% "
                f"({k06.detail.get('litiges_actifs', 0)} litiges actifs)."
            )

    k07 = kpi_map.get("K07")
    if k07 and k07.valeur > 365:
        alertes.append(
            f"Durée moyenne des litiges anormalement élevée "
            f"({int(k07.valeur)} jours) — réviser le processus de résolution."
        )

    # ── Analyse performance ────────────────────────────────────
    k08 = kpi_map.get("K08")
    if k08:
        if k08.valeur > 30:
            alertes.append(
                f"Délai de validation moyen de {int(k08.valeur)} jours "
                f"({k08.detail.get('en_attente', 0)} dossiers en attente) — "
                "objectif : ≤ 15 jours."
            )
        else:
            indicateurs_cles.append(
                f"Performance validation : délai moyen {int(k08.valeur)} jours. "
                f"{k08.detail.get('en_attente', 0)} dossiers en attente."
            )

    k09 = kpi_map.get("K09")
    if k09 and k09.valeur < 70:
        alertes.append(
            f"Taux d'approbation des dossiers à {k09.valeur:.0f}% (< 70%). "
            "Analyser les motifs de refus."
        )

    # ── Analyse risques ───────────────────────────────────────
    k13 = kpi_map.get("K13")
    if k13:
        crit = k13.detail.get("critique", 0)
        if crit > 0:
            alertes.append(
                f"{crit} alerte(s) prédictive(s) de niveau CRITIQUE active(s). "
                "Intervention immédiate requise."
            )

    # ── Zones prioritaires ────────────────────────────────────
    zones_risque = sorted(zones, key=lambda z: -z.score_risque)
    for z in zones_risque[:3]:
        if z.score_risque > 30:
            zones_prio.append(
                f"{z.nom} ({z.code}) — score risque {z.score_risque:.0f}/100, "
                f"{z.litiges_actifs} litige(s) actif(s), {z.mutations_30j} mutation(s)/30j"
            )

    # ── Recommandations ───────────────────────────────────────
    if any(z.score_risque > 60 for z in zones):
        recommandations.append(
            "Mobiliser les équipes terrain dans les zones à score de risque > 60."
        )
    if kpi_map.get("K08") and (kpi_map["K08"].valeur or 0) > 20:
        recommandations.append(
            "Réduire les délais de validation : dématérialiser les étapes manuelles."
        )
    if kpi_map.get("K06") and (kpi_map["K06"].valeur or 0) > 3:
        recommandations.append(
            "Mettre en place des médiations préventives dans les zones à conflits élevés."
        )
    if kpi_map.get("K11") and (kpi_map["K11"].valeur or 0) < 80:
        recommandations.append(
            "Renforcer la formation des agents sur les procédures de validation."
        )
    if not recommandations:
        recommandations.append(
            "Système foncier fonctionnel — maintenir le rythme de traitement actuel."
        )

    # ── Score de santé global ─────────────────────────────────
    nb_alertes_kpi = sum(1 for k in kpis if k.alerte)
    score_sante    = max(0.0, 100.0 - nb_alertes_kpi * 15 - len(alertes) * 10)

    return SyntheseStrategique(
        titre        = "Synthèse Stratégique du Système Foncier National",
        date_calcul  = datetime.now(timezone.utc).isoformat(),
        periode      = periode,
        indicateurs_cles  = indicateurs_cles[:6],
        alertes           = alertes[:5],
        tendances         = tendances[:4],
        recommandations   = recommandations[:5],
        zones_prioritaires= zones_prio[:3],
        score_sante       = round(score_sante, 0),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EXPORT INSTITUTIONNEL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def exporter_csv(
    kpis: List[KPI], zones: List[ZoneData], synthese: Optional[SyntheseStrategique]
) -> str:
    """Génère un export CSV pour rapports institutionnels."""
    buf = io.StringIO()
    w   = csv.writer(buf)

    # En-tête
    w.writerow(["FONCIER+ — Tableau de bord national",
                datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")])
    w.writerow([])

    # KPIs
    w.writerow(["=== INDICATEURS CLÉS ==="])
    w.writerow(["Code", "Indicateur", "Valeur", "Unité", "Variation %", "Tendance", "Alerte"])
    for k in kpis:
        w.writerow([
            k.code, k.label,
            f"{k.valeur:.2f}".replace(".", ","),
            k.unite,
            f"{k.variation:.1f}".replace(".", ",") if k.variation is not None else "",
            k.tendance,
            "OUI" if k.alerte else "NON",
        ])
    w.writerow([])

    # Zones
    w.writerow(["=== DONNÉES PAR RÉGION ==="])
    w.writerow(["Code", "Nom", "Parcelles", "Mutations 30j",
                "Mutations an", "Litiges actifs", "Taux conflit %",
                "Délai valid. (j)", "Score risque"])
    for z in zones:
        w.writerow([
            z.code, z.nom, z.parcelles, z.mutations_30j, z.mutations_an,
            z.litiges_actifs,
            f"{z.taux_conflit:.2f}".replace(".", ","),
            f"{z.delai_validation_jours:.1f}".replace(".", ","),
            f"{z.score_risque:.1f}".replace(".", ","),
        ])
    w.writerow([])

    # Synthèse
    if synthese:
        w.writerow(["=== SYNTHÈSE STRATÉGIQUE ==="])
        w.writerow(["Score de santé global", f"{synthese.score_sante:.0f}/100"])
        w.writerow([])
        for label, items in [
            ("Indicateurs clés", synthese.indicateurs_cles),
            ("Alertes",          synthese.alertes),
            ("Tendances",        synthese.tendances),
            ("Recommandations",  synthese.recommandations),
            ("Zones prioritaires", synthese.zones_prioritaires),
        ]:
            if items:
                w.writerow([label])
                for item in items:
                    w.writerow(["", item])
                w.writerow([])

    return buf.getvalue()


def exporter_json_rapport(
    tb: TableauBordNational,
    titre: str = "Rapport foncier national",
) -> str:
    """Export JSON structuré pour intégrations institutionnelles."""
    rapport = {
        "titre":     titre,
        "version":   "FONCIER+ v107",
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "contenu":   tb.as_dict(),
    }
    return json.dumps(rapport, ensure_ascii=False, indent=2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MOTEUR PRINCIPAL — DashboardEngine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DashboardEngine:
    """
    Point d'entrée principal du moteur de tableau de bord national.

    Usage :
        tb = await DashboardEngine.tableau_bord_national(db)
        tb = await DashboardEngine.tableau_bord_regional(db, region="NI-01")
        kpis = await DashboardEngine.kpis_seulement(db, region="NI-03")
        csv_str = await DashboardEngine.exporter(db, format="csv")
    """

    @classmethod
    async def tableau_bord_national(
        cls, db,
        region:    Optional[str] = None,
        nb_mois:   int            = 12,
        avec_cache: bool          = True,
    ) -> TableauBordNational:
        """Tableau de bord complet : KPIs + zones + séries + synthèse."""
        cache_key = f"tbn:{region}:{nb_mois}"
        if avec_cache:
            cached = _cache.get(cache_key)
            if cached:
                return cached

        t0   = time.monotonic()
        kpis: List[KPI]             = []
        zones: List[ZoneData]       = []
        series: List[SerieTemporelle]= []

        # Collecte parallèle des KPIs
        import asyncio
        results = await asyncio.gather(
            _kpi_parcelles(db, region),
            _kpi_mutations(db, region),
            _kpi_conflits(db, region),
            _kpi_validations(db),
            _kpi_systeme(db),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, list):
                kpis.extend(r)

        # Données géographiques (uniquement en vue nationale)
        if not region:
            zones = await _donnees_zones(db)

        # Séries temporelles
        s_results = await asyncio.gather(
            _serie_mutations_mensuelle(db, region, nb_mois),
            _serie_conflits_mensuelle(db, nb_mois),
            _serie_validations_mensuelle(db, nb_mois),
            _serie_risques_zones(db),
            return_exceptions=True,
        )
        for s in s_results:
            if isinstance(s, SerieTemporelle):
                series.append(s)

        # Synthèse stratégique
        periode = f"Vue {'nationale' if not region else region} — {nb_mois} derniers mois"
        synthese = _generer_synthese(kpis, zones, periode)

        tb = TableauBordNational(
            kpis=kpis, zones=zones, series=series, synthese=synthese,
            filtres={"region": region or "NATIONAL", "nb_mois": str(nb_mois)},
            duree_ms=int((time.monotonic() - t0) * 1000),
        )

        if avec_cache:
            _cache.set(cache_key, tb)
        return tb

    @classmethod
    async def kpis_seulement(
        cls, db, region: Optional[str] = None
    ) -> List[KPI]:
        """Retourne uniquement les KPIs (plus rapide, pour polling fréquent)."""
        cache_key = f"kpis:{region}"
        cached = _cache.get(cache_key)
        if cached:
            return cached

        import asyncio
        results = await asyncio.gather(
            _kpi_parcelles(db, region),
            _kpi_mutations(db, region),
            _kpi_conflits(db, region),
            _kpi_validations(db),
            _kpi_systeme(db),
            return_exceptions=True,
        )
        kpis: List[KPI] = []
        for r in results:
            if isinstance(r, list):
                kpis.extend(r)

        _cache.set(cache_key, kpis)
        return kpis

    @classmethod
    async def zones_stats(cls, db) -> List[ZoneData]:
        """Données par zone pour la carte interactive."""
        cache_key = "zones"
        cached = _cache.get(cache_key)
        if cached:
            return cached
        zones = await _donnees_zones(db)
        _cache.set(cache_key, zones)
        return zones

    @classmethod
    async def series_temporelles(
        cls, db, region: Optional[str] = None, nb_mois: int = 12
    ) -> List[SerieTemporelle]:
        """Séries temporelles pour graphiques."""
        import asyncio
        results = await asyncio.gather(
            _serie_mutations_mensuelle(db, region, nb_mois),
            _serie_conflits_mensuelle(db, nb_mois),
            _serie_validations_mensuelle(db, nb_mois),
            _serie_risques_zones(db),
            return_exceptions=True,
        )
        return [s for s in results if isinstance(s, SerieTemporelle)]

    @classmethod
    async def exporter(
        cls, db, format: str = "csv",
        region: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Exporte le tableau de bord.
        Retourne (contenu, content_type).
        """
        tb = await cls.tableau_bord_national(db, region=region, avec_cache=False)

        if format.lower() == "csv":
            contenu = exporter_csv(tb.kpis, tb.zones, tb.synthese)
            return contenu, "text/csv; charset=utf-8"
        else:
            contenu = exporter_json_rapport(tb)
            return contenu, "application/json"

    @classmethod
    def invalider_cache(cls) -> None:
        _cache.invalidate()
