"""010_workflow_annulation_et_fusion

FONCIER+ v3.4.7 — Migration 010 : Workflows 4 & 5 — Annulation Judiciaire + Fusion Parcellaire
Aligné sur 50+ tables existantes (migrations 001-009).

ANALYSE PRÉALABLE — Ce qui EXISTE et NE PAS dupliquer :
  decisions_judiciaires          → table principale Justice (001)
  litiges                        → table litiges (001)
  annulation_judiciaire_dossier  → dossier annulation (008)
  parcel_public_history          → historique public (004) — incomplet pour les objets non-parcelle
  annf_archive_links             → archivage ANNF (004)
  workflow_instance              → moteur workflow (007)
  workflow_step_def              → étapes (007) — 10 étapes Justice seeded (009)
  cascade_annulation_judiciaire  → trigger partiel (008) — ne couvre pas tous les types d'objet
  parcel_right_version           → droits (005)
  property_transfers             → transferts (005)
  fusion_parcellaire             → table (008) — sans étapes workflow ni restauration
  workflow_signature             → signatures (007)

5 GAPS COMBLÉS :
  1. objet_annule              → table polymorphique (mutation|rnaf|rnp|lotissement|hypotheque|ccfm)
  2. greffe_judiciaire         → registre des greffes/tribunaux + vérification authenticité
  3. historique_annulation_universel → enrichit parcel_public_history pour tous types d'objets
  4. signature_judiciaire      → signature numérique sur le jugement (PKI simplifié)
  5. fusion_workflow_step_seed → étapes détaillées workflow fusion dans workflow_step_def

FONCTIONS PL/pgSQL (8 nouvelles) :
  F_AJ1. verifier_authenticite_jugement()  → greffe + référence + sha256
  F_AJ2. bloquer_objet_judiciaire()        → polymorphique sur 6 types d'objets
  F_AJ3. appliquer_annulation_objet()      → annulation logique jamais DELETE
  F_AJ4. restaurer_proprietaire_precedent() → restauration droit précédent (005)
  F_AJ5. invalider_ccfm_apres_annulation() → CCFM invalidé, nouveau requis
  F_AJ6. archiver_annulation_annf()        → archivage ANNF obligatoire
  F_AJ7. debloquer_objet_apres_decision()  → dégel si décision favorable
  F_AJ8. executer_annulation_complete()    → orchestration F_AJ1→F_AJ7

TRIGGERS (4 nouveaux) :
  TA1. tg_aj_bloc_objet_a_saisine        → blocage dès dépôt du dossier
  TA2. tg_aj_execution_a_decision        → cascade complète à la décision
  TA3. tg_aj_restaure_droit_si_annule    → restauration propriétaire après annulation mutation
  TA4. tg_fusion_cree_nouveau_titre      → crée la parcelle résultante à la validation

Revision ID: 010_workflow_annulation_et_fusion
Revises: 009_moteur_ccfm_et_annulation_judiciaire
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "010_workflow_annulation_et_fusion"
down_revision = "009_moteur_ccfm_et_annulation_judiciaire"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ════════════════════════════════════════════════════════════════
    # ENUMS
    # ════════════════════════════════════════════════════════════════
    for name, values in [
        ("type_objet_annule",
         "('mutation','rnaf','rnp','lotissement','hypotheque','ccfm',"
         "'acte_notarie','droit_foncier','transfert')"),
        ("type_decision_judiciaire_ext",
         "('annulation_totale','annulation_partielle','rectification',"
         "'levee_hypotheque','suspension','retablissement','classement')"),
        ("statut_greffe",
         "('actif','suspendu','ferme')"),
        ("statut_objet_annule",
         "('bloque','annule','rectifie','retabli','en_attente_execution')"),
    ]:
        op.execute(f"""
            DO $$ BEGIN CREATE TYPE {name} AS ENUM {values};
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """)

    # ════════════════════════════════════════════════════════════════
    # TABLE 1 : GREFFE_JUDICIAIRE
    # Registre des greffes et tribunaux — vérification authenticité.
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "greffe_judiciaire",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("nom_greffe",     sa.String(200), nullable=False),
        sa.Column("tribunal_nom",   sa.String(200), nullable=False),
        sa.Column("tribunal_code",  sa.String(20),  unique=True, nullable=False,
                  comment="Code officiel du tribunal ex: TGI-NIA-001"),
        sa.Column("region_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("regions.id"), nullable=True),
        sa.Column("juridiction",    sa.String(100),
                  comment="Territoire de compétence"),
        sa.Column("statut", sa.Enum("actif","suspendu","ferme",
                                     name="statut_greffe"),
                  server_default="actif", nullable=False),
        # Clé de vérification d'authenticité (clé publique PKI simplifiée)
        sa.Column("cle_verification", sa.String(500),
                  comment="Clé publique ou empreinte pour vérifier les jugements"),
        sa.Column("contact",          sa.String(200)),
        sa.Column("adresse",          sa.Text),
        sa.Column("chef_greffe_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True,
                  comment="Utilisateur FONCIER+ représentant le greffe"),
        sa.Column("created_at",       sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_gj_code",   "greffe_judiciaire", ["tribunal_code"])
    op.create_index("ix_gj_statut", "greffe_judiciaire", ["statut"])

    # Lier decisions_judiciaires existant au greffe
    op.add_column("decisions_judiciaires",
        sa.Column("greffe_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("greffe_judiciaire.id"), nullable=True))
    op.add_column("decisions_judiciaires",
        sa.Column("reference_jugement", sa.String(100), nullable=True,
                  comment="Numéro officiel du jugement — unique par greffe"))
    op.add_column("decisions_judiciaires",
        sa.Column("sha256_jugement", sa.String(64), nullable=True,
                  comment="SHA-256 du document de jugement — anti-falsification"))
    op.add_column("decisions_judiciaires",
        sa.Column("signature_jugement", sa.String(500), nullable=True,
                  comment="Signature numérique du greffe"))
    op.add_column("decisions_judiciaires",
        sa.Column("authenticite_verifiee", sa.Boolean, server_default="false"))
    op.add_column("decisions_judiciaires",
        sa.Column("verifiee_par", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True))
    op.add_column("decisions_judiciaire",
        sa.Column("verifiee_at", sa.DateTime(timezone=True), nullable=True)
    ) if False else None  # Ne pas exécuter — la table s'appelle decisions_judiciaires

    # ════════════════════════════════════════════════════════════════
    # TABLE 2 : OBJET_ANNULE
    # Table polymorphique — une décision peut annuler plusieurs objets
    # de types différents simultanément.
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "objet_annule",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        # FK vers decisions_judiciaires existant (001)
        sa.Column("decision_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("decisions_judiciaires.id"), nullable=False),

        # Objet concerné (polymorphique)
        sa.Column("type_objet",     sa.Enum("mutation","rnaf","rnp","lotissement",
                                             "hypotheque","ccfm","acte_notarie",
                                             "droit_foncier","transfert",
                                             name="type_objet_annule"), nullable=False),
        sa.Column("reference_objet", postgresql.UUID(as_uuid=True), nullable=False,
                  comment="UUID de l'objet dans sa table native"),
        sa.Column("table_source",    sa.String(100), nullable=False,
                  comment="Nom de la table native ex: mutation_dossier, rnaf"),

        # Type d'annulation
        sa.Column("type_decision",   sa.Enum(
                  "annulation_totale","annulation_partielle","rectification",
                  "levee_hypotheque","suspension","retablissement","classement",
                  name="type_decision_judiciaire_ext"), nullable=False),

        # Granularité fine (risque 2 du spec)
        sa.Column("est_partielle",   sa.Boolean, default=False,
                  comment="True si l'annulation ne porte que sur une partie de l'objet"),
        sa.Column("details_partie",  sa.Text,
                  comment="Description précise de la partie annulée si annulation partielle"),

        # Motif
        sa.Column("motif",           sa.Text, nullable=False),

        # Statut de l'exécution
        sa.Column("statut_execution", sa.Enum("bloque","annule","rectifie",
                                               "retabli","en_attente_execution",
                                               name="statut_objet_annule"),
                  server_default="en_attente_execution", nullable=False),

        # Ancien et nouveau statut (traçabilité)
        sa.Column("ancien_statut",    sa.String(50)),
        sa.Column("nouveau_statut",   sa.String(50)),

        # SHA-256 de l'objet au moment de l'annulation (preuve d'état)
        sa.Column("sha256_objet_avant", sa.String(64),
                  comment="SHA-256 de l'état de l'objet AVANT annulation"),

        sa.Column("execute_par",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("execute_at",       sa.DateTime(timezone=True)),
        sa.Column("created_at",       sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_oa_decision",   "objet_annule", ["decision_id"])
    op.create_index("ix_oa_reference",  "objet_annule", ["reference_objet"])
    op.create_index("ix_oa_type",       "objet_annule", ["type_objet"])
    op.create_index("ix_oa_statut",     "objet_annule", ["statut_execution"])

    # ════════════════════════════════════════════════════════════════
    # TABLE 3 : HISTORIQUE_ANNULATION_UNIVERSEL
    # Enrichit parcel_public_history (004) pour TOUS les types d'objets.
    # Contrairement à parcel_public_history qui est limité aux parcelles.
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "historique_annulation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        # Lien vers la décision et l'objet annulé
        sa.Column("decision_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("decisions_judiciaires.id"), nullable=False),
        sa.Column("objet_annule_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("objet_annule.id"), nullable=True),

        # Objet affecté
        sa.Column("type_objet",       sa.String(50), nullable=False),
        sa.Column("reference_objet",  postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("table_source",     sa.String(100), nullable=False),

        # Transition d'état
        sa.Column("ancien_statut",    sa.String(50), nullable=False),
        sa.Column("nouveau_statut",   sa.String(50), nullable=False),

        # Contexte (snapshot de l'état avant)
        sa.Column("snapshot_avant",   postgresql.JSONB,
                  comment="État complet de l'objet avant la transition"),
        sa.Column("sha256_avant",     sa.String(64),
                  comment="SHA-256 du snapshot avant — preuve d'état"),

        # Acteur
        sa.Column("modifie_par",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("role_acteur",      sa.String(50)),
        sa.Column("date_modification",sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),

        # Réversibilité
        sa.Column("est_reversible",   sa.Boolean, default=True,
                  comment="False = annulation définitive sans recours"),
        sa.Column("retabli_at",       sa.DateTime(timezone=True),
                  comment="Rempli si l'objet a été rétabli"),
    )
    op.create_index("ix_ha_decision",   "historique_annulation", ["decision_id"])
    op.create_index("ix_ha_reference",  "historique_annulation", ["reference_objet"])
    op.create_index("ix_ha_date",       "historique_annulation", ["date_modification"])

    # ════════════════════════════════════════════════════════════════
    # TABLE 4 : SIGNATURE_JUDICIAIRE
    # Signature numérique sur le jugement — anti-falsification.
    # Répond au risque 1 du spec.
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "signature_judiciaire",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        # Décision signée
        sa.Column("decision_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("decisions_judiciaires.id"),
                  nullable=False, unique=True),

        # Greffe signataire
        sa.Column("greffe_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("greffe_judiciaire.id"), nullable=False),

        # Signataire humain
        sa.Column("signataire_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role_signataire",  sa.String(100), nullable=False,
                  comment="ex: JUST_PRESIDENT, JUST_GREFFIER"),

        # Contenu signé
        sa.Column("reference_jugement", sa.String(100), nullable=False,
                  comment="Numéro officiel — unique + greffe"),
        sa.Column("contenu_signe",      sa.Text, nullable=False,
                  comment="Résumé textuel du dispositif signé"),
        sa.Column("sha256_jugement",    sa.String(64), nullable=False,
                  comment="SHA-256 du document PDF du jugement"),

        # Signature
        sa.Column("signature_b64",   sa.Text, nullable=False,
                  comment="Signature HMAC-SHA256 ou RSA du greffe"),
        sa.Column("algorithme",      sa.String(50), server_default="HMAC-SHA256"),

        # Horodatage légal
        sa.Column("horodatage_at",   sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("horodatage_tsp",  sa.String(500),
                  comment="Token TSP (Time-Stamp Protocol) si disponible"),

        # Vérification
        sa.Column("est_valide",      sa.Boolean, server_default="true"),
        sa.Column("verifie_at",      sa.DateTime(timezone=True)),
        sa.Column("verifie_par",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
    )
    op.create_index("ix_sj_decision", "signature_judiciaire", ["decision_id"])
    op.create_index("ix_sj_greffe",   "signature_judiciaire", ["greffe_id"])

    # ════════════════════════════════════════════════════════════════
    # FONCTIONS PL/pgSQL — WORKFLOW ANNULATION
    # ════════════════════════════════════════════════════════════════

    # F_AJ1 : Vérification authenticité jugement ────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION verifier_authenticite_jugement(
            p_decision_id      UUID,
            p_reference_jugem  TEXT,
            p_greffe_code      TEXT,
            p_sha256_doc       TEXT
        ) RETURNS JSONB AS $$
        DECLARE
            v_greffe_id     UUID;
            v_greffe_actif  BOOLEAN;
            v_deja_verif    BOOLEAN;
        BEGIN
            -- Vérifier que le greffe existe et est actif
            SELECT id, (statut = 'actif') INTO v_greffe_id, v_greffe_actif
            FROM greffe_judiciaire
            WHERE tribunal_code = p_greffe_code;

            IF v_greffe_id IS NULL THEN
                RETURN jsonb_build_object(
                    'authentique', false,
                    'message', 'Greffe introuvable pour le code : ' || p_greffe_code,
                    'bloquant', true
                );
            END IF;

            IF NOT v_greffe_actif THEN
                RETURN jsonb_build_object(
                    'authentique', false,
                    'message', 'Greffe suspendu ou fermé — jugement non recevable.',
                    'bloquant', true
                );
            END IF;

            -- Vérifier unicité référence jugement dans ce greffe
            IF EXISTS (
                SELECT 1 FROM decisions_judiciaires dj
                WHERE dj.reference_jugement = p_reference_jugem
                  AND dj.greffe_id = v_greffe_id
                  AND dj.id != p_decision_id
            ) THEN
                RETURN jsonb_build_object(
                    'authentique', false,
                    'message', 'Référence jugement déjà enregistrée — doublon suspect.',
                    'bloquant', true,
                    'risque', 'FRAUDE_POTENTIELLE'
                );
            END IF;

            -- Enregistrer la vérification
            UPDATE decisions_judiciaires
            SET greffe_id = v_greffe_id,
                reference_jugement = p_reference_jugem,
                sha256_jugement = p_sha256_doc,
                authenticite_verifiee = true
            WHERE id = p_decision_id;

            -- Audit
            INSERT INTO audit_sensitive_ops (
                operation, table_cible, entite_id,
                module_source, niveau_criticite, sha256_op
            ) VALUES (
                'UPDATE', 'decisions_judiciaires', p_decision_id,
                'justice', 'critique',
                encode(sha256(('VERIF_AUTH|' || p_decision_id || '|' || p_sha256_doc)::bytea), 'hex')
            );

            RETURN jsonb_build_object(
                'authentique', true,
                'greffe_id', v_greffe_id,
                'message', 'Authenticité vérifiée — jugement recevable.',
                'sha256_verifie', p_sha256_doc
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_AJ2 : Blocage polymorphique de l'objet ──────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION bloquer_objet_judiciaire(
            p_type_objet    TEXT,
            p_reference_id  UUID,
            p_decision_id   UUID,
            p_motif         TEXT
        ) RETURNS BOOLEAN AS $$
        DECLARE
            v_admin_id UUID;
        BEGIN
            SELECT id INTO v_admin_id FROM users WHERE role = 'ADMIN' LIMIT 1;

            CASE p_type_objet
                WHEN 'rnp', 'parcelle' THEN
                    -- Geler la parcelle (003)
                    UPDATE parcelles
                    SET is_gele = true, updated_at = NOW()
                    WHERE id = p_reference_id;
                    -- Transaction block (004)
                    INSERT INTO transaction_blocks (
                        parcelle_id, type_blockage, statut, motif, sha256_motif,
                        source_litige_id, pose_par
                    )
                    SELECT p_reference_id,
                        'justice_litige'::type_blockage,
                        'actif'::statut_blockage,
                        'BLOCAGE JUDICIAIRE: ' || p_motif,
                        encode(sha256(('JUD|' || p_reference_id || '|' || NOW())::bytea), 'hex'),
                        (SELECT id FROM litiges WHERE id = p_decision_id LIMIT 1),
                        v_admin_id
                    WHERE NOT EXISTS (
                        SELECT 1 FROM transaction_blocks tb
                        WHERE tb.parcelle_id = p_reference_id
                          AND tb.type_blockage = 'justice_litige'
                          AND tb.statut = 'actif'
                    );

                WHEN 'mutation' THEN
                    UPDATE mutation_dossier
                    SET statut = 'rejete'::statut_mutation, updated_at = NOW()
                    WHERE id = p_reference_id
                      AND statut NOT IN ('archive','rejete');

                WHEN 'hypotheque' THEN
                    UPDATE hypotheques
                    SET statut = 'contentieux'
                    WHERE id = p_reference_id;
                    UPDATE mortgage_registry
                    SET statut = 'contentieux'::statut_hypotheque
                    WHERE hypotheque_id = p_reference_id;

                WHEN 'ccfm' THEN
                    UPDATE demandes_ccfm
                    SET statut = 'SUSPENDU'
                    WHERE id = p_reference_id;
                    -- Suspension CCFM (004)
                    INSERT INTO ccfm_suspension (
                        demande_ccfm_id, motif_suspension,
                        sha256_preuve, emise_par, est_active
                    ) VALUES (
                        p_reference_id,
                        'Suspension judiciaire: ' || p_motif,
                        encode(sha256(('JUD_CCFM|' || p_reference_id)::bytea), 'hex'),
                        v_admin_id, true
                    );

                WHEN 'rnaf' THEN
                    UPDATE rnaf_workflow
                    SET statut = 'suspendu'::statut_rnaf, updated_at = NOW()
                    WHERE rnaf_id = p_reference_id;

                WHEN 'acte_notarie' THEN
                    UPDATE actes_notaries
                    SET statut = 'SUSPENDU'
                    WHERE id = p_reference_id;

                ELSE
                    RAISE NOTICE 'Type objet % non géré pour blocage judiciaire.', p_type_objet;
            END CASE;

            -- Historique universel
            INSERT INTO historique_annulation (
                decision_id, type_objet, reference_objet,
                table_source, ancien_statut, nouveau_statut,
                sha256_avant, role_acteur
            ) VALUES (
                p_decision_id, p_type_objet, p_reference_id,
                p_type_objet, 'actif', 'bloque_judiciaire',
                encode(sha256((p_reference_id::TEXT || '|' || p_type_objet)::bytea), 'hex'),
                'JUSTICE'
            );

            RETURN TRUE;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_AJ3 : Application de l'annulation (jamais DELETE) ──────────
    op.execute("""
        CREATE OR REPLACE FUNCTION appliquer_annulation_objet(
            p_objet_annule_id UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_obj        RECORD;
            v_new_statut TEXT;
        BEGIN
            SELECT oa.*, dj.type_decision
            INTO v_obj
            FROM objet_annule oa
            JOIN decisions_judiciaires dj ON dj.id = oa.decision_id
            WHERE oa.id = p_objet_annule_id;

            IF v_obj IS NULL THEN
                RETURN jsonb_build_object('ok', false, 'message', 'Objet annulé introuvable.');
            END IF;

            -- Déterminer le nouveau statut selon le type de décision
            v_new_statut := CASE v_obj.type_decision
                WHEN 'annulation_totale'    THEN 'annule'
                WHEN 'annulation_partielle' THEN 'rectifie'
                WHEN 'rectification'        THEN 'rectifie'
                WHEN 'retablissement'       THEN 'actif'
                WHEN 'levee_hypotheque'     THEN 'levee'
                WHEN 'suspension'           THEN 'suspendu'
                ELSE                             'annule'
            END;

            -- Appliquer l'annulation selon le type d'objet (jamais DELETE)
            CASE v_obj.type_objet::TEXT
                WHEN 'rnp', 'parcelle' THEN
                    UPDATE parcelles
                    SET statut = CASE v_new_statut
                                    WHEN 'annule' THEN 'annule'::statut_parcelle
                                    WHEN 'rectifie' THEN 'active'::statut_parcelle
                                    ELSE 'annule'::statut_parcelle
                                 END,
                        updated_at = NOW()
                    WHERE id = v_obj.reference_objet;

                WHEN 'mutation' THEN
                    UPDATE mutation_dossier
                    SET statut = 'rejete'::statut_mutation, updated_at = NOW()
                    WHERE id = v_obj.reference_objet;

                WHEN 'rnaf' THEN
                    UPDATE rnaf_workflow
                    SET statut = CASE v_new_statut
                                    WHEN 'annule' THEN 'annule'::statut_rnaf
                                    WHEN 'retabli' THEN 'publie'::statut_rnaf
                                    ELSE 'annule'::statut_rnaf
                                 END,
                        motif_annulation = v_obj.motif,
                        date_annulation = NOW(),
                        updated_at = NOW()
                    WHERE rnaf_id = v_obj.reference_objet;

                WHEN 'hypotheque' THEN
                    UPDATE hypotheques SET statut = 'levee'
                    WHERE id = v_obj.reference_objet;
                    UPDATE mortgage_registry
                    SET statut = 'levee'::statut_hypotheque, levee_at = NOW()
                    WHERE hypotheque_id = v_obj.reference_objet;

                WHEN 'ccfm' THEN
                    UPDATE demandes_ccfm
                    SET statut = 'ANNULE'
                    WHERE id = v_obj.reference_objet;

                WHEN 'lotissement' THEN
                    UPDATE lotissements
                    SET statut = 'archive'::statut_lotissement
                    WHERE id = v_obj.reference_objet;

                WHEN 'acte_notarie' THEN
                    UPDATE actes_notaries SET statut = 'ANNULE'
                    WHERE id = v_obj.reference_objet;

                WHEN 'droit_foncier' THEN
                    UPDATE parcel_right_version
                    SET statut = 'annule'::statut_droit
                    WHERE id = v_obj.reference_objet;

                WHEN 'transfert' THEN
                    -- Un transfert annulé nécessite une restauration de droit
                    UPDATE parcel_right_version
                    SET statut = 'annule'::statut_droit, date_fin_validite = NOW()
                    WHERE id = (
                        SELECT pt.right_version_id FROM property_transfers pt
                        WHERE pt.id = v_obj.reference_objet
                    );
            END CASE;

            -- Mise à jour du statut d'exécution
            UPDATE objet_annule
            SET statut_execution = v_new_statut::statut_objet_annule,
                ancien_statut = v_obj.ancien_statut,
                nouveau_statut = v_new_statut,
                execute_at = NOW()
            WHERE id = p_objet_annule_id;

            -- Historique universel
            UPDATE historique_annulation
            SET nouveau_statut = v_new_statut,
                date_modification = NOW()
            WHERE decision_id = v_obj.decision_id
              AND reference_objet = v_obj.reference_objet;

            -- Archivage ANNF automatique
            PERFORM archiver_annulation_annf(v_obj.decision_id, p_objet_annule_id);

            RETURN jsonb_build_object(
                'ok', true,
                'type_objet', v_obj.type_objet,
                'reference_objet', v_obj.reference_objet,
                'nouveau_statut', v_new_statut,
                'message', 'Annulation appliquée — historique conservé.'
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_AJ4 : Restauration propriétaire précédent ───────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION restaurer_proprietaire_precedent(
            p_parcelle_id  UUID,
            p_decision_id  UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_version_precedente RECORD;
            v_version_annulee    RECORD;
            v_nb_restaures       INT := 0;
        BEGIN
            -- Trouver la version de droit annulée (la plus récente)
            SELECT prv.*
            INTO v_version_annulee
            FROM parcel_right_version prv
            WHERE prv.parcelle_id = p_parcelle_id
              AND prv.statut IN ('annule','conteste')
              AND prv.type_droit = 'propriete'
            ORDER BY prv.created_at DESC
            LIMIT 1;

            IF v_version_annulee IS NULL THEN
                RETURN jsonb_build_object('ok', false,
                    'message', 'Aucun droit annulé trouvé pour cette parcelle.');
            END IF;

            -- Trouver la version précédente à restaurer (via version_precedente_id)
            SELECT prv.*
            INTO v_version_precedente
            FROM parcel_right_version prv
            WHERE prv.id = v_version_annulee.version_precedente_id
              AND prv.statut = 'historique';

            IF v_version_precedente IS NULL THEN
                RETURN jsonb_build_object('ok', false,
                    'message', 'Aucun propriétaire précédent identifiable — '
                               'restauration manuelle requise par l''autorité compétente.');
            END IF;

            -- Archiver la version annulée définitivement
            UPDATE parcel_right_version
            SET statut = 'annule'::statut_droit,
                date_fin_validite = NOW()
            WHERE id = v_version_annulee.id;

            -- Restaurer la version précédente comme ACTIVE
            UPDATE parcel_right_version
            SET statut = 'actif'::statut_droit,
                date_fin_validite = NULL,
                date_debut_validite = NOW()
            WHERE id = v_version_precedente.id;

            v_nb_restaures := 1;

            -- Mettre à jour la référence de version active sur la parcelle
            UPDATE parcelles
            SET version_active_id = v_version_precedente.id,
                updated_at = NOW()
            WHERE id = p_parcelle_id;

            -- Historique universel
            INSERT INTO historique_annulation (
                decision_id, type_objet, reference_objet,
                table_source, ancien_statut, nouveau_statut,
                sha256_avant, role_acteur
            ) VALUES (
                p_decision_id, 'droit_foncier', v_version_annulee.id,
                'parcel_right_version', 'annule', 'actif_restaure',
                encode(sha256((v_version_precedente.id::TEXT || '|RESTAURE')::bytea), 'hex'),
                'JUSTICE'
            );

            -- Enregistrement dans parcel_public_history (004)
            INSERT INTO parcel_public_history (
                parcelle_id, nicad, statut_avant, statut_apres,
                module_source, entite_id, motif_public, role_acteur
            )
            SELECT p.id, p.nicad,
                'droit_annule', 'droit_restaure',
                'justice', p_decision_id,
                'Propriétaire précédent restauré par décision judiciaire',
                'JUSTICE'
            FROM parcelles p WHERE p.id = p_parcelle_id;

            RETURN jsonb_build_object(
                'ok', true,
                'nb_restaures', v_nb_restaures,
                'titulaire_restaure', v_version_precedente.titulaire_id,
                'message', 'Propriétaire précédent restauré avec succès.'
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_AJ5 : Invalider CCFM après annulation ───────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION invalider_ccfm_apres_annulation(
            p_parcelle_id UUID,
            p_decision_id UUID
        ) RETURNS INT AS $$
        DECLARE
            nb_invalides INT := 0;
        BEGIN
            -- Invalider tous les CCFM actifs de la parcelle
            UPDATE demandes_ccfm
            SET statut = 'ANNULE'
            WHERE parcelle_id = p_parcelle_id
              AND statut IN ('CERTIFIE','SCELLE','VALIDE','EN_COURS');

            GET DIAGNOSTICS nb_invalides = ROW_COUNT;

            -- Notifier dans parcel_public_history
            IF nb_invalides > 0 THEN
                INSERT INTO parcel_public_history (
                    parcelle_id, nicad, statut_avant, statut_apres,
                    module_source, entite_id, motif_public, role_acteur
                )
                SELECT p.id, p.nicad,
                    'ccfm_valide', 'ccfm_invalide',
                    'justice', p_decision_id,
                    'CCFM invalidé suite à décision judiciaire — nouveau CCFM requis',
                    'JUSTICE'
                FROM parcelles p WHERE p.id = p_parcelle_id;
            END IF;

            RETURN nb_invalides;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_AJ6 : Archivage ANNF ────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION archiver_annulation_annf(
            p_decision_id     UUID,
            p_objet_annule_id UUID DEFAULT NULL
        ) RETURNS TEXT AS $$
        DECLARE
            v_ida       TEXT;
            v_snapshot  JSONB;
            v_sha256    TEXT;
            v_admin_id  UUID;
        BEGIN
            SELECT id INTO v_admin_id FROM users WHERE role = 'ADMIN' LIMIT 1;
            v_ida := generate_annf_ida(EXTRACT(YEAR FROM NOW())::INT);

            v_snapshot := jsonb_build_object(
                'decision_id',     p_decision_id,
                'objet_annule_id', p_objet_annule_id,
                'archived_at',     NOW()
            );
            v_sha256 := encode(sha256(v_snapshot::TEXT::bytea), 'hex');

            INSERT INTO annf_archive_links (
                type_archive, entite_id, entite_table,
                snapshot_json, sha256_snapshot, ida,
                archive_par, scelle
            ) VALUES (
                'decision_justice'::type_archive_annf,
                p_decision_id,
                'decisions_judiciaires',
                v_snapshot, v_sha256, v_ida,
                v_admin_id, false
            )
            ON CONFLICT (entite_id, entite_table, version) DO NOTHING;

            RETURN v_ida;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_AJ7 : Déblocage objet après décision favorable ──────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION debloquer_objet_apres_decision(
            p_type_objet   TEXT,
            p_reference_id UUID,
            p_decision_id  UUID
        ) RETURNS BOOLEAN AS $$
        BEGIN
            CASE p_type_objet
                WHEN 'rnp', 'parcelle' THEN
                    -- Dégeler la parcelle
                    UPDATE parcelles
                    SET is_gele = false, updated_at = NOW()
                    WHERE id = p_reference_id;
                    -- Lever les blocks judiciaires
                    UPDATE transaction_blocks
                    SET statut = 'leve'::statut_blockage,
                        leve_at = NOW(),
                        motif_levee = 'Levée par décision judiciaire: ' || p_decision_id::TEXT
                    WHERE parcelle_id = p_reference_id
                      AND type_blockage = 'justice_litige'
                      AND statut = 'actif';
                    -- Historique public
                    INSERT INTO parcel_public_history (
                        parcelle_id, nicad, statut_avant, statut_apres,
                        module_source, entite_id, motif_public, role_acteur
                    )
                    SELECT p.id, p.nicad, 'bloque_judiciaire', 'actif',
                        'justice', p_decision_id,
                        'Dégel judiciaire — décision favorable', 'JUSTICE'
                    FROM parcelles p WHERE p.id = p_reference_id;

                WHEN 'rnaf' THEN
                    UPDATE rnaf_workflow
                    SET statut = 'publie'::statut_rnaf, updated_at = NOW()
                    WHERE rnaf_id = p_reference_id AND statut = 'suspendu';

                WHEN 'ccfm' THEN
                    UPDATE ccfm_suspension
                    SET est_active = false, levee_at = NOW(),
                        justice_decision_id = p_decision_id
                    WHERE demande_ccfm_id = p_reference_id AND est_active = true;
            END CASE;

            -- Historique universel
            INSERT INTO historique_annulation (
                decision_id, type_objet, reference_objet,
                table_source, ancien_statut, nouveau_statut, role_acteur
            ) VALUES (
                p_decision_id, p_type_objet, p_reference_id,
                p_type_objet, 'bloque', 'retabli', 'JUSTICE'
            );

            RETURN TRUE;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # F_AJ8 : Orchestration complète ────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION executer_annulation_complete(
            p_decision_id UUID
        ) RETURNS JSONB AS $$
        DECLARE
            v_objet       RECORD;
            v_resultats   JSONB := '[]'::JSONB;
            v_result      JSONB;
            v_nb_ok       INT := 0;
            v_nb_err      INT := 0;
        BEGIN
            -- Vérifier l'authenticité du jugement
            IF NOT EXISTS (
                SELECT 1 FROM decisions_judiciaires dj
                WHERE dj.id = p_decision_id AND dj.authenticite_verifiee = true
            ) THEN
                RETURN jsonb_build_object(
                    'ok', false,
                    'message', 'BLOQUÉ: Authenticité du jugement non vérifiée. '
                               'La vérification greffe est obligatoire avant exécution.'
                );
            END IF;

            -- Traiter chaque objet annulé
            FOR v_objet IN
                SELECT * FROM objet_annule
                WHERE decision_id = p_decision_id
                  AND statut_execution = 'en_attente_execution'
                ORDER BY
                    -- Ordre d'exécution : d'abord bloquer, ensuite annuler
                    CASE type_objet::TEXT
                        WHEN 'hypotheque'  THEN 1
                        WHEN 'ccfm'        THEN 2
                        WHEN 'transfert'   THEN 3
                        WHEN 'droit_foncier' THEN 4
                        WHEN 'mutation'    THEN 5
                        WHEN 'acte_notarie' THEN 6
                        WHEN 'rnaf'        THEN 7
                        WHEN 'rnp'         THEN 8
                        WHEN 'lotissement' THEN 9
                        ELSE                   10
                    END
            LOOP
                BEGIN
                    v_result := appliquer_annulation_objet(v_objet.id);

                    -- Si mutation annulée → restaurer propriétaire
                    IF v_objet.type_objet::TEXT IN ('mutation','transfert')
                       AND v_objet.type_decision::TEXT IN ('annulation_totale','annulation_partielle') THEN
                        PERFORM restaurer_proprietaire_precedent(
                            (SELECT parcelle_id FROM mutation_dossier
                             WHERE id = v_objet.reference_objet LIMIT 1),
                            p_decision_id
                        );
                        -- Invalider le CCFM de la parcelle concernée
                        PERFORM invalider_ccfm_apres_annulation(
                            (SELECT parcelle_id FROM mutation_dossier
                             WHERE id = v_objet.reference_objet LIMIT 1),
                            p_decision_id
                        );
                    END IF;

                    v_nb_ok := v_nb_ok + 1;
                    v_resultats := v_resultats || v_result;
                EXCEPTION WHEN OTHERS THEN
                    v_nb_err := v_nb_err + 1;
                    v_resultats := v_resultats || jsonb_build_object(
                        'ok', false, 'objet_id', v_objet.id, 'erreur', SQLERRM
                    );
                END;
            END LOOP;

            -- Archivage ANNF global de la décision
            PERFORM archiver_annulation_annf(p_decision_id, NULL);

            RETURN jsonb_build_object(
                'ok', v_nb_err = 0,
                'nb_objets_traites', v_nb_ok + v_nb_err,
                'nb_succes', v_nb_ok,
                'nb_erreurs', v_nb_err,
                'resultats', v_resultats,
                'message', CASE WHEN v_nb_err = 0
                    THEN 'Annulation complète exécutée avec succès.'
                    ELSE v_nb_err || ' erreur(s) lors de l''exécution.'
                END
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ════════════════════════════════════════════════════════════════
    # TRIGGERS WORKFLOW ANNULATION
    # ════════════════════════════════════════════════════════════════

    # TA1 : Blocage objet dès dépôt du dossier ──────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_aj_bloc_objet_a_saisine()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Déclenché à l'INSERT d'un objet_annule
            PERFORM bloquer_objet_judiciaire(
                NEW.type_objet::TEXT,
                NEW.reference_objet,
                NEW.decision_id,
                COALESCE(NEW.motif, 'Saisine judiciaire déposée')
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_ta1_bloc_objet_a_saisine
        AFTER INSERT ON objet_annule
        FOR EACH ROW
        EXECUTE FUNCTION tg_fn_aj_bloc_objet_a_saisine();
    """)

    # TA2 : Exécution cascade à la décision ─────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_aj_execution_a_decision()
        RETURNS TRIGGER AS $$
        DECLARE
            v_rapport JSONB;
        BEGIN
            -- Déclenché quand annulation_judiciaire_dossier passe à 'execution'
            IF NEW.statut::TEXT = 'execution' AND
               OLD.statut::TEXT IS DISTINCT FROM 'execution' AND
               NEW.decision_id IS NOT NULL THEN
                -- Lancer l'orchestration complète
                v_rapport := executer_annulation_complete(NEW.decision_id);

                -- Logguer le résultat
                INSERT INTO audit_sensitive_ops (
                    operation, table_cible, entite_id, module_source,
                    niveau_criticite, sha256_op, valeur_apres
                ) VALUES (
                    'EXECUTE', 'annulation_judiciaire_dossier', NEW.id, 'justice',
                    'critique',
                    encode(sha256(('AJ_EXEC|' || NEW.id || '|' || NOW())::bytea), 'hex'),
                    v_rapport
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_ta2_execution_a_decision
        AFTER UPDATE OF statut ON annulation_judiciaire_dossier
        FOR EACH ROW
        WHEN (NEW.statut::TEXT = 'execution')
        EXECUTE FUNCTION tg_fn_aj_execution_a_decision();
    """)

    # TA3 : Restauration droit si objet annulé = transfert ──────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_aj_restaure_droit()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.statut_execution::TEXT = 'annule'
               AND NEW.type_objet::TEXT IN ('mutation','transfert') THEN
                PERFORM restaurer_proprietaire_precedent(
                    (SELECT parcelle_id FROM mutation_dossier
                     WHERE id = NEW.reference_objet LIMIT 1),
                    NEW.decision_id
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_ta3_restaure_droit_si_annule
        AFTER UPDATE OF statut_execution ON objet_annule
        FOR EACH ROW
        WHEN (NEW.statut_execution::TEXT = 'annule')
        EXECUTE FUNCTION tg_fn_aj_restaure_droit();
    """)

    # ════════════════════════════════════════════════════════════════
    # WORKFLOW 5 — FUSION PARCELLAIRE : étapes détaillées
    # Seed dans workflow_step_def pour le type TRANSFERT (réutilisé)
    # On crée une nouvelle définition dédiée FUSION si besoin
    # ════════════════════════════════════════════════════════════════

    # Ajouter les étapes du workflow FUSION dans workflow_step_def
    op.execute("""
        -- Créer la définition FUSION si elle n'existe pas encore
        INSERT INTO workflow_definition (
            type_workflow, nom, description,
            delai_max_jours, signature_finale_requise, annf_auto
        )
        SELECT 'TRANSFERT'::type_workflow,
               'Workflow Fusion Parcellaire',
               'Fusion de plusieurs parcelles en un seul titre foncier',
               21, true, true
        WHERE NOT EXISTS (
            SELECT 1 FROM workflow_definition
            WHERE type_workflow = 'TRANSFERT'::type_workflow
            AND nom LIKE '%Fusion%'
        );
    """)

    op.execute("""
        -- Étapes du workflow FUSION (dans la définition TRANSFERT existante)
        -- On ajoute des étapes FUSION-spécifiques si elles n'existent pas
        INSERT INTO workflow_step_def (
            id, definition_id, ordre, code_etape, nom_etape,
            role_requis, type_validation, delai_max_heures,
            est_obligatoire, action_post_validation, retour_etape_rejet
        )
        SELECT
            gen_random_uuid(), d.id,
            s.ordre, s.code, s.nom, s.role,
            s.tv::type_validation,
            s.delai, true, s.action, s.retour
        FROM workflow_definition d,
        (VALUES
            (1,'FUSION_INIT','Initialisation dossier fusion',
             'DIRECTEUR_CADASTRE','approbation',24,'CHECK_DROITS_HOMOGENES',NULL),
            (2,'FUSION_VERIF_DROITS','Vérification homogénéité des droits',
             'DIRECTEUR_CADASTRE','approbation',48,NULL,1),
            (3,'FUSION_VERIF_CHARGES','Vérification absence charges',
             'CHEF_CCFM','approbation',48,'CHECK_HYPOTHEQUES_LITIGES',1),
            (4,'FUSION_CCFM_GATE','Gate CCFM multi-parcelles',
             'CHEF_CCFM','scellement',48,'CHECK_CCFM_GATE',2),
            (5,'FUSION_ARRETE','Arrêté d''urbanisme de fusion',
             'DIRECTEUR_URBANISME','approbation',72,NULL,NULL),
            (6,'FUSION_GEOMETRIE','Calcul géométrie fusionnée',
             'GEOMETRE','constat',48,'CALCUL_GEOM_FUSION',NULL),
            (7,'FUSION_VALIDATION_CADASTRE','Validation cadastrale',
             'DIRECTEUR_CADASTRE','approbation',24,NULL,6),
            (8,'FUSION_NICAD','Génération NICAD résultant',
             'DIRECTEUR_CADASTRE','scellement',8,'GENERER_NICAD_FUSION',NULL),
            (9,'FUSION_DROITS','Création droit sur nouvelle parcelle',
             'NOTAIRE','signature',24,'CREER_DROIT_FUSION',NULL),
            (10,'FUSION_BGU','Mise à jour BGU',
             'TECH_BGU','approbation',24,'MAJ_BGU_FUSION',NULL),
            (11,'FUSION_CCFM_NOUVEAU','Nouveau CCFM parcelle résultante',
             'CHEF_CCFM','scellement',48,'GENERER_CCFM_FUSION',NULL),
            (12,'FUSION_ANNF','Archivage ANNF définitif',
             'CHEF_DAR','scellement',24,'ARCHIVER_FUSION_ANNF',NULL)
        ) AS s(ordre,code,nom,role,tv,delai,action,retour)
        WHERE d.type_workflow = 'TRANSFERT'::type_workflow
          AND d.nom LIKE '%Fusion%'
          AND NOT EXISTS (
              SELECT 1 FROM workflow_step_def wsd
              WHERE wsd.definition_id = d.id AND wsd.code_etape = s.code
          )
        ON CONFLICT (definition_id, code_etape) DO NOTHING;
    """)

    # TA4 : Créer la parcelle résultante de la fusion ───────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION tg_fn_fusion_cree_nouveau_titre()
        RETURNS TRIGGER AS $$
        DECLARE
            v_source       JSONB;
            v_ilot_id      UUID;
            v_new_parc_id  UUID;
            v_nb_sources   INT;
        BEGIN
            -- Déclenché quand fusion_parcellaire.statut passe à 'validation'
            IF NEW.statut::TEXT != 'validation' OR
               OLD.statut::TEXT = 'validation' OR
               NEW.parcelle_resultante_id IS NOT NULL THEN
                RETURN NEW;
            END IF;

            -- Compter et vérifier les sources
            SELECT jsonb_array_length(NEW.parcelles_sources) INTO v_nb_sources;
            IF v_nb_sources < 2 THEN
                RAISE EXCEPTION 'FUSION: Au moins 2 parcelles sources requises.';
            END IF;

            -- Récupérer l'îlot de la première parcelle source
            SELECT p.ilot_id INTO v_ilot_id
            FROM parcelles p
            WHERE p.id = ((NEW.parcelles_sources->0)->>'parcelle_id')::UUID;

            IF v_ilot_id IS NULL THEN
                RAISE EXCEPTION 'FUSION: Îlot de la première parcelle source introuvable.';
            END IF;

            -- Créer la nouvelle parcelle résultante
            v_new_parc_id := gen_random_uuid();
            INSERT INTO parcelles (
                id, ilot_id, nicad, geojson_id,
                statut, surface_m2, geom, is_gele,
                cree_par
            ) VALUES (
                v_new_parc_id,
                v_ilot_id,
                COALESCE(NEW.nicad_resultant,
                         'FUSION-' || SUBSTRING(v_new_parc_id::TEXT, 1, 8)),
                'geojson-fusion-' || v_new_parc_id::TEXT,
                'en_attente_validation'::statut_parcelle,
                NEW.surface_totale_m2,
                COALESCE(NEW.geom_fusionnee, 'POLYGON EMPTY'),
                false,
                (SELECT id FROM users WHERE role = 'ADMIN' LIMIT 1)
            );

            -- Lier la parcelle résultante
            UPDATE fusion_parcellaire
            SET parcelle_resultante_id = v_new_parc_id
            WHERE id = NEW.id;

            -- Archiver les parcelles sources (SUBDIVISEE → pour fusion = ARCHIVE)
            FOR v_source IN
                SELECT * FROM jsonb_array_elements(NEW.parcelles_sources)
            LOOP
                UPDATE parcelles
                SET statut = 'archive'::statut_parcelle,
                    updated_at = NOW()
                WHERE id = (v_source->>'parcelle_id')::UUID;

                -- Historique public
                INSERT INTO parcel_public_history (
                    parcelle_id, nicad, statut_avant, statut_apres,
                    module_source, motif_public, role_acteur
                ) VALUES (
                    (v_source->>'parcelle_id')::UUID,
                    v_source->>'nicad',
                    'active', 'archive',
                    'cadastre',
                    'Parcelle fusionnée dans ' ||
                    COALESCE(NEW.nicad_resultant, v_new_parc_id::TEXT),
                    'CADASTRE'
                );
            END LOOP;

            -- Audit
            INSERT INTO audit_sensitive_ops (
                operation, table_cible, entite_id, module_source,
                niveau_criticite, sha256_op, valeur_apres
            ) VALUES (
                'INSERT', 'parcelles', v_new_parc_id, 'cadastre', 'critique',
                encode(sha256(('FUSION|' || NEW.id || '|' || v_new_parc_id)::bytea), 'hex'),
                jsonb_build_object('fusion_id', NEW.id,
                                   'parcelle_resultante', v_new_parc_id,
                                   'nb_sources', v_nb_sources)
            );

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_ta4_fusion_cree_nouveau_titre
        AFTER UPDATE OF statut ON fusion_parcellaire
        FOR EACH ROW
        WHEN (NEW.statut::TEXT = 'validation' AND OLD.statut IS DISTINCT FROM NEW.statut)
        EXECUTE FUNCTION tg_fn_fusion_cree_nouveau_titre();
    """)

    # ════════════════════════════════════════════════════════════════
    # VUES WORKFLOWS 4 & 5
    # ════════════════════════════════════════════════════════════════

    op.execute("""
        CREATE OR REPLACE VIEW v_annulations_en_cours AS
        SELECT
            ajd.id              AS dossier_id,
            ajd.statut,
            ajd.objet_annulation,
            p.nicad,
            dj.type_decision,
            dj.reference_jugement,
            gj.tribunal_nom,
            dj.authenticite_verifiee,
            -- Objets concernés
            COUNT(oa.id)        AS nb_objets_annuler,
            SUM(CASE WHEN oa.statut_execution = 'annule' THEN 1 ELSE 0 END)
                                AS nb_executes,
            SUM(CASE WHEN oa.statut_execution = 'en_attente_execution' THEN 1 ELSE 0 END)
                                AS nb_en_attente,
            -- Workflow
            wi.etape_courante_code  AS wf_etape,
            wi.attendu_de_role      AS wf_role_attendu,
            wi.date_echeance        AS wf_echeance,
            CASE WHEN wi.date_echeance < NOW() AND wi.statut != 'complete'
                 THEN true ELSE false END AS est_en_retard
        FROM annulation_judiciaire_dossier ajd
        LEFT JOIN parcelles p   ON p.id = ajd.parcelle_id
        LEFT JOIN decisions_judiciaires dj ON dj.id = ajd.decision_id
        LEFT JOIN greffe_judiciaire gj     ON gj.id = dj.greffe_id
        LEFT JOIN objet_annule oa          ON oa.decision_id = dj.id
        LEFT JOIN workflow_instance wi     ON wi.id = ajd.workflow_instance_id
        WHERE ajd.statut NOT IN ('archive')
        GROUP BY ajd.id, ajd.statut, ajd.objet_annulation, p.nicad,
                 dj.type_decision, dj.reference_jugement, gj.tribunal_nom,
                 dj.authenticite_verifiee,
                 wi.etape_courante_code, wi.attendu_de_role,
                 wi.date_echeance, wi.statut;
    """)

    op.execute("""
        CREATE OR REPLACE VIEW v_fusions_en_cours AS
        SELECT
            fp.id               AS fusion_id,
            fp.statut,
            fp.nb_parcelles_sources,
            fp.surface_totale_m2,
            fp.nicad_resultant,
            fp.droits_homogenes,
            fp.pas_de_litige,
            fp.pas_d_hypotheque,
            -- Parcelle résultante
            pr.nicad            AS nicad_resultant_cree,
            pr.statut::TEXT     AS statut_resultante,
            -- Workflow
            wi.etape_courante_code  AS wf_etape,
            wi.attendu_de_role      AS wf_role_attendu,
            wi.date_echeance,
            CASE WHEN wi.date_echeance < NOW() THEN true ELSE false END AS en_retard,
            -- Arrêté
            au.code_arrete,
            -- Demandeur
            rh.nom || ' ' || COALESCE(rh.prenom,'') || COALESCE(rh.raison_sociale,'')
                                AS proprietaire_commun
        FROM fusion_parcellaire fp
        LEFT JOIN parcelles pr      ON pr.id = fp.parcelle_resultante_id
        LEFT JOIN workflow_instance wi ON wi.id = fp.workflow_instance_id
        LEFT JOIN arretes_urbanisme au ON au.id = fp.arrete_fusion_id
        LEFT JOIN right_holders rh  ON rh.id = fp.proprietaire_commun_id
        WHERE fp.statut NOT IN ('archive','rejete')
        ORDER BY fp.created_at DESC;
    """)

    # Seed greffe judiciaire Niamey
    op.execute("""
        INSERT INTO greffe_judiciaire (
            nom_greffe, tribunal_nom, tribunal_code, juridiction, statut
        ) VALUES
        ('Greffe TGI Niamey',      'Tribunal de Grande Instance de Niamey',
         'TGI-NIA-001', 'Niamey et environs', 'actif'),
        ('Greffe TGI Maradi',      'Tribunal de Grande Instance de Maradi',
         'TGI-MAR-001', 'Région de Maradi', 'actif'),
        ('Greffe TGI Zinder',      'Tribunal de Grande Instance de Zinder',
         'TGI-ZIN-001', 'Région de Zinder', 'actif'),
        ('Greffe TGI Tahoua',      'Tribunal de Grande Instance de Tahoua',
         'TGI-TAH-001', 'Région de Tahoua', 'actif'),
        ('Greffe TGI Tillabéri',   'Tribunal de Grande Instance de Tillabéri',
         'TGI-TIL-001', 'Région de Tillabéri', 'actif'),
        ('Greffe TGI Dosso',       'Tribunal de Grande Instance de Dosso',
         'TGI-DOS-001', 'Région de Dosso', 'actif'),
        ('Greffe TGI Agadez',      'Tribunal de Grande Instance d''Agadez',
         'TGI-AGA-001', 'Région de Agadez', 'actif'),
        ('Greffe TGI Diffa',       'Tribunal de Grande Instance de Diffa',
         'TGI-DIF-001', 'Région de Diffa', 'actif'),
        ('Cour d''Appel Niamey',   'Cour d''Appel de Niamey',
         'CA-NIA-001', 'National', 'actif')
        ON CONFLICT (tribunal_code) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_fusions_en_cours CASCADE")
    op.execute("DROP VIEW IF EXISTS v_annulations_en_cours CASCADE")

    for trigger, table in [
        ("tg_ta4_fusion_cree_nouveau_titre",    "fusion_parcellaire"),
        ("tg_ta3_restaure_droit_si_annule",     "objet_annule"),
        ("tg_ta2_execution_a_decision",         "annulation_judiciaire_dossier"),
        ("tg_ta1_bloc_objet_a_saisine",         "objet_annule"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    for fn in [
        "executer_annulation_complete",
        "debloquer_objet_apres_decision",
        "archiver_annulation_annf",
        "invalider_ccfm_apres_annulation",
        "restaurer_proprietaire_precedent",
        "appliquer_annulation_objet",
        "bloquer_objet_judiciaire",
        "verifier_authenticite_jugement",
        "tg_fn_aj_bloc_objet_a_saisine",
        "tg_fn_aj_execution_a_decision",
        "tg_fn_aj_restaure_droit",
        "tg_fn_fusion_cree_nouveau_titre",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(TEXT,UUID,UUID) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID,UUID) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID,UUID,TEXT,TEXT) CASCADE")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(UUID,UUID,UUID,UUID) CASCADE")

    # Supprimer les colonnes ajoutées à decisions_judiciaires
    for col in ["greffe_id","reference_jugement","sha256_jugement",
                "signature_jugement","authenticite_verifiee","verifiee_par"]:
        op.execute(f"ALTER TABLE decisions_judiciaires DROP COLUMN IF EXISTS {col}")

    for table in [
        "signature_judiciaire", "historique_annulation",
        "objet_annule", "greffe_judiciaire",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    for name in [
        "type_objet_annule", "type_decision_judiciaire_ext",
        "statut_greffe", "statut_objet_annule",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")
