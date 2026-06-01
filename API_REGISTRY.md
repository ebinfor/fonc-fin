# 🌐 Registre Exhaustif des API Passerelles (API_REGISTRY.md)

**République du Niger**  
*Ministère de l'Urbanisme, de l'Habitat et du Domaine Foncier*  
*Index & Contrats de Service des Passerelles Numériques (v4.0)*

---

> [!IMPORTANT]
> Tous les appels aux endpoints listés ci-dessous (à l'exception de l'authentification initiale) requièrent la transmission du jeton d'accès dans l'en-tête HTTP : `Authorization: Bearer <JWT_TOKEN>`. Le non-respect du format de payload déclenche une erreur standardisée `422 Unprocessable Entity`.

---

## 🔑 1. API AUTHENTIFICATION & SESSIONS (`/api/v1/auth`)

### 🔓 Login Administrateur / Agent
*   **Path** : `/api/v1/auth/login`
*   **Méthode** : `POST`
*   **Description** : Authentification et délivrance des tokens JWT (Access & Refresh).
*   **Permissions** : Public (Rate-limit actif).
*   **Payload (JSON)** :
    ```json
    {
      "email": "agent.niamey@foncier.ne",
      "password": "SecurePassword123!"
    }
    ```
*   **Réponse (200 OK)** :
    ```json
    {
      "access_token": "eyJhbGciOiJIUzI1NiIsIn...",
      "refresh_token": "eyJhbGciOiJIUzI1NiIsIn...",
      "token_type": "bearer",
      "expires_in": 3600
    }
    ```

### 🔒 Déconnexion (Session Revocation)
*   **Path** : `/api/v1/auth/logout`
*   **Méthode** : `POST`
*   **Description** : Révocation immédiate de la session et enregistrement du JTI dans la liste noire.
*   **Permissions** : Connecté (`require_role` : Tous).
*   **Réponse (200 OK)** :
    ```json
    {
      "message": "Session révoquée avec succès"
    }
    ```

---

## 👤 2. API GESTION DES UTILISATEURS (`/api/v1/users`)

### 📋 Liste des Comptes Nationaux
*   **Path** : `/api/v1/users`
*   **Méthode** : `GET`
*   **Description** : Liste des comptes (segmentée automatiquement selon la juridiction de l'utilisateur demandeur).
*   **Permissions** : `ADMIN`, `AUDITEUR`.
*   **Paramètres Query** : `page` (int, default=1), `limit` (int, default=50).
*   **Réponse (200 OK)** :
    ```json
    [
      {
        "id": "usr_99812",
        "email": "agent.niamey@foncier.ne",
        "role": "INGENIEUR_CADASTRE",
        "region": "Niamey",
        "est_actif": true
      }
    ]
    ```

---

## 🗺️ 3. API CADASTRE & PARCELLES (`/api/v1/cadastre`)

### 📍 Immatriculation d'une Nouvelle Parcelle
*   **Path** : `/api/v1/cadastre/parcelles`
*   **Méthode** : `POST`
*   **Description** : Enregistrement cadastral initial et attribution automatique du NICAD.
*   **Permissions** : `INGENIEUR_CADASTRE`, `DIRECTEUR_CADASTRE`.
*   **Payload (JSON)** :
    ```json
    {
      "commune_id": "com_01",
      "surface_officielle": 450.25,
      "geojson_data": {
        "type": "Feature",
        "geometry": {
          "type": "Polygon",
          "coordinates": [[[2.109, 13.512], [2.110, 13.512], [2.110, 13.511], [2.109, 13.511], [2.109, 13.512]]]
        }
      },
      "arrete_rnaf_id": "rnaf_2026_099"
    }
    ```
*   **Réponse (201 Created)** :
    ```json
    {
      "id": "par_44510",
      "nicad": "NICAD-NE-NY-01-44510",
      "surface_officielle": 450.25,
      "is_gele": false
    }
    ```

### 🔍 Consultation par NICAD
*   **Path** : `/api/v1/cadastre/parcelles/{nicad}`
*   **Méthode** : `GET`
*   **Description** : Récupération complète de la fiche administrative, historique des droits (WORM), et charges d'une parcelle.
*   **Permissions** : Connecté (Tous).
*   **Réponse (200 OK)** :
    ```json
    {
      "nicad": "NICAD-NE-NY-01-44510",
      "surface": 450.25,
      "is_gele": false,
      "proprietaires": ["Elhadj Moussa Kassa"],
      "charges": ["Hypothèque Banque BOA active"],
      "historique_versions_count": 3
    }
    ```

---

## 🛡️ 4. API CERTIFICATE OF CONFORMITY (`/api/v1/ccfm`)

### ✍️ Enregistrement d'une Demande CCFM
*   **Path** : `/api/v1/ccfm/demandes`
*   **Méthode** : `POST`
*   **Description** : Enregistrement de la demande, paiement des droits d'instruction de 50 000 FCFA.
*   **Permissions** : `GUICHETIER_CCFM`.
*   **Payload (JSON)** :
    ```json
    {
      "nicad": "NICAD-NE-NY-01-44510",
      "demandeur_nom": "Mahamadou Issoufou",
      "nip_passeport": "NIP-9988112",
      "quittance_paiement_ref": "TX-MUNI-998271"
    }
    ```
*   **Réponse (201 Created)** :
    ```json
    {
      "id": "ccfm_88291",
      "nus": "NUS-NE-2026-88291",
      "statut": "CREEE",
      "date_creation": "2026-05-17T15:47:00Z"
    }
    ```

### 🖋️ Signature & Délivrance du Certificat CCFM
*   **Path** : `/api/v1/ccfm/demandes/{id}/signer`
*   **Méthode** : `POST`
*   **Description** : Signature numérique X.509 et versement de l'acte au format WORM dans l'ANNF.
*   **Permissions** : `DIRECTEUR_URBANISME`.
*   **Réponse (200 OK)** :
    ```json
    {
      "id": "ccfm_88291",
      "statut": "SIGNE",
      "hash_scellement": "sha256:d8f923c10b78ee4b37f48e...",
      "pdf_telechargement_url": "https://s3.foncier.ne/ccfm/certificat_88291.pdf"
    }
    ```

---

## ⚖️ 5. API JUSTICE & LITIGES (`/api/v1/justice`)

### 🔒 Gel Conservatoire de Parcelle (TGI)
*   **Path** : `/api/v1/justice/litiges/gel`
*   **Méthode** : `POST`
*   **Description** : Blocage immédiat de toutes les mutations de propriété sur une parcelle querellée.
*   **Permissions** : `JUGE_FONCIER`.
*   **Payload (JSON)** :
    ```json
    {
      "nicad": "NICAD-NE-NY-01-44510",
      "numero_role_general": "RG-2026-554",
      "motif_suspension": "Contestation de délimitation de bornage par les héritiers Kassa"
    }
    ```
*   **Réponse (200 OK)** :
    ```json
    {
      "nicad": "NICAD-NE-NY-01-44510",
      "is_gele": true,
      "block_id": "block_99812",
      "statut": "SUSPENDU"
    }
    ```

### 🔓 Mainlevée Judiciaire (Dégel)
*   **Path** : `/api/v1/justice/litiges/{block_id}/degel`
*   **Méthode** : `POST`
*   **Description** : Levée du gel conservatoire suite à une sentence judiciaire passée en force de chose jugée.
*   **Permissions** : `JUGE_FONCIER`.
*   **Payload (JSON)** :
    ```json
    {
      "reference_jugement": "JUG-TGI-2026-88"
    }
    ```
*   **Réponse (200 OK)** :
    ```json
    {
      "nicad": "NICAD-NE-NY-01-44510",
      "is_gele": false,
      "statut": "ACTIF"
    }
    ```
