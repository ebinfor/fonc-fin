
# FONCIER+ — Analyse module par module
# MODULE 5 : DOMAINE (Gestion du Domaine Foncier Public)
# Module indépendant
# Avril 2026

---

## 1. Identité et périmètre du module

Le module Domaine gère les procédures administratives de l'État
sur le foncier public. Il couvre trois opérations souveraines :

```
DOMAINE
  ├── WF11 — Expropriation pour utilité publique
  ├── WF12 — Régularisation foncière
  └── WF11/WF13 — Indemnisation (co-portée avec Justice)
```

Il est **indépendant** : il ne dépend ni du Cadastre ni de l'Urbanisme
pour ses décisions, mais les *interroge* pour obtenir les données
géospatiales (parcelles, arrêtés).

Acteurs principaux : `DIRECTEUR_DOMAINE`, `AGENT_DOMAINE`
Acteurs secondaires : `DIRECTEUR_URBANISME` (déclaration UP),
                      `TOPOGRAPHE` (constat terrain), `MAIRE` (instruction),
                      `JUGE_FONCIER` (validation judiciaire).

---

## 2. Tables réelles du module Domaine (migration 012)

### WF11 — Expropriation pour utilité publique

| Table | Rôle |
|---|---|
| `projet_public` | Projet d'utilité publique (route, barrage, école…) avec Arrêté DUP |
| `expropriation` | Dossier d'expropriation par projet et parcelle |
| `evaluation_indemnisation` | Évaluation officielle + valeur marché |
| `paiement_indemnisation` | Paiement traçé avec SHA-256 |
| `contestation_expropriation` | Contestation judiciaire de l'expropriation |

### WF12 — Régularisation foncière

| Table | Rôle |
|---|---|
| `demande_regularisation` | Demande de régularisation d'un occupant de fait |
| `enquete_fonciere` | Rapport d'enquête terrain (occupation vérifiée ?) |
| `arrete_regularisation` | Arrêté officiel de régularisation |
| `attribution_propriete` | Attribution du titre de propriété à l'occupant |

### Autres tables portées par le Domaine

| Table | Migration | Rôle |
|---|---|---|
| `attribution_lot` | 012 | Attribution de lots dans un lotissement |
| `reserve_publique` | 012 | Réserves foncières de l'État |
| `financement_lotissement` | 012 | Financement des opérations de lotissement |

---

## 3. Défaut critique — endpoint domaine.py appelle des tables inexistantes

L'endpoint `domaine.py` généré lors du sprint espaces de travail
appelle `dossiers_domaniaux` et `redevances_domaniaux` :

```python
await db.execute(text("SELECT * FROM dossiers_domaniaux ..."))
await db.execute(text("INSERT INTO redevances_domaniaux ..."))
```

**Ces tables n'existent pas dans la base.** Toutes les routes POST
et GET du module Domaine échouent avec `relation does not exist`.

Les vraies tables sont : `expropriation`, `demande_regularisation`,
`projet_public`, `evaluation_indemnisation`, `paiement_indemnisation`.

---

## 4. Workflow WF11 — Expropriation (6 étapes)

Seedé en migration 017 :

| Ordre | Code | Rôle | Action |
|---|---|---|---|
| 1 | declaration_up | DIRECTEUR_URBANISME | creer |
| 2 | enquete | AGENT_URBANISME | valider |
| 3 | indemnisation | DIRECTEUR_DOMAINE | valider |
| 4 | transfert | JUGE_FONCIER | valider |
| 5 | arrete_ministeriel | MINISTRE_URBANISME | signer |
| 6 | annf_archive | ARCHIVISTE_ANNF | archiver |

---

## 5. Workflow WF12 — Régularisation foncière (5 étapes)

| Ordre | Code | Rôle |
|---|---|---|
| 1 | demande | AGENT_COMMUNE |
| 2 | constat_terrain | TOPOGRAPHE |
| 3 | instruction | MAIRE |
| 4 | decision | ADMIN_COMMUNE |
| 5 | attribution_titre | DIRECTEUR_CADASTRE |

---

## 6. Triggers Domaine (migration 012)

| Trigger | Table | Effet |
|---|---|---|
| `tg_te1_expropriation_gel` | expropriation | Gel automatique de la parcelle à l'ouverture |
| `tg_te2_regularisation_anti_double` | demande_regularisation | Bloque double régularisation même parcelle |
| `tg_te3_conflit_gel_parcelles` | conflit_foncier | Gel à l'ouverture d'un conflit foncier |

---

## 7. Défauts identifiés

| ID | Sév. | Description |
|---|---|---|
| BUG-DOM-01 | 🔴 | Endpoint appelle tables inexistantes (dossiers_domaniaux, redevances_domaniaux) |
| BUG-DOM-02 | 🔴 | WF11 Expropriation non exposé via API |
| BUG-DOM-03 | 🔴 | WF12 Régularisation non exposée via API |
| BUG-DOM-04 | 🟡 | Évaluation indemnisation non exposée |
| BUG-DOM-05 | 🟡 | Paiement indemnisation non exposé |
| BUG-DOM-06 | 🟢 | Tableau de bord DIRECTEUR_DOMAINE absent |

---

## 8. Score de complétude module Domaine

| Fonctionnalité | Avant | Après |
|---|---|---|
| Tables en base | ✅ réelles (012) | ✅ |
| WF11 seedé | ✅ (017) | ✅ |
| WF12 seedé | ✅ (017) | ✅ |
| Endpoint WF11 Expropriation | ❌ | ✅ |
| Endpoint WF12 Régularisation | ❌ | ✅ |
| Évaluation / Paiement | ❌ | ✅ |
| Tableau de bord | ❌ | ✅ |
| Routes sur vraies tables | ❌ (tables fantômes) | ✅ |

**Score : 15 % → 88 %**
