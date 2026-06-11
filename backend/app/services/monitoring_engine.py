"""
FONCIER+ ── Moteur de Monitoring Temps Réel Intelligent
Surveillance continue de toutes les opérations du système foncier.

Architecture :
  ┌─────────────────────────────────────────────────────────────┐
  │  CollecteurEvenements            (collecte depuis la DB)    │
  │    ├─ poll_parcelles()           audit_immutable             │
  │    ├─ poll_workflows()           workflow_instance            │
  │    ├─ poll_validations()         decision_engine_log          │
  │    ├─ poll_regles()              sql_validation_log           │
  │    ├─ poll_verrous()             entity_lock                  │
  │    └─ poll_transactions()        property_transfers           │
  │                                                              │
  │  DetecteurAnomalies              (6 heuristiques)            │
  │    ├─ D1 WorkflowBlockageDetector                            │
  │    ├─ D2 SurchargeDetector                                   │
  │    ├─ D3 ConflitValidationDetector                           │
  │    ├─ D4 AccesInhabituelDetector                             │
  │    ├─ D5 RatioEchecDetector                                  │
  │    └─ D6 CadeauVerrouDetector                                │
  │                                                              │
  │  AlerteManager                   (gestion des alertes)      │
  │    ├─ creer(niveau, message)     → alerte_monitoring         │
  │    ├─ acquitter(id)              → acquittement              │
  │    └─ broadcast() → SSE         → /monitoring/stream        │
  │                                                              │
  │  MonitoringEngine                (orchestrateur principal)   │
  │    ├─ demarrer()                 → boucle asynchrone         │
  │    ├─ arreter()                                              │
  │    ├─ get_tableau_bord()        → métriques temps réel       │
  │    └─ subscribe(queue)          → SSE client                 │
  └─────────────────────────────────────────────────────────────┘

Niveaux d'alertes :
  INFO     — événement normal mais notable (nouvelle parcelle, workflow démarré)
  WARNING  — anomalie détectée, action recommandée
  CRITICAL — blocage ou danger système, action immédiate requise

Transport temps réel :
  • PostgreSQL LISTEN/NOTIFY (canal : foncier_monitoring)
  • Server-Sent Events (SSE) pour le frontend
  • File asyncio interne (broadcast vers N clients connectés)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Deque

_log = logging.getLogger("foncier.monitoring")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONSTANTES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

POLL_INTERVAL_SEC    = 15      # intervalle de polling DB
MAX_CLIENTS_SSE      = 50      # max clients SSE simultanés
MAX_EVENTS_BUFFER    = 500     # buffer circulaire d'événements récents
MAX_ALERTS_BUFFER    = 200     # alertes en mémoire
WINDOW_SHORT_SEC     = 300     # fenêtre courte  : 5 min
WINDOW_LONG_SEC      = 3600    # fenêtre longue  : 1 h

# Seuils de détection d'anomalies
SEUIL_WORKFLOWS_BLOQUES     = 5    # nb workflows bloqués > X → CRITICAL
SEUIL_VALIDATIONS_REFUSEES  = 10   # nb refus / WINDOW_SHORT → WARNING
SEUIL_SURCHARGE_OPS         = 200  # nb ops / WINDOW_SHORT → WARNING
SEUIL_RATIO_ECHEC_PCT       = 40   # % échecs sandbox / fenêtre → WARNING
SEUIL_VERROUS_ACTIFS        = 20   # nb verrous actifs → WARNING
SEUIL_ACCES_MEME_USER       = 50   # nb ops / user / fenêtre → WARNING


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TYPES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class NiveauAlerte(str, Enum):
    INFO     = "INFO"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"


class CategorieEvenement(str, Enum):
    PARCELLE     = "PARCELLE"
    WORKFLOW     = "WORKFLOW"
    VALIDATION   = "VALIDATION"
    REGLE        = "REGLE"
    VERROU       = "VERROU"
    TRANSACTION  = "TRANSACTION"
    SYSTEME      = "SYSTEME"


@dataclass
class Evenement:
    """Un événement système collecté depuis la DB."""
    id:           str
    categorie:    CategorieEvenement
    operation:    str
    entite_type:  str
    entite_id:    str
    details:      Dict[str, Any]
    user_id:      Optional[str]
    region:       Optional[str]
    ts:           float = field(default_factory=time.monotonic)
    created_at:   str   = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return {
            "id":          self.id,
            "categorie":   self.categorie.value,
            "operation":   self.operation,
            "entite_type": self.entite_type,
            "entite_id":   self.entite_id,
            "details":     self.details,
            "user_id":     self.user_id,
            "region":      self.region,
            "created_at":  self.created_at,
        }


@dataclass
class Alerte:
    """Une alerte générée par un détecteur d'anomalies."""
    id:           str
    niveau:       NiveauAlerte
    detecteur:    str
    titre:        str
    message:      str
    details:      Dict[str, Any]
    acquittee:    bool  = False
    acquittee_par: Optional[str] = None
    created_at:   str   = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sha256:       str   = ""

    def as_dict(self) -> dict:
        return {
            "id":           self.id,
            "niveau":       self.niveau.value,
            "detecteur":    self.detecteur,
            "titre":        self.titre,
            "message":      self.message,
            "details":      self.details,
            "acquittee":    self.acquittee,
            "acquittee_par": self.acquittee_par,
            "created_at":   self.created_at,
            "sha256":       self.sha256,
        }


@dataclass
class TableauBord:
    """Métriques temps réel du tableau de bord."""
    # Compteurs généraux
    parcelles_actives:        int = 0
    workflows_en_cours:       int = 0
    workflows_bloques:        int = 0
    validations_en_attente:   int = 0
    verrous_actifs:           int = 0
    regles_actives:           int = 0
    # Activité récente (5 min)
    ops_5min:                 int = 0
    validations_5min:         int = 0
    echecs_5min:              int = 0
    simulations_5min:         int = 0
    # Alertes actives
    alertes_critical:         int = 0
    alertes_warning:          int = 0
    alertes_info:             int = 0
    # Performance
    latence_moy_ms:           float = 0.0
    # Santé globale : OK | DEGRADÉ | CRITIQUE
    sante:                    str = "OK"
    calcule_a:                str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> dict:
        return asdict(self)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COLLECTEUR D'ÉVÉNEMENTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CollecteurEvenements:
    """
    Collecte les événements depuis les tables de log existantes.
    Utilise un curseur temporel pour ne lire que les nouveaux événements.
    """

    def __init__(self) -> None:
        # Curseurs : timestamp de la dernière lecture par source
        self._curseurs: Dict[str, datetime] = {}

    def _curseur(self, source: str) -> datetime:
        if source not in self._curseurs:
            self._curseurs[source] = datetime.now(timezone.utc) - timedelta(seconds=POLL_INTERVAL_SEC * 2)
        return self._curseurs[source]

    def _maj_curseur(self, source: str) -> None:
        self._curseurs[source] = datetime.now(timezone.utc)

    async def collecter_tout(self, db) -> List[Evenement]:
        """Collecte tous les types d'événements depuis la dernière lecture."""
        all_events: List[Evenement] = []
        sources = [
            self._poll_parcelles,
            self._poll_workflows,
            self._poll_validations,
            self._poll_regles,
            self._poll_verrous,
            self._poll_transactions,
            self._poll_simulations,
        ]
        for poll_fn in sources:
            try:
                evts = await poll_fn(db)
                all_events.extend(evts)
            except Exception as exc:
                _log.debug("Collecteur %s: %s", poll_fn.__name__, exc)

        return sorted(all_events, key=lambda e: e.ts)

    async def _poll_parcelles(self, db) -> List[Evenement]:
        events: List[Evenement] = []
        curseur = self._curseur("parcelles")
        try:
            r = await db.execute(_text("""
                SELECT ai.id, ai.entite_type, ai.entite_id::TEXT,
                       ai.operation, ai.effectue_par::TEXT,
                       ai.region, ai.created_at,
                       ai.etat_apres
                FROM audit_immutable ai
                WHERE ai.entite_type IN ('parcelle','parcelles')
                  AND ai.created_at > :ts
                ORDER BY ai.created_at DESC
                LIMIT 50
            """), {"ts": curseur})
            rows = r.mappings().all()
        except Exception:
            return events

        for row in rows:
            events.append(Evenement(
                id          = str(row["id"]),
                categorie   = CategorieEvenement.PARCELLE,
                operation   = row["operation"],
                entite_type = row["entite_type"],
                entite_id   = row["entite_id"] or "",
                details     = {"etat_apres": row.get("etat_apres") or {}},
                user_id     = row.get("effectue_par"),
                region      = row.get("region"),
                created_at  = str(row["created_at"]),
            ))
        if rows:
            self._maj_curseur("parcelles")
        return events

    async def _poll_workflows(self, db) -> List[Evenement]:
        events: List[Evenement] = []
        curseur = self._curseur("workflows")
        try:
            r = await db.execute(_text("""
                SELECT wsl.id::TEXT, wsl.instance_id::TEXT,
                       wsl.etape_code, wsl.statut,
                       wsl.commentaire, wsl.created_at,
                       wi.type_workflow, wi.entite_type,
                       wi.entite_id::TEXT, wi.statut AS statut_instance,
                       wsl.valide_par::TEXT
                FROM workflow_step_log wsl
                JOIN workflow_instances wi ON wi.id = wsl.instance_id
                WHERE wsl.created_at > :ts
                ORDER BY wsl.created_at DESC
                LIMIT 50
            """), {"ts": curseur})
            rows = r.mappings().all()
        except Exception:
            return events

        for row in rows:
            events.append(Evenement(
                id          = str(row["id"]),
                categorie   = CategorieEvenement.WORKFLOW,
                operation   = f"WORKFLOW_{row['statut']}",
                entite_type = row.get("entite_type", "workflow"),
                entite_id   = row.get("entite_id", ""),
                details     = {
                    "type_workflow":   row.get("type_workflow"),
                    "etape_code":      row.get("etape_code"),
                    "statut":          row.get("statut"),
                    "statut_instance": row.get("statut_instance"),
                },
                user_id    = row.get("valide_par"),
                region     = None,
                created_at = str(row["created_at"]),
            ))
        if rows:
            self._maj_curseur("workflows")
        return events

    async def _poll_validations(self, db) -> List[Evenement]:
        events: List[Evenement] = []
        curseur = self._curseur("validations")
        try:
            r = await db.execute(_text("""
                SELECT del.id::TEXT, del.entite_type, del.entite_id::TEXT,
                       del.operation, del.decision, del.motif,
                       del.effectue_par::TEXT, del.created_at
                FROM decision_engine_log del
                WHERE del.created_at > :ts
                ORDER BY del.created_at DESC
                LIMIT 100
            """), {"ts": curseur})
            rows = r.mappings().all()
        except Exception:
            return events

        for row in rows:
            events.append(Evenement(
                id          = str(row["id"]),
                categorie   = CategorieEvenement.VALIDATION,
                operation   = f"DECISION_{row['decision']}",
                entite_type = row.get("entite_type", ""),
                entite_id   = row.get("entite_id", ""),
                details     = {
                    "decision": row.get("decision"),
                    "motif":    row.get("motif", "")[:200] if row.get("motif") else "",
                },
                user_id    = row.get("effectue_par"),
                region     = None,
                created_at = str(row["created_at"]),
            ))
        if rows:
            self._maj_curseur("validations")
        return events

    async def _poll_regles(self, db) -> List[Evenement]:
        events: List[Evenement] = []
        curseur = self._curseur("regles")
        try:
            r = await db.execute(_text("""
                SELECT svl.id::TEXT, svl.code_regle, svl.domaine,
                       svl.statut, svl.nb_erreurs, svl.duree_ms,
                       svl.soumis_par::TEXT, svl.created_at
                FROM sql_validation_log svl
                WHERE svl.created_at > :ts
                ORDER BY svl.created_at DESC
                LIMIT 50
            """), {"ts": curseur})
            rows = r.mappings().all()
        except Exception:
            return events

        for row in rows:
            events.append(Evenement(
                id          = str(row["id"]),
                categorie   = CategorieEvenement.REGLE,
                operation   = f"REGLE_{row['statut']}",
                entite_type = "regle_metier",
                entite_id   = row.get("code_regle", ""),
                details     = {
                    "statut":    row.get("statut"),
                    "domaine":   row.get("domaine"),
                    "nb_erreurs":row.get("nb_erreurs", 0),
                    "duree_ms":  row.get("duree_ms", 0),
                },
                user_id    = row.get("soumis_par"),
                region     = None,
                created_at = str(row["created_at"]),
            ))
        if rows:
            self._maj_curseur("regles")
        return events

    async def _poll_verrous(self, db) -> List[Evenement]:
        events: List[Evenement] = []
        curseur = self._curseur("verrous")
        try:
            r = await db.execute(_text("""
                SELECT el.id::TEXT, el.entite_type, el.entite_id::TEXT,
                       el.workflow_type, el.raison,
                       el.verrouille_par::TEXT, el.date_expiration,
                       el.actif, el.created_at
                FROM entity_lock el
                WHERE el.created_at > :ts
                ORDER BY el.created_at DESC
                LIMIT 50
            """), {"ts": curseur})
            rows = r.mappings().all()
        except Exception:
            return events

        for row in rows:
            events.append(Evenement(
                id          = str(row["id"]),
                categorie   = CategorieEvenement.VERROU,
                operation   = "VERROU_POSE" if row.get("actif") else "VERROU_LEVE",
                entite_type = row.get("entite_type", ""),
                entite_id   = row.get("entite_id", ""),
                details     = {
                    "workflow_type":  row.get("workflow_type"),
                    "raison":         row.get("raison", "")[:200] if row.get("raison") else "",
                    "actif":          row.get("actif"),
                    "date_expiration":str(row.get("date_expiration") or ""),
                },
                user_id    = row.get("verrouille_par"),
                region     = None,
                created_at = str(row["created_at"]),
            ))
        if rows:
            self._maj_curseur("verrous")
        return events

    async def _poll_transactions(self, db) -> List[Evenement]:
        events: List[Evenement] = []
        curseur = self._curseur("transactions")
        try:
            r = await db.execute(_text("""
                SELECT pt.id::TEXT, pt.parcelle_id::TEXT,
                       pt.type_transfert, pt.statut,
                       pt.montant, pt.devise,
                       pt.notaire_id::TEXT, pt.created_at
                FROM property_transfers pt
                WHERE pt.created_at > :ts
                ORDER BY pt.created_at DESC
                LIMIT 50
            """), {"ts": curseur})
            rows = r.mappings().all()
        except Exception:
            return events

        for row in rows:
            events.append(Evenement(
                id          = str(row["id"]),
                categorie   = CategorieEvenement.TRANSACTION,
                operation   = f"TRANSFERT_{row.get('statut','').upper()}",
                entite_type = "property_transfer",
                entite_id   = row.get("parcelle_id", ""),
                details     = {
                    "type_transfert": row.get("type_transfert"),
                    "statut":         row.get("statut"),
                    "montant":        float(row.get("montant") or 0),
                    "devise":         row.get("devise", "XOF"),
                },
                user_id    = row.get("notaire_id"),
                region     = None,
                created_at = str(row["created_at"]),
            ))
        if rows:
            self._maj_curseur("transactions")
        return events

    async def _poll_simulations(self, db) -> List[Evenement]:
        events: List[Evenement] = []
        curseur = self._curseur("simulations")
        try:
            r = await db.execute(_text("""
                SELECT sl.id::TEXT, sl.simulation_id::TEXT,
                       sl.operation_type, sl.statut,
                       sl.nb_bloquants, sl.autorisee,
                       sl.user_id::TEXT, sl.region, sl.created_at
                FROM simulation_log sl
                WHERE sl.created_at > :ts
                ORDER BY sl.created_at DESC
                LIMIT 30
            """), {"ts": curseur})
            rows = r.mappings().all()
        except Exception:
            return events

        for row in rows:
            events.append(Evenement(
                id          = str(row["id"]),
                categorie   = CategorieEvenement.SYSTEME,
                operation   = f"SIMULATION_{row.get('statut','').upper()}",
                entite_type = "simulation",
                entite_id   = str(row.get("simulation_id", "")),
                details     = {
                    "operation_type": row.get("operation_type"),
                    "statut":         row.get("statut"),
                    "nb_bloquants":   row.get("nb_bloquants", 0),
                    "autorisee":      row.get("autorisee", False),
                },
                user_id    = row.get("user_id"),
                region     = row.get("region"),
                created_at = str(row["created_at"]),
            ))
        if rows:
            self._maj_curseur("simulations")
        return events


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DÉTECTEURS D'ANOMALIES (6 heuristiques)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DetecteurBase:
    id          = "D0"
    description = "Détecteur de base"

    def analyser(
        self,
        events:   Deque[Evenement],
        alertes_actives: List[Alerte],
    ) -> List[Alerte]:
        return []


class D1_WorkflowBlockage(DetecteurBase):
    """
    Détecte les workflows bloqués (en cours depuis trop longtemps).
    CRITICAL si > SEUIL_WORKFLOWS_BLOQUES workflows bloqués simultanément.
    WARNING  pour les workflows individuels dépassant leur SLA.
    """
    id          = "D1_WORKFLOW_BLOCKAGE"
    description = "Blocages de workflows"

    def analyser(self, events, alertes_actives):
        alertes: List[Alerte] = []
        maintenant = time.monotonic()
        fenetre = maintenant - WINDOW_SHORT_SEC

        # Compter les workflows bloqués dans la fenêtre
        bloques = [
            e for e in events
            if e.categorie == CategorieEvenement.WORKFLOW
            and e.ts > fenetre
            and e.details.get("statut_instance") == "en_cours"
        ]

        # Déduplication par instance
        instances_bloquees: Set[str] = set()
        for e in bloques:
            instances_bloquees.add(e.entite_id)

        nb = len(instances_bloquees)
        if nb >= SEUIL_WORKFLOWS_BLOQUES:
            # Éviter les doublons d'alertes
            deja_alerte = any(
                a.detecteur == self.id and not a.acquittee
                for a in alertes_actives
            )
            if not deja_alerte:
                alertes.append(_creer_alerte(
                    NiveauAlerte.CRITICAL, self.id,
                    f"Blocage workflows : {nb} instances bloquées",
                    f"{nb} workflows sont en cours depuis plus de {WINDOW_SHORT_SEC//60} min "
                    f"sans progression.",
                    {"nb_bloques": nb, "instances": list(instances_bloquees)[:10]},
                ))
        elif nb > 0:
            nb_warn_actives = sum(
                1 for a in alertes_actives
                if a.detecteur == self.id
                and a.niveau == NiveauAlerte.WARNING
                and not a.acquittee
            )
            if nb_warn_actives == 0:
                alertes.append(_creer_alerte(
                    NiveauAlerte.WARNING, self.id,
                    f"{nb} workflow(s) potentiellement bloqué(s)",
                    f"Des workflows sont en cours depuis plus de {WINDOW_SHORT_SEC//60} min.",
                    {"nb_bloques": nb},
                ))

        return alertes


class D2_Surcharge(DetecteurBase):
    """
    Détecte la surcharge système : trop d'opérations en un court délai.
    WARNING  si > SEUIL_SURCHARGE_OPS ops dans la fenêtre courte.
    CRITICAL si > 3× le seuil.
    """
    id          = "D2_SURCHARGE"
    description = "Surcharge système"

    def analyser(self, events, alertes_actives):
        alertes: List[Alerte] = []
        maintenant = time.monotonic()
        fenetre = maintenant - WINDOW_SHORT_SEC

        nb_ops = sum(1 for e in events if e.ts > fenetre)

        if nb_ops > SEUIL_SURCHARGE_OPS * 3:
            if not any(a.detecteur == self.id and a.niveau == NiveauAlerte.CRITICAL
                       and not a.acquittee for a in alertes_actives):
                alertes.append(_creer_alerte(
                    NiveauAlerte.CRITICAL, self.id,
                    f"Surcharge critique : {nb_ops} ops/{WINDOW_SHORT_SEC//60}min",
                    f"Volume {nb_ops} dépasse 3× le seuil ({SEUIL_SURCHARGE_OPS}). "
                    "Risque de dégradation des performances.",
                    {"nb_ops": nb_ops, "seuil": SEUIL_SURCHARGE_OPS},
                ))
        elif nb_ops > SEUIL_SURCHARGE_OPS:
            if not any(a.detecteur == self.id and not a.acquittee
                       for a in alertes_actives):
                alertes.append(_creer_alerte(
                    NiveauAlerte.WARNING, self.id,
                    f"Surcharge : {nb_ops} ops/{WINDOW_SHORT_SEC//60}min",
                    f"Volume d'opérations élevé. Seuil normal : {SEUIL_SURCHARGE_OPS}.",
                    {"nb_ops": nb_ops, "seuil": SEUIL_SURCHARGE_OPS},
                ))
        return alertes


class D3_ConflitValidation(DetecteurBase):
    """
    Détecte les conflits de validation répétés.
    Si un grand nombre de validations sont REFUSÉES sur la même entité
    dans la fenêtre courte → tentatives d'injection ou configuration incorrecte.
    """
    id          = "D3_CONFLIT_VALIDATION"
    description = "Conflits de validation répétés"

    def analyser(self, events, alertes_actives):
        alertes: List[Alerte] = []
        maintenant = time.monotonic()
        fenetre = maintenant - WINDOW_SHORT_SEC

        refus = [
            e for e in events
            if e.ts > fenetre
            and e.categorie == CategorieEvenement.VALIDATION
            and "REFUSE" in e.operation
        ]

        if len(refus) >= SEUIL_VALIDATIONS_REFUSEES:
            # Grouper par entité
            par_entite: Dict[str, int] = defaultdict(int)
            for e in refus:
                par_entite[e.entite_id] += 1

            top = sorted(par_entite.items(), key=lambda x: -x[1])[:5]
            if not any(a.detecteur == self.id and not a.acquittee
                       for a in alertes_actives):
                alertes.append(_creer_alerte(
                    NiveauAlerte.WARNING, self.id,
                    f"{len(refus)} validations refusées en {WINDOW_SHORT_SEC//60} min",
                    "Nombre anormal de refus détecté. Possible mauvaise configuration "
                    "ou tentatives répétées sur des entités bloquées.",
                    {"nb_refus": len(refus), "top_entites": top},
                ))
        return alertes


class D4_AccesInhabituel(DetecteurBase):
    """
    Détecte les accès inhabituels : un utilisateur effectuant un nombre
    anormal d'opérations dans la fenêtre courte.
    """
    id          = "D4_ACCES_INHABITUEL"
    description = "Accès inhabituels"

    def analyser(self, events, alertes_actives):
        alertes: List[Alerte] = []
        maintenant = time.monotonic()
        fenetre = maintenant - WINDOW_SHORT_SEC

        par_user: Dict[str, int] = defaultdict(int)
        for e in events:
            if e.ts > fenetre and e.user_id:
                par_user[e.user_id] += 1

        suspects = [
            (uid, nb) for uid, nb in par_user.items()
            if nb >= SEUIL_ACCES_MEME_USER
        ]

        for uid, nb in suspects[:3]:
            deja = any(
                a.detecteur == self.id
                and a.details.get("user_id") == uid
                and not a.acquittee
                for a in alertes_actives
            )
            if not deja:
                alertes.append(_creer_alerte(
                    NiveauAlerte.WARNING, self.id,
                    f"Accès inhabituel : {nb} ops par un utilisateur",
                    f"L'utilisateur {uid[:8]}… a effectué {nb} opérations en "
                    f"{WINDOW_SHORT_SEC//60} min (seuil : {SEUIL_ACCES_MEME_USER}).",
                    {"user_id": uid, "nb_ops": nb, "seuil": SEUIL_ACCES_MEME_USER},
                ))
        return alertes


class D5_RatioEchec(DetecteurBase):
    """
    Détecte un taux d'échec anormalement élevé sur les règles SQL
    (sandbox, logique) ou les simulations.
    """
    id          = "D5_RATIO_ECHEC"
    description = "Taux d'échec anormal"

    def analyser(self, events, alertes_actives):
        alertes: List[Alerte] = []
        maintenant = time.monotonic()
        fenetre = maintenant - WINDOW_LONG_SEC

        regles_recentes = [
            e for e in events
            if e.ts > fenetre and e.categorie == CategorieEvenement.REGLE
        ]
        if len(regles_recentes) < 5:
            return alertes

        echecs = [e for e in regles_recentes if "REFUSE" in e.operation]
        ratio  = len(echecs) / len(regles_recentes) * 100

        if ratio >= SEUIL_RATIO_ECHEC_PCT:
            if not any(a.detecteur == self.id and not a.acquittee
                       for a in alertes_actives):
                alertes.append(_creer_alerte(
                    NiveauAlerte.WARNING, self.id,
                    f"Taux d'échec règles élevé : {ratio:.0f}%",
                    f"{len(echecs)}/{len(regles_recentes)} validations SQL refusées "
                    f"dans la dernière heure. Seuil : {SEUIL_RATIO_ECHEC_PCT}%.",
                    {"ratio_pct": round(ratio, 1), "nb_echecs": len(echecs),
                     "nb_total": len(regles_recentes)},
                ))
        return alertes


class D6_VerrouCadeau(DetecteurBase):
    """
    Détecte l'accumulation de verrous actifs (entity_lock).
    Un grand nombre de verrous non libérés indique un dysfonctionnement
    ou des opérations abandonnées sans nettoyage.
    """
    id          = "D6_VERROU_CADEAU"
    description = "Accumulation de verrous"

    def analyser(self, events, alertes_actives):
        alertes: List[Alerte] = []
        maintenant = time.monotonic()
        fenetre = maintenant - WINDOW_LONG_SEC

        verrous_poses = [
            e for e in events
            if e.ts > fenetre
            and e.categorie == CategorieEvenement.VERROU
            and "POSE" in e.operation
        ]
        verrous_leves = [
            e for e in events
            if e.ts > fenetre
            and e.categorie == CategorieEvenement.VERROU
            and "LEVE" in e.operation
        ]

        # Verrous nets = posés - levés
        nb_nets = len(verrous_poses) - len(verrous_leves)

        if nb_nets >= SEUIL_VERROUS_ACTIFS:
            if not any(a.detecteur == self.id and not a.acquittee
                       for a in alertes_actives):
                alertes.append(_creer_alerte(
                    NiveauAlerte.WARNING, self.id,
                    f"{nb_nets} verrous actifs non libérés",
                    f"{nb_nets} verrous ont été posés sans être levés dans la dernière heure. "
                    "Des opérations pourraient être bloquées.",
                    {"nb_verrous_nets": nb_nets, "poses": len(verrous_poses),
                     "leves": len(verrous_leves)},
                ))
        return alertes


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _creer_alerte(
    niveau: NiveauAlerte,
    detecteur: str,
    titre: str,
    message: str,
    details: Dict[str, Any],
) -> Alerte:
    import uuid as _uuid
    alerte_id = str(_uuid.uuid4())
    sha = hashlib.sha256(
        json.dumps({"id": alerte_id, "titre": titre,
                    "niveau": niveau.value}, sort_keys=True).encode()
    ).hexdigest()
    return Alerte(
        id=alerte_id, niveau=niveau, detecteur=detecteur,
        titre=titre, message=message, details=details, sha256=sha,
    )


# Adaptateur SQLAlchemy text() — fallback si non disponible
try:
    from sqlalchemy import text as _sa_text
    def _text(q): return _sa_text(q)
except ImportError:
    def _text(q): return q  # type: ignore


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GESTIONNAIRE D'ALERTES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AlerteManager:
    """Centralise la création, le stockage et la diffusion des alertes."""

    def __init__(self) -> None:
        self._alertes:  List[Alerte] = []
        self._clients:  List[asyncio.Queue] = []

    def ajouter(self, alerte: Alerte) -> None:
        self._alertes.append(alerte)
        if len(self._alertes) > MAX_ALERTS_BUFFER:
            self._alertes = self._alertes[-MAX_ALERTS_BUFFER:]

    def acquitter(self, alerte_id: str, user_id: str) -> bool:
        for a in self._alertes:
            if a.id == alerte_id and not a.acquittee:
                a.acquittee     = True
                a.acquittee_par = user_id
                return True
        return False

    def actives(self) -> List[Alerte]:
        return [a for a in self._alertes if not a.acquittee]

    def historique(self, limite: int = 100) -> List[Alerte]:
        return list(reversed(self._alertes[-limite:]))

    def abonner(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        if len(self._clients) < MAX_CLIENTS_SSE:
            self._clients.append(q)
        return q

    def desabonner(self, q: asyncio.Queue) -> None:
        if q in self._clients:
            self._clients.remove(q)

    async def diffuser(self, payload: dict) -> None:
        """Envoie un événement à tous les clients SSE abonnés."""
        morts = []
        for q in self._clients:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                morts.append(q)
        for q in morts:
            self.desabonner(q)

    async def persister(self, db, alerte: Alerte) -> None:
        """Stocke l'alerte dans alerte_monitoring (append-only)."""
        if db is None:
            return
        try:
            await db.execute(_text("""
                INSERT INTO alerte_monitoring (
                    alerte_id, niveau, detecteur, titre,
                    message, details_json, sha256
                ) VALUES (
                    :aid::uuid, :niv, :det, :tit,
                    :msg, :det_json::jsonb, :sha
                )
            """), {
                "aid":      alerte.id,
                "niv":      alerte.niveau.value,
                "det":      alerte.detecteur,
                "tit":      alerte.titre,
                "msg":      alerte.message,
                "det_json": json.dumps(alerte.details),
                "sha":      alerte.sha256,
            })
        except Exception as exc:
            _log.debug("alerte_monitoring persist: %s", exc)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MOTEUR DE MONITORING PRINCIPAL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _boucle(self) -> None:
        """Boucle principale : collecte → détection → alertes → diffusion."""
        import contextlib  # Assure l'importation locale si nécessaire

        while self._running:
            t_start = time.monotonic()
            try:
                db_ctx = self._db_factory()
                
                # Cas 1 : C'est déjà un gestionnaire de contexte asynchrone classique
                if hasattr(db_ctx, "__aenter__"):
                    async with db_ctx as db:
                        await self._cycle(db)
                        
                # Cas 2 : C'est un générateur asynchrone (FastAPI style get_db)
                elif hasattr(db_ctx, "__anext__") or hasattr(db_ctx, "asend"):
                    # On l'enveloppe proprement pour en faire un contexte valide
                    wrapper = contextlib.asynccontextmanager(lambda: db_ctx)
                    async with wrapper() as db:
                        await self._cycle(db)
                else:
                    _log.error("Le db_factory fourni n'est ni un contexte ni un générateur asynchrone.")
                    
            except asyncio.CancelledError:
                break
            except Exception as exc:
                _log.warning("Monitoring cycle erreur: %s", exc)

            elapsed = time.monotonic() - t_start
            self._latences.append(elapsed * 1000)
            wait = max(0, POLL_INTERVAL_SEC - elapsed)
            await asyncio.sleep(wait)

        # Activité récente (fenêtre 5 min en mémoire)
        maintenant = time.monotonic()
        fenetre = maintenant - WINDOW_SHORT_SEC
        recents = [e for e in self._events if e.ts > fenetre]

        tb.ops_5min          = len(recents)
        tb.validations_5min  = sum(1 for e in recents if e.categorie == CategorieEvenement.VALIDATION)
        tb.echecs_5min       = sum(1 for e in recents if "REFUSE" in e.operation or "ECHEC" in e.operation)
        tb.simulations_5min  = sum(1 for e in recents if e.categorie == CategorieEvenement.SYSTEME and "SIMULATION" in e.operation)

        # Alertes actives
        actives = self.alertes.actives()
        tb.alertes_critical = sum(1 for a in actives if a.niveau == NiveauAlerte.CRITICAL)
        tb.alertes_warning  = sum(1 for a in actives if a.niveau == NiveauAlerte.WARNING)
        tb.alertes_info     = sum(1 for a in actives if a.niveau == NiveauAlerte.INFO)

        # Performance
        if self._latences:
            tb.latence_moy_ms = round(sum(self._latences) / len(self._latences), 1)

        # Santé globale
        if tb.alertes_critical > 0 or tb.workflows_bloques >= SEUIL_WORKFLOWS_BLOQUES:
            tb.sante = "CRITIQUE"
        elif tb.alertes_warning > 0 or tb.workflows_bloques > 0:
            tb.sante = "DÉGRADÉ"
        else:
            tb.sante = "OK"

        tb.calcule_a = datetime.now(timezone.utc).isoformat()
        return tb

    def events_recents(self, limite: int = 50) -> List[dict]:
        """Retourne les N derniers événements."""
        evts = list(self._events)[-limite:]
        return [e.as_dict() for e in reversed(evts)]

    def stats(self) -> dict:
        """Statistiques cumulées par catégorie."""
        return dict(self._stats)
