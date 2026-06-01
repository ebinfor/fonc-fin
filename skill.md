# Référentiel des Skills (Rôles Internes)

Ce document définit l'équipe IA spécialisée et les rôles générés pour orchestrer la plateforme GovTech nationale **FONCIER+ / e-LOTIS**.

## 📊 Table des Rôles & Niveaux de Priorité

| Ordre | Rôle / Skill | Catégorie | Priorité | Fiche Spécifique |
| :---: | :--- | :--- | :---: | :--- |
| **1** | **META ORCHESTRATOR** | Gouvernance Cognitive | **Critique** | [1_meta_orchestrator](file:///c:/Users/USER/Desktop/fonc%20final/skills/1_meta_orchestrator/README.md) |
| **2** | **SYSTEM ARCHITECT** | Infrastructure & Structure | **Critique** | [2_system_architect](file:///c:/Users/USER/Desktop/fonc%20final/skills/2_system_architect/README.md) |
| **3** | **WORKFLOW ENGINE** | États & Logique Métier | **Critique** | [3_workflow_engine](file:///c:/Users/USER/Desktop/fonc%20final/skills/3_workflow_engine/README.md) |
| **4** | **BACKEND ENGINEER** | APIs & Données | **Critique** | [4_backend_engineer](file:///c:/Users/USER/Desktop/fonc%20final/skills/4_backend_engineer/README.md) |
| **5** | **QA AUDITOR** | Tests & Non-régression | **Très haute** | [5_qa_auditor](file:///c:/Users/USER/Desktop/fonc%20final/skills/5_qa_auditor/README.md) |
| **6** | **SECURITY ENGINE** | Cryptographie & RBAC | **Très haute** | [6_security_engine](file:///c:/Users/USER/Desktop/fonc%20final/skills/6_security_engine/README.md) |
| **7** | **GIS ENGINE** | Spatial & Topologie | **Haute** | [7_gis_engine](file:///c:/Users/USER/Desktop/fonc%20final/skills/7_gis_engine/README.md) |
| **8** | **DOCUMENT ENGINE** | Automatisation PDF/Archives | **Haute** | [8_document_engine](file:///c:/Users/USER/Desktop/fonc%20final/skills/8_document_engine/README.md) |
| **9** | **GOVTECH COMPLIANCE** | Conformité Réglementaire | **Haute** | [9_govtech_compliance](file:///c:/Users/USER/Desktop/fonc%20final/skills/9_govtech_compliance/README.md) |
| **10** | **FRONTEND UX** | Interface & Accessibilité | **Haute** | [10_frontend_ux](file:///c:/Users/USER/Desktop/fonc%20final/skills/10_frontend_ux/README.md) |

---

## 🛠️ Description Détaillée des Rôles

### 1. [META ORCHESTRATOR](file:///c:/Users/USER/Desktop/fonc%20final/skills/1_meta_orchestrator/README.md)
- **Responsabilités** : Coordination multi-agent, décomposition des exigences complexes, contrôle d'intégrité globale, routage cognitif, arbitrage des choix techniques.
- **Focus** : Cohérence logique absolue entre le code, la base de données, la sécurité, et l'interface utilisateur.
- **Standards** : Supervision totale, traçabilité des décisions des agents.

### 2. [SYSTEM ARCHITECT](file:///c:/Users/USER/Desktop/fonc%20final/skills/2_system_architect/README.md)
- **Responsabilités** : Structure globale du projet, modularité, transition Flask ➔ FastAPI, conception des modèles de données, intégration microservices.
- **Focus** : Performance, extensibilité à l'échelle nationale, découplage des domaines.
- **Standards** : Principes SOLID, patron Strangler Fig, haute modularité.

### 3. [WORKFLOW ENGINE](file:///c:/Users/USER/Desktop/fonc%20final/skills/3_workflow_engine/README.md)
- **Responsabilités** : Automatisation et contrôle des 33 workflows nationaux, validation des transitions d'états, protection contre les interblocages (deadlocks).
- **Focus** : Fiabilité absolue des dossiers (Brouillon ➔ Soumis ➔ En instruction ➔ En validation ➔ Approuvé / Rejeté).
- **Standards** : Machines à états déterministes, journaux de décision (audit trails) immutables.

### 4. [BACKEND ENGINEER](file:///c:/Users/USER/Desktop/fonc%20final/skills/4_backend_engineer/README.md)
- **Responsabilités** : Logique métier en Python, développement d'APIs robustes, requêtes SQL optimisées, modélisation ORM SQLAlchemy/Pydantic.
- **Focus** : Rapidité d'exécution, typage fort, résilience aux pannes, intégrité des transactions.
- **Standards** : Clean Code, requêtes paramétrées, couverture de tests unitaires systématique.

### 5. [QA AUDITOR](file:///c:/Users/USER/Desktop/fonc%20final/skills/5_qa_auditor/README.md)
- **Responsabilités** : Conception et exécution des plans de tests (unitaires, intégration, E2E), rapports de couverture, détection proactive de régressions.
- **Focus** : Maintien du verrou de release (100% de succès requis sur les 1005 cas de tests).
- **Standards** : Pytest, tests basés sur des cas métier réels (doublons, chevauchements).

### 6. [SECURITY ENGINE](file:///c:/Users/USER/Desktop/fonc%20final/skills/6_security_engine/README.md)
- **Responsabilités** : Sécurisation des APIs, contrôle d'accès granulaire RBAC (29 rôles), chiffrement, signature numérique X.509, lutte anti-fraude.
- **Focus** : Confidentialité des données cadastrales et souveraines, protection documentaire.
- **Standards** : Zero Trust, conformité OWASP Top 10, hashes cryptographiques dans les QR codes.

### 7. [GIS ENGINE](file:///c:/Users/USER/Desktop/fonc%20final/skills/7_gis_engine/README.md)
- **Responsabilités** : Moteur spatial PostGIS, calculs géométriques (surfaces, contours), détection de chevauchement topologique, GeoJSON APIs.
- **Focus** : Précision géographique absolue, prévention automatique des conflits de limites parcellaires.
- **Standards** : Utilisation stricte d'index spatiaux (`GIST`), vérification de validité de géométrie (`ST_IsValid`).

### 8. [DOCUMENT ENGINE](file:///c:/Users/USER/Desktop/fonc%20final/skills/8_document_engine/README.md)
- **Responsabilités** : Génération automatique de documents PDF officiels (Titres fonciers, reçus de dépôt), gestion de templates CSS d'impression, archivage légal.
- **Focus** : Esthétique premium et rigueur des documents officiels, intégration des QR codes cryptographiques.
- **Standards** : Weasyprint/ReportLab, archivage hiérarchique immutable.

### 9. [GOVTECH COMPLIANCE](file:///c:/Users/USER/Desktop/fonc%20final/skills/9_govtech_compliance/README.md)
- **Responsabilités** : Conformité vis-à-vis des lois foncières et d'urbanisme du Niger, validation des mentions légales obligatoires, audit réglementaire.
- **Focus** : Respect des règles et procédures administratives, vérification de la légalité numérique.
- **Standards** : Alignement législatif complet, conformité notariale et cadastrale.

### 10. [FRONTEND UX](file:///c:/Users/USER/Desktop/fonc%20final/skills/10_frontend_ux/README.md)
- **Responsabilités** : Interfaces React, design system avec Tailwind CSS & ShadCN, micro-animations (Framer Motion), carte Leaflet, formulaires intelligents.
- **Focus** : Expérience utilisateur (UX) simple, fluide, claire et sans ambiguïté pour les agents de l'État.
- **Standards** : Accessibilité, validation côté client rigoureuse, temps de rendu < 100ms.
