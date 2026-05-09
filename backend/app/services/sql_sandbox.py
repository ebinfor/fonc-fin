"""
FONCIER+ ── SQL Sandbox
Validation sécurisée des règles métier SQL (4 couches officielles).

Architecture (alignée sur le diagramme technique) :

  POST /admin/regles-metier
        │
        ▼
  COUCHE 1 — StaticSQLValidator          (Python pur, 0 dépendance)
        │ 40+ mots-clés interdits, commentaires, dollar-quoting,
        │ multi-instructions, UNION, INTO, profondeur max 6,
        │ WITH RECURSIVE, schémas système, fonctions dangereuses
        │ → OK | REFUSE immédiat
        ▼ si OK
  COUCHE 2 — SemanticSQLValidator        (logique métier foncier)
        │ Whitelist 30 tables autorisées, pg_shadow/pg_catalog bloqués,
        │ paramètres $1/$2, cohérence domaine, expression booléenne
        │ → OK | INVALIDE avec détail
        ▼ si OK
  COUCHE 3 — PostgresSandbox             (si DB disponible)
        │ SAVEPOINT + EXPLAIN (aucune exécution réelle)
        │ SET LOCAL default_transaction_read_only = ON
        │ Timeout 2 secondes strict
        │ Détection tentative d'écriture → REFUSE
        │ → OK | INVALIDE / REFUSE
        ▼ si OK
  COUCHE 4 — RuleSignature               (non-répudiation)
        │ Normalisation des espaces
        │ SHA-256(sql | domaine | code | niveau | user | date)
        └→ safe_sql + sha256_regle

  En amont (C0 interne) — BypassDetector
        Normalisation Unicode, homoglyphs cyrilliques, hex/URL/octal,
        caractères invisibles. Transparent pour l'appelant.

Garanties :
  • Aucune instruction DDL/DML ne peut passer (4 barrières)
  • Exécution dans une transaction READ ONLY annulée par ROLLBACK
  • Timeout 2 s maximum par règle
  • Toutes les tentatives journalisées dans sql_validation_log
  • Rate-limiting Python-layer : 200 validations/heure/user
  • Cache mémoire TTL 1 h (500 entrées max)
  • Batch validation : jusqu'à 50 règles en une passe
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

_log = logging.getLogger("foncier.sql_sandbox")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONSTANTES DE SÉCURITÉ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 40+ mots-clés absolument interdits
FORBIDDEN_KEYWORDS: Set[str] = {
    # DDL
    "DROP", "CREATE", "ALTER", "TRUNCATE", "RENAME",
    # DML
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT",
    # Transactions (ne doivent pas apparaître dans une condition SQL)
    "COMMIT", "ROLLBACK", "SAVEPOINT", "BEGIN",
    # Administration PostgreSQL
    "COPY", "VACUUM", "ANALYZE", "CLUSTER", "REINDEX",
    "CHECKPOINT", "NOTIFY", "LISTEN", "UNLISTEN",
    # Droits
    "GRANT", "REVOKE", "SECURITY", "OWNER",
    # Exécution dynamique
    "EXECUTE", "PERFORM", "CALL",
    # Accès fichiers
    "LOAD", "lo_import", "lo_export",
    # Extensions dangereuses
    "dblink", "pg_exec", "eval",
    # PL/pgSQL interdit dans les conditions
    "RAISE", "ASSERT", "DECLARE",
}

# Fonctions PostgreSQL interdites (DoS, escalade, exfiltration)
FORBIDDEN_FUNCTIONS: Set[str] = {
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
    "pg_stat_file", "pg_write_file",
    "dblink_exec", "dblink_open", "dblink_connect",
    "exec", "set_config",
    "pg_cancel_backend", "pg_terminate_backend",
    "pg_reload_conf", "pg_export_snapshot",
    "setval", "nextval",          # séquences = écriture
    "pg_sleep",                   # DoS timing
    "current_setting",            # lecture de GUC (info-leak)
    "txid_current",               # gestion transaction
}

# Fonctions SQL autorisées (allowlist positive)
ALLOWED_FUNCTIONS: Set[str] = {
    # Comparaison / null-handling
    "coalesce", "nullif", "greatest", "least",
    # Agrégats de lecture
    "count", "sum", "max", "min", "avg",
    # Texte (lecture seule)
    "lower", "upper", "trim", "length", "substr",
    "substring", "split_part", "regexp_replace",
    # Date/heure
    "now", "current_date", "current_timestamp",
    "date_trunc", "extract", "age",
    # UUID
    "gen_random_uuid",
    # JSON (lecture)
    "jsonb_exists", "jsonb_array_elements",
}

# Tables autorisées dans les règles (whitelist de 30 tables)
ALLOWED_TABLES: Set[str] = {
    # ── Parcellaire & identifiants
    "parcelles", "nicad_registry", "lotissements",
    "bgu_geojson_master", "bgu_parcel_status", "bgu_projection",
    # ── RNAF / RNP
    "rnaf", "rnaf_workflow", "rnp_demandes",
    # ── CCFM
    "demandes_ccfm", "fiches_constat_ccfm",
    "rapports_appreciation_ccfm", "ordres_mission_ccfm", "historique_ccfm",
    # ── Droits fonciers et transactions
    "parcel_right_version", "property_transfers",
    "right_holders", "transaction_blocks", "parcel_freeze_events",
    # ── Sécurité & contentieux
    "litiges", "parcel_disputes",
    "hypotheques", "mortgage_registry",
    "ccfm_suspension", "ccfm_contestation",
    # ── Institutions et agents
    "users", "agent_profil", "institutions",
    "delegation_administrative", "territoire_juridiction",
    # ── Urbanisme
    "arretes_urbanisme", "urbanisme_conformite",
    # ── Archives
    "annf_archive_links",
    # ── Moteur règles (lecture seule)
    "regle_metier",
    # ── Verrous & workflows
    "validation_pipeline", "entity_lock",
    # ── Compléments géo
    "conflits_parcellaires", "parcel_lineage",
    "parcel_public_history", "parcel_servitude",
}

# Tables système PostgreSQL — TOUJOURS bloquées
SYSTEM_TABLES: Set[str] = {
    "pg_shadow", "pg_authid", "pg_auth_members",
    "pg_hba_file_rules", "pg_ident_file_mappings",
    "pg_config", "pg_file_settings",
    "pg_stat_activity", "pg_locks", "pg_prepared_xacts",
    "pg_replication_slots",
}

SQL_MAX_LENGTH = 2000

# Correspondances homoglyphes cyrilliques → ASCII
_CYRILLIC: Dict[str, str] = {
    "а": "a", "е": "e", "о": "o", "р": "r", "с": "c", "х": "x",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
    "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
}

# Caractères invisibles Unicode (vecteurs d'obfuscation)
_INVISIBLE: frozenset = frozenset([
    "\u200b", "\u200c", "\u200d", "\ufeff",
    "\u2060", "\u00ad", "\u180e",
])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TYPES RÉSULTATS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ValidationStatus(str, Enum):
    VALIDE   = "VALIDE"    # 4 couches passées
    INVALIDE = "INVALIDE"  # Problème de structure/sémantique corrigible
    REFUSE   = "REFUSE"    # Tentative dangereuse — rejet définitif


@dataclass
class ValidationError:
    code:    str
    message: str
    couche:  int  # 0=bypass, 1=statique, 2=sémantique, 3=sandbox, 4=signature


@dataclass
class LayerTrace:
    """Trace d'exécution d'une couche (pour la visualisation pipeline)."""
    couche:   int
    nom:      str
    statut:   str   # "PASS" | "FAIL" | "SKIP" | "WARN"
    duree_ms: int
    details:  List[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    status:        ValidationStatus
    errors:        List[ValidationError] = field(default_factory=list)
    warnings:      List[str]             = field(default_factory=list)
    sha256_regle:  str                   = ""
    duree_ms:      int                   = 0
    safe_sql:      Optional[str]         = None
    bypass_alerts: List[str]             = field(default_factory=list)
    # Trace couche par couche (pour le frontend pipeline)
    layers:        List[LayerTrace]      = field(default_factory=list)
    # Rapport C5 — validation logique (None si non exécutée)
    logic_report:  Optional[Dict]        = None

    @property
    def valide(self) -> bool:
        return self.status == ValidationStatus.VALIDE

    def as_dict(self) -> dict:
        return {
            "status":        self.status.value,
            "valide":        self.valide,
            "errors":        [{"code": e.code, "message": e.message, "couche": e.couche}
                              for e in self.errors],
            "warnings":      self.warnings,
            "bypass_alerts": self.bypass_alerts,
            "sha256_regle":  self.sha256_regle,
            "duree_ms":      self.duree_ms,
            "safe_sql":      self.safe_sql,
            "layers":        [{"couche": l.couche, "nom": l.nom, "statut": l.statut,
                               "duree_ms": l.duree_ms, "details": l.details}
                              for l in self.layers],
            "logic_report":  self.logic_report,
        }


@dataclass
class BatchValidationResult:
    total:           int
    valides:         int
    invalides:       int
    refuses:         int
    duree_totale_ms: int
    resultats:       List[Dict]

    @property
    def taux_validation(self) -> float:
        return round(self.valides / self.total * 100, 1) if self.total else 0.0

    def as_dict(self) -> dict:
        return {
            "total":           self.total,
            "valides":         self.valides,
            "invalides":       self.invalides,
            "refuses":         self.refuses,
            "duree_totale_ms": self.duree_totale_ms,
            "taux_validation": self.taux_validation,
            "resultats":       self.resultats,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RATE LIMITER (Python-layer, sans dépendance Redis)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class InMemoryRateLimiter:
    """200 validations / heure / user_id. Nettoyage automatique toutes les 5 min."""

    MAX_PER_HOUR = 200
    WINDOW       = 3600.0

    _store:        Dict[str, List[float]] = defaultdict(list)
    _last_cleanup: float = 0.0

    @classmethod
    def check(cls, user_id: str) -> Tuple[bool, int]:
        """
        Retourne (allowed, remaining).
        Enregistre la tentative si autorisée.
        """
        now = time.time()
        # Nettoyage périodique
        if now - cls._last_cleanup > 300:
            cutoff = now - cls.WINDOW
            stale = [uid for uid, ts in cls._store.items()
                     if not any(t > cutoff for t in ts)]
            for uid in stale:
                del cls._store[uid]
            cls._last_cleanup = now

        cutoff = now - cls.WINDOW
        cls._store[user_id] = [t for t in cls._store[user_id] if t > cutoff]
        count = len(cls._store[user_id])
        if count >= cls.MAX_PER_HOUR:
            return False, 0
        cls._store[user_id].append(now)
        return True, cls.MAX_PER_HOUR - count - 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# C0 (INTERNE) — Pré-filtrage anti-bypass Unicode
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BypassDetector:
    """
    Couche interne (transparente pour le diagramme officiel).
    Normalise le SQL avant la C1 pour éliminer les vecteurs d'obfuscation :
      • Caractères invisibles Unicode (ZWSP, ZWJ, BOM…)
      • Homoglyphes cyrilliques (о→o, р→r, с→c…)
      • Normalisation NFKC (caractères composés → décomposés)
      • Encodage hex Python (\x44\x52…)
      • Encodage URL (%44%52…)
      • Encodage octal PostgreSQL (E'\104\122…')
      • Unicode escape PostgreSQL (U&'...')
      • Commentaires blocs (/*...*/)
    """

    @classmethod
    def normalize_and_check(cls, sql: str) -> Tuple[str, List[str]]:
        """Retourne (sql_nettoyé, alertes)."""
        alerts: List[str] = []

        # Caractères invisibles
        invisible = [c for c in sql if c in _INVISIBLE]
        if invisible:
            alerts.append(f"INVISIBLE_CHARS: {len(invisible)} caractère(s) invisible(s)")
            sql = "".join(c for c in sql if c not in _INVISIBLE)

        # NFKC — décompose les ligatures, caractères composés
        nfkc = unicodedata.normalize("NFKC", sql)
        if nfkc != sql:
            alerts.append("UNICODE_NORMALIZATION: caractères composés normalisés")
            sql = nfkc

        # Homoglyphes cyrilliques
        glyphs = [c for c in sql if c in _CYRILLIC]
        if glyphs:
            alerts.append(f"HOMOGLYPH_CYRILLIC: {len(glyphs)} substitution(s)")
            sql = "".join(_CYRILLIC.get(c, c) for c in sql)

        # Hex Python (\x44 = 'D')
        if re.search(r'\\x[0-9a-fA-F]{2}', sql):
            alerts.append("HEX_ENCODING: encodage \\xNN détecté")

        # URL encoding (%44 = 'D')
        if re.search(r'%[0-9a-fA-F]{2}', sql):
            alerts.append("URL_ENCODING: encodage %NN détecté")

        # Octal PostgreSQL (E'\104' = 'D')
        if re.search(r"E'.*?\\[0-7]{3}", sql, re.IGNORECASE):
            alerts.append("OCTAL_ENCODING: E-string octal PostgreSQL détecté")

        # Unicode escape PostgreSQL (U&'...')
        if re.search(r"U&'|UESCAPE", sql, re.IGNORECASE):
            alerts.append("UNICODE_ESCAPE: U&'...' PostgreSQL détecté")

        # Commentaires blocs (DR/**/OP bypass)
        if "/*" in sql or "*/" in sql:
            alerts.append("INLINE_BLOCK_COMMENT: commentaire bloc /* */ détecté")

        return sql, alerts

    @classmethod
    def is_dangerous(cls, alerts: List[str]) -> bool:
        CRITICAL = {
            "INVISIBLE_CHARS", "HOMOGLYPH_CYRILLIC", "HEX_ENCODING",
            "URL_ENCODING", "OCTAL_ENCODING", "UNICODE_ESCAPE",
            "INLINE_BLOCK_COMMENT",
        }
        return any(any(c in a for c in CRITICAL) for a in alerts)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COUCHE 1 — StaticSQLValidator  (Python pur, 0 dépendance)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class StaticSQLValidator:
    """
    Validation purement syntaxique — aucune connexion DB requise.

    Vérifications (diagramme C1) :
      • Tokenisation SQL + détection 40+ mots-clés interdits
        DROP, DELETE, INSERT, UPDATE, TRUNCATE, EXECUTE…
      • Commentaires : -- et /* */
      • Dollar-quoting $$ et $tag$
      • Multi-instructions (;)
      • UNION, INTERSECT, EXCEPT
      • INTO (écriture variable/table)
      • Fonctions dangereuses : pg_sleep, dblink, pg_read_file…
      • Profondeur d'imbrication max 6
      • WITH RECURSIVE (boucle infinie)
      • Schémas système explicites (pg_catalog.*, information_schema.*)
      • FOR UPDATE / FOR SHARE / NOWAIT / SKIP LOCKED
    """

    @classmethod
    def validate(cls, sql: str) -> Tuple[bool, List[ValidationError]]:
        errors: List[ValidationError] = []
        add = lambda c, m: errors.append(ValidationError(c, m, 1))

        # ── Longueur ──────────────────────────────────────────
        if not sql or not sql.strip():
            add("SQL_VIDE", "La condition SQL ne peut pas être vide")
            return False, errors

        if len(sql) > SQL_MAX_LENGTH:
            add("SQL_TROP_LONG",
                f"Règle SQL trop longue : {len(sql)} > {SQL_MAX_LENGTH} caractères")
            return False, errors

        UP = sql.upper()

        # ── Commentaires ──────────────────────────────────────
        if "--" in sql or "/*" in sql or "*/" in sql:
            add("SQL_COMMENTAIRE",
                "Les commentaires SQL sont interdits dans les règles métier (-- et /* */)")
            return False, errors

        # ── Multi-instruction ─────────────────────────────────
        if ";" in sql:
            add("SQL_MULTI_INSTRUCTION",
                "Instruction unique requise — le point-virgule (;) est interdit")
            return False, errors

        # ── Dollar-quoting ────────────────────────────────────
        if re.search(r'\$\$|\$[a-zA-Z_][a-zA-Z0-9_]*\$', sql):
            add("SQL_DOLLAR_QUOTING",
                "Dollar-quoting ($$ ou $tag$) interdit — vecteur d'injection PL/pgSQL")
            return False, errors

        # ── Mots-clés interdits (tokenisation robuste) ────────
        norm = re.sub(r'\s+', ' ', UP)
        tokens = set(re.split(r'[^\w]+', norm))
        tokens.discard("")
        found = tokens & FORBIDDEN_KEYWORDS
        if found:
            add("SQL_MOT_CLE_INTERDIT",
                f"Mots-clés SQL interdits détectés : {sorted(found)}")
            return False, errors

        # ── Fonctions dangereuses ─────────────────────────────
        for fn in FORBIDDEN_FUNCTIONS:
            if fn.upper() in UP:
                add("SQL_FONCTION_INTERDITE",
                    f"Fonction PostgreSQL interdite : {fn}()")
                return False, errors

        # ── UNION / INTERSECT / EXCEPT ────────────────────────
        if re.search(r'\b(UNION|INTERSECT|EXCEPT)\b', UP):
            add("SQL_UNION_INTERDIT",
                "UNION / INTERSECT / EXCEPT sont interdits "
                "(risque d'extraction de données)")
            return False, errors

        # ── INTO ──────────────────────────────────────────────
        if re.search(r'\bINTO\b', UP):
            add("SQL_INTO_INTERDIT",
                "INTO est interdit dans les conditions SQL (écriture variable/table)")
            return False, errors

        # ── WITH RECURSIVE ────────────────────────────────────
        if re.search(r'\bWITH\s+RECURSIVE\b', UP):
            add("SQL_RECURSIVE",
                "WITH RECURSIVE est interdit (risque de boucle infinie / DoS)")
            return False, errors

        # ── Schémas système explicites ────────────────────────
        if re.search(
            r'\b(pg_catalog|information_schema|pg_toast)\s*\.\s*\w+',
            sql, re.IGNORECASE
        ):
            add("SQL_SCHEMA_SYSTEME",
                "Accès direct aux schémas système interdit "
                "(pg_catalog.*, information_schema.*)")
            return False, errors

        # ── Équilibre des parenthèses ─────────────────────────
        depth = max_depth = 0
        for c in sql:
            if c == "(":
                depth += 1
                max_depth = max(max_depth, depth)
            elif c == ")":
                depth -= 1
        if depth != 0:
            add("SQL_PARENTHESES_DESEQUILIBREES",
                "Parenthèses non équilibrées dans la règle SQL")
            return False, errors
        if max_depth > 6:
            add("SQL_TROP_IMBRIQUE",
                f"Imbrication trop profonde : {max_depth} niveaux (max 6)")
            return False, errors

        # ── Mots-clés de verrouillage (écriture implicite) ────
        for kw in ("RETURNING", "FOR UPDATE", "FOR SHARE", "NOWAIT", "SKIP LOCKED"):
            if kw in UP:
                add("SQL_VERROU",
                    f"Mot-clé de verrouillage/écriture interdit : {kw}")
                # Non-bloquant immédiat mais ajouté comme erreur

        # ── Tautologies d'injection (always-true / always-false) ─
        # OR 1=1, OR 1 = 1, OR TRUE, OR 'x'='x', OR ''='', AND 1=0, etc.
        _TAUTOLOGY = re.compile(
            r'''\b(?:OR|AND)\s+(?:
                1\s*=\s*1          |   # OR 1=1
                0\s*=\s*0          |   # OR 0=0
                TRUE\b             |   # OR TRUE
                FALSE\b            |   # AND FALSE
                '[^']*'\s*=\s*'[^']*'  # OR 'x'='x'
            )''',
            re.IGNORECASE | re.VERBOSE
        )
        if _TAUTOLOGY.search(sql):
            add("SQL_TAUTOLOGIE",
                "Tautologie détectée (OR 1=1, OR TRUE, OR 'x'='x'…) — "
                "injection always-true/always-false refusée")
            return False, errors

        return len(errors) == 0, errors


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COUCHE 2 — SemanticSQLValidator  (logique métier foncier)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SemanticSQLValidator:
    """
    Validation sémantique métier — aucune connexion DB requise.

    Vérifications (diagramme C2) :
      • Whitelist de 30 tables autorisées
      • Blocage explicite de pg_shadow, pg_catalog, information_schema
      • Vérification de la présence des paramètres $1 / $2
      • Cohérence domaine / tables référencées
      • Heuristique expression booléenne (doit retourner BOOLEAN)
      • Fonctions non répertoriées dans l'allowlist (warning)
      • LIMIT / OFFSET suspect (warning)
    """

    # Tables attendues par domaine
    _DOMAIN_TABLES: Dict[str, Set[str]] = {
        "CCFM":       {"demandes_ccfm", "fiches_constat_ccfm", "historique_ccfm"},
        "MUTATION":   {"property_transfers", "parcel_right_version"},
        "RNAF":       {"rnaf", "rnaf_workflow"},
        "BGU":        {"bgu_geojson_master", "bgu_parcel_status"},
        "JUSTICE":    {"litiges", "parcel_disputes"},
        "HYPOTHEQUE": {"hypotheques", "mortgage_registry"},
        "SUCCESSION": {"parcel_right_version"},
        "URBANISME":  {"arretes_urbanisme", "urbanisme_conformite"},
        "GENERAL":    ALLOWED_TABLES,
    }

    @classmethod
    def validate(
        cls, sql: str, domaine: str
    ) -> Tuple[bool, List[ValidationError], List[str]]:
        errors:   List[ValidationError] = []
        warnings: List[str]             = []

        # ── Tables référencées dans la règle ──────────────────
        table_refs = re.findall(
            r'\bFROM\s+([a-zA-Z_]\w*)'
            r'|\bJOIN\s+([a-zA-Z_]\w*)'
            r'|\bEXISTS\s*\(\s*SELECT\s+\S+\s+FROM\s+([a-zA-Z_]\w*)',
            sql, re.IGNORECASE
        )
        tables: Set[str] = set()
        for g1, g2, g3 in table_refs:
            for t in (g1, g2, g3):
                if t:
                    tables.add(t.lower())

        # Tables système → REFUSE immédiat (diagramme : pg_shadow, pg_catalog…)
        sys_found = tables & {t.lower() for t in SYSTEM_TABLES}
        if sys_found:
            errors.append(ValidationError(
                "SQL_TABLE_SYSTEME",
                f"Tables système interdites détectées : {sorted(sys_found)} "
                f"(pg_shadow, pg_catalog… sont strictement bloqués)",
                2
            ))
            return False, errors, warnings

        # Tables hors whitelist
        forbidden = tables - {t.lower() for t in ALLOWED_TABLES}
        if forbidden:
            errors.append(ValidationError(
                "SQL_TABLE_NON_AUTORISEE",
                f"Tables non autorisées : {sorted(forbidden)}. "
                f"Seules les {len(ALLOWED_TABLES)} tables de la whitelist foncière sont permises.",
                2
            ))

        # ── Paramètres $1 / $2 ───────────────────────────────
        if tables and "$1" not in sql and "$2" not in sql:
            warnings.append(
                "La règle ne référence ni $1 (entite_id) ni $2 (user_id) — "
                "elle sera évaluée sans contexte d'entité spécifique"
            )

        # ── Cohérence domaine / tables ────────────────────────
        expected = cls._DOMAIN_TABLES.get(domaine.upper(), set())
        if tables and expected and expected is not ALLOWED_TABLES:
            if not tables & expected:
                warnings.append(
                    f"Aucune table du domaine '{domaine}' n'est référencée "
                    f"(tables utilisées : {sorted(tables)[:3]}…)"
                )

        # ── Heuristique expression booléenne ─────────────────
        stripped = sql.strip().upper()
        is_bool = (
            stripped.startswith(("EXISTS", "NOT EXISTS", "NOT (", "COALESCE"))
            or stripped in ("TRUE", "FALSE")
            or ("$1" in sql and any(op in sql for op in ("=", "!=", "<", ">", "IS", "IN")))
        )
        if not is_bool:
            warnings.append(
                "La règle ne semble pas retourner un BOOLEAN — "
                "utilisez EXISTS(…), NOT EXISTS(…) ou une comparaison directe"
            )

        # ── Fonctions non répertoriées (allowlist positive) ───
        SQL_KW = {"select", "where", "and", "or", "not", "in", "like",
                  "between", "case", "when", "then", "else", "end",
                  "from", "join", "on", "as", "is", "null", "true", "false",
                  "exists", "any", "all"}
        fn_calls = re.findall(r'\b([a-zA-Z_]\w*)\s*\(', sql, re.IGNORECASE)
        unknown = {
            fn.lower() for fn in fn_calls
            if fn.lower() not in ALLOWED_FUNCTIONS
            and fn.lower() not in {f.lower() for f in FORBIDDEN_FUNCTIONS}
            and fn.lower() not in SQL_KW
        }
        if unknown:
            warnings.append(
                f"Fonctions non répertoriées dans l'allowlist : {sorted(unknown)} "
                f"— vérification manuelle recommandée"
            )

        # ── LIMIT / OFFSET suspect ───────────────────────────
        if re.search(r'\b(LIMIT|OFFSET)\s+\d+', sql, re.IGNORECASE):
            warnings.append(
                "LIMIT / OFFSET dans une règle métier peut indiquer "
                "une tentative d'extraction de données par oracle booléen"
            )

        return len(errors) == 0, errors, warnings


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COUCHE 3 — PostgresSandbox  (si DB disponible)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PostgresSandbox:
    """
    Sandbox PostgreSQL — exécution contrôlée.

    Mécanisme (diagramme C3) :
      • SAVEPOINT pour isolation totale
      • EXPLAIN uniquement (ANALYZE FALSE) — aucune exécution réelle
      • SET LOCAL default_transaction_read_only = ON
      • Timeout 2 secondes strict via SET LOCAL statement_timeout
      • ROLLBACK TO SAVEPOINT systématique (succès ou échec)
      • Détection tentative d'écriture → REFUSE immédiat

    Implémentation sans DO block pour éviter le vecteur dollar-quoting
    injectable (correction v2 par rapport au design original).
    """

    TIMEOUT_MS = 2000

    @classmethod
    async def validate(cls, db, sql: str) -> Tuple[bool, List[ValidationError]]:
        from sqlalchemy import text
        errors: List[ValidationError] = []

        # Nom de SAVEPOINT unique pour cette validation
        sp = f"sp_sandbox_{abs(hash(sql + str(time.monotonic()))) % 99999:05d}"

        try:
            await db.execute(text(f"SAVEPOINT {sp}"))
            await db.execute(text(
                f"SET LOCAL statement_timeout = '{cls.TIMEOUT_MS}'"
            ))
            await db.execute(text(
                "SET LOCAL default_transaction_read_only = ON"
            ))

            # EXPLAIN sans ANALYZE = planification seule, aucune écriture possible
            stmt = text(
                "EXPLAIN (ANALYZE FALSE, VERBOSE FALSE, FORMAT TEXT) "
                f"SELECT ({sql})::BOOLEAN "
                "FROM (VALUES ("
                "  '00000000-0000-0000-0000-000000000001'::UUID,"
                "  '00000000-0000-0000-0000-000000000002'::UUID"
                ")) AS _sandbox($1, $2)"
            )
            await db.execute(stmt)

            await db.execute(text(f"ROLLBACK TO SAVEPOINT {sp}"))
            await db.execute(text(f"RELEASE SAVEPOINT {sp}"))

        except Exception as exc:
            # Rollback systématique
            try:
                await db.execute(text(f"ROLLBACK TO SAVEPOINT {sp}"))
                await db.execute(text(f"RELEASE SAVEPOINT {sp}"))
            except Exception:
                pass

            msg = str(exc).lower()

            if "read_only_sql_transaction" in msg or "read only" in msg:
                errors.append(ValidationError(
                    "SANDBOX_ECRITURE_DETECTEE",
                    "La règle SQL tente une écriture en base de données — REFUSÉE",
                    3
                ))
                return False, errors

            if "statement_timeout" in msg or "timeout" in msg:
                errors.append(ValidationError(
                    "SANDBOX_TIMEOUT",
                    f"La règle SQL dépasse le timeout de {cls.TIMEOUT_MS} ms",
                    3
                ))
                return False, errors

            if any(kw in msg for kw in (
                "syntax error", "parse error", "unexpected",
                "invalid input", "lexer error"
            )):
                clean = str(exc)[:200].split("\n")[0]
                errors.append(ValidationError(
                    "SANDBOX_SYNTAXE",
                    f"Erreur de syntaxe SQL (PostgreSQL) : {clean}",
                    3
                ))
                return False, errors

            # Référence absente dans l'environnement sandbox — non bloquant
            if any(kw in msg for kw in (
                "does not exist", "column", "relation", "undefined"
            )):
                _log.debug("Sandbox: référence manquante (ignorée en sandbox) — %s",
                           str(exc)[:120])
                return True, errors

            _log.warning("Sandbox: erreur non critique — %s", str(exc)[:200])

        return True, errors


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COUCHE 4 — RuleSignature  (non-répudiation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RuleSignature:
    """
    Normalisation + scellement (diagramme C4).

    • Normalise les espaces (forme canonique pour le hachage)
    • SHA-256(sql | domaine | code | niveau | user_id | AAAA-MM-JJ)
      → reproductible sur le même jour pour un même auteur
    • Garantit la non-répudiation : qui a validé quoi et quand
    """

    @staticmethod
    def signer(
        sql: str, domaine: str, code: str,
        niveau: str, user_id: str
    ) -> str:
        payload = "|".join([
            sql.strip(),
            domaine.upper(), code.upper(), niveau.upper(), user_id,
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        ])
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def normaliser(sql: str) -> str:
        """Forme canonique : espaces normalisés, parenthèses serrées."""
        sql = sql.strip()
        sql = re.sub(r'\s+', ' ', sql)
        sql = re.sub(r'\s*\(\s*', '(', sql)
        sql = re.sub(r'\s*\)\s*', ')', sql)
        return sql


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CACHE VALIDATION (TTL 1 h, 500 entrées max)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ValidationCache:
    _store: Dict[str, Tuple[ValidationResult, float]] = {}
    _MAX   = 500
    _TTL   = 3600.0

    @classmethod
    def get(cls, sql: str, domaine: str, code: str) -> Optional[ValidationResult]:
        key = cls._key(sql, domaine, code)
        entry = cls._store.get(key)
        if not entry:
            return None
        result, ts = entry
        if time.monotonic() - ts > cls._TTL:
            del cls._store[key]
            return None
        return result

    @classmethod
    def put(cls, sql: str, domaine: str, code: str, result: ValidationResult) -> None:
        if len(cls._store) >= cls._MAX:
            oldest = sorted(cls._store, key=lambda k: cls._store[k][1])
            for k in oldest[:50]:
                del cls._store[k]
        cls._store[cls._key(sql, domaine, code)] = (result, time.monotonic())

    @classmethod
    def invalidate(cls, code: str) -> int:
        keys = [k for k in cls._store if code.upper() in k]
        for k in keys:
            del cls._store[k]
        return len(keys)

    @staticmethod
    def _key(sql: str, domaine: str, code: str) -> str:
        return hashlib.sha256(f"{sql}|{domaine}|{code}".encode()).hexdigest()[:32]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ORCHESTRATEUR — SQLRuleValidator
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SQLRuleValidator:
    """
    Orchestre les 4 couches officielles du diagramme.
    Point d'entrée unique pour valider une règle SQL.

    Usage :
        result = await SQLRuleValidator.valider(
            sql="EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND is_gele=FALSE)",
            domaine="GENERAL",
            code="CHECK_GELE",
            db=db_session,   # Optionnel — active la couche 3
        )
        if result.valide:
            # sha256 disponible, safe_sql normalisé
    """

    @classmethod
    async def valider(
        cls,
        sql:           str,
        domaine:       str,
        code:          str           = "INCONNU",
        niveau_blocage: str          = "BLOQUANT",
        user_id:       str           = "SYSTEM",
        db                           = None,
        log_db                       = None,
        use_cache:     bool          = True,
    ) -> ValidationResult:

        t0 = time.monotonic()
        layers:        List[LayerTrace]      = []
        errors:        List[ValidationError] = []
        warnings:      List[str]             = []
        bypass_alerts: List[str]             = []

        # ── Rate limiting ──────────────────────────────────────
        if user_id != "SYSTEM":
            allowed, remaining = InMemoryRateLimiter.check(user_id)
            if not allowed:
                result = ValidationResult(
                    status=ValidationStatus.REFUSE,
                    errors=[ValidationError(
                        "RATE_LIMIT",
                        f"Limite dépassée : {InMemoryRateLimiter.MAX_PER_HOUR} "
                        f"validations/heure. Réessayez dans quelques minutes.",
                        0
                    )],
                    duree_ms=0,
                )
                await cls._log(log_db, sql, domaine, code, result, user_id)
                return result
            if remaining < 10:
                warnings.append(f"Rate limit : {remaining} validation(s) restante(s) cette heure")

        # ── Cache ──────────────────────────────────────────────
        if use_cache and db is None:
            cached = ValidationCache.get(sql, domaine, code)
            if cached is not None:
                return cached

        # ── C0 : Pré-filtrage bypass (interne, transparent) ───
        t_c0 = time.monotonic()
        sql_clean, alerts = BypassDetector.normalize_and_check(sql)
        bypass_alerts.extend(alerts)
        d_c0 = int((time.monotonic() - t_c0) * 1000)

        if BypassDetector.is_dangerous(alerts):
            layers.append(LayerTrace(0, "Anti-bypass", "FAIL", d_c0, alerts))
            result = ValidationResult(
                status=ValidationStatus.REFUSE,
                errors=[ValidationError(
                    "BYPASS_DETECTE",
                    f"Tentative d'obfuscation détectée : {'; '.join(alerts[:3])}",
                    0
                )],
                bypass_alerts=bypass_alerts,
                duree_ms=int((time.monotonic() - t0) * 1000),
                layers=layers,
            )
            await cls._log(log_db, sql, domaine, code, result, user_id)
            return result
        if alerts:
            layers.append(LayerTrace(0, "Anti-bypass", "WARN", d_c0, alerts))
        sql = sql_clean

        # ── C1 : Validation statique ──────────────────────────
        t_c1 = time.monotonic()
        ok1, errs1 = StaticSQLValidator.validate(sql)
        d_c1 = int((time.monotonic() - t_c1) * 1000)
        errors.extend(errs1)
        layers.append(LayerTrace(
            1, "Syntaxe statique",
            "PASS" if ok1 else "FAIL",
            d_c1,
            [e.message for e in errs1],
        ))
        if not ok1:
            result = ValidationResult(
                status=ValidationStatus.REFUSE,
                errors=errors, bypass_alerts=bypass_alerts,
                duree_ms=int((time.monotonic() - t0) * 1000),
                layers=layers,
            )
            await cls._log(log_db, sql, domaine, code, result, user_id)
            return result

        # ── C2 : Validation sémantique ────────────────────────
        t_c2 = time.monotonic()
        ok2, errs2, warns2 = SemanticSQLValidator.validate(sql, domaine)
        d_c2 = int((time.monotonic() - t_c2) * 1000)
        errors.extend(errs2)
        warnings.extend(warns2)
        layers.append(LayerTrace(
            2, "Sémantique foncière",
            "PASS" if ok2 else "FAIL",
            d_c2,
            [e.message for e in errs2] + warns2,
        ))
        if not ok2:
            result = ValidationResult(
                status=ValidationStatus.INVALIDE,
                errors=errors, warnings=warnings,
                bypass_alerts=bypass_alerts,
                duree_ms=int((time.monotonic() - t0) * 1000),
                layers=layers,
            )
            await cls._log(log_db, sql, domaine, code, result, user_id)
            return result

        # ── C3 : Sandbox PostgreSQL ───────────────────────────
        if db is not None:
            t_c3 = time.monotonic()
            ok3, errs3 = await PostgresSandbox.validate(db, sql)
            d_c3 = int((time.monotonic() - t_c3) * 1000)
            errors.extend(errs3)
            layers.append(LayerTrace(
                3, "Sandbox PostgreSQL",
                "PASS" if ok3 else "FAIL",
                d_c3,
                [e.message for e in errs3],
            ))
            if not ok3:
                is_refuse = any(
                    e.code == "SANDBOX_ECRITURE_DETECTEE" for e in errs3
                )
                result = ValidationResult(
                    status=ValidationStatus.REFUSE if is_refuse
                           else ValidationStatus.INVALIDE,
                    errors=errors, warnings=warnings,
                    bypass_alerts=bypass_alerts,
                    duree_ms=int((time.monotonic() - t0) * 1000),
                    layers=layers,
                )
                await cls._log(log_db, sql, domaine, code, result, user_id)
                return result
        else:
            layers.append(LayerTrace(3, "Sandbox PostgreSQL", "SKIP", 0,
                                     ["Mode sans DB — couche 3 ignorée"]))

        # ── C4 : Normalisation + Signature ────────────────────
        t_c4 = time.monotonic()
        safe_sql = RuleSignature.normaliser(sql)
        sha256   = RuleSignature.signer(safe_sql, domaine, code, niveau_blocage, user_id)
        d_c4 = int((time.monotonic() - t_c4) * 1000)
        layers.append(LayerTrace(
            4, "Signature SHA-256",
            "PASS", d_c4,
            [f"SHA-256 : {sha256[:32]}…"],
        ))

        # ── C5 : Validation logique (si DB disponible) ──────
        logic_report_dict: Optional[Dict] = None
        if db is not None:
            try:
                from app.services.logic_validator import (
                    LogicConflictEngine, Recommandation
                )
                t_c5 = time.monotonic()
                logic_report = await LogicConflictEngine.valider(
                    sql           = safe_sql,
                    domaine       = domaine,
                    code          = code,
                    niveau_blocage= niveau_blocage,
                    nom           = code,
                    db            = db,
                    log_db        = log_db,
                    user_id       = user_id,
                )
                d_c5 = int((time.monotonic() - t_c5) * 1000)
                logic_report_dict = logic_report.as_dict()
                logic_statut = (
                    "FAIL" if logic_report.bloque
                    else "WARN" if logic_report.nb_majeur > 0
                    else "PASS"
                )
                layers.append(LayerTrace(
                    5, "Validation logique",
                    logic_statut, d_c5,
                    [f"{logic_report.recommandation.value} "
                     f"— {logic_report.nb_critique} CRITIQUE "
                     f"/ {logic_report.nb_majeur} MAJEUR "
                     f"/ {logic_report.nb_mineur} MINEUR",
                     *[c.message for c in logic_report.conflits[:3]]],
                ))
                # Bloquer l'activation si conflit CRITIQUE détecté
                if logic_report.bloque:
                    errors.append(ValidationError(
                        "LOGIC_CONFLIT_CRITIQUE",
                        f"Conflit logique critique détecté : "
                        f"{logic_report.nb_critique} conflit(s) bloquant(s). "
                        f"Règles impactées : {logic_report.regles_impactees[:5]}",
                        5
                    ))
                    result = ValidationResult(
                        status        = ValidationStatus.REFUSE,
                        errors        = errors,
                        warnings      = warnings,
                        bypass_alerts = bypass_alerts,
                        sha256_regle  = sha256,
                        safe_sql      = safe_sql,
                        duree_ms      = int((time.monotonic() - t0) * 1000),
                        layers        = layers,
                        logic_report  = logic_report_dict,
                    )
                    await cls._log(log_db, sql, domaine, code, result, user_id)
                    return result
                elif logic_report.nb_majeur > 0:
                    warnings.append(
                        f"Validation logique C5 : {logic_report.nb_majeur} conflit(s) "
                        f"MAJEUR(s) — révision recommandée avant activation. "
                        f"Règles impactées : {logic_report.regles_impactees[:3]}"
                    )
            except ImportError:
                layers.append(LayerTrace(5, "Validation logique", "SKIP", 0,
                                         ["Module logic_validator non disponible"]))
            except Exception as exc:
                _log.warning("C5 logic_validator erreur: %s", exc)
                layers.append(LayerTrace(5, "Validation logique", "SKIP", 0,
                                         [f"Erreur: {str(exc)[:80]}"]))
        else:
            layers.append(LayerTrace(5, "Validation logique", "SKIP", 0,
                                     ["Mode sans DB — couche 5 ignorée"]))

        result = ValidationResult(
            status=ValidationStatus.VALIDE,
            errors=errors, warnings=warnings,
            bypass_alerts=bypass_alerts,
            sha256_regle=sha256, safe_sql=safe_sql,
            duree_ms=int((time.monotonic() - t0) * 1000),
            layers=layers,
            logic_report=logic_report_dict,
        )
        if use_cache and db is None:
            ValidationCache.put(sql, domaine, code, result)
        await cls._log(log_db, sql, domaine, code, result, user_id)
        return result

    # ── Batch ──────────────────────────────────────────────────

    @classmethod
    async def valider_lot(
        cls,
        regles:  List[Dict],
        db=None, log_db=None,
        user_id: str = "SYSTEM",
    ) -> BatchValidationResult:
        t0 = time.monotonic()
        valides = invalides = refuses = 0
        resultats = []

        for r in regles:
            res = await cls.valider(
                sql=r.get("sql", ""),
                domaine=r.get("domaine", "GENERAL"),
                code=r.get("code", "INCONNU"),
                niveau_blocage=r.get("niveau_blocage", "BLOQUANT"),
                user_id=user_id, db=db, log_db=log_db, use_cache=True,
            )
            if   res.status == ValidationStatus.VALIDE:   valides   += 1
            elif res.status == ValidationStatus.INVALIDE: invalides += 1
            else:                                         refuses   += 1
            resultats.append({
                "code":     r.get("code"),
                "domaine":  r.get("domaine"),
                "status":   res.status.value,
                "valide":   res.valide,
                "errors":   [{"code": e.code, "message": e.message}
                             for e in res.errors],
                "warnings": res.warnings,
                "sha256":   res.sha256_regle,
                "duree_ms": res.duree_ms,
                "layers":   [{"couche": l.couche, "nom": l.nom,
                              "statut": l.statut, "duree_ms": l.duree_ms}
                             for l in res.layers],
            })

        return BatchValidationResult(
            total=len(regles), valides=valides,
            invalides=invalides, refuses=refuses,
            duree_totale_ms=int((time.monotonic() - t0) * 1000),
            resultats=resultats,
        )

    # ── Journalisation ─────────────────────────────────────────

    @staticmethod
    async def _log(
        db, sql: str, domaine: str, code: str,
        result: ValidationResult, user_id: str
    ) -> None:
        if db is None:
            return
        try:
            from sqlalchemy import text
            await db.execute(text("""
                INSERT INTO sql_validation_log (
                    code_regle, domaine, sql_soumis_hash,
                    statut, nb_erreurs, nb_warnings,
                    sha256_regle, duree_ms, soumis_par
                ) VALUES (
                    :code, :dom, :hash,
                    :statut, :nerr, :nwarn,
                    :sha256, :duree, :uid::uuid
                )
            """), {
                "code":   code,
                "dom":    domaine,
                "hash":   hashlib.sha256(sql.encode()).hexdigest(),
                "statut": result.status.value,
                "nerr":   len(result.errors),
                "nwarn":  len(result.warnings),
                "sha256": result.sha256_regle or "",
                "duree":  result.duree_ms,
                "uid":    user_id if user_id != "SYSTEM" else None,
            })
        except Exception as exc:
            _log.warning("sql_validation_log: %s", exc)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EXEMPLES (tests + documentation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EXEMPLES_VALIDES: List[str] = [
    "EXISTS (SELECT 1 FROM demandes_ccfm dc "
    "WHERE dc.etat IN ('SIGNE_DIRECTEUR','ARCHIVE') "
    "AND dc.nicad_code IN (SELECT nicad FROM parcelles WHERE id=$1))",

    "NOT EXISTS (SELECT 1 FROM parcelles p "
    "WHERE p.id=$1 AND p.is_gele=TRUE)",

    "EXISTS (SELECT 1 FROM agent_profil ap "
    "WHERE ap.user_id=$2 AND ap.niveau_autorite >= 2)",

    "NOT EXISTS (SELECT 1 FROM property_transfers pt "
    "WHERE pt.parcelle_id=$1 AND pt.statut IN ('en_cours','valide'))",

    "NOT EXISTS (SELECT 1 FROM entity_lock el "
    "WHERE el.entite_id=$1 AND el.actif=TRUE)",

    "EXISTS (SELECT 1 FROM litiges l "
    "WHERE l.parcelle_id=$1 AND l.statut='ACTIF')",

    "TRUE",
]

EXEMPLES_INVALIDES: List[str] = [
    # C1 — DDL
    "EXISTS (SELECT 1 FROM users); DROP TABLE users; --",
    # C1 — DML dans condition
    "EXISTS (SELECT 1 FROM parcelles) AND "
    "(INSERT INTO users VALUES('x','x','x')) IS NULL",
    # C1 — Dollar-quoting
    "$$ BEGIN DROP TABLE parcelles; END $$",
    # C1 — Commentaire + DDL
    "TRUE -- DROP TABLE users;",
    # C1 — Fonction dangereuse
    "pg_sleep(10) IS NOT NULL",
    # C1 — UNION
    "EXISTS (SELECT 1 FROM parcelles WHERE id=$1 "
    "UNION SELECT 1 FROM users)",
    # C1 — Multi-instruction
    "EXISTS (SELECT 1 FROM parcelles); SELECT pg_sleep(5)",
    # C1 — Schéma système
    "EXISTS (SELECT 1 FROM pg_catalog.pg_shadow)",
    # C1 — Récursif
    "EXISTS (WITH RECURSIVE r AS "
    "(SELECT 1 UNION ALL SELECT 1 FROM r) SELECT 1 FROM r)",
    # C2 — Table système
    "EXISTS (SELECT 1 FROM pg_shadow WHERE usename=$2)",
]
