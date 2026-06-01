# Système et Domaines Métiers (FONCIER+)

## 1. VUE D'ENSEMBLE
FONCIER+ est une plateforme GovTech nationale. Elle fusionne la gestion cadastrale, l'administration notariale, l'urbanisme et le cadastre en intégrant étroitement des processus documentaires, des données relationnelles transactionnelles et des données spatiales géospatiales.

---

## 2. GOUVERNANCE MÉTIER (Cartographie des Agents par Domaine)

### A. Gestion Foncière, Cadastre & SIG
- **Description** : Gestion des parcelles physiques, limites géographiques, lotissements municipaux et réserves foncières étatiques.
- **Entités** : Parcelles, Lotissements, Coordonnées UTM, Polygones géospatiaux.
- **Moteur Géospatial** : PostGIS (SRID 4326 / UTM 31N/32N), triggers topologiques de non-chevauchement.
- **Agents Responsables** :
  - 👤 **[GIS ENGINE](file:///c:/Users/USER/Desktop/fonc%20final/skills/7_gis_engine/README.md)** (Propriétaire Spatial : validation géométrique, surface et intersections).
  - 👤 **[BACKEND ENGINEER](file:///c:/Users/USER/Desktop/fonc%20final/skills/4_backend_engineer/README.md)** (Modèles ORM et persistance des entités parcelles).

### B. Administration & Gestion des 33 Workflows
- **Description** : Circuits d'instruction, d'approbation et de validation des dossiers de titres ou d'autorisation.
- **Entités** : Dossiers, Actions de transition, Historique d'instruction, Notes administratives.
- **Contrôles** : Validation hiérarchique stricte basée sur les 29 rôles RBAC.
- **Agents Responsables** :
  - 👤 **[WORKFLOW ENGINE](file:///c:/Users/USER/Desktop/fonc%20final/skills/3_workflow_engine/README.md)** (Propriétaire Processus : machine à états finis, transitions de dossiers).
  - 👤 **[GOVTECH COMPLIANCE](file:///c:/Users/USER/Desktop/fonc%20final/skills/9_govtech_compliance/README.md)** (Garant Légal : conformité des étapes réglementaires et mentions).

### C. Sécurité, Contrôle d'Accès & Anti-Fraude
- **Description** : Étanchéité de la plateforme, traçabilité des modifications, authentification et auditabilité de la chaîne foncière.
- **Entités** : Utilisateurs, Sessions, Audit Logs, Rôles, Clés de signature.
- **Moteur Anti-Fraude** : Détection des anomalies d'identité, détections d'accès suspects, journalisation immutable.
- **Agents Responsables** :
  - 👤 **[SECURITY ENGINE](file:///c:/Users/USER/Desktop/fonc%20final/skills/6_security_engine/README.md)** (Gardien Sécurité : chiffrements, RBAC, jetons, et signatures X.509).
  - 👤 **[QA AUDITOR](file:///c:/Users/USER/Desktop/fonc%20final/skills/5_qa_auditor/README.md)** (Garant Qualité : audits de non-régression, exécution automatisée de tests de vulnérabilité logique).

### D. Production Documentaire & Conservation des Archives
- **Description** : Génération automatisée de documents à valeur juridique probante et archivage à long terme.
- **Entités** : Titres Fonciers (PDF), Reçus Officiels, Certificats de non-conflit, Fichiers notariaux.
- **Opérations** : Rendu HTML-to-PDF haute fidélité, encodage QR code cryptographique.
- **Agents Responsables** :
  - 👤 **[DOCUMENT ENGINE](file:///c:/Users/USER/Desktop/fonc%20final/skills/8_document_engine/README.md)** (Constructeur Documentaire : moteur de rendu PDF, indexation d'archives).
  - 👤 **[FRONTEND UX](file:///c:/Users/USER/Desktop/fonc%20final/skills/10_frontend_ux/README.md)** (Visualisation Interface : visualiseur PDF, dashboards et rapports graphiques).

---

## 3. PRINCIPES FONDAMENTAUX DE CONCEPTION SYSTÈME
1. **Source de Vérité Unique** : La base de données PostgreSQL/PostGIS détient la vérité souveraine absolue. L'état frontend est une projection réactive et asynchrone de celle-ci.
2. **Couplage Lâche et Bus d'Orchestration** : Le **[System Architect](file:///c:/Users/USER/Desktop/fonc%20final/skills/2_system_architect/README.md)** impose des contrats d'interfaces stricts entre les services pour garantir qu'un bug UI ou cartographique ne bloque jamais une transaction de validation notariale critique.
3. **Auditabilité par Défaut** : Aucun agent ni utilisateur ne peut modifier l'état du système sans générer une entrée d'audit non-modifiable, assurant la conformité aux directives du Niger.
