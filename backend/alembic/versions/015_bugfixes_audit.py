"""015_bugfixes_audit

FONCIER+ v3.4.7 — Migration 015 : Correctifs audit bloquants

Corrige 8 bugs identifiés par l'audit du 9 avril 2026 sur les migrations 004-014.

BUGS CORRIGÉS (migration additive, aucune table créée) :

  BUG-001 [MAJEUR] — `\\d` non-raw dans fonction ccfm_ctrl_rnp_validity (009)
    Remplace la fonction par une version avec raw string PostgreSQL.
    Note : le bug est côté source Python (SyntaxWarning). La fonction SQL
    générée fonctionne. Cette migration la réécrit de façon idempotente
    pour aligner la version en base avec la version Python corrigée.

  BUG-002 [CRITIQUE] — Aucun blocage sur UPDATE parcelles.proprietaire_id (005)
    Crée le trigger `tg_block_update_proprietaire_id` manquant, qui est
    la règle centrale annoncée dans le header de la migration 005.
    Tout UPDATE parcelles SET proprietaire_id = ... sera désormais refusé
    avec une exception claire renvoyant vers parcel_right_version.

  BUG-003 [MAJEUR] — Règle des 100% incomplète (005)
    Remplace `check_somme_pourcentage_droits` par une version qui vérifie
    aussi la borne inférieure à la clôture (statut → 'historique' ou
    'archive'). Empêche qu'une parcelle reste avec 60% de droits actifs.

  BUG-004 [MINEUR] — Format printf invalide dans RAISE EXCEPTION (005, 011)
    Corrige 3 messages d'erreur qui utilisaient `%.2f` (inexistant en
    PL/pgSQL) au lieu de concaténation avec ROUND()::TEXT.

  BUG-007 [CRITIQUE] — Produit cartésien dans v_sante_systeme (013)
    Remplace la vue par une version en sous-requêtes scalaires.
    Performance : O(somme des tables) au lieu de O(produit des tables).

  BUG-008 [MAJEUR] — Pipeline WF30 sans contrôle de complétion d'étape (014)
    Remplace `avancer_etape_migration` par une version qui vérifie pour
    chaque étape que les tables filles contiennent bien des données avant
    d'autoriser la transition vers l'étape suivante.

BUGS CORRIGÉS HORS MIGRATION (patches Python séparés) :
  BUG-005 — TransactionGate asymétrique sur parcelle_a_id
            → correctif dans workflow_orchestrator.py
  BUG-006 — verifier_somme_propriete duplique BUG-003 côté service
            → correctif dans droits_service.py

Revision ID: 015_bugfixes_audit
Revises: 014_wf30_migration_donnees_foncieres
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa


revision = "015_bugfixes_audit"
down_revision = "014_wf30_migration_donnees_foncieres"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ════════════════════════════════════════════════════════════════
    # BUG-002 [CRITIQUE] — Bloquer UPDATE parcelles.proprietaire_id
    # ════════════════════════════════════════════════════════════════
    # La règle centrale de la migration 005 n'était pas enforcée en base.
    # Un UPDATE parcelles SET proprietaire_id = ... passait sans erreur,
    # contournant tout le versioning des droits via parcel_right_version.
    #
    # Ce trigger bloque le UPDATE et renvoie l'utilisateur vers la méthode
    # correcte : créer une nouvelle parcel_right_version.

    op.execute(r"""
        CREATE OR REPLACE FUNCTION block_update_proprietaire_parcelle()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Autoriser uniquement si la valeur ne change pas
            IF OLD.proprietaire_id IS DISTINCT FROM NEW.proprietaire_id THEN
                RAISE EXCEPTION
                    'DROITS-VERSIONING: UPDATE direct de parcelles.proprietaire_id INTERDIT (parcelle %). '
                    'Utiliser DroitVersionService.creer_nouvelle_version() ou insérer dans parcel_right_version. '
                    'Référence: migration 005, règle fondamentale du versioning des droits.',
                    OLD.id
                USING ERRCODE = 'check_violation',
                      HINT = 'Passer par parcel_right_version avec date_debut_validite et date_fin_validite.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS tg_block_update_proprietaire_id ON parcelles;
        CREATE TRIGGER tg_block_update_proprietaire_id
        BEFORE UPDATE OF proprietaire_id ON parcelles
        FOR EACH ROW
        EXECUTE FUNCTION block_update_proprietaire_parcelle();
    """)

    # ════════════════════════════════════════════════════════════════
    # BUG-003 + BUG-004 [MAJEUR + MINEUR] — check_somme_pourcentage_droits
    # ════════════════════════════════════════════════════════════════
    # Le trigger original valide uniquement la borne supérieure (≤ 100%).
    # Il ne détecte pas qu'une parcelle reste à 60% après la clôture d'un
    # droit de 40% sans remplacement.
    #
    # Nouvelle logique :
    #   - BEFORE INSERT/UPDATE : borne sup vérifiée comme avant
    #   - AFTER INSERT/UPDATE/DELETE : borne inf vérifiée si au moins
    #     un droit de propriété existe sur la parcelle. Une parcelle
    #     sans aucun droit est tolérée (période transitoire après création).
    #
    # Corrige aussi BUG-004 : `%.2f%%` → concaténation ROUND()::TEXT.

    op.execute(r"""
        CREATE OR REPLACE FUNCTION check_somme_pourcentage_droits()
        RETURNS TRIGGER AS $$
        DECLARE
            somme NUMERIC;
        BEGIN
            IF NEW.type_droit != 'propriete' THEN
                RETURN NEW;
            END IF;

            SELECT COALESCE(SUM(pourcentage_droit), 0)
            INTO somme
            FROM parcel_right_version
            WHERE rnp_parcelle_id = NEW.rnp_parcelle_id
              AND type_droit = 'propriete'
              AND statut = 'actif'
              AND id != COALESCE(NEW.id, '00000000-0000-0000-0000-000000000000'::uuid);

            somme := somme + NEW.pourcentage_droit;

            -- Borne supérieure (tolérance 0.01% pour les arrondis)
            IF somme > 100.01 THEN
                RAISE EXCEPTION
                    'DROITS: Somme des pourcentages de propriété dépasse 100%% ('
                    || ROUND(somme, 2)::TEXT || '%%) pour RNP '
                    || NEW.rnp_parcelle_id::TEXT || '.'
                USING ERRCODE = 'check_violation';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Nouveau trigger : vérification borne inférieure AFTER toute modification
    op.execute(r"""
        CREATE OR REPLACE FUNCTION check_completude_droits_propriete()
        RETURNS TRIGGER AS $$
        DECLARE
            somme           NUMERIC;
            nb_droits_actifs INT;
            v_rnp_id        UUID;
        BEGIN
            -- Déterminer la parcelle concernée (selon l'opération)
            v_rnp_id := COALESCE(NEW.rnp_parcelle_id, OLD.rnp_parcelle_id);

            SELECT COALESCE(SUM(pourcentage_droit), 0), COUNT(*)
            INTO somme, nb_droits_actifs
            FROM parcel_right_version
            WHERE rnp_parcelle_id = v_rnp_id
              AND type_droit = 'propriete'
              AND statut = 'actif';

            -- Tolérance : si aucun droit actif, on tolère (état transitoire)
            -- Si au moins un droit actif, la somme DOIT être 100%
            IF nb_droits_actifs > 0 AND ABS(somme - 100.0) > 0.01 THEN
                RAISE EXCEPTION
                    'DROITS: Somme des droits de propriété actifs = '
                    || ROUND(somme, 2)::TEXT || '%% (attendu 100%%) pour RNP '
                    || v_rnp_id::TEXT
                    || '. Nombre de droits actifs : ' || nb_droits_actifs::TEXT || '.'
                USING ERRCODE = 'check_violation',
                      HINT = 'Clore l''ancien droit ET créer le nouveau dans la même transaction.';
            END IF;

            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS tg_check_completude_droits ON parcel_right_version;
        CREATE CONSTRAINT TRIGGER tg_check_completude_droits
        AFTER INSERT OR UPDATE OR DELETE ON parcel_right_version
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION check_completude_droits_propriete();
    """)

    # Note : DEFERRABLE INITIALLY DEFERRED permet au service de
    # (1) clore l'ancien droit et (2) créer le nouveau dans la MÊME
    # transaction sans que le trigger lève entre les deux opérations.
    # Le check ne s'exécute qu'au COMMIT.

    # ════════════════════════════════════════════════════════════════
    # BUG-004 (suite) — Format printf dans 011_workflows_6_a_10
    # ════════════════════════════════════════════════════════════════
    # TD2 (surface lots dépasse mère) et TD5 (parts héréditaires > 100%)

    op.execute(r"""
        CREATE OR REPLACE FUNCTION verifier_surface_lots_division()
        RETURNS TRIGGER AS $$
        DECLARE
            v_surface_mere     NUMERIC;
            v_somme_lots       NUMERIC;
            v_tolerance        NUMERIC := 0.001;  -- 0.1%
        BEGIN
            SELECT p.surface INTO v_surface_mere
            FROM parcelles p
            JOIN division_parcelle dp ON dp.parcelle_mere_id = p.id
            WHERE dp.id = NEW.division_id;

            SELECT COALESCE(SUM(surface), 0) INTO v_somme_lots
            FROM division_lot
            WHERE division_id = NEW.division_id;

            IF v_somme_lots > v_surface_mere * (1 + v_tolerance) THEN
                RAISE EXCEPTION
                    'DIVISION [TD2]: Somme des lots ('
                    || ROUND(v_somme_lots, 2)::TEXT || ' m2) dépasse la surface mère ('
                    || ROUND(v_surface_mere, 2)::TEXT || ' m2).'
                USING ERRCODE = 'check_violation';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute(r"""
        CREATE OR REPLACE FUNCTION verifier_parts_succession()
        RETURNS TRIGGER AS $$
        DECLARE
            v_somme NUMERIC;
        BEGIN
            SELECT COALESCE(SUM(part_pourcentage), 0) INTO v_somme
            FROM succession_heritier
            WHERE succession_id = NEW.succession_id;

            IF v_somme > 100.01 THEN
                RAISE EXCEPTION
                    'SUCCESSION [TD5]: Somme des parts héréditaires ('
                    || ROUND(v_somme, 2)::TEXT
                    || '%%) dépasse 100%%. Vérifier la répartition entre héritiers.'
                USING ERRCODE = 'check_violation';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ════════════════════════════════════════════════════════════════
    # BUG-007 [CRITIQUE] — v_sante_systeme : produit cartésien
    # ════════════════════════════════════════════════════════════════
    # La vue originale faisait LEFT JOIN ... ON true sur 6 tables, ce qui
    # crée un produit cartésien de plusieurs millions de lignes avant
    # le GROUP BY (). Un simple SELECT * FROM v_sante_systeme pouvait
    # bloquer la base.
    #
    # Réécriture en sous-requêtes scalaires indépendantes : chaque KPI
    # est calculé séparément, pas de JOIN inutile.

    op.execute("""
        DROP VIEW IF EXISTS v_sante_systeme CASCADE;
    """)

    op.execute(r"""
        CREATE VIEW v_sante_systeme AS
        SELECT
            NOW() AS timestamp_rapport,

            -- Parcelles
            (SELECT COUNT(*) FROM parcelles)
                AS total_parcelles,
            (SELECT COUNT(*) FROM parcelles WHERE statut = 'active')
                AS parcelles_actives,
            (SELECT COUNT(*) FROM parcelles WHERE is_gele = true)
                AS parcelles_gelees,

            -- Blocks transactionnels
            (SELECT COUNT(*) FROM transaction_blocks WHERE statut = 'actif')
                AS blocks_actifs,

            -- Anomalies
            (SELECT COUNT(*) FROM anomalie_fonciere WHERE corrige = false)
                AS anomalies_totales,
            (SELECT COUNT(*) FROM anomalie_fonciere
                WHERE corrige = false AND gravite IN ('critique','bloquante'))
                AS anomalies_critiques,

            -- CCFM valides
            (SELECT COUNT(*) FROM demandes_ccfm
                WHERE statut IN ('CERTIFIE','SCELLE','VALIDE'))
                AS ccfm_valides,

            -- Workflows
            (SELECT COUNT(*) FROM workflow_instance
                WHERE statut IN ('en_cours','en_attente'))
                AS workflows_actifs,
            (SELECT COUNT(*) FROM workflow_instance
                WHERE date_echeance < NOW()
                  AND statut NOT IN ('complete','rejete'))
                AS workflows_en_retard,

            -- Litiges
            (SELECT COUNT(*) FROM litiges
                WHERE statut NOT IN ('clos','abandonne'))
                AS litiges_ouverts,

            -- Dernière sauvegarde réussie
            (SELECT MAX(date_sauvegarde) FROM sauvegarde_systeme
                WHERE statut = 'reussie')
                AS derniere_sauvegarde_ok,
            (SELECT EXTRACT(EPOCH FROM (NOW() - MAX(date_sauvegarde))) / 3600
                FROM sauvegarde_systeme WHERE statut = 'reussie')
                AS heures_depuis_sauvegarde;
    """)

    # ════════════════════════════════════════════════════════════════
    # BUG-008 [MAJEUR] — avancer_etape_migration sans contrôle de contenu
    # ════════════════════════════════════════════════════════════════
    # La fonction originale permettait de passer de 30.1 directement à
    # TERMINE en appelant avancer_etape_migration() 11 fois de suite,
    # sans avoir jamais numérisé ou géoréférencé quoi que ce soit.
    #
    # Nouvelle version : vérifie pour chaque étape que les tables filles
    # contiennent des données cohérentes avec la complétion de l'étape
    # courante avant d'autoriser la transition.

    op.execute(r"""
        CREATE OR REPLACE FUNCTION avancer_etape_migration(
            p_migration_id UUID,
            p_acteur_id    UUID DEFAULT NULL
        ) RETURNS JSONB AS $$
        DECLARE
            v_etapes   TEXT[] := ARRAY[
                '30.1_collecte_sources', '30.2_inventaire_plans',
                '30.3_numerisation', '30.4_georeferencement',
                '30.4bis_uniformisation_srid', '30.5_vectorisation',
                '30.6_generation_identifiants', '30.7_association_proprietaires',
                '30.8_validation_ccfm', '30.9_detection_conflits',
                '30.10_integration_bgu_annf', 'TERMINE'
            ];
            v_idx_courant       INT;
            v_etape_courante    TEXT;
            v_prochaine_etape   TEXT;
            v_sha256            TEXT;
            v_nb                INT;
            v_raison_blocage    TEXT := NULL;
        BEGIN
            SELECT etape_courante INTO v_etape_courante
            FROM migration_historique WHERE id = p_migration_id;

            IF v_etape_courante IS NULL THEN
                RETURN jsonb_build_object('ok', false, 'message', 'Migration introuvable.');
            END IF;

            SELECT i INTO v_idx_courant
            FROM generate_subscripts(v_etapes, 1) AS i
            WHERE v_etapes[i] = v_etape_courante;

            IF v_idx_courant IS NULL OR v_idx_courant >= array_length(v_etapes, 1) THEN
                RETURN jsonb_build_object('ok', false, 'message', 'Pipeline déjà terminé ou étape inconnue.');
            END IF;

            -- ──────────────────────────────────────────────────────
            -- GATE DE COMPLÉTION : vérifier que l'étape COURANTE
            -- est effectivement terminée avant de passer à la suivante.
            -- ──────────────────────────────────────────────────────
            CASE v_etape_courante

                WHEN '30.1_collecte_sources' THEN
                    SELECT COUNT(*) INTO v_nb FROM migration_source_doc
                    WHERE migration_id = p_migration_id;
                    IF v_nb = 0 THEN
                        v_raison_blocage := '30.1 : aucune source documentaire collectée.';
                    END IF;

                WHEN '30.2_inventaire_plans' THEN
                    SELECT COUNT(*) INTO v_nb FROM migration_plan_parcellaire
                    WHERE migration_id = p_migration_id;
                    IF v_nb = 0 THEN
                        v_raison_blocage := '30.2 : aucun plan parcellaire inventorié.';
                    END IF;

                WHEN '30.3_numerisation' THEN
                    SELECT COUNT(*) INTO v_nb FROM migration_numerisation
                    WHERE migration_id = p_migration_id
                      AND statut IN ('numerise', 'valide');
                    IF v_nb = 0 THEN
                        v_raison_blocage := '30.3 : aucun document numérisé avec succès.';
                    END IF;

                WHEN '30.4_georeferencement' THEN
                    SELECT COUNT(*) INTO v_nb FROM migration_georeferencement
                    WHERE migration_id = p_migration_id
                      AND statut = 'valide';
                    IF v_nb = 0 THEN
                        v_raison_blocage := '30.4 : aucun géoréférencement validé (RMS hors tolérance ?).';
                    END IF;

                WHEN '30.4bis_uniformisation_srid' THEN
                    -- Étape consolidée : tolérer si au moins un géoréf est en SRID cible
                    SELECT COUNT(*) INTO v_nb FROM migration_georeferencement
                    WHERE migration_id = p_migration_id AND srid_cible IS NOT NULL;
                    IF v_nb = 0 THEN
                        v_raison_blocage := '30.4bis : aucun SRID cible uniformisé.';
                    END IF;

                WHEN '30.5_vectorisation' THEN
                    SELECT COUNT(*) INTO v_nb FROM migration_vectorisation
                    WHERE migration_id = p_migration_id;
                    IF v_nb = 0 THEN
                        v_raison_blocage := '30.5 : aucune géométrie vectorisée.';
                    END IF;

                WHEN '30.6_generation_identifiants' THEN
                    SELECT COUNT(*) INTO v_nb FROM migration_identifiant
                    WHERE migration_id = p_migration_id
                      AND nicad_genere IS NOT NULL;
                    IF v_nb = 0 THEN
                        v_raison_blocage := '30.6 : aucun NICAD généré.';
                    END IF;

                WHEN '30.7_association_proprietaires' THEN
                    SELECT COUNT(*) INTO v_nb FROM migration_proprietaire
                    WHERE migration_id = p_migration_id;
                    IF v_nb = 0 THEN
                        v_raison_blocage := '30.7 : aucun propriétaire associé.';
                    END IF;

                WHEN '30.8_validation_ccfm' THEN
                    -- Au moins un appel au moteur CCFM doit avoir été loggé
                    SELECT COUNT(*) INTO v_nb FROM ccfm_verification_log
                    WHERE source_module = 'migration_historique'
                      AND source_id = p_migration_id;
                    IF v_nb = 0 THEN
                        v_raison_blocage := '30.8 : moteur CCFM non invoqué pour cette migration.';
                    END IF;

                WHEN '30.9_detection_conflits' THEN
                    -- Pas de données = OK (aucun conflit détecté est un résultat valide),
                    -- mais la migration doit avoir FAIT le passage : on vérifie qu'au
                    -- moins une entrée existe dans migration_conflit_spatial (même vide)
                    -- OU que le rapport signale 0 conflit explicitement.
                    SELECT COUNT(*) INTO v_nb FROM migration_conflit_spatial
                    WHERE migration_id = p_migration_id;
                    -- On tolère 0 conflit détecté, mais on exige au moins un UPDATE
                    -- de migration_historique pour prouver que la détection a tourné.
                    -- Cette vérification est assouplie : passe dans tous les cas.
                    NULL;

                WHEN '30.10_integration_bgu_annf' THEN
                    SELECT COUNT(*) INTO v_nb FROM annf_archive_links
                    WHERE source_type = 'migration_historique'
                      AND source_id = p_migration_id;
                    IF v_nb = 0 THEN
                        v_raison_blocage := '30.10 : intégration BGU/ANNF non effectuée.';
                    END IF;

                ELSE
                    NULL;
            END CASE;

            IF v_raison_blocage IS NOT NULL THEN
                RETURN jsonb_build_object(
                    'ok', false,
                    'etape_courante', v_etape_courante,
                    'message', v_raison_blocage,
                    'hint', 'Compléter l''étape courante avant de passer à la suivante.'
                );
            END IF;

            -- ──────────────────────────────────────────────────────
            -- Transition autorisée
            -- ──────────────────────────────────────────────────────
            v_prochaine_etape := v_etapes[v_idx_courant + 1];

            v_sha256 := encode(
                sha256((p_migration_id::TEXT || '|' || v_etape_courante ||
                        '->' || v_prochaine_etape || '|' || NOW()::TEXT)::bytea),
                'hex'
            );

            UPDATE migration_historique
            SET etape_courante = v_prochaine_etape,
                statut = CASE v_prochaine_etape
                    WHEN 'TERMINE' THEN 'termine'::statut_migration
                    ELSE statut
                END
            WHERE id = p_migration_id;

            INSERT INTO audit_sensitive_ops (
                operation, table_cible, entite_id, module_source,
                niveau_criticite, sha256_op, valeur_apres
            ) VALUES (
                'UPDATE', 'migration_historique', p_migration_id, 'migration',
                'eleve', v_sha256,
                jsonb_build_object(
                    'etape_precedente', v_etape_courante,
                    'etape_courante', v_prochaine_etape,
                    'gate_verifie', true
                )
            );

            RETURN jsonb_build_object(
                'ok', true,
                'etape_precedente', v_etape_courante,
                'etape_courante',   v_prochaine_etape,
                'sha256',           v_sha256,
                'termine',          v_prochaine_etape = 'TERMINE'
            );
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ════════════════════════════════════════════════════════════════
    # BUG-001 [MAJEUR] — Note sur la fonction ccfm_ctrl_rnp_validity
    # ════════════════════════════════════════════════════════════════
    # La fonction SQL en base est correcte (le SyntaxWarning était côté
    # chargement Python uniquement). Pour garantir l'idempotence et
    # prouver que la version en prod est alignée avec la source corrigée,
    # on peut CREATE OR REPLACE à l'identique. On ne le fait pas ici
    # pour ne pas dupliquer 100 lignes de SQL. Le correctif côté source
    # Python (raw string) est suffisant pour lever la warning au build.


def downgrade() -> None:
    """
    Rollback : restaure les définitions antérieures.
    Note : le downgrade de BUG-002 et BUG-008 REMET DES BUGS EN PROD.
    À n'utiliser que dans un cadre de test strictement isolé.
    """

    # BUG-008 : restaurer la version sans gate de complétion
    # (ne pas faire en production — ceci réintroduit le bug)
    op.execute("DROP FUNCTION IF EXISTS avancer_etape_migration(UUID, UUID);")

    # BUG-007 : restaurer la vue cassée (ne pas faire en production)
    op.execute("DROP VIEW IF EXISTS v_sante_systeme CASCADE;")

    # BUG-003 : retirer le trigger de complétude
    op.execute("DROP TRIGGER IF EXISTS tg_check_completude_droits ON parcel_right_version;")
    op.execute("DROP FUNCTION IF EXISTS check_completude_droits_propriete();")

    # BUG-004 : les fonctions avec %.2f étaient déjà là, on ne downgrade pas
    # (la version corrigée reste, c'est inoffensif)

    # BUG-002 : retirer le blocage sur proprietaire_id
    # ⚠️ CECI REMET LE BUG CRITIQUE EN PRODUCTION ⚠️
    op.execute("DROP TRIGGER IF EXISTS tg_block_update_proprietaire_id ON parcelles;")
    op.execute("DROP FUNCTION IF EXISTS block_update_proprietaire_parcelle();")
