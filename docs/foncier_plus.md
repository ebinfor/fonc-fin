# FONCIER+ — Documentation Centrale

**Plateforme Nationale de Gestion Foncière — République du Niger**
*Ministère de l'Urbanisme et de l'Habitat — DNCD*

**Version courante :** v1.0.1 — Sprint 4 (Avril 2026)
**Responsable technique :** Papa (Architecture & Sprint Lead)
**Stack :** React 18 + TS + Vite / FastAPI + Python 3.12 + PostgreSQL/PostGIS / Docker Compose + Nginx + MinIO + Redis

---

## Table des Matières

1. [Organisation de l'équipe](#1-organisation-de-léquipe)
2. [Workflow de production](#2-workflow-de-production)
3. [Architecture technique](#3-architecture-technique)
4. [Modules métier](#4-modules-métier)
5. [CCFM — Schéma officiel](#5-ccfm--schéma-officiel)
6. [Architecture nationale renforcée (Sprint 4)](#6-architecture-nationale-renforcée-sprint-4)
7. [SQL Sandbox — Validation sécurisée](#7-sql-sandbox--validation-sécurisée)
8. [Scénarios spéciaux](#8-scénarios-spéciaux)
9. [SEO et optimisation](#9-seo-et-optimisation)
10. [Bugs connus et correctifs](#10-bugs-connus-et-correctifs)
11. [Décisions techniques](#11-décisions-techniques)
12. [Suivi sprint & déploiement](#12-suivi-sprint--déploiement)

---

## 1. Organisation de l'équipe

| Cellule | Périmètre | Livrables attendus |
|---|---|---|
| **Front-end** | React 18 / TS / Tailwind, 8 fichiers, LandingPage + Login + Dashboard | Pages `useDataFetch`, sidebar 13 modules, KPIs, alertes |
| **Back-end** | FastAPI / SQLAlchemy async / Alembic, 125 fichiers Python, 356 routes | Endpoints `response_model`, `require_role`, séquences atomiques |
| **DevOps** | Docker Compose, Nginx, CI/CD GitHub Actions, MinIO, Redis | `make deploy`, health checks, backup quotidien, DRP |
| **SEO Specialist** | Meta-tags, maillage interne, vitesse, portail `/verify` public | React Helmet, sitemap, OpenGraph |
| **QA & Testeurs** | Vitest + Playwright + pytest-asyncio, 13 suites, 379 tests | Tests unitaires, E2E, RBAC, PKI, sécurité, SQL sandbox |
| **Doc & Markdown Manager** | Ce fichier `foncier_plus.md` + READMEs de sprint | Mise à jour après chaque sprint / ZIP cumulatif |

---

## 2. Workflow de production

1. Chaque fonctionnalité est découpée en tâches atomiques avec propriétaire clair
2. L'architecture et la logique métier sont **validées d'abord** (règle foncière, ownership module)
3. Implémentation en sprint court → **ZIP cumulatif** (ex. `FONCIER_v100_PRODUCTION_FINAL.zip`)
4. Chaque décision technique est consignée ici (sections 11)
5. QA + revue SEO + conformité réglementaire Niger **avant** `make deploy-check`
6. Mise à jour de ce `.md` **obligatoire** à chaque clôture de sprint

---

## 3. Architecture technique

### Stack

| Couche | Technologie |
|---|---|
| Frontend | React 18 + TypeScript + Vite + Tailwind CSS |
| Backend | FastAPI + Python 3.12 + SQLAlchemy async + Pydantic v2 |
| Base de données | PostgreSQL 16 + PostGIS 3.4 (RLS par région/rôle) |
| Auth | JWT HS256 (2h access + 7j refresh) + bcrypt + RBAC 29 rôles |
| Cache | Redis 7 authentifié |
| Stockage | MinIO S3-compatible (SHA-256 par upload) |
| PKI | CA X.509 interne, RSA-PSS-SHA256, blockchain hash |
| Monitoring | Sentry front + back + logs JSON |
| CI/CD | GitHub Actions (5 jobs : front, back, E2E, Docker, security) |
| SQL Sandbox | Validation 4 couches (statique + sémantique + PostgreSQL + SHA-256) |

### Chaîne foncière — 9 maillons

`COMMUNE → ARRÊTÉ → JO → RNAF → CADASTRE/BGU → RNP → ATTRIBUTION → CCFM → TRANSACTION`

### Métriques v1.0.1 (Sprint 4)

| Indicateur | Valeur |
|---|---|
| Fichiers Python | 125 (63 093 lignes) |
| Fichiers Frontend (TSX/TS) | 8 (1 841 lignes) |
| Endpoints API | 23 modules, **356 routes** |
| Migrations Alembic | **31** (002→032) |
| Tables PostgreSQL | ~145 (dont 31 nouvelles Sprint 4) |
| Triggers PL/pgSQL | 90+ (dont 8 nouveaux Sprint 4) |
| Tests automatisés | 13 suites, **379 cas** |
| Services | 8 (dont `decision_engine`, `sql_sandbox`) |
| Rôles RBAC | 29 |

---

## 4. Modules métier

| Module | Couche | Routes | Workflows | DAR→ANNF |
|---|---|---|---|---|
| **Urbanisme** | Institution | 18 | WF1, WF10, WF11 | ✓ |
| **Commune** | Institution | 21 | WF12 | ✓ |
| **Journal Officiel** | Institution | 17 | — | ✓ |
| **BGU** | Géospatiale | 19 | BGU interne | ✓ |
| **CCFM** | Certification | 23 | WF2, WF23 | ✓ |
| **Domaine** | Transaction | 38 | WF20-WF22 | ✓ |
| **Notaire** | Transaction | 45 | WF17-WF19 | ✓ |
| **Banque** | Transaction | 22 | WF8 | ✓ |
| **Justice** | Contrôle | 26 | WF24-WF28 | ✓ |
| **ANNF** | Archives | 22 | WF27, WF30 | Centrale |
| **Admin** | National | 27 | — | — |

> **Gate CCFM obligatoire** sur Commune, Domaine, Justice, Banque, Notaire :
> 3 routes par module (`/verifier-ccfm/{parcelle_id}`, `/verifier-ccfm-qr`, `/ccfm-gate/{parcelle_id}`)

---

## 5. CCFM — Schéma officiel

### Workflow 7 acteurs / 15 états (migration 030)

| Étape | Acteur | Route | Résultat |
|---|---|---|---|
| 1 | **Guichetière** | `POST /ccfm/demandes` | NUS-AAAA-NNNNN + récépissé PDF A4 |
| 2 | **Système** | `POST /ccfm/demandes/{nus}/ticket` | RNAF+BGU+GPS ticket hash |
| 3 | **Secrétaire** | `POST /ccfm/ordres-mission` | OM-AAAA-NNNNN + ticket joint |
| 4 | **Topographe** | `POST /ccfm/fiches-constat` | Fiche terrain + écart GPS |
| 5 | **Système** | `POST /ccfm/rapports-appreciation` | Rapport compilé (verdict) |
| 6 | **Directeur** | `POST /ccfm/demandes/{nus}/signer` | PDF A5 + QR Code scannable |
| 6b | — | `GET /ccfm/demandes/{nus}/certificat-pdf` | Téléchargement PDF |
| 7 | **Archiviste** | `POST /ccfm/demandes/{nus}/archiver` | ANNF-CCFM-AAAA-NNNNN + blockchain |

### NUS et référence

- **NUS** : `NUS-AAAA-NNNNN` (ex: NUS-2026-00089) — généré atomiquement par `generer_nus_ccfm()`
- **Référence CCFM** : `CCFM/AAAA/MM/NNNN` (ex: CCFM/2026/02/0089) — générée à la signature
- **Validité** : 2 ans à compter de la date de signature
- **QR Code** : URL `https://foncier.gov.ne/verify/{nus}` — 4 formats de scan supportés

### Vérification publique QR / NUS

| Format | Exemple |
|---|---|
| URL directe | `https://foncier.gov.ne/verify/NUS-2026-00089` |
| JSON | `{"nus":"NUS-2026-00089","url":"..."}` |
| Pipe | `CCFM\|NUS-2026-00089\|https://...` |
| NUS brut | `NUS-2026-00089` |

---

## 6. Architecture nationale renforcée (Sprint 4)

### Migration 031 — 12 composants (15 améliorations)

| # | Composant | Tables/Fonctions | Rôle |
|---|---|---|---|
| 1 | Validation multi-niveaux | `validation_pipeline` + `valider_niveau_suivant()` | 4 niveaux : local→national |
| 2 | Hiérarchie institutionnelle | `institutions`, `directions_services`, `agent_profil` | Ownership territoire |
| 3 | Decision Engine | `regle_metier`, `decision_engine_log` + `decision_engine_evaluer()` | Arbitrage central |
| 4 | Audit immuable | `audit_immutable` (append-only) + `audit_enregistrer()` | Hash chaining |
| 5 | Verrouillage entités | `entity_lock` + `entity_lock_poser/liberer()` | NOWAIT PostgreSQL |
| 6 | Topologie PostGIS | `topo_valider_geom()` + trigger `tg_parcelles_topo` | Anti-chevauchement |
| 7 | Délégations admin | `delegation_administrative` + trigger expiration | Traçabilité |
| 8 | Versioning documentaire | `document_foncier` + `document_version` | Lien doc→transaction |
| 9 | DRP | `drp_backup_log`, `drp_replication_status`, `drp_simulation` | Plan de reprise |
| 10 | Interopérabilité | `integration_externe` + `webhook_log` | API externes |
| 11 | Règles métier versionnées | `regle_metier` + `regle_version` (6 règles seeds) | Externalisation logique |
| 12 | Segmentation territoriale | `territoire_juridiction` (9 seeds) + `check_juridiction()` | Blocage hors-zone |

### Service `DecisionEngine` — 8 méthodes

```python
DecisionEngine.evaluer()              # Évalue toutes les règles actives
DecisionEngine.verifier_juridiction() # check_juridiction() PostgreSQL
DecisionEngine.exiger_juridiction()   # → HTTP 403 si hors-zone
DecisionEngine.poser_verrou()         # entity_lock_poser() NOWAIT
DecisionEngine.liberer_verrou()       # entity_lock_liberer()
DecisionEngine.auditer()              # audit_immutable append-only
DecisionEngine.creer_pipeline()       # validation_pipeline multi-niveaux
DecisionEngine.valider_niveau()       # Passe au niveau suivant
DecisionEngine.detecter_conflits()    # Verrous + pipelines en cours
DecisionEngine.valider_topologie()    # topo_valider_geom()
DecisionEngine.delegataire_actif()    # Résolution délégation
```

---

## 7. SQL Sandbox — Validation sécurisée

### Migration 032 — Architecture 4 couches

**Problème résolu** : `EXECUTE 'SELECT (' || condition_sql || ')'` permettait l'injection SQL directe.

**Solution** : Validation obligatoire avant tout INSERT dans `regle_metier`.

| Couche | Composant | Protection |
|---|---|---|
| **1 — Statique** | `StaticSQLValidator` | 40+ mots-clés interdits, commentaires, dollar-quoting, UNION, INTO, multi-instructions |
| **2 — Sémantique** | `SemanticSQLValidator` | Whitelist 30 tables, cohérence domaine, paramètres $1/$2 |
| **3 — Sandbox DB** | `PostgresSandbox` | EXPLAIN READ ONLY + SAVEPOINT + timeout 2s |
| **4 — Signature** | `RuleSignature` | SHA-256(sql+domaine+code+niveau+user+date) |

**Règle de sécurité DB** :
- Trigger `tg_regle_validation` : `actif=TRUE` requiert `sandbox_valide=TRUE`
- Rôle `foncier_reader` : SELECT uniquement sur 25 tables autorisées
- Fonction `sql_regle_executer_secure()` : remplace `EXECUTE` direct
- `decision_engine_evaluer()` refondu : n'exécute que les règles `sandbox_valide=TRUE`

**Vecteurs d'attaque bloqués (8/8)** :

```
✓ SQL Injection classique (OR 1=1)
✓ Stacked queries (;DROP TABLE)
✓ Timing attack (pg_sleep)
✓ Credential extraction (pg_shadow)
✓ UNION injection
✓ Dollar-quoting bypass ($$)
✓ Comment bypass (--)
✓ Oversized payload (>2000 chars)
```

### Nouveaux endpoints Admin

```
POST /admin/regles-metier/valider         Valider SQL (couches 1+2+3+4)
POST /admin/regles-metier                 Créer règle (validation obligatoire)
GET  /admin/regles-metier/{id}/validation Rapport de validation
GET  /admin/sandbox/stats                 Stats 30 jours
GET  /admin/sandbox/attente-validation    Règles non validées
```

### Tests sandbox — 39 cas

```
TestStaticValidation   : 18 tests (mots-clés interdits, cas valides)
TestSemanticValidation :  5 tests (tables, domaines)
TestSignature          :  5 tests (déterminisme, unicité, SHA-256)
TestSecurityAttacks    : 12 tests paramétrés (vecteurs d'attaque)
TestSQLRuleValidator   :  5 tests async (sans DB, intégration)
```

---

## 8. Scénarios spéciaux

### 8.1 Parcelle à double numérotation
Ancienne nomenclature `ilot.parcelle.lotissement` → nouvelle `REG/COM/LOT/SEC-ILOT/PARC`.
Le générateur Nouvelle Formule RNP convertit automatiquement. `ParcelVersion` conserve les deux.

### 8.2 Parcelle déplacée
Détection BGU : 5 types d'écarts (`added`, `deleted`, `moved`, `modified`, `overlapping`).
Trigger `tg_parcelles_topo` + `topo_valider_geom()` → alerte antifraude + régularisation imposée.

### 8.3 Conflit de CCFM
Deux CCFM actifs sur même parcelle → blocage Justice et Banque.
Gate `/ccfm-gate/{parcelle_id}` retourne `CCFM_GATE_FAILED` (HTTP 422).

### 8.4 Hors juridiction
Agent opérant hors de sa région → `check_juridiction()` PostgreSQL → `security.exiger_juridiction()` → HTTP 403 `HORS_JURIDICTION`.

### 8.5 Délégation active
Si une délégation administrative active existe, `get_user_effectif()` retourne le délégataire automatiquement.

---

## 9. SEO et optimisation

### État actuel
- **B-12 (en attente)** — React Helmet sur le portail `/verification` public
- Lazy loading actif (React.lazy + Suspense, App.tsx)
- Gzip level 6 + keepalive Nginx upstream
- Docker multi-stage ~200 MB

### Travail à planifier
1. React Helmet sur `CCFMVerificationPublique.tsx`
2. OpenGraph pages publiques
3. `sitemap.xml` routes publiques uniquement
4. `robots.txt` : Disallow routes authentifiées

---

## 10. Bugs connus et correctifs

### Résolus

| ID | Description | Correctif | Version |
|---|---|---|---|
| B-001 | `index.html` manquant à la build Vite | Ajouté à la racine `frontend/` | v3.4.5 |
| B-002 | `docker-compose.yml` obsolète | Passage à `expose:` interne | v3.4.5 |
| B-003 | Migration Alembic 002 manquante | Création stub | v3.4.5 |
| B-004 | Secret MinIO hardcodé | Variable d'env `MINIO_SECRET_KEY` | v3.4.5 |
| B-007 | QR Code CCFM non scannable | `QrCodeWidget` natif ReportLab | v3.4.7 |
| B-008 | Vérification CCFM réservée aux connectés | Page publique `/verify/:nus` | v3.4.7 |
| **B-SQL1** | **Injection SQL dans `decision_engine_evaluer`** | **SQL Sandbox 4 couches** | **v1.0.1-S4** |
| **B-SQL2** | **Aucune validation avant INSERT regle_metier** | **Validation obligatoire** | **v1.0.1-S4** |
| **B-SQL3** | **EXECUTE direct `condition_sql`** | **`sql_regle_executer_secure()`** | **v1.0.1-S4** |

### Ouverts

| ID | Description | Priorité |
|---|---|---|
| B-12 | SEO — React Helmet sur `/verification` | Moyenne |
| D-06 | Colonne PostGIS `geometry` réelle | Haute |
| D-08 | Signature PKI sur certificats BGU | Haute |
| W-01 | Tests unitaires frontend (Vitest) | Moyenne |

---

## 11. Décisions techniques

### 11.1 Base de données
- PostgreSQL 16 + PostGIS 3.4, RLS par région/rôle via `fn_set_app_context()`
- **31 migrations** Alembic, chaîne intacte 002→032
- ~145 tables, 90+ triggers PL/pgSQL
- 13 séquences atomiques (zéro race condition)
- Pool SQLAlchemy async : 20 connexions + 10 overflow

### 11.2 Sécurité SQL (nouveau — Sprint 4)
- Toute règle `condition_sql` doit passer 4 couches avant activation
- Le trigger `tg_regle_validation` est la dernière défense : `actif=TRUE` sans `sandbox_valide=TRUE` lève une exception PostgreSQL
- Le rôle `foncier_reader` (SELECT seul) est le contexte d'exécution sandbox
- `sql_validation_log` est **append-only** (trigger `tg_svl_no_modify`)
- `audit_immutable` est **append-only** (trigger `tg_audit_no_update`)

### 11.3 Architecture nationale (nouveau — Sprint 4)
- **Hiérarchie institutionnelle** : institutions → directions → agent_profil (niveau 1-4)
- **`check_juridiction()`** : fonction PostgreSQL, appelée par `security.exiger_juridiction()`
- **`decision_engine_evaluer()`** : toutes les opérations critiques passent par ce point unique
- **`entity_lock`** avec `FOR UPDATE NOWAIT` : zéro double attribution possible
- **`audit_immutable`** avec hash chaining : intégrité juridique des historiques

### 11.4 Frontend v3.4.7
- Hook `useDataFetch` universel, zéro `useEffect` manuel
- Lazy loading toutes pages, `PrivateRoute` guard
- `0 useState<any>`, `0 as any`
- Palette Niger : COL_INK `#1a1a1a`, COL_SAND `#F5EFE0`, COL_GREEN `#3C4F33`, COL_RED `#B8220E`

---

## 12. Suivi sprint & déploiement

### 12.1 Checklist premier déploiement

```bash
1. make setup-env          # JWT 64 hex, POSTGRES, REDIS, MINIO aléatoires
2. Éditer backend/.env     # SENTRY_DSN, domaine production
3. backend/ssl/            # foncier.gov.ne.crt + .key + chain.pem
4. make deploy-check       # Valide env + SSL + docker-compose + TypeScript
5. make deploy             # Pull + build + up + alembic upgrade head + health checks
6. make seed               # 45 utilisateurs, 29 rôles

# Sprint 4 spécifique :
7. pip install reportlab   # Générateur PDF CCFM
8. psql -c "SELECT * FROM v_regles_attente_validation"  # Valider les règles seeds
```

### 12.2 Historique des sprints

| Sprint | Livrable | Contenu |
|---|---|---|
| S1 (v3.4.6) | `PRODUCTION_READY.zip` | Socle production-ready, 126 fichiers, 26 tables |
| S2 (v3.4.7) | `FONCIER_v347_CCFM_UPDATE.zip` | CCFM QR Code, PDF A5, vérification publique |
| S3 (v1.0.0) | `FONCIER_v100_PRODUCTION_FINAL.zip` | Frontend v3.4.7, foncier_plus.md, audit |
| **S4 (v1.0.1)** | **`FONCIER_v100_PRODUCTION_FINAL.zip`** | **15 améliorations structurelles + SQL Sandbox** |

### 12.3 Prochain sprint (proposé)

**Priorité 1 — Déploiement des migrations 031-032**
1. Dry-run `alembic upgrade 031` sur staging → vérifier institutions seeds
2. Seed initial des profils agents pour les 45 utilisateurs existants
3. Valider les 6 règles `regle_metier` seeds via `POST /admin/regles-metier/valider`
4. Tester `check_juridiction()` avec les 8 régions

**Priorité 2 — Tests E2E architecture nationale**
5. Tests de juridiction (agent NIA tente une action sur AGZ)
6. Tests de délégation (délégation temporaire 24h)
7. Tests de verrouillage concurrent (deux mutations simultanées bloquées)
8. Tests audit immuable (vérification intégrité hash chaining)

**Priorité 3 — Frontend branché**
9. Dashboard `v_sante_systeme` + `v_entity_locks_actifs`
10. Page gestion règles métier (CRUD + indicateur sandbox)
11. Page délégations administratives

**Priorité 4 — SEO & cadastre**
12. B-12 — React Helmet portail public
13. D-06 — Colonne PostGIS `geometry` réelle
14. D-08 — Signature PKI certificats BGU

---

*Document maintenu par le Markdown Manager — dernière mise à jour : v1.0.1 Sprint 4, Avril 2026*

---

## 7.bis ScopeFilter — Filtrage contextuel par acteur

### Principe fondamental
Chaque acteur ne voit **que ce qui le concerne**. `scope_filter.py` est le point de vérité unique.

### Règles par catégorie de rôle

| Catégorie | Rôles | Filtre DB appliqué |
|---|---|---|
| **Vision nationale** | `ADMIN`, `AUDITEUR`, `MINISTRE_URBANISME`, `RESPONSABLE_ANNF/BGU` | Aucun filtre — voient tout |
| **Filtre région** | `AGENT_URBANISME`, `CHEF_URBANISME`, `AGENT_DOMAINE`, `TOPOGRAPHE`, `GUICHETIER_CCFM`, `MAIRE`, `GEOMETRE`, `ARCHIVISTE_ANNF`... | `WHERE region = agent.region` |
| **Filtre entité** | `NOTAIRE`, `BANQ_DIRECTEUR`, `BANQ_AGENT`, `JUGE_FONCIER`, `GREFFIER`, `HUISSIER` | `WHERE entite_id = agent.entite_id` |

### Utilisation dans un endpoint

```python
scope = ScopeFilter.from_user(current_user)
sql, params = scope.apply(base_sql, params, table_alias="c", col_region="region")
r = await db.execute(text(sql), params)
```

### Modules couverts (12/25)

`ccfm_v3`, `justice`, `rnaf`, `workflows`, `domaine`, `bgu`, `cadastre`,
`urbanisme`, `banque`, `notaire`, `commune`, `annf`

### Scénarios CCFM par acteur

| Acteur | Voit |
|---|---|
| Guichetière | Ses propres enregistrements (`enregistre_par = moi`) — étapes 1-4 |
| Secrétaire | Demandes TICKET_EMIS → ATTENTE_VISITE — sa région |
| Topographe | Demandes assignées à lui (`topographe_assigne = moi`) |
| Directeur | Demandes ATTENTE_SIGNATURE_DIRECTEUR — sa région |
| Archiviste | Demandes SIGNE_DIRECTEUR → RETIRE — sa région |
| Chef CCFM / Admin | Tout |

---

## 12. Corrections P0/P1 post-audit (Sprint 5)

### Corrections P0 appliquées

| # | Problème | Correction | Fichier(s) |
|---|---|---|---|
| P0-1a | Commit sans rollback (4 modules) | `await db.rollback()` dans les blocs `except` | `cadastre_parc`, `droits_fonciers`, `rnaf`, `workflows` |
| P0-1b | 8 fonctions sans try/except | Wrapper try/except avec HTTPException 500 | `annf`, `auth`, `banque`, `bgu` |
| P0-2a | 7 schemas Pydantic manquants ccfm_v3 | `VerificationAutoIn`, `FicheConstatV3In`, `RapportAppreciationV3In`, `ArchiverV3In`... | `ccfm_v3.py` |
| P0-2b | 6 POST sans schema branché | Signatures mises à jour | `ccfm_v3.py` |

### Corrections P1 appliquées

| # | Problème | Correction | Fichier(s) |
|---|---|---|---|
| P1-1 | 0 index composite sur tables ops | Migration 041 : 18 index partiels/composites | `041_index_composites_performance.py` |
| P1-2 | 0 response_model sur ccfm_v3 GET | `response_model=list/dict` + `DemandeCCFMOut` | `ccfm_v3.py` |
| P1-3 | `as any` dans 7 pages CCFM | Remplacé par `unknown` / `Record<string,unknown>` | pages `ccfm/*.tsx` |
| P1-4 | `useEffect` direct LandingPage | TODO P2 ajouté + migration planifiée | `LandingPage.tsx` |

### Tests ajoutés

- `test_workspace_acces.py` — 26 tests : isolation CCFM, Justice, Banque, Notaire, Domaine, workspace roles
- `test_scope_filter.py` — 36 tests : ScopeFilter unitaire + scénarios réels

### Migration 041 — 18 index composites

Indexes partiels sur : `demandes_ccfm (etat, localite)`, `demandes_ccfm (enregistre_par, etat)`,
`demandes_ccfm (topographe_assigne, etat)`, `litiges (region, statut)`, `litiges (tgi_id, statut)`,
`hypotheques (banque_id, statut)`, `workflow_instances (agent_courant_id, statut)`,
`rnaf (region, statut)`, `expropriations (region, statut)`, `parcelles (region, is_gele)`,
`annf_archive_links (scelle=FALSE)`, `audit_immutable (seq DESC)`, `entity_lock (actif=TRUE)`,
`validation_pipeline (statut NOT IN VALIDE/REJETE)`.


---

## 13. SEO & Optimisation portail public

### Pages publiques indexables
- `/verify` et `/verify/:nus` — portail de vérification CCFM (sans auth)
- `/ccfm/verification-publique` — alias portail CCFM

### React Helmet (P2 — à implémenter sprint 6)
```tsx
import { Helmet } from 'react-helmet-async'
<Helmet>
  <title>FONCIER+ — Plateforme foncière Niger</title>
  <meta name="description" content="Gestion foncière nationale du Niger — DNCD" />
  <meta property="og:title" content="FONCIER+" />
</Helmet>
```

### URLs et performance
- Portail public `/verify/{nus}` — format canonique NUS-AAAA-NNNNN
- `robots.txt` : Disallow `/dashboard`, `/admin`
- Gzip level 6 + keepalive Nginx upstream
- Lazy loading React.lazy sur 22 pages

---

## 14. Corrections architecturales — Structure modulaire (v1.0.9)

### Principe : sous-modules et DAR

Papa a confirmé les constantes architecturales suivantes :

#### RNAF → Sous-module Urbanisme
Le Registre National des Actes Fonciers est un **sous-module de l'Urbanisme**.
- Ancien préfixe : `/v1/rnaf/*`
- Nouveau préfixe : `/v1/urbanisme/rnaf/*`
- Justification : le RNAF est produit par le service de l'Urbanisme après publication JO.

#### RNP → Sous-module Cadastre
Le Registre National Parcellaire est un **sous-module du Cadastre/BGU**.
- Ancien préfixe : `/v1/rnp/*`
- Nouveau préfixe : `/v1/cadastre/rnp/*`
- Justification : le RNP lie les parcelles NICAD aux personnes — c'est une opération cadastrale.

#### DAR + WF30 → Sous-module de chaque module fonctionnel
La Direction des Archives (DAR) existe dans **chaque module fonctionnel** (11 instances).
Chaque DAR dispose de son propre pipeline WF30 pour migrer ses archives spécifiques.

| Module | Préfixe DAR | Archives migrées par WF30 |
|---|---|---|
| Urbanisme | `/v1/urbanisme/dar` | Arrêtés papier, RNAF anciens, plans directeurs |
| Cadastre | `/v1/cadastre/dar` | Plans cadastraux papier, NICAD historiques, BD Topo |
| CCFM | `/v1/ccfm/dar` | Certificats CCFM pré-numérique, dossiers papier |
| Domaine | `/v1/domaine/dar` | Expropriations historiques, concessions, régularisations |
| Notaire | `/v1/notaire/dar` | Actes notariés papier, mutations anciennes |
| Banque | `/v1/banque/dar` | Hypothèques papier, registres BCEAO anciens |
| Justice | `/v1/justice/dar` | Jugements fonciers papier, greffes historiques |
| Commune | `/v1/commune/dar` | Registres communaux, dépôts fonciers anciens |
| ANNF | `/v1/annf/dar` | Supervision inter-DAR, scellement central |
| BGU | `/v1/bgu/dar` | Plans graphiques papier, cartes topographiques |
| JO | `/v1/jo/dar` | Parutions JO papier, journaux anciens |

Le WF30 standalone (`/v1/wf30/*`) est **déprécié** depuis v1.0.9.
Utiliser `/v1/{module}/dar/wf30/*` pour toutes les nouvelles intégrations.

### Structure finale Urbanisme
```
/v1/urbanisme/*          → Module principal (18 routes)
/v1/urbanisme/rnaf/*     → Sous-module RNAF (7 routes)
/v1/urbanisme/dar/*      → DAR Urbanisme (9 routes)
/v1/urbanisme/dar/wf30/* → Pipeline migration archives urbanisme
```

### Structure finale Cadastre
```
/v1/cadastre/*               → Module principal (18 routes)
/v1/cadastre/parcelles/*     → Sous-module parcellaire (10 routes)
/v1/cadastre/rnp/*           → Sous-module RNP (2 routes)
/v1/cadastre/dar/*           → DAR Cadastre (9 routes)
/v1/cadastre/dar/wf30/*      → Pipeline migration archives cadastrales
```

---

## 15. TODO P3 — N+1 Queries (Sprint 7)

### Modules concernés
Les modules suivants ont des boucles Python avec requêtes DB individuelles.
Refactoriser en `SELECT ... WHERE id = ANY(:ids)` ou `JOIN` batch.

| Module | Occurrences | Pattern à corriger |
|---|---|---|
| admin.py | 26 | Boucle validation règles SQL |
| notaire.py | 24 | Boucle héritiers + actes |
| domaine.py | 13 | Boucle projets publics |
| banque.py | 12 | Boucle hypothèques par parcelle |
| monitoring.py | 6 | Boucle alertes par module |
| wf30.py | 5 | Boucle sources documents |

### Modèle de correction
```python
# Avant (N+1)
for hid in heritiers_ids:
    r = await db.execute(text("SELECT * FROM right_holders WHERE id = :id"), {"id": hid})

# Après (batch)
r = await db.execute(
    text("SELECT * FROM right_holders WHERE id = ANY(:ids)"),
    {"ids": heritiers_ids}
)
```

## TODO P3 — N+1 Queries
Documenté pour Sprint 7.
