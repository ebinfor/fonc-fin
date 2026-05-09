# FONCIER+ ── Moteur de Validation Logique (C5)

## Vue d'ensemble

Le moteur de validation logique constitue la **5ᵉ couche** (`C5`) du pipeline de validation sécurisée des règles métier SQL.

```
POST /admin/regles-metier/valider
     │
C1 ─ Syntaxe statique          (Python pur, 40+ mots-clés interdits)
C2 ─ Sémantique foncière       (whitelist 30 tables, $1/$2)
C3 ─ Sandbox PostgreSQL        (EXPLAIN READ ONLY + timeout 2 s)
C4 ─ Signature SHA-256         (non-répudiation)
C5 ─ Validation logique  ◄─── CE MODULE
     │
     ▼
AUTORISER | CORRIGER | REFUSER
```

## Architecture

```
LogicConflictEngine
├── register(heuristic)       ← extensibilité sans modification
├── disable/enable(id)        ← contrôle opérationnel
├── list_heuristics()         ← introspection
└── valider(sql, domaine, …)  ← point d'entrée unique
    │
    ├─ _load_rules(db, domaine)   → règles actives + GENERAL
    │
    ├─ H1  DuplicateRuleHeuristic
    ├─ H2  NegationContradictionHeuristic
    ├─ H3  BlockingConflictHeuristic
    ├─ H4  SubsumptionHeuristic
    ├─ H5  AlwaysBlockingHeuristic
    ├─ HG  GlobalCoherenceHeuristic
    └─ [extensions custom]
```

## Heuristiques intégrées

| ID                   | Niveau max | Description                                              |
|----------------------|------------|----------------------------------------------------------|
| `H1_DUPLICATE`       | CRITIQUE   | SQL normalisé identique ou quasi-identique (sim ≥ 85 %) |
| `H2_NEGATION`        | CRITIQUE   | Paire EXISTS / NOT EXISTS sur mêmes tables+conditions   |
| `H3_BLOCKING`        | CRITIQUE   | Prédicats contradictoires entre deux règles BLOQUANT    |
| `H4_SUBSUMPTION`     | MINEUR     | Nouvelle règle déjà couverte par une plus générale      |
| `H5_ALWAYS_BLOCKING` | MAJEUR     | Combinaison créant une impossibilité totale             |
| `HG_GLOBAL`          | MAJEUR     | Incohérence de niveau, collision GENERAL / spécialisé   |

## Niveaux de gravité et recommandations

| Situation                        | Recommandation | Bloque l'activation |
|----------------------------------|----------------|---------------------|
| Au moins 1 conflit `CRITIQUE`    | `REFUSER`      | ✓ Oui               |
| Uniquement des conflits `MAJEUR` | `CORRIGER`     | ✗ Non (avertissement)|
| Uniquement des conflits `MINEUR` | `AUTORISER`    | ✗ Non (informatif)  |
| Aucun conflit                    | `AUTORISER`    | ✗ Non               |

## Utilisation

### Via le pipeline complet (recommandé)

```python
# La validation logique est intégrée automatiquement dans SQLRuleValidator.valider()
# lorsque db est fourni (mode COMPLET)
result = await SQLRuleValidator.valider(
    sql="NOT EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND is_gele=TRUE)",
    domaine="GENERAL",
    code="CHECK_NON_GELE",
    niveau_blocage="BLOQUANT",
    db=db,           # Active C3 + C5
    log_db=db,
)

# Rapport logique disponible dans result.logic_report
if result.logic_report:
    print(result.logic_report["recommandation"])   # AUTORISER | CORRIGER | REFUSER
    print(result.logic_report["conflits"])          # liste détaillée
```

### Via l'endpoint dédié

```http
POST /api/v1/admin/regles-metier/valider-logique
Content-Type: application/json

{
  "sql": "NOT EXISTS (SELECT 1 FROM parcelles WHERE id=$1 AND is_gele=TRUE)",
  "domaine": "GENERAL",
  "code": "CHECK_NON_GELE",
  "niveau_blocage": "BLOQUANT"
}
```

### Réponse

```json
{
  "recommandation": "AUTORISER",
  "bloque": false,
  "nb_critique": 0,
  "nb_majeur": 0,
  "nb_mineur": 0,
  "regles_impactees": [],
  "regles_analysees": 12,
  "duree_ms": 45,
  "sha256_rapport": "a3f2...",
  "conflits": []
}
```

## Extensibilité

Ajouter une nouvelle heuristique **sans modifier l'architecture** :

```python
from app.services.logic_validator import LogicHeuristic, LogicConflict, ConflictNiveau
from app.services.logic_validator import LogicConflictEngine

class MaHeuristique(LogicHeuristic):
    id          = "MA_HEURISTIQUE"
    description = "Vérifie que la nouvelle règle n'excède pas X prédicats"

    def analyze(self, new_rule, existing_rules):
        if len(new_rule.predicats) > 10:
            return [LogicConflict(
                heuristique = "MA_HEURISTIQUE",
                niveau      = ConflictNiveau.MINEUR,
                regle_cible = "",
                message     = "Règle trop complexe (> 10 prédicats)",
                details     = f"Nombre de prédicats : {len(new_rule.predicats)}",
                suggestion  = "Décomposer en plusieurs règles plus simples.",
            )]
        return []

# Enregistrer une seule fois au démarrage de l'application
LogicConflictEngine.register(MaHeuristique())
```

## Tables DB

| Table / Vue                          | Description                              |
|--------------------------------------|------------------------------------------|
| `regle_validation_logique_log`       | Journal append-only des validations C5   |
| `logic_heuristic_config`             | Configuration des heuristiques par domaine|
| `v_logic_conflits_recents`           | Conflits des 30 derniers jours           |
| `v_logic_sante`                      | Santé du moteur (24h)                    |
| `v_regles_conflits_logiques`         | Règles actuellement en conflit logique   |
| `logic_taux_conflit(jours)`          | Taux de conflit par domaine              |

## Endpoints API

| Méthode | Endpoint                                     | Description                      |
|---------|----------------------------------------------|----------------------------------|
| `POST`  | `/admin/regles-metier/valider-logique`       | Analyse logique d'une règle      |
| `GET`   | `/admin/logic/rapport`                       | Rapport de santé complet         |
| `GET`   | `/admin/logic/conflits`                      | Règles avec conflits actifs      |
| `GET`   | `/admin/logic/heuristiques`                  | Liste et config des heuristiques |
| `PATCH` | `/admin/logic/heuristiques/{id}`             | Activer / désactiver             |
| `GET`   | `/admin/logic/taux-conflit`                  | Taux de conflit par domaine      |

## Performances

- Limite : `MAX_RULES_ANALYSEES = 200` règles chargées depuis la DB par analyse
- Timeout global : `ENGINE_TIMEOUT_MS = 3000` ms
- Index DB : `idx_rm_sandbox` (domaine + actif + sandbox_valide)
- Chaque heuristique est O(n) sur le nombre de règles existantes
- Heuristiques désactivables individuellement via `disable(id)` ou via l'API
