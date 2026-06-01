# 🛡️ Politique de Sécurité Applicative (SECURITY_POLICY.md)

**République du Niger**  
*Ministère de l'Urbanisme, de l'Habitat et du Domaine Foncier*  
*Directive Gouvernementale de Sécurité Informatique (v4.0)*

---

> [!IMPORTANT]
> Cette politique s'applique à tous les composants logiciels, APIs et intégrateurs externes connectés à la plateforme **FONCIER+**. Tout contournement d'un contrôle de cette politique constitue un incident de sécurité nationale.

---

## 🔑 1. Politique des Mots de Passe & Authentification (Passwords)

### R-PASS-01 : Complexité Exigée des Mots de Passe
*   **Longueur minimale** : **12 Caractères**
*   **Composition** : 
    *   Au moins 1 lettre majuscule (A-Z)
    *   Au moins 1 lettre minuscule (a-z)
    *   Au moins 1 chiffre (0-9)
    *   Au moins 1 caractère spécial (ex: `!@#$%^&*()_+-=`)
*   **Chiffrement en Base** : Algorithme `bcrypt` (Passlib) avec sel individuel généré à la volée. Interdiction formelle de stocker ou d'inscrire un mot de passe en clair (même temporairement).

### R-PASS-02 : Verrouillage Automatique de Compte (Brute-Force Protection)
*   Après **5 tentatives consécutives en échec** sur le même compte, celui-ci est temporairement verrouillé pour une durée de **15 minutes**.
*   Toute tentative de connexion sur un compte verrouillé lève une exception de sécurité et notifie l'auditeur.

---

## 🔒 2. Gestion du Cycle de Vie des Jetons (JWT Lifecycle)

*   **Algorithme de Signature** : `HMAC-SHA256` avec clé secrète asymétrique de 512 bits minimum.
*   **Durée de vie des Jetons** :
    *   `Access Token` : **15 Minutes maximum**
    *   `Refresh Token` : **7 Jours maximum** (usage unique avec rotation forcée).
*   **Mécanisme de Révocabilité (JTI Blacklisting)** :
    *   Chaque jeton embarque un identifiant unique `jti`.
    *   Lors de l'appel à `/logout`, le `jti` est immédiatement inscrit dans la liste noire globale (Redis / Cache local en mémoire) pour la durée restante de sa validité.
    *   Le middleware de sécurité rejette systématiquement les requêtes dont le `jti` figure dans la liste noire.

---

## 🚦 3. Limitation du Débit de Requêtes (Rate-Limiting)

Pour prémunir les serveurs contre les attaques par déni de service (DoS) et les tentatives de force brute sur l'authentification, les quotas de requêtes par adresse IP suivants sont appliqués :

*   **API Authentification (`/api/v1/auth/login`)** : **Maximum 5 requêtes par minute** par adresse IP.
*   **API Consultation Cadastrale (`/api/v1/cadastre/*`)** : **Maximum 100 requêtes par minute** par adresse IP.
*   **Toutes autres APIs d'écriture** : **Maximum 30 requêtes par minute** par adresse IP.
*   *Sanction Système* : En cas de dépassement du quota, l'adresse IP est bloquée pour une durée de 10 minutes (Code HTTP `429 Too Many Requests`).

---

## 🧼 4. Assainissement des Entrées & Anti-Injection (Sanitization)

### R-SAN-01 : Paramétrage des Requêtes SQL (Anti-SQLi)
*   Interdiction formelle de concaténer des chaînes de caractères saisies par l'utilisateur pour fabriquer des requêtes SQL.
*   Toutes les interactions avec PostgreSQL doivent utiliser l'ORM SQLAlchemy ou des requêtes préparées avec typage strict.

### R-SAN-02 : Échappement HTML (Anti-XSS)
*   Toutes les chaînes de caractères textuelles affichées sur l'interface React ou insérées dans les documents PDF générés font l'objet d'un échappement systématique des balises HTML/XML (conversion des caractères `<`, `>`, `&`, `"`, `'`).
*   Les schémas de validation Pydantic filtrent et rejettent les chaînes de caractères contenant des balises script (`<script>`).
