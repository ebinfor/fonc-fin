
# FONCIER+ — Analyse module par module
# MODULE 3 : BGU (Bureau de Gestion Urbaine)
# Base géospatiale unique de la République du Niger
# Avril 2026

---

## 1. Identité du module

Le BGU est la **base géospatiale de référence nationale**. Il ne dépend
pas organisationnellement du Cadastre : c'est lui qui valide les géométries
produites par le Cadastre et les publie comme source de vérité publique.

Architecture de ses données :

```
bgu_geojson_master   ← source de vérité géom scellée (SHA-256 + blockchain)
      ↓ scellement
bgu_projection       ← cohérence RNAF/RNP/surface calculée PostGIS
      ↓ trigger tg_fn_maj_bgu_projection
controle_geometrique ← contrôles qualité : validité, superposition, surface
      ↓
bgu_parcel_status    ← statut public portail citoyen (actif|suspendu|gelé|annulé)
      ↑ sync depuis
 tg_sync_rnp_from_rnaf  (quand RNAF suspendu/annulé)
```

Tables legacy référencées : `assiettes_bgu` (migration 001).

---

## 2. Tables du module BGU

| Table | Rôle |
|---|---|
| `bgu_geojson_master` | Géométrie scellée — SHA-256 + ancrage blockchain |
| `bgu_projection` | Résultat de projection : cohérence RNAF, RNP, surface |
| `controle_geometrique` | Contrôles PostGIS : validité, superposition, surface |
| `bgu_parcel_status` | Statut public visible sur le portail citoyen |

Tables legacy : `assiettes_bgu` (legacy 001) référencée par `bgu_parcel_status`.

---

## 3. BGUGeoJSONMaster — colonnes clés

```
id           UUID PK
parcelle_id  → parcelles.id  (UNIQUE — 1 entrée par parcelle)
geojson_id   String 100  UNIQUE, invariable
geom         GEOMETRY(POLYGON, SRID 4326)  NOT NULL
sha256_geom  String 64  NOT NULL  — hash de la géométrie WKT
scelle       Boolean  default False
scelle_at    DateTime
scelle_par   → users.id
blockchain_hash     String 128  — txid Ethereum/autre
blockchain_network  String 50   — 'internal' par défaut
blockchain_timestamp DateTime
version      Integer  default 1
```

**Règle absolue :** après scellement (`scelle=True`), plus aucune
modification de `geom` ni de `sha256_geom` n'est autorisée.
Toute correction nécessite une nouvelle entrée (incrément version).

---

## 4. BGU Projection — cohérence géospatiale

La table `bgu_projection` est remplie automatiquement par le trigger
`tg_tc3_maj_bgu_projection` **AFTER UPDATE scelle=true** sur
`bgu_geojson_master`. Elle stocke :

| Colonne | Contrôle |
|---|---|
| `coherence_rnaf` | RNAF projeté = arrêté source de la parcelle |
| `coherence_rnp` | RNP projeté = parcelle cadastrée |
| `surface_bgu_m2` | Calculée par `ST_Area(geom::geography)` |
| `ecart_surface_pct` | Écart surface BGU vs surface RNP déclarée |
| `projection_valide` | Synthèse booléenne |
| `sha256_projection` | Hash chaîné du résultat |

---

## 5. ControleGeometrique — qualité PostGIS

Table créée en migration 009, remplie lors du workflow BGU :

| Colonne | Test PostGIS |
|---|---|
| `pas_de_superposition` | ST_Intersects = 0 avec voisins actifs |
| `geometrie_fermee` | ST_IsValid(geom) = true |
| `surface_coherente` | Écart déclaré vs calculé < 0.5 % |
| `dans_lotissement` | ST_Within(parcelle, lotissement) |

---

## 6. Workflow BGU — défaut critique

### BUG-WF-BGU-01 — CRITIQUE : workflow_definition BGU sans étapes

La `workflow_definition` de type BGU est bien créée en migration 007
(délai 10 jours, signature requise) **mais aucun `INSERT INTO workflow_step_def`
n'est effectué pour le type_workflow='BGU'**.

Le workflow BGU n'a **zéro étape** en base. Il ne peut ni démarrer,
ni avancer, ni se compléter. Toute tentative via `WorkflowEngine.demarrer()`
renverra une erreur car aucune étape d'ordre=1 n'existe.

**Correction :** Créer les 6 étapes du workflow BGU en migration 025 :

| Ordre | Code | Nom | Rôle | Type | Action |
|---|---|---|---|---|---|
| 1 | IMPORT_GEOM | Import géométries sources | RESPONSABLE_BGU | constat | 48h | — |
| 2 | CONTROLE_QUALITE | Contrôle PostGIS | GEOMETRE | constat | 24h | CHECK_GEOM_QUALITE |
| 3 | VERIFICATION_RNAF | Vérification cohérence RNAF | DIRECTEUR_URBANISME | approbation | 48h | VERIFIER_COHERENCE_RNAF |
| 4 | VALIDATION | Validation géospatiale | DIRECTEUR_CADASTRE | approbation | 24h | — |
| 5 | SCELLEMENT | Scellement BGU | RESPONSABLE_BGU | scellement | 8h | SCELLER_BGU_GEOM |
| 6 | ARCHIVAGE_ANNF | Archivage ANNF | ARCHIVISTE_ANNF | scellement | 24h | ARCHIVER_BGU_ANNF |

---

## 7. Défauts identifiés

| ID | Sév. | Description |
|---|---|---|
| BUG-WF-BGU-01 | 🔴 | Workflow BGU sans étapes — ne peut jamais démarrer |
| BUG-API-BGU-01 | 🟡 | Endpoint BGU : 3 routes seulement, pas de scellement |
| BUG-API-BGU-02 | 🟡 | Contrôle géométrique non exposé |
| BUG-API-BGU-03 | 🟡 | BGUGeoJSONMaster non accessible via API |
| BUG-API-BGU-04 | 🟢 | Projection BGU non consultable |

---

## 8. Couverture fonctionnelle avant/après corrections

| Fonctionnalité | Avant | Après |
|---|---|---|
| BGUGeoJSONMaster CRUD + scellement | ❌ | ✅ |
| Contrôle géométrique PostGIS | ❌ | ✅ |
| Projection BGU | ❌ | ✅ |
| Statut public portail | ✅ 3 routes | ✅ enrichi |
| Workflow BGU exécutable | ❌ 0 étapes | ✅ 6 étapes |
| Tableau de bord RESPONSABLE_BGU | ❌ | ✅ |
| WF30 intégration BGU | ✅ trigger | ✅ |

**Score module BGU : 20 % → 92 %**
