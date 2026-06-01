# 📋 Registre National des Formulaires Fonciers (FORM_REGISTRY.md)

**République du Niger**  
*Ministère de l'Urbanisme, de l'Habitat et du Domaine Foncier*  
*Spécifications des Schémas de Saisie Administrative & Légale (v4.0)*

---

## 🗺️ Index des Formulaires Documentés

1. [F-CAD-01 : Création Cadastrale (Immatriculation)](#f-cad-01--creation-cadastrale-immatriculation)
2. [F-CCFM-01 : Demande de Conformité Foncière](#f-ccfm-01--demande-de-conformite-fonciere)
3. [F-NOT-01 : Acte de Mutation Notariale](#f-not-01--acte-de-mutation-notariale)
4. [F-BANQ-01 : Inscription d'Hypothèque Bancaire](#f-banq-01--inscription-dhypotheque-bancaire)
5. [F-JUST-01 : Demande de Gel Conservatoire Judiciaire](#f-just-01--demande-de-gel-conservatoire-judiciaire)

---

## 📋 Spécifications Détaillées des Formulaires

### F-CAD-01 : Création Cadastrale (Immatriculation)

*   **Description** : Formulaire technique de saisie pour l'immatriculation d'une parcelle de terrain à la Base Géospatiale Unique.
*   **Acteur Habilité** : `INGENIEUR_CADASTRE` / `DIRECTEUR_CADASTRE`

| Clé JSON Payload | Type de Donnée | UI Rendu Control | Règle de Saisie / Description |
| :--- | :--- | :--- | :--- |
| `commune_id` | `String (UUID)` | Menu Déroulant | Identifiant de la commune territoriale d'affectation |
| `surface_officielle` | `Float` | Champ Numérique | Superficie en m² (précision 2 décimales obligatoires) |
| `geojson_data` | `Object (GeoJSON)`| Carte Interactive | Polygone spatial de limites (coordonnées WGS 84) |
| `arrete_rnaf_id` | `String (UUID)` | Recherche Auto | Identifiant de l'arrêté régional source publié au RNAF |

---

### F-CCFM-01 : Demande de Conformité Foncière

*   **Description** : Formulaire d'ouverture d'un dossier pour la délivrance du Certificat de Conformité Foncière Métropolitaine (CCFM).
*   **Acteur Habilité** : `GUICHETIER_CCFM`

| Clé JSON Payload | Type de Donnée | UI Rendu Control | Règle de Saisie / Description |
| :--- | :--- | :--- | :--- |
| `nicad` | `String` | Champ Texte Alpha | Identifiant Cadastral unique de la parcelle ciblée |
| `demandeur_nom` | `String` | Champ Texte | Nom complet et prénoms du demandeur (propriétaire) |
| `nip_passeport` | `String` | Champ Texte Alpha | Numéro d'Identification Personnel ou passeport valide |
| `quittance_ref` | `String` | Champ Texte Alpha | Référence de la quittance municipale de 50 000 FCFA |

---

### F-NOT-01 : Acte de Mutation Notariale

*   **Description** : Formulaire d'instruction pour le transfert de propriété entre parties (vente ou succession).
*   **Acteur Habilité** : `NOTAIRE`

| Clé JSON Payload | Type de Donnée | UI Rendu Control | Règle de Saisie / Description |
| :--- | :--- | :--- | :--- |
| `nicad` | `String` | Champ Texte Alpha | NICAD de la parcelle à muter |
| `ccfm_nus` | `String` | Recherche Auto | NUS du certificat CCFM actif et valide |
| `vendeurs` | `Array [Object]` | Tableau Dynamique | Liste des vendeurs avec nom, quotité (part) et NIP |
| `acquereurs` | `Array [Object]` | Tableau Dynamique | Liste des acquéreurs avec nom, quotité (part) et NIP |
| `prix_vente` | `Float` | Champ Numérique | Prix de transaction en FCFA (supérieur à 0) |
| `acte_vente_url` | `String (URL)` | Upload Fichier | Fichier PDF de l'acte de vente notarié signé |

---

### F-BANQ-01 : Inscription d'Hypothèque Bancaire

*   **Description** : Demande d'affectation hypothécaire d'une parcelle pour la garantie d'un prêt de crédit bancaire.
*   **Acteur Habilité** : `BANQ_AGENT`

| Clé JSON Payload | Type de Donnée | UI Rendu Control | Règle de Saisie / Description |
| :--- | :--- | :--- | :--- |
| `nicad` | `String` | Champ Texte Alpha | NICAD du terrain mis en hypothèque |
| `ccfm_nus` | `String` | Recherche Auto | Certificat CCFM valide obligatoirement rattaché |
| `montant_credit` | `Float` | Champ Numérique | Valeur du crédit garanti en FCFA |
| `banque_holder_id`| `String (UUID)` | Non Modifiable | Identifiant de l'institution bancaire demandeuse |
| `duree_mois` | `Integer` | Slider Numérique | Durée d'endettement en mois (min=12, max=360) |

---

### F-JUST-01 : Demande de Gel Conservatoire Judiciaire

*   **Description** : Formulaire d'urgence de verrouillage cadastral ordonné par le Greffe du Tribunal.
*   **Acteur Habilité** : `GREFFIER_TGI` / `JUGE_FONCIER`

| Clé JSON Payload | Type de Donnée | UI Rendu Control | Règle de Saisie / Description |
| :--- | :--- | :--- | :--- |
| `nicad` | `String` | Champ Texte Alpha | NICAD de la parcelle sous litige |
| `role_general_ref`| `String` | Champ Texte Alpha | Numéro de rôle général (RG) du procès au TGI |
| `motif_suspension`| `String` | Zone de Texte | Motif légal du gel (ex: contestation de bornage) |
| `ordonnance_pdf` | `String (URL)` | Upload Fichier | Copie scannée de l'ordonnance judiciaire de gel signée |
