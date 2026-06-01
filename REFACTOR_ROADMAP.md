# 🚀 Plan Directeur de Refactoring & Modernisation (REFACTOR_ROADMAP.md)

**République du Niger**  
*Ministère de l'Urbanisme, de l'Habitat et du Domaine Foncier*  
*Gouvernance & Excellence Technologique GovTech*

---

> [!IMPORTANT]
> Cette feuille de route constitue le plan d'action souverain et stratégique pour amener la plateforme **FONCIER+** d'un prototype fonctionnel à un système industriel résilient, hautement sécurisé, et conforme aux plus hauts standards nationaux et internationaux de l'administration foncière.

---

## 🗺️ Index Chronologique des Phases

```
[Phase 1: Stabilisation] ➔ [Phase 2: Standardisation] ➔ [Phase 3: Sécurité] ➔ [Phase 4: Workflow]
                                                                                │
[Phase 8: Prod] ⬽ [Phase 7: Intelligence Artificielle] ⬽ [Phase 6: Anti-Fraude] ⬽ [Phase 5: GIS]
```

---

## 📋 Spécifications Détaillées des Phases

### 🛠️ PHASE 1 — Stabilisation
*   **Objectif** : Résolution immédiate des blocages de compilation, élimination des régressions de code et syntaxe.
*   **Actions Clés** :
    *   [x] Correction du bug de syntaxe critique sur la route d'archive `/dar/archives` dans [ccfm.py](file:///c:/Users/USER/Desktop/fonc%20final/backend/app/api/v1/endpoints/ccfm.py).
    *   [x] Correction des imports circulaires et invalides dans [service_registry.py](file:///c:/Users/USER/Desktop/fonc%20final/backend/app/api/v1/endpoints/service_registry.py) (déclarant la passerelle `/v1`).
    *   [x] Correction de l'import obsolète de `CREER_DROIT_VERSION` dans [workflow_engine.py](file:///c:/Users/USER/Desktop/fonc%20final/backend/app/services/workflow_engine.py).
    *   [ ] Mise en place d'une vérification automatique pré-commit (Linter Ruff / Pytest local) pour interdire l'insertion de code brisé.
*   **Indicateurs de Succès** : Zéro erreur de compilation Python au démarrage du backend.

---

### 📏 PHASE 2 — Standardisation
*   **Objectif** : Unification des schémas d'échange (Payloads) entre les clients React et l'API Passerelle FastAPI.
*   **Actions Clés** :
    *   [x] Alignement de l'API d'authentification [apiClient.ts](file:///c:/Users/USER/Desktop/fonc%20final/frontend/src/services/apiClient.ts) sur le dictionnaire `{ email, password }` attendu par le modèle Pydantic `LoginIn` (suppression de l'utilisation de `username`).
    *   [x] Unification des schémas d'enregistrement CCFM, raccordant le champ frontend `nip_passeport` au format requis par la base de données PostgreSQL.
    *   [ ] Standardisation des formats de réponse d'erreur de l'ensemble des contrôleurs REST (code HTTP standard, structure JSON uniforme avec message d'explication et de correction).
    *   [ ] Mise en conformité stricte des routes d'API avec les spécifications OpenAPI (Swagger).
*   **Indicateurs de Succès** : Zéro erreur `422 Unprocessable Entity` lors des appels d'API essentiels.

---

### 🔒 PHASE 3 — Sécurité
*   **Objectif** : Hardening du moteur d'authentification JWT et de la traçabilité administrative.
*   **Actions Clés** :
    *   [x] Introduction d'un identifiant de session unique **JTI (JWT ID)** cryptographique dans chaque jeton d'accès ou de rafraîchissement.
    *   [x] Intégration d'un système de blacklistage de session (`JWTBlacklist`) instantané lors du `/logout`.
    *   [x] Implémentation d'un **système dual de blacklist** dans [security.py](file:///c:/Users/USER/Desktop/fonc%20final/backend/app/core/security.py) (Redis en production, Set local en mémoire pour le développement sans dépendance externe).
    *   [x] Mise en œuvre de la segmentation par scopes territoriaux régionaux (`check_juridiction`) et gestion des délégations temporaires (`require_delegation`).
    *   [ ] Activation obligatoire de l'authentification multifacteur (MFA) pour les rôles sensibles (`DIRECTEUR_CADASTRE`, `JUGE_FONCIER`).
*   **Indicateurs de Succès** : Révocabilité immédiate d'un token volé vérifiée par tests d'intrusion.

---

### 🔄 PHASE 4 — Workflow Engine
*   **Objectif** : Consolider le moteur d'exécution et éliminer définitivement la duplication de logique métier.
*   **Actions Clés** :
    *   [x] Refactoring du contrôleur `/v1/ccfm` pour déléguer l'ensemble des écritures en base au service unique `CCFMWorkflowService`.
    *   [x] Correction du mécanisme de calcul de l'échéance de transition (`date_echeance`) avec extension de deadline proportionnelle lors d'un rejet de dossier.
    *   [x] Enclenchement du rollback transactionnel obligatoire de la base de données lors d'une rupture ou erreur de scellement d'une étape de workflow.
    *   [x] Raccordement des rôles de secours (`role_backup`) au filtrage des tâches à traiter (`todo_only`).
    *   [ ] Automatisation du job d'arrière-plan d'escalade et relance (SLA) via Celery / Redis.
*   **Indicateurs de Succès** : 100% des transitions CCFM et cadastrales passent exclusivement par la machine d'état unifiée.

---

### 📍 PHASE 5 — GIS
*   **Objectif** : Amélioration de l'intégrité topologique et traitement géospatial.
*   **Actions Clés** :
    *   [ ] Intégration des triggers spatiaux PostGIS au niveau PostgreSQL pour interdire l'enregistrement de géométries invalides ou auto-intersectées.
    *   [ ] Optimisation des requêtes de non-superposition (calcul d'intersection spatiale) avec index GIST spatiaux performants.
    *   [ ] Raccordement du module cartographique Leaflet du frontend à un serveur de tuiles géospatiales national souverain.
    *   [ ] Importation de la Base Géospatiale Unique (BGU) historique avec script de validation topologique massif (vectorisation des anciens plans papier).
*   **Indicateurs de Succès** : Taux d'empiétement non détecté égal à 0%.

---

### 🛡️ PHASE 6 — Anti-Fraude
*   **Objectif** : Inviolabilité des données, scellement cryptographique et auditabilité permanente.
*   **Actions Clés** :
    *   [x] Implémentation du scellement cryptographique SHA-256 unifié par étape de workflow.
    *   [ ] Signature numérique des documents PDF générés (Certificats CCFM, Titres Fonciers) avec certificat racine de l'État (Autorité de Certification nationale du Niger).
    *   [ ] Mise en place d'un réseau blockchain privé (Hyperledger Fabric) reliant les 7 institutions pour l'indexation décentralisée des hachages de scellement.
    *   [ ] Déploiement d'un tableau de bord de détection automatique des anomalies d'intégrité (comparaison temps réel entre le hash PostgreSQL et le hash Blockchain).
*   **Indicateurs de Succès** : Détection instantanée de toute modification directe frauduleuse de la base de données.

---

### 🤖 PHASE 7 — IA
*   **Objectif** : Automatisation intelligente de l'instruction et aide à la décision.
*   **Actions Clés** :
    *   [x] Automatisation de la génération du rapport d'appréciation technique (analyse d'écart de surface et de géométrie).
    *   [ ] Intégration d'un modèle d'analyse d'images satellites pour valider de manière autonome la mise en valeur réelle d'une parcelle (détection de bâtis/cultures pour le passage au Titre Foncier).
    *   [ ] Déploiement d'un agent conversationnel IA sécurisé pour guider les demandeurs sur les pièces justificatives manquantes selon leur situation.
    *   [ ] Analyse prédictive des conflits fonciers potentiels par traitement automatique du langage naturel (NLP) des plaintes historiques du TGI.
*   **Indicateurs de Succès** : Réduction de 40% du temps d'instruction administrative.

---

### 🚀 PHASE 8 — Industrialisation
*   **Objectif** : Déploiement à l'échelle nationale, résilience des infrastructures et monitoring.
*   **Actions Clés** :
    *   [ ] Déploiement de l'architecture microservices (8 services isolés) sous conteneurs Docker/Compose avec équilibrage de charge Nginx.
    *   [ ] Automatisation du plan de sauvegarde périodique de la base de données PostgreSQL (sauvegardes chiffrées distantes toutes les 6 heures).
    *   [ ] Configuration du monitoring d'application en production via Sentry (FastAPI) et Prometheus/Grafana pour les ressources système.
    *   [ ] Exécution des scripts de seeds industriels (initialisation automatique de la structure administrative avec les 45 utilisateurs et 29 rôles régionaux).
    *   [ ] Passage à 100% de couverture de tests sur les routes d'API critiques.
*   **Indicateurs de Succès** : Taux de disponibilité annuel de la plateforme supérieur à 99.9%.
