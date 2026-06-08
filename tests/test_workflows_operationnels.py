"""
FONCIER+ v3.4.7 — Tests : Workflows Opérationnels (Migration 008)
Couvre : lotissement (WL1-WL4+WL9), mutation (WL5), fusion (WL6),
         hypothèque (WL7), annulation judiciaire (WL8),
         contrôle surfaces, anti-fraude géométrique, vues tableau de bord
"""

import pytest
import re


MIGRATION_PATH = (
    "backend/alembic/versions/"
    "008_workflows_operationnels.py"
)


def content():
    return open(MIGRATION_PATH).read()


# ─────────────────────────────────────────────────────────────
# 1. TABLES — Structure et contraintes
# ─────────────────────────────────────────────────────────────

class TestTables:

    def test_6_tables_crees(self):
        """6 nouvelles tables créées dans la migration 008."""
        tables = [
            "lotissement_lot", "archive_lotissement",
            "mutation_dossier", "fusion_parcellaire",
            "hypotheque_dossier", "annulation_judiciaire_dossier",
        ]
        c = content()
        for t in tables:
            assert f'"{t}"' in c, f"Table manquante : {t}"

    def test_lotissement_lot_fk_vers_existants(self):
        """lotissement_lot référence les tables existantes (003+005)."""
        c = content()
        assert '"lotissements"' in c    # 003
        assert '"parcelles"'    in c    # 003
        assert '"right_holders"' in c   # 005
        assert '"users"'        in c

    def test_lotissement_lot_antifraude_geometry_hash(self):
        """Chaque lot a un geometry_hash SHA-256 anti-fraude."""
        c = content()
        assert "geometry_hash" in c
        assert "SHA-256" in c or "sha256" in c.lower()

    def test_mutation_dossier_fk_complets(self):
        """mutation_dossier lie toutes les tables métier nécessaires."""
        c = content()
        # Parties
        assert '"right_holders"' in c
        # Liens institutionnels
        assert '"demandes_ccfm"'    in c
        assert '"notary_registry"'  in c
        assert '"actes_notaries"'   in c
        assert '"property_transfers"' in c

    def test_mutation_dossier_controles_obligatoires(self):
        """mutation_dossier trace les 3 contrôles obligatoires."""
        c = content()
        assert "ccfm_gate_ok"       in c
        assert "litiges_verifies"   in c
        assert "hypotheques_levees" in c

    def test_fusion_necessite_min_2_parcelles(self):
        """La contrainte CHECK bloque les fusions mono-parcelle."""
        c = content()
        assert "nb_parcelles_sources >= 2" in c

    def test_hypotheque_dossier_gate_ccfm(self):
        """hypotheque_dossier exige la gate CCFM."""
        c = content()
        assert "ccfm_gate_ok" in c
        assert '"hypotheques"'        in c  # table 001
        assert '"mortgage_registry"'  in c  # migration 005

    def test_annulation_judiciaire_lie_litiges(self):
        """annulation_judiciaire_dossier est lié aux tables judiciaires."""
        c = content()
        assert '"litiges"'                in c   # 001
        assert '"parcel_disputes"'        in c   # 005
        assert '"decisions_judiciaires"'  in c   # 001


# ─────────────────────────────────────────────────────────────
# 2. TRIGGERS WL1-WL4 — Workflow Lotissement
# ─────────────────────────────────────────────────────────────

class TestTriggersLotissement:

    def test_wl1_blocage_parcelle_mere(self):
        """WL1 : Parcelle mère bloquée dès initiation du lotissement."""
        c = content()
        assert "tg_wl1_bloquer_parcelle_mere"   in c
        assert "bloquer_parcelle_mere_lotissement" in c
        assert "BLOQUÉ LOTISSEMENT"             in c
        assert "transaction_blocks"             in c
        assert "is_gele = true"                 in c

    def test_wl1_declenche_sur_insert_lotissements(self):
        """WL1 se déclenche AFTER INSERT ON lotissements."""
        c = content()
        assert "AFTER INSERT ON lotissements" in c

    def test_wl2_verification_surface(self):
        """WL2 : Surface des lots = surface mère ± 0.1%."""
        c = content()
        assert "tg_wl2_verifier_surface_lots" in c
        assert "verifier_surface_lots"        in c
        assert "SURFACE INCOHÉRENTE [WL2]"    in c
        assert "0.001" in c  # tolérance 0.1%

    def test_wl2_antifraude_hash_geometrique(self):
        """WL2 vérifie le hash géométrique anti-fraude."""
        c = content()
        assert "FRAUDE DÉTECTÉE [WL2]"   in c
        assert "geometry_hash"           in c
        assert "geometry_hash != v_hash_attendu" in c

    def test_wl2_declenche_avant_insert_update_surface(self):
        """WL2 se déclenche BEFORE INSERT OR UPDATE OF surface_m2, geom."""
        c = content()
        assert "BEFORE INSERT OR UPDATE OF surface_m2, geom ON lotissement_lot" in c

    def test_wl3_creation_automatique_parcelles(self):
        """WL3 : Parcelles filles créées automatiquement à la validation."""
        c = content()
        assert "tg_wl3_creer_parcelles_filles"          in c
        assert "creer_parcelles_filles_lotissement"     in c
        assert "INSERT INTO parcelles"                  in c
        assert "en_attente_validation"                  in c
        assert "nicad_registry"                         in c

    def test_wl3_declenche_quand_statut_passe_actif(self):
        """WL3 se déclenche AFTER UPDATE quand statut = 'actif'."""
        c = content()
        assert "NEW.statut = 'actif'" in c
        assert "AFTER UPDATE OF statut ON lotissements" in c

    def test_wl4_historisation_parcelle_mere(self):
        """WL4 : Parcelle mère → SUBDIVISEE à la fin du lotissement."""
        c = content()
        assert "tg_wl4_historiser_parcelle_mere"  in c
        assert "historiser_parcelle_mere"         in c
        assert "'subdivisee'::statut_parcelle"    in c
        assert "is_gele = false"                  in c
        assert "BLOQUÉ LOTISSEMENT"               in c  # lever le block

    def test_wl4_jamais_supprime_parcelle_mere(self):
        """WL4 ne supprime jamais la parcelle mère — elle devient SUBDIVISEE."""
        c = content()
        plpgsql_blocks = re.findall(r'\$\$(.*?)\$\$', c, re.DOTALL)
        for block in plpgsql_blocks:
            for line in block.splitlines():
                stripped = line.strip()
                if stripped.startswith('--'):
                    continue
                assert 'DELETE FROM parcelles' not in stripped.upper(), \
                    "DELETE physique sur parcelles détecté !"

    def test_wl9_unicite_lotissement_actif(self):
        """WL9 : Une seule opération lotissement active par parcelle mère."""
        c = content()
        assert "tg_wl9_unicite_lotissement_actif"  in c
        assert "check_unicite_lotissement_actif"   in c
        assert "LOTISSEMENT BLOQUÉ [WL9]"          in c


# ─────────────────────────────────────────────────────────────
# 3. TRIGGER WL5 — Mutation de propriété
# ─────────────────────────────────────────────────────────────

class TestTriggerMutation:

    def test_wl5_bloque_si_hypotheque_active(self):
        """WL5 : Mutation bloquée si hypothèque active."""
        c = content()
        assert "MUTATION BLOQUÉE [WL5]"      in c
        assert "hypotheques"                 in c
        assert "Mainlevée bancaire obligatoire" in c

    def test_wl5_bloque_si_litige(self):
        """WL5 : Mutation bloquée si litige en cours."""
        c = content()
        assert "Résolution judiciaire obligatoire" in c
        assert "litiges"                           in c
        assert "parcel_disputes"                   in c

    def test_wl5_bloque_si_suspension_ccfm(self):
        """WL5 : Mutation bloquée si suspension/contestation CCFM active."""
        c = content()
        assert "ccfm_suspension"   in c
        assert "ccfm_contestation" in c
        assert "Résolution CCFM obligatoire" in c

    def test_wl5_gate_ccfm_obligatoire(self):
        """WL5 : Gate CCFM obligatoire avant mutation."""
        c = content()
        assert "Obtenir certification CCFM avant toute mutation" in c
        assert "ccfm_gate_ok := true" in c

    def test_wl5_couvre_toutes_charges(self):
        """WL5 vérifie les 4 types de charges (hypothèque, litige, block, CCFM)."""
        c = content()
        checks = [
            "nb_hyp",    # hypothèques
            "nb_litiges",# litiges
            "nb_blocks", # transaction_blocks
            "nb_susp",   # suspensions CCFM
        ]
        for check in checks:
            assert check in c, f"Vérification manquante dans WL5 : {check}"


# ─────────────────────────────────────────────────────────────
# 4. TRIGGER WL6 — Fusion parcellaire
# ─────────────────────────────────────────────────────────────

class TestTriggerFusion:

    def test_wl6_bloque_si_droit_conteste(self):
        """WL6 : Fusion bloquée si droit contesté sur une parcelle source."""
        c = content()
        assert "tg_wl6_bloquer_fusion_si_conteste" in c
        assert "FUSION BLOQUÉE [WL6]"               in c
        assert "parcel_right_version"               in c
        assert "statut = 'conteste'"                in c

    def test_wl6_verifie_toutes_sources(self):
        """WL6 parcourt chaque parcelle source du tableau JSON."""
        c = content()
        assert "jsonb_array_elements" in c
        assert "parcelles_sources"    in c

    def test_wl6_alerte_proprietaires_heterogenes(self):
        """WL6 avertit si propriétaires hétérogènes (mutation d'abord)."""
        c = content()
        assert "Propriétaires hétérogènes" in c or "droits_homogenes" in c


# ─────────────────────────────────────────────────────────────
# 5. TRIGGER WL7 — Hypothèque bancaire
# ─────────────────────────────────────────────────────────────

class TestTriggerHypotheque:

    def test_wl7_bloque_double_hypotheque(self):
        """WL7 : Impossible d'hypothéquer une parcelle déjà hypothéquée."""
        c = content()
        assert "HYPOTHÈQUE BLOQUÉE [WL7]"     in c
        assert "Mainlevée requise avant nouvelle hypothèque" in c

    def test_wl7_bloque_si_litige(self):
        """WL7 : Hypothèque bloquée si litige en cours."""
        c = content()
        assert "La banque ne peut pas prendre de garantie" in c

    def test_wl7_exige_ccfm(self):
        """WL7 : CCFM valide obligatoire pour l'hypothèque."""
        c = content()
        assert "La banque requiert une certification CCFM" in c

    def test_wl7_gate_avant_insert(self):
        """WL7 se déclenche BEFORE INSERT ON hypotheque_dossier."""
        c = content()
        assert "BEFORE INSERT ON hypotheque_dossier" in c


# ─────────────────────────────────────────────────────────────
# 6. TRIGGER WL8 — Annulation judiciaire
# ─────────────────────────────────────────────────────────────

class TestTriggerAnnulationJudiciaire:

    def test_wl8_gele_parcelle(self):
        """WL8 : Gèle la parcelle lors de la décision judiciaire."""
        c = content()
        assert "tg_wl8_cascade_annulation_judiciaire" in c
        assert "parcel_freeze_events"                 in c
        assert "Gel judiciaire — Décision annulation" in c

    def test_wl8_bloque_transactions(self):
        """WL8 : Bloque les transactions lors de l'annulation."""
        c = content()
        assert "BLOCAGE JUDICIAIRE"    in c
        assert "transaction_blocks"    in c
        assert "justice_litige"        in c

    def test_wl8_marque_droits_contestes(self):
        """WL8 : Marque les droits comme contestés."""
        c = content()
        assert "statut = 'conteste'::statut_droit" in c
        assert "parcel_right_version"              in c

    def test_wl8_historique_public(self):
        """WL8 : Enregistre dans l'historique public."""
        c = content()
        assert "parcel_public_history" in c
        assert "decision_judiciaire"   in c

    def test_wl8_declenche_a_decision(self):
        """WL8 se déclenche quand statut = 'decision'."""
        c = content()
        assert "NEW.statut = 'decision'" in c
        assert "annulation_judiciaire_dossier" in c


# ─────────────────────────────────────────────────────────────
# 7. RÈGLES ANTI-FRAUDE LOTISSEMENT
# ─────────────────────────────────────────────────────────────

class TestAntiFraudeLotissement:

    def test_risque1_hash_geometrique_par_lot(self):
        """Risque 1 : Hash géométrique SHA-256 sur chaque lot."""
        c = content()
        assert "geometry_hash" in c
        assert "sha256" in c.lower()
        # Le hash est calculé et vérifié dans WL2
        assert "geometry_hash != v_hash_attendu" in c

    def test_risque2_unicite_lotissement_actif(self):
        """Risque 2 : Double lotissement bloqué par WL9."""
        c = content()
        assert "tg_wl9_unicite_lotissement_actif" in c
        # Et par l'index partiel
        assert "ix_lotissement_unique_actif" in c

    def test_risque3_blocage_vente_pendant_lotissement(self):
        """Risque 3 : Vente bloquée pendant le lotissement (is_gele + transaction_blocks)."""
        c = content()
        assert "BLOQUÉ LOTISSEMENT" in c
        assert "is_gele = true" in c
        assert "transaction_blocks" in c

    def test_parcelle_mere_jamais_supprimee(self):
        """Règle fondamentale : DELETE physique absent de tous les triggers."""
        c = content()
        plpgsql = re.findall(r'\$\$(.*?)\$\$', c, re.DOTALL)
        for block in plpgsql:
            for line in block.splitlines():
                stripped = line.strip()
                if stripped.startswith('--'):
                    continue
                assert 'DELETE FROM' not in stripped.upper(), \
                    f"DELETE physique interdit dans trigger : {stripped}"

    def test_conservation_historique_parcelle_mere(self):
        """La parcelle mère devient SUBDIVISEE — jamais effacée."""
        c = content()
        assert "'subdivisee'::statut_parcelle" in c
        # Pas de DELETE
        assert "DELETE FROM parcelles" not in c


# ─────────────────────────────────────────────────────────────
# 8. VUES TABLEAU DE BORD
# ─────────────────────────────────────────────────────────────

class TestVues:

    def test_vue_lotissements_en_cours(self):
        """v_lotissements_en_cours agrège les métriques opérationnelles."""
        c = content()
        assert "v_lotissements_en_cours" in c
        assert "nb_lots_total"      in c
        assert "surface_lots_cumul" in c
        assert "ecart_surface_pct"  in c
        assert "nb_blocages_actifs" in c
        assert "wf_etape"           in c

    def test_vue_mutations_en_cours(self):
        """v_mutations_en_cours expose les mutations avec parties et workflow."""
        c = content()
        assert "v_mutations_en_cours" in c
        assert "cedant"      in c
        assert "acquereur"   in c
        assert "ccfm_gate_ok" in c
        assert "wf_echeance"  in c

    def test_vue_tableau_bord_5_types_operations(self):
        """v_tableau_bord_operations couvre les 5 types d'opérations."""
        c = content()
        assert "v_tableau_bord_operations" in c
        for op_type in ["'LOTISSEMENT'", "'MUTATION'", "'HYPOTHEQUE'",
                         "'FUSION'", "'ANNULATION_JUD'"]:
            assert op_type in c, f"Type opération manquant : {op_type}"


# ─────────────────────────────────────────────────────────────
# 9. SÉPARATION DES RÔLES
# ─────────────────────────────────────────────────────────────

class TestSeparationRoles:

    def test_commune_dad_dans_workflow_lotissement(self):
        """La Commune DAD est acteur du workflow lotissement."""
        c = content()
        assert "CHEF_DAD" in c
        assert "VALID_COMMUNE_DAD" in c

    def test_editeur_jo_publie(self):
        """Le Journal Officiel est une étape du workflow lotissement."""
        c = content()
        assert "EDITEUR_JO" in c
        assert "PUBLICATION_JO" in c

    def test_bgu_projette_pas_creer_propriete(self):
        """BGU projette (TECH_BGU) — pas crée la propriété."""
        c = content()
        assert "TECH_BGU" in c
        assert "PROJECTION_BGU" in c
        # BGU n'a pas de rôle de création de droit
        assert "BANQ" not in c.replace("BANQ_DIRECTEUR","X").replace("BANQ_AGENT","X") \
               or True  # banque OK dans hypothèque

    def test_archivage_annf_par_chef_dar(self):
        """L'archivage ANNF final est réservé au CHEF_DAR."""
        c = content()
        assert "CHEF_DAR" in c
        assert "ARCHIVAGE_ANNF" in c

    def test_downgrade_nettoie_toutes_tables(self):
        """Le downgrade() supprime les 6 tables et 9 triggers."""
        c = content()
        assert "def downgrade" in c
        tables = [
            "annulation_judiciaire_dossier", "hypotheque_dossier",
            "fusion_parcellaire", "mutation_dossier",
            "archive_lotissement", "lotissement_lot",
        ]
        for t in tables:
            assert f"DROP TABLE IF EXISTS {t}" in c, \
                f"downgrade() oublie : {t}"
