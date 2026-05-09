"""027_workflow_form_schemas

FONCIER+ — Migration 027 : Schémas de formulaires pour les 14 workflows principaux

Chaque étape de workflow dispose maintenant d'un JSON Schema décrivant
les champs du formulaire, les champs obligatoires et les règles de validation.
Ces schémas sont utilisés par le frontend pour construire dynamiquement
les formulaires de chaque étape.

Revision ID: 027_workflow_form_schemas
Revises: 026_messagerie_notariale
"""
import json
from alembic import op

revision      = "027_workflow_form_schemas"
down_revision = "026_messagerie_notariale"
branch_labels = None
depends_on    = None


# Schémas JSON par workflow / étape
FORM_SCHEMAS = [
    # ── RNAF (WF Urbanisme) ───────────────────────────────────────
    ("RNAF", "REDACTION", 1, {
        "type":"object","title":"Rédaction de l\'arrêté foncier",
        "properties":{
            "numero_arrete":{"type":"string","minLength":3,"description":"Référence officielle"},
            "date_arrete":{"type":"string","format":"date"},
            "objet":{"type":"string","minLength":20},
            "zone_concernee":{"type":"string"},
            "region_id":{"type":"string","format":"uuid"},
            "commune_id":{"type":"string","format":"uuid"},
        },
        "required":["numero_arrete","date_arrete","objet","region_id"]
    }, ["numero_arrete","date_arrete","objet"]),
    ("RNAF", "VERIFICATION", 2, {
        "type":"object","title":"Vérification juridique RNAF",
        "properties":{
            "conforme_loi":{"type":"boolean","description":"Conforme à la loi foncière du Niger"},
            "observations":{"type":"string"},
            "parcelles_concernees":{"type":"array","items":{"type":"string","format":"uuid"}},
        },
        "required":["conforme_loi"]
    }, ["conforme_loi"]),
    ("RNAF", "SCELLEMENT", 5, {
        "type":"object","title":"Scellement RNAF",
        "properties":{
            "motif_scellement":{"type":"string"},
            "sha256_document":{"type":"string","minLength":64,"maxLength":64},
        },
        "required":["sha256_document"]
    }, ["sha256_document"]),

    # ── CCFM ──────────────────────────────────────────────────────
    ("CCFM", "RECEPTION", 1, {
        "type":"object","title":"Réception de la demande CCFM",
        "properties":{
            "nus":{"type":"string"},
            "parcelle_id":{"type":"string","format":"uuid"},
            "demandeur_nom":{"type":"string","minLength":2},
            "demandeur_cni":{"type":"string","minLength":3},
            "superficie_m2":{"type":"number","minimum":1},
        },
        "required":["parcelle_id","demandeur_nom","demandeur_cni"]
    }, ["parcelle_id","demandeur_nom","demandeur_cni"]),
    ("CCFM", "VERIFICATION_ADMIN", 2, {
        "type":"object","title":"Vérification administrative CCFM",
        "properties":{
            "dossier_complet":{"type":"boolean"},
            "observations":{"type":"string"},
            "pièces_manquantes":{"type":"array","items":{"type":"string"}},
        },
        "required":["dossier_complet"]
    }, ["dossier_complet"]),
    ("CCFM", "SCELLEMENT", 5, {
        "type":"object","title":"Scellement certificat CCFM",
        "properties":{
            "sha256_certificat":{"type":"string"},
            "qr_code_url":{"type":"string"},
            "motif_scellement":{"type":"string"},
        },
        "required":["sha256_certificat"]
    }, ["sha256_certificat"]),

    # ── WF8 HYPOTHEQUE ────────────────────────────────────────────
    ("WF8", "demande", 1, {
        "type":"object","title":"Demande d\'hypothèque bancaire",
        "properties":{
            "parcelle_id":{"type":"string","format":"uuid"},
            "banque_id":{"type":"string","format":"uuid"},
            "montant_fcfa":{"type":"number","minimum":1},
            "duree_mois":{"type":"integer","minimum":1,"maximum":360},
            "taux_interet":{"type":"number","minimum":0,"maximum":30},
            "reference_contrat":{"type":"string","minLength":3},
            "nus_ccfm":{"type":"string","description":"NUS du certificat CCFM valide"},
        },
        "required":["parcelle_id","banque_id","montant_fcfa","duree_mois","reference_contrat"]
    }, ["parcelle_id","banque_id","montant_fcfa","duree_mois"]),
    ("WF8", "signature", 4, {
        "type":"object","title":"Signature acte hypothécaire",
        "properties":{
            "acte_hypotheque_ref":{"type":"string"},
            "sha256_acte":{"type":"string","minLength":64,"maxLength":64},
            "conditions_particulieres":{"type":"string"},
        },
        "required":["acte_hypotheque_ref","sha256_acte"]
    }, ["acte_hypotheque_ref","sha256_acte"]),

    # ── WF11 EXPROPRIATION ────────────────────────────────────────
    ("WF11", "declaration_up", 1, {
        "type":"object","title":"Déclaration d\'Utilité Publique",
        "properties":{
            "nom_projet":{"type":"string","minLength":5},
            "type_projet":{"type":"string","enum":["route","barrage","ecole","hopital","infrastructure_autre"]},
            "arrete_dut_ref":{"type":"string","minLength":3},
            "date_dut":{"type":"string","format":"date"},
            "budget_fcfa":{"type":"number","minimum":0},
            "parcelles_concernees":{"type":"array","items":{"type":"string","format":"uuid"}},
        },
        "required":["nom_projet","type_projet","arrete_dut_ref","date_dut"]
    }, ["nom_projet","type_projet","arrete_dut_ref","date_dut"]),
    ("WF11", "indemnisation", 3, {
        "type":"object","title":"Évaluation et indemnisation",
        "properties":{
            "valeur_officielle_fcfa":{"type":"number","minimum":1},
            "indemnite_proposee_fcfa":{"type":"number","minimum":1},
            "expert_evaluateur":{"type":"string"},
            "indemnite_acceptee":{"type":"boolean"},
            "motif_refus":{"type":"string"},
        },
        "required":["valeur_officielle_fcfa","indemnite_proposee_fcfa"]
    }, ["valeur_officielle_fcfa","indemnite_proposee_fcfa"]),

    # ── WF12 REGULARISATION ───────────────────────────────────────
    ("WF12", "demande", 1, {
        "type":"object","title":"Demande de régularisation foncière",
        "properties":{
            "occupant_id":{"type":"string","format":"uuid"},
            "surface_m2":{"type":"number","minimum":1},
            "date_occupation":{"type":"string","format":"date"},
            "justificatifs":{"type":"array","items":{"type":"string"},"minItems":1},
            "geom_wkt":{"type":"string","description":"WKT de la parcelle à régulariser"},
        },
        "required":["occupant_id","surface_m2","date_occupation"]
    }, ["occupant_id","surface_m2","date_occupation"]),
    ("WF12", "constat_terrain", 2, {
        "type":"object","title":"Constat terrain topographe",
        "properties":{
            "occupation_confirmee":{"type":"boolean"},
            "conflit_detecte":{"type":"boolean"},
            "observations":{"type":"string"},
            "recommandation":{"type":"string","enum":["valider","rejeter","approfondir"]},
            "sha256_rapport":{"type":"string"},
        },
        "required":["occupation_confirmee","recommandation"]
    }, ["occupation_confirmee","recommandation"]),

    # ── WF17 MUTATION VENTE ───────────────────────────────────────
    ("WF17", "depot_mutation", 1, {
        "type":"object","title":"Dépôt dossier de mutation",
        "properties":{
            "parcelle_id":{"type":"string","format":"uuid"},
            "rnp_parcelle_id":{"type":"string","format":"uuid"},
            "type_mutation":{"type":"string","enum":["vente","donation","heritage","cession","echange"]},
            "cedant_id":{"type":"string","format":"uuid"},
            "acquereur_id":{"type":"string","format":"uuid"},
            "pourcentage_cede":{"type":"number","minimum":0.01,"maximum":100},
            "prix_fcfa":{"type":"number","minimum":0},
            "nus_ccfm":{"type":"string"},
        },
        "required":["parcelle_id","type_mutation","cedant_id","acquereur_id","pourcentage_cede"]
    }, ["parcelle_id","type_mutation","cedant_id","acquereur_id"]),
    ("WF17", "verif_juridique", 2, {
        "type":"object","title":"Vérification juridique CCFM gate",
        "properties":{
            "ccfm_gate_ok":{"type":"boolean"},
            "nus_ccfm":{"type":"string"},
            "observations":{"type":"string"},
        },
        "required":["ccfm_gate_ok","nus_ccfm"]
    }, ["ccfm_gate_ok","nus_ccfm"]),
    ("WF17", "validation_finale", 4, {
        "type":"object","title":"Validation finale cadastre",
        "properties":{
            "numero_acte":{"type":"string"},
            "sha256_acte":{"type":"string","minLength":64},
            "date_acte":{"type":"string","format":"date"},
            "notaire_id":{"type":"string","format":"uuid"},
        },
        "required":["numero_acte","sha256_acte"]
    }, ["numero_acte","sha256_acte"]),

    # ── WF19 SUCCESSION ───────────────────────────────────────────
    ("WF19", "declaration_deces", 1, {
        "type":"object","title":"Déclaration de décès",
        "properties":{
            "de_cujus_id":{"type":"string","format":"uuid"},
            "date_deces":{"type":"string","format":"date"},
            "type_succession":{"type":"string","enum":["testamentaire","ab_intestat","coutumiere","judiciaire","mixte"]},
            "acte_deces_ref":{"type":"string"},
        },
        "required":["de_cujus_id","date_deces","type_succession"]
    }, ["de_cujus_id","date_deces","type_succession"]),
    ("WF19", "identification_heritiers", 2, {
        "type":"object","title":"Identification des héritiers",
        "properties":{
            "heritiers":{"type":"array","minItems":1,"items":{
                "type":"object","properties":{
                    "heritier_id":{"type":"string","format":"uuid"},
                    "lien_parente":{"type":"string","enum":["conjoint","enfant","parent","frere_soeur","autre"]},
                    "part_pct":{"type":"number","minimum":0.01,"maximum":100},
                },
                "required":["heritier_id","lien_parente","part_pct"]
            }},
            "somme_parts_validee":{"type":"boolean","const":True},
        },
        "required":["heritiers","somme_parts_validee"]
    }, ["heritiers","somme_parts_validee"]),

    # ── WF25 LITIGE ───────────────────────────────────────────────
    ("WF25", "depot_plainte", 1, {
        "type":"object","title":"Dépôt de plainte foncière",
        "properties":{
            "parcelle_id":{"type":"string","format":"uuid"},
            "type_litige":{"type":"string","enum":["limite","propriete","heritage","fraude","occupation"]},
            "autorite_saisie":{"type":"string","enum":["TGI","PREFET","MAIRIE"]},
            "description":{"type":"string","minLength":20},
            "plaignant_id":{"type":"string","format":"uuid"},
            "defendeur_id":{"type":"string","format":"uuid"},
            "greffe_id":{"type":"string"},
            "pieces_jointes":{"type":"array","items":{"type":"string"}},
        },
        "required":["parcelle_id","type_litige","autorite_saisie","description","greffe_id"]
    }, ["parcelle_id","type_litige","autorite_saisie","description"]),
    ("WF25", "enregistrement", 2, {
        "type":"object","title":"Enregistrement au greffe",
        "properties":{
            "numero_registre":{"type":"string"},
            "date_enregistrement":{"type":"string","format":"date"},
            "greffe_id":{"type":"string"},
            "observations":{"type":"string"},
        },
        "required":["numero_registre","date_enregistrement"]
    }, ["numero_registre","date_enregistrement"]),

    # ── WF26 LEVÉE LITIGE ─────────────────────────────────────────
    ("WF26", "decision_tribunal", 1, {
        "type":"object","title":"Décision du tribunal",
        "properties":{
            "litige_id":{"type":"string","format":"uuid"},
            "num_jugement":{"type":"string","minLength":3},
            "dispositif":{"type":"string","minLength":10},
            "type_decision":{"type":"string","enum":["mediation_reussie","jugement","classement","accord_amiable"]},
            "beneficiaire_id":{"type":"string","format":"uuid"},
            "sha256_jugement":{"type":"string"},
        },
        "required":["litige_id","num_jugement","dispositif","type_decision"]
    }, ["litige_id","num_jugement","dispositif"]),

    # ── WF27 ARCHIVAGE WORM ───────────────────────────────────────
    ("WF27", "detection_inactivite", 1, {
        "type":"object","title":"Détection d\'entité à archiver",
        "properties":{
            "entite_id":{"type":"string","format":"uuid"},
            "entite_type":{"type":"string","enum":["rnaf","rnp","ccfm","acte_notarie","decision_justice","bgu_scelle","plan_cadastral"]},
            "entite_table":{"type":"string"},
            "raison_archivage":{"type":"string","enum":["inactivite_12_mois","fin_procedure","archivage_periodique"]},
            "snapshot":{"type":"object"},
        },
        "required":["entite_id","entite_type","entite_table"]
    }, ["entite_id","entite_type","entite_table"]),
    ("WF27", "scellement_worm", 4, {
        "type":"object","title":"Scellement WORM définitif",
        "properties":{
            "ida":{"type":"string"},
            "sha256_snapshot":{"type":"string","minLength":64},
            "confirmation_worm":{"type":"boolean","const":True,"description":"Confirmer : cette action est IRRÉVERSIBLE"},
        },
        "required":["ida","sha256_snapshot","confirmation_worm"]
    }, ["ida","sha256_snapshot","confirmation_worm"]),

    # ── BGU ───────────────────────────────────────────────────────
    ("BGU", "IMPORT_GEOM", 1, {
        "type":"object","title":"Import géométries sources",
        "properties":{
            "parcelle_id":{"type":"string","format":"uuid"},
            "geom_wkt":{"type":"string","description":"Géométrie WKT POLYGON SRID 4326"},
            "source_document":{"type":"string"},
            "sha256_source":{"type":"string"},
        },
        "required":["parcelle_id","geom_wkt"]
    }, ["parcelle_id","geom_wkt"]),
    ("BGU", "SCELLEMENT", 5, {
        "type":"object","title":"Scellement BGU officiel",
        "properties":{
            "sha256_geom":{"type":"string","minLength":64,"maxLength":64},
            "blockchain_hash":{"type":"string"},
            "blockchain_network":{"type":"string","default":"internal"},
            "confirmation":{"type":"boolean","const":True},
        },
        "required":["sha256_geom","confirmation"]
    }, ["sha256_geom","confirmation"]),

    # ── WF6 DIVISION PARCELLAIRE ──────────────────────────────────
    ("WF6", "depot_plan", 1, {
        "type":"object","title":"Depot plan de division",
        "properties":{
            "parcelle_mere_id":{"type":"string","format":"uuid"},
            "demandeur_id":{"type":"string","format":"uuid"},
            "nb_lots_cibles":{"type":"integer","minimum":2,"maximum":50},
            "motif":{"type":"string","minLength":5},
            "plan_topographique_ref":{"type":"string"},
        },
        "required":["parcelle_mere_id","demandeur_id","nb_lots_cibles","motif"]
    }, ["parcelle_mere_id","demandeur_id","nb_lots_cibles"]),
    ("WF6", "bornage_terrain", 3, {
        "type":"object","title":"Proces-verbal de bornage",
        "properties":{
            "topographe_id":{"type":"string","format":"uuid"},
            "date_bornage":{"type":"string","format":"date"},
            "lots_count":{"type":"integer","minimum":2},
            "surface_totale_coherente":{"type":"boolean"},
            "sha256_pv":{"type":"string"},
        },
        "required":["topographe_id","date_bornage","surface_totale_coherente"]
    }, ["topographe_id","date_bornage","surface_totale_coherente"]),

    # ── WF7 SERVITUDES ────────────────────────────────────────────
    ("WF7", "declaration", 1, {
        "type":"object","title":"Declaration de servitude",
        "properties":{
            "parcelle_id":{"type":"string","format":"uuid"},
            "type_servitude":{"type":"string",
                "enum":["passage","vue","ecoulement","electricite","canalisations","autre"]},
            "description":{"type":"string","minLength":10},
            "date_debut":{"type":"string","format":"date"},
            "acte_reference":{"type":"string"},
        },
        "required":["parcelle_id","type_servitude","description","date_debut"]
    }, ["parcelle_id","type_servitude","description","date_debut"]),
    ("WF7", "verification", 2, {
        "type":"object","title":"Verification juridique servitude",
        "properties":{
            "conforme":{"type":"boolean"},
            "impact_propriete":{"type":"string","enum":["mineur","modere","majeur"]},
            "accord_proprietaire":{"type":"boolean"},
            "observations":{"type":"string"},
        },
        "required":["conforme","impact_propriete","accord_proprietaire"]
    }, ["conforme","impact_propriete","accord_proprietaire"]),

    # ── WF9 SUCCESSION TECHNIQUE ──────────────────────────────────
    ("WF9", "declaration", 1, {
        "type":"object","title":"Declaration de succession technique",
        "properties":{
            "de_cujus_id":{"type":"string","format":"uuid"},
            "date_deces":{"type":"string","format":"date"},
            "acte_deces_ref":{"type":"string","minLength":3},
            "type_succession":{"type":"string",
                "enum":["testamentaire","ab_intestat","coutumiere","judiciaire","mixte"]},
        },
        "required":["de_cujus_id","date_deces","type_succession"]
    }, ["de_cujus_id","date_deces","type_succession"]),
    ("WF9", "heritiers", 2, {
        "type":"object","title":"Identification heritiers et parts",
        "properties":{
            "nb_heritiers":{"type":"integer","minimum":1},
            "somme_parts_pct":{"type":"number","minimum":99.9,"maximum":100.1},
            "somme_validee_100":{"type":"boolean","const":True},
        },
        "required":["nb_heritiers","somme_validee_100"]
    }, ["nb_heritiers","somme_validee_100"]),

    # ── WF10 CHANGEMENT USAGE ─────────────────────────────────────
    ("WF10", "depot", 1, {
        "type":"object","title":"Demande de changement d usage",
        "properties":{
            "parcelle_id":{"type":"string","format":"uuid"},
            "usage_actuel":{"type":"string",
                "enum":["residentiel","commercial","industriel","agricole","mixte","autre"]},
            "usage_demande":{"type":"string",
                "enum":["residentiel","commercial","industriel","agricole","mixte",
                        "equipement_public","espace_vert","autre"]},
            "motif":{"type":"string","minLength":20},
        },
        "required":["parcelle_id","usage_actuel","usage_demande","motif"]
    }, ["parcelle_id","usage_actuel","usage_demande","motif"]),
    ("WF10", "plan_urbanisme", 2, {
        "type":"object","title":"Avis plan urbanisme",
        "properties":{
            "conforme_plan":{"type":"boolean"},
            "avis_technique":{"type":"string",
                "enum":["favorable","favorable_avec_reserves","defavorable"]},
            "conditions":{"type":"string"},
        },
        "required":["conforme_plan","avis_technique"]
    }, ["conforme_plan","avis_technique"]),

    # ── WF18 DONATION ─────────────────────────────────────────────
    ("WF18", "acte_donation", 1, {
        "type":"object","title":"Acte de donation fonciere",
        "properties":{
            "parcelle_id":{"type":"string","format":"uuid"},
            "donateur_id":{"type":"string","format":"uuid"},
            "beneficiaire_id":{"type":"string","format":"uuid"},
            "pourcentage_donne":{"type":"number","minimum":0.01,"maximum":100},
            "type_donation":{"type":"string",
                "enum":["simple","avec_charge","entre_vifs","testamentaire"]},
            "valeur_estimee_fcfa":{"type":"number","minimum":0},
        },
        "required":["parcelle_id","donateur_id","beneficiaire_id",
                    "pourcentage_donne","type_donation"]
    }, ["parcelle_id","donateur_id","beneficiaire_id","pourcentage_donne"]),
    ("WF18", "consentement", 2, {
        "type":"object","title":"Consentement et verifications legales",
        "properties":{
            "consentement_donateur":{"type":"boolean","const":True},
            "absence_hypotheque":{"type":"boolean"},
            "absence_litige":{"type":"boolean"},
        },
        "required":["consentement_donateur","absence_hypotheque","absence_litige"]
    }, ["consentement_donateur","absence_hypotheque","absence_litige"]),

    # ── WF23 RECTIFICATION CCFM ───────────────────────────────────
    ("WF23", "depot_rectification", 1, {
        "type":"object","title":"Demande de rectification CCFM",
        "properties":{
            "nus_a_rectifier":{"type":"string"},
            "champ_a_corriger":{"type":"string",
                "enum":["surface","usage","identite_titulaire","limites","autre"]},
            "ancienne_valeur":{"type":"string","minLength":1},
            "nouvelle_valeur":{"type":"string","minLength":1},
            "justification":{"type":"string","minLength":20},
        },
        "required":["nus_a_rectifier","champ_a_corriger",
                    "nouvelle_valeur","justification"]
    }, ["nus_a_rectifier","champ_a_corriger","nouvelle_valeur","justification"]),
    ("WF23", "double_validation", 2, {
        "type":"object","title":"Double validation CCFM + Cadastre",
        "properties":{
            "validation_chef_ccfm":{"type":"boolean","const":True},
            "validation_chef_cadastre":{"type":"boolean","const":True},
            "observations":{"type":"string"},
        },
        "required":["validation_chef_ccfm","validation_chef_cadastre"]
    }, ["validation_chef_ccfm","validation_chef_cadastre"]),
]


def upgrade() -> None:
    for wf_code, etape, ordre, schema, required in FORM_SCHEMAS:
        schema_str = json.dumps(schema, ensure_ascii=False).replace("'", "''")
        required_str = json.dumps(required).replace("'", "''")
        op.execute(f"""
            INSERT INTO workflow_form_schema
                (id, workflow_code, etape, json_schema, required_fields,
                 validation_rules, version, is_active)
            VALUES
                (gen_random_uuid(),
                 '{wf_code}', '{etape}',
                 '{schema_str}'::jsonb,
                 '{required_str}'::varchar[],
                 '{{}}'::jsonb,
                 1, TRUE)
            ON CONFLICT (workflow_code, etape, version) DO UPDATE SET
                json_schema     = EXCLUDED.json_schema,
                required_fields = EXCLUDED.required_fields,
                is_active       = TRUE;
        """)

    # Vérification
    op.execute("""
        DO $$
        DECLARE v_nb INT;
        BEGIN
            SELECT COUNT(*) INTO v_nb FROM workflow_form_schema WHERE is_active=TRUE;
            RAISE NOTICE '027 OK — % schémas de formulaires créés', v_nb;
        END $$;
    """)


def downgrade() -> None:
    for wf_code, etape, *_ in FORM_SCHEMAS:
        op.execute(f"""
            DELETE FROM workflow_form_schema
            WHERE workflow_code='{wf_code}' AND etape='{etape}';
        """)
