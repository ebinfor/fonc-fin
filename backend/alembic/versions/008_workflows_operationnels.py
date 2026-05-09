"""008_workflows_operationnels

FONCIER+ v3.4.7 — Migration 008 : Workflows Opérationnels Lourds
Aligné sur 70 tables existantes (migrations 001-007).

ANALYSE PRÉALABLE — Ce qui EXISTE déjà et NE PAS dupliquer :
  lotissements         → migration 003 (structure, surface, statut)
  ilots                → migration 003
  parcelles            → migration 003 (statut, geom, nicad, is_gele)
  parcel_lineage       → migration 003 (parent-enfant, suffixe a/b)
  refontes_lotissements→ migration 003 (refonte totale Autorité centrale)
  transaction_blocks   → migration 004 (blocage transactionnel)
  parcel_freeze_events → migration 004 (historique gel/dégel)
  right_holders        → migration 005 (titulaires)
  parcel_right_version → migration 005 (droits versionnés)
  property_transfers   → migration 005 (transferts traçables)
  mortgage_registry    → migration 005 (hypothèques enrichies)
  workflow_instance    → migration 007 (moteur workflow)
  workflow_step_log    → migration 007

GAPS COMBLÉS (6 tables nouvelles) :
  1. lotissement_lot          → lots individuels d'un lotissement
  2. archive_lotissement      → archivage dédié opération lotissement
  3. mutation_dossier         → dossier de mutation de propriété
  4. fusion_parcellaire       → dossier de fusion de parcelles
  5. hypotheque_dossier       → dossier workflow hypothèque bancaire
  6. annulation_judiciaire_dossier → dossier annulation judiciaire

TRIGGERS OPÉRATIONNELS (9) :
  WL1. tg_bloquer_parcelle_mere_lotissement   — statut BLOQUE à l'initiation
  WL2. tg_verifier_surface_lots              — somme lots = surface mère (tolérance 0.1%)
  WL3. tg_creer_parcelles_filles_lotissement — création automatique parcelles à validation
  WL4. tg_historiser_parcelle_mere           — mère → SUBDIVISEE à la fin
  WL5. tg_block_mutation_si_charges          — mutation bloquée si hypothèque/litige/suspension
  WL6. tg_block_fusion_si_droit_conteste     — fusion bloquée si droit contesté sur une parcelle
  WL7. tg_hypotheque_gate                   — hypothèque bloquée si parcelle inéligible
  WL8. tg_annulation_cascade                 — annulation judiciaire cascade sur droits/transfers
  WL9. tg_unicite_lotissement_actif         — une seule opération active par parcelle mère

CONTRAINTES ANTI-FRAUDE LOTISSEMENT :
  - hash géométrique SHA-256 par lot
  - UNIQUE (parcelle_mere_id) WHERE statut IN ('en_cours','en_attente')
  - Surface lots = surface mère ± 0.1%
  - Blocage vente pendant lotissement (statut BLOQUE_LOTISSEMENT)
  - Parcelle mère conservée (jamais supprimée → SUBDIVISEE)

Revision ID: 008_workflows_operationnels
Revises: 007_workflow_engine_et_triggers_complets
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "008_workflows_operationnels"
down_revision = "007_workflow_engine_et_triggers_complets"
branch_labels = None
depends_on = None

TOLERANCE_SURFACE_PCT = 0.001  # 0.1% de tolérance géométrique


def upgrade() -> None:

    # ════════════════════════════════════════════════════════════════
    # ENUMS
    # ════════════════════════════════════════════════════════════════

    for name, values in [
        ("statut_operation_lot",
         "('en_cours','en_attente_validation','valide','rejete','annule','archive')"),
        ("statut_lot",
         "('provisoire','valide','actif','vendu','annule')"),
        ("statut_mutation",
         "('initie','ccfm_verif','notaire_redaction','signature','enregistrement','archive','rejete')"),
        ("type_mutation",
         "('vente','donation','heritage','cession','echange','expropriation')"),
        ("statut_fusion",
         "('initie','verification','cadastre','validation','archive','rejete')"),
        ("statut_hypotheque_dossier",
         "('initie','ccfm_gate','evaluation','signature','enregistrement','levee','contentieux')"),
        ("statut_annulation_judiciaire",
         "('saisine','instruction','audience','decision','execution','archive')"),
    ]:
        op.execute(f"""
            DO $$ BEGIN CREATE TYPE {name} AS ENUM {values};
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """)

    # ════════════════════════════════════════════════════════════════
    # 1. LOTISSEMENT_LOT
    # Chaque lot d'un lotissement = futur parcelle RNP.
    # Lié à lotissements (003), pas à rnp_parcelle (spec) car
    # rnp_demandes est la table RNP existante.
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "lotissement_lot",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        # FK vers lotissements existant (003)
        sa.Column("lotissement_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lotissements.id"), nullable=False),

        # Lot
        sa.Column("numero_lot",     sa.String(50),  nullable=False),
        sa.Column("surface_m2",     sa.Numeric(12, 4), nullable=False),
        sa.Column("geom",           sa.String(2000),
                  comment="WKT du périmètre du lot"),

        # Anti-fraude : hash de la géométrie
        sa.Column("geometry_hash",  sa.String(64), nullable=False,
                  comment="SHA-256(numero_lot|surface_m2|geom) — anti-fraude"),

        sa.Column("statut",         sa.Enum("provisoire","valide","actif",
                                             "vendu","annule", name="statut_lot"),
                  server_default="provisoire", nullable=False),

        # Parcelle créée à partir de ce lot (rempli après validation)
        sa.Column("parcelle_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),

        # NICAD futur (provisoire → définitif à la création de la parcelle)
        sa.Column("nicad_provisoire", sa.String(30)),
        sa.Column("nicad_definitif",  sa.String(30), unique=True, nullable=True),

        # Attributaire (dès la validation du lot)
        sa.Column("attributaire_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=True),

        # Lot validé par
        sa.Column("valide_par",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id")),
        sa.Column("valide_at",     sa.DateTime(timezone=True)),

        sa.Column("created_at",    sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),

        sa.UniqueConstraint("lotissement_id", "numero_lot",
                             name="uq_lot_numero_lotissement"),
        sa.CheckConstraint("surface_m2 > 0", name="ck_lot_surface_positive"),
    )
    op.create_index("ix_ll_lotissement", "lotissement_lot", ["lotissement_id"])
    op.create_index("ix_ll_statut",      "lotissement_lot", ["statut"])

    # Index partiel anti-fraude : unicité lotissement actif par parcelle mère
    # (géré par trigger WL9 + index partiel sur lotissements)
    op.execute("""
        CREATE UNIQUE INDEX ix_lotissement_unique_actif
        ON lotissements (arrete_id)
        WHERE statut = 'actif';
    """)

    # ════════════════════════════════════════════════════════════════
    # 2. ARCHIVE_LOTISSEMENT
    # Archivage dédié à l'opération de lotissement.
    # Lié à annf_archive_links (004) via annf_link_id.
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "archive_lotissement",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        sa.Column("lotissement_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lotissements.id"), nullable=False),

        # Lien ANNF (004)
        sa.Column("annf_link_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("annf_archive_links.id"), nullable=True),

        # Documents de l'opération
        sa.Column("document_reference", sa.String(200), nullable=False),
        sa.Column("type_document",      sa.String(50),
                  comment="arrete_urbanisme|plan_cadastral|pv_bornage|publication_jo|certificat_ccfm"),
        sa.Column("chemin_stockage",    sa.String(500)),
        sa.Column("sha256_document",    sa.String(64), nullable=False),

        # Résumé de l'opération (snapshot)
        sa.Column("snapshot_json",      postgresql.JSONB),
        sa.Column("nb_lots_total",      sa.Integer),
        sa.Column("surface_totale_m2",  sa.Numeric(15, 4)),

        # Workflow associé
        sa.Column("workflow_instance_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=True),

        sa.Column("date_archivage", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("archive_par",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),

        # WORM
        sa.Column("scelle",         sa.Boolean, server_default="false"),
        sa.Column("scelle_at",      sa.DateTime(timezone=True)),
    )
    op.create_index("ix_al_lotissement", "archive_lotissement", ["lotissement_id"])

    # ════════════════════════════════════════════════════════════════
    # 3. MUTATION_DOSSIER
    # Dossier de mutation de propriété (vente, donation, héritage...).
    # Orchestre : CCFM gate → Notaire → Enregistrement → ANNF.
    # Enrichit property_transfers (005) avec le dossier procédural.
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "mutation_dossier",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        # Parcelle concernée
        sa.Column("parcelle_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("rnp_parcelle_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnp_demandes.id"), nullable=False),

        # Type et parties
        sa.Column("type_mutation", sa.Enum("vente","donation","heritage",
                                            "cession","echange","expropriation",
                                            name="type_mutation"), nullable=False),
        sa.Column("cedant_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("acquereur_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),

        sa.Column("pourcentage_cede", sa.Numeric(5, 2), nullable=False),
        sa.Column("prix_fcfa",        sa.Numeric(18, 2),
                  comment="Prix en Francs CFA — NULL si donation/héritage"),

        sa.Column("statut", sa.Enum("initie","ccfm_verif","notaire_redaction",
                                     "signature","enregistrement","archive","rejete",
                                     name="statut_mutation"),
                  server_default="initie", nullable=False),

        # Liens vers entités existantes
        sa.Column("ccfm_validation_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=True),
        sa.Column("notaire_id",          postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("notary_registry.id"), nullable=True),
        sa.Column("acte_notarie_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("actes_notaries.id"), nullable=True),
        sa.Column("transfer_id",         postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("property_transfers.id"), nullable=True),

        # Workflow
        sa.Column("workflow_instance_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=True),

        # Dates clés
        sa.Column("date_initiation",    sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("date_ccfm_valide",   sa.DateTime(timezone=True)),
        sa.Column("date_signature",     sa.DateTime(timezone=True)),
        sa.Column("date_enregistrement",sa.DateTime(timezone=True)),

        # Contrôles obligatoires
        sa.Column("ccfm_gate_ok",       sa.Boolean, server_default="false",
                  comment="Gate CCFM franchie"),
        sa.Column("litiges_verifies",   sa.Boolean, server_default="false"),
        sa.Column("hypotheques_levees", sa.Boolean, server_default="false"),

        # SHA-256 du dossier final
        sa.Column("sha256_dossier", sa.String(64)),

        sa.Column("initie_par",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at",  sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",  sa.DateTime(timezone=True)),

        sa.CheckConstraint("pourcentage_cede > 0 AND pourcentage_cede <= 100",
                            name="ck_mutation_pct_range"),
    )
    op.create_index("ix_md_parcelle", "mutation_dossier", ["parcelle_id"])
    op.create_index("ix_md_statut",   "mutation_dossier", ["statut"])
    op.create_index("ix_md_cedant",   "mutation_dossier", ["cedant_id"])

    # ════════════════════════════════════════════════════════════════
    # 4. FUSION_PARCELLAIRE
    # Fusion de plusieurs parcelles en une seule.
    # Inverse logique de la subdivision (parcel_lineage 003).
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "fusion_parcellaire",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        # Parcelle résultante (créée après validation)
        sa.Column("parcelle_resultante_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=True),

        # Parcelles sources (minimum 2)
        sa.Column("parcelles_sources", postgresql.JSONB, nullable=False,
                  comment="[{parcelle_id, nicad, surface_m2}] — min 2 parcelles"),

        sa.Column("nb_parcelles_sources", sa.Integer, nullable=False,
                  server_default="2"),

        # Géométrie fusionnée
        sa.Column("geom_fusionnee",     sa.String(5000),
                  comment="WKT ST_Union des géométries sources"),
        sa.Column("surface_totale_m2",  sa.Numeric(15, 4), nullable=False),
        sa.Column("geometry_hash",      sa.String(64),
                  comment="SHA-256 de la géométrie fusionnée"),

        # Nouveau NICAD
        sa.Column("nicad_resultant",    sa.String(30), unique=True, nullable=True),

        sa.Column("statut", sa.Enum("initie","verification","cadastre",
                                     "validation","archive","rejete",
                                     name="statut_fusion"),
                  server_default="initie", nullable=False),

        # Titulaire unique requis (toutes les parcelles sources doivent avoir
        # le même propriétaire à 100% — sinon procédure de mutation d'abord)
        sa.Column("proprietaire_commun_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=True,
                  comment="NULL si multi-propriétaires — mutation obligatoire avant"),

        # Liens institutionnels
        sa.Column("arrete_fusion_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("arretes_urbanisme.id"), nullable=True),
        sa.Column("ccfm_validation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=True),
        sa.Column("workflow_instance_id",postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=True),

        # Vérifications obligatoires
        sa.Column("droits_homogenes",   sa.Boolean, server_default="false",
                  comment="True si même propriétaire à 100% sur toutes les sources"),
        sa.Column("pas_de_litige",      sa.Boolean, server_default="false"),
        sa.Column("pas_d_hypotheque",   sa.Boolean, server_default="false"),

        sa.Column("motif",      sa.Text, nullable=False),
        sa.Column("sha256_dossier", sa.String(64)),

        sa.Column("initie_par",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at",  sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",  sa.DateTime(timezone=True)),

        sa.CheckConstraint("nb_parcelles_sources >= 2",
                            name="ck_fusion_min_2_sources"),
    )
    op.create_index("ix_fp_statut", "fusion_parcellaire", ["statut"])

    # ════════════════════════════════════════════════════════════════
    # 5. HYPOTHEQUE_DOSSIER
    # Dossier workflow complet d'une hypothèque bancaire.
    # Enrichit mortgage_registry (005) avec le processus procédural.
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "hypotheque_dossier",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        sa.Column("parcelle_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),
        sa.Column("rnp_parcelle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rnp_demandes.id"), nullable=False),

        # Parties
        sa.Column("debiteur_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False),
        sa.Column("banque_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=False,
                  comment="FK vers right_holders type_personne=banque"),

        # Conditions financières
        sa.Column("montant_fcfa",    sa.Numeric(18, 2), nullable=False),
        sa.Column("taux_interet",    sa.Numeric(5, 2)),
        sa.Column("duree_mois",      sa.Integer),
        sa.Column("date_debut",      sa.DateTime(timezone=True)),
        sa.Column("date_fin",        sa.DateTime(timezone=True)),

        sa.Column("statut", sa.Enum("initie","ccfm_gate","evaluation",
                                     "signature","enregistrement","levee","contentieux",
                                     name="statut_hypotheque_dossier"),
                  server_default="initie", nullable=False),

        # Liens vers entités existantes
        sa.Column("ccfm_validation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demandes_ccfm.id"), nullable=True),
        sa.Column("hypotheque_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("hypotheques.id"), nullable=True),
        sa.Column("mortgage_registry_id",postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("mortgage_registry.id"), nullable=True),
        sa.Column("acte_notarie_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("actes_notaries.id"), nullable=True),
        sa.Column("workflow_instance_id",postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=True),

        # Gate obligatoire : CCFM doit être valide AVANT l'hypothèque
        sa.Column("ccfm_gate_ok",       sa.Boolean, server_default="false"),
        sa.Column("valeur_expertise_m2",sa.Numeric(12, 2),
                  comment="Valeur au m² estimée par l'expert"),
        sa.Column("valeur_totale_fcfa", sa.Numeric(18, 2),
                  comment="Valeur totale de la parcelle estimée"),

        # Mainlevée
        sa.Column("date_levee",     sa.DateTime(timezone=True)),
        sa.Column("motif_levee",    sa.Text),
        sa.Column("acte_mainlevee", sa.String(200)),

        sa.Column("sha256_dossier", sa.String(64)),
        sa.Column("initie_par",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at",  sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",  sa.DateTime(timezone=True)),
    )
    op.create_index("ix_hd_parcelle", "hypotheque_dossier", ["parcelle_id"])
    op.create_index("ix_hd_statut",   "hypotheque_dossier", ["statut"])

    # ════════════════════════════════════════════════════════════════
    # 6. ANNULATION_JUDICIAIRE_DOSSIER
    # Dossier d'annulation judiciaire d'un droit ou d'une opération.
    # Enrichit decisions_judiciaires (001) + parcel_disputes (005).
    # ════════════════════════════════════════════════════════════════
    op.create_table(
        "annulation_judiciaire_dossier",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        sa.Column("parcelle_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcelles.id"), nullable=False),

        # Ce qui est contesté / annulé
        sa.Column("objet_annulation", sa.String(50), nullable=False,
                  comment="transfert|hypotheque|lotissement|droit|mutation|ccfm"),
        sa.Column("entite_cible_id",  postgresql.UUID(as_uuid=True),
                  comment="UUID de l'opération contestée"),

        # Liens judiciaires
        sa.Column("litige_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("litiges.id"), nullable=True),
        sa.Column("dispute_id",      postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("parcel_disputes.id"), nullable=True),
        sa.Column("decision_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("decisions_judiciaires.id"), nullable=True),

        # Saisissant
        sa.Column("plaignant_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("right_holders.id"), nullable=True),

        sa.Column("statut", sa.Enum("saisine","instruction","audience",
                                     "decision","execution","archive",
                                     name="statut_annulation_judiciaire"),
                  server_default="saisine", nullable=False),

        # Décision finale
        sa.Column("decision_type",      sa.String(30),
                  comment="annulation_totale|annulation_partielle|rejet|classement"),
        sa.Column("date_decision",      sa.DateTime(timezone=True)),
        sa.Column("motif_decision",     sa.Text),

        # Effets appliqués
        sa.Column("droits_annules",     sa.Boolean, server_default="false"),
        sa.Column("parcelle_gelee",     sa.Boolean, server_default="true"),
        sa.Column("transactions_bloquees",sa.Boolean, server_default="true"),

        sa.Column("workflow_instance_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_instance.id"), nullable=True),

        sa.Column("sha256_dossier", sa.String(64)),
        sa.Column("cree_par",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at",  sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",  sa.DateTime(timezone=True)),
    )
    op.create_index("ix_ajd_parcelle", "annulation_judiciaire_dossier", ["parcelle_id"])
    op.create_index("ix_ajd_statut",   "annulation_judiciaire_dossier", ["statut"])

    # ════════════════════════════════════════════════════════════════
    # TRIGGERS OPÉRATIONNELS
    # ════════════════════════════════════════════════════════════════

    # WL1 : Bloquer la parcelle mère dès initiation lotissement ──────
    op.execute("""
        CREATE OR REPLACE FUNCTION bloquer_parcelle_mere_lotissement()
        RETURNS TRIGGER AS $$
        DECLARE
            v_parcelle_id UUID;
            v_nicad       TEXT;
        BEGIN
            -- Remonter de lotissement → ilot → parcelle
            -- Structure : parcelles → ilots → lotissements (003)
            SELECT p.id, p.nicad INTO v_parcelle_id, v_nicad
            FROM lotissements l
            JOIN ilots i ON i.lotissement_id = l.id
            JOIN parcelles p ON p.ilot_id = i.id
            WHERE l.id = NEW.id
              AND p.statut = 'active'
            LIMIT 1;

            IF v_parcelle_id IS NOT NULL THEN
                -- Bloquer via transaction_blocks (004)
                INSERT INTO transaction_blocks (
                    parcelle_id, type_blockage, statut, motif,
                    sha256_motif, pose_par
                )
                SELECT
                    v_parcelle_id,
                    'admin_manuel'::type_blockage,
                    'actif'::statut_blockage,
                    'BLOQUÉ LOTISSEMENT: Opération de lotissement initiée sur ' || v_nicad,
                    encode(sha256(('LOTISSEMENT|' || v_parcelle_id || '|' || NOW())::bytea), 'hex'),
                    (SELECT id FROM users WHERE role = 'ADMIN' LIMIT 1)
                WHERE NOT EXISTS (
                    SELECT 1 FROM transaction_blocks tb
                    WHERE tb.parcelle_id = v_parcelle_id
                      AND tb.statut = 'actif'
                      AND tb.motif LIKE '%BLOQUÉ LOTISSEMENT%'
                );

                -- Marquer comme gelée dans parcelles
                UPDATE parcelles
                SET is_gele = true, updated_at = NOW()
                WHERE id = v_parcelle_id;

                -- Enregistrer dans parcel_public_history (004)
                INSERT INTO parcel_public_history (
                    parcelle_id, nicad, statut_avant, statut_apres,
                    module_source, motif_public, role_acteur
                ) VALUES (
                    v_parcelle_id, v_nicad,
                    'active', 'bloquee_lotissement',
                    'cadastre',
                    'Parcelle bloquée — Opération de lotissement en cours',
                    'CADASTRE'
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_wl1_bloquer_parcelle_mere
        AFTER INSERT ON lotissements
        FOR EACH ROW
        EXECUTE FUNCTION bloquer_parcelle_mere_lotissement();
    """)

    # WL2 : Vérification surface lots = surface mère ─────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION verifier_surface_lots()
        RETURNS TRIGGER AS $$
        DECLARE
            v_total_lots   NUMERIC := 0;
            v_surface_mere NUMERIC;
            v_lot_id       UUID;
            v_ecart_pct    NUMERIC;
            v_tolerance    NUMERIC := 0.001; -- 0.1%
        BEGIN
            -- Calculer la somme des lots existants + le nouveau
            SELECT COALESCE(SUM(l.surface_m2), 0)
            INTO v_total_lots
            FROM lotissement_lot l
            WHERE l.lotissement_id = NEW.lotissement_id
              AND l.statut != 'annule'
              AND l.id != COALESCE(NEW.id, '00000000-0000-0000-0000-000000000000'::uuid);

            v_total_lots := v_total_lots + NEW.surface_m2;

            -- Surface de la parcelle mère (via lotissement → ilots → parcelles)
            SELECT SUM(p.surface_m2) INTO v_surface_mere
            FROM lotissements lot
            JOIN ilots i ON i.lotissement_id = lot.id
            JOIN parcelles p ON p.ilot_id = i.id
            WHERE lot.id = NEW.lotissement_id
              AND p.statut NOT IN ('annule','archive');

            IF v_surface_mere IS NULL OR v_surface_mere = 0 THEN
                RETURN NEW; -- Pas encore de parcelle mère liée
            END IF;

            -- Vérifier seulement si on dépasse (ne pas bloquer si incomplet)
            v_ecart_pct := ABS(v_total_lots - v_surface_mere) / v_surface_mere;

            IF v_total_lots > v_surface_mere * (1 + v_tolerance) THEN
                RAISE EXCEPTION
                    'SURFACE INCOHÉRENTE [WL2]: Total des lots (%.2f m²) dépasse '
                    'la surface mère (%.2f m²) de %.3f%%. '
                    'Tolérance : 0.1%%.',
                    v_total_lots, v_surface_mere, v_ecart_pct * 100;
            END IF;

            -- Vérifier le hash géométrique anti-fraude
            DECLARE
                v_hash_attendu TEXT;
            BEGIN
                v_hash_attendu := encode(
                    sha256((NEW.numero_lot || '|' ||
                            NEW.surface_m2::TEXT || '|' ||
                            COALESCE(NEW.geom, 'NO_GEOM'))::bytea),
                    'hex'
                );
                IF NEW.geometry_hash != v_hash_attendu THEN
                    RAISE EXCEPTION
                        'FRAUDE DÉTECTÉE [WL2]: Hash géométrique invalide pour le lot %. '
                        'La géométrie ou la surface a été modifiée après signature.',
                        NEW.numero_lot;
                END IF;
            END;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_wl2_verifier_surface_lots
        BEFORE INSERT OR UPDATE OF surface_m2, geom ON lotissement_lot
        FOR EACH ROW
        EXECUTE FUNCTION verifier_surface_lots();
    """)

    # WL3 : Création automatique des parcelles filles ─────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION creer_parcelles_filles_lotissement()
        RETURNS TRIGGER AS $$
        DECLARE
            v_lot       RECORD;
            v_ilot_id   UUID;
            v_new_parc  UUID;
            v_nb_crees  INT := 0;
        BEGIN
            -- Déclenché quand statut lotissement passe à 'actif'
            IF NEW.statut != 'actif' OR OLD.statut = 'actif' THEN
                RETURN NEW;
            END IF;

            -- Récupérer un ilot du lotissement pour rattacher les nouvelles parcelles
            SELECT i.id INTO v_ilot_id
            FROM ilots i WHERE i.lotissement_id = NEW.id LIMIT 1;

            IF v_ilot_id IS NULL THEN
                RAISE WARNING 'WL3: Aucun îlot trouvé pour lotissement %. Parcelles non créées.', NEW.id;
                RETURN NEW;
            END IF;

            FOR v_lot IN
                SELECT ll.*
                FROM lotissement_lot ll
                WHERE ll.lotissement_id = NEW.id
                  AND ll.statut = 'valide'
                  AND ll.parcelle_id IS NULL
            LOOP
                v_new_parc := gen_random_uuid();

                -- Créer la parcelle dans parcelles (003)
                INSERT INTO parcelles (
                    id, ilot_id, nicad, geojson_id,
                    statut, surface_m2, geom,
                    is_gele, cree_par
                ) VALUES (
                    v_new_parc,
                    v_ilot_id,
                    COALESCE(v_lot.nicad_definitif, v_lot.nicad_provisoire,
                             'LOT-' || SUBSTRING(v_lot.id::TEXT, 1, 8)),
                    'geojson-lot-' || v_lot.id::TEXT,
                    'en_attente_validation'::statut_parcelle,
                    v_lot.surface_m2,
                    v_lot.geom,
                    false,
                    (SELECT id FROM users WHERE role = 'ADMIN' LIMIT 1)
                );

                -- Lier le lot à sa parcelle
                UPDATE lotissement_lot
                SET parcelle_id = v_new_parc,
                    statut = 'actif'
                WHERE id = v_lot.id;

                -- Enregistrer la filiation dans nicad_registry (003) si NICAD défini
                IF v_lot.nicad_definitif IS NOT NULL THEN
                    INSERT INTO nicad_registry (
                        nicad, parcelle_id, code_region, code_commune,
                        code_arrete, code_ilot, code_parcelle, genere_par
                    )
                    SELECT
                        v_lot.nicad_definitif,
                        v_new_parc,
                        SUBSTRING(v_lot.nicad_definitif, 1, 2),
                        SUBSTRING(v_lot.nicad_definitif, 4, 2),
                        SUBSTRING(v_lot.nicad_definitif, 7, 7),
                        SUBSTRING(v_lot.nicad_definitif, 15, 3),
                        SUBSTRING(v_lot.nicad_definitif, 19, 3),
                        (SELECT id FROM users WHERE role = 'ADMIN' LIMIT 1)
                    ON CONFLICT (nicad) DO NOTHING;
                END IF;

                v_nb_crees := v_nb_crees + 1;
            END LOOP;

            -- Journaliser
            INSERT INTO audit_sensitive_ops (
                operation, table_cible, entite_id, module_source,
                niveau_criticite, sha256_op, valeur_apres
            ) VALUES (
                'INSERT', 'parcelles', NEW.id, 'cadastre', 'eleve',
                encode(sha256(('WL3|' || NEW.id || '|' || v_nb_crees)::bytea), 'hex'),
                jsonb_build_object('lotissement_id', NEW.id,
                                   'nb_parcelles_crees', v_nb_crees)
            );

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_wl3_creer_parcelles_filles
        AFTER UPDATE OF statut ON lotissements
        FOR EACH ROW
        WHEN (NEW.statut = 'actif' AND OLD.statut IS DISTINCT FROM NEW.statut)
        EXECUTE FUNCTION creer_parcelles_filles_lotissement();
    """)

    # WL4 : Historiser la parcelle mère à la fin du lotissement ──────
    op.execute("""
        CREATE OR REPLACE FUNCTION historiser_parcelle_mere()
        RETURNS TRIGGER AS $$
        DECLARE
            v_parc_id UUID;
            v_nicad   TEXT;
        BEGIN
            IF NEW.statut NOT IN ('actif', 'archive') OR
               OLD.statut IN ('actif', 'archive') THEN
                RETURN NEW;
            END IF;

            -- Trouver et historiser la parcelle mère
            SELECT p.id, p.nicad INTO v_parc_id, v_nicad
            FROM ilots i
            JOIN parcelles p ON p.ilot_id = i.id
            WHERE i.lotissement_id = NEW.id
              AND p.is_gele = true
            LIMIT 1;

            IF v_parc_id IS NOT NULL THEN
                UPDATE parcelles
                SET statut = 'subdivisee'::statut_parcelle,
                    is_gele = false,
                    updated_at = NOW()
                WHERE id = v_parc_id;

                -- Lever le blocage
                UPDATE transaction_blocks
                SET statut = 'leve'::statut_blockage,
                    leve_at = NOW(),
                    motif_levee = 'Lotissement finalisé — parcelle mère historisée'
                WHERE parcelle_id = v_parc_id
                  AND statut = 'actif'
                  AND motif LIKE '%BLOQUÉ LOTISSEMENT%';

                -- Enregistrer la transition publique
                INSERT INTO parcel_public_history (
                    parcelle_id, nicad, statut_avant, statut_apres,
                    module_source, motif_public, role_acteur
                ) VALUES (
                    v_parc_id, v_nicad,
                    'bloquee_lotissement', 'subdivisee',
                    'cadastre',
                    'Lotissement finalisé — parcelle mère archivée en tant que SUBDIVISÉE',
                    'CADASTRE'
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_wl4_historiser_parcelle_mere
        AFTER UPDATE OF statut ON lotissements
        FOR EACH ROW
        WHEN (NEW.statut IN ('actif','archive') AND OLD.statut IS DISTINCT FROM NEW.statut)
        EXECUTE FUNCTION historiser_parcelle_mere();
    """)

    # WL5 : Blocage mutation si charges actives ───────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION bloquer_mutation_si_charges()
        RETURNS TRIGGER AS $$
        DECLARE
            nb_hyp    INT;
            nb_litiges INT;
            nb_blocks  INT;
            nb_susp    INT;
        BEGIN
            -- Hypothèques actives
            SELECT COUNT(*) INTO nb_hyp
            FROM hypotheques h WHERE h.parcelle_id = NEW.parcelle_id
              AND h.statut = 'active';
            SELECT COUNT(*) + nb_hyp INTO nb_hyp
            FROM mortgage_registry mr WHERE mr.parcelle_id = NEW.parcelle_id
              AND mr.statut = 'active';

            IF nb_hyp > 0 THEN
                RAISE EXCEPTION
                    'MUTATION BLOQUÉE [WL5]: % hypothèque(s) active(s) sur la parcelle. '
                    'Mainlevée bancaire obligatoire avant toute mutation.',
                    nb_hyp;
            END IF;

            -- Litiges ouverts
            SELECT COUNT(*) INTO nb_litiges
            FROM litiges l WHERE l.parcelle_id = NEW.parcelle_id
              AND l.statut NOT IN ('clos','abandonne');
            SELECT COUNT(*) + nb_litiges INTO nb_litiges
            FROM parcel_disputes pd WHERE pd.parcelle_id = NEW.parcelle_id
              AND pd.statut IN ('ouvert','en_instruction');

            IF nb_litiges > 0 THEN
                RAISE EXCEPTION
                    'MUTATION BLOQUÉE [WL5]: % litige(s) en cours sur la parcelle. '
                    'Résolution judiciaire obligatoire avant mutation.',
                    nb_litiges;
            END IF;

            -- Transaction blocks actifs
            SELECT COUNT(*) INTO nb_blocks
            FROM transaction_blocks tb WHERE tb.parcelle_id = NEW.parcelle_id
              AND tb.statut = 'actif';

            IF nb_blocks > 0 THEN
                RAISE EXCEPTION
                    'MUTATION BLOQUÉE [WL5]: % blocage(s) transactionnel(s) actif(s). '
                    'Lever les blocages avant toute mutation.',
                    nb_blocks;
            END IF;

            -- Suspensions CCFM
            SELECT COUNT(*) INTO nb_susp
            FROM ccfm_suspension cs WHERE cs.parcelle_id = NEW.parcelle_id
              AND cs.est_active = true;
            SELECT COUNT(*) + nb_susp INTO nb_susp
            FROM ccfm_contestation cc WHERE cc.parcelle_id = NEW.parcelle_id
              AND cc.statut IN ('ouvert','en_instruction');

            IF nb_susp > 0 THEN
                RAISE EXCEPTION
                    'MUTATION BLOQUÉE [WL5]: % suspension(s)/contestation(s) CCFM active(s). '
                    'Résolution CCFM obligatoire avant mutation.',
                    nb_susp;
            END IF;

            -- Gate CCFM obligatoire
            IF NEW.ccfm_gate_ok = false THEN
                IF NOT EXISTS (
                    SELECT 1 FROM demandes_ccfm dc
                    WHERE dc.parcelle_id = NEW.parcelle_id
                      AND dc.statut IN ('CERTIFIE','SCELLE','VALIDE')
                ) THEN
                    RAISE EXCEPTION
                        'MUTATION BLOQUÉE [WL5]: Aucun certificat CCFM valide pour la parcelle. '
                        'Obtenir certification CCFM avant toute mutation de propriété.';
                END IF;
                -- Marquer gate franchie
                NEW.ccfm_gate_ok := true;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_wl5_bloquer_mutation_si_charges
        BEFORE INSERT ON mutation_dossier
        FOR EACH ROW
        EXECUTE FUNCTION bloquer_mutation_si_charges();
    """)

    # WL6 : Bloquer fusion si droit contesté ─────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION bloquer_fusion_si_droit_conteste()
        RETURNS TRIGGER AS $$
        DECLARE
            v_source   JSONB;
            v_parc_id  UUID;
            nb_issues  INT;
        BEGIN
            -- Vérifier chaque parcelle source
            FOR v_source IN SELECT * FROM jsonb_array_elements(NEW.parcelles_sources)
            LOOP
                v_parc_id := (v_source->>'parcelle_id')::UUID;

                SELECT COUNT(*) INTO nb_issues
                FROM (
                    SELECT 1 FROM parcel_right_version prv
                    WHERE prv.parcelle_id = v_parc_id
                      AND prv.statut = 'conteste'
                    UNION ALL
                    SELECT 1 FROM transaction_blocks tb
                    WHERE tb.parcelle_id = v_parc_id
                      AND tb.statut = 'actif'
                    UNION ALL
                    SELECT 1 FROM litiges l
                    WHERE l.parcelle_id = v_parc_id
                      AND l.statut NOT IN ('clos','abandonne')
                ) x;

                IF nb_issues > 0 THEN
                    RAISE EXCEPTION
                        'FUSION BLOQUÉE [WL6]: La parcelle % (UUID: %) a des droits '
                        'contestés ou des blocages actifs. '
                        'Résoudre avant toute fusion.',
                        v_source->>'nicad', v_parc_id;
                END IF;
            END LOOP;

            -- Vérifier l'homogénéité des propriétaires
            IF NEW.droits_homogenes = false THEN
                RAISE NOTICE
                    'FUSION [WL6]: Propriétaires hétérogènes détectés. '
                    'Procéder aux mutations de propriété nécessaires d''abord.';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_wl6_bloquer_fusion_si_conteste
        BEFORE INSERT ON fusion_parcellaire
        FOR EACH ROW
        EXECUTE FUNCTION bloquer_fusion_si_droit_conteste();
    """)

    # WL7 : Gate hypothèque — vérifications avant inscription ────────
    op.execute("""
        CREATE OR REPLACE FUNCTION gate_hypotheque()
        RETURNS TRIGGER AS $$
        DECLARE
            nb_hyp_actives INT;
            nb_litiges     INT;
        BEGIN
            -- Vérifier qu'il n'y a pas déjà une hypothèque active
            SELECT COUNT(*) INTO nb_hyp_actives
            FROM hypotheques h
            WHERE h.parcelle_id = NEW.parcelle_id AND h.statut = 'active';
            SELECT COUNT(*) + nb_hyp_actives INTO nb_hyp_actives
            FROM mortgage_registry mr
            WHERE mr.parcelle_id = NEW.parcelle_id AND mr.statut = 'active';

            IF nb_hyp_actives > 0 THEN
                RAISE EXCEPTION
                    'HYPOTHÈQUE BLOQUÉE [WL7]: % hypothèque(s) déjà active(s) sur la parcelle. '
                    'Mainlevée requise avant nouvelle hypothèque.',
                    nb_hyp_actives;
            END IF;

            -- Vérifier absence de litiges
            SELECT COUNT(*) INTO nb_litiges
            FROM litiges l
            WHERE l.parcelle_id = NEW.parcelle_id
              AND l.statut NOT IN ('clos','abandonne');

            IF nb_litiges > 0 THEN
                RAISE EXCEPTION
                    'HYPOTHÈQUE BLOQUÉE [WL7]: % litige(s) en cours. '
                    'La banque ne peut pas prendre de garantie sur une parcelle litigieuse.',
                    nb_litiges;
            END IF;

            -- Gate CCFM obligatoire pour l'hypothèque
            IF NOT EXISTS (
                SELECT 1 FROM demandes_ccfm dc
                WHERE dc.parcelle_id = NEW.parcelle_id
                  AND dc.statut IN ('CERTIFIE','SCELLE','VALIDE')
            ) THEN
                RAISE EXCEPTION
                    'HYPOTHÈQUE BLOQUÉE [WL7]: Aucun certificat CCFM valide. '
                    'La banque requiert une certification CCFM avant toute hypothèque.';
            END IF;

            NEW.ccfm_gate_ok := true;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_wl7_gate_hypotheque
        BEFORE INSERT ON hypotheque_dossier
        FOR EACH ROW
        EXECUTE FUNCTION gate_hypotheque();
    """)

    # WL8 : Cascade annulation judiciaire ─────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION cascade_annulation_judiciaire()
        RETURNS TRIGGER AS $$
        DECLARE
            v_admin_id UUID;
        BEGIN
            -- Déclenché quand statut passe à 'decision'
            IF NEW.statut != 'decision' OR OLD.statut = 'decision' THEN
                RETURN NEW;
            END IF;

            SELECT id INTO v_admin_id FROM users WHERE role = 'ADMIN' LIMIT 1;

            -- Geler la parcelle
            IF NEW.parcelle_gelee = true THEN
                UPDATE parcelles
                SET is_gele = true, updated_at = NOW()
                WHERE id = NEW.parcelle_id;

                INSERT INTO parcel_freeze_events (
                    parcelle_id, type_gel, litige_id,
                    gele, motif, gele_par
                ) VALUES (
                    NEW.parcelle_id, 'justice', NEW.litige_id,
                    true,
                    'Gel judiciaire — Décision annulation: ' || COALESCE(NEW.motif_decision, ''),
                    COALESCE(v_admin_id, NEW.cree_par)
                );
            END IF;

            -- Bloquer les transactions
            IF NEW.transactions_bloquees = true THEN
                INSERT INTO transaction_blocks (
                    parcelle_id, type_blockage, statut, motif,
                    sha256_motif, source_litige_id, pose_par
                )
                SELECT
                    NEW.parcelle_id,
                    'justice_litige'::type_blockage,
                    'actif'::statut_blockage,
                    'BLOCAGE JUDICIAIRE: Annulation judiciaire en cours — '
                    || COALESCE(NEW.objet_annulation, 'opération'),
                    encode(sha256(('WL8|' || NEW.parcelle_id || '|ANNULATION')::bytea), 'hex'),
                    NEW.litige_id,
                    COALESCE(v_admin_id, NEW.cree_par)
                WHERE NOT EXISTS (
                    SELECT 1 FROM transaction_blocks tb
                    WHERE tb.parcelle_id = NEW.parcelle_id
                      AND tb.type_blockage = 'justice_litige'
                      AND tb.statut = 'actif'
                );
            END IF;

            -- Marquer les droits concernés comme contestés
            IF NEW.droits_annules = true THEN
                UPDATE parcel_right_version
                SET statut = 'conteste'::statut_droit
                WHERE parcelle_id = NEW.parcelle_id
                  AND statut = 'actif';
            END IF;

            -- Historique public
            INSERT INTO parcel_public_history (
                parcelle_id, nicad, statut_avant, statut_apres,
                module_source, entite_id, motif_public, role_acteur
            )
            SELECT
                NEW.parcelle_id, p.nicad,
                p.statut::TEXT, 'decision_judiciaire',
                'justice', NEW.id,
                'Décision judiciaire — annulation en cours sur ' || NEW.objet_annulation,
                'JUSTICE'
            FROM parcelles p WHERE p.id = NEW.parcelle_id;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_wl8_cascade_annulation_judiciaire
        AFTER UPDATE OF statut ON annulation_judiciaire_dossier
        FOR EACH ROW
        WHEN (NEW.statut = 'decision' AND OLD.statut IS DISTINCT FROM NEW.statut)
        EXECUTE FUNCTION cascade_annulation_judiciaire();
    """)

    # WL9 : Unicité opération active par parcelle mère ────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION check_unicite_lotissement_actif()
        RETURNS TRIGGER AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM lotissements l2
                JOIN ilots i2     ON i2.lotissement_id = l2.id
                JOIN ilots i_new  ON i_new.lotissement_id = NEW.id
                JOIN parcelles p2 ON p2.ilot_id = i2.id
                JOIN parcelles pn ON pn.ilot_id = i_new.id
                WHERE p2.id = pn.id
                  AND l2.id != NEW.id
                  AND l2.statut IN ('actif')
            ) THEN
                RAISE EXCEPTION
                    'LOTISSEMENT BLOQUÉ [WL9]: Une opération de lotissement active '
                    'existe déjà sur cette parcelle mère. '
                    'Un seul lotissement actif par parcelle.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_wl9_unicite_lotissement_actif
        BEFORE INSERT ON lotissements
        FOR EACH ROW
        EXECUTE FUNCTION check_unicite_lotissement_actif();
    """)

    # ════════════════════════════════════════════════════════════════
    # VUES OPÉRATIONNELLES
    # ════════════════════════════════════════════════════════════════

    op.execute("""
        CREATE OR REPLACE VIEW v_lotissements_en_cours AS
        SELECT
            l.id                    AS lotissement_id,
            l.nom_lotissement,
            l.statut,
            l.surface_totale_m2,
            l.nb_ilots_attendus,
            au.code_arrete,
            au.date_signature,
            c.nom_commune,
            r.nom_region,
            -- Lots
            COUNT(ll.id)            AS nb_lots_total,
            SUM(CASE WHEN ll.statut = 'valide' THEN 1 ELSE 0 END)
                                    AS nb_lots_valides,
            SUM(ll.surface_m2)      AS surface_lots_cumul,
            -- Incohérence surface
            ABS(SUM(COALESCE(ll.surface_m2,0)) - l.surface_totale_m2)
                / NULLIF(l.surface_totale_m2, 0) * 100
                                    AS ecart_surface_pct,
            -- Blocages actifs
            (SELECT COUNT(*) FROM transaction_blocks tb
             JOIN ilots i ON i.lotissement_id = l.id
             JOIN parcelles p ON p.ilot_id = i.id
             WHERE tb.parcelle_id = p.id AND tb.statut = 'actif'
            )                       AS nb_blocages_actifs,
            -- Workflow
            wi.statut               AS wf_statut,
            wi.etape_courante_code  AS wf_etape,
            wi.attendu_de_role      AS wf_attendu_role
        FROM lotissements l
        JOIN arretes_urbanisme au ON au.id = l.arrete_id
        JOIN communes c           ON c.id = au.commune_id
        JOIN regions r            ON r.id = c.region_id
        LEFT JOIN lotissement_lot ll ON ll.lotissement_id = l.id
        LEFT JOIN workflow_instance wi
               ON wi.entite_id = l.id
              AND wi.statut NOT IN ('complete','rejete','annule')
        WHERE l.statut NOT IN ('archive')
        GROUP BY l.id, l.nom_lotissement, l.statut, l.surface_totale_m2,
                 l.nb_ilots_attendus, au.code_arrete, au.date_signature,
                 c.nom_commune, r.nom_region, wi.statut, wi.etape_courante_code,
                 wi.attendu_de_role;
    """)

    op.execute("""
        CREATE OR REPLACE VIEW v_mutations_en_cours AS
        SELECT
            md.id               AS dossier_id,
            md.type_mutation,
            md.statut,
            md.pourcentage_cede,
            md.prix_fcfa,
            p.nicad,
            p.surface_m2,
            rh_c.nom || ' ' || COALESCE(rh_c.prenom,'') || COALESCE(rh_c.raison_sociale,'')
                                AS cedant,
            rh_a.nom || ' ' || COALESCE(rh_a.prenom,'') || COALESCE(rh_a.raison_sociale,'')
                                AS acquereur,
            md.ccfm_gate_ok,
            md.litiges_verifies,
            md.hypotheques_levees,
            md.date_initiation,
            wi.etape_courante_code  AS wf_etape,
            wi.attendu_de_role      AS wf_attendu_role,
            wi.date_echeance        AS wf_echeance
        FROM mutation_dossier md
        JOIN parcelles p        ON p.id = md.parcelle_id
        JOIN right_holders rh_c ON rh_c.id = md.cedant_id
        JOIN right_holders rh_a ON rh_a.id = md.acquereur_id
        LEFT JOIN workflow_instance wi ON wi.id = md.workflow_instance_id
        WHERE md.statut NOT IN ('archive','rejete')
        ORDER BY md.date_initiation DESC;
    """)

    op.execute("""
        CREATE OR REPLACE VIEW v_tableau_bord_operations AS
        SELECT 'LOTISSEMENT'  AS type_op,
               COUNT(*)       AS total,
               SUM(CASE WHEN statut IN ('actif') THEN 1 ELSE 0 END)  AS en_cours,
               SUM(CASE WHEN statut = 'refonte' THEN 1 ELSE 0 END)   AS en_attente,
               0              AS rejetes
        FROM lotissements WHERE statut != 'archive'
        UNION ALL
        SELECT 'MUTATION',
               COUNT(*), SUM(CASE WHEN statut NOT IN ('archive','rejete') THEN 1 ELSE 0 END),
               SUM(CASE WHEN statut IN ('initie','ccfm_verif') THEN 1 ELSE 0 END),
               SUM(CASE WHEN statut = 'rejete' THEN 1 ELSE 0 END)
        FROM mutation_dossier
        UNION ALL
        SELECT 'HYPOTHEQUE',
               COUNT(*), SUM(CASE WHEN statut NOT IN ('levee','contentieux') THEN 1 ELSE 0 END),
               SUM(CASE WHEN statut IN ('initie','ccfm_gate') THEN 1 ELSE 0 END),
               SUM(CASE WHEN statut = 'contentieux' THEN 1 ELSE 0 END)
        FROM hypotheque_dossier
        UNION ALL
        SELECT 'FUSION',
               COUNT(*), SUM(CASE WHEN statut NOT IN ('archive','rejete') THEN 1 ELSE 0 END),
               SUM(CASE WHEN statut = 'initie' THEN 1 ELSE 0 END),
               SUM(CASE WHEN statut = 'rejete' THEN 1 ELSE 0 END)
        FROM fusion_parcellaire
        UNION ALL
        SELECT 'ANNULATION_JUD',
               COUNT(*), SUM(CASE WHEN statut NOT IN ('archive') THEN 1 ELSE 0 END),
               SUM(CASE WHEN statut IN ('saisine','instruction') THEN 1 ELSE 0 END),
               0
        FROM annulation_judiciaire_dossier;
    """)

    # Seed des étapes workflow dans workflow_step_def pour LOTISSEMENT et MUTATION
    op.execute("""
        INSERT INTO workflow_step_def (
            id, definition_id, ordre, code_etape, nom_etape,
            role_requis, type_validation, delai_max_heures, est_obligatoire, action_post_validation
        )
        SELECT gen_random_uuid(), d.id, s.ordre, s.code, s.nom,
               s.role, s.tv::type_validation, s.delai, true, s.action
        FROM workflow_definition d,
        (VALUES
            (1,'INIT_LOTISSEMENT',   'Initialisation dossier','DIRECTEUR_CADASTRE','approbation',24,NULL),
            (2,'VALID_URBANISME',    'Validation Urbanisme',  'DIRECTEUR_URBANISME','approbation',72,'VERIFIER_ARRETE'),
            (3,'DECOUPE_CADASTRE',   'Découpage géométrique', 'GEOMETRE','constat',96,'HASH_GEOMETRIE'),
            (4,'VALID_COMMUNE_DAD',  'Validation Commune DAD','CHEF_DAD','approbation',48,NULL),
            (5,'CERTIF_CCFM',        'Certification CCFM',    'CHEF_CCFM','scellement',48,'CHECK_CCFM_GATE'),
            (6,'PUBLICATION_JO',     'Publication JO',        'EDITEUR_JO','publication',24,'PUBLIER_RNAF_JO'),
            (7,'PROJECTION_BGU',     'Projection BGU',        'TECH_BGU','approbation',24,'MAJ_BGU'),
            (8,'ARCHIVAGE_ANNF',     'Archivage définitif',   'CHEF_DAR','scellement',24,'ARCHIVER_LOTISSEMENT_ANNF')
        ) AS s(ordre,code,nom,role,tv,delai,action)
        WHERE d.type_workflow = 'RNP'
          AND NOT EXISTS (SELECT 1 FROM workflow_step_def wsd WHERE wsd.definition_id = d.id AND wsd.code_etape = s.code)
        ON CONFLICT (definition_id, code_etape) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_tableau_bord_operations CASCADE")
    op.execute("DROP VIEW IF EXISTS v_mutations_en_cours CASCADE")
    op.execute("DROP VIEW IF EXISTS v_lotissements_en_cours CASCADE")

    for trigger, table in [
        ("tg_wl9_unicite_lotissement_actif",         "lotissements"),
        ("tg_wl8_cascade_annulation_judiciaire",     "annulation_judiciaire_dossier"),
        ("tg_wl7_gate_hypotheque",                   "hypotheque_dossier"),
        ("tg_wl6_bloquer_fusion_si_conteste",        "fusion_parcellaire"),
        ("tg_wl5_bloquer_mutation_si_charges",       "mutation_dossier"),
        ("tg_wl4_historiser_parcelle_mere",          "lotissements"),
        ("tg_wl3_creer_parcelles_filles",            "lotissements"),
        ("tg_wl2_verifier_surface_lots",             "lotissement_lot"),
        ("tg_wl1_bloquer_parcelle_mere",             "lotissements"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    for fn in [
        "bloquer_parcelle_mere_lotissement",
        "verifier_surface_lots",
        "creer_parcelles_filles_lotissement",
        "historiser_parcelle_mere",
        "bloquer_mutation_si_charges",
        "bloquer_fusion_si_droit_conteste",
        "gate_hypotheque",
        "cascade_annulation_judiciaire",
        "check_unicite_lotissement_actif",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")

    op.execute("DROP INDEX IF EXISTS ix_lotissement_unique_actif")

    for table in [
        "annulation_judiciaire_dossier", "hypotheque_dossier",
        "fusion_parcellaire", "mutation_dossier",
        "archive_lotissement", "lotissement_lot",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    for name in [
        "statut_operation_lot", "statut_lot", "statut_mutation",
        "type_mutation", "statut_fusion", "statut_hypotheque_dossier",
        "statut_annulation_judiciaire",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")
