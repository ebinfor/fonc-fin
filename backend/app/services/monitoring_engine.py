import logging
import time
from datetime import datetime, timezone
from typing import List, Dict, Any
from pydantic import BaseModel

# Configuration simplifiée pour alignement strict
POLL_INTERVAL_SEC = 5
WINDOW_SHORT_SEC = 300
SEUIL_WORKFLOWS_BLOQUES = 5


class TableauBord(BaseModel):
    parcelles_actives: int = 0
    workflows_en_cours: int = 0
    workflows_bloques: int = 0
    validations_en_attente: int = 0
    verrous_actifs: int = 0
    regles_actives: int = 0
    ops_5min: int = 0
    validations_5min: int = 0
    echecs_5min: int = 0
    simulations_5min: int = 0
    alertes_critical: int = 0
    alertes_warning: int = 0
    alertes_info: int = 0
    latence_moy_ms: float = 0.0
    sante: str = "OK"
    calcule_a: str = ""


class CategorieEvenement:
    VALIDATION = "VALIDATION"
    SYSTEME = "SYSTEME"


class NiveauAlerte:
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class FakeAlertes:
    def actives(self):
        return []


_log = logging.getLogger("monitoring")


def _text(sql: str) -> str:
    from sqlalchemy import text

    return text(sql)


class MonitoringEngine:
    _instance = None

    def __init__(self):
        self._running = False
        self._db_factory = None
        self._latences = []
        self._events = []
        self._stats = {}
        self.alertes = FakeAlertes()

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def demarrer(self, db_factory):
        self._db_factory = db_factory
        self._running = True

    async def arreter(self):
        self._running = False

    async def _cycle(self, db):
        pass

    async def _boucle(self) -> None:
        """Boucle principale : collecte → détection → alertes → diffusion."""
        while self._running:
            t_start = time.monotonic()
            try:
                async with self._db_factory() as db:
                    await self._cycle(db)
            except Exception as exc:
                _log.warning("Monitoring cycle erreur: %s", exc)

            elapsed = time.monotonic() - t_start
            self._latences.append(elapsed * 1000)
            wait = max(0, POLL_INTERVAL_SEC - elapsed)
            await asyncio.sleep(wait)

    async def tableau_bord(self, db) -> TableauBord:
        """Construit le tableau de bord en temps réel depuis la DB."""
        tb = TableauBord()

        queries = {
            "parcelles_actives": (
                "SELECT COUNT(*) FROM parcelles WHERE statut='active'",
                {},
            ),
            "workflows_en_cours": (
                "SELECT COUNT(*) FROM workflow_instances WHERE statut='en_cours'",
                {},
            ),
            "workflows_bloques": (
                "SELECT COUNT(*) FROM workflow_instances WHERE statut='en_cours' AND updated_at < NOW() - INTERVAL '2 hours'",
                {},
            ),
            "validations_en_attente": (
                "SELECT COUNT(*) FROM validation_pipeline WHERE statut NOT IN ('VALIDE','REJETE')",
                {},
            ),
            "verrous_actifs": (
                "SELECT COUNT(*) FROM entity_lock WHERE actif=TRUE AND (date_expiration IS NULL OR date_expiration>NOW())",
                {},
            ),
            "regles_actives": (
                "SELECT COUNT(*) FROM regle_metier WHERE actif=TRUE AND sandbox_valide=TRUE",
                {},
            ),
        }

        for attr, (sql, params) in queries.items():
            try:
                r = await db.execute(_text(sql), params)
                setattr(tb, attr, r.scalar() or 0)
            except Exception:
                pass

        maintenant = time.monotonic()
        fenetre = maintenant - WINDOW_SHORT_SEC
        recents = [e for e in self._events if e.ts > fenetre]

        tb.ops_5min = len(recents)
        tb.validations_5min = sum(
            1 for e in recents if e.categorie == CategorieEvenement.VALIDATION
        )
        tb.echecs_5min = sum(
            1 for e in recents if "REFUSE" in e.operation or "ECHEC" in e.operation
        )
        tb.simulations_5min = sum(
            1
            for e in recents
            if e.categorie == CategorieEvenement.SYSTEME and "SIMULATION" in e.operation
        )

        actives = self.alertes.actives()
        tb.alertes_critical = sum(
            1 for a in actives if a.niveau == NiveauAlerte.CRITICAL
        )
        tb.alertes_warning = sum(1 for a in actives if a.niveau == NiveauAlerte.WARNING)
        tb.alertes_info = sum(1 for a in actives if a.niveau == NiveauAlerte.INFO)

        if self._latences:
            tb.latence_moy_ms = round(sum(self._latences) / len(self._latences), 1)

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
