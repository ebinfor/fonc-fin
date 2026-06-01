# 🛡️ Règles de Validation & Contraintes Métier (VALIDATION_RULES.md)

**République du Niger**  
*Ministère de l'Urbanisme, de l'Habitat et du Domaine Foncier*  
*Spécifications des Contraintes Système Souveraines*

---

> [!IMPORTANT]
> Les règles définies dans ce document sont impératives et codées au niveau le plus bas du moteur applicatif. Tout manquement à l'une de ces contraintes lève une exception transactionnelle immédiate et interrompt la transaction de base de données.

---

## 📐 1. Règles Géospatiales & Topologiques (GIS Engine)

### R-GIS-01 : Marge de Tolérance Cadastrale (GPS Margins)
*   **Principe** : L'écart entre la surface topographique calculée par bornage réel GPS (`surface_reelle`) et la surface déclarée dans le titre foncier d'origine (`surface_officielle`) ne doit jamais excéder un seuil maximum.
*   **Seuil Toléré** : **Maximum 5.0%**
*   **Assertion Mathématique** :
    $$\text{Écart} = \frac{|\text{surface\_reelle} - \text{surface\_officielle}|}{\text{surface\_officielle}} \le 0.05$$

### R-GIS-02 : Règle de Non-Superposition Spatiale (Non-Overlap Bounds)
*   **Principe** : L'enregistrement d'une nouvelle parcelle ou d'un nouveau plan cadastral ne doit présenter aucun chevauchement géométrique avec une parcelle immatriculée ou une emprise de l'État.
*   **Seuil Toléré** : **Maximum 0.05 m²** (marge de tolérance de calcul de précision vectorielle).
*   **Assertion PostGIS** :
    ```sql
    -- Interdit le commit si l'intersection spatiale dépasse le seuil
    SELECT ST_Area(ST_Intersection(new_geom, existing_geom)) <= 0.05;
    ```

---

## 👥 2. Règles Juridiques de Propriété (FONCIER Core)

### R-PROP-01 : Règle de la Quotité Intégrale (100% Ownership Sum)
*   **Principe** : La somme des quotités (parts de propriété) de l'ensemble des copropriétaires (indivisaires) d'une parcelle doit impérativement égaler exactement 1.0 (ou 100%).
*   **Assertion Applicative** :
    ```python
    # Appliqué lors d'une mutation ou attribution dans droits_service.py
    somme_quotites = sum(proprietaire.quotite for proprietaire in right_holders)
    if abs(somme_quotites - 1.0) > 1e-9:
        raise ValueError("La somme des quotités de propriété doit être exactement égale à 100%")
    ```

### R-PROP-02 : Barrière de Certification de Mutation (CCFM Gate)
*   **Principe** : Aucun transfert de propriété notariale (`WF-NOT-01`) ou hypothèque (`WF-BANQ-01`) ne peut être validé si le dossier ne possède pas de Certificat de Conformité Foncière Métropolitaine (CCFM) actif, valide, et non suspendu.

---

## 🔒 3. Contraintes de Sécurité & Session (AUTH & JWT)

### R-SEC-01 : Fenêtres d'Expiration des Jetons JWT (Token Expirations)
*   **Principe** : Limiter la durée de vie des clés de session pour empêcher la réutilisation de jetons interceptés.
*   **Seuils Enforcés** :
    *   `Access Token` : **15 Minutes maximum**
    *   `Refresh Token` : **7 Jours maximum** (stockage et rotation gérée avec JTI unique).
*   **Révocabilité** : Tout token lié à un `jti` inscrit sur la `JWTBlacklist` est rejeté instantanément (temps de réponse < 2ms).

### R-SEC-02 : Scellement Cryptographique X.509 (Digital Signature Constraints)
*   **Principe** : Les documents officiels produits (duplicata de Titre Foncier, certificat CCFM, ordonnance judiciaire) doivent comporter une signature numérique cryptographique et un QR Code d'intégrité scellé.
*   **Algorithme Requis** : **SHA-256 avec RSA 2048-bits** minimum.
*   **Vérification** : La clé publique de l'autorité de certification de l'État doit pouvoir authentifier de manière autonome l'intégrité du document hors-ligne.
