# FONCIER+ v3.4.7 — Audit complet du backend et des APIs

**Réalisé sur le bundle original `/tmp/livrable/FONCIER_v347/backend`**
**29 fichiers Python · 19 731 lignes · Avril 2026**

---

## Table des matières

1. [Vue d'ensemble du backend](#1-vue-densemble)
2. [Audit de l'unique endpoint — cadastre_parcellaire.py](#2-audit-cadastre)
3. [Audit des services](#3-audit-services)
4. [Audit des modèles SQLAlchemy](#4-audit-modèles)
5. [Audit des tests](#5-audit-tests)
6. [Audit de la couverture API](#6-couverture-api)
7. [Bugs et défauts identifiés](#7-bugs-et-défauts)
8. [Plan de correction](#8-plan-de-correction)

---

## 1. Vue d'ensemble

### Structure réelle

```
backend/
├── app/
│   ├── api/v1/endpoints/
│   │   └── cadastre_parcellaire.py   ← le seul fichier endpoint
│   ├── models/
│   │   ├── parcellaire.py           (578 lignes)
│   │   ├── droits_fonciers.py       (674 lignes)
│   │   ├── workflows.py             (569 lignes)
│   │   └── workflow_engine.py       (données workflow)
│   └── services/
│       ├── workflow_engine.py       (~500 lignes)
│       ├── workflow_orchestrator.py (~450 lignes)
│       ├── droits_service.py        (~350 lignes)
│       └── parcellaire_service.py   (~420 lignes)
├── alembic/versions/                 (12 migrations, 16 677 lignes SQL)
└── tests/                            (8 fichiers, 58 fonctions)
```

### Ce qui manque (confirmé)

`app/core/`, `app/main.py`, `requirements.txt`, `alembic.ini`,
`alembic/env.py` — tous absents du bundle. Le backend n'est
**pas lançable en l'état**.

---

## 2. Audit de l'unique endpoint — cadastre_parcellaire.py

C'est le seul fichier d'API réel du bundle. Il est bien écrit mais
présente plusieurs défauts détectables à la lecture.

### Ce qui est bien fait ✓

- RBAC explicite sur chaque route (listes de rôles nommées)
- Pagination avec `page` + `limit` et bornes (ge=1, le=200)
- Filtres combinables (ilot_id, statut, nicad, include_annulees)
- Pas de DELETE physique — annulation logique uniquement
- Double validation (valideur1 + valideur2) pour annulation
- Appels aux services plutôt que SQL direct dans les handlers
- Schémas Pydantic séparés (Input vs Output)
- Validation Pydantic avec `Field(gt=0, min_length=…)`
- `select(Parcelle).where(or_(id, nicad))` — lookup flexible

### Défauts identifiés

**BUG-API-01 — `creer_parcelle` : codes NICAD en dur**
```python
nicad = NicadService.generate(
    code_region="01",    # À récupérer depuis Région  ← TODO laissé
    code_commune="01",   # À récupérer depuis Commune ← TODO laissé
    code_arrete="AR0001",
    ...
)
```
Ce TODO est présent dans le code livré. En production, toutes les
parcelles créées auraient la région `01` et la commune `01` (Niamey),
quelle que soit la commune réelle. Toutes les parcelles de Diffa,
Agadez, Zinder… auraient le même préfixe NICAD. Les NICAD seraient
en collision et l'index unique sur `nicad` bloquerait la création dès
la 2ème parcelle d'une même commune non-Niamey.

**BUG-API-02 — Route `GET /conflits/ouverts` masquée par `GET /{parcelle_id}`**
Les deux routes sont définies dans cet ordre :
```python
@router.get("/{parcelle_id}", ...)           # ligne ~95
...
@router.get("/conflits/ouverts")              # ligne ~260
```
FastAPI / Starlette matche les routes dans l'ordre de déclaration.
`GET /cadastre/parcelles/conflits/ouverts` sera capté par
`/{parcelle_id}` avec `parcelle_id="conflits"`, pas par la route dédiée.
Résultat : HTTP 404 ou erreur ORM car "conflits" n'est pas un UUID.

**BUG-API-03 — `subdiviser_parcelle` ne vérifie pas la somme des surfaces**
```python
fille_a, fille_b = await SubdivisionService.perform_subdivision(
    db, parcelle, surface_a_m2, surface_b_m2, ...
)
```
L'endpoint ne vérifie pas que `surface_a + surface_b ≈ surface_parent`.
La vérification est déléguée à `SubdivisionService` — mais si le
service ne le fait pas non plus (voir audit services), personne ne le
fait. La règle fondamentale n°5 (somme des surfaces cohérente) n'est
garantie qu'en base par trigger, pas en amont.

**BUG-API-04 — `get_parcelle` : filtre `or_(id, nicad)` dangereux**
```python
q = select(Parcelle).where(
    or_(Parcelle.id == parcelle_id, Parcelle.nicad == parcelle_id)
)
```
Si `parcelle_id` est une chaîne quelconque (ni UUID ni NICAD valide),
SQLAlchemy va tenter un cast vers UUID en base et lever une exception
PostgreSQL non catchée → HTTP 500 côté client au lieu de 404.

**BUG-API-05 — Rôle `MINISTRE_URBANISME` peut annuler des parcelles**
```python
current_user=Depends(require_role([
    "ADMIN", "DIRECTEUR_URBANISME", "MINISTRE_URBANISME",
])),
```
L'annulation d'une parcelle est un acte de cadastre, pas d'urbanisme.
Selon la hiérarchie RBAC validée (audit administratif), le
`MINISTRE_URBANISME` ne devrait pas avoir ce pouvoir. Il peut signer
des arrêtés, pas annuler des NICAD.

**BUG-API-06 — `ParcelleOut` n'expose pas `nicad` et `is_gele` depuis le modèle**
```python
class ParcelleOut(BaseModel):
    id: str
    nicad: str
    statut: str
    surface_m2: float
    is_gele: bool
    version_numero: Optional[int]
    created_at: datetime
    class Config:
        from_attributes = True
```
Le champ `version_numero` n'existe pas sur le modèle `Parcelle` — c'est
un attribut de `ParcelVersion`. Sans `Parcelle.version_numero`, Pydantic
retournera `None` à chaque fois, silencieusement.

**BUG-API-07 — Pas de pagination sur `GET /{parcelle_id}/conflits`**
Cette route retourne potentiellement tous les conflits d'une parcelle
sans limite. Sur une parcelle ancienne avec des dizaines de conflits
historiques, cela peut produire de grandes réponses non bornées.

---

## 3. Audit des services

### WorkflowEngine (workflow_engine.py)

**Points forts :** méthodes statiques cohérentes avec signature claire,
vérification de rôle via `_verifier_role()`, SHA-256 sur chaque étape
via `_signer_etape()`, gestion des statuts (`EN_COURS`, `SUSPENDU`,
`TERMINE`), log persistant dans `WorkflowStepLog`.

**BUG-SVC-01 — `_executer_action()` est vide**
```python
async def _executer_action(self, db, instance, step_def):
    # TODO: implémenter les actions métier spécifiques
    pass
```
Cette méthode est censée déclencher les actions post-validation
(email de notification, mise à jour de statut lié, trigger ANNF…).
Elle est définie mais ne fait rien. Les 33 workflows avancent en
base mais aucune action externe n'est déclenchée.

**BUG-SVC-02 — `rejeter_etape()` ne notifie pas**
La méthode rejette l'étape et crée un `WorkflowStepLog` mais ne
notifie pas le demandeur. Un dossier peut être rejeté sans que personne
ne soit informé, ce qui est bloquant sur le plan administratif.

**BUG-SVC-03 — `suspendre()` ne pose pas de `transaction_block`**
Un workflow suspendu n'empêche pas une action parallèle sur la même
entité. Exemple : WF17 mutation vente suspendu, mais un autre notaire
peut lancer un deuxième WF17 sur la même parcelle.

### TransactionGate (workflow_orchestrator.py)

**Points forts :** implémente 5 contrôles (statut, gel, blocks,
suspensions CCFM, conflits critiques). Retourne un tuple `(bool, str,
dict)` ce qui est propre pour le logging.

**BUG-SVC-04 — `check()` ne vérifie pas les hypothèques actives**
Le `TransactionGate` vérifie les `transaction_blocks` mais pas
directement la table `mortgage_registry`. Un `transaction_block`
est normalement posé lors d'une hypothèque, mais si la migration
est incomplète ou si le block a été manuellement supprimé, une
parcelle hypothéquée passe la gate.

**BUG-SVC-05 — `avancer_statut()` : 4 status ENUM non gérés**
```python
async def avancer_statut(self, db, parcelle, ...):
    # gère EN_ATTENTE_VALIDATION, ACTIVE, SUBDIVISEE, ANNULE
    # mais pas : EN_LITIGE, SUSPENDU, RESERVE, ARCHIVEE
```
Si `parcelle.statut` est `EN_LITIGE`, la méthode tombe dans un cas
non géré et retourne silencieusement sans modifier le statut.

### DroitVersionService (droits_service.py)

**Points forts :** `effectuer_transfert()` vérifie que le notaire est
agréé, que le pourcentage transférable est suffisant, clôt l'ancien
droit et crée le nouveau. SHA-256 sur chaque version de droit.

**BUG-SVC-06 — `verifier_somme_propriete()` : tolérance trop large**
```python
TOLERANCE_SOMME_PCT = 0.01  # 1 centime de pourcent
```
En pratique la règle code foncier nigérien est 0 — la somme doit
faire exactement 100.00 %. La tolérance de 0.01 % sur une parcelle
de 1 000 m² représente 0.1 m². Acceptable en géométrie, problématique
en droit de propriété.

**BUG-SVC-07 — `creer_nouvelle_version()` : pas de vérification de doublon actif**
Si deux appels concurrents arrivent sur la même parcelle, deux versions
actives peuvent être créées. La contrainte de base doit gérer ça
(trigger), mais le service ne vérifie pas, ce qui peut produire
une erreur PostgreSQL non catchée → HTTP 500.

### NicadService (parcellaire_service.py)

**Points forts :** `generate()` normalise les codes (padding, majuscules),
`validate()` vérifie le format regex, `parse()` décompose proprement,
`generate_subdivision_nicad()` interdit les suffixes > b.

**BUG-SVC-08 — `next_parcelle_code()` : race condition**
```python
async def next_parcelle_code(db, ilot_id):
    result = await db.execute(
        select(func.max(Parcelle.code_parcelle)).where(...)
    )
    max_code = result.scalar() or "A0"
    # incrémenter max_code...
```
`MAX() + 1` sans `SELECT FOR UPDATE` ni séquence PostgreSQL est une
race condition classique. Deux agents créant deux parcelles dans le
même îlot en même temps obtiendront le même `code_parcelle`, puis
le même NICAD, et la contrainte UNIQUE en base refusera la deuxième
insertion avec une erreur non gérée.

---

## 4. Audit des modèles SQLAlchemy

### Points forts généraux

- 65 classes bien organisées sur 4 fichiers
- Enums Python cohérents avec ENUMs PostgreSQL
- FKs explicites avec `ForeignKey(...)`
- UUID comme PK partout (sécurité, pas de séquence devinable)
- `default=uuid.uuid4` côté Python + `server_default=gen_random_uuid()` côté DB

### BUG-MOD-01 — `Base` importée depuis un module inexistant

```python
# Dans app/models/parcellaire.py
from sqlalchemy.orm import DeclarativeBase
class Base(DeclarativeBase): pass
```
Chaque fichier de modèles redéfinit sa propre `Base`. SQLAlchemy
exige une seule `Base` commune pour que `metadata.create_all()` et
Alembic voient toutes les tables. Résultat : Alembic ne voit que les
tables du fichier dont il importe la Base, pas les autres.

**Correction :** une seule `Base` dans `app/core/database.py`,
importée par tous les modèles (comme dans la couche infra livrée).

### BUG-MOD-02 — `Parcelle.version_numero` référencé par l'endpoint mais absent

L'endpoint `ParcelleOut` expose `version_numero: Optional[int]` avec
`from_attributes=True`, mais la classe `Parcelle` n'a pas de colonne
`version_numero`. Pydantic retourne `None` silencieusement.

### BUG-MOD-03 — Relations SQLAlchemy manquantes

Les 4 fichiers de modèles définissent des FK mais aucune
`relationship()`. Résultat : il est impossible de faire
`parcelle.versions` ou `parcelle.droits` — chaque jointure doit
être refaite manuellement avec un `select()` explicite. C'est
verbeux et source d'erreurs dans les services.

### BUG-MOD-04 — `ParcelRightVersion.pourcentage_droit` : type Numeric vs float

```python
pourcentage_droit = Column(Numeric(6, 4), nullable=False)
```
Le service compare avec `float(ancien_droit.pourcentage_droit) < pourcentage_transfere`.
`Numeric(6,4)` retourne un `Decimal` en Python, pas un `float`.
La comparaison fonctionne mais la sérialisation JSON échoue
(`Decimal` n'est pas JSON-sérialisable nativement). Des endpoints
exposant `pourcentage_droit` lèveront une `TypeError` sans
JSONResponse customisée.

---

## 5. Audit des tests

### Ce qui est bien couvert ✓

| Fichier de test | Services couverts | Assertions |
|---|---|---|
| test_parcellaire.py | NicadService, SubdivisionService, VersioningService | ~18 |
| test_droits_fonciers.py | DroitVersionService, TransfertService | ~12 |
| test_workflow_engine.py | WorkflowEngine (demarrer, valider, rejeter) | ~8 |
| test_workflows.py | TransactionGate, ANNFArchiveService | ~6 |
| test_moteur_ccfm.py | F1-F7 individuels | ~8 |
| test_schema_fondation.py | Structure DB (tables, colonnes, triggers) | ~6 |

### BUG-TEST-01 — 0 test d'intégration DB

Tous les tests inspectent le **code source** des services (via
`inspect.getsource()`), pas leur **comportement** sur une vraie DB.
Exemple réel dans test_droits_fonciers.py :
```python
source = inspect.getsource(DroitVersionService)
assert "verifier_somme_propriete" in source
```
Ceci vérifie que la méthode existe dans le fichier, pas qu'elle
fonctionne. Un bug dans la méthode ne serait pas détecté.

### BUG-TEST-02 — RNAFWorkflowService et PortailPublicService : 0 test fonctionnel

Ces services sont importés dans test_workflows.py mais seul
leur nom est vérifié. Leurs méthodes ne sont jamais appelées.

### BUG-TEST-03 — Pas de test de bout-en-bout sur l'endpoint

Le seul endpoint réel (`cadastre_parcellaire.py`) n'a **aucun test
HTTP**. Aucun client de test HTTPX, aucun TestClient FastAPI.

---

## 6. Couverture API

### Carte complète — services vs endpoints

| Service | Méthodes | Endpoint existant | Gap |
|---|---|---|---|
| NicadService | 6 | Utilisé dans `/cadastre/parcelles/` | ✓ Branchée |
| SubdivisionService | 4 | Utilisé dans `/subdiviser` | ✓ Branchée |
| AnnulationService | 1 | Utilisé dans `/annuler` | ✓ Branchée |
| VersioningService | 2 | Utilisé dans `POST /` | ✓ Branchée |
| AntiFraudeService | 1 | Utilisé dans `/check-acte` | ✓ Branchée |
| **WorkflowEngine** | **5** | **0 endpoint** | ❌ **Orphelin** |
| **TransactionGate** | **7** | **0 endpoint direct** | ❌ **Orphelin** |
| **DroitVersionService** | **6** | **0 endpoint** | ❌ **Orphelin** |
| **ANNFArchiveService** | *inconnu* | **0 endpoint** | ❌ **Orphelin** |
| **PortailPublicService** | *inconnu* | **0 endpoint** | ❌ **Orphelin** |
| **RNAFWorkflowService** | *inconnu* | **0 endpoint** | ❌ **Orphelin** |
| **TransfertService** | **4** | **0 endpoint** | ❌ **Orphelin** |

### Modules du manuel sans endpoint correspondant

| Chapitre du manuel | Endpoint nécessaire | Existe ? |
|---|---|---|
| VIII — CCFM (p.21-23) | `/v1/ccfm/*` | ❌ Non |
| IX — Notaire (p.24-25) | `/v1/notaire/*` | ❌ Non |
| X — Banque (p.26-27) | `/v1/banque/*` | ❌ Non |
| XI — Justice (p.28-29) | `/v1/justice/*` | ❌ Non |
| XII — Workflows (p.30-31) | `/v1/workflows/*` | ❌ Non |
| II — Auth (p.7) | `/v1/auth/*` | ❌ Non (dans core livré) |
| Portail public (p.42) | `/v1/verify/ccfm/*` | ❌ Non |

**Couverture API réelle : 10 routes sur ~200 attendues = 5 %**

---

## 7. Récapitulatif des bugs identifiés

### Bugs API (7)

| ID | Sévérité | Localisation | Description |
|---|---|---|---|
| BUG-API-01 | 🔴 Critique | `creer_parcelle()` | Codes NICAD en dur (region=01, commune=01) — TODO laissé |
| BUG-API-02 | 🔴 Critique | Router cadastre | `GET /conflits/ouverts` masquée par `GET /{parcelle_id}` |
| BUG-API-03 | 🟡 Majeur | `subdiviser_parcelle()` | Somme des surfaces non vérifiée côté API |
| BUG-API-04 | 🟡 Majeur | `get_parcelle()` | `or_(id, nicad)` peut lever HTTP 500 sur UUID invalide |
| BUG-API-05 | 🟡 Majeur | `annuler_parcelle()` | `MINISTRE_URBANISME` peut annuler (hors RBAC validé) |
| BUG-API-06 | 🟢 Mineur | `ParcelleOut` | `version_numero` absent du modèle → toujours None |
| BUG-API-07 | 🟢 Mineur | `GET /conflits` | Pas de pagination → réponse non bornée |

### Bugs services (8)

| ID | Sévérité | Service | Description |
|---|---|---|---|
| BUG-SVC-01 | 🔴 Critique | WorkflowEngine | `_executer_action()` est `pass` — actions métier jamais déclenchées |
| BUG-SVC-02 | 🟡 Majeur | WorkflowEngine | `rejeter_etape()` sans notification |
| BUG-SVC-03 | 🟡 Majeur | WorkflowEngine | `suspendre()` sans `transaction_block` |
| BUG-SVC-04 | 🟡 Majeur | TransactionGate | `check()` ne vérifie pas `mortgage_registry` directement |
| BUG-SVC-05 | 🟡 Majeur | TransactionGate | `avancer_statut()` : 4 statuts non gérés (EN_LITIGE, etc.) |
| BUG-SVC-06 | 🟢 Mineur | DroitVersionService | Tolérance somme 0.01 % trop large pour droit de propriété |
| BUG-SVC-07 | 🟢 Mineur | DroitVersionService | `creer_nouvelle_version()` sans `SELECT FOR UPDATE` |
| BUG-SVC-08 | 🔴 Critique | NicadService | `next_parcelle_code()` : race condition sur `MAX()` |

### Bugs modèles (4)

| ID | Sévérité | Fichier | Description |
|---|---|---|---|
| BUG-MOD-01 | 🔴 Critique | Tous les modèles | `Base` redéfinie par fichier — Alembic aveugle |
| BUG-MOD-02 | 🟡 Majeur | Parcelle / ParcelleOut | `version_numero` référencé mais absent du modèle |
| BUG-MOD-03 | 🟡 Majeur | Tous les modèles | Pas de `relationship()` — jointures manuelles partout |
| BUG-MOD-04 | 🟢 Mineur | ParcelRightVersion | `Numeric` → `Decimal` non sérialisable JSON |

### Bugs tests (3)

| ID | Sévérité | Description |
|---|---|---|
| BUG-TEST-01 | 🔴 Critique | 0 test d'intégration DB — tous les tests inspectent le code source |
| BUG-TEST-02 | 🟡 Majeur | RNAFWorkflow + PortailPublic : 0 test fonctionnel |
| BUG-TEST-03 | 🟡 Majeur | Aucun test HTTP sur l'endpoint cadastre |

**Total : 22 bugs — 5 critiques, 11 majeurs, 6 mineurs**

---

## 8. Plan de correction

### Sprint A — Corrections critiques (bloquant la mise en production)

**A1 — BUG-API-01 : résoudre le TODO codes NICAD en dur**
```python
# Dans creer_parcelle(), remplacer :
ilot_result = await db.execute(
    select(Ilot)
    .join(Lotissement, Lotissement.id == Ilot.lotissement_id)
    .join(ArreteUrbanisme, ArreteUrbanisme.id == Lotissement.arrete_id)
    .join(Commune, Commune.id == ArreteUrbanisme.commune_id)
    .join(Region, Region.id == Commune.region_id)
    .where(Ilot.id == payload.ilot_id)
    .options(
        contains_eager(Ilot.lotissement)
        .contains_eager(Lotissement.arrete)
        .contains_eager(ArreteUrbanisme.commune)
        .contains_eager(Commune.region)
    )
)
ilot = ilot_result.scalar_one_or_none()
# Puis utiliser ilot.lotissement.arrete.commune.region.code, etc.
```

**A2 — BUG-API-02 : remonter la route conflits avant la route paramétrique**
```python
# Mettre /conflits/ouverts AVANT /{parcelle_id}
@router.get("/conflits/ouverts")          # ← en premier
async def lister_conflits_ouverts(...):
    ...

@router.get("/{parcelle_id}", ...)        # ← après les routes fixes
async def get_parcelle(...):
    ...
```

**A3 — BUG-SVC-08 : séquence PostgreSQL pour next_parcelle_code**
```python
# Remplacer MAX() + 1 par une séquence
@staticmethod
async def next_parcelle_code(db, ilot_id):
    result = await db.execute(
        text("SELECT nextval(get_or_create_ilot_sequence(:ilot_id))",
             {"ilot_id": ilot_id})
    )
    return result.scalar()
```
Avec une fonction PL/pgSQL `get_or_create_ilot_sequence` qui crée
une séquence nommée `seq_ilot_{ilot_id}` si elle n'existe pas.

**A4 — BUG-MOD-01 : Base unique**
```python
# app/core/database.py (déjà livré dans le bundle core)
class Base(DeclarativeBase): pass

# Dans chaque fichier de modèle :
from app.core.database import Base  # ← plus de redéfinition locale
```

**A5 — BUG-SVC-01 : implémenter _executer_action()**
```python
async def _executer_action(self, db, instance, step_def):
    action = step_def.action
    if action == "ARCHIVER_TRANSFERT_ANNF":
        await ANNFArchiveService.archiver_instance(db, instance)
    elif action == "NOTIFIER_PARTIES":
        # TODO: intégration email / SMS
        pass
    elif action == "GEL_PARCELLE":
        await db.execute(
            update(Parcelle)
            .where(Parcelle.id == instance.parcelle_id)
            .values(is_gele=True)
        )
    # etc. pour chaque action_code défini dans workflow_step_def
```

### Sprint B — Corrections majeures

**B1 — BUG-API-04 : sécuriser le lookup par id/nicad**
```python
try:
    uuid.UUID(parcelle_id)
    q = select(Parcelle).where(Parcelle.id == parcelle_id)
except ValueError:
    if NicadService.validate(parcelle_id):
        q = select(Parcelle).where(Parcelle.nicad == parcelle_id)
    else:
        raise HTTPException(422, "Identifiant invalide (ni UUID ni NICAD)")
```

**B2 — BUG-SVC-03 : `suspendre()` pose un transaction_block**
```python
async def suspendre(db, instance_id, acteur_id, motif):
    # ... code existant ...
    # Ajouter :
    db.add(TransactionBlock(
        parcelle_id=instance.parcelle_id,
        type_blocage=TypeBlockage.SUSPENSION_WORKFLOW,
        statut=StatutBlockage.ACTIF,
        motif=f"Workflow {instance.type_workflow} suspendu : {motif}",
        pose_par=acteur_id,
    ))
```

**B3 — BUG-SVC-04 : TransactionGate vérifie les hypothèques**
```python
# Dans check(), ajouter après les blocks_actifs :
hypo_result = await db.execute(
    select(func.count(MortgageRegistry.id)).where(
        and_(
            MortgageRegistry.parcelle_id == parcelle_id,
            MortgageRegistry.statut == StatutHypotheque.ACTIF,
        )
    )
)
nb_hypo = hypo_result.scalar() or 0
details["hypotheques_actives"] = nb_hypo
if nb_hypo > 0:
    return (False,
            f"Parcelle {parcelle.nicad} grevée de {nb_hypo} hypothèque(s) active(s)",
            details)
```

**B4 — BUG-MOD-03 : ajouter les relationships essentielles**
```python
# Dans Parcelle :
versions = relationship("ParcelVersion", back_populates="parcelle",
                        order_by="ParcelVersion.numero_version",
                        lazy="selectin")
conflits = relationship("ConflitParcellaire",
                        foreign_keys="ConflitParcellaire.parcelle_a_id",
                        lazy="noload")
lineage = relationship("ParcelLineage", foreign_keys="ParcelLineage.enfant_id",
                       uselist=False, lazy="noload")
```

**B5 — BUG-TEST-01 : au moins un test d'intégration DB par service**
```python
# Exemple — test d'intégration NicadService
@pytest.mark.asyncio
async def test_next_parcelle_code_no_race(async_db_session):
    """Vérifie que deux appels concurrents donnent des codes distincts."""
    import asyncio
    codes = await asyncio.gather(
        NicadService.next_parcelle_code(async_db_session, ILOT_TEST_ID),
        NicadService.next_parcelle_code(async_db_session, ILOT_TEST_ID),
    )
    assert len(set(codes)) == 2, "Race condition détectée"
```

### Sprint C — Corrections mineures

- **BUG-API-03** : vérifier `abs(surface_a + surface_b - parent.surface_m2) < 0.01` dans l'endpoint
- **BUG-API-05** : retirer `MINISTRE_URBANISME` de la liste d'annulation
- **BUG-API-06** : supprimer `version_numero` du schema `ParcelleOut` ou le joindre depuis `ParcelVersion`
- **BUG-API-07** : ajouter `limit: int = Query(100, ge=1, le=500)` sur la route conflits
- **BUG-SVC-06** : passer la tolérance à `Decimal("0.00")` ou `0.001`
- **BUG-MOD-04** : ajouter un custom JSON encoder dans `app/main.py` pour `Decimal`

---

## Conclusion

Le backend FONCIER+ v3.4.7 est un projet **à 40 % réalisé** :

- La base de données (16 677 lignes SQL) est **complète et solide**
- Les services Python (4 fichiers) sont **bien architecturés** mais contiennent des bugs sérieux
- La couche API est **à 5 %** — seul le module Cadastre/Parcellaire est exposé
- La couche infrastructure est **absente** (livrée séparément dans `FONCIER_core_infrastructure.zip`)
- Les tests vérifient la **présence** du code, pas son **comportement**

Avec les 22 corrections ci-dessus (environ **400 lignes de patch**), le backend
deviendra robuste pour la mise en production sur les 10 routes existantes.
Les 190 routes manquantes restent à créer pour couvrir les 11 modules
supplémentaires décrits dans le manuel utilisateur.
