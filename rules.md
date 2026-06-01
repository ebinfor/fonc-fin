# Règles Absolues et Standards de Qualité

Ce document régit de manière absolue les comportements de tous les agents IA du système **FONCIER+** et les standards de qualité attendus pour la production.

---

## 1. RÈGLES ABSOLUES DES AGENTS (NE JAMAIS)
- ❌ **NE JAMAIS bypasser l'Orchestrateur** : Aucun agent ne peut modifier un fichier ou un code en dehors du protocole de validation supervisé par le **[Meta Orchestrator](file:///c:/Users/USER/Desktop/fonc%20final/skills/1_meta_orchestrator/README.md)**.
- ❌ **NE JAMAIS casser le code existant** : Pas de réécriture totale ou aveugle. Tout refactoring doit être justifié par une dette technique identifiée et approuvée par le **[System Architect](file:///c:/Users/USER/Desktop/fonc%20final/skills/2_system_architect/README.md)**.
- ❌ **NE JAMAIS autoriser de contournement de privilège** : Aucune route API ou action ne doit être développée sans son décorateur RBAC vérifié par le **[Security Engine](file:///c:/Users/USER/Desktop/fonc%20final/skills/6_security_engine/README.md)**.
- ❌ **NE JAMAIS désactiver des tests pour forcer une release** : Si un test existant ou méta-test échoue, la release est immédiatement bloquée par le **[QA Auditor](file:///c:/Users/USER/Desktop/fonc%20final/skills/5_qa_auditor/README.md)**.
- ❌ **NE JAMAIS autoriser de chevauchement géométrique** : Le **[GIS Engine](file:///c:/Users/USER/Desktop/fonc%20final/skills/7_gis_engine/README.md)** doit rejeter toute transaction violant l'intégrité spatiale cadastrale.

---

## 2. COMPORTEMENTS OBLIGATOIRES (TOUJOURS)
- ✅ **TOUJOURS auditer avant d'éditer** : Comprendre précisément l'impact d'une modification de base de données ou de workflow sur le reste du système national.
- ✅ **TOUJOURS valider la conformité juridique** : Le **[GovTech Compliance](file:///c:/Users/USER/Desktop/fonc%20final/skills/9_govtech_compliance/README.md)** doit donner son feu vert réglementaire sur tous les formulaires et libellés de documents générés.
- ✅ **TOUJOURS signer les documents produits** : Tout document officiel sortant du **[Document Engine](file:///c:/Users/USER/Desktop/fonc%20final/skills/8_document_engine/README.md)** doit comporter sa signature numérique X.509 et son QR code cryptographique.
- ✅ **TOUJOURS documenter le code** : Chaque fonction ou API ajoutée par le **[Backend Engineer](file:///c:/Users/USER/Desktop/fonc%20final/skills/4_backend_engineer/README.md)** ou le **[Frontend UX](file:///c:/Users/USER/Desktop/fonc%20final/skills/10_frontend_ux/README.md)** doit être documentée avec des docstrings, des types Python explicites et des commentaires d'impact.

---

## 3. GOUVERNANCE ET RÉSOLUTION DES CONFLITS D'AGENTS
En cas de divergence d'opinions ou de contraintes entre agents, le Meta Orchestrator résout les arbitrages selon la hiérarchie de priorités suivante :
1. 🛡️ **Sécurité & Légalité** : La conformité réglementaire (**GovTech Compliance**) et la sécurité (**Security Engine**) priment sur toute contrainte de performance ou de rapidité.
2. 📐 **Intégrité Structurale** : Les contraintes de la base de données (**System Architect** et **GIS Engine**) priment sur l'affichage visuel (**Frontend UX**).
3. 🧪 **Validation QA** : Si le **QA Auditor** relève une non-conformité, la livraison est gelée, même si tous les autres agents estiment avoir terminé leur travail.

---

## 4. STANDARDS QUALITÉ GOVTECH (Échelle de Priorités)
La qualité de la plateforme est mesurée selon 10 critères non-négociables (dans l'ordre de priorité absolue) :
1. **Stabilité technique** (0 crash système toléré).
2. **Cohérence métier et administrative** (Workflows fluides sans blocage).
3. **Sécurité et intégrité des données** (Zero Trust, RBAC strict).
4. **Modularité et propreté du code** (Prêt pour l'échelle nationale).
5. **Maintenabilité** (Architecture documentée et standardisée).
6. **Auditabilité continue** (Logs immuables de toutes les actions).
7. **Traçabilité spatiale et cadastrale** (PostGIS strict).
8. **Performance** (APIs réactives, chargement UI rapide).
9. **Évolutivité douce** (Migration progressive Flask ➔ FastAPI).
10. **Conformité réglementaire** (Nigerien Land Law compliant).
