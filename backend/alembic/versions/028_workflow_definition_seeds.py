"""028_workflow_definition_seeds

FONCIER+ — Migration 028 : Seeds workflow_definition types classiques

Assure que workflow_definition contient une entrée active pour chaque
type de workflow utilisé par le moteur. Idempotente (ON CONFLICT DO UPDATE).

Revision ID: 028_workflow_definition_seeds
Revises: 027_workflow_form_schemas
"""
from alembic import op

revision      = "028_workflow_definition_seeds"
down_revision = "027_workflow_form_schemas"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO workflow_definition
            (id, type_workflow, nom, description,
             delai_max_jours, signature_finale_requise, annf_auto, is_active)
        VALUES
        (gen_random_uuid(), 'RNAF',
         'Publication RNAF - Registre National des Arretes Fonciers',
         'De la redaction de l arrête à la publication officielle au JO',
         30, true, true, true),
        (gen_random_uuid(), 'RNP',
         'Immatriculation RNP - Registre National Parcellaire',
         'De la demande de numerotation a la generation du NICAD certifie',
         15, true, true, true),
        (gen_random_uuid(), 'CCFM',
         'Certification CCFM - Certificat de Conformite Fonciere',
         'De la demande au certificat scelle avec QR code de verification',
         21, true, true, true),
        (gen_random_uuid(), 'BGU',
         'Scellement BGU - Base Geospatiale Unique',
         'Import, controle qualite, validation et scellement geometrie',
         10, true, true, true),
        (gen_random_uuid(), 'ARCHIVAGE_DEFINITIF',
         'Archivage WORM definitif - WF27',
         'Detection, transfert, indexation et scellement WORM irréversible',
         7, true, true, true),
        (gen_random_uuid(), 'ANNULATION_JUDICIAIRE',
         'Annulation judiciaire - WF24',
         'Saisine judiciaire, instruction, decision et execution cascade',
         90, true, true, true),
        (gen_random_uuid(), 'MUTATION_VENTE',
         'Mutation vente - WF17',
         'De la demande de mutation a l enregistrement au cadastre',
         14, true, true, true),
        (gen_random_uuid(), 'SUCCESSION_FONCIERE',
         'Succession fonciere - WF19',
         'De la declaration de deces au transfert des droits aux heritiers',
         60, true, true, true),
        (gen_random_uuid(), 'HYPOTHEQUE',
         'Hypotheques bancaires - WF8',
         'De la demande a l inscription hypothecaire officielle',
         10, true, true, true),
        (gen_random_uuid(), 'MISE_SOUS_LITIGE',
         'Mise sous litige avec gel - WF25',
         'Du depot de plainte au gel judiciaire de la parcelle',
         5, true, false, true),
        (gen_random_uuid(), 'LEVEE_LITIGE',
         'Levee de litige - WF26',
         'De la decision du tribunal au degele de la parcelle',
         10, true, false, true)
        ON CONFLICT (type_workflow) DO UPDATE SET
            is_active = TRUE,
            annf_auto = EXCLUDED.annf_auto;
    """)

    op.execute("""
        DO $$
        DECLARE v_nb INT;
        BEGIN
            SELECT COUNT(*) INTO v_nb FROM workflow_definition WHERE is_active=TRUE;
            RAISE NOTICE '028 OK - % workflow_definition actifs au total', v_nb;
        END $$;
    """)


def downgrade() -> None:
    for typ in ('RNAF','RNP','CCFM','BGU','ARCHIVAGE_DEFINITIF',
                'ANNULATION_JUDICIAIRE','MUTATION_VENTE',
                'SUCCESSION_FONCIERE','HYPOTHEQUE',
                'MISE_SOUS_LITIGE','LEVEE_LITIGE'):
        op.execute(
            f"DELETE FROM workflow_definition WHERE type_workflow='{typ}';"
        )
