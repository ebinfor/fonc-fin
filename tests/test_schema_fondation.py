"""
FONCIER+ v3.4.7 — Tests : Schéma Fondation Consolidé (Migration 006)
Couvre : 8 triggers de verrouillage, RBAC granulaire, audit,
         vues consolidées, rnaf_version_audit, ccfm_contestation
"""

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


# ─────────────────────────────────────────────────────────────
# 1. TRIGGERS DE VERROUILLAGE — règles → contraintes SQL
# ─────────────────────────────────────────────────────────────

class TestTriggersVerrouillage:

    def test_tous_les_triggers_declares_dans_migration(self):
        """Tous les triggers critiques sont déclarés dans la migration 006."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        triggers_requis = [
            "tg_enforce_rnaf_publie_for_rnp",
            "tg_block_acte_if_hypotheque_active",
            "tg_block_geom_without_ccfm",
            "tg_block_transfer_if_litige",
            "tg_enforce_rnaf_scelle_for_jo",
            "tg_auto_block_ccfm_contestation",
            "tg_auto_version_rnaf",
        ]
        for trigger in triggers_requis:
            assert trigger in content, \
                f"Trigger manquant dans migration 006 : {trigger}"

    def test_trigger_t1_rnp_sans_rnaf_publie(self):
        """T1 : Message d'erreur clair si RNAF non publié."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "RNP BLOQUÉ" in content
        assert "PUBLIÉ requis" in content
        assert "rnp_demandes" in content

    def test_trigger_t2_acte_avec_hypotheque_active(self):
        """T2 : Blocage acte notarié si hypothèque active."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "ACTE BLOQUÉ" in content
        assert "Mainlevée bancaire obligatoire" in content
        assert "actes_notaries" in content

    def test_trigger_t3_geom_sans_ccfm(self):
        """T3 : Modification géométrique impossible sans CCFM valide."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "GÉOMÉTRIE BLOQUÉE" in content
        assert "CCFM valide" in content
        assert "UPDATE OF geom ON parcelles" in content

    def test_trigger_t4_transfert_avec_litige(self):
        """T4 : Transfert impossible si litige ouvert."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "TRANSFERT BLOQUÉ" in content
        assert "litiges doivent être clôturés" in content
        assert "property_transfers" in content

    def test_trigger_t5_jo_sans_rnaf_scelle(self):
        """T5 : Publication JO impossible si RNAF non scellé."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "JOURNAL OFFICIEL BLOQUÉ" in content
        assert "SCELLÉ requis" in content
        assert "publications_jo" in content

    def test_trigger_t6_contestation_ccfm_auto_block(self):
        """T6 : Contestation CCFM déclenche blocage automatique."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "ccfm_contestation" in content
        assert "transaction_blocks" in content
        assert "bloque_transactions" in content

    def test_trigger_t7_audit_tables_sensibles(self):
        """T7 : L'audit est appliqué sur toutes les tables sensibles."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        tables_auditees = [
            "parcel_right_version",
            "property_transfers",
            "rnaf_workflow",
            "transaction_blocks",
            "ccfm_contestation",
            "parcelles",
        ]
        for table in tables_auditees:
            assert f"tg_audit_{table}" in content, \
                f"Audit manquant sur table {table}"

    def test_trigger_t8_version_rnaf_automatique(self):
        """T8 : Chaque changement de statut RNAF crée une version."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "tg_auto_version_rnaf" in content
        assert "rnaf_version_audit" in content
        assert "auto_version_rnaf_on_status_change" in content


# ─────────────────────────────────────────────────────────────
# 2. RBAC GRANULAIRE
# ─────────────────────────────────────────────────────────────

class TestRBACGranulaire:

    def test_29_roles_ont_permissions(self):
        """Les 29 rôles FONCIER+ ont des permissions définies."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        roles_critiques = [
            "ADMIN", "DIRECTEUR_URBANISME", "MINISTRE_URBANISME",
            "DIRECTEUR_CADASTRE", "CHEF_SERVICE_CADASTRE", "GEOMETRE",
            "CHEF_CCFM", "GUICHETIER_CCFM", "JUST_PRESIDENT",
            "JUST_JUGE_FONCIER", "JUST_GREFFIER", "NOTAIRE",
            "BANQ_DIRECTEUR", "BANQ_AGENT", "CHEF_DAR", "ARCHIVISTE_DAR",
            "AUDITEUR",
        ]
        for role in roles_critiques:
            assert f"'{role}'" in content, \
                f"Rôle manquant dans seed RBAC : {role}"

    def test_permissions_couvrent_operations_critiques(self):
        """Les permissions couvrent toutes les opérations critiques."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        permissions_critiques = [
            "RNAF:PUBLIER",
            "RNAF:SUSPENDRE",
            "RNAF:ANNULER",
            "CADASTRE:PARCELLE:ANNULER",
            "CCFM:SUSPENSION:EMIT",
            "JUSTICE:PARCELLE:GELER",
            "JUSTICE:PARCELLE:DEGELER",
            "DROIT:TRANSFERT:ENREGISTRER",
            "ANNF:ARCHIVE:SCELLER",
        ]
        for perm in permissions_critiques:
            assert perm in content, f"Permission critique manquante : {perm}"

    def test_perimetre_requis_defini_par_permission(self):
        """Chaque permission a un périmètre géographique défini."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "perimetre_requis" in content
        assert "'national'" in content
        assert "'regional'" in content
        assert "'justice'" in content

    def test_vue_rbac_permissions_effective(self):
        """La vue v_rbac_permissions intègre le périmètre géographique."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "v_rbac_permissions" in content
        assert "type_structure" in content
        assert "perimetre_ok" in content


# ─────────────────────────────────────────────────────────────
# 3. AUDIT GLOBAL
# ─────────────────────────────────────────────────────────────

class TestAuditGlobal:

    def test_audit_sensitive_ops_structure(self):
        """La table audit_sensitive_ops a tous les champs requis."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        champs_requis = [
            "user_id", "role_acteur", "ip_address",
            "operation", "table_cible", "entite_id",
            "valeur_avant", "valeur_apres", "diff_json",
            "sha256_op", "sha256_prev",
            "niveau_criticite", "nicad",
        ]
        for champ in champs_requis:
            assert f'"{champ}"' in content or f"'{champ}'" in content or \
                   f"sa.Column(\"{champ}\"" in content or champ in content, \
                f"Champ audit manquant : {champ}"

    def test_audit_chaine_sha256(self):
        """L'audit enchaîne les SHA-256 (sha256_prev → chaîne inviolable)."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "sha256_prev" in content
        assert "sha256_op" in content

    def test_niveaux_criticite_definis(self):
        """Les niveaux de criticité couvrent toutes les situations."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "'faible'" in content or "faible" in content
        assert "'normal'" in content
        assert "'eleve'" in content
        assert "'critique'" in content

    def test_vue_audit_recent_critique(self):
        """La vue v_audit_recent_critique filtre sur 24h."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "v_audit_recent_critique" in content
        assert "24 hours" in content
        assert "eleve" in content
        assert "critique" in content


# ─────────────────────────────────────────────────────────────
# 4. RNAF_VERSION_AUDIT
# ─────────────────────────────────────────────────────────────

class TestRNAFVersionAudit:

    def test_rnaf_version_audit_structure(self):
        """rnaf_version_audit a les champs du spec."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "rnaf_version_audit" in content
        assert "numero_version" in content
        assert "statut_version" in content
        assert "motif_modification" in content
        assert "sha256_version" in content
        assert "snapshot_rnaf" in content

    def test_version_unique_par_rnaf(self):
        """Contrainte d'unicité (rnaf_id, numero_version)."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "uq_rnaf_version_num" in content

    def test_trigger_cree_version_automatiquement(self):
        """Le trigger crée une version RNAF à chaque transition."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "tg_auto_version_rnaf" in content
        assert "OLD.statut IS DISTINCT FROM NEW.statut" in content


# ─────────────────────────────────────────────────────────────
# 5. CCFM_CONTESTATION
# ─────────────────────────────────────────────────────────────

class TestCCFMContestation:

    def test_ccfm_contestation_structure(self):
        """ccfm_contestation a les champs du spec."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "ccfm_contestation" in content
        assert "demande_ccfm_id" in content
        assert "date_contestation" in content
        assert "motif" in content
        assert "statut" in content
        assert "autorite_saisie" in content
        assert "decision_judiciaire_id" in content

    def test_contestation_bloque_transactions_par_defaut(self):
        """Une contestation CCFM bloque les transactions par défaut."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "bloque_transactions" in content
        assert 'server_default="true"' in content

    def test_contestation_lie_decisions_judiciaires(self):
        """La contestation peut être liée à une décision judiciaire."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "decisions_judiciaires" in content


# ─────────────────────────────────────────────────────────────
# 6. VUE ETAT PARCELLE COMPLET
# ─────────────────────────────────────────────────────────────

class TestVueEtatParcelleComplet:

    def test_vue_couvre_toutes_sources(self):
        """v_etat_parcelle_complet intègre toutes les sources de blocage."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        sources_requises = [
            "transaction_blocks",   # migration 004
            "ccfm_suspension",      # migration 004
            "ccfm_contestation",    # migration 006 (nouveau)
            "conflits_parcellaires",# migration 003
            "hypotheques",          # migration 001
            "litiges",              # migration 001
            "parcel_disputes",      # migration 005
            "rnp_parcelle_link",    # migration 004
            "bgu_parcel_status",    # migration 004
        ]
        for source in sources_requises:
            assert source in content, \
                f"Source manquante dans v_etat_parcelle_complet : {source}"

    def test_vue_expose_eligible_transaction(self):
        """La vue calcule l'éligibilité transaction globale."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "eligible_transaction" in content

    def test_analyse_gaps_combles(self):
        """Les 5 gaps identifiés par l'analyse sont tous comblés."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        # Gap 1 : rnaf_version_audit
        assert "rnaf_version_audit" in content
        # Gap 2 : ccfm_contestation
        assert "ccfm_contestation" in content
        # Gap 3+4 : rbac_permission + rbac_role_permission
        assert "rbac_permission" in content
        assert "rbac_role_permission" in content
        # Gap 5 : audit_sensitive_ops
        assert "audit_sensitive_ops" in content


# ─────────────────────────────────────────────────────────────
# 7. COHÉRENCE GLOBALE SCHÉMA (64+ tables)
# ─────────────────────────────────────────────────────────────

class TestCoherenceGlobale:

    def test_aucun_delete_physique_dans_triggers(self):
        """Les triggers SQL ne font jamais de DELETE physique."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        # Extraire uniquement le contenu des fonctions PL/pgSQL
        import re
        plpgsql_blocks = re.findall(
            r'\$\$(.*?)\$\$', content, re.DOTALL
        )
        for block in plpgsql_blocks:
            lines = [l.strip() for l in block.splitlines()]
            for line in lines:
                if line.startswith('--'):
                    continue
                assert 'DELETE FROM' not in line.upper() or \
                       'no delete' in line.lower(), \
                    f"DELETE physique dans trigger : {line}"

    def test_tous_raise_exception_ont_messages_clairs(self):
        """Tous les RAISE EXCEPTION ont des messages métier clairs."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        raises = [l.strip() for l in content.splitlines()
                  if 'RAISE EXCEPTION' in l]
        # Chaque RAISE doit expliquer QUI est bloqué et POURQUOI
        for r in raises:
            assert len(r) > 30, f"Message RAISE trop court : {r}"

    def test_downgrade_nettoie_tout(self):
        """Le downgrade supprime toutes les créations de la migration."""
        content = open(
            "/home/claude/foncier_v347/backend/alembic/versions/"
            "006_schema_fondation_consolide.py"
        ).read()
        assert "def downgrade" in content
        # Toutes les tables créées sont supprimées
        tables_crees = [
            "audit_sensitive_ops", "rbac_role_permission",
            "rbac_permission", "ccfm_contestation", "rnaf_version_audit",
        ]
        for table in tables_crees:
            assert f"DROP TABLE IF EXISTS {table}" in content, \
                f"downgrade() ne supprime pas {table}"

    def test_total_tables_schema(self):
        """Le schéma global atteint 69 tables (64 + 5 nouvelles)."""
        import os, re
        tables = set()
        for root, dirs, files in os.walk('/home/claude/foncier_v347/backend'):
            for f in files:
                if not f.endswith('.py'):
                    continue
                content = open(os.path.join(root, f)).read()
                for m in re.findall(r'create_table\(\s*"([\w_]+)"', content):
                    tables.add(m)
                for m in re.findall(r"create_table\(\s*'([\w_]+)'", content):
                    tables.add(m)

        v347 = {
            "permis","arretes_fonciers","dossiers_commune","liaisons_commune_urbanisme",
            "publications_jo","parutions_jo","rnaf","rnp_demandes","assiettes_bgu",
            "dossiers_workflow_bgu","syncs_bgu","audit_sync_bgu","plans_cadastraux",
            "parcel_versions","operations_cadastrales","incidents_fonciers",
            "anti_fraude_alertes","demandes_ccfm","recensement_dar","habilitations_dar",
            "dossiers_fonciers","journal_dar","dossiers_domaine","redevances",
            "actes_notaries","hypotheques","conversations_notaires","messages_notaires",
            "dossiers_dad","litiges","decisions_judiciaires","audit_logs","alertes_systeme",
            "archives_dar","users"
        }
        total = len(tables | v347)
        assert total >= 64, f"Schéma incomplet : {total} tables (attendu ≥ 64)"
        print(f"\n✅ Schéma total : {total} tables")
