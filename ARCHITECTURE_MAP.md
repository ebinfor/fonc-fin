# 🏗️ Carte d'Architecture Technique (ARCHITECTURE_MAP.md)

**République du Niger**  
*Ministère de l'Urbanisme, de l'Habitat et du Domaine Foncier*  
*Gouvernance & Alignement de l'Infrastructure Numérique Souveraine*

---

## 🏛️ 1. Architecture Applicative Multicouche (Layered Architecture)

Le backend de **FONCIER+** est structuré selon un patron d'architecture propre (Clean Architecture) segmenté en quatre couches étanches :

```
┌────────────────────────────────────────────────────────┐
│             COUCHE API / ROUTERS (FastAPI)              │
│  - app.api.v1.endpoints.*                              │
│  - app.api.v3.endpoints.*                              │
└───────────┬────────────────────────────────────────────┘
            │  Injecte Session DB & Authentification
            ▼
┌────────────────────────────────────────────────────────┐
│             COUCHE SERVICES / WORKFLOWS                │
│  - app.services.workflow_engine.WorkflowEngine         │
│  - app.services.parcellaire_service.ParcelleService    │
│  - app.services.droits_service.DroitVersionService     │
└───────────┬────────────────────────────────────────────┘
            │  Manipule la Business Logic & Scellages
            ▼
┌────────────────────────────────────────────────────────┐
│             COUCHE MODÈLES / MODÈLES ORM               │
│  - app.models.users.User                               │
│  - app.models.parcellaire.Parcelle                     │
│  - app.models.droits_fonciers.MortgageRegistry         │
└───────────┬────────────────────────────────────────────┘
            │  Définit les Entités & Triggers SQL
            ▼
┌────────────────────────────────────────────────────────┐
│             COUCHE INFRASTRUCTURE / PERSISTENCE        │
│  - PostgreSQL + Extension Spatial PostGIS (WKT/WKB)    │
│  - Redis cache (Sessions JWT, blacklists, rate-limit)  │
│  - Object Storage MinIO (Plans, certificats PDFs A4/A5)│
└────────────────────────────────────────────────────────┘
```

1. **Couche API (Controllers)** : Endpoints REST déclarés via `APIRouter`. Ils valident les types d'entrée via des modèles **Pydantic** et délèguent le traitement métier aux Services.
2. **Couche Services (Business Logic)** : Orchestrateurs et managers de transactions. Ils gèrent la cohérence métier, les transitions d'états de workflows, et calculent les écarts géodésiques.
3. **Couche Modèles (Domain Entities)** : Modèles de données déclarés via **SQLAlchemy ORM** reliés aux tables relationnelles.
4. **Couche Infrastructure** : Base de données, stockage cloud S3/MinIO, et cache Redis.

---

## 📦 2. Scénarios de Déploiement : Monolithe vs Microservices

FONCIER+ supporte une double configuration de déploiement à partir d'un unique dépôt de code source (Monorepo), s'adaptant à la maturité de l'infrastructure d'hébergement (souveraineté locale vs cloud évolutif) :

### A. Déploiement Monolithique (Production Standard)
*   **Fichier** : `config/docker-compose.prod.yml`
*   **Principe** : L'ensemble du code tourne dans un conteneur unique `backend`. Un serveur mandataire inverse (Nginx) gère les certificats SSL et route toutes les requêtes vers ce conteneur.
*   **Avantage** : Facilité d'installation, maintenance simplifiée, parfaitement adapté aux infrastructures d'hébergement ministérielles à ressources limitées.

### B. Déploiement Microservices (Production Scalable)
*   **Fichier** : `docker-compose.microservices.yml`
*   **Principe** : Séparation logique et physique en **8 microservices autonomes** :
    1.  `svc-dar` (Tiers archiveur & DAR)
    2.  `svc-parcel` (Registre cadastral et limites PostGIS)
    3.  `svc-legal` (Gestion des transactions notariales et actes)
    4.  `svc-workflow` (Moteur de workflow d'état central)
    5.  `svc-alert` (Détection de fraude et de collisions)
    6.  `svc-conflict` (Tribunaux et gel conservatoire)
    7.  `svc-audit` (Logs inaltérables WORM)
    8.  `svc-cert` (Certification CCFM nationale)
*   **Avantage** : Résilience accrue (une panne du service de certification n'affecte pas l'accès au cadastre), scaling indépendant, isolation complète des bases de données de chaque institution.

---

## 🔄 3. Les Grands Pipelines de Flux de Données (Pipelines)

### Pipeline 1 : Authentification & Contrôle d'Accès RBAC
```
Client React (Dépôt email/pass)
       │
       ▼
FastAPI `/v1/auth/login` (Rate-limit IP via Redis)
       │
       ▼
SQLAlchemy (Lecture hash bcrypt `users` + Scope check)
       │
       ▼
Génération JWT (Encapsulation UserID, Rôle, JTI unique, Région)
       │
       ▼
Stockage LocalStorage (Frontend) -> Intercepté à chaque requête
```

### Pipeline 2 : Mutation de Propriété (Notaire)
```
Notaire rédige acte
       │
       ▼
Requête de mutation `/v1/notaire/actes`
       │
       ▼
Vérification `CHECK_CCFM_GATE` (CCFM valide existant)
       │
       ▼
Vérification `is_gele == False` (Absence de litige actif)
       │
       ▼
Calcul des parts héritières / acquéreurs (Validation 100% de quotité)
       │
       ▼
SQLAlchemy transaction `DroitVersionService.creer_nouvelle_version`
       │
       ▼
Calcul SHA-256 de version chaîné sur la version antérieure
       │
       ▼
Commit PostgreSQL (Rollback complet si échec)
       │
       ▼
Envoi asynchrone à l'ANNF (Tiers archiveur scellé)
```

### Pipeline 3 : Vectorisation & Overlap checking (BGU / GIS)
```
Géomètre charge plan DXF/SHP
       │
       ▼
Pipeline BGU `/v1/bgu` (Validation de la syntaxe géométrique)
       │
       ▼
Exécution requête PostGIS d'intersection spatiale :
`ST_Intersection(geom_nouveau, geom_existant)`
       │
       ▼
Si surface intersection > 0.05m² -> Alerte immédiate Fraud Check
       │
       ▼
Si OK -> Scellement WKT de la géométrie de la parcelle
```

---

## 🛠️ 4. Stack Technique de Référence

Le tableau ci-dessous recense les technologies du socle souverain FONCIER+ :

| Composant | Technologie Spécifiée | Rôle / Rationale |
| :--- | :--- | :--- |
| **Backend Framework** | FastAPI (Python 0.115.0) | Moteur d'API asynchrone ultra-rapide avec validation automatique Pydantic |
| **ORM** | SQLAlchemy (v2.0.35) | Abstraction de base de données performante supportant l'asynchronisme |
| **Base de Données** | PostgreSQL 16 + PostGIS | Moteur relationnel standard avec extensions géospatiales vectorielles |
| **Cache & Sessions** | Redis (v5.1.0) | Blacklist active des jetons, rate-limiting, et cache de session |
| **Object Storage** | MinIO (Compatible AWS S3) | Stockage on-premise hautement sécurisé des justificatifs et des PDFs |
| **Génération PDF** | ReportLab | Compilation dynamique et performante des maquettes A4/A5 scellées |
| **Frontend Framework** | React 18 + Vite | Interface utilisateur web réactive et performante pour la saisie administrative |
| **Cartographie** | Leaflet 1.9.4 | Librairie de rendu géographique et topologique côté navigateur |
