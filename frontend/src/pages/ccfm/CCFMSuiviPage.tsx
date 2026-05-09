/**
 * FONCIER+ v3.0 — CCFM Suivi d'Avancement
 * Stepper visuel 7 étapes · Recherche NUS/nom · Timeline · Mini-progress
 * Aligné sur le workflow officiel avec Ticket de Réponse (étape 2)
 */
import { useState, useEffect } from 'react';
// Données principales via useDataFetch (architecture v3.4.7)
// Actions POST/PATCH conservent fetch() direct
import { mockBackend } from '@/services/mockBackend';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Loader2, Search, ChevronDown, ChevronUp } from 'lucide-react';
import { WORKFLOW_STEPS, ETAT_LABELS } from './ccfmConstants';


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

// ─── Utilitaires stepper ──────────────────────────────────────────────────
function getStepStatus(
  stepEtats: string[],
  currentEtat: string,
): 'done' | 'active' | 'pending' | 'error' {
  if (currentEtat === 'REJETE') return 'error';
  const allEtats = WORKFLOW_STEPS.flatMap(s => s.etats);
  const currentIndex    = allEtats.indexOf(currentEtat);
  const stepStartIndex  = allEtats.indexOf(stepEtats[0]);
  const stepEndIndex    = allEtats.indexOf(stepEtats[stepEtats.length - 1]);
  if (currentIndex < 0) return 'pending';
  if (stepEtats.includes(currentEtat)) return 'active';
  if (currentIndex > stepEndIndex) return 'done';
  return 'pending';
}

function StepperTracker({ dossier }: { dossier: any }) {
  const isRejected = dossier.etat === 'REJETE';
  return (
    <div className="py-4">
      {/* Stepper horizontal */}
      <div className="flex items-start overflow-x-auto pb-2 gap-0">
        {WORKFLOW_STEPS.map((step, i) => {
          const status = isRejected ? 'pending' : getStepStatus(step.etats, dossier.etat);
          const isLast = i === WORKFLOW_STEPS.length - 1;
          return (
            <div key={step.n} className="flex items-start flex-shrink-0">
              <div className="flex flex-col items-center" style={{ width: '84px' }}>
                <div className={`w-9 h-9 rounded-full flex items-center justify-center text-base border-2 transition-all
                  ${status === 'done'   ? 'bg-green-500 border-green-500 text-white' : ''}
                  ${status === 'active' ? 'bg-orange-500 border-orange-500 text-white ring-4 ring-orange-200 animate-pulse' : ''}
                  ${status === 'pending'? 'bg-gray-100 border-gray-300 text-gray-400' : ''}
                  ${status === 'error'  ? 'bg-red-500 border-red-500 text-white' : ''}
                `}>
                  {status === 'done' ? '✓' : step.icon}
                </div>
                <p className={`text-[10px] mt-1 text-center leading-tight font-medium
                  ${status === 'done'   ? 'text-green-700' : ''}
                  ${status === 'active' ? 'text-orange-700 font-bold' : ''}
                  ${status === 'pending'? 'text-gray-400' : ''}
                  ${status === 'error'  ? 'text-red-400' : ''}
                `}>{step.titre.split(' ')[0]}</p>
                <p className={`text-[9px] text-center
                  ${status === 'active' ? 'text-orange-500' : 'text-gray-300'}
                `}>{step.acteur.split(' ')[0]}</p>
              </div>
              {!isLast && (
                <div className={`h-0.5 mt-4 flex-shrink-0 ${status === 'done' ? 'bg-green-400' : 'bg-gray-200'}`}
                  style={{ width: '16px' }} />
              )}
            </div>
          );
        })}
      </div>

      {/* Barre rejet */}
      {isRejected && (
        <div className="mt-3 p-3 bg-red-50 border border-red-200 rounded-lg flex items-center gap-3">
          <span className="text-xl">⛔</span>
          <div>
            <p className="font-bold text-red-800 text-sm">Dossier REJETÉ</p>
            <p className="text-xs text-red-600">Ce dossier ne peut plus avancer dans le workflow.</p>
          </div>
        </div>
      )}

      {/* État actuel */}
      {!isRejected && (
        <div className="mt-3 p-3 bg-orange-50 border border-orange-200 rounded-lg flex items-center gap-3">
          <span className="text-lg">📍</span>
          <div>
            <p className="font-semibold text-orange-800 text-sm">
              {ETAT_LABELS[dossier.etat] || dossier.etat}
            </p>
            <p className="text-xs text-orange-600">
              Acteur en charge : <strong>
                {WORKFLOW_STEPS.find(s => s.etats.includes(dossier.etat))?.acteur || '—'}
              </strong>
            </p>
          </div>
          {/* Ticket GPS disponible */}
          {dossier.latitude && (
            <div className="ml-auto text-xs bg-cyan-100 text-cyan-700 rounded px-2 py-1">
              📍 GPS ticket : {dossier.latitude}, {dossier.longitude}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DossierTimeline({ dossier }: { dossier: any }) {
  const events: { date: string; label: string; icon: string; actor: string }[] = [];

  if (dossier.date_enregistrement)
    events.push({ date: dossier.date_enregistrement, label: 'Demande enregistrée — NUS généré', icon: '🪟', actor: 'Guichetière' });
  if (dossier.date_ticket_emis || ['TICKET_EMIS','ORDRE_MISSION_EMIS','VISITE_EN_COURS','FICHE_CONSTAT_DEPOSEE','RAPPORT_APPRECIATION','ATTENTE_SIGNATURE_DIRECTEUR','SIGNE_DIRECTEUR','ARCHIVE','RETIRE'].includes(dossier.etat))
    events.push({ date: dossier.date_ticket_emis || dossier.date_enregistrement, label: 'Ticket RNAF/BGU/GPS émis', icon: '🎫', actor: 'Système' });
  if (['ORDRE_MISSION_EMIS','ATTENTE_VISITE_TERRAIN','VISITE_EN_COURS','FICHE_CONSTAT_DEPOSEE','RAPPORT_APPRECIATION','ATTENTE_SIGNATURE_DIRECTEUR','SIGNE_DIRECTEUR','ARCHIVE','RETIRE'].includes(dossier.etat))
    events.push({ date: dossier.date_enregistrement, label: 'Ordre de mission émis (ticket joint)', icon: '📤', actor: 'Secrétaire' });
  if (['FICHE_CONSTAT_DEPOSEE','RAPPORT_APPRECIATION','ATTENTE_SIGNATURE_DIRECTEUR','SIGNE_DIRECTEUR','ARCHIVE','RETIRE'].includes(dossier.etat))
    events.push({ date: dossier.date_enregistrement, label: 'Fiche de constat déposée', icon: '📐', actor: 'Topographe' });
  if (['RAPPORT_APPRECIATION','ATTENTE_SIGNATURE_DIRECTEUR','SIGNE_DIRECTEUR','ARCHIVE','RETIRE'].includes(dossier.etat))
    events.push({ date: dossier.date_enregistrement, label: 'Rapport d\'appréciation généré', icon: '📊', actor: 'Système' });
  if (dossier.date_signature)
    events.push({ date: dossier.date_signature, label: 'Certificat signé — PDF disponible', icon: '✅', actor: 'Directeur' });
  if (dossier.date_archivage)
    events.push({ date: dossier.date_archivage, label: 'Archivé ANNF + blockchain', icon: '🏛️', actor: 'Archiviste' });

  return (
    <div className="mt-3 pl-4 border-l-2 border-gray-200 space-y-3">
      {events.map((e, i) => (
        <div key={i} className="flex items-start gap-3 relative">
          <div className="absolute -left-[21px] w-3 h-3 rounded-full bg-green-500 border-2 border-white" />
          <div>
            <p className="text-xs text-gray-500">{new Date(e.date).toLocaleDateString('fr-FR')} · {e.actor}</p>
            <p className="text-sm font-medium text-gray-700">{e.icon} {e.label}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function MiniProgress({ etat }: { etat: string }) {
  const allEtats = WORKFLOW_STEPS.flatMap(s => s.etats);
  const currentIndex = allEtats.indexOf(etat);
  const total = allEtats.length;
  const pct = etat === 'REJETE' ? 0
    : etat === 'RETIRE' ? 100
    : Math.round(((currentIndex + 1) / total) * 100);
  return (
    <div>
      <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${etat === 'REJETE' ? 'bg-red-500' : pct === 100 ? 'bg-green-500' : 'bg-orange-500'}`}
          style={{ width: `${Math.max(pct, 4)}%` }} />
      </div>
      <p className={`text-[10px] mt-0.5 text-right font-bold ${etat === 'REJETE' ? 'text-red-500' : pct === 100 ? 'text-green-600' : 'text-orange-600'}`}>
        {etat === 'REJETE' ? 'Rejeté' : `${pct}%`}
      </p>
    </div>
  );
}

function InfoBlock({ label, value }: { label: string; value?: string }) {
  if (!value) return null;
  return (
    <div className="bg-white rounded p-2 border">
      <p className="text-[10px] text-gray-400 uppercase">{label}</p>
      <p className="text-sm font-medium text-gray-800 truncate">{value}</p>
    </div>
  );
}

// ─── Page principale ────────────────────────────────────────────────────────
export default function CCFMSuiviPage() {
  const [demandes, setDemandes]   = useState<any[]>([]);
  const [loading, setLoading]     = useState(true);
  const [search, setSearch]       = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try { const d = await mockBackend.getDemandesCCFM({}); setDemandes(d); }
      catch { /* silent */ }
      setLoading(false);
    })();
  }, []);

  const filtered = demandes.filter(d =>
    !search ||
    d.nom_prenom?.toLowerCase().includes(search.toLowerCase()) ||
    d.nus?.toLowerCase().includes(search.toLowerCase()) ||
    d.reference_ccfm?.toLowerCase().includes(search.toLowerCase()) ||
    d.localite?.toLowerCase().includes(search.toLowerCase())
  );

  if (loading) return (
    <div className="flex justify-center py-12">
      <Loader2 className="animate-spin h-8 w-8 text-orange-500" />
    </div>
  );

  return (
    <div className="space-y-6">
      {/* Barre de recherche */}
      <Card className="p-4">
        <div className="flex items-center gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <Input placeholder="Rechercher par NUS, nom, référence, localité…"
              value={search} onChange={e => setSearch(e.target.value)} className="pl-10" />
          </div>
          <p className="text-sm text-gray-500">{filtered.length} dossier(s)</p>
        </div>
      </Card>

      {/* Légende */}
      <div className="flex items-center gap-4 text-xs text-gray-500">
        {[
          { color: 'bg-green-500',            label: 'Étape terminée' },
          { color: 'bg-orange-500 animate-pulse', label: 'Étape en cours' },
          { color: 'bg-gray-200',              label: 'À venir' },
          { color: 'bg-red-500',               label: 'Rejeté' },
        ].map(l => (
          <span key={l.label} className="flex items-center gap-1">
            <span className={`w-3 h-3 rounded-full ${l.color} inline-block`} /> {l.label}
          </span>
        ))}
      </div>

      {/* Liste des dossiers */}
      {filtered.length === 0
        ? <Card className="p-8 text-center text-gray-400">Aucun dossier trouvé.</Card>
        : (
          <div className="space-y-4">
            {filtered.map(d => {
              const isExpanded = expandedId === d.ccfm_id;
              return (
                <Card key={d.ccfm_id} className={`overflow-hidden transition ${isExpanded ? 'border-orange-300 shadow-md' : 'hover:border-orange-200'}`}>
                  {/* En-tête */}
                  <div className="p-4 cursor-pointer flex items-center gap-4"
                    onClick={() => setExpandedId(isExpanded ? null : d.ccfm_id)}>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-bold text-gray-900">{d.nom_prenom}</span>
                        <Badge className={`text-xs ${
                          d.etat === 'REJETE' ? 'bg-red-200 text-red-800' :
                          ['ARCHIVE','RETIRE'].includes(d.etat) ? 'bg-green-100 text-green-700' :
                          'bg-orange-100 text-orange-700'
                        }`}>
                          {ETAT_LABELS[d.etat]?.split('—')[0]?.trim() || d.etat}
                        </Badge>
                        {/* Indicateur ticket GPS */}
                        {d.latitude && (
                          <span className="text-xs bg-cyan-100 text-cyan-700 rounded px-1.5 py-0.5">📍 GPS</span>
                        )}
                      </div>
                      <div className="flex gap-4 text-xs text-gray-500">
                        <span className="font-mono font-bold text-orange-600">{d.nus}</span>
                        <span>📍 {d.localite}</span>
                        <span>📐 {d.superficie_m2} m²</span>
                        {d.reference_ccfm && <span className="text-gray-400">{d.reference_ccfm}</span>}
                      </div>
                    </div>
                    <div className="w-28 flex-shrink-0">
                      <MiniProgress etat={d.etat} />
                    </div>
                    <Button variant="ghost" size="sm">
                      {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                    </Button>
                  </div>

                  {/* Détail expandé */}
                  {isExpanded && (
                    <div className="px-4 pb-4 border-t bg-gray-50">
                      <StepperTracker dossier={d} />

                      {/* Ticket de réponse détaillé si disponible */}
                      {(d.rnaf_conforme !== undefined || d.bgu_conforme !== undefined) && (
                        <div className="mt-3 p-3 bg-cyan-50 border border-cyan-200 rounded-lg">
                          <p className="text-xs font-bold text-cyan-800 mb-2">🎫 Ticket de Réponse RNAF+BGU+GPS</p>
                          <div className="grid grid-cols-3 gap-2 text-xs">
                            <div className={`p-2 rounded ${d.rnaf_conforme ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                              RNAF : {d.rnaf_conforme ? '✅ Conforme' : '❌ Non conforme'}
                            </div>
                            <div className={`p-2 rounded ${d.bgu_conforme ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                              BGU : {d.bgu_conforme ? '✅ Concordant' : '❌ Non concordant'}
                            </div>
                            <div className="p-2 rounded bg-blue-100 text-blue-700 font-mono">
                              {d.latitude ? `📍 ${d.latitude}, ${d.longitude}` : 'GPS non extrait'}
                            </div>
                          </div>
                        </div>
                      )}

                      {/* Infos complémentaires */}
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
                        <InfoBlock label="NIP / Passeport"  value={d.nip_passeport} />
                        <InfoBlock label="Lot / Parcelle"   value={`${d.lot} · ${d.parcelle}`} />
                        <InfoBlock label="Paiement"         value={`${d.mode_paiement} — ${d.montant_paye?.toLocaleString()} FCFA`} />
                        <InfoBlock label="Réf. paiement"    value={d.reference_paiement} />
                        {d.qr_code  && <InfoBlock label="QR Code"   value={d.qr_code} />}
                        {d.annf_code     && <InfoBlock label="Réf. ANNF" value={d.annf_code} />}
                      </div>

                      {/* Timeline */}
                      <div className="mt-4">
                        <p className="text-sm font-bold text-gray-700 mb-2">📅 Historique</p>
                        <DossierTimeline dossier={d} />
                      </div>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        )
      }
    </div>
  );
}
