/**
 * FONCIER+ v3.0 — CCFM Pages secondaires
 * WorkflowPage · CertificatsPage · VerificationPage
 * Workflow 7 étapes — Ticket de réponse visible partout
 */
import { useState, useEffect } from 'react';
// Données principales via useDataFetch (architecture v3.4.7)
// Actions POST/PATCH conservent fetch() direct
import { mockBackend } from '@/services/mockBackend';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { toast } from 'sonner';
import { Search, Download, CheckCircle, XCircle, Loader2, Shield } from 'lucide-react';
import { WORKFLOW_STEPS, ETAT_CONF } from './ccfmConstants';


// Types domaine CCFM
interface DemandeCCFM {
  ccfm_id: string
  nus: string
  nom_prenom: string
  localite: string
  lot: string
  parcelle: string
  superficie_m2: number
  etat: string
  date_enregistrement?: string
  topographe_assigne?: string
  enregistre_par?: string
}

interface EtatCCFM {
  value: string
  label: string
  acteur: string
}

// ════════════════════════════════════════════════════════════════════════════
// WORKFLOW PAGE — Pipeline visuel 7 étapes
// ════════════════════════════════════════════════════════════════════════════

export function CCFMWorkflowPage() {
  return (
    <div className="space-y-6">

      {/* Métriques clés */}
      <Card className="p-6">
        <div className="grid grid-cols-3 gap-4">
          {[
            { label: '7 étapes',      value: '4 acteurs + système',  sub: 'Workflow officiel' },
            { label: '50 000 FCFA',   value: 'Forfaitaire',           sub: 'Validité 2 ans' },
            { label: '8–15 jours',    value: 'Délai officiel',        sub: '15 états intermédiaires' },
          ].map(k => (
            <div key={k.label} className="text-center p-3 bg-orange-50 rounded-lg border border-orange-100">
              <p className="text-lg font-bold text-orange-800">{k.value}</p>
              <p className="text-sm font-semibold text-gray-700">{k.label}</p>
              <p className="text-xs text-gray-500">{k.sub}</p>
            </div>
          ))}
        </div>
      </Card>

      {/* Note Ticket de Réponse */}
      <div className="p-4 bg-cyan-50 border border-cyan-200 rounded-lg text-sm text-cyan-800">
        <strong>🎫 Ticket de Réponse (étape 2) :</strong> Généré automatiquement après vérification RNAF+BGU,
        il contient les résultats de conformité et les <strong>coordonnées GPS officielles de la parcelle</strong>.
        Ce ticket est transmis à la secrétaire, joint à l'ordre de mission du topographe, et inclus
        dans le rapport d'appréciation soumis au Directeur.
      </div>

      {/* Étapes */}
      <div className="space-y-4">
        {WORKFLOW_STEPS.map((step, idx) => (
          <Card key={step.n} className="overflow-hidden">
            <div className={`bg-gradient-to-r ${step.gradient} p-4 flex items-center gap-4`}>
              <div className="w-10 h-10 bg-white/20 rounded-full flex items-center justify-center text-white font-bold text-lg flex-shrink-0">
                {step.n}
              </div>
              <div className="text-2xl">{step.icon}</div>
              <div className="text-white flex-1">
                <p className="text-xs font-bold opacity-80 uppercase tracking-wider">{step.acteur}</p>
                <p className="font-bold">{step.titre}</p>
              </div>
              <div className="text-white text-right flex-shrink-0">
                <p className="text-xs opacity-70">Durée estimée</p>
                <p className="font-bold text-sm">{step.duree}</p>
              </div>
            </div>
            <div className="p-4">
              <ul className="space-y-1.5">
                {step.details.map((d, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-gray-600">
                    <CheckCircle className="h-4 w-4 text-green-500 flex-shrink-0 mt-0.5" />
                    {d}
                  </li>
                ))}
              </ul>

              {/* Encarts spéciaux */}
              {step.n === 2 && (
                <div className="mt-3 p-3 bg-cyan-50 border border-cyan-100 rounded text-xs text-cyan-700">
                  <strong>Ticket de Réponse généré :</strong> RNAF ok/ko · BGU ok/ko ·
                  Coordonnées GPS parcelle · Hash d'intégrité · Transmis à la secrétaire
                </div>
              )}
              {step.n === 3 && (
                <div className="mt-3 p-3 bg-amber-50 border border-amber-100 rounded text-xs text-amber-700">
                  <strong>Contenu de l'ordre de mission :</strong> Identité demandeur · Références parcelle ·
                  <strong> Ticket RNAF/BGU/GPS joint automatiquement</strong> · Instructions d'intervention
                </div>
              )}
              {step.n === 4 && (
                <div className="mt-3 p-3 bg-indigo-50 border border-indigo-100 rounded text-xs text-indigo-700">
                  <strong>Fiche officielle :</strong> Fiche de Vérification Administrative Foncière —
                  Ministère de l'Urbanisme, du Logement et de l'Habitat · 9 sections · Exigible avant tout constat d'huissier
                </div>
              )}
              {step.n === 5 && (
                <div className="mt-3 p-3 bg-purple-50 border border-purple-100 rounded text-xs text-purple-700">
                  <strong>Rapport d'appréciation contient :</strong> Données ticket (RNAF/BGU/GPS ticket) ·
                  Données fiche constat (GPS mesuré, superficie mesurée, décision topographe) ·
                  Analyse des écarts · Verdict global
                </div>
              )}
            </div>
            {idx < WORKFLOW_STEPS.length - 1 && (
              <div className="text-center py-1 text-gray-300 text-sm">↓</div>
            )}
          </Card>
        ))}
      </div>

      {/* Note légale */}
      <Card className="p-4 bg-amber-50 border-amber-200">
        <p className="text-sm text-amber-800">
          <strong>⚠️ Rappel légal :</strong> Le CCFM n'est ni un acte de cession, ni un titre foncier,
          mais une <strong>condition préalable obligatoire</strong> à tout établissement de ces derniers
          et à toute transaction légale.
          Référence : <em>Arrêté n°…/MULH/… — Ministère de l'Urbanisme, du Logement et de l'Habitat</em>
        </p>
      </Card>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// CERTIFICATS PAGE — Liste des CCFM émis
// ════════════════════════════════════════════════════════════════════════════

export function CCFMCertificatsPage() {
  const [demandes, setDemandes] = useState<any[]>([]);
  const [loading, setLoading]   = useState(true);
  const [search, setSearch]     = useState('');

  useEffect(() => {
    mockBackend.getDemandesCCFM({ etat: 'TOUS' })
      .then(d => setDemandes(d))
      .finally(() => setLoading(false));
  }, []);

  const allCerts = demandes.filter(d => ['SIGNE_DIRECTEUR', 'ARCHIVE', 'RETIRE'].includes(d.etat));
  const filtered = search
    ? allCerts.filter(d =>
        d.nus?.includes(search) ||
        d.nom_prenom?.toLowerCase().includes(search.toLowerCase()) ||
        d.reference_ccfm?.includes(search))
    : allCerts;

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <div className="flex gap-3 items-center">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <Input className="pl-9" placeholder="NUS, demandeur, référence CCFM…"
              value={search} onChange={e => setSearch(e.target.value)} />
          </div>
        </div>
      </Card>
      <Card className="overflow-hidden">
        <div className="grid grid-cols-6 bg-gray-50 px-4 py-3 text-xs font-semibold text-gray-500 uppercase border-b">
          <span>NUS</span><span>Référence CCFM</span><span>Bénéficiaire</span>
          <span>Parcelle</span><span>Date signature</span><span>Actions</span>
        </div>
        {loading
          ? <div className="flex justify-center py-8"><Loader2 className="h-6 w-6 animate-spin text-orange-500" /></div>
          : filtered.length === 0
            ? <div className="py-8 text-center text-gray-500">Aucun certificat émis</div>
            : filtered.map(d => (
              <div key={d.ccfm_id} className="grid grid-cols-6 px-4 py-3 border-b items-center hover:bg-gray-50">
                <span className="font-mono text-xs font-bold text-orange-700">{d.nus}</span>
                <span className="font-mono text-xs text-gray-500">{d.reference_ccfm || '—'}</span>
                <span className="text-sm">{d.nom_prenom}</span>
                <span className="text-xs text-gray-500">{d.lot}/{d.parcelle} · {d.localite}</span>
                <span className="text-xs text-gray-500">
                  {d.date_signature?.slice(0, 10) || d.date_enregistrement?.slice(0, 10)}
                </span>
                <div className="flex gap-2 items-center">
                  <Button size="sm" className="bg-orange-600 hover:bg-orange-700 text-xs gap-1"
                    onClick={() => toast.info('Génération PDF — backend requis')}>
                    <Download className="h-3 w-3" />PDF
                  </Button>
                  {d.qr_code && (
                    <Badge className="bg-teal-100 text-teal-700 text-xs">⛓ Chaîne</Badge>
                  )}
                </div>
              </div>
            ))
        }
        <div className="px-4 py-2 bg-gray-50 border-t text-xs text-gray-400">
          {filtered.length} certificat(s) émis
        </div>
      </Card>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// VÉRIFICATION PAGE — Vérification publique d'un CCFM
// ════════════════════════════════════════════════════════════════════════════

export function CCFMVerificationPage() {
  const [nus, setNus]         = useState('');
  const [lot, setLot]         = useState('');
  const [parcelle, setParcelle] = useState('');
  const [result, setResult]   = useState<any>(null);
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState(false);

  const handleVerify = async () => {
    if (!nus && !(lot && parcelle)) {
      toast.error('Saisir NUS ou Lot + Parcelle');
      return;
    }
    setChecking(true); setChecked(false);
    try {
      const res = await mockBackend.verifierCCFM({ nus, lot, parcelle });
      setResult(res); setChecked(true);
    } catch { toast.error('Erreur lors de la vérification'); }
    finally { setChecking(false); }
  };

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <Card className="p-6">
        <div className="flex items-center gap-3 mb-4">
          <Shield className="h-6 w-6 text-orange-600" />
          <div>
            <h3 className="text-lg font-semibold text-gray-800">Vérification publique CCFM</h3>
            <p className="text-xs text-gray-500">
              Tout module doit vérifier le CCFM avant toute transaction foncière.
            </p>
          </div>
        </div>
        <div className="space-y-3">
          <div>
            <label className="text-sm font-medium text-gray-700">Numéro Unique de Suivi (NUS)</label>
            <Input className="mt-1" placeholder="NUS-2026-00001"
              value={nus} onChange={e => setNus(e.target.value)} />
          </div>
          <div className="text-center text-sm text-gray-400">— ou —</div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm font-medium text-gray-700">Lot</label>
              <Input className="mt-1" placeholder="LOT-1234"
                value={lot} onChange={e => setLot(e.target.value)} />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700">Parcelle</label>
              <Input className="mt-1" placeholder="PARC-5678"
                value={parcelle} onChange={e => setParcelle(e.target.value)} />
            </div>
          </div>
          <Button className="w-full bg-orange-600 hover:bg-orange-700 py-5"
            onClick={handleVerify} disabled={checking}>
            {checking ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Search className="h-4 w-4 mr-2" />}
            Vérifier le CCFM
          </Button>
        </div>
      </Card>

      {/* Résultat */}
      {checked && result && (
        <Card className={`p-6 border-2 ${result.valide ? 'border-emerald-400 bg-emerald-50' : 'border-red-400 bg-red-50'}`}>
          <div className="flex items-center gap-3 mb-4">
            {result.valide
              ? <CheckCircle className="h-8 w-8 text-emerald-600" />
              : <XCircle className="h-8 w-8 text-red-600" />}
            <div>
              <p className={`text-lg font-bold ${result.valide ? 'text-emerald-800' : 'text-red-800'}`}>
                {result.valide ? '✅ CCFM VALIDE' : '❌ CCFM INVALIDE'}
              </p>
              <p className="text-sm text-gray-600">Statut : <strong>{result.statut}</strong></p>
            </div>
          </div>
          {result.ccfm && (
            <div className="space-y-2 text-sm">
              <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                <div><span className="text-gray-500">NUS :</span> <span className="font-mono font-bold">{result.ccfm.nus}</span></div>
                <div><span className="text-gray-500">Référence :</span> <span className="font-mono">{result.ccfm.reference_ccfm || '—'}</span></div>
                <div><span className="text-gray-500">Bénéficiaire :</span> <strong>{result.ccfm.nom_prenom}</strong></div>
                <div><span className="text-gray-500">Localité :</span> {result.ccfm.localite}</div>
                <div><span className="text-gray-500">Lot/Parcelle :</span> {result.ccfm.lot}/{result.ccfm.parcelle}</div>
                <div><span className="text-gray-500">Superficie :</span> {result.ccfm.superficie_m2?.toLocaleString('fr-FR')} m²</div>
              </div>
              {result.ccfm.qr_code && (
                <div className="p-2 bg-white border rounded mt-2">
                  <p className="text-xs text-gray-400 mb-1">Hash QR Code</p>
                  <p className="font-mono text-xs break-all">{result.ccfm.qr_code}</p>
                </div>
              )}
              <div className="p-3 bg-white border rounded text-xs text-gray-600 mt-2">
                <p className="font-semibold mb-1">Attestations :</p>
                <p>✅ Parcelle vérifiée RNAF (Registre National des Arrêtés Fonciers)</p>
                <p>✅ Géométrie BGU v4.0.0 validée — aucun chevauchement</p>
                <p>✅ Coordonnées GPS concordantes (ticket ↔ constat terrain)</p>
                <p>✅ Fiche de Vérification Administrative Foncière validée</p>
              </div>
            </div>
          )}
        </Card>
      )}

      {/* Modules consommateurs */}
      <Card className="p-4">
        <p className="text-xs text-gray-500 font-semibold mb-2">Modules consommateurs de la vérification CCFM :</p>
        <div className="flex flex-wrap gap-2">
          {['Urbanisme', 'Cadastre', 'Domaine', 'Titres Fonciers', 'Notaires', 'Banques', 'Transactions'].map(m => (
            <Badge key={m} className="bg-orange-100 text-orange-700 text-xs">{m}</Badge>
          ))}
        </div>
        <p className="text-xs text-gray-400 mt-2">
          Statuts : VALIDE · EXPIRÉ (&gt;2 ans) · RÉVOQUÉ · EN_COURS · INTROUVABLE
        </p>
      </Card>
    </div>
  );
}

export default CCFMWorkflowPage;
