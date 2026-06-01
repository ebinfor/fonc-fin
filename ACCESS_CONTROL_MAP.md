# 🗺️ Carte du Contrôle d'Accès aux APIs (ACCESS_CONTROL_MAP.md)

**République du Niger**  
*Ministère de l'Urbanisme, de l'Habitat et du Domaine Foncier*  
*Spécifications des Habilitations Techniques des Passerelles*

---

> [!NOTE]
> Cette carte répertorie l'ensemble des fichiers contrôleurs du backend et explicite les barrières d'authentification (`require_role`) et de segmentation (`ScopeFilter`) codées pour chaque route d'API.

---

## 🗺️ 1. Mappage des Fichiers de Contrôleurs & Routes

### A. Passerelle Authentification & Session
*   **Fichier Backend** : [auth.py](file:///c:/Users/USER/Desktop/fonc%20final/backend/app/api/v1/endpoints/auth.py)

| Endpoint / Méthode | Action Métier | Rôles Habilités | Gardes de Sécurité / Dépendances |
| :--- | :--- | :--- | :--- |
| `POST /auth/login` | Connexion | Public (Tous) | Rate-limit (5 req/min) |
| `POST /auth/logout`| Déconnexion | Connecté (Tous) | `Depends(get_current_user)` (Vérifie JTI Blacklist) |
| `POST /auth/mfa` | Validation MFA | Connecté (Tous) | `Depends(get_current_user)` + Validation TOTP |

---

### B. Registre Cadastral & Cartographie
*   **Fichier Backend** : [cadastre_parcellaire.py](file:///c:/Users/USER/Desktop/fonc%20final/backend/app/api/v1/endpoints/cadastre_parcellaire.py)

| Endpoint / Méthode | Action Métier | Rôles Habilités | Gardes de Sécurité / Dépendances |
| :--- | :--- | :--- | :--- |
| `GET /parcelles` | Liste des parcelles| Connecté (Tous) | `Depends(get_current_user)` + **`ScopeFilter` automatique** |
| `POST /parcelles` | Créer une parcelle | `INGENIEUR_CADASTRE`, `DIRECTEUR_CADASTRE` | `Depends(require_role)` + `Depends(check_juridiction)` |
| `GET /parcelles/{nicad}`| Fiche parcelle | Connecté (Tous) | `Depends(get_current_user)` |
| `DELETE /parcelles/{nicad}`| Radiation parcelle| `DIRECTEUR_CADASTRE` national | `Depends(require_role(["DIRECTEUR_CADASTRE"]))` |

---

### C. Certification de Conformité Métropolitaine (CCFM)
*   **Fichier Backend** : [ccfm.py](file:///c:/Users/USER/Desktop/fonc%20final/backend/app/api/v1/endpoints/ccfm.py)

| Endpoint / Méthode | Action Métier | Rôles Habilités | Gardes de Sécurité / Dépendances |
| :--- | :--- | :--- | :--- |
| `GET /ccfm/demandes` | Liste des demandes | `GUICHETIER_CCFM`, `CHEF_CCFM`, `DIRECTEUR_URBANISME` | `Depends(get_current_user)` + **`ScopeFilter` automatique** |
| `POST /ccfm/demandes` | Créer une demande | `GUICHETIER_CCFM` | `Depends(require_role(["GUICHETIER_CCFM"]))` |
| `POST /ccfm/demandes/{id}/constat` | Saisie du constat terrain | `TOPOGRAPHE_CCFM` | `Depends(require_role(["TOPOGRAPHE_CCFM"]))` + `check_juridiction` |
| `POST /ccfm/demandes/{id}/signer` | Signer certificat | `DIRECTEUR_URBANISME` | `Depends(require_role(["DIRECTEUR_URBANISME"]))` + X.509 signature |

---

### D. Justice & Litiges Judiciaires
*   **Fichier Backend** : [justice.py](file:///c:/Users/USER/Desktop/fonc%20final/backend/app/api/v1/endpoints/justice.py)

| Endpoint / Méthode | Action Métier | Rôles Habilités | Gardes de Sécurité / Dépendances |
| :--- | :--- | :--- | :--- |
| `POST /justice/litiges/gel` | Gel conservatoire | `JUGE_FONCIER`, `GREFFIER_TGI` | `Depends(require_role)` ➔ Met `parcelles.is_gele` à True |
| `POST /justice/litiges/{block_id}/degel`| Levée du gel | `JUGE_FONCIER` | `Depends(require_role)` ➔ Met `parcelles.is_gele` à False |

---

## 🛡️ 2. Gardes et Filtres Système Réutilisables (Security Guards)

*   **`Depends(get_current_user)`** : Vérifie l'authenticité de la session en extrayant le jeton JWT de l'en-tête HTTP `Authorization`. Interroge la base/cache de blacklist pour s'assurer que la session n'a pas été révoquée.
*   **`Depends(require_role(roles_list))`** : S'assure que le compte de l'utilisateur connecté détient au moins l'un des rôles autorisés de la liste pour accéder à la méthode de l'API.
*   **`Depends(check_juridiction)`** : Récupère le champ géographique (région, commune) de l'utilisateur connecté et le confronte à la juridiction territoriale du dossier ciblé. Lève un code d'erreur `403 Forbidden` en cas d'incohérence.
*   **`ScopeFilter` (Injection SQL)** : Middleware de base de données interceptant la génération de requêtes par l'ORM. Il injecte de manière invisible et automatique la clause `WHERE region = :user_region` ou `WHERE commune_id = :user_commune` lors de la consultation des tables cadastrales ou CCFM, interdisant toute fuite d'information transverse.
