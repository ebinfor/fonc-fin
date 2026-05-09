"""029_annf_dar_liaison

FONCIER+ — Migration 029 : Liaison DAR automatique vers ANNF

Ajoute la colonne source_module à annf_archive_links pour tracer
l'origine de chaque archive (quel module DAR l'a créée).
Ajoute un index et une vue v_annf_par_module pour le tableau de bord.

Revision ID: 029_annf_dar_liaison
Revises: 028_workflow_definition_seeds
"""
from alembic import op
import sqlalchemy as sa

revision      = "029_annf_dar_liaison"
down_revision = "028_workflow_definition_seeds"
branch_labels = None
depends_on    = None


MODULES_DAR = [
    "urbanisme","cadastre","domaine","commune","banque",
    "journal_officiel","justice","ccfm","notaire","bgu","annf"
]


def upgrade() -> None:
    # Ajouter source_module à annf_archive_links
    op.execute("""
        ALTER TABLE annf_archive_links
            ADD COLUMN IF NOT EXISTS source_module VARCHAR(50)
                DEFAULT NULL
                CHECK (source_module IN (
                    'urbanisme','cadastre','domaine','commune','banque',
                    'journal_officiel','justice','ccfm','notaire','bgu',
                    'annf','wf30_migration','system'
                ));
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_annf_source_module
            ON annf_archive_links(source_module);
    """)

    # Vue consolidée DAR par module
    op.execute("""
        CREATE OR REPLACE VIEW v_annf_par_module AS
        SELECT
            COALESCE(source_module, 'non_classe') AS module,
            type_archive,
            COUNT(*)                              AS nb_archives,
            COUNT(*) FILTER (WHERE scelle=TRUE)  AS nb_scelles,
            COUNT(*) FILTER (WHERE scelle=FALSE) AS nb_en_attente,
            MAX(created_at)                       AS derniere_archive,
            MAX(scelle_at)                        AS dernier_scellement
        FROM annf_archive_links
        GROUP BY COALESCE(source_module,'non_classe'), type_archive
        ORDER BY module, type_archive;
    """)

    # Trigger : renseigner automatiquement source_module
    # si l'entite_table contient le nom du module
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_auto_source_module()
        RETURNS TRIGGER AS $$
        DECLARE
            v_mod TEXT := NULL;
        BEGIN
            IF NEW.source_module IS NOT NULL THEN
                RETURN NEW;
            END IF;
            -- Inférer depuis entite_table
            IF    NEW.entite_table ILIKE '%rnaf%'
               OR NEW.entite_table ILIKE '%arrete%'
               OR NEW.entite_table ILIKE '%lotissement%'
               THEN v_mod := 'urbanisme';
            ELSIF NEW.entite_table ILIKE '%cadastre%'
               OR NEW.entite_table ILIKE '%parcelle%'
               OR NEW.entite_table ILIKE '%nicad%'
               OR NEW.entite_table ILIKE '%rnp%'
               THEN v_mod := 'cadastre';
            ELSIF NEW.entite_table ILIKE '%ccfm%'
               OR NEW.entite_table ILIKE '%certificat%'
               THEN v_mod := 'ccfm';
            ELSIF NEW.entite_table ILIKE '%notaire%'
               OR NEW.entite_table ILIKE '%acte%'
               OR NEW.entite_table ILIKE '%succession%'
               THEN v_mod := 'notaire';
            ELSIF NEW.entite_table ILIKE '%banque%'
               OR NEW.entite_table ILIKE '%mortgage%'
               OR NEW.entite_table ILIKE '%hypotheque%'
               THEN v_mod := 'banque';
            ELSIF NEW.entite_table ILIKE '%justice%'
               OR NEW.entite_table ILIKE '%litige%'
               OR NEW.entite_table ILIKE '%decision%'
               THEN v_mod := 'justice';
            ELSIF NEW.entite_table ILIKE '%journal%'
               OR NEW.entite_table ILIKE '%publication%'
               OR NEW.entite_table ILIKE '%parution%'
               THEN v_mod := 'journal_officiel';
            ELSIF NEW.entite_table ILIKE '%commune%'
               OR NEW.entite_table ILIKE '%dar%'
               THEN v_mod := 'commune';
            ELSIF NEW.entite_table ILIKE '%domaine%'
               OR NEW.entite_table ILIKE '%dossier_domain%'
               THEN v_mod := 'domaine';
            ELSIF NEW.entite_table ILIKE '%bgu%'
               OR NEW.entite_table ILIKE '%geojson%'
               THEN v_mod := 'bgu';
            ELSIF NEW.entite_table ILIKE '%migration%'
               THEN v_mod := 'wf30_migration';
            ELSE
                v_mod := 'annf';
            END IF;
            NEW.source_module := v_mod;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS tg_auto_source_module ON annf_archive_links;
        CREATE TRIGGER tg_auto_source_module
        BEFORE INSERT ON annf_archive_links
        FOR EACH ROW
        EXECUTE FUNCTION fn_auto_source_module();
    """)

    op.execute("""
        DO $$
        BEGIN
            RAISE NOTICE '029 OK — source_module + v_annf_par_module + tg_auto_source_module';
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tg_auto_source_module ON annf_archive_links;")
    op.execute("DROP FUNCTION IF EXISTS fn_auto_source_module();")
    op.execute("DROP VIEW IF EXISTS v_annf_par_module;")
    op.execute("""
        ALTER TABLE annf_archive_links
            DROP COLUMN IF EXISTS source_module;
    """)
