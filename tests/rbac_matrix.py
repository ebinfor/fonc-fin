"""
FONCIER+ v3.4.7 — Matrice RBAC officielle
==========================================

Source de vérité unique pour les tests E2E rôles × workflows.
Couvre les 29 rôles RBAC et les 33 workflows nationaux.

Pattern : hiérarchie à 4 niveaux par module (ADMIN_X, DIRECTEUR_X, CHEF_X, AGENT_X)
observée dans le backend FONCIER+ v3.4.7 (migration 006).

Légende des permissions :
    'L'  = Launch (peut lancer/initier le workflow)
    'V'  = Validate (peut valider une étape du workflow)
    'R'  = Reject (peut rejeter)
    'S'  = Sign (peut signer électroniquement la décision finale)
    'C'  = Consult (lecture seule, aucune action)
    '-'  = Accès refusé (doit lever HTTP 403)
"""

# ═══════════════════════════════════════════════════════════════════
# LES 29 RÔLES RBAC — déduits du pattern à 4 niveaux × 12 modules
# ═══════════════════════════════════════════════════════════════════
ROLES = [
    # Niveau national (2)
    "ADMIN",                    # Super-administrateur Ministère
    "MINISTRE_URBANISME",       # Signature ministérielle

    # Module Cadastre (6) — hiérarchie + sous-module RNP
    "ADMIN_CADASTRE",
    "DIRECTEUR_CADASTRE",
    "CHEF_SERVICE_CADASTRE",
    "GEOMETRE",
    "TOPOGRAPHE",
    "SECRETARIAT_CADASTRE",

    # Module Urbanisme (3) — inclut sous-module RNAF
    "DIRECTEUR_URBANISME",
    "CHEF_URBANISME",
    "AGENT_URBANISME",          # traite aussi RNAF

    # Module Commune (3) — inclut sous-modules DAD et DAR
    "ADMIN_COMMUNE",
    "MAIRE",
    "AGENT_COMMUNE",            # traite aussi DAD/DAR

    # Module CCFM (2)
    "CHEF_CCFM",
    "AGENT_CCFM",

    # Module Notaire (1)
    "NOTAIRE",

    # Module Banque (2)
    "BANQ_DIRECTEUR",
    "BANQ_AGENT",

    # Module Justice (3)
    "JUGE_FONCIER",
    "GREFFIER",
    "HUISSIER",

    # Module Domaine (2)
    "DIRECTEUR_DOMAINE",
    "AGENT_DOMAINE",

    # Module Journal Officiel (1)
    "EDITEUR_JO",

    # Module BGU (1)
    "RESPONSABLE_BGU",

    # Module Audit + ANNF (3)
    "AUDITEUR",
    "ARCHIVISTE_ANNF",
    "RESPONSABLE_ANNF",
]
assert len(ROLES) == 29, f"Attendu 29 rôles, trouvé {len(ROLES)}"

# ═══════════════════════════════════════════════════════════════════
# MATRICE COMPLÈTE : 33 WORKFLOWS × 29 RÔLES
# ═══════════════════════════════════════════════════════════════════
# Format : WORKFLOWS[wf_code] = {
#     "nom": str,
#     "module": str,
#     "etapes": [str, ...],           # étapes officielles
#     "permissions": {role: code}     # pour chaque rôle, code L/V/R/S/C/-
# }
# Par défaut si un rôle absent de permissions : "-" (refusé)
# ═══════════════════════════════════════════════════════════════════

def _default_perms(overrides):
    """Construit un dict de permissions avec '-' par défaut pour tous les rôles."""
    perms = {r: "-" for r in ROLES}
    # ADMIN et AUDITEUR ont toujours au moins la consultation
    perms["ADMIN"] = "V"
    perms["AUDITEUR"] = "C"
    perms.update(overrides)
    return perms


WORKFLOWS = {
    # ─── BLOC I — Workflows de base (WF1 → WF5) ───
    "WF1": {
        "nom": "RNAF — Registre National d'Affectation Foncière",
        "module": "Urbanisme",
        "etapes": ["Dépôt", "Instruction", "Validation", "Publication JO", "Scellement"],
        "permissions": _default_perms({
            "AGENT_URBANISME":      "L",
            "CHEF_URBANISME":       "V",
            "DIRECTEUR_URBANISME":  "S",
            "MINISTRE_URBANISME":   "S",
            "EDITEUR_JO":           "V",    # publication
            "AGENT_COMMUNE":        "C",
        }),
    },
    "WF2": {
        "nom": "RNP — Registre National Parcellaire",
        "module": "Cadastre",
        "etapes": ["Demande RNP", "Vérification RNAF", "Création NICAD", "Validation"],
        "permissions": _default_perms({
            "GEOMETRE":             "L",
            "TOPOGRAPHE":           "V",
            "CHEF_SERVICE_CADASTRE": "V",
            "DIRECTEUR_CADASTRE":   "S",
            "ADMIN_CADASTRE":       "V",
            "SECRETARIAT_CADASTRE": "C",
            "RESPONSABLE_BGU":      "C",
        }),
    },
    "WF3": {
        "nom": "CCFM — Émission du certificat",
        "module": "CCFM",
        "etapes": ["Demande", "Constat terrain", "Contrôles F1-F7", "Signature", "Remise"],
        "permissions": _default_perms({
            "AGENT_CCFM":           "L",
            "TOPOGRAPHE":           "V",    # constat
            "CHEF_CCFM":            "S",    # signature finale
            "NOTAIRE":              "C",    # consultation avant acte
            "BANQ_AGENT":           "C",
            "BANQ_DIRECTEUR":       "C",
        }),
    },
    "WF4": {
        "nom": "Notaire — Authentification d'acte",
        "module": "Notaire",
        "etapes": ["Vérification CCFM", "Gate 6 contrôles", "Rédaction", "Signature PIN", "Archivage"],
        "permissions": _default_perms({
            "NOTAIRE":              "S",    # le notaire signe lui-même
            "ARCHIVISTE_ANNF":      "V",
        }),
    },
    "WF5": {
        "nom": "Justice — Procédure judiciaire foncière",
        "module": "Justice",
        "etapes": ["Saisine", "Instruction", "Jugement", "Greffe", "Exécution"],
        "permissions": _default_perms({
            "HUISSIER":             "L",
            "GREFFIER":             "V",
            "JUGE_FONCIER":         "S",
        }),
    },

    # ─── BLOC II — Opérations courantes (WF6 → WF15) ───
    "WF6": {
        "nom": "Division (variante technique)",
        "module": "Cadastre",
        "etapes": ["Demande", "Conformité urbanisme", "Tracé", "NICAD filles", "Titres"],
        "permissions": _default_perms({
            "GEOMETRE":             "L",
            "CHEF_SERVICE_CADASTRE": "V",
            "DIRECTEUR_CADASTRE":   "S",
            "CHEF_URBANISME":       "V",    # conformité urbanisme
        }),
    },
    "WF7": {
        "nom": "Servitudes foncières",
        "module": "Cadastre",
        "etapes": ["Déclaration", "Vérification", "Enregistrement", "Publication"],
        "permissions": _default_perms({
            "GEOMETRE":             "L",
            "CHEF_SERVICE_CADASTRE": "V",
            "DIRECTEUR_CADASTRE":   "S",
            "NOTAIRE":              "C",
        }),
    },
    "WF8": {
        "nom": "Hypothèques (variante technique)",
        "module": "Banque",
        "etapes": ["Demande", "Vérification parcelle", "Inscription", "Notification"],
        "permissions": _default_perms({
            "BANQ_AGENT":           "L",
            "BANQ_DIRECTEUR":       "S",
            "NOTAIRE":              "V",
        }),
    },
    "WF9": {
        "nom": "Succession (variante technique)",
        "module": "Notaire",
        "etapes": ["Déclaration", "Héritiers", "Partage", "Titres"],
        "permissions": _default_perms({
            "NOTAIRE":              "L",
            "JUGE_FONCIER":         "V",
            "CHEF_SERVICE_CADASTRE": "V",
        }),
    },
    "WF10": {
        "nom": "Changement d'usage (opérationnel)",
        "module": "Urbanisme",
        "etapes": ["Demande", "Plan urbanisme", "Décision"],
        "permissions": _default_perms({
            "AGENT_URBANISME":      "L",
            "CHEF_URBANISME":       "V",
            "DIRECTEUR_URBANISME":  "S",
        }),
    },
    "WF11": {
        "nom": "Expropriation pour utilité publique",
        "module": "Urbanisme",
        "etapes": ["Déclaration UP", "Enquête", "Indemnisation", "Transfert", "Arrêté"],
        "permissions": _default_perms({
            "DIRECTEUR_URBANISME":  "L",
            "MINISTRE_URBANISME":   "S",
            "JUGE_FONCIER":         "V",
            "DIRECTEUR_DOMAINE":    "V",
            "AGENT_DOMAINE":        "C",
        }),
    },
    "WF12": {
        "nom": "Régularisation foncière",
        "module": "Commune",
        "etapes": ["Demande", "Constat", "Instruction", "Décision", "Titre"],
        "permissions": _default_perms({
            "AGENT_COMMUNE":        "L",
            "MAIRE":                "V",
            "ADMIN_COMMUNE":        "S",
            "TOPOGRAPHE":           "V",
            "DIRECTEUR_CADASTRE":   "V",
        }),
    },
    "WF13": {
        "nom": "Gestion des conflits fonciers",
        "module": "Justice",
        "etapes": ["Signalement", "Conciliation", "Décision", "Exécution"],
        "permissions": _default_perms({
            "MAIRE":                "L",
            "HUISSIER":             "V",
            "JUGE_FONCIER":         "S",
            "GREFFIER":             "V",
        }),
    },
    "WF14": {
        "nom": "Opération de lotissement",
        "module": "Urbanisme",
        "etapes": ["Projet", "Approbation", "Arrêté", "Bornage", "Attribution"],
        "permissions": _default_perms({
            "DIRECTEUR_URBANISME":  "L",
            "MINISTRE_URBANISME":   "S",
            "GEOMETRE":             "V",
            "TOPOGRAPHE":           "V",
            "CHEF_SERVICE_CADASTRE": "V",
        }),
    },
    "WF15": {
        "nom": "Zones spéciales (industrielle, minière, protégée)",
        "module": "Urbanisme",
        "etapes": ["Classification", "Arrêté", "Inscription", "Publication"],
        "permissions": _default_perms({
            "DIRECTEUR_URBANISME":  "L",
            "MINISTRE_URBANISME":   "S",
            "EDITEUR_JO":           "V",
        }),
    },

    # ─── BLOC III — Vie juridique du titre (WF16 → WF27) ───
    "WF16": {
        "nom": "Attribution de titre initial",
        "module": "Cadastre",
        "etapes": ["Demande", "Vérification", "Attribution", "Émission"],
        "permissions": _default_perms({
            "MAIRE":                "L",
            "CHEF_SERVICE_CADASTRE": "V",
            "DIRECTEUR_CADASTRE":   "S",
            "NOTAIRE":              "C",
        }),
    },
    "WF17": {
        "nom": "Mutation de propriété (vente)",
        "module": "Notaire",
        "etapes": ["Dépôt mutation", "Vérif juridique", "Vérif financière",
                   "Validation", "Nouveau titre"],
        "permissions": _default_perms({
            "NOTAIRE":              "L",    # dépose la mutation
            "CHEF_CCFM":            "V",    # vérif juridique via CCFM
            "DIRECTEUR_DOMAINE":    "V",    # vérif financière
            "AGENT_DOMAINE":        "V",    # taxes
            "DIRECTEUR_CADASTRE":   "S",    # nouveau titre
            "BANQ_DIRECTEUR":       "C",
            "ARCHIVISTE_ANNF":      "V",
        }),
    },
    "WF18": {
        "nom": "Donation foncière",
        "module": "Notaire",
        "etapes": ["Acte donation", "Consentement", "Héritiers", "Validation", "Titre"],
        "permissions": _default_perms({
            "NOTAIRE":              "L",
            "JUGE_FONCIER":         "V",    # protection héritiers
            "CHEF_SERVICE_CADASTRE": "V",
            "DIRECTEUR_CADASTRE":   "S",
            "ARCHIVISTE_ANNF":      "V",
        }),
    },
    "WF19": {
        "nom": "Succession foncière",
        "module": "Notaire",
        "etapes": ["Décès", "Héritiers", "Répartition", "Validation", "Titres"],
        "permissions": _default_perms({
            "NOTAIRE":              "L",
            "JUGE_FONCIER":         "S",    # jugement succession
            "GREFFIER":             "V",
            "MAIRE":                "V",    # acte de décès
            "CHEF_SERVICE_CADASTRE": "V",
            "ARCHIVISTE_ANNF":      "V",
        }),
    },
    "WF20": {
        "nom": "Fusion de parcelles",
        "module": "Cadastre",
        "etapes": ["Demande", "Compatibilité ST_Touches", "Fusion géo",
                   "Calcul surface", "Nouveau titre"],
        "permissions": _default_perms({
            "GEOMETRE":             "L",
            "TOPOGRAPHE":           "V",
            "CHEF_SERVICE_CADASTRE": "V",
            "DIRECTEUR_CADASTRE":   "S",
            "NOTAIRE":              "V",    # acte de fusion
            "RESPONSABLE_BGU":      "V",
        }),
    },
    "WF21": {
        "nom": "Division de parcelle",
        "module": "Cadastre",
        "etapes": ["Demande", "Conformité urbanisme", "Polygones",
                   "Numéros", "Titres"],
        "permissions": _default_perms({
            "GEOMETRE":             "L",
            "TOPOGRAPHE":           "V",
            "CHEF_SERVICE_CADASTRE": "V",
            "DIRECTEUR_CADASTRE":   "S",
            "CHEF_URBANISME":       "V",
            "RESPONSABLE_BGU":      "V",
        }),
    },
    "WF22": {
        "nom": "Changement d'usage du terrain",
        "module": "Urbanisme",
        "etapes": ["Dépôt", "Plan urbanisme", "Consultation", "Décision", "Statut"],
        "permissions": _default_perms({
            "AGENT_URBANISME":      "L",
            "CHEF_URBANISME":       "V",
            "DIRECTEUR_URBANISME":  "S",
            "MAIRE":                "V",    # consultation commune
            "CHEF_SERVICE_CADASTRE": "V",   # mise à jour statut
        }),
    },
    "WF23": {
        "nom": "Rectification administrative",
        "module": "Cadastre",
        "etapes": ["Demande", "Analyse erreur", "Double validation",
                   "Mise à jour", "Archivage"],
        "permissions": _default_perms({
            "AGENT_CCFM":           "L",
            "CHEF_CCFM":            "V",    # 1ère validation
            "CHEF_SERVICE_CADASTRE": "V",   # 2ème validation (double)
            "DIRECTEUR_CADASTRE":   "S",
            "ARCHIVISTE_ANNF":      "V",
        }),
    },
    "WF24": {
        "nom": "Annulation de titre",
        "module": "Justice",
        "etapes": ["Déclenchement", "Vérification légale", "Annulation",
                   "Statut annulé", "Archivage permanent"],
        "permissions": _default_perms({
            "JUGE_FONCIER":         "L",    # seul le juge déclenche
            "GREFFIER":             "V",
            "HUISSIER":             "V",    # exécution
            "DIRECTEUR_CADASTRE":   "V",    # propagation
            "CHEF_CCFM":            "V",    # révocation CCFM
            "BANQ_DIRECTEUR":       "V",    # mainlevée
            "ARCHIVISTE_ANNF":      "S",    # archivage WORM
            "RESPONSABLE_ANNF":     "S",
        }),
    },
    "WF25": {
        "nom": "Mise sous litige",
        "module": "Justice",
        "etapes": ["Dépôt plainte", "Enregistrement", "Blocage", "Notification"],
        "permissions": _default_perms({
            "HUISSIER":             "L",
            "GREFFIER":             "V",
            "JUGE_FONCIER":         "S",
            "NOTAIRE":              "C",
            "BANQ_DIRECTEUR":       "C",
        }),
    },
    "WF26": {
        "nom": "Levée de litige",
        "module": "Justice",
        "etapes": ["Décision tribunal", "Vérification", "Levée", "Réactivation"],
        "permissions": _default_perms({
            "JUGE_FONCIER":         "L",
            "GREFFIER":             "V",
            "CHEF_SERVICE_CADASTRE": "V",
            "DIRECTEUR_CADASTRE":   "S",
        }),
    },
    "WF27": {
        "nom": "Archivage définitif",
        "module": "ANNF",
        "etapes": ["Détection inactivité", "Transfert archive", "Indexation", "Stockage"],
        "permissions": _default_perms({
            "ARCHIVISTE_ANNF":      "L",
            "RESPONSABLE_ANNF":     "S",
        }),
    },

    # ─── BLOC IV — Stratégiques (WF28 → WF33) ───
    "WF28": {
        "nom": "Litiges enrichis (unification)",
        "module": "Justice",
        "etapes": ["Consolidation", "Matching", "Fusion dossiers"],
        "permissions": _default_perms({
            "JUGE_FONCIER":         "L",
            "GREFFIER":             "V",
            "AUDITEUR":             "V",
        }),
    },
    "WF29": {
        "nom": "Révisions cadastrales massives",
        "module": "Cadastre",
        "etapes": ["Programme", "Campagne terrain", "Mise à jour", "Publication"],
        "permissions": _default_perms({
            "DIRECTEUR_CADASTRE":   "L",
            "ADMIN_CADASTRE":       "V",
            "TOPOGRAPHE":           "V",
            "GEOMETRE":             "V",
            "RESPONSABLE_BGU":      "V",
            "MINISTRE_URBANISME":   "S",
        }),
    },
    "WF30": {
        "nom": "Migration des archives foncières",
        "module": "ANNF",
        "etapes": ["30.1 Collecte", "30.2 Inventaire", "30.3 Numérisation",
                   "30.4 Géoréférencement", "30.4bis SRID", "30.5 Vectorisation",
                   "30.6 NICAD", "30.7 Propriétaires", "30.8 CCFM croisé",
                   "30.9 Conflits", "30.10 BGU + ANNF"],
        "permissions": _default_perms({
            "ARCHIVISTE_ANNF":      "L",    # 30.1, 30.2, 30.3
            "RESPONSABLE_ANNF":     "S",    # 30.10 scellement
            "TOPOGRAPHE":           "V",    # 30.4, 30.4bis, 30.5
            "GEOMETRE":             "V",    # 30.5, 30.6
            "NOTAIRE":              "V",    # 30.7 propriétaires
            "CHEF_CCFM":            "V",    # 30.8 CCFM croisé
            "RESPONSABLE_BGU":      "V",    # 30.10 BGU
            "ADMIN_CADASTRE":       "V",
        }),
    },
    "WF31": {
        "nom": "Alertes et anomalies automatiques",
        "module": "Audit",
        "etapes": ["Détection", "Classification", "Notification", "Résolution"],
        "permissions": _default_perms({
            "AUDITEUR":             "L",
            "ADMIN_CADASTRE":       "V",
            "ADMIN_COMMUNE":        "V",
            "CHEF_CCFM":            "V",
            "DIRECTEUR_CADASTRE":   "V",
            "JUGE_FONCIER":         "V",    # fraude détectée
        }),
    },
    "WF32": {
        "nom": "Indicateurs nationaux (KPIs gouvernance)",
        "module": "Audit",
        "etapes": ["Collecte", "Calcul", "Agrégation", "Publication dashboard"],
        "permissions": _default_perms({
            "AUDITEUR":             "L",
            "ADMIN":                "V",
            "MINISTRE_URBANISME":   "C",    # consulte uniquement
            "DIRECTEUR_CADASTRE":   "C",
            "DIRECTEUR_URBANISME":  "C",
            "DIRECTEUR_DOMAINE":    "C",
        }),
    },
    "WF33": {
        "nom": "Continuité système (sauvegarde/restauration)",
        "module": "Audit",
        "etapes": ["Sauvegarde", "Vérification intégrité", "Restauration", "Validation"],
        "permissions": _default_perms({
            "ADMIN":                "S",    # seul l'admin système peut
            "AUDITEUR":             "V",
            "RESPONSABLE_ANNF":     "V",
        }),
    },
}

assert len(WORKFLOWS) == 33, f"Attendu 33 workflows, trouvé {len(WORKFLOWS)}"


# ═══════════════════════════════════════════════════════════════════
# HELPERS POUR LES TESTS E2E
# ═══════════════════════════════════════════════════════════════════
def roles_autorises(wf_code: str, action: str = "any") -> list[str]:
    """Retourne les rôles ayant le niveau d'accès demandé sur un workflow.

    action peut être 'L' (launch), 'V' (validate), 'S' (sign), 'C' (consult),
    ou 'any' pour tout accès non refusé.
    """
    wf = WORKFLOWS[wf_code]
    if action == "any":
        return [r for r, p in wf["permissions"].items() if p != "-"]
    return [r for r, p in wf["permissions"].items() if p == action]


def roles_refuses(wf_code: str) -> list[str]:
    """Retourne les rôles qui doivent recevoir HTTP 403 sur ce workflow."""
    wf = WORKFLOWS[wf_code]
    return [r for r, p in wf["permissions"].items() if p == "-"]


def matrice_tests_parametres() -> list[tuple]:
    """Génère la liste exhaustive des cas de test paramétrés.

    Retourne une liste de tuples (wf_code, role, action_attendue)
    pour alimenter @pytest.mark.parametrize.

    Total attendu : 33 × 29 = 957 cas minimum.
    """
    cas = []
    for wf_code, wf in WORKFLOWS.items():
        for role in ROLES:
            perm = wf["permissions"].get(role, "-")
            cas.append((wf_code, role, perm))
    return cas


def statistiques() -> dict:
    """Statistiques de couverture de la matrice."""
    total = len(ROLES) * len(WORKFLOWS)
    by_action = {"L": 0, "V": 0, "S": 0, "C": 0, "R": 0, "-": 0}
    for wf in WORKFLOWS.values():
        for role in ROLES:
            perm = wf["permissions"].get(role, "-")
            by_action[perm] = by_action.get(perm, 0) + 1
    return {
        "total_cas": total,
        "nb_roles": len(ROLES),
        "nb_workflows": len(WORKFLOWS),
        "repartition": by_action,
        "cas_autorises": total - by_action["-"],
        "cas_refuses": by_action["-"],
        "couverture_pct": round(100 * (total - by_action["-"]) / total, 1),
    }


if __name__ == "__main__":
    stats = statistiques()
    print("═" * 60)
    print("FONCIER+ v3.4.7 — Matrice RBAC")
    print("═" * 60)
    print(f"Rôles       : {stats['nb_roles']}")
    print(f"Workflows   : {stats['nb_workflows']}")
    print(f"Total cas   : {stats['total_cas']}")
    print(f"Autorisés   : {stats['cas_autorises']}")
    print(f"Refusés     : {stats['cas_refuses']}")
    print(f"Couverture  : {stats['couverture_pct']}%")
    print()
    print("Répartition par niveau d'accès :")
    for action, count in stats["repartition"].items():
        label = {"L": "Lancer", "V": "Valider", "S": "Signer",
                 "C": "Consulter", "R": "Rejeter", "-": "Refusé"}[action]
        print(f"  {action} ({label:10s}) : {count:4d}")
