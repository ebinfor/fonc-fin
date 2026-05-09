/**
 * FONCIER+ v3.0 — Fiche de Vérification Administrative Foncière
 * Étape 4 du workflow CCFM — Rôle : TOPOGRAPHE
 *
 * Conforme au formulaire officiel du Ministère de l'Urbanisme, du Logement et de l'Habitat
 * République du Niger — Direction Générale de l'Urbanisme
 *
 * 9 sections officielles :
 *   §1  Références de la demande (pré-remplies depuis le dossier)
 *   §2  Vérification de l'arrêté de lotissement
 *   §3  Vérification de l'assiette foncière (GPS ticket vs mesuré)
 *   §4  Vérification du titre foncier (si applicable)
 *   §5  Résultats de la vérification (synthèse automatique)
 *   §6  Observations de l'administration
 *   §7  Décision
 *   §8  Signatures et cachets
 *   §9  Note légale obligatoire
 *
 * ⚠️ Le Ticket de Réponse (RNAF/BGU/GPS) est affiché en haut
 *    pour que le topographe puisse comparer les GPS terrain vs ticket.
 */
import { useState, useEffect, useRef } from 'react';
// Données principales via useDataFetch (architecture v3.4.7)
// Actions POST/PATCH conservent fetch() direct
import { mockBackend } from '@/services/mockBackend';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import { Ruler, MapPin, Printer, Save, Loader2, AlertTriangle, CheckCircle, XCircle, FileCheck, User, Calendar, ClipboardList, Eye } from 'lucide-react';

// ─── Types ───────────────────────────────────────────────────────────────────
interface FicheForm {
  // §1 Références
  objet_demande: string;
  // §2 Arrêté de lotissement
  arrete_numero: string;
  arrete_date_signature: string;
  arrete_autorite: string;
  jo_publie: boolean | null;
  jo_reference: string;
  // §3 Assiette foncière
  parcelle_ref_titre: string;
  superficie_mesuree: string;
  gps_mesure_latitude: string;
  gps_mesure_longitude: string;
  parcelle_conforme: boolean | null;
  // §4 Titre foncier
  titre_foncier_numero: string;
  titre_foncier_emetteur: string;
  titre_authentique: boolean | null;
  // §6 Observations
  observations: string;
  // §7 Décision
  decision: 'AUTORISATION' | 'REFUS' | '';
  motif_refus: string;
  // §8 Signatures
  topographe_nom: string;
  topographe_fonction: string;
  chef_service_nom: string;
  chef_service_fonction: string;
  // Photos
  photos_count: number;
}

const EMPTY: FicheForm = {
  objet_demande: '', arrete_numero: '', arrete_date_signature: '',
  arrete_autorite: '', jo_publie: null, jo_reference: '',
  parcelle_ref_titre: '', superficie_mesuree: '', gps_mesure_latitude: '', gps_mesure_longitude: '',
  parcelle_conforme: null, titre_foncier_numero: '', titre_foncier_emetteur: '',
  titre_authentique: null, observations: '',
  decision: '', motif_refus: '',
  topographe_nom: '', topographe_fonction: 'Topographe agréé',
  chef_service_nom: '', chef_service_fonction: 'Chef de Service Foncier',
  photos_count: 0,
};

// ─── Composants ──────────────────────────────────────────────────────────────

function SectionHeader({ n, titre, icon }: { n: string; titre: string; icon?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 mb-3 pb-2 border-b border-gray-200">
      <div className="w-7 h-7 rounded-full bg-orange-600 flex items-center justify-center text-white font-bold text-xs flex-shrink-0">
        {n}
      </div>
      {icon && <span className="text-gray-500">{icon}</span>}
      <h3 className="font-semibold text-gray-800 text-sm uppercase tracking-wide">{titre}</h3>
    </div>
  );
}

function RadioGroup({
  value, onChange, options, name,
}: {
  value: boolean | null;
  onChange: (v: boolean) => void;
  options: [boolean, string][];
  name: string;
}) {
  return (
    <div className="flex gap-4">
      {options.map(([val, label]) => (
        <label key={String(val)} className={`flex items-center gap-2 cursor-pointer px-3 py-1.5 rounded-lg border transition-colors text-sm
          ${value === val ? 'border-orange-400 bg-orange-50 text-orange-800 font-medium' : 'border-gray-200 text-gray-600 hover:border-gray-300'}`}>
          <input type="radio" name={name} checked={value === val} onChange={() => onChange(val)} className="accent-orange-600" />
          {label}
        </label>
      ))}
    </div>
  );
}

// ─── Page principale ─────────────────────────────────────────────────────────

export default function CCFMFicheConstatTopographe() {
  const [demandes, setDemandes]       = useState<any[]>([]);
  const [selectedDossier, setSelected] = useState<any>(null);
  const [form, setForm]               = useState<FicheForm>(EMPTY);
  const [submitting, setSubmitting]   = useState(false);
  const [submitted, setSubmitted]     = useState(false);
  const [loading, setLoading]         = useState(true);
  const printRef                      = useRef<HTMLDivElement>(null);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const all = await mockBackend.getDemandesCCFM({});
        // Topographe voit les dossiers en visite ou ordre émis
        const siens = all.filter((d: any) =>
          ['ORDRE_MISSION_EMIS', 'ATTENTE_VISITE_TERRAIN', 'VISITE_EN_COURS'].includes(d.etat)
        );
        setDemandes(siens);
      } catch { /* silent */ }
      setLoading(false);
    })();
  }, []);

  const handleSelectDossier = (d: any) => {
    setSelected(d);
    setSubmitted(false);
    setForm({
      ...EMPTY,
      objet_demande: `Vérification foncière — ${d.localite}, Lot ${d.lot}, Parcelle ${d.parcelle}`,
      parcelle_ref_titre: `${d.lot}/${d.parcelle}`,
      superficie_mesuree: d.superficie_m2?.toString() || '',
      arrete_numero: d.numero_arrete || '',
      titre_foncier_numero: d.numero_titre_foncier || '',
      // Pré-remplir GPS depuis le ticket si disponible
      gps_mesure_latitude:  d.latitude?.toString()  || '',
      gps_mesure_longitude: d.longitude?.toString() || '',
    });
  };

  const f = (field: keyof FicheForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm(prev => ({ ...prev, [field]: e.target.value }));

  const handleSubmit = async () => {
    if (!selectedDossier) return;
    if (!form.topographe_nom) { toast.error('Le nom du topographe est obligatoire'); return; }
    if (!form.decision)        { toast.error('La décision (§7) est obligatoire');     return; }
    setSubmitting(true);
    try {
      await mockBackend.deposerFicheConstat({
        ccfm_id: selectedDossier.ccfm_id,
        ...form,
        superficie_mesuree_m2: parseFloat(form.superficie_mesuree) || 0,
        gps_mesure_latitude:   parseFloat(form.gps_mesure_latitude)  || null,
        gps_mesure_longitude:  parseFloat(form.gps_mesure_longitude) || null,
        constat_conforme: form.decision === 'AUTORISATION',
        concordance_bgu: form.parcelle_conforme === true,
      });
      toast.success(`Fiche de constat déposée ✓ — Dossier ${selectedDossier.nus} → Rapport d'appréciation en génération`);
      setSubmitted(true);
    } catch { toast.error('Erreur lors du dépôt de la fiche'); }
    finally { setSubmitting(false); }
  };

  const handlePrint = () => window.print();

  if (loading) return (
    <div className="flex justify-center h-48 items-center">
      <Loader2 className="h-8 w-8 animate-spin text-orange-500" />
    </div>
  );

  // ── Calcul concordance GPS (ticket vs mesuré) ──
  const gpsEcart = selectedDossier?.latitude && form.gps_mesure_latitude
    ? Math.abs(parseFloat(form.gps_mesure_latitude) - selectedDossier.latitude)
    : null;
  const gpsOk = gpsEcart !== null ? gpsEcart < 0.001 : null; // ~100m tolérance

  // ── Calcul résultats §5 ──
  const superficieDeclaree = selectedDossier?.superficie_m2 || 0;
  const superficieMesuree  = parseFloat(form.superficie_mesuree) || 0;
  const ecartPct = superficieDeclaree > 0 && superficieMesuree > 0
    ? Math.abs(superficieMesuree - superficieDeclaree) / superficieDeclaree * 100
    : null;

  return (
    <div className="space-y-6">

      {/* ── En-tête officiel ── */}
      <Card className="p-5">
        <div className="flex items-start gap-4">
          <div className="w-14 h-14 bg-orange-100 rounded-lg flex items-center justify-center text-3xl flex-shrink-0">🇳🇪</div>
          <div className="flex-1">
            <p className="text-xs font-bold text-gray-500 uppercase">République du Niger · Ministère de l'Urbanisme, du Logement et de l'Habitat</p>
            <h2 className="text-lg font-bold text-gray-900 mt-0.5">Direction Générale de l'Urbanisme</h2>
            <h3 className="text-base font-semibold text-orange-700">Fiche de Vérification Administrative Foncière</h3>
            <p className="text-xs text-gray-500 mt-1">
              Étape 4 du workflow CCFM · Exigible avant tout constat d'huissier et/ou intervention technique
            </p>
          </div>
          <Badge className="bg-indigo-100 text-indigo-700 self-start">📐 TOPOGRAPHE</Badge>
        </div>
      </Card>

      <div className="grid grid-cols-3 gap-6">

        {/* ── Colonne gauche : sélection dossier ── */}
        <div className="space-y-4">
          <Card className="p-4">
            <h3 className="font-semibold text-gray-800 mb-3 text-sm">Dossiers assignés</h3>
            {demandes.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-4">Aucun ordre de mission en attente</p>
            ) : (
              <div className="space-y-2">
                {demandes.map(d => (
                  <div key={d.ccfm_id}
                    onClick={() => handleSelectDossier(d)}
                    className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                      selectedDossier?.ccfm_id === d.ccfm_id
                        ? 'border-indigo-400 bg-indigo-50'
                        : 'hover:border-gray-300 hover:bg-gray-50'
                    }`}>
                    <p className="font-mono text-xs font-bold text-orange-700">{d.nus}</p>
                    <p className="text-sm font-medium text-gray-800">{d.nom_prenom}</p>
                    <p className="text-xs text-gray-500">{d.localite} — {d.lot}/{d.parcelle}</p>
                    <p className="text-xs text-gray-400">{d.superficie_m2?.toLocaleString('fr-FR')} m²</p>
                    {/* Ticket GPS disponible ? */}
                    {d.latitude && (
                      <p className="text-xs text-cyan-600 mt-1 font-mono">📍 GPS ticket : {d.latitude}, {d.longitude}</p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* Note légale §9 */}
          <Card className="p-4 bg-amber-50 border-amber-200">
            <p className="text-xs text-amber-800 font-semibold mb-1">§9 — Note légale obligatoire</p>
            <p className="text-xs text-amber-700">
              La présente fiche de vérification administrative <strong>ne constitue pas un titre de propriété</strong>.
              Elle atteste uniquement de la conformité légale et réglementaire d'une parcelle avec un arrêté
              de lotissement publié. Elle est exigible avant tout constat d'huissier et/ou intervention technique.
            </p>
          </Card>
        </div>

        {/* ── Colonne droite : formulaire ── */}
        <div className="col-span-2" ref={printRef}>
          {!selectedDossier ? (
            <Card className="p-12 text-center text-gray-400">
              <ClipboardList className="h-12 w-12 mx-auto mb-3 text-gray-300" />
              <p>Sélectionnez un dossier à gauche pour remplir la fiche</p>
            </Card>
          ) : submitted ? (
            <Card className="p-8 text-center bg-green-50 border-green-200">
              <CheckCircle className="h-12 w-12 text-green-500 mx-auto mb-3" />
              <p className="text-lg font-bold text-green-800">Fiche déposée avec succès</p>
              <p className="text-sm text-green-600 mt-1">
                Le système va générer le rapport d'appréciation (ticket + fiche) pour le Directeur.
              </p>
              <Button className="mt-4 bg-indigo-600 hover:bg-indigo-700"
                onClick={() => { setSelected(null); setSubmitted(false); }}>
                Nouvelle fiche
              </Button>
            </Card>
          ) : (
            <div className="space-y-5">

              {/* Ticket de réponse — affiché en haut pour comparaison GPS */}
              {selectedDossier.latitude && (
                <Card className="p-4 bg-cyan-50 border-cyan-200">
                  <p className="text-xs font-bold text-cyan-800 mb-2">🎫 Ticket de Réponse RNAF+BGU+GPS (joint à votre ordre de mission)</p>
                  <div className="grid grid-cols-3 gap-3 text-xs">
                    <div className={`p-2 rounded ${selectedDossier.rnaf_conforme ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                      RNAF : {selectedDossier.rnaf_conforme ? '✅ Conforme' : '❌ Non conforme'}
                    </div>
                    <div className={`p-2 rounded ${selectedDossier.bgu_conforme ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                      BGU : {selectedDossier.bgu_conforme ? '✅ Concordant' : '❌ Non concordant'}
                    </div>
                    <div className="p-2 rounded bg-blue-100 text-blue-700 font-mono">
                      GPS officiel : {selectedDossier.latitude}, {selectedDossier.longitude}
                    </div>
                  </div>
                  <p className="text-xs text-cyan-600 mt-2">
                    ⚠️ Comparez les GPS du ticket avec vos mesures terrain (§3 ci-dessous)
                  </p>
                </Card>
              )}

              {/* §1 — Références */}
              <Card className="p-5">
                <SectionHeader n="1" titre="Références de la demande" icon={<FileCheck className="h-4 w-4" />} />
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <label className="text-xs text-gray-500">N° de dossier (NUS)</label>
                    <Input value={selectedDossier.nus} disabled className="mt-1 bg-gray-50 font-mono text-orange-700" />
                  </div>
                  <div>
                    <label className="text-xs text-gray-500">Date de la demande</label>
                    <Input value={selectedDossier.date_enregistrement?.slice(0, 10) || ''} disabled className="mt-1 bg-gray-50" />
                  </div>
                  <div>
                    <label className="text-xs text-gray-500">Demandeur</label>
                    <Input value={selectedDossier.nom_prenom} disabled className="mt-1 bg-gray-50" />
                  </div>
                  <div>
                    <label className="text-xs text-gray-500">Objet de la demande</label>
                    <Input value={form.objet_demande} onChange={f('objet_demande')} className="mt-1" />
                  </div>
                </div>
              </Card>

              {/* §2 — Arrêté de lotissement */}
              <Card className="p-5">
                <SectionHeader n="2" titre="Vérification de l'arrêté de lotissement" />
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs text-gray-500">Numéro de l'arrêté</label>
                    <Input value={form.arrete_numero} onChange={f('arrete_numero')} className="mt-1" placeholder="Arrêté n°…" />
                  </div>
                  <div>
                    <label className="text-xs text-gray-500">Date de signature</label>
                    <Input type="date" value={form.arrete_date_signature} onChange={f('arrete_date_signature')} className="mt-1" />
                  </div>
                  <div className="col-span-2">
                    <label className="text-xs text-gray-500">Autorité signataire</label>
                    <Input value={form.arrete_autorite} onChange={f('arrete_autorite')} className="mt-1" placeholder="Ministère de l'Urbanisme…" />
                  </div>
                  <div className="col-span-2">
                    <label className="text-xs text-gray-500 block mb-2">Publication au Journal Officiel</label>
                    <RadioGroup
                      value={form.jo_publie} name="jo_publie"
                      onChange={v => setForm(p => ({ ...p, jo_publie: v }))}
                      options={[[true, '☑ Oui'], [false, '☐ Non']]}
                    />
                    {form.jo_publie && (
                      <div className="mt-2">
                        <label className="text-xs text-gray-500">Référence JO</label>
                        <Input value={form.jo_reference} onChange={f('jo_reference')} className="mt-1" placeholder="N° JO — Date…" />
                      </div>
                    )}
                  </div>
                </div>
              </Card>

              {/* §3 — Assiette foncière */}
              <Card className="p-5">
                <SectionHeader n="3" titre="Vérification de l'assiette foncière" icon={<MapPin className="h-4 w-4" />} />
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs text-gray-500">Référence parcelle / titre foncier</label>
                    <Input value={form.parcelle_ref_titre} onChange={f('parcelle_ref_titre')} className="mt-1" />
                  </div>
                  <div>
                    <label className="text-xs text-gray-500">Superficie mesurée (m²)</label>
                    <div className="flex gap-2 mt-1">
                      <Input type="number" value={form.superficie_mesuree} onChange={f('superficie_mesuree')} placeholder={selectedDossier.superficie_m2?.toString()} />
                      {ecartPct !== null && (
                        <Badge className={`flex-shrink-0 ${ecartPct > 5 ? 'bg-red-100 text-red-700' : ecartPct > 2 ? 'bg-amber-100 text-amber-700' : 'bg-green-100 text-green-700'}`}>
                          Écart {ecartPct.toFixed(1)}%
                        </Badge>
                      )}
                    </div>
                  </div>
                  <div>
                    <label className="text-xs text-gray-500">GPS mesuré — Latitude</label>
                    <Input value={form.gps_mesure_latitude} onChange={f('gps_mesure_latitude')} className="mt-1 font-mono" placeholder={selectedDossier.latitude?.toString() || '13.5137…'} />
                  </div>
                  <div>
                    <label className="text-xs text-gray-500">GPS mesuré — Longitude</label>
                    <Input value={form.gps_mesure_longitude} onChange={f('gps_mesure_longitude')} className="mt-1 font-mono" placeholder={selectedDossier.longitude?.toString() || '2.1098…'} />
                  </div>

                  {/* Comparaison GPS ticket vs mesuré */}
                  {selectedDossier.latitude && form.gps_mesure_latitude && (
                    <div className="col-span-2 p-3 rounded-lg border text-xs">
                      <p className="font-semibold text-gray-700 mb-1">Concordance GPS : ticket vs terrain</p>
                      <div className="flex gap-4">
                        <span>Ticket : <code>{selectedDossier.latitude}, {selectedDossier.longitude}</code></span>
                        <span>Mesuré : <code>{form.gps_mesure_latitude}, {form.gps_mesure_longitude}</code></span>
                        {gpsOk !== null && (
                          <Badge className={gpsOk ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}>
                            {gpsOk ? '✅ Concordant' : '⚠️ Écart significatif'}
                          </Badge>
                        )}
                      </div>
                    </div>
                  )}

                  <div className="col-span-2">
                    <label className="text-xs text-gray-500 block mb-2">Correspondance avec l'assiette autorisée par arrêté</label>
                    <RadioGroup
                      value={form.parcelle_conforme} name="parcelle_conforme"
                      onChange={v => setForm(p => ({ ...p, parcelle_conforme: v }))}
                      options={[[true, '☑ Conforme'], [false, '☐ Non conforme']]}
                    />
                  </div>
                </div>
              </Card>

              {/* §4 — Titre foncier */}
              <Card className="p-5">
                <SectionHeader n="4" titre="Vérification du titre foncier (si applicable)" />
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs text-gray-500">Numéro du titre foncier</label>
                    <Input value={form.titre_foncier_numero} onChange={f('titre_foncier_numero')} className="mt-1" placeholder="TF-…" />
                  </div>
                  <div>
                    <label className="text-xs text-gray-500">Émetteur</label>
                    <Input value={form.titre_foncier_emetteur} onChange={f('titre_foncier_emetteur')} className="mt-1" placeholder="Direction du Cadastre…" />
                  </div>
                  <div className="col-span-2">
                    <label className="text-xs text-gray-500 block mb-2">Authenticité confirmée aux archives domaniales</label>
                    <RadioGroup
                      value={form.titre_authentique} name="titre_authentique"
                      onChange={v => setForm(p => ({ ...p, titre_authentique: v }))}
                      options={[[true, '☑ Oui'], [false, '☐ Non']]}
                    />
                  </div>
                </div>
              </Card>

              {/* §5 — Résultats (synthèse automatique) */}
              <Card className="p-5 bg-gray-50">
                <SectionHeader n="5" titre="Résultats de la vérification (synthèse)" />
                <div className="space-y-2 text-sm">
                  {[
                    {
                      label: 'La parcelle',
                      yes: 'appartient', no: 'n\'appartient pas',
                      suffix: 'à un lotissement dûment autorisé',
                      val: form.parcelle_conforme,
                    },
                    {
                      label: 'L\'arrêté ministériel correspondant est',
                      yes: 'valide et publié', no: 'inexistant ou non publié',
                      suffix: '',
                      val: form.jo_publie,
                    },
                    {
                      label: 'Les coordonnées GPS',
                      yes: 'concordent', no: 'ne concordent pas',
                      suffix: 'avec l\'assiette autorisée',
                      val: gpsOk !== null ? gpsOk : form.parcelle_conforme,
                    },
                    {
                      label: 'Le titre foncier présenté est',
                      yes: 'authentique', no: 'contestable',
                      suffix: '',
                      val: form.titre_authentique,
                    },
                  ].map(r => (
                    <div key={r.label} className="flex items-center gap-2 p-2 bg-white rounded border text-xs">
                      {r.val === null
                        ? <span className="text-gray-300">○</span>
                        : r.val
                          ? <CheckCircle className="h-3.5 w-3.5 text-green-500 flex-shrink-0" />
                          : <XCircle className="h-3.5 w-3.5 text-red-500 flex-shrink-0" />}
                      <span className="text-gray-600">
                        {r.label} <strong>{r.val === null ? '…' : r.val ? r.yes : r.no}</strong>{r.suffix ? ` ${r.suffix}` : ''}.
                      </span>
                    </div>
                  ))}
                </div>
              </Card>

              {/* §6 — Observations */}
              <Card className="p-5">
                <SectionHeader n="6" titre="Observations de l'administration" />
                <Textarea
                  value={form.observations}
                  onChange={f('observations')}
                  rows={3}
                  placeholder="Observations complémentaires sur l'état du terrain, anomalies constatées, éléments particuliers…"
                  className="text-sm"
                />
              </Card>

              {/* §7 — Décision */}
              <Card className="p-5">
                <SectionHeader n="7" titre="Décision" />
                <div className="space-y-3">
                  <RadioGroup
                    value={form.decision === 'AUTORISATION' ? true : form.decision === 'REFUS' ? false : null}
                    name="decision"
                    onChange={v => setForm(p => ({ ...p, decision: v ? 'AUTORISATION' : 'REFUS' }))}
                    options={[
                      [true,  '☑ Autorisation de poursuivre la procédure de constat'],
                      [false, '☐ Refus motivé (arrêté inexistant / non publié / incohérence GPS / faux titre)'],
                    ]}
                  />
                  {form.decision === 'REFUS' && (
                    <div>
                      <label className="text-xs text-gray-500">Motif du refus *</label>
                      <Textarea value={form.motif_refus} onChange={f('motif_refus')} rows={2}
                        className="mt-1 text-sm border-red-200" placeholder="Préciser le motif…" />
                    </div>
                  )}
                </div>
              </Card>

              {/* §8 — Signatures */}
              <Card className="p-5">
                <SectionHeader n="8" titre="Signatures et cachets" icon={<User className="h-4 w-4" />} />
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <p className="text-xs font-bold text-gray-600 uppercase">Agent vérificateur (Topographe)</p>
                    <div>
                      <label className="text-xs text-gray-500">Nom *</label>
                      <Input value={form.topographe_nom} onChange={f('topographe_nom')} className="mt-1" placeholder="Issoufou Mahamane" />
                    </div>
                    <div>
                      <label className="text-xs text-gray-500">Fonction</label>
                      <Input value={form.topographe_fonction} onChange={f('topographe_fonction')} className="mt-1" />
                    </div>
                    <div className="h-12 border-2 border-dashed border-gray-300 rounded flex items-center justify-center text-xs text-gray-400">
                      Signature & Cachet
                    </div>
                  </div>
                  <div className="space-y-2">
                    <p className="text-xs font-bold text-gray-600 uppercase">Chef de service / Directeur</p>
                    <div>
                      <label className="text-xs text-gray-500">Nom</label>
                      <Input value={form.chef_service_nom} onChange={f('chef_service_nom')} className="mt-1" placeholder="Ibrahim Sani" />
                    </div>
                    <div>
                      <label className="text-xs text-gray-500">Fonction</label>
                      <Input value={form.chef_service_fonction} onChange={f('chef_service_fonction')} className="mt-1" />
                    </div>
                    <div className="h-12 border-2 border-dashed border-gray-300 rounded flex items-center justify-center text-xs text-gray-400">
                      Signature & Cachet
                    </div>
                  </div>
                </div>

                {/* Photos */}
                <div className="mt-4">
                  <label className="text-xs text-gray-500">Nombre de photos géolocalisées</label>
                  <Input type="number" min={0} max={20}
                    value={form.photos_count}
                    onChange={e => setForm(p => ({ ...p, photos_count: parseInt(e.target.value) || 0 }))}
                    className="mt-1 w-24" />
                </div>
              </Card>

              {/* Actions */}
              <div className="flex gap-3 pb-4">
                <Button
                  className="flex-1 bg-indigo-600 hover:bg-indigo-700 gap-2 py-5"
                  onClick={handleSubmit}
                  disabled={submitting || !form.decision || !form.topographe_nom}>
                  {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                  Déposer la fiche de constat
                </Button>
                <Button variant="outline" className="gap-2" onClick={handlePrint}>
                  <Printer className="h-4 w-4" />Imprimer
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
