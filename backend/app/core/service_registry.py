"""
FONCIER+ — ServiceRegistry
===========================
Permet au main.py monolithique de démarrer en mode
"microservice" en n'activant que les routers appartenant
au service demandé (variable FONCIER_MODULES).

Ce mécanisme est le SEUL point de découplage entre le
monolithe et l'architecture microservices. Aucune logique
métier n'est modifiée — seule la surface exposée change.

Usage dans main.py :
    from app.core.service_registry import ServiceRegistry
    for router in ServiceRegistry.get_routers():
        app.include_router(router, prefix="/v1")
"""
import os
import logging
from typing import List

_log = logging.getLogger("service_registry")


# Mapping module → (import_path, prefix_override)
# prefix_override = None → préfixe déclaré dans le router lui-même
_MODULE_REGISTRY = {
    # ── Auth & Users ──────────────────────────────────────────
    "auth":                  ("app.api.v1.endpoints.auth",             "/v1"),
    "users":                 ("app.api.v1.endpoints.users",            "/v1"),
    "admin":                 ("app.api.v1.endpoints.admin",            "/v1"),

    # ── Chaîne foncière ───────────────────────────────────────
    "rnaf"  # Sous-module Urbanisme → /v1/urbanisme/rnaf/*:                  ("app.api.v1.endpoints.rnaf",             "/v1"),
    "journal_officiel":      ("app.api.v1.endpoints.journal_officiel", "/v1"),
    "cadastre":              ("app.api.v1.endpoints.cadastre",         "/v1"),
    "cadastre_parcellaire":  ("app.api.v1.endpoints.cadastre_parcellaire", "/v1"),
    "rnp"   # Sous-module Cadastre  → /v1/cadastre/rnp/*:                   ("app.api.v1.endpoints.rnp",              "/v1"),
    "bgu":                   ("app.api.v1.endpoints.bgu",              "/v1"),
    "ccfm":                  ("app.api.v1.endpoints.ccfm",             "/v1"),

    # ── CCFM v3 (préfixe propre /api/v3/ccfm) ────────────────
    "ccfm_v3":               ("app.api.v1.endpoints.ccfm_v3",         None),

    # ── Modules transactionnels ───────────────────────────────
    "domaine":               ("app.api.v1.endpoints.domaine",          "/v1"),
    "commune":               ("app.api.v1.endpoints.commune",          "/v1"),
    "notaire":               ("app.api.v1.endpoints.notaire",          "/v1"),
    "banque":                ("app.api.v1.endpoints.banque",           "/v1"),
    "justice":               ("app.api.v1.endpoints.justice",          "/v1"),
    "urbanisme":             ("app.api.v1.endpoints.urbanisme",        "/v1"),

    # ── Archives & Workflows ──────────────────────────────────
    "annf":                  ("app.api.v1.endpoints.annf",             "/v1"),
    "wf30":                  ("app.api.v1.endpoints.wf30",             "/v1"),
    "workflows":             ("app.api.v1.endpoints.workflows",        "/v1"),

    # ── Droits & Espaces ──────────────────────────────────────
    "droits_fonciers":       ("app.api.v1.endpoints.droits_fonciers",  "/v1"),
    "espace_travail":        ("app.api.v1.endpoints.espace_travail",   "/v1"),
    "portail_public":        ("app.api.v1.endpoints.portail_public",   "/v1"),

    # ── Intelligence (préfixe /api/v1) ────────────────────────
    "monitoring":            ("app.api.v1.endpoints.monitoring",       "/api/v1"),
    "risk":                  ("app.api.v1.endpoints.risk",             "/api/v1"),
    "dashboard":             ("app.api.v1.endpoints.dashboard",        "/api/v1"),
}

# Ensemble complet (mode monolithique)
_ALL_MODULES = set(_MODULE_REGISTRY.keys())

# Modules par service (variable FONCIER_MODULES)
_SERVICE_DEFAULTS = {
    "dar":      {"annf", "wf30"},
    "parcel":   {"bgu", "cadastre", "cadastre_parcellaire", "rnp", "droits_fonciers"},
    "legal":    {"rnaf", "journal_officiel", "notaire", "domaine",
                 "commune", "urbanisme", "banque"},
    "workflow": {"workflows", "espace_travail"},
    "alert":    {"monitoring", "risk", "dashboard"},
    "conflict": {"justice"},
    "audit":    {"admin", "portail_public"},
    "cert":     {"ccfm", "ccfm_v3", "auth", "users"},
    "all":      _ALL_MODULES,   # mode monolithique
}


class ServiceRegistry:
    """
    Résout les routers à charger en fonction de la variable FONCIER_MODULES.
    En l'absence de FONCIER_MODULES, charge tous les modules (monolithique).
    """

    @staticmethod
    def active_modules() -> List[str]:
        """
        Retourne la liste des modules actifs selon FONCIER_MODULES.
        Format : "mod1,mod2,mod3" ou "all" ou absent (= tous).
        """
        raw = os.getenv("FONCIER_MODULES", "all").strip().lower()
        service = os.getenv("FONCIER_SERVICE", "").strip().lower()

        if raw == "all" or not raw:
            modules = list(_ALL_MODULES)
        else:
            modules = [m.strip() for m in raw.split(",") if m.strip()]

        # Valider
        unknown = [m for m in modules if m not in _MODULE_REGISTRY]
        if unknown:
            _log.warning("Modules inconnus ignorés : %s", unknown)
            modules = [m for m in modules if m in _MODULE_REGISTRY]

        _log.info(
            "ServiceRegistry [%s] — %d modules actifs : %s",
            service or "monolith",
            len(modules),
            modules,
        )
        return modules

    @staticmethod
    def get_routers() -> list:
        """
        Charge et retourne les routers FastAPI des modules actifs.
        Les erreurs d'import sont loguées sans faire planter l'app.
        Retourne une liste de tuples (router, prefix) :
            prefix = None → le router gère son propre préfixe
            prefix = "/v1" → prefix override
        """
        from importlib import import_module

        routers = []
        for mod_name in ServiceRegistry.active_modules():
            import_path, prefix = _MODULE_REGISTRY[mod_name]
            try:
                module = import_module(import_path)
                router = getattr(module, "router", None)
                if router is None:
                    _log.warning("%s n'expose pas de 'router'", import_path)
                    continue
                routers.append((router, prefix))
                _log.debug("Router chargé : %s (prefix=%s)", import_path, prefix)
            except ImportError as e:
                _log.error("Impossible de charger %s : %s", import_path, e)
            except Exception as e:
                _log.error("Erreur lors du chargement de %s : %s", import_path, e)

        return routers

    @staticmethod
    def service_name() -> str:
        """Retourne le nom du service courant (ex: 'cert', 'parcel', 'all')."""
        return os.getenv("FONCIER_SERVICE", "monolith")

    @staticmethod
    def is_monolith() -> bool:
        """True si le service tourne en mode monolithique."""
        return ServiceRegistry.service_name() == "monolith"
