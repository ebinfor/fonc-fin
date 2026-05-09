"""
FONCIER+ ── Moteur d'Analyse Prédictive des Risques Fonciers
Anticipe les conflits et anomalies avant leur apparition.

Architecture :
  ┌──────────────────────────────────────────────────────────────┐
  │  RiskEngine                (orchestrateur principal)         │
  │    ├─ analyser_parcelle(id)  → RiskScore                    │
  │    ├─ analyser_zone(region)  → RiskScore                    │
  │    ├─ analyser_utilisateur(id) → RiskScore                  │
  │    ├─ analyser_operation(type) → RiskScore                  │
  │    ├─ rapport_global()       → RapportRisque                │
  │    └─ run_batch()            → calcul périodique en arrière  │
  │                                                              │
  │  Analyseurs (6) ─ chacun produit des SignauxRisque           │
  │    A1 ConflitZoneAnalyzer      litiges + conflits géo        │
  │    A2 MutationFreqAnalyzer     transfers multiples/parcelle  │
  │    A3 UtilisateurInstableAnalyzer  refus + anomalies accès   │
  │    A4 DensiteMutationAnalyzer  concentration géo mutations   │
  │    A5 HistoriqueAnomalieAnalyzer   audit_immutable patterns  │
  │    A6 OperationRisqueeAnalyzer     types ops + simulations   │
  │                                                              │
  │  ScoreCalculator             (agrégation pondérée)           │
  │    └─ score = Σ(poids_i × signal_i) / Σ(poids_i)  ∈ [0,100]│
  │                                                              │
  │  RiskAlertGenerator          (seuillage → alertes)           │
  │    ├─ FAIBLE    : score  0-25                                │
  │    ├─ MOYEN     : score 26-50                                │
  │    ├─ ÉLEVÉ     : score 51-75                                │
  │    └─ CRITIQUE  : score 76-100                               │
  └──────────────────────────────────────────────────────────────┘

Sources de données analysées :
  • parcelles            — statut, gel, surface, région
  • property_transfers   — fréquence mutations, montants
  • litiges              — conflits judiciaires actifs
  • conflits_parcellaires— superpositions géographiques
  • audit_immutable      — fréquence modifications
  • decision_engine_log  — taux de refus par entité/user
  • entity_lock          — verrous répétés
  • workflow_instances   — blocages
  • simulation_log       — simulations échouées
  • alerte_monitoring    — alertes système déjà levées

Intégration pipeline :
  Le moteur ne BLOQUE PAS les opérations.
  Il enrichit la réponse DecisionEngine avec un champ risk_score
  et peut déclencher une validation renforcée (niveau + 1) si CRITIQUE.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("foncier.risk")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONSTANTES & SEUILS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class NiveauRisque(str, Enum):
    FAIBLE   = "FAIBLE"    # 0-25
    MOYEN    = "MOYEN"     # 26-50
    ELEVE    = "ÉLEVÉ"     # 51-75
    CRITIQUE = "CRITIQUE"  # 76-100


class TypeEntite(str, Enum):
    PARCELLE   = "PARCELLE"
    ZONE       = "ZONE"
    UTILISATEUR= "UTILISATEUR"
    OPERATION  = "OPERATION"


# Seuils de score
SEUIL_FAIBLE   = 25
SEUIL_MOYEN    = 50
SEUIL_ELEVE    = 75

# Poids des analyseurs (somme = 100)
POIDS_ANALYSEURS = {
    "A1_CONFLIT_ZONE":        25,  # conflits judiciaires/géo
    "A2_MUTATION_FREQ":       20,  # fréquence mutations
    "A3_UTILISATEUR":         20,  # comportement utilisateur
    "A4_DENSITE_MUTATION":    15,  # concentration géographique
    "A5_HISTORIQUE":          10,  # anomalies historiques
    "A6_OPERATION":           10,  # type d'opération risqué
}

# Fenêtres temporelles
FENETRE_COURTE_JOURS  = 30
FENETRE_LONGUE_JOURS  = 365

# Adaptateur SQLAlchemy text() avec fallback si non disponible
try:
    from sqlalchemy import text as _sa_text
    def _text(q): return _sa_text(q)
except ImportError:
    def _text(q): return q  # type: ignore


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TYPES RÉSULTATS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class SignalRisque:
    """Un signal individuel produit par un analyseur."""
    analyseur:   str
    valeur:      float    # valeur brute normalisée [0-100]
    poids:       int      # poids de cet analyseur
    description: str      # explication du signal
    donnees:     Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskScore:
    """Score de risque agrégé pour une entité."""
    entite_type:  TypeEntite
    entite_id:    str
    score:        float       # [0-100]
    niveau:       NiveauRisque
    signaux:      List[SignalRisque] = field(default_factory=list)
    facteurs_cles: List[str]         = field(default_factory=list)
    recommandations: List[str]       = field(default_factory=list)
    duree_ms:     int  = 0
    calcule_a:    str  = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sha256:       str  = ""

    def as_dict(self) -> dict:
        return {
            "entite_type":     self.entite_type.value,
            "entite_id":       self.entite_id,
            "score":           round(self.score, 1),
            "niveau":          self.niveau.value,
            "facteurs_cles":   self.facteurs_cles,
            "recommandations": self.recommandations,
            "duree_ms":        self.duree_ms,
            "calcule_a":       self.calcule_a,
            "sha256":          self.sha256,
            "signaux": [
                {
                    "analyseur":   s.analyseur,
                    "valeur":      round(s.valeur, 1),
                    "poids":       s.poids,
                    "description": s.description,
                    "donnees":     s.donnees,
                }
                for s in self.signaux
            ],
        }


@dataclass
class AlertePredictive:
    """Alerte prédictive générée par le moteur de risque."""
    id:             str
    niveau:         NiveauRisque
    entite_type:    TypeEntite
    entite_id:      str
    titre:          str
    message:        str
    score:          float
    facteurs:       List[str]
    recommandation: str
    calcule_a:      str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sha256:         str = ""

    def as_dict(self) -> dict:
        return {
            "id":             self.id,
            "niveau":         self.niveau.value,
            "entite_type":    self.entite_type.value,
            "entite_id":      self.entite_id,
            "titre":          self.titre,
            "message":        self.message,
            "score":          round(self.score, 1),
            "facteurs":       self.facteurs,
            "recommandation": self.recommandation,
            "calcule_a":      self.calcule_a,
            "sha256":         self.sha256,
        }


@dataclass
class RapportRisque:
    """Rapport global produit par un run_batch()."""
    nb_parcelles_analysees:  int = 0
    nb_zones_analysees:      int = 0
    nb_users_analyses:       int = 0
    nb_alertes_generees:     int = 0
    nb_critique:             int = 0
    nb_eleve:                int = 0
    nb_moyen:                int = 0
    nb_faible:               int = 0
    top_parcelles_risque:    List[dict] = field(default_factory=list)
    top_zones_risque:        List[dict] = field(default_factory=list)
    top_users_risque:        List[dict] = field(default_factory=list)
    duree_ms:                int = 0
    calcule_a:               str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return asdict(self)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ANALYSEUR DE BASE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AnalyseurBase:
    id          = "A0"
    description = "Analyseur de base"
    poids       = 0

    async def analyser_parcelle(self, db, parcelle_id: str) -> Optional[SignalRisque]:
        """Analyser le risque sur une parcelle spécifique.
        Retourne None si cet analyseur ne couvre pas cette dimension.
        Surchargé dans A1 (ConflitZone), A2 (MutationFreq),
        A4 (DensiteMutation), A5 (HistoriqueAnomalie), A6 (OperationRisquee).
        """
        return None

    async def analyser_zone(self, db, region: str) -> Optional[SignalRisque]:
        """Analyser le risque sur une région entière.
        Surchargé dans A1, A2, A4, A5.
        """
        return None

    async def analyser_utilisateur(self, db, user_id: str) -> Optional[SignalRisque]:
        """Analyser le risque associé à un utilisateur.
        Surchargé dans A3 (UtilisateurInstable).
        """
        return None

    async def analyser_operation(self, db, op_type: str) -> Optional[SignalRisque]:
        """Analyser le risque d'un type d'opération (WF, mutation, hypothèque…).
        Surchargé dans A6 (OperationRisquee).
        """
        return None

    def _signal(self, valeur: float, description: str, donnees: dict = None) -> SignalRisque:
        return SignalRisque(
            analyseur=self.id,
            valeur=max(0.0, min(100.0, valeur)),
            poids=self.poids,
            description=description,
            donnees=donnees or {},
        )

    @staticmethod
    def _norm(val: float, max_val: float) -> float:
        """Normalise val dans [0, 100] avec max_val comme référence."""
        if max_val <= 0:
            return 0.0
        return min(100.0, (val / max_val) * 100.0)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# A1 — Analyseur Conflits de Zone
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class A1_ConflitZone(AnalyseurBase):
    """
    Mesure le risque de conflit basé sur :
    - Litiges judiciaires actifs sur la parcelle / dans la zone
    - Conflits géographiques (superpositions)
    - Taux de conflits historiques dans la région
    """
    id    = "A1_CONFLIT_ZONE"
    poids = POIDS_ANALYSEURS["A1_CONFLIT_ZONE"]

    async def analyser_parcelle(self, db, parcelle_id: str) -> Optional[SignalRisque]:
        try:
            r = await db.execute(_text("""
                SELECT
                    COUNT(DISTINCT l.id) FILTER (WHERE l.statut = 'ACTIF')       AS litiges_actifs,
                    COUNT(DISTINCT l.id) FILTER (WHERE l.statut != 'RESOLU')       AS litiges_total,
                    COUNT(DISTINCT cp.id) FILTER (WHERE cp.statut = 'ouvert')      AS conflits_geo_ouverts,
                    COUNT(DISTINCT cp.id)                                           AS conflits_geo_total,
                    COALESCE(MAX(l.created_at), '1970-01-01'::TIMESTAMP)           AS dernier_litige
                FROM parcelles p
                LEFT JOIN litiges l          ON l.parcelle_id = p.id
                LEFT JOIN conflits_parcellaires cp
                    ON (cp.parcelle_id_ref = p.id OR cp.parcelle_id_conflit = p.id)
                WHERE p.id = :pid::uuid
            """), {"pid": parcelle_id})
            row = r.mappings().first()
        except Exception:
            return None

        if not row:
            return None

        la = int(row.get("litiges_actifs") or 0)
        lt = int(row.get("litiges_total") or 0)
        cg = int(row.get("conflits_geo_ouverts") or 0)
        cgt= int(row.get("conflits_geo_total") or 0)

        # Calcul du score : litiges actifs pèsent le plus
        score = min(100.0, la * 35 + cg * 20 + lt * 5 + cgt * 3)

        return self._signal(
            score,
            f"{la} litige(s) actif(s), {cg} conflit(s) géo ouvert(s)",
            {"litiges_actifs": la, "litiges_total": lt,
             "conflits_geo_ouverts": cg, "conflits_geo_total": cgt},
        )

    async def analyser_zone(self, db, region: str) -> Optional[SignalRisque]:
        try:
            r = await db.execute(_text("""
                SELECT
                    COUNT(DISTINCT l.id) FILTER (WHERE l.statut = 'ACTIF')       AS litiges_actifs,
                    COUNT(DISTINCT p.id)                                           AS parcelles_avec_litige,
                    COUNT(DISTINCT cp.id) FILTER (WHERE cp.statut = 'ouvert')     AS conflits_geo,
                    COUNT(DISTINCT p2.id)                                          AS total_parcelles
                FROM parcelles p2
                LEFT JOIN litiges l ON l.parcelle_id = p2.id
                LEFT JOIN parcelles p ON p.region = :reg
                LEFT JOIN conflits_parcellaires cp
                    ON (cp.parcelle_id_ref = p.id OR cp.parcelle_id_conflit = p.id)
                WHERE p2.region = :reg
            """), {"reg": region})
            row = r.mappings().first()
        except Exception:
            return None

        if not row:
            return None

        total  = int(row.get("total_parcelles") or 1)
        la     = int(row.get("litiges_actifs") or 0)
        pawl   = int(row.get("parcelles_avec_litige") or 0)
        cg     = int(row.get("conflits_geo") or 0)

        taux_conflit = (pawl / total * 100) if total > 0 else 0
        score = min(100.0, taux_conflit * 1.5 + la * 2 + cg * 1.5)

        return self._signal(
            score,
            f"Zone {region}: {taux_conflit:.1f}% parcelles en conflit",
            {"taux_conflit_pct": round(taux_conflit, 1),
             "litiges_actifs": la, "conflits_geo": cg, "total_parcelles": total},
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# A2 — Analyseur Fréquence de Mutations
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class A2_MutationFreq(AnalyseurBase):
    """
    Détecte les parcelles fréquemment mutées (signe de spéculation ou fraude).
    Analyse : nombre de transferts sur différentes fenêtres temporelles.
    """
    id    = "A2_MUTATION_FREQ"
    poids = POIDS_ANALYSEURS["A2_MUTATION_FREQ"]

    async def analyser_parcelle(self, db, parcelle_id: str) -> Optional[SignalRisque]:
        try:
            r = await db.execute(_text("""
                SELECT
                    COUNT(*) FILTER (WHERE pt.created_at >= NOW() - INTERVAL '30 days')  AS muts_30j,
                    COUNT(*) FILTER (WHERE pt.created_at >= NOW() - INTERVAL '180 days') AS muts_6m,
                    COUNT(*) FILTER (WHERE pt.created_at >= NOW() - INTERVAL '365 days') AS muts_1an,
                    COUNT(*)                                                               AS muts_total,
                    AVG(EXTRACT(DAY FROM AGE(pt.created_at, LAG(pt.created_at) OVER
                        (ORDER BY pt.created_at))))                                        AS intervalle_moy_jours
                FROM property_transfers pt
                WHERE pt.parcelle_id = :pid::uuid
                  AND pt.statut = 'valide'
            """), {"pid": parcelle_id})
            row = r.mappings().first()
        except Exception:
            return None

        if not row:
            return self._signal(0, "Aucun transfert historique", {})

        m30 = int(row.get("muts_30j") or 0)
        m6  = int(row.get("muts_6m") or 0)
        m1  = int(row.get("muts_1an") or 0)
        tot = int(row.get("muts_total") or 0)
        intv= float(row.get("intervalle_moy_jours") or 9999)

        # Scoring : mutations récentes et fréquentes = risque élevé
        score = min(100.0,
                    m30 * 25   # mutation ce mois = très suspect
                    + m6 * 8
                    + m1 * 3
                    + max(0, (30 - intv) * 1.5)  # intervalles courts
                )

        desc = f"{m1} mutation(s)/an, {m30} ce mois"
        return self._signal(
            score, desc,
            {"muts_30j": m30, "muts_6m": m6, "muts_1an": m1,
             "muts_total": tot, "intervalle_moy_jours": round(intv, 1)},
        )

    async def analyser_zone(self, db, region: str) -> Optional[SignalRisque]:
        try:
            r = await db.execute(_text("""
                SELECT
                    COUNT(pt.id) AS total_mutations,
                    COUNT(DISTINCT pt.parcelle_id) AS parcelles_mutees,
                    COUNT(pt.id) FILTER (
                        WHERE pt.created_at >= NOW() - INTERVAL '90 days'
                    ) AS mutations_recentes,
                    AVG(pt.montant) FILTER (WHERE pt.montant IS NOT NULL) AS montant_moyen
                FROM property_transfers pt
                JOIN parcelles p ON p.id = pt.parcelle_id
                WHERE p.region = :reg
                  AND pt.statut = 'valide'
                  AND pt.created_at >= NOW() - INTERVAL '365 days'
            """), {"reg": region})
            row = r.mappings().first()
        except Exception:
            return None

        if not row:
            return self._signal(0, "Aucune donnée de mutation", {})

        total   = int(row.get("total_mutations") or 0)
        recents = int(row.get("mutations_recentes") or 0)
        pmutees = int(row.get("parcelles_mutees") or 0)
        montant = float(row.get("montant_moyen") or 0)

        # Forte densité de mutations récentes = zone spéculative
        score = min(100.0, recents * 0.5 + total * 0.2 + pmutees * 0.3)

        return self._signal(
            score,
            f"Zone {region}: {total} mutations/an, {recents} récentes",
            {"total_mutations": total, "mutations_recentes": recents,
             "parcelles_mutees": pmutees, "montant_moyen_xof": round(montant)},
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# A3 — Analyseur Utilisateur Instable
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class A3_UtilisateurInstable(AnalyseurBase):
    """
    Évalue le comportement d'un utilisateur :
    - Taux de validations refusées (decision_engine_log)
    - Accès inhabituels détectés (alerte_monitoring)
    - Simulations échouées répétées
    - Validations SQL rejetées (injection, bypass)
    """
    id    = "A3_UTILISATEUR"
    poids = POIDS_ANALYSEURS["A3_UTILISATEUR"]

    async def analyser_utilisateur(self, db, user_id: str) -> Optional[SignalRisque]:
        try:
            r = await db.execute(_text("""
                SELECT
                    COUNT(*) FILTER (WHERE del.decision = 'REFUSE')                   AS refus_total,
                    COUNT(*)                                                            AS ops_total,
                    COUNT(*) FILTER (WHERE del.created_at >= NOW() - INTERVAL '30 days'
                                     AND del.decision = 'REFUSE')                      AS refus_30j,
                    COUNT(DISTINCT sl.id) FILTER (WHERE sl.statut = 'ECHEC')           AS simulations_echec,
                    COUNT(DISTINCT svl.id) FILTER (WHERE svl.statut = 'REFUSE')        AS regles_refusees,
                    COUNT(DISTINCT sal.id)                                              AS alertes_acces
                FROM users u
                LEFT JOIN decision_engine_log del
                    ON del.effectue_par = u.id
                    AND del.created_at >= NOW() - INTERVAL '90 days'
                LEFT JOIN simulation_log sl
                    ON sl.user_id = u.id
                    AND sl.created_at >= NOW() - INTERVAL '90 days'
                LEFT JOIN sql_validation_log svl
                    ON svl.soumis_par = u.id
                    AND svl.created_at >= NOW() - INTERVAL '90 days'
                LEFT JOIN sql_bypass_alert_log sal
                    ON sal.user_id = u.id
                    AND sal.created_at >= NOW() - INTERVAL '30 days'
                WHERE u.id = :uid::uuid
                GROUP BY u.id
            """), {"uid": user_id})
            row = r.mappings().first()
        except Exception:
            return None

        if not row:
            return self._signal(0, "Aucune activité détectée", {})

        ops   = int(row.get("ops_total") or 1)
        refus = int(row.get("refus_total") or 0)
        r30   = int(row.get("refus_30j") or 0)
        sims  = int(row.get("simulations_echec") or 0)
        regl  = int(row.get("regles_refusees") or 0)
        acces = int(row.get("alertes_acces") or 0)

        taux_refus = (refus / ops * 100) if ops > 0 else 0
        score = min(100.0,
                    taux_refus * 0.8    # taux refus global
                    + r30 * 5           # refus récents pèsent plus
                    + sims * 8          # simulations échouées
                    + regl * 12         # règles SQL rejetées
                    + acces * 20        # alertes accès inhabituel
                )

        return self._signal(
            score,
            f"Taux refus {taux_refus:.0f}%, {regl} règle(s) SQL rejetée(s)",
            {"taux_refus_pct": round(taux_refus, 1), "refus_total": refus,
             "refus_30j": r30, "simulations_echec": sims,
             "regles_refusees": regl, "alertes_acces": acces},
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# A4 — Analyseur Densité de Mutations
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class A4_DensiteMutation(AnalyseurBase):
    """
    Mesure la concentration spatiale des mutations dans une zone.
    Une forte densité sur une zone géographique restreinte
    indique un risque de spéculation foncière.
    """
    id    = "A4_DENSITE_MUTATION"
    poids = POIDS_ANALYSEURS["A4_DENSITE_MUTATION"]

    async def analyser_zone(self, db, region: str) -> Optional[SignalRisque]:
        try:
            r = await db.execute(_text("""
                WITH mutations_par_commune AS (
                    SELECT
                        p.commune,
                        COUNT(pt.id)                              AS nb_mutations,
                        COUNT(DISTINCT pt.parcelle_id)            AS parcelles_uniques,
                        SUM(pt.montant) FILTER (WHERE pt.montant > 0) AS montant_total
                    FROM property_transfers pt
                    JOIN parcelles p ON p.id = pt.parcelle_id
                    WHERE p.region = :reg
                      AND pt.statut = 'valide'
                      AND pt.created_at >= NOW() - INTERVAL '180 days'
                    GROUP BY p.commune
                ),
                stats AS (
                    SELECT
                        COUNT(DISTINCT p.commune)   AS nb_communes,
                        COUNT(DISTINCT p.id)         AS total_parcelles
                    FROM parcelles p WHERE p.region = :reg
                )
                SELECT
                    COALESCE(MAX(m.nb_mutations), 0)                     AS max_mutations_commune,
                    COALESCE(AVG(m.nb_mutations), 0)                     AS moy_mutations_commune,
                    COALESCE(MAX(m.nb_mutations) - AVG(m.nb_mutations), 0) AS ecart,
                    s.nb_communes,
                    s.total_parcelles,
                    COALESCE(SUM(m.montant_total), 0)                    AS montant_total_region
                FROM stats s
                LEFT JOIN mutations_par_commune m ON TRUE
                GROUP BY s.nb_communes, s.total_parcelles
            """), {"reg": region})
            row = r.mappings().first()
        except Exception:
            return None

        if not row:
            return self._signal(0, "Aucune donnée de densité", {})

        max_c   = float(row.get("max_mutations_commune") or 0)
        moy_c   = float(row.get("moy_mutations_commune") or 0)
        ecart   = float(row.get("ecart") or 0)
        total_p = int(row.get("total_parcelles") or 1)
        montant = float(row.get("montant_total_region") or 0)

        # Forte concentration = écart max/moy élevé
        conc_score = min(100.0, ecart * 2 + max_c * 1.5)
        score      = conc_score

        return self._signal(
            score,
            f"Densité mutations: max {max_c:.0f}/commune, moy {moy_c:.1f}",
            {"max_mutations_commune": round(max_c),
             "moy_mutations_commune": round(moy_c, 1),
             "ecart_type": round(ecart, 1),
             "montant_total_xof": round(montant)},
        )

    async def analyser_parcelle(self, db, parcelle_id: str) -> Optional[SignalRisque]:
        """
        Pour une parcelle : vérifie si sa commune a une forte densité de mutations.
        """
        try:
            r = await db.execute(_text("""
                SELECT
                    COUNT(pt2.id) AS mutations_commune_6m
                FROM parcelles p1
                JOIN parcelles p2 ON p2.commune = p1.commune
                LEFT JOIN property_transfers pt2
                    ON pt2.parcelle_id = p2.id
                    AND pt2.created_at >= NOW() - INTERVAL '180 days'
                    AND pt2.statut = 'valide'
                WHERE p1.id = :pid::uuid
            """), {"pid": parcelle_id})
            row = r.mappings().first()
        except Exception:
            return None

        if not row:
            return self._signal(0, "Contexte communal inconnu", {})

        mc = int(row.get("mutations_commune_6m") or 0)
        score = self._norm(mc, 50)  # 50 mutations/commune/6m = score max

        return self._signal(
            score,
            f"{mc} mutation(s) dans la commune (6 mois)",
            {"mutations_commune_6m": mc},
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# A5 — Analyseur Anomalies Historiques
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class A5_HistoriqueAnomalie(AnalyseurBase):
    """
    Détecte les patterns anormaux dans l'historique d'une entité :
    - Modifications très fréquentes (audit_immutable)
    - Verrous répétés (entity_lock)
    - Workflows bloqués récurrents
    - Simulations échouées sur cette parcelle
    """
    id    = "A5_HISTORIQUE"
    poids = POIDS_ANALYSEURS["A5_HISTORIQUE"]

    async def analyser_parcelle(self, db, parcelle_id: str) -> Optional[SignalRisque]:
        try:
            r = await db.execute(_text("""
                SELECT
                    COUNT(DISTINCT ai.seq) FILTER (
                        WHERE ai.created_at >= NOW() - INTERVAL '90 days'
                    )                                                   AS modifs_90j,
                    COUNT(DISTINCT ai.seq)                              AS modifs_total,
                    COUNT(DISTINCT el.id) FILTER (WHERE el.actif = TRUE) AS verrous_actifs,
                    COUNT(DISTINCT el.id)                               AS verrous_total,
                    COUNT(DISTINCT wi.id) FILTER (WHERE wi.statut = 'bloque') AS wf_bloques,
                    COUNT(DISTINCT sl.simulation_id) FILTER (
                        WHERE sl.statut = 'ECHEC'
                        AND sl.created_at >= NOW() - INTERVAL '30 days'
                    )                                                   AS simulations_echec_30j
                FROM parcelles p
                LEFT JOIN audit_immutable ai
                    ON ai.entite_id = p.id
                    AND ai.entite_type IN ('parcelle','parcelles')
                LEFT JOIN entity_lock el ON el.entite_id = p.id
                LEFT JOIN workflow_instances wi ON wi.entite_id = p.id
                LEFT JOIN simulation_log sl ON p.id = ANY(sl.parcelle_ids)
                WHERE p.id = :pid::uuid
                GROUP BY p.id
            """), {"pid": parcelle_id})
            row = r.mappings().first()
        except Exception:
            return None

        if not row:
            return self._signal(0, "Aucun historique", {})

        m90  = int(row.get("modifs_90j") or 0)
        mt   = int(row.get("modifs_total") or 0)
        va   = int(row.get("verrous_actifs") or 0)
        vt   = int(row.get("verrous_total") or 0)
        wfb  = int(row.get("wf_bloques") or 0)
        sims = int(row.get("simulations_echec_30j") or 0)

        score = min(100.0,
                    m90 * 3     # modifications récentes fréquentes
                    + mt * 0.5
                    + va * 15   # verrou actif = mauvais signe
                    + vt * 2
                    + wfb * 20  # workflow bloqué = problème sérieux
                    + sims * 10 # simulations échouées
                )

        return self._signal(
            score,
            f"{m90} modif(s) en 90j, {va} verrou(s) actif(s), {wfb} WF bloqué(s)",
            {"modifs_90j": m90, "modifs_total": mt,
             "verrous_actifs": va, "verrous_total": vt,
             "wf_bloques": wfb, "simulations_echec_30j": sims},
        )

    async def analyser_zone(self, db, region: str) -> Optional[SignalRisque]:
        try:
            r = await db.execute(_text("""
                SELECT
                    COUNT(DISTINCT ai.seq) FILTER (
                        WHERE ai.created_at >= NOW() - INTERVAL '30 days'
                    )                       AS modifs_recentes,
                    COUNT(DISTINCT el.id) FILTER (WHERE el.actif = TRUE) AS verrous_actifs,
                    COUNT(DISTINCT wi.id) FILTER (WHERE wi.statut = 'bloque') AS wf_bloques
                FROM parcelles p
                LEFT JOIN audit_immutable ai ON ai.entite_id = p.id
                LEFT JOIN entity_lock el ON el.entite_id = p.id
                LEFT JOIN workflow_instances wi ON wi.entite_id = p.id
                WHERE p.region = :reg
            """), {"reg": region})
            row = r.mappings().first()
        except Exception:
            return None

        if not row:
            return self._signal(0, "Aucun historique régional", {})

        mr  = int(row.get("modifs_recentes") or 0)
        va  = int(row.get("verrous_actifs") or 0)
        wfb = int(row.get("wf_bloques") or 0)

        score = min(100.0, mr * 0.1 + va * 5 + wfb * 10)

        return self._signal(
            score,
            f"Région: {mr} modif(s)/30j, {wfb} WF bloqué(s)",
            {"modifs_recentes": mr, "verrous_actifs": va, "wf_bloques": wfb},
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# A6 — Analyseur Opérations Risquées
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class A6_OperationRisquee(AnalyseurBase):
    """
    Évalue le risque intrinsèque d'un type d'opération basé sur :
    - Taux historique d'échec des simulations pour ce type
    - Taux de refus des décisions moteur
    - Complexité opérationnelle (niveaux validation requis)
    """
    id    = "A6_OPERATION"
    poids = POIDS_ANALYSEURS["A6_OPERATION"]

    # Scores de risque de base par type d'opération (0-100)
    _RISQUE_BASE: Dict[str, float] = {
        "MUTATION":        40,
        "HYPOTHEQUE":      45,
        "MAINLEVEE":       15,
        "FUSION":          65,
        "SCISSION":        60,
        "LOTISSEMENT":     70,
        "EXPROPRIATION":   85,
        "SUCCESSION":      50,
        "IMMATRICULATION": 30,
        "RECTIFICATION":   20,
        "SERVITUDE":       25,
        "MISE_EN_GELE":    55,
    }

    async def analyser_operation(self, db, op_type: str) -> Optional[SignalRisque]:
        base   = self._RISQUE_BASE.get(op_type.upper(), 30)
        taux_e = 0.0
        taux_r = 0.0

        try:
            r = await db.execute(_text("""
                SELECT
                    COUNT(*) FILTER (WHERE statut = 'ECHEC')    AS echecs,
                    COUNT(*)                                      AS total
                FROM simulation_log
                WHERE operation_type = :op
                  AND created_at >= NOW() - INTERVAL '90 days'
            """), {"op": op_type.upper()})
            row = r.mappings().first()
            if row and int(row.get("total") or 0) > 0:
                taux_e = int(row.get("echecs") or 0) / int(row.get("total") or 1) * 100
        except Exception:
            pass

        try:
            r2 = await db.execute(_text("""
                SELECT
                    COUNT(*) FILTER (WHERE decision = 'REFUSE') AS refus,
                    COUNT(*)                                      AS total
                FROM decision_engine_log
                WHERE operation = :op
                  AND created_at >= NOW() - INTERVAL '90 days'
            """), {"op": op_type.upper()})
            row2 = r2.mappings().first()
            if row2 and int(row2.get("total") or 0) > 0:
                taux_r = int(row2.get("refus") or 0) / int(row2.get("total") or 1) * 100
        except Exception:
            pass

        # Score = base + ajustement taux d'échec historique
        score = min(100.0, base * 0.6 + taux_e * 0.25 + taux_r * 0.15)

        return self._signal(
            score,
            f"Opération {op_type}: risque base {base:.0f}, échecs {taux_e:.0f}%",
            {"risque_base": base, "taux_echec_simulation_pct": round(taux_e, 1),
             "taux_refus_decision_pct": round(taux_r, 1)},
        )

    async def analyser_parcelle(self, db, parcelle_id: str) -> Optional[SignalRisque]:
        """Risque opérationnel basé sur les types d'opérations passées sur la parcelle."""
        try:
            r = await db.execute(_text("""
                SELECT
                    COUNT(*) FILTER (WHERE sl.statut = 'ECHEC')   AS simulations_echec,
                    COUNT(*)                                        AS simulations_total,
                    ARRAY_AGG(DISTINCT sl.operation_type)          AS types_ops
                FROM simulation_log sl
                WHERE :pid::uuid = ANY(sl.parcelle_ids)
                  AND sl.created_at >= NOW() - INTERVAL '180 days'
            """), {"pid": parcelle_id})
            row = r.mappings().first()
        except Exception:
            return self._signal(0, "Aucune simulation antérieure", {})

        if not row:
            return self._signal(0, "Aucune simulation antérieure", {})

        echecs = int(row.get("simulations_echec") or 0)
        total  = int(row.get("simulations_total") or 0)
        ops    = list(row.get("types_ops") or [])

        if total == 0:
            return self._signal(0, "Aucune simulation antérieure", {})

        taux = echecs / total * 100
        # Pénalité pour opérations complexes
        penalite = sum(self._RISQUE_BASE.get(op, 30) for op in ops) / max(len(ops), 1) if ops else 0
        score    = min(100.0, taux * 0.6 + penalite * 0.4)

        return self._signal(
            score,
            f"{echecs}/{total} simulation(s) échouée(s) sur {len(ops)} type(s) ops",
            {"simulations_echec": echecs, "simulations_total": total,
             "types_ops": ops, "taux_echec_pct": round(taux, 1)},
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CALCULATEUR DE SCORE AGRÉGÉ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ScoreCalculator:
    """
    Agrège les signaux de tous les analyseurs en un score final pondéré.
    score_final = Σ(valeur_i × poids_i) / Σ(poids_i)
    """

    @staticmethod
    def calculer(signaux: List[SignalRisque]) -> float:
        if not signaux:
            return 0.0
        total_poids  = sum(s.poids for s in signaux if s.poids > 0)
        total_score  = sum(s.valeur * s.poids for s in signaux if s.poids > 0)
        if total_poids == 0:
            return 0.0
        return min(100.0, total_score / total_poids)

    @staticmethod
    def niveau(score: float) -> NiveauRisque:
        if score <= SEUIL_FAIBLE:
            return NiveauRisque.FAIBLE
        if score <= SEUIL_MOYEN:
            return NiveauRisque.MOYEN
        if score <= SEUIL_ELEVE:
            return NiveauRisque.ELEVE
        return NiveauRisque.CRITIQUE

    @staticmethod
    def facteurs_cles(signaux: List[SignalRisque], top_n: int = 3) -> List[str]:
        """Retourne les N signaux les plus contributeurs."""
        contrib = sorted(signaux, key=lambda s: s.valeur * s.poids, reverse=True)
        return [s.description for s in contrib[:top_n] if s.valeur > 5]

    @staticmethod
    def recommandations(niveau: NiveauRisque, signaux: List[SignalRisque]) -> List[str]:
        recos: List[str] = []
        if niveau == NiveauRisque.CRITIQUE:
            recos.append("Validation renforcée obligatoire (niveau +1) avant toute opération.")
            recos.append("Audit complet recommandé avant activation.")
        elif niveau == NiveauRisque.ELEVE:
            recos.append("Vérification manuelle conseillée avant validation.")

        for s in signaux:
            if s.valeur >= 50 and s.analyseur == "A1_CONFLIT_ZONE":
                recos.append("Résoudre les litiges et conflits géographiques actifs.")
            if s.valeur >= 60 and s.analyseur == "A2_MUTATION_FREQ":
                recos.append("Investigations sur la fréquence anormale de mutations.")
            if s.valeur >= 50 and s.analyseur == "A3_UTILISATEUR":
                recos.append("Profil utilisateur à surveiller — vérifier les habilitations.")
            if s.valeur >= 70 and s.analyseur == "A5_HISTORIQUE":
                recos.append("Libérer les verrous actifs et débloquer les workflows.")

        return recos[:5]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GÉNÉRATEUR D'ALERTES PRÉDICTIVES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RiskAlertGenerator:
    """Transforme un RiskScore en AlertePredictive si le niveau le justifie."""

    @staticmethod
    def generer(score: RiskScore) -> Optional[AlertePredictive]:
        if score.niveau == NiveauRisque.FAIBLE:
            return None  # Pas d'alerte pour les risques faibles

        import uuid as _uuid
        alerte_id = str(_uuid.uuid4())
        sha = hashlib.sha256(
            json.dumps({"id": alerte_id, "score": round(score.score, 1),
                        "entite": score.entite_id}, sort_keys=True).encode()
        ).hexdigest()

        titres = {
            NiveauRisque.CRITIQUE: f"⚠ Risque CRITIQUE détecté — {score.entite_type.value}",
            NiveauRisque.ELEVE:    f"Risque ÉLEVÉ — {score.entite_type.value}",
            NiveauRisque.MOYEN:    f"Risque MOYEN — {score.entite_type.value}",
        }

        messages = {
            NiveauRisque.CRITIQUE:
                f"Score de risque {score.score:.0f}/100 sur "
                f"{score.entite_type.value} {score.entite_id[:12]}. "
                "Intervention immédiate recommandée.",
            NiveauRisque.ELEVE:
                f"Score {score.score:.0f}/100. "
                "Vérification manuelle conseillée avant toute opération.",
            NiveauRisque.MOYEN:
                f"Score {score.score:.0f}/100. "
                "Surveiller l'évolution du risque.",
        }

        return AlertePredictive(
            id=alerte_id,
            niveau=score.niveau,
            entite_type=score.entite_type,
            entite_id=score.entite_id,
            titre=titres.get(score.niveau, "Risque détecté"),
            message=messages.get(score.niveau, ""),
            score=score.score,
            facteurs=score.facteurs_cles,
            recommandation=score.recommandations[0] if score.recommandations else "",
            sha256=sha,
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MOTEUR PRINCIPAL — RiskEngine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RiskEngine:
    """
    Point d'entrée unique du moteur d'analyse prédictive.

    Usage :
        score = await RiskEngine.analyser_parcelle(db, parcelle_id)
        score = await RiskEngine.analyser_zone(db, "NI-01")
        score = await RiskEngine.analyser_utilisateur(db, user_id)
        score = await RiskEngine.analyser_operation(db, "MUTATION")
        rapport = await RiskEngine.rapport_global(db)
    """

    _analyseurs: List[AnalyseurBase] = [
        A1_ConflitZone(),
        A2_MutationFreq(),
        A3_UtilisateurInstable(),
        A4_DensiteMutation(),
        A5_HistoriqueAnomalie(),
        A6_OperationRisquee(),
    ]

    # ── Analyse par entité ────────────────────────────────────────

    @classmethod
    async def analyser_parcelle(
        cls, db, parcelle_id: str,
        log_db=None, user_id: str = "SYSTEM"
    ) -> RiskScore:
        t0 = time.monotonic()
        signaux: List[SignalRisque] = []

        for a in cls._analyseurs:
            try:
                s = await a.analyser_parcelle(db, parcelle_id)
                if s:
                    signaux.append(s)
            except Exception as exc:
                _log.debug("Analyseur %s parcelle: %s", a.id, exc)

        return cls._finaliser(
            TypeEntite.PARCELLE, parcelle_id, signaux,
            t0, log_db or db, user_id
        )

    @classmethod
    async def analyser_zone(
        cls, db, region: str,
        log_db=None, user_id: str = "SYSTEM"
    ) -> RiskScore:
        t0 = time.monotonic()
        signaux: List[SignalRisque] = []

        analyseurs_zone = [a for a in cls._analyseurs
                           if hasattr(a, 'analyser_zone')]
        for a in analyseurs_zone:
            try:
                s = await a.analyser_zone(db, region)
                if s:
                    signaux.append(s)
            except Exception as exc:
                _log.debug("Analyseur %s zone: %s", a.id, exc)

        return cls._finaliser(
            TypeEntite.ZONE, region, signaux,
            t0, log_db or db, user_id
        )

    @classmethod
    async def analyser_utilisateur(
        cls, db, user_id: str,
        log_db=None, appelant_id: str = "SYSTEM"
    ) -> RiskScore:
        t0 = time.monotonic()
        signaux: List[SignalRisque] = []

        for a in cls._analyseurs:
            try:
                s = await a.analyser_utilisateur(db, user_id)
                if s:
                    signaux.append(s)
            except Exception as exc:
                _log.debug("Analyseur %s user: %s", a.id, exc)

        return cls._finaliser(
            TypeEntite.UTILISATEUR, user_id, signaux,
            t0, log_db or db, appelant_id
        )

    @classmethod
    async def analyser_operation(
        cls, db, op_type: str,
        log_db=None, user_id: str = "SYSTEM"
    ) -> RiskScore:
        t0 = time.monotonic()
        signaux: List[SignalRisque] = []

        for a in cls._analyseurs:
            try:
                s = await a.analyser_operation(db, op_type)
                if s:
                    signaux.append(s)
            except Exception as exc:
                _log.debug("Analyseur %s op: %s", a.id, exc)

        return cls._finaliser(
            TypeEntite.OPERATION, op_type, signaux,
            t0, log_db or db, user_id
        )

    # ── Rapport global ────────────────────────────────────────────

    @classmethod
    async def rapport_global(
        cls, db, limite_par_type: int = 10
    ) -> RapportRisque:
        """
        Calcule un rapport de risque global sur toutes les entités.
        Analyse les parcelles et zones les plus actives.
        """
        t0 = time.monotonic()
        rapport = RapportRisque()

        # Top parcelles à analyser (les plus actives récemment)
        try:
            r = await db.execute(_text("""
                SELECT DISTINCT ai.entite_id::TEXT AS id
                FROM audit_immutable ai
                WHERE ai.entite_type IN ('parcelle','parcelles')
                  AND ai.created_at >= NOW() - INTERVAL '30 days'
                ORDER BY ai.entite_id
                LIMIT :lim
            """), {"lim": limite_par_type})
            parcelle_ids = [row["id"] for row in r.mappings().all()]
        except Exception:
            parcelle_ids = []

        for pid in parcelle_ids:
            score = await cls.analyser_parcelle(db, pid)
            rapport.nb_parcelles_analysees += 1
            _incrementer_niveau(rapport, score.niveau)
            rapport.top_parcelles_risque.append({
                "id": pid, "score": round(score.score, 1),
                "niveau": score.niveau.value,
                "facteurs": score.facteurs_cles[:2],
            })

        rapport.top_parcelles_risque.sort(key=lambda x: -x["score"])

        # Zones (régions Niger)
        regions = ["NI-01", "NI-02", "NI-03", "NI-04",
                   "NI-05", "NI-06", "NI-07", "NI-08"]
        for reg in regions:
            score = await cls.analyser_zone(db, reg)
            rapport.nb_zones_analysees += 1
            rapport.top_zones_risque.append({
                "region": reg, "score": round(score.score, 1),
                "niveau": score.niveau.value,
                "facteurs": score.facteurs_cles[:2],
            })

        rapport.top_zones_risque.sort(key=lambda x: -x["score"])

        # Top utilisateurs actifs
        try:
            r = await db.execute(_text("""
                SELECT DISTINCT del.effectue_par::TEXT AS id
                FROM decision_engine_log del
                WHERE del.effectue_par IS NOT NULL
                  AND del.created_at >= NOW() - INTERVAL '30 days'
                LIMIT :lim
            """), {"lim": limite_par_type})
            user_ids = [row["id"] for row in r.mappings().all()]
        except Exception:
            user_ids = []

        for uid in user_ids:
            score = await cls.analyser_utilisateur(db, uid)
            rapport.nb_users_analyses += 1
            rapport.top_users_risque.append({
                "user_id": uid, "score": round(score.score, 1),
                "niveau": score.niveau.value,
                "facteurs": score.facteurs_cles[:2],
            })

        rapport.top_users_risque.sort(key=lambda x: -x["score"])

        rapport.nb_alertes_generees = (
            rapport.nb_critique + rapport.nb_eleve + rapport.nb_moyen
        )
        rapport.duree_ms = int((time.monotonic() - t0) * 1000)
        return rapport

    # ── Helpers ───────────────────────────────────────────────────

    @classmethod
    def _finaliser(
        cls,
        entite_type: TypeEntite,
        entite_id:   str,
        signaux:     List[SignalRisque],
        t0:          float,
        db,
        user_id:     str,
    ) -> RiskScore:
        score_val = ScoreCalculator.calculer(signaux)
        niveau    = ScoreCalculator.niveau(score_val)
        facteurs  = ScoreCalculator.facteurs_cles(signaux)
        recos     = ScoreCalculator.recommandations(niveau, signaux)

        sha = hashlib.sha256(
            json.dumps({
                "entite": entite_id,
                "score":  round(score_val, 1),
                "niveau": niveau.value,
            }, sort_keys=True).encode()
        ).hexdigest()

        score = RiskScore(
            entite_type=entite_type,
            entite_id=entite_id,
            score=score_val,
            niveau=niveau,
            signaux=signaux,
            facteurs_cles=facteurs,
            recommandations=recos,
            duree_ms=int((time.monotonic() - t0) * 1000),
            sha256=sha,
        )

        # Générer alerte si nécessaire (non bloquant)
        alerte = RiskAlertGenerator.generer(score)
        if alerte:
            asyncio.create_task(cls._persister_alerte(db, alerte, score, user_id))

        # Persister le score
        asyncio.create_task(cls._persister_score(db, score, user_id))

        return score

    @staticmethod
    async def _persister_score(db, score: RiskScore, user_id: str) -> None:
        if db is None:
            return
        try:
            await db.execute(_text("""
                INSERT INTO risk_score (
                    entite_type, entite_id, score, niveau,
                    facteurs_json, signaux_json, sha256, calcule_par
                ) VALUES (
                    :et, :eid, :sc, :niv,
                    :fac::jsonb, :sig::jsonb, :sha, :uid::uuid
                )
                ON CONFLICT (entite_type, entite_id)
                DO UPDATE SET
                    score         = EXCLUDED.score,
                    niveau        = EXCLUDED.niveau,
                    facteurs_json = EXCLUDED.facteurs_json,
                    signaux_json  = EXCLUDED.signaux_json,
                    sha256        = EXCLUDED.sha256,
                    calcule_par   = EXCLUDED.calcule_par,
                    updated_at    = NOW()
            """), {
                "et":  score.entite_type.value,
                "eid": score.entite_id,
                "sc":  round(score.score, 2),
                "niv": score.niveau.value,
                "fac": json.dumps(score.facteurs_cles),
                "sig": json.dumps([s.donnees for s in score.signaux]),
                "sha": score.sha256,
                "uid": user_id if user_id != "SYSTEM" else None,
            })
        except Exception as exc:
            _log.debug("risk_score persist: %s", exc)

    @staticmethod
    async def _persister_alerte(
        db, alerte: AlertePredictive, score: RiskScore, user_id: str
    ) -> None:
        if db is None:
            return
        try:
            await db.execute(_text("""
                INSERT INTO risk_alert (
                    alerte_id, niveau, entite_type, entite_id,
                    titre, message, score, facteurs_json,
                    recommandation, sha256
                ) VALUES (
                    :aid::uuid, :niv, :et, :eid,
                    :tit, :msg, :sc, :fac::jsonb,
                    :reco, :sha
                )
            """), {
                "aid":  alerte.id,
                "niv":  alerte.niveau.value,
                "et":   alerte.entite_type.value,
                "eid":  alerte.entite_id,
                "tit":  alerte.titre,
                "msg":  alerte.message,
                "sc":   round(alerte.score, 2),
                "fac":  json.dumps(alerte.facteurs),
                "reco": alerte.recommandation,
                "sha":  alerte.sha256,
            })
        except Exception as exc:
            _log.debug("risk_alert persist: %s", exc)


# Nécessite asyncio pour les fire-and-forget
import asyncio


def _incrementer_niveau(rapport: RapportRisque, niveau: NiveauRisque) -> None:
    if niveau == NiveauRisque.CRITIQUE: rapport.nb_critique += 1
    elif niveau == NiveauRisque.ELEVE:  rapport.nb_eleve    += 1
    elif niveau == NiveauRisque.MOYEN:  rapport.nb_moyen    += 1
    else:                               rapport.nb_faible   += 1
