# 🐛 Registre des Anomalies & Bugs Applicatifs (BUG_REGISTRY.md)

**République du Niger**  
*Ministère de l'Urbanisme, de l'Habitat et du Domaine Foncier*  
*Index de Suivi QA des Anomalies Système*

---

> [!IMPORTANT]
> Ce registre assure le suivi rigoureux des défauts logiciels identifiés au cours de l'instruction technique et de l'audit d'assurance qualité (QA). Le statut `RÉSOLU` indique que le code correspondant a été corrigé et validé par l'auditeur.

---

## 📋 1. Fiches de Suivi des Anomalies

### BUG-001 : Import Circulaire dans le Registre de Service
*   **Module Impacté** : `API Gateway` / `service_registry.py`
*   **Symptômes** : Exception `ImportError` fatale empêchant le démarrage de l'application FastAPI lors de l'initialisation des routeurs de la version `/v1`.
*   **Statut** : **RÉSOLU**
*   **Correction Appliquée** : Refactoring des dépendances de services et passage à un patron d'initialisation différée des instances de base de données.

### BUG-002 : Mismatch de Payload d'Authentification Frontend/Backend
*   **Module Impacté** : `AUTH` / `apiClient.ts`
*   **Symptômes** : Erreur `422 Unprocessable Entity` systématique retournée par le serveur lors d'une tentative de connexion, causée par la clé `{ username }` transmise à la place de la clé `{ email }` attendue par le modèle de validation backend `LoginIn`.
*   **Statut** : **RÉSOLU**
*   **Correction Appliquée** : Alignement de la structure d'appel d'Axios dans `apiClient.ts` pour transmettre la clé correcte `{ email, password }`.

### BUG-003 : Syntaxe Invalide de Commentaire Inline dans la Passerelle CCFM
*   **Module Impacté** : `CCFM` / `ccfm.py`
*   **Symptômes** : Erreur de parsing de l'interpréteur Python (`SyntaxError: invalid syntax`) bloquant le démarrage des conteneurs à la ligne déclarant l'endpoint d'archive `/dar/archives`.
*   **Statut** : **RÉSOLU**
*   **Correction Appliquée** : Nettoyage de la route, suppression de l'inline brisé et standardisation des commentaires d'API.

### BUG-004 : Écart d'Arrondi sur les Surfaces Géodésiques PostGIS
*   **Module Impacté** : `GIS` / `parcellaire_service.py`
*   **Symptômes** : Rejets injustifiés de conformité topo lors de la soumission de plans géométriques valides, causés par des micro-écarts de calcul flottants (ex: 450.0000001 m² au lieu de 450.00 m²).
*   **Statut** : **RÉSOLU**
*   **Correction Appliquée** : Utilisation systématique de la fonction PostGIS `ST_SnapToGrid` pour normaliser les points topographiques et arrondi applicatif à 2 décimales via le type `Decimal` de Python.

---

## 📊 2. Tableau de Bord QA des Correctifs

| ID Bug | Priorité | Description de l'Anomalie | Acteur Correcteur | Statut |
| :--- | :--- | :--- | :--- | :--- |
| **BUG-001** | Critique | Import circulaire sur Gateway `/v1` | QA Auditor | **RÉSOLU** |
| **BUG-002** | Critique | Formulaire d'authentification brisé | Frontend UX | **RÉSOLU** |
| **BUG-003** | Critique | Syntaxe invalide sur `/dar/archives` | QA Auditor | **RÉSOLU** |
| **BUG-004** | Élevée | Micro-écarts géométriques flottants | GIS Engine | **RÉSOLU** |
| **BUG-005** | Moyenne | Injection de caractères dans filtres SQL | QA Auditor | **RÉSOLU** |
