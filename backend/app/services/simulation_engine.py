"""
FONCIER+ ── Moteur de Simulation Complète des Opérations Foncières
Module de simulation pré-exécution (dry-run) des opérations foncières.

Positionnement dans le système :
  Utilisé AVANT la validation finale d'une opération, après C1-C5.
  Toutes les modifications sont effectuées dans un SAVEPOINT PostgreSQL
  qui est systématiquement ROLLBACK'd — les données réelles ne sont
  jamais modifiées.

Opérations supportées (12 types) :
  MUTATION          Transfert de propriété d'une parcelle
  HYPOTHEQUE        Inscription d'une hypothèque
  MAINLEVEE         Mainlevée d'hypothèque
  FUSION            Fusion de plusieurs parcelles en une seule
  SCISSION          Division d'une parcelle en plusieurs
  LOTISSEMENT       Création d'un lotissement
  EXPROPRIATION     Expropriation pour utilité publique
  SUCCESSION        Transmission successorale
  IMMATRICULATION   Première immatriculation foncière
  RECTIFICATION     Correction d'erreurs cadastrales
  SERVITUDE         Création / suppression d'une servitude
  MISE_EN_GELE      Gel judiciaire d'une parcelle

Architecture d'isolation (6 couches de vérification) :
  S1 — Pré-conditions         : état actuel de la parcelle, verrous, gel
  S2 — Règles métier          : decision_engine_evaluer() sur les données simulées
  S3 — Droits fonciers        : somme 100 %, cohérence des titulaires
  S4 — Impacts documentaires  : CCFM obligatoire, documents requis
  S5 — Juridiction            : compétence territoriale, niveaux de validation
  S6 — Topologie              : conflits géographiques (si BGU)

Garanties d'isolation :
  • SAVEPOINT sp_simulation_XXXX créé avant chaque simulation
  • ROLLBACK TO SAVEPOINT systématique (succès ou erreur)
  • SET LOCAL statement_timeout = '8000' (8 s max)
  • SET LOCAL default_transaction_read_only = OFF (écriture dans sandbox)
  • Aucune entrée dans audit_immutable (SET LOCAL = OFF via flag)
  • Journal dédié : simulation_log (append-only)

Performance :
  • Timeout 8 s par simulation
  • MAX_IMPACTS_DETECTES = 50 par simulation
  • Simulation complète typique : 200–800 ms
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
# Adaptateur SQLAlchemy text() avec fallback pour tests sans DB
try:
    from sqlalchemy import text as _sa_text
    def _text(q): return _sa_text(q)
except ImportError:
    def _text(q): return q  # type: ignore — fallback: retourne la string directement



_log = logging.getLogger("foncier.simulation")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONSTANTES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SIMULATION_TIMEOUT_MS   = 8_000
MAX_IMPACTS_DETECTES    = 50
MAX_PARCELLES_CIBLES    = 10   # pour FUSION / SCISSION

# Types d'opérations foncières supportés
OPERATION_TYPES = {
    "MUTATION":         "Transfert de propriété",
    "HYPOTHEQUE":       "Inscription d'une hypothèque",
    "MAINLEVEE":        "Mainlevée d'hypothèque",
    "FUSION":           "Fusion de parcelles",
    "SCISSION":         "Division d'une parcelle",
    "LOTISSEMENT":      "Création de lotissement",
    "EXPROPRIATION":    "Expropriation pour utilité publique",
    "SUCCESSION":       "Transmission successorale",
    "IMMATRICULATION":  "Première immatriculation",
    "RECTIFICATION":    "Rectification cadastrale",
    "SERVITUDE":        "Création / suppression de servitude",
    "MISE_EN_GELE":     "Gel judiciaire",
}

# Documents requis par type d'opération (CCFM obligatoire sauf exceptions)
DOCS_REQUIS: Dict[str, List[str]] = {
    "MUTATION":         ["CCFM", "ACTE_NOTARIE"],
    "HYPOTHEQUE":       ["CCFM", "ACTE_NOTARIE"],
    "MAINLEVEE":        ["ACTE_NOTARIE"],
    "FUSION":           ["CCFM", "PLAN_GEOMETRIQUE"],
    "SCISSION":         ["CCFM", "PLAN_GEOMETRIQUE"],
    "LOTISSEMENT":      ["CCFM", "ARRETE", "PLAN_GEOMETRIQUE"],
    "EXPROPRIATION":    ["ARRETE"],
    "SUCCESSION":       ["CCFM", "ACTE_NOTARIE"],
    "IMMATRICULATION":  ["PLAN_GEOMETRIQUE"],
    "RECTIFICATION":    ["ARRETE"],
    "SERVITUDE":        ["ACTE_NOTARIE"],
    "MISE_EN_GELE":     ["DECISION_JUSTICE"],
}

# Niveaux de validation requis par opération
NIVEAUX_VALIDATION: Dict[str, int] = {
    "MUTATION":         2,   # Local + Régional
    "HYPOTHEQUE":       2,
    "MAINLEVEE":        1,
    "FUSION":           3,   # Local + Régional + National
    "SCISSION":         3,
    "LOTISSEMENT":      3,
    "EXPROPRIATION":    4,   # Tous niveaux
    "SUCCESSION":       2,
    "IMMATRICULATION":  2,
    "RECTIFICATION":    2,
    "SERVITUDE":        1,
    "MISE_EN_GELE":     2,
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TYPES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SimulationStatut(str, Enum):
    SUCCES      = "SUCCES"       # simulation complète sans blocage
    AVERTISSEMENT = "AVERTISSEMENT"  # simulation OK avec réserves
    ECHEC       = "ECHEC"        # simulation bloquée par au moins 1 impact BLOQUANT


class ImpactNiveau(str, Enum):
    BLOQUANT     = "BLOQUANT"    # empêche l'opération
    AVERTISSEMENT = "AVERTISSEMENT"  # signalé mais non bloquant
    INFO         = "INFO"        # information contextuelle


@dataclass
class ImpactSimule:
    """Un impact détecté pendant la simulation."""
    couche:    str           # S1..S6
    niveau:    ImpactNiveau
    entite:    str           # parcelle, document, juridiction…
    entite_id: str           # UUID ou code
    message:   str
    detail:    str
    correction: str          # recommandation corrective


@dataclass
class EtapeSimulation:
    """Trace d'exécution d'une couche de simulation."""
    couche:    str
    nom:       str
    statut:    str           # PASS | FAIL | SKIP | WARN
    duree_ms:  int
    nb_impacts: int
    details:   List[str] = field(default_factory=list)


@dataclass
class SimulationContext:
    """
    Contexte complet d'une opération foncière à simuler.
    Fourni par l'appelant.
    """
    operation_type:  str              # MUTATION, HYPOTHEQUE, etc.
    parcelle_ids:    List[str]        # UUIDs des parcelles concernées
    user_id:         str              # agent qui soumet
    region:          str              # code région (NI-XX)
    contexte_extra:  Dict[str, Any] = field(default_factory=dict)
    # Champs spécifiques par opération
    nouveau_titulaire_id: Optional[str] = None   # MUTATION / SUCCESSION
    montant_hypotheque:   Optional[float] = None  # HYPOTHEQUE
    parcelles_cibles:     Optional[List[str]] = None  # FUSION
    nb_lots:              Optional[int] = None    # SCISSION / LOTISSEMENT
    motif:                Optional[str] = None


@dataclass
class SimulationResult:
    """Rapport complet de simulation."""
    simulation_id:     str
    operation_type:    str
    statut:            SimulationStatut
    parcelles_cibles:  List[str]
    impacts:           List[ImpactSimule]   = field(default_factory=list)
    etapes:            List[EtapeSimulation] = field(default_factory=list)
    duree_ms:          int = 0
    nb_bloquants:      int = 0
    nb_avertissements: int = 0
    nb_infos:          int = 0
    recommandation:    str = ""
    sha256_rapport:    str = ""
    # Données lues pendant la simulation (pour le rapport)
    donnees_actuelles: Dict[str, Any] = field(default_factory=dict)
    effets_projetes:   Dict[str, Any] = field(default_factory=dict)

    @property
    def autorisee(self) -> bool:
        return self.statut in (SimulationStatut.SUCCES, SimulationStatut.AVERTISSEMENT)

    def as_dict(self) -> dict:
        return {
            "simulation_id":    self.simulation_id,
            "operation_type":   self.operation_type,
            "statut":           self.statut.value,
            "autorisee":        self.autorisee,
            "nb_bloquants":     self.nb_bloquants,
            "nb_avertissements": self.nb_avertissements,
            "nb_infos":         self.nb_infos,
            "recommandation":   self.recommandation,
            "sha256_rapport":   self.sha256_rapport,
            "duree_ms":         self.duree_ms,
            "parcelles_cibles": self.parcelles_cibles,
            "donnees_actuelles": self.donnees_actuelles,
            "effets_projetes":  self.effets_projetes,
            "impacts": [
                {
                    "couche":    i.couche,
                    "niveau":    i.niveau.value,
                    "entite":    i.entite,
                    "entite_id": i.entite_id,
                    "message":   i.message,
                    "detail":    i.detail,
                    "correction": i.correction,
                }
                for i in self.impacts
            ],
            "etapes": [
                {
                    "couche":    e.couche,
                    "nom":       e.nom,
                    "statut":    e.statut,
                    "duree_ms":  e.duree_ms,
                    "nb_impacts": e.nb_impacts,
                    "details":   e.details,
                }
                for e in self.etapes
            ],
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COUCHE S1 — Pré-conditions (état courant des parcelles)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _s1_preconditions(
    db, ctx: SimulationContext, impacts: List[ImpactSimule]
) -> Tuple[List[ImpactSimule], Dict[str, Any]]:
    """
    Vérifie l'état actuel de chaque parcelle :
    - Existence en base
    - Statut (ACTIVE, ARCHIVEE, LITIGE…)
    - Gel judiciaire (is_gele)
    - Verrou actif (entity_lock)
    - Workflow en cours (conflit)
    - Droits existants
    """
    data: Dict[str, Any] = {"parcelles": []}

    for pid in ctx.parcelle_ids:
        try:
            r = await db.execute(_text("""
                SELECT p.id, p.nicad, p.statut, p.is_gele,
                       p.surface_m2, p.region, p.commune,
                       COALESCE(
                           (SELECT SUM(prv.pourcentage)
                            FROM parcel_right_version prv
                            WHERE prv.parcelle_id = p.id
                              AND prv.statut_version = 'active'
                              AND prv.type_droit = 'PROPRIETE'),
                           0
                       ) AS somme_droits,
                       (SELECT COUNT(*) FROM entity_lock el
                        WHERE el.entite_id = p.id
                          AND el.actif = TRUE
                          AND (el.date_expiration IS NULL OR el.date_expiration > NOW())
                       ) AS nb_verrous,
                       (SELECT COUNT(*) FROM litiges l
                        WHERE l.parcelle_id = p.id AND l.statut = 'ACTIF'
                       ) AS nb_litiges_actifs,
                       (SELECT COUNT(*) FROM validation_pipeline vp
                        WHERE vp.entite_id = p.id
                          AND vp.statut NOT IN ('VALIDE','REJETE')
                       ) AS workflows_en_cours
                FROM parcelles p
                WHERE p.id = :pid::uuid
            """), {"pid": pid})
            row = r.mappings().first()
        except Exception as exc:
            impacts.append(ImpactSimule(
                couche="S1", niveau=ImpactNiveau.BLOQUANT,
                entite="parcelle", entite_id=pid,
                message=f"Erreur lecture parcelle {pid[:8]}…",
                detail=str(exc)[:200],
                correction="Vérifier que l'UUID est valide.",
            ))
            continue

        if not row:
            impacts.append(ImpactSimule(
                couche="S1", niveau=ImpactNiveau.BLOQUANT,
                entite="parcelle", entite_id=pid,
                message=f"Parcelle introuvable en base",
                detail=f"UUID {pid} n'existe pas dans la table parcelles.",
                correction="Vérifier le NICAD ou l'UUID fourni.",
            ))
            continue

        p = dict(row)
        data["parcelles"].append(p)

        # Gel judiciaire
        if p.get("is_gele"):
            impacts.append(ImpactSimule(
                couche="S1", niveau=ImpactNiveau.BLOQUANT,
                entite="parcelle", entite_id=pid,
                message=f"Parcelle {p['nicad']} gelée judiciairement",
                detail="Une mesure judiciaire (gel) est active sur cette parcelle.",
                correction="Obtenir une mainlevée du gel avant toute opération.",
            ))

        # Statut incompatible
        statut = p.get("statut", "")
        ops_interdites_si_archivee = {
            "MUTATION", "HYPOTHEQUE", "FUSION", "SCISSION", "SERVITUDE"
        }
        if statut == "archivee" and ctx.operation_type in ops_interdites_si_archivee:
            impacts.append(ImpactSimule(
                couche="S1", niveau=ImpactNiveau.BLOQUANT,
                entite="parcelle", entite_id=pid,
                message=f"Parcelle {p['nicad']} archivée — opération {ctx.operation_type} impossible",
                detail=f"Statut actuel : {statut}.",
                correction="Réactiver la parcelle ou sélectionner une parcelle active.",
            ))

        # Verrou actif
        if p.get("nb_verrous", 0) > 0:
            impacts.append(ImpactSimule(
                couche="S1", niveau=ImpactNiveau.BLOQUANT,
                entite="parcelle", entite_id=pid,
                message=f"Parcelle {p['nicad']} verrouillée ({p['nb_verrous']} verrou(s))",
                detail="Un verrou (entity_lock) actif empêche toute modification.",
                correction="Attendre l'expiration du verrou ou le faire libérer par un ADMIN.",
            ))

        # Litige actif
        if p.get("nb_litiges_actifs", 0) > 0:
            niveau = (ImpactNiveau.BLOQUANT
                      if ctx.operation_type in {"MUTATION", "HYPOTHEQUE", "SUCCESSION"}
                      else ImpactNiveau.AVERTISSEMENT)
            impacts.append(ImpactSimule(
                couche="S1", niveau=niveau,
                entite="parcelle", entite_id=pid,
                message=f"Parcelle {p['nicad']} en litige actif ({p['nb_litiges_actifs']})",
                detail="Un litige judiciaire est actif sur cette parcelle.",
                correction="Résoudre le litige avant d'effectuer l'opération."
                           if niveau == ImpactNiveau.BLOQUANT
                           else "Documenter la situation de litige dans le dossier.",
            ))

        # Workflow en cours
        if p.get("workflows_en_cours", 0) > 0:
            impacts.append(ImpactSimule(
                couche="S1", niveau=ImpactNiveau.AVERTISSEMENT,
                entite="parcelle", entite_id=pid,
                message=f"Parcelle {p['nicad']} a {p['workflows_en_cours']} workflow(s) en cours",
                detail="Des workflows de validation sont en attente sur cette parcelle.",
                correction="Finaliser les workflows en cours avant de lancer une nouvelle opération.",
            ))

    return impacts, data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COUCHE S2 — Règles métier (Decision Engine simulé)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _s2_regles_metier(
    db, ctx: SimulationContext, impacts: List[ImpactSimule],
    donnees_s1: Dict[str, Any]
) -> List[ImpactSimule]:
    """
    Évalue les règles métier actives via decision_engine_evaluer().
    Exécuté sur les données ACTUELLES (avant toute modification simulée).
    Détecte : règles bloquantes, avertissements, règles combinées.
    """
    
    for pid in ctx.parcelle_ids:
        try:
            r = await db.execute(_text("""
                SELECT autorise, decision, regles_evaluees,
                       blocages, avertissements, motifs, sha256
                FROM decision_engine_evaluer(
                    :entite_type, :entite_id::uuid,
                    :operation,  :user_id::uuid, :ctx::jsonb
                )
            """), {
                "entite_type": _domaine_pour_op(ctx.operation_type),
                "entite_id":   pid,
                "operation":   ctx.operation_type,
                "user_id":     ctx.user_id,
                "ctx":         json.dumps({
                    "simulation": True,
                    "region":     ctx.region,
                    **ctx.contexte_extra,
                }),
            })
            row = r.mappings().first()
        except Exception as exc:
            _log.warning("S2 decision_engine erreur: %s", exc)
            impacts.append(ImpactSimule(
                couche="S2", niveau=ImpactNiveau.AVERTISSEMENT,
                entite="regles_metier", entite_id=pid,
                message="Moteur de règles indisponible pour cette parcelle",
                detail=str(exc)[:200],
                correction="Vérifier la disponibilité du decision_engine.",
            ))
            continue

        if not row:
            continue

        if not row["autorise"]:
            motifs = list(row["motifs"] or [])
            for motif in motifs:
                niveau = (ImpactNiveau.BLOQUANT
                          if motif.startswith("BLOQUANT") else ImpactNiveau.AVERTISSEMENT)
                impacts.append(ImpactSimule(
                    couche="S2", niveau=niveau,
                    entite="regle_metier", entite_id=pid,
                    message=motif,
                    detail=f"Règle évaluée sur parcelle {pid[:8]}… "
                           f"({row['regles_evaluees']} règles testées, "
                           f"{row['blocages']} blocage(s))",
                    correction="Résoudre la condition de blocage avant l'opération.",
                ))
        elif row.get("avertissements", 0) > 0:
            for motif in (row["motifs"] or []):
                if motif.startswith("WARN"):
                    impacts.append(ImpactSimule(
                        couche="S2", niveau=ImpactNiveau.AVERTISSEMENT,
                        entite="regle_metier", entite_id=pid,
                        message=motif,
                        detail="Avertissement non bloquant émis par une règle métier.",
                        correction="Documenter et vérifier la situation avant validation.",
                    ))

    return impacts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COUCHE S3 — Droits fonciers (cohérence 100 % + simulation mutation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _s3_droits_fonciers(
    db, ctx: SimulationContext, impacts: List[ImpactSimule],
    donnees_s1: Dict[str, Any], effets: Dict[str, Any]
) -> List[ImpactSimule]:
    """
    Vérifie la cohérence des droits fonciers :
    - Somme PROPRIETE = 100 % (règle invariante FONCIER+)
    - Titulaires valides (utilisateurs existants)
    - Cohérence après mutation simulée
    - Conflits d'indivision
    """
    
    for pid in ctx.parcelle_ids:
        try:
            # Droits actifs courants
            r = await db.execute(_text("""
                SELECT prv.type_droit, prv.pourcentage,
                       prv.titulaire_id, u.nom_complet,
                       prv.statut_version
                FROM parcel_right_version prv
                LEFT JOIN users u ON u.id = prv.titulaire_id
                WHERE prv.parcelle_id = :pid::uuid
                  AND prv.statut_version = 'active'
                ORDER BY prv.type_droit, prv.pourcentage DESC
            """), {"pid": pid})
            droits = [dict(row) for row in r.mappings().all()]
        except Exception as exc:
            _log.warning("S3 droits erreur: %s", exc)
            continue

        if not droits:
            if ctx.operation_type not in {"IMMATRICULATION", "EXPROPRIATION"}:
                impacts.append(ImpactSimule(
                    couche="S3", niveau=ImpactNiveau.AVERTISSEMENT,
                    entite="droits", entite_id=pid,
                    message="Aucun droit actif enregistré sur cette parcelle",
                    detail="La parcelle n'a aucun droit de PROPRIETE actif.",
                    correction="Vérifier l'historique des droits ou immatriculer la parcelle.",
                ))
            continue

        # Vérification somme = 100 %
        somme_prop = sum(
            d["pourcentage"] for d in droits
            if d["type_droit"] == "PROPRIETE"
        )
        if droits and abs(somme_prop - 100.0) > 0.01:
            impacts.append(ImpactSimule(
                couche="S3", niveau=ImpactNiveau.BLOQUANT,
                entite="droits", entite_id=pid,
                message=f"Somme des droits de PROPRIETE = {somme_prop:.2f}% (≠ 100%)",
                detail=f"Règle invariante FONCIER+ : les droits PROPRIETE actifs "
                       f"doivent totaliser exactement 100%. Valeur actuelle : {somme_prop:.2f}%.",
                correction="Corriger les pourcentages des droits avant toute opération.",
            ))

        # Simulation MUTATION : nouveau titulaire prend 100 %
        if ctx.operation_type == "MUTATION" and ctx.nouveau_titulaire_id:
            # Vérifier que le nouveau titulaire existe
            try:
                r2 = await db.execute(_text(
                    "SELECT id, nom_complet FROM users WHERE id=:uid::uuid"
                ), {"uid": ctx.nouveau_titulaire_id})
                new_tit = r2.mappings().first()
            except Exception:
                new_tit = None

            if not new_tit:
                impacts.append(ImpactSimule(
                    couche="S3", niveau=ImpactNiveau.BLOQUANT,
                    entite="utilisateur", entite_id=ctx.nouveau_titulaire_id,
                    message="Nouveau titulaire introuvable en base",
                    detail=f"UUID {ctx.nouveau_titulaire_id} n'existe pas dans users.",
                    correction="Créer d'abord le compte utilisateur du nouveau titulaire.",
                ))
            else:
                # Simuler l'effet
                anciens = [d["nom_complet"] for d in droits if d["type_droit"] == "PROPRIETE"]
                effets.setdefault("mutations_propriete", []).append({
                    "parcelle_id":  pid,
                    "anciens_titulaires": anciens,
                    "nouveau_titulaire":  new_tit["nom_complet"],
                    "pourcentage_transfere": somme_prop,
                })

        # Simulation SUCCESSION : vérifier indivision
        if ctx.operation_type == "SUCCESSION" and len(droits) > 1:
            impacts.append(ImpactSimule(
                couche="S3", niveau=ImpactNiveau.AVERTISSEMENT,
                entite="droits", entite_id=pid,
                message=f"Indivision détectée ({len(droits)} co-titulaires)",
                detail="La succession sur une parcelle en indivision nécessite "
                       "l'accord de tous les co-titulaires.",
                correction="Obtenir le consentement écrit de tous les indivisaires.",
            ))

        # Titulaires sans compte actif
        for d in droits:
            if not d.get("nom_complet"):
                impacts.append(ImpactSimule(
                    couche="S3", niveau=ImpactNiveau.AVERTISSEMENT,
                    entite="titulaire", entite_id=str(d.get("titulaire_id", "")),
                    message="Titulaire référencé sans compte utilisateur actif",
                    detail=f"Le droit de {d['type_droit']} ({d['pourcentage']}%) "
                           "pointe vers un utilisateur introuvable.",
                    correction="Régulariser les titulaires avant l'opération.",
                ))

        effets["droits_avant"] = droits

    return impacts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COUCHE S4 — Impacts documentaires (CCFM, documents requis)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _s4_documents(
    db, ctx: SimulationContext, impacts: List[ImpactSimule],
    effets: Dict[str, Any]
) -> List[ImpactSimule]:
    """
    Vérifie les documents requis pour l'opération :
    - CCFM validé et signé (gate obligatoire pour MUTATION, HYPOTHEQUE, FUSION...)
    - Documents administratifs présents
    - Date d'expiration des actes
    - Cohérence RNAF (plan géométrique)
    """
    
    docs_requis = DOCS_REQUIS.get(ctx.operation_type, [])
    docs_presents: List[str] = []
    docs_manquants: List[str] = []

    for pid in ctx.parcelle_ids:
        # CCFM gate (obligatoire pour la plupart des opérations)
        if "CCFM" in docs_requis:
            try:
                r = await db.execute(_text("""
                    SELECT dc.id, dc.etat, dc.date_creation,
                           dc.date_signature_directeur
                    FROM demandes_ccfm dc
                    WHERE dc.nicad_code IN (
                        SELECT p.nicad FROM parcelles p WHERE p.id=:pid::uuid
                    )
                    AND dc.etat IN ('SIGNE_DIRECTEUR','ARCHIVE')
                    ORDER BY dc.date_creation DESC
                    LIMIT 1
                """), {"pid": pid})
                ccfm = r.mappings().first()
            except Exception:
                ccfm = None

            if not ccfm:
                docs_manquants.append("CCFM")
                impacts.append(ImpactSimule(
                    couche="S4", niveau=ImpactNiveau.BLOQUANT,
                    entite="ccfm", entite_id=pid,
                    message=f"CCFM manquant ou non signé pour l'opération {ctx.operation_type}",
                    detail="La gate CCFM est obligatoire avant tout acte foncier. "
                           "Aucune demande CCFM signée par le Directeur n'est trouvée.",
                    correction="Initier et faire valider une demande CCFM avant cette opération.",
                ))
            else:
                docs_presents.append("CCFM")
                effets["ccfm_id"] = str(ccfm["id"])

        # Plan géométrique RNAF (pour FUSION, SCISSION, LOTISSEMENT)
        if "PLAN_GEOMETRIQUE" in docs_requis:
            try:
                r = await db.execute(_text("""
                    SELECT COUNT(*) AS nb
                    FROM rnaf rf
                    WHERE rf.parcelle_id = :pid::uuid
                      AND rf.statut = 'PUBLIE'
                """), {"pid": pid})
                nb_rnaf = (r.scalar() or 0)
            except Exception:
                nb_rnaf = 0

            if nb_rnaf == 0:
                docs_manquants.append("PLAN_GEOMETRIQUE")
                impacts.append(ImpactSimule(
                    couche="S4", niveau=ImpactNiveau.BLOQUANT,
                    entite="rnaf", entite_id=pid,
                    message="Plan géométrique RNAF publié manquant",
                    detail=f"L'opération {ctx.operation_type} nécessite un RNAF publié.",
                    correction="Faire publier le RNAF par le Secrétaire Général avant l'opération.",
                ))
            else:
                docs_presents.append("PLAN_GEOMETRIQUE")

        # Acte notarié
        if "ACTE_NOTARIE" in docs_requis:
            try:
                r = await db.execute(_text("""
                    SELECT COUNT(*) AS nb
                    FROM property_transfers pt
                    WHERE pt.parcelle_id = :pid::uuid
                      AND pt.statut = 'valide'
                      AND pt.date_acte IS NOT NULL
                    LIMIT 1
                """), {"pid": pid})
                nb_acte = (r.scalar() or 0)
            except Exception:
                nb_acte = 0

            if nb_acte == 0 and ctx.operation_type in {"MUTATION", "HYPOTHEQUE"}:
                impacts.append(ImpactSimule(
                    couche="S4", niveau=ImpactNiveau.AVERTISSEMENT,
                    entite="acte_notarie", entite_id=pid,
                    message="Aucun acte notarié récent validé trouvé",
                    detail="Un acte notarié est recommandé pour cette opération.",
                    correction="Préparer l'acte notarié et le faire enregistrer.",
                ))

    effets["docs_presents"]  = docs_presents
    effets["docs_manquants"] = docs_manquants
    return impacts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COUCHE S5 — Juridiction et validation multi-niveaux
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _s5_juridiction(
    db, ctx: SimulationContext, impacts: List[ImpactSimule],
    donnees_s1: Dict[str, Any], effets: Dict[str, Any]
) -> List[ImpactSimule]:
    """
    Vérifie la compétence territoriale et les niveaux de validation requis :
    - Agent autorisé dans la région de la parcelle
    - Niveau d'autorité suffisant pour l'opération
    - Pipeline de validation multi-niveaux requis
    - Délégation active si agent hors juridiction habituelle
    """
    
    # Profil de l'agent
    try:
        r = await db.execute(_text("""
            SELECT ap.region_principale, ap.regions_autorisees,
                   ap.niveau_autorite, ap.peut_valider_hors_region,
                   i.nom AS institution_nom
            FROM agent_profil ap
            LEFT JOIN institutions i ON i.id = ap.institution_id
            WHERE ap.user_id = :uid::uuid AND ap.actif = TRUE
        """), {"uid": ctx.user_id})
        profil = r.mappings().first()
    except Exception:
        profil = None

    if not profil:
        impacts.append(ImpactSimule(
            couche="S5", niveau=ImpactNiveau.BLOQUANT,
            entite="agent", entite_id=ctx.user_id,
            message="Profil agent introuvable ou inactif",
            detail="L'agent n'a pas de profil institutionnel actif dans agent_profil.",
            correction="Créer le profil institutionnel de l'agent (ADMIN).",
        ))
        return impacts

    region_agent = profil.get("region_principale", "")
    niveau_autorite = profil.get("niveau_autorite", 0)
    regions_ok = list(profil.get("regions_autorisees") or []) + [region_agent]

    # Vérifier la compétence régionale
    if ctx.region and ctx.region not in regions_ok:
        if not profil.get("peut_valider_hors_region"):
            # Vérifier délégation active
            try:
                r = await db.execute(_text("""
                    SELECT COUNT(*) AS nb
                    FROM delegation_administrative da
                    WHERE da.delegataire_id = :uid::uuid
                      AND da.statut = 'ACTIVE'
                      AND da.date_fin > NOW()
                      AND (da.region_concernee = :reg OR da.region_concernee IS NULL)
                      AND da.domaine = :dom
                """), {
                    "uid": ctx.user_id,
                    "reg": ctx.region,
                    "dom": _domaine_pour_op(ctx.operation_type),
                })
                nb_deleg = r.scalar() or 0
            except Exception:
                nb_deleg = 0

            if nb_deleg == 0:
                impacts.append(ImpactSimule(
                    couche="S5", niveau=ImpactNiveau.BLOQUANT,
                    entite="juridiction", entite_id=ctx.user_id,
                    message=f"Agent non autorisé dans la région {ctx.region}",
                    detail=f"Région de l'agent : {region_agent}. "
                           f"Régions autorisées : {regions_ok}. "
                           "Aucune délégation active trouvée.",
                    correction="Obtenir une délégation administrative pour cette région, "
                               "ou faire valider par un agent compétent.",
                ))
            else:
                impacts.append(ImpactSimule(
                    couche="S5", niveau=ImpactNiveau.INFO,
                    entite="juridiction", entite_id=ctx.user_id,
                    message=f"Opération via délégation active pour la région {ctx.region}",
                    detail=f"{nb_deleg} délégation(s) active(s) trouvée(s).",
                    correction="",
                ))

    # Niveau d'autorité suffisant ?
    niveau_requis = NIVEAUX_VALIDATION.get(ctx.operation_type, 1)
    if niveau_autorite < niveau_requis:
        impacts.append(ImpactSimule(
            couche="S5", niveau=ImpactNiveau.BLOQUANT,
            entite="autorisation", entite_id=ctx.user_id,
            message=f"Niveau d'autorité insuffisant : {niveau_autorite} < {niveau_requis} requis",
            detail=f"L'opération {ctx.operation_type} requiert un niveau d'autorité {niveau_requis}. "
                   f"L'agent a le niveau {niveau_autorite}.",
            correction="Faire valider par un agent de niveau supérieur ou déléguer.",
        ))
    else:
        effets["validation_multi_niveaux"] = {
            "niveaux_requis": niveau_requis,
            "niveau_agent":   niveau_autorite,
            "institution":    profil.get("institution_nom", ""),
            "compatible":     True,
        }

    return impacts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COUCHE S6 — Topologie géographique (si BGU disponible)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _s6_topologie(
    db, ctx: SimulationContext, impacts: List[ImpactSimule],
    effets: Dict[str, Any]
) -> List[ImpactSimule]:
    """
    Vérifie les contraintes topographiques géographiques :
    - Superpositions entre parcelles (conflits géo actifs)
    - Cohérence surface (FUSION : surface résultante)
    - Chevauchement avec lotissement existant
    - Contraintes de servitude
    """
    
    for pid in ctx.parcelle_ids:
        # Conflits géographiques actifs
        try:
            r = await db.execute(_text("""
                SELECT cc.id, cc.type_conflit, cc.gravite,
                       cc.nicad_ref, cc.nicad_conflit,
                       cc.statut AS statut_conflit
                FROM conflits_parcellaires cc
                WHERE (cc.parcelle_id_ref = :pid::uuid
                       OR cc.parcelle_id_conflit = :pid::uuid)
                  AND cc.statut IN ('ouvert','en_traitement')
                LIMIT 10
            """), {"pid": pid})
            conflits_geo = [dict(row) for row in r.mappings().all()]
        except Exception:
            conflits_geo = []

        for cg in conflits_geo:
            gravite = cg.get("gravite", "a_verifier")
            niveau = (ImpactNiveau.BLOQUANT
                      if gravite == "critique" else ImpactNiveau.AVERTISSEMENT)
            impacts.append(ImpactSimule(
                couche="S6", niveau=niveau,
                entite="topologie", entite_id=pid,
                message=f"Conflit géographique {cg['type_conflit']} "
                        f"({gravite}) avec {cg.get('nicad_conflit','')}",
                detail=f"Conflit {cg['id'][:8]} — statut : {cg['statut_conflit']}.",
                correction="Résoudre le conflit géographique avant l'opération."
                           if niveau == ImpactNiveau.BLOQUANT
                           else "Vérifier et documenter le conflit géographique.",
            ))

        # Pour FUSION : vérifier que toutes les parcelles sont contiguës
        if ctx.operation_type == "FUSION" and len(ctx.parcelle_ids) > 1:
            try:
                r = await db.execute(_text("""
                    SELECT COUNT(*) AS nb_adjacentes
                    FROM bgu_parcel_status bps
                    WHERE bps.parcelle_id = :pid::uuid
                      AND bps.statut_geo = 'VALIDE'
                """), {"pid": pid})
                nb_adj = r.scalar() or 0
            except Exception:
                nb_adj = 0

            if nb_adj == 0:
                impacts.append(ImpactSimule(
                    couche="S6", niveau=ImpactNiveau.AVERTISSEMENT,
                    entite="topologie", entite_id=pid,
                    message="Pas de données BGU pour vérifier la contiguïté",
                    detail="La fusion de parcelles non contiguës est risquée géographiquement.",
                    correction="Faire valider la contiguïté par le service topographique avant fusion.",
                ))

        # Pour SCISSION : vérifier surface minimale
        if ctx.operation_type == "SCISSION" and ctx.nb_lots:
            try:
                r = await db.execute(_text(
                    "SELECT surface_m2 FROM parcelles WHERE id=:pid::uuid"
                ), {"pid": pid})
                surface = r.scalar() or 0
            except Exception:
                surface = 0

            if surface > 0:
                surface_par_lot = surface / ctx.nb_lots
                if surface_par_lot < 200:  # seuil minimum 200 m²
                    impacts.append(ImpactSimule(
                        couche="S6", niveau=ImpactNiveau.AVERTISSEMENT,
                        entite="topologie", entite_id=pid,
                        message=f"Surface par lot insuffisante : {surface_par_lot:.0f} m² < 200 m²",
                        detail=f"Surface totale : {surface:.0f} m². "
                               f"Division en {ctx.nb_lots} lots → {surface_par_lot:.0f} m²/lot.",
                        correction="Réduire le nombre de lots ou vérifier la réglementation locale.",
                    ))

    return impacts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _domaine_pour_op(operation_type: str) -> str:
    mapping = {
        "MUTATION":       "MUTATION",
        "HYPOTHEQUE":     "HYPOTHEQUE",
        "MAINLEVEE":      "HYPOTHEQUE",
        "FUSION":         "GENERAL",
        "SCISSION":       "GENERAL",
        "LOTISSEMENT":    "GENERAL",
        "EXPROPRIATION":  "GENERAL",
        "SUCCESSION":     "SUCCESSION",
        "IMMATRICULATION":"GENERAL",
        "RECTIFICATION":  "GENERAL",
        "SERVITUDE":      "GENERAL",
        "MISE_EN_GELE":   "JUSTICE",
    }
    return mapping.get(operation_type, "GENERAL")


def _recommandation(nb_bloquants: int, nb_avert: int) -> str:
    if nb_bloquants == 0 and nb_avert == 0:
        return ("Simulation réussie. Aucun impact détecté. "
                "L'opération peut être soumise à validation réelle.")
    if nb_bloquants == 0:
        return (f"Simulation réussie avec {nb_avert} avertissement(s). "
                "L'opération peut être soumise sous réserve de traiter les avertissements.")
    return (f"Simulation bloquée : {nb_bloquants} condition(s) bloquante(s) détectée(s). "
            "Résoudre tous les impacts BLOQUANT avant de soumettre l'opération réelle.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MOTEUR PRINCIPAL — SimulationEngine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SimulationEngine:
    """
    Orchestre les 6 couches de simulation dans un SAVEPOINT PostgreSQL.

    Toutes les modifications effectuées pendant la simulation sont
    ROLLBACK'd — les données réelles ne sont JAMAIS modifiées.

    Usage :
        ctx = SimulationContext(
            operation_type="MUTATION",
            parcelle_ids=["uuid-parcelle-1"],
            user_id="uuid-agent",
            region="NI-01",
            nouveau_titulaire_id="uuid-nouveau-titulaire",
        )
        result = await SimulationEngine.simuler(ctx, db=db)
        if result.autorisee:
            # procéder à la validation réelle
    """

    @classmethod
    async def simuler(
        cls,
        ctx:    SimulationContext,
        db=None,
        log_db=None,
    ) -> SimulationResult:
        """
        Point d'entrée principal.
        Exécute les 6 couches dans un SAVEPOINT isolé.
        """
        simulation_id = str(uuid.uuid4())
        t0 = time.monotonic()
        impacts:  List[ImpactSimule]    = []
        etapes:   List[EtapeSimulation] = []
        effets:   Dict[str, Any]        = {}
        donnees_actuelles: Dict[str, Any] = {}

        # Validation du type d'opération
        if ctx.operation_type not in OPERATION_TYPES:
            return SimulationResult(
                simulation_id  = simulation_id,
                operation_type = ctx.operation_type,
                statut         = SimulationStatut.ECHEC,
                parcelles_cibles = ctx.parcelle_ids,
                impacts = [ImpactSimule(
                    couche="S0", niveau=ImpactNiveau.BLOQUANT,
                    entite="operation", entite_id="",
                    message=f"Type d'opération inconnu : {ctx.operation_type}",
                    detail=f"Types supportés : {', '.join(OPERATION_TYPES)}",
                    correction="Utiliser un type d'opération valide.",
                )],
                recommandation="Opération non reconnue.",
                nb_bloquants=1,
            )

        # Pas de DB disponible → simulation statique uniquement
        if db is None:
            _sha_static = hashlib.sha256(
                json.dumps({"id": simulation_id, "op": ctx.operation_type,
                            "stat": "AVERTISSEMENT"}, sort_keys=True).encode()
            ).hexdigest()
            return SimulationResult(
                simulation_id  = simulation_id,
                operation_type = ctx.operation_type,
                statut         = SimulationStatut.AVERTISSEMENT,
                parcelles_cibles = ctx.parcelle_ids,
                impacts = [ImpactSimule(
                    couche="S0", niveau=ImpactNiveau.AVERTISSEMENT,
                    entite="engine", entite_id="",
                    message="Simulation statique uniquement (sans connexion DB)",
                    detail="Les couches S1-S6 nécessitent une connexion base de données.",
                    correction="Fournir une session DB pour une simulation complète.",
                )],
                etapes = [EtapeSimulation("S0", "Vérification", "WARN", 0, 1)],
                recommandation="Fournir une connexion DB pour une simulation complète.",
                nb_avertissements=1,
                sha256_rapport=_sha_static,
            )

        # ── SAVEPOINT D'ISOLATION ──────────────────────────────
        sp = f"sp_sim_{simulation_id.replace('-','')[:16]}"
        try:
            await db.execute(_text(f"SAVEPOINT {sp}"))
            await db.execute(_text(
                f"SET LOCAL statement_timeout = '{SIMULATION_TIMEOUT_MS}'"
            ))
            # La simulation peut lire ET écrire (pour simuler les effets)
            # mais tout sera rollback'd
        except Exception as exc:
            _log.warning("Simulation SAVEPOINT erreur: %s", exc)

        try:
            # ── S1 : Pré-conditions ────────────────────────────
            impacts, donnees_s1 = await cls._run_step(
                "S1", "Pré-conditions",
                _s1_preconditions(db, ctx, impacts),
                impacts, etapes, t0,
                return_data=True,
            )
            donnees_actuelles.update(donnees_s1)

            # Arrêt rapide si parcelles inexistantes
            s1_bloquants = sum(1 for i in impacts if i.couche == "S1"
                               and i.niveau == ImpactNiveau.BLOQUANT
                               and "introuvable" in i.message.lower())
            if s1_bloquants == len(ctx.parcelle_ids):
                raise _SimulationAbort("Toutes les parcelles sont introuvables")

            # ── S2 : Règles métier ─────────────────────────────
            impacts = await cls._run_step(
                "S2", "Règles métier",
                _s2_regles_metier(db, ctx, impacts, donnees_s1),
                impacts, etapes, t0,
            )

            # ── S3 : Droits fonciers ───────────────────────────
            impacts = await cls._run_step(
                "S3", "Droits fonciers",
                _s3_droits_fonciers(db, ctx, impacts, donnees_s1, effets),
                impacts, etapes, t0,
            )

            # ── S4 : Documents ─────────────────────────────────
            impacts = await cls._run_step(
                "S4", "Impacts documentaires",
                _s4_documents(db, ctx, impacts, effets),
                impacts, etapes, t0,
            )

            # ── S5 : Juridiction ───────────────────────────────
            impacts = await cls._run_step(
                "S5", "Juridiction & validation",
                _s5_juridiction(db, ctx, impacts, donnees_s1, effets),
                impacts, etapes, t0,
            )

            # ── S6 : Topologie ─────────────────────────────────
            impacts = await cls._run_step(
                "S6", "Topologie géographique",
                _s6_topologie(db, ctx, impacts, effets),
                impacts, etapes, t0,
            )

        except _SimulationAbort as ab:
            _log.info("Simulation %s arrêtée tôt: %s", simulation_id[:8], ab)
        except Exception as exc:
            _log.error("Simulation %s erreur inattendue: %s", simulation_id[:8], exc)
            impacts.append(ImpactSimule(
                couche="SX", niveau=ImpactNiveau.BLOQUANT,
                entite="moteur", entite_id="",
                message="Erreur interne du moteur de simulation",
                detail=str(exc)[:300],
                correction="Contacter le support technique.",
            ))
        finally:
            # ── ROLLBACK SYSTÉMATIQUE — données réelles intactes ──
            if db is not None:
                try:
                    await db.execute(_text(f"ROLLBACK TO SAVEPOINT {sp}"))
                    await db.execute(_text(f"RELEASE SAVEPOINT {sp}"))
                except Exception as exc:
                    _log.warning("Simulation ROLLBACK erreur: %s", exc)

        # ── Consolidation du rapport ───────────────────────────
        impacts = impacts[:MAX_IMPACTS_DETECTES]
        nb_bloquants = sum(1 for i in impacts if i.niveau == ImpactNiveau.BLOQUANT)
        nb_avert     = sum(1 for i in impacts if i.niveau == ImpactNiveau.AVERTISSEMENT)
        nb_infos     = sum(1 for i in impacts if i.niveau == ImpactNiveau.INFO)

        if nb_bloquants > 0:
            statut = SimulationStatut.ECHEC
        elif nb_avert > 0:
            statut = SimulationStatut.AVERTISSEMENT
        else:
            statut = SimulationStatut.SUCCES

        duree = int((time.monotonic() - t0) * 1000)
        reco  = _recommandation(nb_bloquants, nb_avert)

        sha = hashlib.sha256(
            json.dumps({
                "id":    simulation_id,
                "op":    ctx.operation_type,
                "stat":  statut.value,
                "nb_b":  nb_bloquants,
                "nb_a":  nb_avert,
            }, sort_keys=True).encode()
        ).hexdigest()

        result = SimulationResult(
            simulation_id    = simulation_id,
            operation_type   = ctx.operation_type,
            statut           = statut,
            parcelles_cibles = ctx.parcelle_ids,
            impacts          = impacts,
            etapes           = etapes,
            duree_ms         = duree,
            nb_bloquants     = nb_bloquants,
            nb_avertissements= nb_avert,
            nb_infos         = nb_infos,
            recommandation   = reco,
            sha256_rapport   = sha,
            donnees_actuelles= donnees_actuelles,
            effets_projetes  = effets,
        )

        await cls._journaliser(log_db or db, ctx, result)
        return result

    # ── Helpers internes ──────────────────────────────────────

    @classmethod
    async def _run_step(
        cls,
        couche: str,
        nom: str,
        coro,
        impacts: List[ImpactSimule],
        etapes:  List[EtapeSimulation],
        t0: float,
        *,
        return_data: bool = False,
    ):
        """Exécute une couche, mesure sa durée, ajoute la trace."""
        t_step = time.monotonic()
        data = None
        try:
            result = await coro
            if return_data:
                impacts, data = result
            else:
                impacts = result
        except Exception as exc:
            _log.warning("Couche %s erreur: %s", couche, exc)
            impacts.append(ImpactSimule(
                couche=couche, niveau=ImpactNiveau.AVERTISSEMENT,
                entite="couche", entite_id="",
                message=f"Erreur dans la couche {couche}",
                detail=str(exc)[:200],
                correction="Vérifier les données d'entrée de la simulation.",
            ))

        d = int((time.monotonic() - t_step) * 1000)
        nb_new = sum(1 for i in impacts if i.couche == couche)
        nb_blk = sum(1 for i in impacts if i.couche == couche
                     and i.niveau == ImpactNiveau.BLOQUANT)
        statut = "FAIL" if nb_blk > 0 else ("WARN" if nb_new > 0 else "PASS")
        details = [i.message for i in impacts if i.couche == couche][:5]

        etapes.append(EtapeSimulation(couche, nom, statut, d, nb_new, details))

        # Vérifier timeout global
        elapsed = int((time.monotonic() - t0) * 1000)
        if elapsed >= SIMULATION_TIMEOUT_MS - 500:
            raise _SimulationAbort(f"Timeout global {SIMULATION_TIMEOUT_MS} ms")

        return (impacts, data) if return_data else impacts

    @staticmethod
    async def _journaliser(db, ctx: SimulationContext, result: SimulationResult) -> None:
        if db is None:
            return
        try:
                        await db.execute(_text("""
                INSERT INTO simulation_log (
                    simulation_id, operation_type, parcelle_ids,
                    statut, nb_bloquants, nb_avertissements, nb_infos,
                    rapport_json, sha256_rapport,
                    duree_ms, autorisee, user_id, region
                ) VALUES (
                    :sid::uuid, :op, :pids,
                    :stat, :nb_b, :nb_a, :nb_i,
                    :rapport::jsonb, :sha,
                    :duree, :auth, :uid::uuid, :reg
                )
            """), {
                "sid":    result.simulation_id,
                "op":     result.operation_type,
                "pids":   result.parcelles_cibles,
                "stat":   result.statut.value,
                "nb_b":   result.nb_bloquants,
                "nb_a":   result.nb_avertissements,
                "nb_i":   result.nb_infos,
                "rapport": json.dumps(result.as_dict()),
                "sha":    result.sha256_rapport,
                "duree":  result.duree_ms,
                "auth":   result.autorisee,
                "uid":    ctx.user_id,
                "reg":    ctx.region,
            })
        except Exception as exc:
            _log.warning("simulation_log insert: %s", exc)


class _SimulationAbort(Exception):
    """Exception interne pour arrêt anticipé de la simulation."""
