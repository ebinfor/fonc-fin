"""
FONCIER+ ── Moteur de Validation Logique des Règles Métier
Couche 5 (C5) du pipeline de validation sécurisée.

Positionnement dans le pipeline :
  C1 Statique → C2 Sémantique → C3 Sandbox DB → C4 SHA-256 → C5 Logique
                                                               ↑ ce module

Responsabilités :
  • Détecter les contradictions entre une nouvelle règle et les règles actives
  • Vérifier la cohérence logique globale au sein d'un domaine
  • Identifier les décisions incompatibles ou mutuellement exclusives
  • Simuler l'application combinée (règles existantes + nouvelle)
  • Générer un rapport détaillé (conflits, gravité, recommandation)
  • Bloquer l'activation si une incohérence CRITIQUE est détectée

Architecture extensible :
  ┌─────────────────────────────────────────────────────────┐
  │  LogicConflictEngine                                    │
  │  ─────────────────────────────────────────────────────  │
  │  _heuristics : List[LogicHeuristic]  (registre global)  │
  │                                                         │
  │  register(heuristic)  ← ajouter une heuristique sans   │
  │                         modifier l'architecture          │
  │  valider(...)         ← point d'entrée unique            │
  └──────────────┬──────────────────────────────────────────┘
                 │ délègue à chaque heuristique enregistrée
         ┌───────┼───────────────────────────────────────┐
         ▼       ▼                                       ▼
  DuplicateH  NegationH  BlockingH  SubsumptionH  SimulationH  …
  (identique) (EXISTS vs  (BLOQUANT  (A ⊃ B       (combinaison  (extensible)
              NOT EXISTS)  conflits)  redondant)    toujours-KO)

Heuristiques intégrées (5 + 1 globale) :
  H1 DuplicateRuleHeuristic      — règle en double (sql normalisé identique)
  H2 NegationContradictionH      — EXISTS vs NOT EXISTS même table/condition
  H3 BlockingConflictHeuristic   — deux BLOQUANT incompatibles même domaine
  H4 SubsumptionHeuristic        — nouvelle règle subsumée par une existante
  H5 AlwaysBlockingHeuristic     — combinaison crée une impossibilité totale
  HG GlobalCoherenceHeuristic    — cohérence globale du domaine (N règles)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

_log = logging.getLogger("foncier.logic_validator")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TYPES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ConflictNiveau(str, Enum):
    CRITIQUE = "CRITIQUE"   # bloque l'activation
    MAJEUR   = "MAJEUR"     # avertissement fort, confirmation requise
    MINEUR   = "MINEUR"     # informatif, n'empêche pas l'activation


class Recommandation(str, Enum):
    AUTORISER = "AUTORISER"   # aucun conflit bloquant
    CORRIGER  = "CORRIGER"    # conflits MAJEUR — révision conseillée
    REFUSER   = "REFUSER"     # conflit CRITIQUE — activation bloquée


@dataclass
class RuleDescriptor:
    """
    Représentation normalisée d'une règle métier pour l'analyse logique.
    Extraite depuis la DB ou fournie directement.
    """
    code:           str
    nom:            str
    domaine:        str
    condition_sql:  str
    niveau_blocage: str   # BLOQUANT | AVERTISSEMENT | INFO
    actif:          bool  = True
    # Champs extraits par parse_rule()
    tables:         Set[str] = field(default_factory=set)
    predicats:      List[Dict[str, str]] = field(default_factory=list)
    is_exists:      bool  = False   # commence par EXISTS
    is_not_exists:  bool  = False   # commence par NOT EXISTS
    sql_normalise:  str   = ""

    @classmethod
    def from_db_row(cls, row: Dict) -> "RuleDescriptor":
        rd = cls(
            code          = row.get("code", ""),
            nom           = row.get("nom", ""),
            domaine       = row.get("domaine", ""),
            condition_sql = row.get("sql_normalise") or row.get("condition_sql", ""),
            niveau_blocage= row.get("niveau_blocage", "BLOQUANT"),
            actif         = bool(row.get("actif", True)),
        )
        rd._parse()
        return rd

    def _parse(self) -> None:
        sql = self.condition_sql.strip()
        self.sql_normalise = _normalise_sql(sql)
        UP  = sql.upper()
        self.is_exists     = UP.startswith("EXISTS")
        self.is_not_exists = UP.startswith("NOT EXISTS")
        self.tables        = _extract_tables(sql)
        self.predicats     = _extract_predicats(sql)


@dataclass
class LogicConflict:
    """Un conflit logique détecté entre la nouvelle règle et une règle existante."""
    heuristique:      str               # identifiant de l'heuristique
    niveau:           ConflictNiveau
    regle_cible:      str               # code de la règle en conflit
    message:          str               # description lisible
    details:          str               # explication technique
    suggestion:       str               # comment corriger


@dataclass
class LogicValidationReport:
    """
    Rapport complet de validation logique (C5).
    Retourné par LogicConflictEngine.valider().
    """
    code_regle:       str
    domaine:          str
    recommandation:   Recommandation
    conflits:         List[LogicConflict] = field(default_factory=list)
    regles_analysees: int = 0
    duree_ms:         int = 0
    sha256_rapport:   str = ""

    @property
    def nb_critique(self) -> int:
        return sum(1 for c in self.conflits if c.niveau == ConflictNiveau.CRITIQUE)

    @property
    def nb_majeur(self) -> int:
        return sum(1 for c in self.conflits if c.niveau == ConflictNiveau.MAJEUR)

    @property
    def nb_mineur(self) -> int:
        return sum(1 for c in self.conflits if c.niveau == ConflictNiveau.MINEUR)

    @property
    def regles_impactees(self) -> List[str]:
        return list({c.regle_cible for c in self.conflits if c.regle_cible})

    @property
    def bloque(self) -> bool:
        return self.recommandation == Recommandation.REFUSER

    def as_dict(self) -> dict:
        return {
            "code_regle":       self.code_regle,
            "domaine":          self.domaine,
            "recommandation":   self.recommandation.value,
            "bloque":           self.bloque,
            "nb_critique":      self.nb_critique,
            "nb_majeur":        self.nb_majeur,
            "nb_mineur":        self.nb_mineur,
            "regles_impactees": self.regles_impactees,
            "regles_analysees": self.regles_analysees,
            "duree_ms":         self.duree_ms,
            "sha256_rapport":   self.sha256_rapport,
            "conflits": [
                {
                    "heuristique": c.heuristique,
                    "niveau":      c.niveau.value,
                    "regle_cible": c.regle_cible,
                    "message":     c.message,
                    "details":     c.details,
                    "suggestion":  c.suggestion,
                }
                for c in self.conflits
            ],
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FONCTIONS D'ANALYSE SQL (extraction de structure)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _normalise_sql(sql: str) -> str:
    """Forme canonique : minuscules, espaces normalisés, parenthèses serrées,
    aliases de table supprimés (p.col → col, dc.etat → etat)."""
    sql = sql.strip().lower()
    sql = re.sub(r'\s+', ' ', sql)
    sql = re.sub(r'\s*\(\s*', '(', sql)
    sql = re.sub(r'\s*\)\s*', ')', sql)
    # Supprimer les préfixes d'alias courts (1-3 lettres) : p.col → col
    sql = re.sub(r'\b[a-z]{1,3}\.([\w]+)', r'\1', sql)
    return sql


def _extract_tables(sql: str) -> Set[str]:
    """Extrait les noms de tables référencées dans la condition SQL."""
    refs = re.findall(
        r'\bfrom\s+([a-z_]\w*)|\bjoin\s+([a-z_]\w*)',
        sql.lower()
    )
    result: Set[str] = set()
    for g1, g2 in refs:
        if g1: result.add(g1)
        if g2: result.add(g2)
    return result


def _extract_predicats(sql: str) -> List[Dict[str, str]]:
    """
    Extrait les prédicats de la forme  colonne OP valeur.
    Retourne une liste de {table, colonne, operateur, valeur}.

    Exemples détectés :
      p.is_gele = TRUE          → {col: is_gele, op: =,  val: TRUE}
      p.is_gele = FALSE         → {col: is_gele, op: =,  val: FALSE}
      statut IN ('A','B')       → {col: statut,  op: IN, val: A,B}
      etat != 'ARCHIVE'         → {col: etat,    op: !=, val: ARCHIVE}
      niveau_autorite >= 2      → {col: niveau_autorite, op: >=, val: 2}
    """
    predicats: List[Dict[str, str]] = []

    # Prédicats colonne = / != / < / > / <= / >= valeur
    for m in re.finditer(
        r'(?:(\w+)\.)?(\w+)\s*(=|!=|<>|>=|<=|>|<)\s*'
        r"('(?:[^']*)'|TRUE|FALSE|\$\d+|\d+(?:\.\d+)?)",
        sql, re.IGNORECASE
    ):
        table  = (m.group(1) or "").lower()
        col    = m.group(2).lower()
        op     = m.group(3).upper().replace("<>", "!=")
        val    = m.group(4).strip("'").upper()
        predicats.append({"table": table, "colonne": col, "op": op, "valeur": val})

    # Prédicats IS NULL / IS NOT NULL / IS TRUE / IS FALSE
    for m in re.finditer(
        r'(?:(\w+)\.)?(\w+)\s+IS\s+(NOT\s+)?(?:NULL|TRUE|FALSE)',
        sql, re.IGNORECASE
    ):
        table  = (m.group(1) or "").lower()
        col    = m.group(2).lower()
        negated = bool(m.group(3))
        full   = m.group(0).upper()
        val    = "NOT_NULL" if (negated and "NULL" in full) \
                 else "NULL" if "NULL" in full \
                 else ("FALSE" if negated else "TRUE")
        op     = "IS"
        predicats.append({"table": table, "colonne": col, "op": op, "valeur": val})

    # Prédicats colonne IN (...)
    for m in re.finditer(
        r'(?:(\w+)\.)?(\w+)\s+(NOT\s+)?IN\s*\(([^)]+)\)',
        sql, re.IGNORECASE
    ):
        table  = (m.group(1) or "").lower()
        col    = m.group(2).lower()
        negated = bool(m.group(3))
        vals   = ",".join(v.strip().strip("'").upper()
                         for v in m.group(4).split(","))
        op     = "NOT_IN" if negated else "IN"
        predicats.append({"table": table, "colonne": col, "op": op, "valeur": vals})

    return predicats


def _predicats_contradictoires(p1: Dict, p2: Dict) -> bool:
    """
    Retourne True si deux prédicats sur la même colonne sont logiquement
    contradictoires (impossible qu'ils soient vrais simultanément).

    Exemples :
      is_gele=TRUE  vs  is_gele=FALSE          → contradictoire
      statut='A'    vs  statut='B'              → contradictoire (valeurs ≠)
      actif=TRUE    vs  actif IS FALSE          → contradictoire
      etat IN (A,B) vs  etat NOT IN (A,B)       → contradictoire (superposition)
    """
    if p1["colonne"] != p2["colonne"]:
        return False

    op1, val1 = p1["op"], p1["valeur"]
    op2, val2 = p2["op"], p2["valeur"]

    # = vs = sur des valeurs différentes (booléens ou statuts)
    if op1 == "=" and op2 == "=" and val1 != val2:
        return True

    # = TRUE  vs  = FALSE
    if {val1, val2} == {"TRUE", "FALSE"} and op1 == op2 == "=":
        return True

    # IS TRUE vs IS FALSE  /  IS NOT NULL vs IS NULL
    if op1 == "IS" and op2 == "IS":
        if {val1, val2} == {"TRUE", "FALSE"}:
            return True
        if {val1, val2} == {"NULL", "NOT_NULL"}:
            return True

    # = val  vs  IS NULL (si val n'est pas NULL)
    if op1 == "=" and op2 == "IS" and val2 == "NULL":
        return True
    if op2 == "=" and op1 == "IS" and val1 == "NULL":
        return True

    # IN (A,B) vs NOT_IN avec intersection non vide
    if op1 == "IN" and op2 == "NOT_IN":
        s1 = set(val1.split(","))
        s2 = set(val2.split(","))
        if s1 & s2:  # intersection non vide → contradiction partielle
            return True
    if op2 == "IN" and op1 == "NOT_IN":
        s1 = set(val1.split(","))
        s2 = set(val2.split(","))
        if s1 & s2:
            return True

    # IN vs IN avec des ensembles disjoints sur même colonne + BLOQUANT
    # (les deux règles ne peuvent jamais être satisfaites simultanément)
    if op1 == "IN" and op2 == "IN":
        s1 = set(val1.split(","))
        s2 = set(val2.split(","))
        if not s1 & s2:  # ensembles disjoints
            return True

    return False


def _sql_similarity(s1: str, s2: str) -> float:
    """
    Score de similarité [0.0, 1.0] entre deux SQL normalisés.
    Basé sur le coefficient de Jaccard des bigrammes de tokens.
    """
    def tokens(s: str) -> List[str]:
        return re.findall(r'\w+', s.lower())

    t1, t2 = tokens(s1), tokens(s2)
    if not t1 or not t2:
        return 0.0

    def bigrams(lst: List[str]) -> Set[Tuple[str, str]]:
        return {(lst[i], lst[i+1]) for i in range(len(lst)-1)} if len(lst) > 1 \
               else {(lst[0],)}

    b1, b2 = bigrams(t1), bigrams(t2)
    intersection = len(b1 & b2)
    union = len(b1 | b2)
    return intersection / union if union else 0.0


def _is_negation_of(r1: RuleDescriptor, r2: RuleDescriptor) -> bool:
    """
    Retourne True si r1 et r2 sont la négation l'une de l'autre :
    EXISTS(cond)  vs  NOT EXISTS(cond)
    avec une similarité de condition > 0.85.
    """
    if not (r1.tables & r2.tables):  # tables différentes
        return False
    if r1.is_exists == r2.is_exists:  # même polarité
        return False
    # Extraire le corps de la sous-requête pour comparer
    body1 = re.sub(r'^(?:not\s+)?exists\s*\(', '', r1.sql_normalise, flags=re.IGNORECASE)
    body2 = re.sub(r'^(?:not\s+)?exists\s*\(', '', r2.sql_normalise, flags=re.IGNORECASE)
    return _sql_similarity(body1, body2) >= 0.80


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BASE HEURISTIQUE (extensible)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class LogicHeuristic(ABC):
    """
    Classe de base pour toutes les heuristiques logiques.
    Implémenter analyze() puis enregistrer via LogicConflictEngine.register().

    Exemple d'extension externe :
        class MyCustomHeuristic(LogicHeuristic):
            id = "MY_CUSTOM"
            description = "Vérifie ..."
            def analyze(self, new_rule, existing_rules):
                ...
        LogicConflictEngine.register(MyCustomHeuristic())
    """

    id:          str = "BASE"
    description: str = "Heuristique de base"

    @abstractmethod
    def analyze(
        self,
        new_rule:       RuleDescriptor,
        existing_rules: List[RuleDescriptor],
    ) -> List[LogicConflict]:
        """
        Analyse la nouvelle règle par rapport aux règles existantes.
        Retourne une liste de conflits détectés (vide si aucun).
        """


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H1 — DuplicateRuleHeuristic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DuplicateRuleHeuristic(LogicHeuristic):
    """
    Détecte les règles en double ou quasi-identiques.

    CRITIQUE (sim ≥ 0.97) : SQL normalisé identique à ±1 token.
    MAJEUR   (sim ≥ 0.85) : SQL très similaire (probable doublon).
    MINEUR   (sim ≥ 0.70) : SQL similaire (même intention).
    """
    id          = "H1_DUPLICATE"
    description = "Détection de règles en double ou quasi-identiques"

    def analyze(
        self, new_rule: RuleDescriptor, existing: List[RuleDescriptor]
    ) -> List[LogicConflict]:
        conflicts: List[LogicConflict] = []
        for r in existing:
            sim = _sql_similarity(new_rule.sql_normalise, r.sql_normalise)
            if sim >= 0.97:
                conflicts.append(LogicConflict(
                    heuristique  = self.id,
                    niveau       = ConflictNiveau.CRITIQUE,
                    regle_cible  = r.code,
                    message      = f"Règle en double : condition SQL identique à [{r.code}]",
                    details      = f"Similarité {sim:.0%}. SQL existant : {r.condition_sql[:80]}…",
                    suggestion   = f"Supprimer la nouvelle règle ou désactiver [{r.code}] "
                                   f"avant d'activer celle-ci.",
                ))
            elif sim >= 0.85:
                conflicts.append(LogicConflict(
                    heuristique  = self.id,
                    niveau       = ConflictNiveau.MAJEUR,
                    regle_cible  = r.code,
                    message      = f"Règle très similaire à [{r.code}] (similarité {sim:.0%})",
                    details      = f"Les deux règles semblent vérifier la même condition. "
                                   f"SQL existant : {r.condition_sql[:80]}…",
                    suggestion   = "Vérifier si les deux règles sont intentionnellement "
                                   "différentes ou si l'une remplace l'autre.",
                ))
            elif sim >= 0.70:
                conflicts.append(LogicConflict(
                    heuristique  = self.id,
                    niveau       = ConflictNiveau.MINEUR,
                    regle_cible  = r.code,
                    message      = f"Règle similaire à [{r.code}] (similarité {sim:.0%})",
                    details      = "Même intention probable. Peut être intentionnel.",
                    suggestion   = "Documenter la différence fonctionnelle entre les deux règles.",
                ))
        return conflicts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H2 — NegationContradictionHeuristic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class NegationContradictionHeuristic(LogicHeuristic):
    """
    Détecte les paires EXISTS / NOT EXISTS contradictoires.

    Si une règle BLOQUANT exige EXISTS(cond) et une autre exige NOT EXISTS(cond)
    sur les mêmes tables et conditions similaires, aucune entité ne peut jamais
    satisfaire les deux → impossibilité logique.

    CRITIQUE si les deux règles sont BLOQUANT.
    MAJEUR   si une seule est BLOQUANT.
    """
    id          = "H2_NEGATION"
    description = "Détection des paires EXISTS / NOT EXISTS contradictoires"

    def analyze(
        self, new_rule: RuleDescriptor, existing: List[RuleDescriptor]
    ) -> List[LogicConflict]:
        conflicts: List[LogicConflict] = []
        for r in existing:
            if not _is_negation_of(new_rule, r):
                continue
            # Polarité : l'une est EXISTS, l'autre NOT EXISTS
            both_bloquant = (
                new_rule.niveau_blocage == "BLOQUANT"
                and r.niveau_blocage    == "BLOQUANT"
            )
            niveau = ConflictNiveau.CRITIQUE if both_bloquant else ConflictNiveau.MAJEUR
            conflicts.append(LogicConflict(
                heuristique = self.id,
                niveau      = niveau,
                regle_cible = r.code,
                message     = f"Contradiction directe EXISTS / NOT EXISTS avec [{r.code}]",
                details     = (
                    f"La nouvelle règle ({'EXISTS' if new_rule.is_exists else 'NOT EXISTS'}) "
                    f"et [{r.code}] ({'EXISTS' if r.is_exists else 'NOT EXISTS'}) "
                    f"ont des conditions complémentaires sur {sorted(new_rule.tables & r.tables)}. "
                    f"Aucune entité ne peut satisfaire les deux simultanément."
                ),
                suggestion  = (
                    "Fusionner les deux règles ou conditionner l'une à l'activation de l'autre. "
                    "Exemple : utiliser des niveaux BLOQUANT / AVERTISSEMENT différents."
                    if both_bloquant
                    else "Vérifier l'intention fonctionnelle des deux règles."
                ),
            ))
        return conflicts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H3 — BlockingConflictHeuristic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BlockingConflictHeuristic(LogicHeuristic):
    """
    Détecte les conflits entre deux règles BLOQUANT du même domaine
    qui imposent des conditions contradictoires sur la même colonne.

    Exemples détectés :
      Règle A : ... WHERE p.is_gele = FALSE   (BLOQUANT)
      Règle B : ... WHERE p.is_gele = TRUE    (BLOQUANT)
      → Impossible : doit être gelé ET non gelé

      Règle A : ... WHERE statut IN ('ACTIF')
      Règle B : ... WHERE statut IN ('ARCHIVE')
      → Impossible si les ensembles sont disjoints
    """
    id          = "H3_BLOCKING"
    description = "Conflits entre règles BLOQUANT à conditions contradictoires"

    def analyze(
        self, new_rule: RuleDescriptor, existing: List[RuleDescriptor]
    ) -> List[LogicConflict]:
        conflicts: List[LogicConflict] = []
        if new_rule.niveau_blocage != "BLOQUANT":
            return conflicts

        for r in existing:
            if r.niveau_blocage != "BLOQUANT":
                continue
            if not (new_rule.tables & r.tables):
                continue

            # Chercher des prédicats contradictoires
            for p1 in new_rule.predicats:
                for p2 in r.predicats:
                    if not _predicats_contradictoires(p1, p2):
                        continue
                    conflicts.append(LogicConflict(
                        heuristique = self.id,
                        niveau      = ConflictNiveau.CRITIQUE,
                        regle_cible = r.code,
                        message     = (
                            f"Conflit BLOQUANT : la colonne '{p1['colonne']}' "
                            f"doit être [{p1['valeur']}] (nouvelle règle) "
                            f"ET [{p2['valeur']}] ([{r.code}]) simultanément"
                        ),
                        details     = (
                            f"Tables communes : {sorted(new_rule.tables & r.tables)}. "
                            f"Prédicat nouvelle règle : {p1['colonne']} {p1['op']} {p1['valeur']}. "
                            f"Prédicat [{r.code}] : {p2['colonne']} {p2['op']} {p2['valeur']}. "
                            f"Aucune entité ne peut passer les deux règles BLOQUANT."
                        ),
                        suggestion  = (
                            f"Désactiver [{r.code}] avant d'activer la nouvelle règle, "
                            "ou modifier l'une des deux pour qu'elles ne soient pas "
                            "mutuellement exclusives."
                        ),
                    ))
        return conflicts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H4 — SubsumptionHeuristic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SubsumptionHeuristic(LogicHeuristic):
    """
    Détecte les cas où une règle est subsumée par une autre
    (redondance logique : la nouvelle règle est toujours couverte
    par une règle plus générale déjà active).

    Heuristique :
      Si la nouvelle règle est plus stricte (plus de prédicats) qu'une existante
      ET que la règle existante est BLOQUANT, la nouvelle règle est "dans l'ombre"
      de la plus générale — elle ne changera jamais le résultat final.
    """
    id          = "H4_SUBSUMPTION"
    description = "Détection de règles redondantes (subsomption logique)"

    def analyze(
        self, new_rule: RuleDescriptor, existing: List[RuleDescriptor]
    ) -> List[LogicConflict]:
        conflicts: List[LogicConflict] = []

        for r in existing:
            if not (new_rule.tables & r.tables):
                continue
            if not r.predicats or not new_rule.predicats:
                continue

            # Vérifier si tous les prédicats de r sont présents dans new_rule
            # (r est plus général → new_rule est un sous-cas de r)
            r_cols   = {(p["colonne"], p["op"], p["valeur"]) for p in r.predicats}
            new_cols = {(p["colonne"], p["op"], p["valeur"]) for p in new_rule.predicats}

            if r_cols and r_cols.issubset(new_cols) and len(new_cols) > len(r_cols):
                # new_rule est plus restrictive que r
                if r.niveau_blocage == "BLOQUANT" and new_rule.niveau_blocage == "BLOQUANT":
                    conflicts.append(LogicConflict(
                        heuristique = self.id,
                        niveau      = ConflictNiveau.MINEUR,
                        regle_cible = r.code,
                        message     = (
                            f"Règle redondante : [{r.code}] (plus générale) couvre déjà "
                            "tous les cas de la nouvelle règle"
                        ),
                        details     = (
                            f"La règle [{r.code}] bloque déjà sur : {sorted(r_cols)}. "
                            "La nouvelle règle ajoute des conditions supplémentaires "
                            f"{sorted(new_cols - r_cols)} mais sera toujours bloquée avant."
                        ),
                        suggestion  = (
                            "La nouvelle règle n'aura aucun effet si [" + r.code + "] "
                            "est déjà active. Vérifier l'utilité fonctionnelle."
                        ),
                    ))
        return conflicts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H5 — AlwaysBlockingHeuristic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AlwaysBlockingHeuristic(LogicHeuristic):
    """
    Détecte si la combinaison de la nouvelle règle + les règles BLOQUANT
    existantes crée une situation d'impossibilité totale
    (aucune entité ne pourrait jamais être autorisée dans le domaine).

    Indicateurs :
      • > 5 règles BLOQUANT sur le même domaine et les mêmes tables
        sans aucune règle de libération (TRUE ou IS NOT NULL sur même col)
      • Règles qui se verrouillent mutuellement sur tous les états possibles
        d'une colonne d'état (ex : statut IN (A,B) + statut IN (C,D)
        sans overlap ET sans règle TRUE)
    """
    id          = "H5_ALWAYS_BLOCKING"
    description = "Détection d'une combinaison créant une impossibilité totale"

    # Seuil : si nb_bloquant dépasse ce seuil sur mêmes tables → alerte
    SEUIL_NB_BLOQUANT = 7

    def analyze(
        self, new_rule: RuleDescriptor, existing: List[RuleDescriptor]
    ) -> List[LogicConflict]:
        conflicts: List[LogicConflict] = []

        # Règles BLOQUANT actives sur les mêmes tables
        bloquants = [
            r for r in existing
            if r.niveau_blocage == "BLOQUANT"
            and (r.tables & new_rule.tables)
        ]

        if new_rule.niveau_blocage == "BLOQUANT":
            bloquants.append(new_rule)

        if len(bloquants) <= 1:
            return conflicts

        # Détecter les colonnes couvertes par des conditions IN disjointes
        col_to_in_vals: Dict[str, List[Set[str]]] = {}
        for r in bloquants:
            for p in r.predicats:
                if p["op"] == "IN":
                    col = p["colonne"]
                    vals = set(p["valeur"].split(","))
                    col_to_in_vals.setdefault(col, []).append(vals)

        for col, val_sets in col_to_in_vals.items():
            if len(val_sets) >= 2:
                # Vérifier si l'union couvre un espace contraint (pas de chevauchement)
                disjoint_pairs = 0
                for i in range(len(val_sets)):
                    for j in range(i+1, len(val_sets)):
                        if not val_sets[i] & val_sets[j]:
                            disjoint_pairs += 1
                if disjoint_pairs >= 2:
                    affected = [r.code for r in bloquants]
                    conflicts.append(LogicConflict(
                        heuristique = self.id,
                        niveau      = ConflictNiveau.MAJEUR,
                        regle_cible = ",".join(affected[:5]),
                        message     = (
                            f"Combinaison potentiellement bloquante : la colonne '{col}' "
                            f"est contrainte par {len(val_sets)} règles aux ensembles disjoints"
                        ),
                        details     = (
                            f"Les règles {affected[:5]} imposent des valeurs IN disjointes "
                            f"sur '{col}'. Une entité devrait correspondre à plusieurs "
                            "ensembles mutuellement exclusifs."
                        ),
                        suggestion  = (
                            "Vérifier que les états possibles de la colonne "
                            f"'{col}' couvrent bien les intersections attendues. "
                            "Envisager une règle globale de type AVERTISSEMENT plutôt que BLOQUANT."
                        ),
                    ))

        # Trop de règles BLOQUANT sur les mêmes tables
        if len(bloquants) >= self.SEUIL_NB_BLOQUANT:
            conflicts.append(LogicConflict(
                heuristique = self.id,
                niveau      = ConflictNiveau.MINEUR,
                regle_cible = ",".join(r.code for r in bloquants[:5]),
                message     = (
                    f"{len(bloquants)} règles BLOQUANT actives sur les mêmes tables "
                    f"({sorted(new_rule.tables)[:3]}) — risque de blocage systématique"
                ),
                details     = (
                    "Un grand nombre de règles BLOQUANT augmente le risque qu'aucune entité "
                    "ne puisse jamais être autorisée. Analyser la combinaison globale."
                ),
                suggestion  = (
                    "Regrouper les règles similaires, ou convertir certaines "
                    "en AVERTISSEMENT si elles ne sont pas strictement bloquantes."
                ),
            ))
        return conflicts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HG — GlobalCoherenceHeuristic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class GlobalCoherenceHeuristic(LogicHeuristic):
    """
    Vérification globale de la cohérence de l'ensemble des règles du domaine.

    Analyse transversale (toutes les règles existantes + nouvelle) :
      • Conflits de niveau entre règles du même domaine :
        une règle AVERTISSEMENT qui contredit une BLOQUANT existante
      • Règles jamais déclenchées (conditions impossibles avec le schéma foncier)
      • Collision entre domaines GENERAL et spécialisé
    """
    id          = "HG_GLOBAL"
    description = "Cohérence globale du domaine (analyse N règles)"

    def analyze(
        self, new_rule: RuleDescriptor, existing: List[RuleDescriptor]
    ) -> List[LogicConflict]:
        conflicts: List[LogicConflict] = []

        # Conflit de niveau : AVERTISSEMENT annule une BLOQUANT similaire
        if new_rule.niveau_blocage == "AVERTISSEMENT":
            for r in existing:
                if r.niveau_blocage != "BLOQUANT":
                    continue
                sim = _sql_similarity(new_rule.sql_normalise, r.sql_normalise)
                if sim >= 0.75:
                    conflicts.append(LogicConflict(
                        heuristique = self.id,
                        niveau      = ConflictNiveau.MAJEUR,
                        regle_cible = r.code,
                        message     = (
                            f"Incohérence de niveau : règle similaire à [{r.code}] "
                            f"(BLOQUANT) mais définie comme AVERTISSEMENT"
                        ),
                        details     = (
                            f"Similarité {sim:.0%}. La même condition est traitée "
                            "différemment (bloquante vs avertissement), ce qui peut "
                            "créer une confusion opérationnelle."
                        ),
                        suggestion  = (
                            f"Aligner le niveau de blocage avec [{r.code}], "
                            "ou documenter explicitement la différence fonctionnelle."
                        ),
                    ))

        # Collision domaine GENERAL vs domaine spécialisé
        if new_rule.domaine != "GENERAL":
            general_rules = [
                r for r in existing
                if r.domaine == "GENERAL"
                and r.tables & new_rule.tables
                and r.niveau_blocage == "BLOQUANT"
            ]
            for r in general_rules:
                sim = _sql_similarity(new_rule.sql_normalise, r.sql_normalise)
                if sim >= 0.70:
                    conflicts.append(LogicConflict(
                        heuristique = self.id,
                        niveau      = ConflictNiveau.MINEUR,
                        regle_cible = r.code,
                        message     = (
                            f"Chevauchement avec règle GENERAL [{r.code}] "
                            f"(similarité {sim:.0%})"
                        ),
                        details     = (
                            f"La règle GENERAL [{r.code}] s'applique à tous les domaines "
                            f"et chevauche la nouvelle règle domaine [{new_rule.domaine}]."
                        ),
                        suggestion  = (
                            "Vérifier que la règle GENERAL ne couvre pas déjà "
                            "le cas de cette règle spécialisée."
                        ),
                    ))

        return conflicts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MOTEUR PRINCIPAL — LogicConflictEngine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class LogicConflictEngine:
    """
    Orchestrateur des heuristiques de validation logique.
    Point d'entrée unique (C5) : LogicConflictEngine.valider(...)

    Extensibilité :
        LogicConflictEngine.register(MaNouvellHeuristique())
        → enregistrée une fois pour toutes, active immédiatement

    Performance :
        • Charge max MAX_RULES_ANALYSEES règles depuis la DB
        • Timeout global ENGINE_TIMEOUT_MS
        • Chaque heuristique peut être activée / désactivée individuellement
    """

    MAX_RULES_ANALYSEES = 200   # limite de règles chargées depuis la DB
    ENGINE_TIMEOUT_MS   = 3000  # timeout global de l'analyse logique

    _heuristics: List[LogicHeuristic] = []
    _disabled:   Set[str] = set()   # IDs des heuristiques désactivées

    @classmethod
    def register(cls, heuristic: LogicHeuristic) -> None:
        """Enregistre une nouvelle heuristique (extensibilité sans modification)."""
        if not any(h.id == heuristic.id for h in cls._heuristics):
            cls._heuristics.append(heuristic)
            _log.info("Heuristique enregistrée : %s", heuristic.id)

    @classmethod
    def disable(cls, heuristic_id: str) -> None:
        """Désactive une heuristique (pour tests ou maintenance)."""
        cls._disabled.add(heuristic_id)

    @classmethod
    def enable(cls, heuristic_id: str) -> None:
        """Réactive une heuristique désactivée."""
        cls._disabled.discard(heuristic_id)

    @classmethod
    def list_heuristics(cls) -> List[Dict[str, str]]:
        return [
            {"id": h.id, "description": h.description,
             "active": h.id not in cls._disabled}
            for h in cls._heuristics
        ]

    @classmethod
    async def valider(
        cls,
        sql:           str,
        domaine:       str,
        code:          str,
        niveau_blocage: str,
        nom:           str           = "",
        db                           = None,
        log_db                       = None,
        user_id:       str           = "SYSTEM",
    ) -> LogicValidationReport:
        """
        Point d'entrée C5 : valide logiquement une règle par rapport
        aux règles actives du même domaine (et GENERAL).

        Étapes :
          1. Charger les règles actives depuis la DB (si db disponible)
          2. Construire le RuleDescriptor de la nouvelle règle
          3. Exécuter chaque heuristique enregistrée
          4. Calculer la recommandation finale
          5. Journaliser dans regle_validation_logique_log
          6. Retourner le rapport
        """
        t0 = time.monotonic()

        # Construire le descripteur de la nouvelle règle
        new_rule = RuleDescriptor(
            code=code, nom=nom, domaine=domaine,
            condition_sql=sql, niveau_blocage=niveau_blocage,
        )
        new_rule._parse()

        existing_rules: List[RuleDescriptor] = []

        # ── Chargement des règles existantes ──────────────────
        if db is not None:
            existing_rules = await cls._load_rules(db, domaine, code)

        # ── Application des heuristiques ──────────────────────
        all_conflicts: List[LogicConflict] = []
        for heuristic in cls._heuristics:
            if heuristic.id in cls._disabled:
                continue
            elapsed = (time.monotonic() - t0) * 1000
            if elapsed >= cls.ENGINE_TIMEOUT_MS:
                _log.warning(
                    "Logic engine: timeout %d ms atteint après %s",
                    cls.ENGINE_TIMEOUT_MS, heuristic.id
                )
                break
            try:
                conflicts = heuristic.analyze(new_rule, existing_rules)
                all_conflicts.extend(conflicts)
            except Exception as exc:
                _log.warning("Heuristique %s erreur: %s", heuristic.id, exc)

        # ── Recommandation finale ──────────────────────────────
        nb_crit  = sum(1 for c in all_conflicts if c.niveau == ConflictNiveau.CRITIQUE)
        nb_major = sum(1 for c in all_conflicts if c.niveau == ConflictNiveau.MAJEUR)

        if nb_crit > 0:
            recommandation = Recommandation.REFUSER
        elif nb_major > 0:
            recommandation = Recommandation.CORRIGER
        else:
            recommandation = Recommandation.AUTORISER

        duree = int((time.monotonic() - t0) * 1000)

        # SHA-256 du rapport (non-répudiation)
        sha = hashlib.sha256(
            json.dumps(
                [c.message for c in all_conflicts] + [recommandation.value],
                sort_keys=True
            ).encode()
        ).hexdigest()

        report = LogicValidationReport(
            code_regle       = code,
            domaine          = domaine,
            recommandation   = recommandation,
            conflits         = all_conflicts,
            regles_analysees = len(existing_rules),
            duree_ms         = duree,
            sha256_rapport   = sha,
        )

        await cls._log_rapport(log_db or db, report, user_id)
        return report

    # ── Helpers ───────────────────────────────────────────────

    @classmethod
    async def _load_rules(
        cls, db, domaine: str, exclude_code: str
    ) -> List[RuleDescriptor]:
        """
        Charge les règles actives et validées depuis la DB.
        Inclut le domaine spécifié ET GENERAL (les deux s'appliquent).
        Limite à MAX_RULES_ANALYSEES pour garantir la performance.
        """
        try:
            from sqlalchemy import text
            r = await db.execute(text("""
                SELECT code, nom, domaine,
                       COALESCE(sql_normalise, condition_sql) AS condition_sql,
                       niveau_blocage, actif
                FROM regle_metier
                WHERE actif          = TRUE
                  AND sandbox_valide = TRUE
                  AND code           != :exclude
                  AND (domaine = :dom OR domaine = 'GENERAL')
                ORDER BY niveau_blocage DESC, code
                LIMIT :lim
            """), {"dom": domaine, "exclude": exclude_code,
                   "lim": cls.MAX_RULES_ANALYSEES})
            return [
                RuleDescriptor.from_db_row(dict(row))
                for row in r.mappings().all()
            ]
        except Exception as exc:
            _log.error(
                "logic_validator: échec chargement règles SQL (retour liste vide): "
                "%s — les règles actives sont inaccessibles", exc,
                exc_info=True,
            )
            return []  # Retour sûr — aucune règle disponible à valider

    @classmethod
    async def _log_rapport(
        cls, db, report: LogicValidationReport, user_id: str
    ) -> None:
        """Journalise le rapport dans regle_validation_logique_log."""
        if db is None:
            return
        try:
            from sqlalchemy import text
            await db.execute(text("""
                INSERT INTO regle_validation_logique_log (
                    code_regle, domaine, recommandation,
                    nb_critique, nb_majeur, nb_mineur,
                    regles_impactees, regles_analysees,
                    rapport_json, sha256_rapport,
                    duree_ms, valide_logiquement, soumis_par
                ) VALUES (
                    :code, :dom, :reco,
                    :ncrit, :nmaj, :nmin,
                    :impacts, :analysees,
                    :rapport::jsonb, :sha256,
                    :duree, :valide, :uid::uuid
                )
            """), {
                "code":      report.code_regle,
                "dom":       report.domaine,
                "reco":      report.recommandation.value,
                "ncrit":     report.nb_critique,
                "nmaj":      report.nb_majeur,
                "nmin":      report.nb_mineur,
                "impacts":   report.regles_impactees,
                "analysees": report.regles_analysees,
                "rapport":   json.dumps(report.as_dict()),
                "sha256":    report.sha256_rapport,
                "duree":     report.duree_ms,
                "valide":    not report.bloque,
                "uid":       user_id if user_id != "SYSTEM" else None,
            })
        except Exception as exc:
            _log.warning("logic_validator: log_rapport: %s", exc)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# INITIALISATION — Enregistrement des heuristiques built-in
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _register_builtin_heuristics() -> None:
    """
    Enregistre les 5+1 heuristiques intégrées.
    Appelé automatiquement à l'import du module.
    Ordre d'enregistrement = ordre d'exécution.
    """
    for h in [
        DuplicateRuleHeuristic(),
        NegationContradictionHeuristic(),
        BlockingConflictHeuristic(),
        SubsumptionHeuristic(),
        AlwaysBlockingHeuristic(),
        GlobalCoherenceHeuristic(),
    ]:
        LogicConflictEngine.register(h)


_register_builtin_heuristics()
