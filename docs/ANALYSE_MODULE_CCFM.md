
# FONCIER+ — Analyse module par module
# MODULE 4 : CCFM (Certificat de Conformité Foncière et Minière)
# Module indépendant — garant de la conformité foncière nationale
# Avril 2026

---

## 1. Identité du module

Le CCFM est le module **certifiant** de la plateforme. Il est le seul
module habilité à produire la preuve juridique qu'une parcelle est
conforme à toutes les règles foncières en vigueur. Sans CCFM valide :
- Aucun acte notarié n'est possible (gate TransactionGate)
- Aucune hypothèque bancaire n'est possible
- Aucun transfert de propriété n'est enregistrable

Le CCFM est **indépendant** : il ne dépend pas du Cadastre ni du BGU
organisationnellement — il les *interroge* via ses 7 fonctions de
contrôle F1-F7.

---

## 2. Architecture du moteur CCFM

```
demandes_ccfm  (legacy 001)
      ↓ UPDATE statut → 'EN_VERIFICATION'
tg_tc1_auto_run_ccfm_moteur  (trigger automatique)
      ↓
generate_ccfm_certificate(demande_id, parcelle_id, rnaf_id)
      ↓ exécute séquentiellement
  F1 ccfm_ctrl_rnaf_validity     → RNAF publié ? Pas suspendu ?
  F2 ccfm_ctrl_rnp_validity      → Parcelle active ? NICAD valide ?
  F3 ccfm_ctrl_urbanisme         → Conformité urbanistique (urbanisme_conformite)
  F4 ccfm_ctrl_bgu               → Projection BGU valide ? Écart surface < 0.5% ?
  F5 ccfm_ctrl_absence_litige    → Aucun litige ouvert ?
  F6 ccfm_ctrl_absence_hypotheque → Aucune hypothèque active ?
  F7 ccfm_ctrl_geometrie         → Géométrie fermée ? Pas de superposition ?
      ↓ résultat JSONB complet
  Décision : VALIDE | CONDITIONNEL | REFUSE
  SHA-256 chaîné sur le résultat
      ↓
  ccfm_verification_log (SHA-256 chaîné sur chaque contrôle)
      ↓ si VALIDE
  ccfm_f8_orchestrer(NUS)   (migration 018)
      ↓
  ccfm_signer_safe(NUS, user_id)   (migration 018)
```

**Contrôles BLOQUANTS** (échouent → REFUSE) : F1, F2, F5, F7
**Contrôles AVERTISSEMENT** (échouent → CONDITIONNEL) : F3, F4, F6

---

## 3. Tables du module CCFM

| Table | Migration | Rôle |
|---|---|---|
| `demandes_ccfm` | legacy 001 | Demandes de certificat (NUS, statut, parcelle_id) |
| `ccfm_contestation` | 006 | Contestation d'un CCFM → blocage auto transactions |
| `ccfm_suspension` | 004 | Suspension active → bloque toutes transactions |
| `ccfm_verification_log` | 009 | Journal SHA-256 chaîné de chaque contrôle F1-F7 |
| `urbanisme_conformite` | 009 | Conformité urbanistique (alimente F3) |

---

## 4. Workflow CCFM — 6 étapes (après correction migration 024)

| Ordre | Code | Rôle | Type | Action |
|---|---|---|---|---|
| 1 | RECEPTION | AGENT_CCFM | approbation | — |
| 2 | VERIFICATION_ADMIN | CHEF_CCFM | approbation | — |
| 3 | CONSTAT_TERRAIN | TOPOGRAPHE | constat | — |
| 4 | VALIDATION_DIRECTEUR | DIRECTEUR_URBANISME | approbation | — |
| 5 | SCELLEMENT | MINISTRE_URBANISME | scellement | GENERER_QR_CODE |
| 6 | ARCHIVAGE | ARCHIVISTE_ANNF | approbation | ARCHIVER_CCFM_ANNF |

---

## 5. NUS — Numéro Unique Séquentiel

Le NUS est l'identifiant unique du certificat CCFM.
Format depuis migration 021 : `CCF-{YYYY}-{NNNNNNN}`
Généré via la séquence atomique `seq_certificat_ccfm`.

Dans l'endpoint actuel :
```python
res = await db.execute(
    text("SELECT generer_numero_officiel('CCF', 'seq_certificat_ccfm')")
)
nus = res.scalar()
```

---

## 6. Défauts identifiés

| ID | Sév. | Description |
|---|---|---|
| BUG-CCFM-01 | 🔴 | `ccfm_f[1-7]()` appelées avec `f'ccfm_{code}'` → F1..F7 non correspondants |
| BUG-CCFM-02 | 🟡 | Contestation CCFM sans endpoint dédié |
| BUG-CCFM-03 | 🟡 | `urbanisme_conformite` non exposée via API |
| BUG-CCFM-04 | 🟡 | `ccfm_verification_log` non consultable |
| BUG-CCFM-05 | 🟢 | Tableau de bord CHEF_CCFM absent |
| BUG-CCFM-06 | 🟢 | `generate_ccfm_certificate()` non exposée directement |

### BUG-CCFM-01 — CRITIQUE : noms de fonctions SQL incorrects

Dans l'endpoint `ccfm.py` lancer_controles_ccfm() :
```python
for code in ["f1", "f2", "f3", "f4", "f5", "f6", "f7"]:
    res = await db.execute(text(f"SELECT ccfm_{code}(:nus)"), {"nus": nus})
```
Les vraies fonctions PL/pgSQL s'appellent :
- `ccfm_ctrl_rnaf_validity`, `ccfm_ctrl_rnp_validity`, `ccfm_ctrl_urbanisme`
- `ccfm_ctrl_bgu`, `ccfm_ctrl_absence_litige`, `ccfm_ctrl_absence_hypotheque`
- `ccfm_ctrl_geometrie`

`ccfm_f1`, `ccfm_f2`... n'existent pas en base.
**Chaque appel échoue avec `ERROR: function ccfm_f1(text) does not exist`.**
Le code fallback `ok = False` masque l'erreur → les contrôles retournent
toujours `False` sans raison réelle.

La solution propre : appeler **directement** `generate_ccfm_certificate()`
qui orchestre les F1-F7 en une seule requête atomique.

---

## 7. Score de complétude module CCFM

| Fonctionnalité | Avant | Après |
|---|---|---|
| NUS atomique | ✅ | ✅ |
| Dépôt de demande | ✅ | ✅ |
| Contrôles F1-F7 | ❌ (mauvais noms) | ✅ (via generate_ccfm_certificate) |
| Signature sécurisée | ✅ (ccfm_signer_safe) | ✅ |
| Contestation | ❌ | ✅ endpoint |
| Log de contrôle | ✅ DB | ✅ exposé API |
| Urbanisme conformité | ✅ DB | ✅ exposé API |
| Tableau de bord | ❌ | ✅ |

**Score : 55 % → 95 %**
