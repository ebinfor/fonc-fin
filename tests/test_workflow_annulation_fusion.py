"""
FONCIER+ v3.4.7 — Tests : Workflows 4 (Annulation) & 5 (Fusion) — Migration 010
Couvre : 4 tables, 8 fonctions F_AJ1-F_AJ8, 4 triggers TA1-TA4,
         authenticité jugement, polymorphisme, restauration propriétaire,
         règle DELETE interdit, workflow fusion 12 étapes
"""

import pytest
import re

MIG = (
    "/home/claude/foncier_v347/backend/alembic/versions/"
    "010_workflow_annulation_et_fusion.py"
)


def c():
    return open(MIG).read()


# ─────────────────────────────────────────────────────────────
# 1. TABLES
# ─────────────────────────────────────────────────────────────

class TestTables:

    def test_4_tables_crees(self):
        """4 tables créées dans la migration 010."""
        tables = [
            "greffe_judiciaire", "objet_annule",
            "historique_annulation", "signature_judiciaire",
        ]
        content = c()
        for t in tables:
            assert f'"{t}"' in content, f"Table manquante : {t}"

    def test_greffe_judiciaire_code_unique(self):
        """greffe_judiciaire a un code tribunal unique."""
        content = c()
        assert "tribunal_code" in content
        assert "unique=True" in content

    def test_greffe_cle_verification(self):
        """greffe_judiciaire a une clé de vérification PKI."""
        content = c()
        assert "cle_verification" in content

    def test_objet_annule_polymorphique(self):
        """objet_annule couvre les 9 types d'objets du spec."""
        content = c()
        types_requis = [
            "mutation","rnaf","rnp","lotissement",
            "hypotheque","ccfm","acte_notarie","droit_foncier","transfert"
        ]
        for t in types_requis:
            assert f"'{t}'" in content, f"Type d'objet manquant : {t}"

    def test_objet_annule_granularite_partielle(self):
        """objet_annule gère les annulations partielles (risque 2)."""
        content = c()
        assert "est_partielle"  in content
        assert "details_partie" in content

    def test_objet_annule_sha256_etat_avant(self):
        """sha256_objet_avant capture l'état avant annulation."""
        content = c()
        assert "sha256_objet_avant" in content

    def test_historique_annulation_universel(self):
        """historique_annulation couvre tous les types d'objets."""
        content = c()
        assert "type_objet"       in content
        assert "reference_objet"  in content
        assert "table_source"     in content
        assert "ancien_statut"    in content
        assert "nouveau_statut"   in content
        assert "snapshot_avant"   in content

    def test_signature_judiciaire_complete(self):
        """signature_judiciaire a tous les champs anti-falsification."""
        content = c()
        assert "reference_jugement" in content
        assert "sha256_jugement"    in content
        assert "signature_b64"      in content
        assert "horodatage_at"      in content
        assert "horodatage_tsp"     in content

    def test_decisions_judiciaires_enrichi(self):
        """decisions_judiciaires est enrichi avec greffe + référence + SHA-256."""
        content = c()
        assert "add_column" in content
        assert "greffe_id"              in content
        assert "reference_jugement"     in content
        assert "sha256_jugement"        in content
        assert "authenticite_verifiee"  in content

    def test_seed_9_greffes_niger(self):
        """9 greffes des 8 régions + Cour d'Appel Niamey seedés."""
        content = c()
        greffes = [
            "TGI-NIA-001", "TGI-MAR-001", "TGI-ZIN-001",
            "TGI-TAH-001", "TGI-TIL-001", "TGI-DOS-001",
            "TGI-AGA-001", "TGI-DIF-001", "CA-NIA-001",
        ]
        for code in greffes:
            assert code in content, f"Greffe manquant : {code}"


# ─────────────────────────────────────────────────────────────
# 2. FONCTIONS F_AJ1–F_AJ8
# ─────────────────────────────────────────────────────────────

class TestFonctions:

    def test_8_fonctions_declarees(self):
        """Les 8 fonctions du workflow annulation sont déclarées."""
        content = c()
        fonctions = [
            "verifier_authenticite_jugement",
            "bloquer_objet_judiciaire",
            "appliquer_annulation_objet",
            "restaurer_proprietaire_precedent",
            "invalider_ccfm_apres_annulation",
            "archiver_annulation_annf",
            "debloquer_objet_apres_decision",
            "executer_annulation_complete",
        ]
        for fn in fonctions:
            assert fn in content, f"Fonction manquante : {fn}"

    def test_f1_verifie_greffe_actif(self):
        """F_AJ1 vérifie que le greffe est actif."""
        content = c()
        assert "tribunal_code = p_greffe_code"      in content
        assert "statut = 'actif'"                   in content
        assert "Greffe suspendu ou fermé"           in content

    def test_f1_detecte_doublon_jugement(self):
        """F_AJ1 bloque les doublons de numéro de jugement (anti-fraude)."""
        content = c()
        assert "reference_jugement = p_reference_jugem" in content
        assert "FRAUDE_POTENTIELLE"                      in content
        assert "doublon suspect"                         in content

    def test_f2_polymorphique_6_types(self):
        """F_AJ2 gère les 6 types d'objets du spec."""
        content = c()
        for obj_type in ["rnp","mutation","hypotheque","ccfm","rnaf","acte_notarie"]:
            assert f"WHEN '{obj_type}'" in content, \
                f"Type non géré dans F_AJ2 : {obj_type}"

    def test_f3_jamais_delete(self):
        """F_AJ3 n'utilise jamais DELETE — annulation logique uniquement."""
        content = c()
        plpgsql = re.findall(r'\$\$(.*?)\$\$', content, re.DOTALL)
        for block in plpgsql:
            for line in block.splitlines():
                stripped = line.strip()
                if stripped.startswith('--'):
                    continue
                assert 'DELETE FROM' not in stripped.upper(), \
                    f"DELETE physique interdit : {stripped[:80]}"

    def test_f3_applique_annulation_par_type(self):
        """F_AJ3 applique l'annulation sur chaque type de table native."""
        content = c()
        # Toutes les tables doivent recevoir une mise à jour de statut
        tables_cibles = [
            "parcelles",          # RNP
            "mutation_dossier",   # MUTATION
            "rnaf_workflow",      # RNAF
            "hypotheques",        # HYPOTHEQUE
            "demandes_ccfm",      # CCFM
            "lotissements",       # LOTISSEMENT
            "actes_notaries",     # ACTE_NOTARIE
            "parcel_right_version", # DROIT_FONCIER
        ]
        for t in tables_cibles:
            assert f"UPDATE {t}" in content, \
                f"Table non traitée dans F_AJ3 : {t}"

    def test_f4_restaure_via_version_precedente_id(self):
        """F_AJ4 remonte la chaîne version_precedente_id (005)."""
        content = c()
        assert "version_precedente_id" in content
        assert "statut = 'historique'" in content
        assert "statut = 'actif'::statut_droit" in content
        assert "Propriétaire précédent restauré" in content

    def test_f4_met_a_jour_version_active(self):
        """F_AJ4 met à jour version_active_id sur la parcelle."""
        content = c()
        assert "version_active_id = v_version_precedente.id" in content

    def test_f5_invalide_ccfm_apres_annulation(self):
        """F_AJ5 invalide les CCFM actifs après annulation."""
        content = c()
        assert "invalider_ccfm_apres_annulation" in content
        assert "nouveau CCFM requis" in content
        assert "SET statut = 'ANNULE'" in content

    def test_f6_utilise_generate_annf_ida(self):
        """F_AJ6 génère un IDA ANNF pour chaque archivage."""
        content = c()
        assert "generate_annf_ida" in content
        assert "decision_justice" in content

    def test_f7_debloque_selon_type(self):
        """F_AJ7 débloque les 3 types principaux (rnp, rnaf, ccfm)."""
        content = c()
        assert "is_gele = false"            in content
        assert "'publie'::statut_rnaf"       in content
        assert "ccfm_suspension"            in content

    def test_f8_orchestration_ordonnee(self):
        """F_AJ8 exécute les annulations dans un ordre métier défini."""
        content = c()
        assert "executer_annulation_complete" in content
        # L'ordre : hypothèque d'abord, lotissement en dernier
        assert "WHEN 'hypotheque'  THEN 1"  in content
        assert "WHEN 'lotissement' THEN 9"  in content

    def test_f8_bloque_si_authenticite_non_verifiee(self):
        """F_AJ8 refuse si l'authenticité du jugement n'est pas vérifiée."""
        content = c()
        assert "authenticite_verifiee = true" in content
        assert "Authenticité du jugement non vérifiée" in content

    def test_f8_gere_les_erreurs_partielles(self):
        """F_AJ8 traite les erreurs objet par objet sans tout annuler."""
        content = c()
        assert "v_nb_ok"  in content
        assert "v_nb_err" in content
        assert "EXCEPTION WHEN OTHERS" in content


# ─────────────────────────────────────────────────────────────
# 3. TRIGGERS TA1–TA4
# ─────────────────────────────────────────────────────────────

class TestTriggers:

    def test_ta1_blocage_a_la_saisine(self):
        """TA1 : blocage objet immédiat à l'INSERT dans objet_annule."""
        content = c()
        assert "tg_ta1_bloc_objet_a_saisine"  in content
        assert "AFTER INSERT ON objet_annule" in content
        assert "bloquer_objet_judiciaire"     in content

    def test_ta2_execution_cascade_a_decision(self):
        """TA2 : exécution complète quand statut passe à 'execution'."""
        content = c()
        assert "tg_ta2_execution_a_decision"             in content
        assert "annulation_judiciaire_dossier"           in content
        assert "executer_annulation_complete"            in content
        assert "NEW.statut::TEXT = 'execution'"          in content

    def test_ta3_restauration_droit_apres_annule(self):
        """TA3 : restaure le droit précédent quand objet_annule passe à 'annule'."""
        content = c()
        assert "tg_ta3_restaure_droit_si_annule"     in content
        assert "AFTER UPDATE OF statut_execution"    in content
        assert "restaurer_proprietaire_precedent"    in content
        assert "statut_execution::TEXT = 'annule'"   in content

    def test_ta4_cree_parcelle_resultante_fusion(self):
        """TA4 : crée automatiquement la parcelle résultante de la fusion."""
        content = c()
        assert "tg_ta4_fusion_cree_nouveau_titre"    in content
        assert "fusion_parcellaire"                  in content
        assert "INSERT INTO parcelles"               in content
        assert "parcelle_resultante_id = v_new_parc_id" in content

    def test_ta4_archive_parcelles_sources(self):
        """TA4 archive les parcelles sources (jamais DELETE)."""
        content = c()
        assert "'archive'::statut_parcelle" in content
        assert "Parcelle fusionnée dans" in content


# ─────────────────────────────────────────────────────────────
# 4. WORKFLOW FUSION — 12 étapes
# ─────────────────────────────────────────────────────────────

class TestWorkflowFusion:

    def test_12_etapes_fusion_seeded(self):
        """Le workflow FUSION a 12 étapes dans workflow_step_def."""
        content = c()
        etapes = [
            "'FUSION_INIT'", "'FUSION_VERIF_DROITS'", "'FUSION_VERIF_CHARGES'",
            "'FUSION_CCFM_GATE'", "'FUSION_ARRETE'", "'FUSION_GEOMETRIE'",
            "'FUSION_VALIDATION_CADASTRE'", "'FUSION_NICAD'",
            "'FUSION_DROITS'", "'FUSION_BGU'", "'FUSION_CCFM_NOUVEAU'",
            "'FUSION_ANNF'",
        ]
        for etape in etapes:
            assert etape in content, f"Étape fusion manquante : {etape}"

    def test_fusion_verifie_droits_homogenes(self):
        """L'étape 2 vérifie l'homogénéité des droits."""
        content = c()
        assert "FUSION_VERIF_DROITS"       in content
        assert "CHECK_DROITS_HOMOGENES"    in content

    def test_fusion_gate_ccfm_multi(self):
        """L'étape 4 vérifie la gate CCFM pour toutes les parcelles sources."""
        content = c()
        assert "FUSION_CCFM_GATE"     in content
        assert "CHECK_CCFM_GATE"      in content

    def test_fusion_nouveau_ccfm_en_fin(self):
        """Un nouveau CCFM est requis sur la parcelle résultante."""
        content = c()
        assert "FUSION_CCFM_NOUVEAU"      in content
        assert "GENERER_CCFM_FUSION"      in content

    def test_fusion_notaire_pour_droits(self):
        """Le NOTAIRE certifie la création des droits sur la parcelle résultante."""
        content = c()
        assert "'FUSION_DROITS'" in content
        assert "'NOTAIRE'"       in content

    def test_fusion_archivage_annf_final(self):
        """L'archivage ANNF est la dernière étape."""
        content = c()
        assert "'FUSION_ANNF'"             in content
        assert "'ARCHIVER_FUSION_ANNF'"    in content
        assert "'CHEF_DAR'"               in content

    def test_ta4_jamais_delete_sources(self):
        """TA4 archive les sources — jamais DELETE."""
        content = c()
        plpgsql = re.findall(r'tg_fn_fusion_cree_nouveau_titre.*?\$\$.*?\$\$',
                              content, re.DOTALL)
        for block in plpgsql:
            assert "DELETE FROM" not in block.upper()


# ─────────────────────────────────────────────────────────────
# 5. RÈGLES FONDAMENTALES
# ─────────────────────────────────────────────────────────────

class TestRegles:

    def test_annulation_jamais_delete(self):
        """Règle absolue : aucun DELETE dans toutes les fonctions."""
        content = c()
        plpgsql = re.findall(r'\$\$(.*?)\$\$', content, re.DOTALL)
        for block in plpgsql:
            for line in block.splitlines():
                stripped = line.strip()
                if stripped.startswith('--'):
                    continue
                assert 'DELETE FROM' not in stripped.upper(), \
                    f"DELETE physique interdit : {stripped[:80]}"

    def test_decision_judiciaire_unique_par_greffe(self):
        """Une référence jugement est unique par greffe."""
        content = c()
        assert "reference_jugement = p_reference_jugem" in content
        assert "greffe_id = v_greffe_id" in content

    def test_authenticite_obligatoire_avant_execution(self):
        """F_AJ8 refuse si authenticité non vérifiée — anti-faux-jugement."""
        content = c()
        assert "authenticite_verifiee = true" in content
        assert "'BLOQUÉ: Authenticité" in content

    def test_historique_conserve_pour_tous_objets(self):
        """Chaque annulation est tracée dans historique_annulation."""
        content = c()
        assert "INSERT INTO historique_annulation" in content
        # Présent dans au moins 3 fonctions
        count = content.count("INSERT INTO historique_annulation")
        assert count >= 3, f"Seulement {count} insertions dans historique_annulation"

    def test_archivage_annf_systematique(self):
        """L'archivage ANNF est appelé après chaque annulation."""
        content = c()
        assert "archiver_annulation_annf" in content
        assert "annf_archive_links" in content

    def test_vues_exposent_etat_complet(self):
        """Les vues exposent l'état complet des opérations."""
        content = c()
        assert "v_annulations_en_cours" in content
        assert "v_fusions_en_cours"     in content
        assert "authenticite_verifiee"  in content
        assert "nb_objets_annuler"      in content
        assert "droits_homogenes"       in content

    def test_downgrade_complet(self):
        """downgrade() nettoie toutes les créations."""
        content = c()
        for table in ["signature_judiciaire","historique_annulation",
                      "objet_annule","greffe_judiciaire"]:
            assert f"DROP TABLE IF EXISTS {table}" in content
        for fn in ["executer_annulation_complete",
                   "restaurer_proprietaire_precedent",
                   "verifier_authenticite_jugement"]:
            assert fn in content
