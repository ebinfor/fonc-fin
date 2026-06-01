# 📦 Graphe des Dépendances & Liaisons Modulaires (MODULE_DEPENDENCIES.md)

**République du Niger**  
*Ministère de l'Urbanisme, de l'Habitat et du Domaine Foncier*  
*Gouvernance Technologique des Modules applicatifs*

---

## 🗺️ 1. Diagramme de la Chaîne d'Imports Python

Le diagramme d'arbre ci-dessous cartographie l'architecture des dépendances et imports internes du backend **FONCIER+** pour éviter toute régression ou couplage fort :

```
             [app.database (Session & Pool)]
                            │
            ┌───────────────┴───────────────┐
            ▼                               ▼
     [app.models.*]                 [app.core.security]
            │                               │
            ├───────────────┬───────────────┤
            ▼               ▼               ▼
     [app.services.*] [app.core.config] [app.core.scope_filter]
            │                               │
            └───────────────┬───────────────┘
                            ▼
                    [app.api.v1.endpoints.*]
                            │
                            ▼
                       [app.main]
```

*   **Règle d'Or d'Import** : Les couches supérieures (`api.v1.endpoints`) peuvent importer de la couche services (`services`), de la couche modèles (`models`) et de la couche base (`core`). **L'inverse est strictement interdit** : la couche domaine (`models`) et la couche base (`core`) ne doivent jamais importer des services ou d'API sous peine de lever une exception circulaire critique à l'importation.

---

## 🔄 2. Résolution & Prévention des Imports Circulaires

Les imports circulaires constituent l'un des risques majeurs lors du développement asynchrone FastAPI. Pour pallier ce problème, FONCIER+ applique les trois règles défensives suivantes :

### A. Injection Locale d'Imports (Local Imports)
Dans les services hautement interdépendants (ex: `droits_service.py` et `parcellaire_service.py`), les imports de classes croisées s'effectuent exclusivement **à l'intérieur de la méthode** et non en tête de fichier.
```python
# Exemple appliqué dans droits_service.py
class DroitVersionService:
    async def effectuer_transfert(self, db: Session, transfer_data):
        # Import local pour interdire l'import circulaire en haut de fichier
        from app.services.parcellaire_service import ParcelleService
        await ParcelleService.verifier_verrou_nicad(db, transfer_data.nicad)
```

### B. Patron Registry (Service Registry Pattern)
Pour découpler l'initialisation des API de la création des instances de services, le backend utilise le [service_registry.py](file:///c:/Users/USER/Desktop/fonc%20final/backend/app/api/v1/endpoints/service_registry.py) :
*   Les instances de services uniques (`CCFMWorkflowService`, `ANNFArchiveService`) y sont enregistrées et partagées via une structure de singleton.
*   Les endpoints récupèrent ces instances dynamiquement lors de la requête HTTP sans avoir à les instancier localement.

### C. Abstraction des Schémas Pydantic
Les schémas Pydantic (`app.schemas.*`) servent de contrats légers d'échange et sont totalement isolés des modèles SQLAlchemy (`app.models.*`). Un modèle ORM ne fait jamais référence à un schéma Pydantic, évitant ainsi d'importer la couche de sérialisation API dans la couche persistance.

---

## 📦 3. Liste des Dépendances Tierces (Third-Party Packages)

Le backend repose sur des librairies stables et éprouvées issues de l'écosystème open-source Python, catégorisées ci-dessous :

| Nom du Package | Version Cible | Rôle & Nécessité dans FONCIER+ |
| :--- | :--- | :--- |
| **`fastapi`** | `0.115.0` | Cadre d'API Web asynchrone haute performance |
| **`uvicorn`** | `0.30.6` | Serveur Web ASGI ultrarapide de production |
| **`sqlalchemy`** | `2.0.35` | ORM relationnel avancé (requêtes asynchrones et asynpg) |
| **`geoalchemy2`**| `0.15.2` | Extension spatiale SQLAlchemy pour support de PostGIS |
| **`asyncpg`** | `0.29.0` | Pilote de base de données PostgreSQL asynchrone natif |
| **`pydantic`** | `2.9.2` | Validation et parsing de types de données à valeur forte |
| **`redis`** | `5.1.0` | Client de base de données en mémoire pour cache et rate-limiting |
| **`python-jose`**| `3.3.0` | Génération et validation sécurisée de jetons JWT |
| **`passlib`** | `1.7.4` | Chiffrement sécurisé de mots de passe avec algorithme `bcrypt` |
| **`boto3`** | `1.35.31` | Connecteur et client cloud S3 pour stockage sur MinIO |
| **`reportlab`** | `4.2.5` | Bibliothèque de génération et dessin de documents PDF souverains |

---

## 🏦 4. Cycles de Vie des Sessions de Base de Données

FONCIER+ implémente le patron **Unit of Work** pour la gestion de ses connexions à la base de données PostgreSQL :

1.  **Scope de Requête (Request-Scoped Session)** : Chaque requête entrante sur une route d'API génère sa propre session de base de données isolée via la dépendance `get_db`.
2.  **Gestion de Transaction Robuste** : Toute modification de données (mutation, certification, litige) s'exécute à l'intérieur d'un bloc `try-except` transactionnel. En cas d'erreur réseau, de validation de signature ou de contrainte PostGIS, la transaction est immédiatement annulée (`db.rollback()`), prévenant toute corruption de données.
3.  **Fermeture Systématique** : La session de base de données est garantie d'être refermée et retournée au pool de connexion asynchrone (`db.close()`) dès la fin de la réponse de l'API.
