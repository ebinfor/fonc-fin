"""
FONCIER+ — ScopeFilter : Filtrage contextuel par acteur
=========================================================
Principe : chaque acteur ne voit QUE ce qui le concerne.

Règles par rôle :
  ADMIN / AUDITEUR / DIRECTEUR_* national  → tout (pas de filtre)
  Rôles régionaux (CHEF_URBANISME, AGENT_URBANISME, DIRECTEUR_CADASTRE...)
      → filtrés par region de l'agent (agent_profil.region_principale)
  Rôles liés à une entité (NOTAIRE, BANQ_DIRECTEUR, JUGE_FONCIER...)
      → filtrés par entité (notaire_id, banque_id, tgi_id)
  Rôles opérationnels (GUICHETIER_CCFM, AGENT_DOMAINE, TOPOGRAPHE...)
      → filtrés par enregistre_par / agent_id

Utilisation dans un endpoint :
    scope = ScopeFilter.from_user(current_user)
    q, p  = scope.apply(base_sql, params, table_alias="c")
    r     = await db.execute(text(q), p)
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

# Rôles qui voient TOUT (supervision nationale)
ROLES_VISION_NATIONALE = frozenset({
    "ADMIN",
    "AUDITEUR",
    "MINISTRE_URBANISME",
    "RESPONSABLE_ANNF",
    "RESPONSABLE_BGU",
})

# Rôles filtrés par RÉGION de l'agent
ROLES_FILTRES_REGION = frozenset({
    "AGENT_URBANISME",
    "CHEF_URBANISME",
    "DIRECTEUR_URBANISME",
    "DIRECTEUR_CADASTRE",
    "CHEF_SERVICE_CADASTRE",
    "SECRETARIAT_CADASTRE",
    "AGENT_COMMUNE",
    "ADMIN_COMMUNE",
    "MAIRE",
    "GEOMETRE",
    "TOPOGRAPHE",
    "AGENT_DOMAINE",
    "DIRECTEUR_DOMAINE",
    "EDITEUR_JO",
    "CHEF_CCFM",
    "GUICHETIER_CCFM",
    "SECRETAIRE_CCFM",
    "ARCHIVISTE_ANNF",
})

# Rôles filtrés par ENTITÉ PROPRE (leur structure)
ROLES_FILTRES_ENTITE = frozenset({
    "NOTAIRE",
    "BANQ_DIRECTEUR",
    "BANQ_AGENT",
    "JUGE_FONCIER",
    "GREFFIER",
    "HUISSIER",
})

# Colonnes de région selon le contexte de la table
_REGION_COLUMNS = {
    # table_alias → colonne région dans la table
    "default":              "region",
    "c":                    "localite",   # demandes_ccfm : localite ≈ région
    "dc":                   "localite",
    "p":                    "region",     # parcelles
    "r":                    "region",     # rnaf
    "wi":                   "region",     # workflow_instances
    "exp":                  "region",     # expropriations
    "lit":                  "region",     # litiges
    "bgu":                  "region",     # bgu_parcel_status
    "conf":                 "region",     # conflits_parcellaires
}


@dataclass
class ScopeFilter:
    """
    Encapsule les règles de filtrage d'un acteur.
    Créer via ScopeFilter.from_user(current_user).
    """
    role:              str
    user_id:           str
    region:            Optional[str]    = None
    entite_id:         Optional[str]    = None   # notaire_id, banque_id, tgi_id
    niveau_autorite:   int              = 1
    peut_voir_national: bool            = False

    # ─── Construction ────────────────────────────────────────────

    @classmethod
    def from_user(cls, current_user) -> "ScopeFilter":
        """
        Construit un ScopeFilter à partir du dict current_user
        retourné par get_current_user() / require_role().
        Compatible avec dict (mappings SQLAlchemy) et objets.
        """
        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        role    = _get(current_user, "role", "AGENT")
        uid     = str(_get(current_user, "id", ""))
        region  = _get(current_user, "region")
        entite  = _get(current_user, "entite_id")  # ex: notaire_id dans le token
        niveau  = int(_get(current_user, "niveau_autorite", 1))

        # Vision nationale : admins + auditeurs + niveau >= 4
        national = (role in ROLES_VISION_NATIONALE or niveau >= 4)

        return cls(
            role=role,
            user_id=uid,
            region=region if region not in (None, "", "NATIONAL") else None,
            entite_id=entite,
            niveau_autorite=niveau,
            peut_voir_national=national,
        )

    # ─── Application du filtre ───────────────────────────────────

    def apply(
        self,
        sql: str,
        params: dict,
        table_alias: str = "default",
        col_region: Optional[str] = None,
        col_agent:  Optional[str] = None,
        col_entite: Optional[str] = None,
    ) -> tuple[str, dict]:
        """
        Injecte les clauses WHERE de filtrage dans une requête SQL.

        Paramètres :
            sql           : requête SQL avec placeholders nommés (":x")
            params        : dict des paramètres existants
            table_alias   : alias de la table principale (ex: "c", "lit")
            col_region    : colonne region dans la table (auto-détectée sinon)
            col_agent     : colonne agent/enregistre_par (ex: "enregistre_par")
            col_entite    : colonne entité propriétaire (ex: "notaire_id")

        Retourne (sql_filtré, params_enrichis).
        """
        if self.peut_voir_national:
            # Admins et superviseurs nationaux : aucun filtre supplémentaire
            return sql, params

        params = dict(params)  # copie défensive
        filters = []

        # ── Filtre RÉGION (pour les rôles régionaux)
        if self.role in ROLES_FILTRES_REGION and self.region:
            rc = col_region or _REGION_COLUMNS.get(table_alias) or _REGION_COLUMNS["default"]
            alias_prefix = f"{table_alias}." if table_alias and table_alias != "default" else ""
            filters.append(f"{alias_prefix}{rc} = :_scope_region")
            params["_scope_region"] = self.region

        # ── Filtre ENTITÉ (notaire voit ses actes, banque ses dossiers…)
        elif self.role in ROLES_FILTRES_ENTITE:
            if self.entite_id and col_entite:
                alias_prefix = f"{table_alias}." if table_alias and table_alias != "default" else ""
                filters.append(f"{alias_prefix}{col_entite} = :_scope_entite")
                params["_scope_entite"] = self.entite_id
            elif col_agent:
                # Fallback : filtrer par agent traitant
                alias_prefix = f"{table_alias}." if table_alias and table_alias != "default" else ""
                filters.append(f"{alias_prefix}{col_agent} = :_scope_agent")
                params["_scope_agent"] = self.user_id

        # ── Filtre AGENT (opérationnels : voient leurs propres dossiers)
        else:
            if col_agent:
                alias_prefix = f"{table_alias}." if table_alias and table_alias != "default" else ""
                filters.append(f"{alias_prefix}{col_agent} = :_scope_agent")
                params["_scope_agent"] = self.user_id

        if not filters:
            return sql, params

        # Injecter dans le WHERE existant
        where_clause = " AND ".join(filters)
        sql = _inject_where(sql, where_clause)
        return sql, params

    def apply_to_query(
        self,
        base_sql: str,
        params: dict,
        **kwargs,
    ) -> tuple[str, dict]:
        """Alias de apply() pour compatibilité."""
        return self.apply(base_sql, params, **kwargs)

    # ─── Helpers de vérification ─────────────────────────────────

    def peut_voir_region(self, region: str) -> bool:
        """True si cet acteur peut voir des données de la région donnée."""
        if self.peut_voir_national:
            return True
        if self.region and self.region == region:
            return True
        return False

    def peut_voir_entite(self, entite_id: str) -> bool:
        """True si cet acteur peut voir les données de l'entité donnée."""
        if self.peut_voir_national:
            return True
        if self.entite_id and self.entite_id == entite_id:
            return True
        return False

    def filtres_actifs(self) -> dict:
        """Retourne les filtres actifs (pour debug/logs)."""
        return {
            "role":              self.role,
            "region":            self.region,
            "entite_id":         self.entite_id,
            "niveau":            self.niveau_autorite,
            "vision_nationale":  self.peut_voir_national,
        }


# ─── Utilitaire SQL ────────────────────────────────────────────

def _inject_where(sql: str, extra_where: str) -> str:
    """
    Injecte extra_where dans la clause WHERE existante de sql.
    Si WHERE absent, l'ajoute. Si présent, préfixe avec AND.
    Gère les sous-requêtes imbriquées avec précaution.
    """
    # Chercher le WHERE de la requête principale (pas dans une sous-requête)
    # Heuristique : premier WHERE après le FROM principal (niveau 0 de parenthèses)
    depth = 0
    where_pos = -1
    order_pos = -1
    limit_pos  = -1

    sql_upper = sql.upper()
    i = 0
    while i < len(sql):
        c = sql[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0:
            tail = sql_upper[i:]
            if re.match(r'^WHERE\b', tail):
                where_pos = i
            elif re.match(r'^ORDER\s+BY\b', tail) and where_pos >= 0:
                order_pos = i
                break
            elif re.match(r'^LIMIT\b', tail) and where_pos >= 0:
                limit_pos = i
                break
        i += 1

    if where_pos >= 0:
        # Insérer juste après WHERE ... (avant ORDER BY / LIMIT)
        insert_at = order_pos if order_pos > 0 else (limit_pos if limit_pos > 0 else len(sql))
        sql = (
            sql[:insert_at].rstrip()
            + f"\n                   AND ({extra_where})"
            + "\n                " + sql[insert_at:].lstrip()
        )
    else:
        # Pas de WHERE : l'ajouter avant ORDER BY / LIMIT / fin
        # Chercher LIMIT ou ORDER BY au niveau 0
        insert_at = len(sql)
        for kw in ["ORDER BY", "LIMIT", "GROUP BY", "HAVING"]:
            m = re.search(r'\b' + kw.replace(" ", r"\s+") + r'\b', sql_upper)
            if m and m.start() < insert_at:
                insert_at = m.start()
        sql = (
            sql[:insert_at].rstrip()
            + f"\n                   WHERE ({extra_where})\n                "
            + sql[insert_at:].lstrip()
        )

    return sql
