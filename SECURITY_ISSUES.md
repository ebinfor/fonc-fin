# 🔐 Registre des Filles de Sécurité Informatique (SECURITY_ISSUES.md)

**République du Niger**  
*Ministère de l'Urbanisme, de l'Habitat et du Domaine Foncier*  
*Audit de Sécurité & Conformité de l'Infrastructure Numérique*

---

> [!CAUTION]
> Ce document répertorie les vulnérabilités de sécurité identifiées au sein de la plateforme **FONCIER+**. Certaines failles critiques ont été immédiatement colmatées en phase d'audit pour préserver la souveraineté des données de l'État.

---

## 📋 1. Fiches de Suivi des Vulnérabilités

### SEC-001 : Banalisation du Blacklistage JWT (Session hijacking)
*   **Gravité** : **Critique**
*   **Description** : Auparavant, les jetons JWT n'étaient pas révoqués lors de la déconnexion (`/logout`), permettant à un tiers ayant intercepté un token de l'utiliser jusqu'à son expiration naturelle.
*   **Statut** : **RÉSOLU (Hardened)**
*   **Correction Appliquée** : Introduction du paramètre cryptographique `jti` (JWT ID) unique par session et mise en place de la passerelle `JWTBlacklist` (sur cache Redis et avec Set in-memory en cas d'indisponibilité du cache).

### SEC-002 : Absence de Double Facteur (MFA) pour Rôles Habilités
*   **Gravité** : **Élevée**
*   **Description** : L'accès à des postes hautement sensibles (ex: `DIRECTEUR_CADASTRE`, `JUGE_FONCIER`) s'opère par simple mot de passe classique, augmentant le risque d'usurpation d'identité en cas de vol de mot de passe par hameçonnage.
*   **Statut** : **EN COURS (Planifié Phase 3)**
*   **Remédiation** : Imposer l'authentification TOTP (Google Authenticator) obligatoire lors de la connexion pour ces rôles spécifiques.

### SEC-003 : Stockage Local des Clés de Signature Privées (AC Mock keys)
*   **Gravité** : **Élevée**
*   **Description** : Les clés de signature X.509 servant au scellement ANNF sont chargées depuis des fichiers `.pem` locaux stockés sur le système de fichiers du conteneur backend.
*   **Statut** : **À LIQUIDER (Planifié Phase 6)**
*   **Remédiation** : Connecter le service de signature à un module matériel de sécurité (**HSM** : Hardware Security Module) étatique ou à une API de gestion de secrets durcie.

### SEC-004 : Risque de Faille IDOR sur les Juridictions (Geographical Spanning)
*   **Gravité** : **Moyenne**
*   **Description** : Risque qu'un agent d'une commune A puisse modifier ou approuver par ruse de requête HTTP un dossier appartenant à une commune B en changeant simplement le paramètre d'URL.
*   **Statut** : **RÉSOLU (Mitigé)**
*   **Correction Appliquée** : Intégration de la barrière `check_juridiction` et injection SQL dynamique automatique (`ScopeFilter`) filtrant les parcelles et certificats selon le territoire de l'agent.

---

## 📊 2. Matrice d'Impact de Sécurité (SecOps Matrix)

| Code Vulnérabilité | Vecteur d'Attaque | Impact Potentiel | Actions Immédiates | Statut |
| :--- | :--- | :--- | :--- | :--- |
| **SEC-001** | Réseau / Interception | Usurpation d'agent de l'État | Blacklistage JTI obligatoire | **RÉSOLU** |
| **SEC-002** | Phishing / Brute-force | Falsification de Titres Fonciers| Implémentation MFA TOTP | **EN COURS** |
| **SEC-003** | Compromission serveur | Vol de la clé de signature État| Transition vers HSM Physique | **PLANIFIÉ**|
| **SEC-004** | IDOR / Requête HTTP | Mutation frauduleuse hors région| Barrière `ScopeFilter` SQL | **RÉSOLU** |
