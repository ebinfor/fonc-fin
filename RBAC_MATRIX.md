# 🔐 Matrice des Rôles & Habilitations (RBAC Matrix)

**République du Niger**  
*Ministère de l'Urbanisme, de l'Habitat et du Domaine Foncier*  
*Moteur de Sécurité & Contrôle d'Accès de Production*

---

> [!IMPORTANT]
> Cette matrice définit de manière absolue les permissions d'accès et d'action de chaque acteur au sein du système national **FONCIER+**. Elle régit les vérifications d'authentification (`require_role`), la segmentation géographique (`check_juridiction`), et les délégations administratives (`require_delegation`) appliquées au niveau des API Passerelles.

---

## 🗺️ 1. Tableau Synthétique de la Matrice RBAC

| Rôle Foncier+ | Lire (Qui voit quoi ?) | Modifier (Qui saisit quoi ?) | Valider (Qui approuve quoi ?) | Archiver (Qui scelle quoi ?) |
| :--- | :--- | :--- | :--- | :--- |
| **`ADMIN`** | Tous les modules (National) | Utilisateurs, rôles, configuration système | Validation d'administration | Scellement global de logs |
| **`ARCHIVISTE_ANNF`** | Archives DAR, fiches cadastrales | Indexation fiches numérisées | Attestation d'origine foncière | Scellement permanent WORM |
| **`DIRECTEUR_URBANISME`**| Urbanisme, CCFM, RNAF | Arrêtés régionaux, plans | Signature certificats CCFM | Versement d'actes à l'ANNF |
| **`CHEF_CCFM`** | CCFM, parcelles cadastrales | Rapports d'appréciation | Éligibilité demandes CCFM | Génération de scellé NUS |
| **`GUICHETIER_CCFM`** | CCFM (Demandes locales) | Enregistrement demandes, paiement | Frais forfaitaires payés | Non (Paiement uniquement) |
| **`DIRECTEUR_CADASTRE`** | Cadastre, BGU, parcelles | Non | Plans de scission & NICAD | Scellement géométrique BGU |
| **`INGENIEUR_CADASTRE`** | Cadastre, BGU, parcelles | Plans topographiques BGU | Rapports de conformité | Non |
| **`OPERATEUR_CADASTRE`** | Cadastre (Lecture seule) | Saisie attributaire parcelles | Non | Non |
| **`NOTAIRE`** | Actes de mutation, parcelles | Actes de vente, compromis | Signature actes authentiques | Versement taxe d'enregistrement |
| **`BANQ_AGENT`** | Hypothèques, parcelles | Requêtes de sûreté bancaire | Inscription hypothécaire | Non |
| **`JUGE_FONCIER`** | Affaires judiciaires, parcelles | Ordonnances, jugements | Ordonnance de gel / dégel | Archivage de l'ordonnance |
| **`GREFFIER_TGI`** | Affaires judiciaires | Enregistrement plaintes (RG) | Non | Non |
| **`EDITEUR_JO`** | Arrêtés RNAF, JO | Publications officielles | Insertion officielle | Non |
| **`AGENT_COMMUNE`** | Concessions locales | Saisie demandes d'attribution | Non | Non |
| **`MAIRE_COMMUNE`** | Concessions municipales | Concessions provisoires | Concessions communales | Non |
| **`AUDITEUR`** | Logs, anomalies, signatures | Non | Rapports d'audit d'intégrité | Scellement blockchain d'audit |

---

## 📋 2. Détail Analytique par Module Système

### A. Registre des Actes Notariés (NOTAIRES)
*   **Qui voit quoi** : Les `NOTAIRE` voient uniquement les actes de mutation qu'ils ont personnellement rédigés ou ceux rattachés à leur étude. Le `CONSERVATEUR_FONCIER` voit l'ensemble des actes du registre national pour vérification.
*   **Qui modifie quoi** : Le `NOTAIRE` modifie les projets d'actes (compromis, actes de vente, partages successoraux) tant qu'ils ne sont pas scellés.
*   **Qui valide quoi** : Le `NOTAIRE` valide l'acte final par signature X.509. Le `RECEVEUR_ENREGISTREMENT` valide fiscalement l'acte en apposant la quittance d'enregistrement.
*   **Qui archive quoi** : Le `NOTAIRE` déclenche l'archivage cryptographique (WORM) de l'acte enregistré pour le rendre invariable et opposable.

### B. Certification Foncière Métropolitaine (CCFM)
*   **Qui voit quoi** : Le `GUICHETIER_CCFM` voit la liste des demandes de son bureau local. Le `CHEF_CCFM` et le `DIRECTEUR_URBANISME` ont une visibilité sur toutes les demandes régionales.
*   **Qui modifie quoi** : Le `GUICHETIER_CCFM` saisit les coordonnées d'identité du demandeur et la référence de paiement. Le `TOPOGRAPHE_CCFM` saisit la fiche de constat et les coordonnées GPS. Le `CHEF_CCFM` saisit le rapport d'appréciation technique.
*   **Qui valide quoi** : Le `CHEF_CCFM` valide la cohérence technique. Le `DIRECTEUR_URBANISME` valide et signe numériquement le certificat final.
*   **Qui archive quoi** : Le `CHEF_CCFM` ou le `SYSTEME_AUTO` archive le document scellé avec son QR Code blockchain.

### C. Cadastre & Base Géospatiale Unique (BGU / Parcelles)
*   **Qui voit quoi** : Tous les agents du cadastre ont accès en lecture aux cartes parcellaires. Les notaires et banques voient la géométrie publique et l'état des charges en lecture seule.
*   **Qui modifie quoi** : L'`INGENIEUR_CADASTRE` ou le `GEOMETRE_CADASTRE` importe et modifie les géométries vectorielles PostGIS. L'`OPERATEUR_CADASTRE` modifie les informations attributaires (adresse, lot).
*   **Qui valide quoi** : Le `DIRECTEUR_CADASTRE` valide les modifications de limites, les plans de scission, et approuve la génération automatique du NICAD.
*   **Qui archive quoi** : Le `DIRECTEUR_CADASTRE` scelle géométriquement la BGU pour empêcher toute modification furtive des parcelles de l'État.

### D. Justice & Litiges (Greffe / Juges)
*   **Qui voit quoi** : Le `GREFFIER_TGI` et le `JUGE_FONCIER` voient l'historique des litiges et des plaintes. Le public et les notaires voient uniquement l'état verrouillé (`is_gele = True`) d'une parcelle cadastrale sans voir les détails confidentiels de l'instruction.
*   **Qui modifie quoi** : Le `GREFFIER_TGI` saisit le dossier de plainte. Le `JUGE_FONCIER` saisit les attendus de l'ordonnance.
*   **Qui valide quoi** : Le `JUGE_FONCIER` valide la suspension des droits (gel) ou la mainlevée (dégel) par décision officielle.
*   **Qui archive quoi** : Le `GREFFIER_TGI` ou le `SYSTEME_AUTO` archive l'ordonnance judiciaire scellée et applique le verrou technique sur le NICAD.

---

## 🛡️ 3. Mécanismes de Contrôle & Règles Spécifiques

### 1. La Règle des Quatre Yeux (Four-Eyes Principle)
Toute action à fort impact juridique ou financier requiert impérativement **deux acteurs distincts** :
*   *Exemple CCFM* : Enregistrement par le `GUICHETIER_CCFM` ➔ Signature finale par le `DIRECTEUR_URBANISME`.
*   *Exemple Mutation* : Rédaction par le `NOTAIRE` ➔ Liquidation fiscale par le `RECEVEUR_ENREGISTREMENT` ➔ Mutation finale au cadastre par le `DIRECTEUR_CADASTRE`.

### 2. Le Cloisonnement Territorial (Jurisdiction Scopes)
À l'exception des rôles à vocation nationale (`ADMIN`, `AUDITEUR`, `DIRECTEUR_CADASTRE` national), tous les utilisateurs sont affectés à une **juridiction régionale** ou **communale** (ex: `Niamey`, `Maradi`, `Zinder`).
```python
# Extrait du contrôle d'accès dans app/core/security.py
def check_juridiction(user, region: str):
    """
    Bloque l'accès si l'agent régional tente de modifier ou valider
    un dossier en dehors de son territoire assigné.
    """
    if user["region"] != "NATIONAL" and user["region"] != region:
        raise HTTPException(status_code=403, detail="Hors juridiction territoriale")
```

### 3. La Délégation Temporaire Habilitée
Un cadre supérieur (`DIRECTEUR_CADASTRE`, `DIRECTEUR_URBANISME`) peut déléguer temporairement son rôle de validation à un adjoint (`role_backup`).
*   La délégation doit spécifier un **domaine précis** (ex: CCFM uniquement), une **limite temporelle** stricte, et fait l'objet d'un scellement de traçabilité dans l'historique d'audit.
*   Le système de logs enregistre l'identité réelle de l'adjoint agissant sous délégation pour maintenir une imputabilité absolue.
