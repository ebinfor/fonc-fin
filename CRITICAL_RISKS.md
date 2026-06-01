# ⚠️ Registre des Risques Critiques & Plans de Contingence (CRITICAL_RISKS.md)

**République du Niger**  
*Ministère de l'Urbanisme, de l'Habitat et du Domaine Foncier*  
*Cartographie des Risques Majeurs & Continuité d'Activité GovTech*

---

> [!CAUTION]
> Ce registre identifie les risques macro-systémiques, juridiques et d'infrastructure susceptibles d'impacter la souveraineté du cadastre numérique national **FONCIER+**. Il définit les mesures préventives et les protocoles de secours obligatoires.

---

## 📋 1. Fiches de Cartographie des Risques

### RISK-001 : Collision Spatiale & Chevauchement de Parcelles (Data Collision)
*   **Probabilité** : **Élevée** (surtout lors de la phase d'importation vectorielle massive des anciens plans papier).
*   **Impact** : **Majeur** (Génère des contentieux juridiques complexes, ruine la confiance publique dans la fiabilité du cadastre).
*   **Description** : Deux parcelles distinctes se chevauchent sur la carte suite à des relevés GPS approximatifs ou des falsifications d'arpentage.
*   **Mesures Préventives (Mitigation)** : Intégration systématique du trigger PostGIS anti-fraude d'empiétement (`tg_antifraude_overlap`) bloquant toute immatriculation si le chevauchement dépasse 0.05m².

### RISK-002 : Interruption Globale de Service (Downtime / Single Point of Failure)
*   **Probabilité** : **Moyenne** (Infrastructures réseau et électriques locales sujettes à des instabilités périodiques).
*   **Impact** : **Majeur** (Paralyse l'ensemble des études notariales du Niger, bloque l'instruction des concessions communales).
*   **Description** : Le conteneur serveur monolithique ou la base de données PostgreSQL subit une panne matérielle ou de réseau.
*   **Mesures Préventives (Mitigation)** : Transition programmée vers un déploiement microservices avec réplication multi-nœuds (Actif/Passif) de la base de données PostgreSQL, configurée sous haute disponibilité (HA).

### RISK-003 : Altération Rétroactive des Droits (Ledger Corruption)
*   **Probabilité** : **Faible** (Requis accès privilégié administrateur ou intrusion système directe).
*   **Impact** : **Désastreux** (Perte de valeur probante des archives nationales de propriété, contestation des titres d'État).
*   **Description** : Un acteur malveillant modifie frauduleusement une ligne de droit dans PostgreSQL pour s'attribuer une parcelle appartenant à un tiers.
*   **Mesures Préventives (Mitigation)** : Règle de non-modification des tables cadastrales (WORM). Hachage SHA-256 chaîné de chaque ligne de droit (`sha256_version`). Double-vérification instantanée asynchrone avec les scellés répliqués sur la Blockchain ANNF décentralisée.

### RISK-004 : Dépendance Technique de Maintenance (Vendor Lock-in)
*   **Probabilité** : **Moyenne**
*   **Impact** : **Moyen** (Ralentissement des évolutions futures, coûts de maintenance élevés).
*   **Description** : Complexité excessive du moteur d'états des 33 workflows, rendant le système dépendant des développeurs d'origine.
*   **Mesures Préventives (Mitigation)** : Documentation exhaustive des architectures (SYSTEM_MAP, API_REGISTRY, DOSSIER_STATES) et transfert de compétences obligatoire aux équipes techniques d'ingénierie du Ministère.

---

## 🗺️ 2. Matrice d'Évaluation des Risques (Risk Matrix)

```
       Impact 
  ▲
  │  [RISK-003: Ledger]          [RISK-001: Collision]
  │  (Faible, Désastreux)        (Élevée, Majeur)
  │
  │                              [RISK-002: Downtime]
  │                              (Moyenne, Majeur)
  │
  │  [RISK-004: Lock-in]
  │  (Moyenne, Moyen)
  │
  └──────────────────────────────────────────────────► Probabilité
```
