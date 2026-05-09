/**
 * FONCIER+ v3.0 — CCFM Documents Officiels
 * Certificat de Conformité Foncière Mixte (PDF officiel)
 * Fiche de Vérification Administrative Foncière (Constat terrain)
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
import {


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
  FileText, Printer, Download, CheckCircle, XCircle,
  Loader2, AlertTriangle, MapPin, User, Ruler, Stamp,
  ClipboardCheck, ChevronDown, Save, Eye,
} from 'lucide-react';

// ─────────────────────────────────────────────────────────────────────────────
// Sélecteur de demande commun
// ─────────────────────────────────────────────────────────────────────────────
function DemandePicker({
  demandes, selected, onSelect,
}: { demandes: any[]; selected: any; onSelect: (d: any) => void }) {
  return (
    <div className="flex gap-3 items-center flex-wrap">
      <label className="text-sm font-semibold text-gray-700 whitespace-nowrap">Dossier CCFM :</label>
      <select
        className="border rounded-lg px-3 py-2 text-sm flex-1 min-w-52 bg-white"
        value={selected?.ccfm_id || ''}
        onChange={e => onSelect(demandes.find(d => d.ccfm_id === e.target.value))}
      >
        <option value="">— Sélectionner une demande —</option>
        {demandes.map(d => (
          <option key={d.ccfm_id} value={d.ccfm_id}>
            {d.nus} — {d.nom_prenom} ({d.localite})
          </option>
        ))}
      </select>
      {selected && (
        <Badge className={`text-xs ${
          selected.etat === 'ARCHIVE' || selected.etat === 'SIGNE_DIRECTEUR'
            ? 'bg-emerald-100 text-emerald-700'
            : selected.etat === 'REJETE'
              ? 'bg-red-100 text-red-700'
              : 'bg-amber-100 text-amber-700'
        }`}>{selected.etat?.replace(/_/g, ' ')}</Badge>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// QR CODE simulé (visuel)
// ─────────────────────────────────────────────────────────────────────────────
function QRCodeVisual({ value }: { value?: string }) {
  if (!value) return (
    <div className="w-28 h-28 border-2 border-dashed border-gray-300 rounded flex items-center justify-center text-gray-300 text-xs text-center p-2">
      QR Code généré à la signature
    </div>
  );
  // Simuler un QR code avec un motif CSS
  return (
    <div className="w-28 h-28 relative bg-white border-2 border-gray-800 p-1 rounded">
      <div className="w-full h-full grid grid-cols-7 grid-rows-7 gap-px">
        {Array.from({ length: 49 }, (_, i) => {
          // Corners fixes
          const row = Math.floor(i / 7), col = i % 7;
          const isCorner = (row < 2 && col < 2) || (row < 2 && col > 4) || (row > 4 && col < 2);
          // Motif pseudo-aléatoire basé sur le hash
          const charCode = value.charCodeAt(i % value.length) || 0;
          const filled = isCorner || (charCode + i) % 3 !== 0;
          return (
            <div key={i} className={`rounded-sm ${filled ? 'bg-gray-900' : 'bg-white'}`} />
          );
        })}
      </div>
      {/* Finder patterns */}
      <div className="absolute top-1 left-1 w-5 h-5 border-2 border-gray-900 rounded-sm">
        <div className="absolute inset-1 bg-gray-900 rounded-sm" />
      </div>
      <div className="absolute top-1 right-1 w-5 h-5 border-2 border-gray-900 rounded-sm">
        <div className="absolute inset-1 bg-gray-900 rounded-sm" />
      </div>
      <div className="absolute bottom-1 left-1 w-5 h-5 border-2 border-gray-900 rounded-sm">
        <div className="absolute inset-1 bg-gray-900 rounded-sm" />
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// COMPOSANT 1 — Certificat CCFM officiel
// ─────────────────────────────────────────────────────────────────────────────
function CertificatCCFMPane({ demandes }: { demandes: any[] }) {
  const [selected, setSelected] = useState<any>(null);
  const printRef = useRef<HTMLDivElement>(null);

  const handlePrint = () => {
    const el = printRef.current;
    if (!el) return;
    const w = window.open('', '_blank');
    if (!w) return;
    w.document.write(`
      <!DOCTYPE html><html><head>
      <meta charset="utf-8"/>
      <title>CCFM — ${selected?.nus || ''}</title>
      <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Times New Roman', serif; font-size: 11pt; color: #000; background: #fff; }
        .page { width: 210mm; min-height: 297mm; margin: 0 auto; padding: 15mm; }
        .header-band { border-top: 4px solid #e65c00; border-bottom: 4px solid #2d7a2d; padding: 8px 0; margin-bottom: 16px; }
        .header-text { text-align: center; }
        .title-main { font-size: 16pt; font-weight: bold; letter-spacing: 1px; }
        .title-sub { font-size: 12pt; }
        .ref-box { float: right; border: 1px solid #000; padding: 8px 16px; margin-left: 20px; line-height: 2; }
        .note { font-size: 8.5pt; font-style: italic; color: #333; margin-bottom: 14px; text-align: center; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 16px; }
        .section-title { font-size: 10pt; font-weight: bold; text-decoration: underline; margin-bottom: 8px; }
        .field { margin-bottom: 6px; font-size: 10pt; border-bottom: 1px dotted #666; padding-bottom: 2px; }
        .field-label { color: #333; }
        .cert-text { font-size: 10pt; text-align: justify; line-height: 1.6; }
        .sig-zone { margin-top: 24px; text-align: center; }
        .sig-name { font-weight: bold; text-decoration: underline; }
        .sig-line { margin-top: 16px; border-top: 1px solid #000; width: 80%; margin-left: auto; margin-right: auto; }
        .armoiries { text-align: center; margin-bottom: 8px; font-size: 32pt; }
        @media print { body { -webkit-print-color-adjust: exact; } }
      </style>
      </head><body><div class="page">
        <div class="armoiries">🇳🇪</div>
        <div class="header-text">
          <div style="font-weight:bold;font-size:14pt;">REPUBLIQUE DU NIGER</div>
          <div style="font-style:italic;">Ministère de l'Urbanisme, du logement et de l'habitat</div>
        </div>
        <div class="header-band"></div>
        <div style="text-align:center;margin-bottom:16px;">
          <div class="title-main">CERTIFICAT DE CONFORMITE FONCIERE MIXTE</div>
          <div class="title-main">CCFM</div>
        </div>
        <div style="float:right;border:1px solid #000;padding:8px 16px;margin-left:20px;line-height:2;font-size:10pt;">
          <strong>Référence :</strong> ${selected?.reference_ccfm || '………………………'}<br/>
          <strong>Date :</strong> ${selected?.date_signature ? new Date(selected.date_signature).toLocaleDateString('fr-FR') : '………………………'}
        </div>
        <p class="note">Note : le CCFM n'est ni un acte de cession, ni un titre foncier, mais une condition préalable à tout établissement de ces derniers, mais aussi à toute transaction légale</p>
        <div style="clear:both;" class="grid">
          <div>
            <div class="section-title">I. IDENTITÉ DU BÉNÉFICIAIRE</div>
            <div class="field"><span class="field-label">Nom & prénom(s) :</span> ${selected?.nom_prenom || '……………………………'}</div>
            <div class="field"><span class="field-label">N° d'Identité (NIP/Passeport) :</span> ${selected?.nip_passeport || '……………………'}</div>
            <div class="field"><span class="field-label">Date et lieu de naissance :</span> ${selected?.date_naissance || '………………'} ${selected?.lieu_naissance ? '— ' + selected.lieu_naissance : ''}</div>
          </div>
          <div>
            <div class="section-title">IV. CERTIFICATION</div>
            <div class="cert-text">Le présent certificat atteste que la parcelle ci-dessus est conforme aux dispositions légales et réglementaires en vigueur.</div>
            <div style="margin-top:12px;text-align:center;">
              <div style="font-size:8pt;color:#666;margin-bottom:4px;">QR Code de vérification</div>
              <div style="width:80px;height:80px;border:2px solid #000;margin:0 auto;display:flex;align-items:center;justify-content:center;font-size:7pt;color:#666;">
                ${selected?.qr_code ? selected.qr_code.slice(0,12)+'…' : '[QR Code]'}
              </div>
            </div>
          </div>
        </div>
        <div class="section-title">II. RÉFÉRENCES DE LA PARCELLE</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">
          <div class="field"><span class="field-label">Localité :</span> ${selected?.localite || '…………………………………'}</div>
          <div class="field"><span class="field-label">Superficie :</span> ${selected?.superficie_m2 ? selected.superficie_m2 + ' m²' : '……… m²'}</div>
          <div class="field"><span class="field-label">Lot :</span> ${selected?.lot || '………………………'} &nbsp;&nbsp; <span class="field-label">Parcelle :</span> ${selected?.parcelle || '………'}</div>
          <div class="field"><span class="field-label">GPS :</span> Lat. ${selected?.latitude || '…………………'} — Long. ${selected?.longitude || '…………………'}</div>
        </div>
        <div class="section-title">III. VALIDATION TECHNIQUE</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px;">
          <div>
            <div class="field"><strong>Huissier :</strong> ${selected?.huissier_nom || 'Nom et Prénoms : ………………………………………'}</div>
            <div style="margin-top:20px;border-top:1px solid #000;width:80%;font-size:9pt;color:#555;">Signature & Cachet</div>
          </div>
          <div>
            <div class="field"><strong>Topographe :</strong> ${selected?.topographe_nom || 'Nom et Prénoms : ………………………………'}</div>
            <div style="margin-top:20px;border-top:1px solid #000;width:80%;font-size:9pt;color:#555;">Signature & Cachet</div>
          </div>
        </div>
        <div style="text-align:center;margin-top:20px;">
          <div class="sig-name">Le Directeur de l'Urbanisme</div>
          <div style="margin-top:32px;border-top:1px solid #000;width:50%;margin-left:auto;margin-right:auto;font-size:9pt;color:#555;">Signature & Cachet</div>
        </div>
        <div style="margin-top:24px;padding-top:8px;border-top:1px solid #ccc;text-align:center;font-size:8pt;color:#555;font-style:italic;">
          Note : le CCFM n'est ni un acte de cession, ni un titre foncier, mais une condition préalable à tout établissement de ces derniers, mais aussi à toute transaction légale
        </div>
      </div></body></html>
    `);
    w.document.close();
    w.focus();
    setTimeout(() => { w.print(); }, 500);
  };

  return (
    <div className="space-y-5">
      {/* Sélecteur */}
      <Card className="p-4">
        <DemandePicker demandes={demandes} selected={selected} onSelect={setSelected} />
      </Card>

      {!selected ? (
        <div className="flex flex-col items-center justify-center py-24 text-gray-400 space-y-3">
          <FileText className="h-16 w-16 opacity-20" />
          <p className="text-sm">Sélectionnez un dossier CCFM pour afficher le certificat</p>
        </div>
      ) : (
        <>
          {/* Actions */}
          <div className="flex gap-3 justify-end">
            <Button variant="outline" className="gap-2 border-orange-200 text-orange-700 hover:bg-orange-50"
              onClick={handlePrint}>
              <Printer className="h-4 w-4" />Imprimer
            </Button>
            <Button className="gap-2 bg-orange-600 hover:bg-orange-700"
              onClick={() => toast.info('Export PDF — signature électronique requise (backend)')}>
              <Download className="h-4 w-4" />Télécharger PDF officiel
            </Button>
          </div>

          {/* ── Certificat officiel ── */}
          <div ref={printRef} className="bg-white border-2 border-gray-300 rounded-xl shadow-lg overflow-hidden max-w-3xl mx-auto">
            {/* Bande couleurs Niger */}
            <div className="h-1.5 bg-gradient-to-r from-orange-500 via-white to-green-600" />

            {/* En-tête officiel */}
            <div className="px-10 pt-6 pb-4 text-center border-b-2 border-gray-200">
              <div className="text-5xl mb-2">🇳🇪</div>
              <h1 className="text-lg font-black uppercase tracking-wider text-gray-900">REPUBLIQUE DU NIGER</h1>
              <p className="text-sm font-semibold text-gray-600 italic">Ministère de l'Urbanisme, du logement et de l'habitat</p>
              <div className="mt-3 h-0.5 bg-gradient-to-r from-orange-500 via-gray-300 to-green-500" />
              <h2 className="mt-4 text-xl font-black uppercase tracking-widest text-gray-900">
                Certificat de Conformité Foncière Mixte
              </h2>
              <h3 className="text-2xl font-black text-orange-600 tracking-[0.3em]">CCFM</h3>
            </div>

            <div className="px-10 py-5">
              {/* Réf + Note */}
              <div className="flex justify-between items-start mb-3">
                <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-3 max-w-xs leading-relaxed">
                  <strong>Note :</strong> le CCFM n'est ni un acte de cession, ni un titre foncier, mais une <strong>condition préalable obligatoire</strong> à toute transaction légale.
                </div>
                <div className="border border-gray-400 rounded px-4 py-3 text-sm font-serif min-w-48">
                  <div className="flex gap-2"><span className="font-bold">Référence :</span> <span className="font-mono text-xs">{selected.reference_ccfm || '…………………'}</span></div>
                  <div className="flex gap-2 mt-1"><span className="font-bold">Date :</span> <span>{selected.date_signature ? new Date(selected.date_signature).toLocaleDateString('fr-FR') : '…………………'}</span></div>
                </div>
              </div>

              {/* Grid principal I + IV */}
              <div className="grid grid-cols-2 gap-8 mb-5">
                {/* Section I */}
                <div>
                  <h4 className="font-black text-sm uppercase tracking-wide border-b-2 border-gray-800 pb-1 mb-3">
                    I. &nbsp;Identité du Bénéficiaire
                  </h4>
                  <div className="space-y-2.5 text-sm font-serif">
                    <div className="border-b border-dotted border-gray-400 pb-1">
                      <span className="text-gray-600">Nom & prénom(s) :</span>
                      <span className="ml-1 font-bold">{selected.nom_prenom || ' ……………………………………'}</span>
                    </div>
                    <div className="border-b border-dotted border-gray-400 pb-1">
                      <span className="text-gray-600">N° Identité (NIP/Passeport) :</span>
                      <span className="ml-1 font-mono text-xs">{selected.nip_passeport || ' …………………………'}</span>
                    </div>
                    <div className="border-b border-dotted border-gray-400 pb-1">
                      <span className="text-gray-600">Date et lieu de naissance :</span>
                      <span className="ml-1">{selected.date_naissance ? `${selected.date_naissance} — ${selected.lieu_naissance || ''}` : ' ………………………………'}</span>
                    </div>
                  </div>
                </div>

                {/* Section IV */}
                <div>
                  <h4 className="font-black text-sm uppercase tracking-wide border-b-2 border-gray-800 pb-1 mb-3">
                    IV. &nbsp;Certification
                  </h4>
                  <p className="text-sm font-serif text-justify leading-relaxed text-gray-800">
                    Le présent certificat atteste que la parcelle ci-dessus est conforme aux dispositions légales et réglementaires en vigueur.
                  </p>
                  <div className="mt-3 flex flex-col items-center">
                    <QRCodeVisual value={selected.qr_code} />
                    {selected.qr_code && (
                      <p className="mt-1 font-mono text-xs text-gray-400 text-center break-all max-w-28">
                        {selected.qr_code.slice(0, 16)}…
                      </p>
                    )}
                  </div>
                </div>
              </div>

              {/* Section II */}
              <div className="mb-5">
                <h4 className="font-black text-sm uppercase tracking-wide border-b-2 border-gray-800 pb-1 mb-3">
                  II. &nbsp;Références de la Parcelle
                </h4>
                <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm font-serif">
                  <div className="border-b border-dotted border-gray-400 pb-1">
                    <span className="text-gray-600">Localité :</span>
                    <span className="ml-1 font-semibold">{selected.localite || ' ……………………………………………'}</span>
                  </div>
                  <div className="border-b border-dotted border-gray-400 pb-1">
                    <span className="text-gray-600">Superficie :</span>
                    <span className="ml-1 font-semibold">{selected.superficie_m2 ? `${selected.superficie_m2.toLocaleString('fr-FR')} m²` : ' ……… m²'}</span>
                  </div>
                  <div className="border-b border-dotted border-gray-400 pb-1">
                    <span className="text-gray-600">Lot :</span>
                    <span className="ml-1">{selected.lot || ' ………………………'}</span>
                    <span className="ml-4 text-gray-600">Parcelle :</span>
                    <span className="ml-1">{selected.parcelle || ' ………'}</span>
                  </div>
                  <div className="border-b border-dotted border-gray-400 pb-1 font-semibold">
                    <span className="text-gray-600 font-normal">Coordonnées GPS :</span>
                    <span className="ml-1">Lat. {selected.latitude || '…………'}</span>
                    <span className="ml-2">Long. {selected.longitude || '…………'}</span>
                  </div>
                </div>
              </div>

              {/* Section III + Directeur */}
              <div className="grid grid-cols-2 gap-8 mt-2">
                {/* III */}
                <div>
                  <h4 className="font-black text-sm uppercase tracking-wide border-b-2 border-gray-800 pb-1 mb-3">
                    III. &nbsp;Validation Technique
                  </h4>
                  <div className="space-y-5 text-sm font-serif">
                    <div>
                      <p><span className="font-bold">Huissier :</span> Nom et Prénoms :</p>
                      <p className="font-semibold mt-0.5">{selected.huissier_nom || '…………………………………………'}</p>
                      <div className="mt-3 pt-1 border-t border-gray-400 text-xs text-gray-500">Signature & Cachet</div>
                    </div>
                    <div>
                      <p><span className="font-bold">Topographe :</span> Nom et Prénoms :</p>
                      <p className="font-semibold mt-0.5">{selected.topographe_nom || '…………………………………………'}</p>
                      <div className="mt-3 pt-1 border-t border-gray-400 text-xs text-gray-500">Signature & Cachet</div>
                    </div>
                  </div>
                </div>

                {/* Signature Directeur */}
                <div className="flex flex-col items-center justify-center">
                  <div className="text-center">
                    <p className="font-black text-sm underline">Le Directeur de l'Urbanisme</p>
                    <div className="mt-8 w-36 border-t border-gray-600 mx-auto pt-1 text-xs text-gray-500 text-center">
                      Signature & Cachet
                    </div>
                    {(selected.etat === 'SIGNE_DIRECTEUR' || selected.etat === 'ARCHIVE') && (
                      <div className="mt-3 flex items-center gap-1.5 justify-center">
                        <CheckCircle className="h-4 w-4 text-emerald-600" />
                        <span className="text-xs text-emerald-700 font-semibold">SIGNÉ</span>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Pied de page */}
              <div className="mt-6 pt-3 border-t border-gray-200 text-center text-xs text-gray-500 italic">
                Note : le CCFM n'est ni un acte de cession, ni un titre foncier, mais une condition préalable à tout établissement de ces derniers, mais aussi à toute transaction légale
              </div>
            </div>

            <div className="h-1.5 bg-gradient-to-r from-orange-500 via-white to-green-600" />
          </div>

          {/* Blockchain / DAR badges */}
          {(selected.blockchain_hash || selected.dar_reference) && (
            <div className="flex gap-3 justify-center flex-wrap max-w-3xl mx-auto">
              {selected.blockchain_hash && (
                <div className="flex items-center gap-2 bg-teal-50 border border-teal-200 rounded-lg px-4 py-2 text-xs text-teal-700">
                  <CheckCircle className="h-3.5 w-3.5" />
                  <span className="font-semibold">Ancré blockchain</span>
                  <span className="font-mono opacity-70">{selected.blockchain_hash.slice(0, 16)}…</span>
                </div>
              )}
              {selected.dar_reference && (
                <div className="flex items-center gap-2 bg-indigo-50 border border-indigo-200 rounded-lg px-4 py-2 text-xs text-indigo-700">
                  <CheckCircle className="h-3.5 w-3.5" />
                  <span className="font-semibold">Archivé DAR</span>
                  <span className="font-mono opacity-70">{selected.dar_reference}</span>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// COMPOSANT 2 — Fiche de Vérification Administrative Foncière
// ─────────────────────────────────────────────────────────────────────────────
function FicheVerificationPane({ demandes }: { demandes: any[] }) {
  const [selected, setSelected] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [activeSection, setActiveSection] = useState<number | null>(null);

  const [form, setForm] = useState({
    // §1 Références
    objet_demande: '',
    // §2 Arrêté
    arrete_numero: '', arrete_date_signature: '', arrete_autorite: '',
    jo_publie: null as boolean | null, jo_reference: '',
    // §3 Assiette
    parcelle_ref_titre: '', superficie_mesuree: '', gps_fournis: '',
    parcelle_conforme: null as boolean | null,
    // §4 Titre foncier
    titre_foncier_numero: '', titre_foncier_emetteur: '',
    titre_authentique: null as boolean | null,
    // §5 Résultats (auto calculés)
    // §6 Observations
    observations_admin: '',
    // §7 Décision — B13 : champ 'decision' aligné avec deposerFicheConstat
    decision: '' as 'AUTORISATION' | 'REFUS' | '',
    motif_refus: '',
    // §8 Signatures
    agent_verificateur: '', agent_fonction: '',
    chef_service: '', chef_fonction: '',
  });

  const handleSelect = async (d: any) => {
    setSelected(d);
    if (!d) return;
    try {
      const fiche = await mockBackend.getFicheVerification(d.ccfm_id);
      setForm(f => ({
        ...f,
        objet_demande: `Vérification CCFM — Lot ${d.lot} / Parcelle ${d.parcelle}`,
        arrete_numero: fiche.arrete_numero || '',
        arrete_date_signature: fiche.arrete_date_signature || '',
        arrete_autorite: fiche.arrete_autorite || '',
        jo_publie: fiche.jo_publie,
        jo_reference: fiche.jo_reference || '',
        parcelle_conforme: fiche.parcelle_conforme,
        titre_foncier_numero: fiche.titre_foncier_numero || '',
        titre_foncier_emetteur: fiche.titre_foncier_emetteur || '',
        titre_authentique: fiche.titre_authentique,
        decision: fiche.decision || '',
        observations_admin: fiche.observations_admin || '',
        agent_verificateur: fiche.agent_verificateur || '',
        agent_fonction: fiche.agent_fonction || '',
        chef_service: fiche.chef_service || '',
        chef_fonction: fiche.chef_fonction || '',
      }));
    } catch { /* nouveau */ }
  };

  const handleSave = async () => {
    if (!selected) return;
    if (!form.decision) {
      toast.error('La décision (§7) est obligatoire');
      return;
    }
    setSaving(true);
    try {
      // B13 — soumettreDecisionAdmin remplacé par deposerFicheConstat (méthode correcte)
      await mockBackend.deposerFicheConstat({
        ccfm_id:                selected.ccfm_id,
        objet_demande:          `Vérification CCFM — Lot ${selected.lot} / Parcelle ${selected.parcelle}`,
        arrete_numero:          form.arrete_numero,
        arrete_date_signature:  form.arrete_date_signature,
        arrete_autorite:        form.arrete_autorite,
        jo_publie:              form.jo_publie,
        jo_reference:           form.jo_reference,
        parcelle_conforme:      form.parcelle_conforme,
        titre_foncier_numero:   form.titre_foncier_numero,
        titre_foncier_emetteur: form.titre_foncier_emetteur,
        titre_authentique:      form.titre_authentique,
        decision:               form.decision,
        motif_refus:            form.motif_refus,
        observations:           form.observations_admin,
        topographe_nom:         form.agent_verificateur || 'Agent vérificateur',
        topographe_fonction:    form.agent_fonction     || 'Vérificateur administratif',
      });
      toast.success(`Fiche de vérification enregistrée — ${selected.nus}`);
    } catch { toast.error('Erreur d\'enregistrement'); }
    finally { setSaving(false); }
  };

  const f = form;
  const sf = (k: keyof typeof form) => (e: any) => setForm(prev => ({ ...prev, [k]: e.target.value }));

  const SectionHeader = ({ n, title, icon }: { n: number; title: string; icon: any }) => (
    <button
      onClick={() => setActiveSection(activeSection === n ? null : n)}
      className="w-full flex items-center gap-3 p-4 bg-gray-50 hover:bg-orange-50 rounded-lg border border-gray-200 hover:border-orange-200 transition-colors group"
    >
      <div className="w-7 h-7 rounded-full bg-orange-100 text-orange-700 flex items-center justify-center text-sm font-black flex-shrink-0 group-hover:bg-orange-200">
        {n}
      </div>
      <span className="text-sm font-bold text-gray-800">{title}</span>
      <ChevronDown className={`h-4 w-4 text-gray-400 ml-auto transition-transform ${activeSection === n ? 'rotate-180' : ''}`} />
    </button>
  );

  return (
    <div className="space-y-4">
      {/* En-tête officiel */}
      <Card className="p-0 overflow-hidden">
        <div className="bg-gradient-to-r from-gray-800 to-gray-900 text-white px-6 py-4">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-xs font-bold uppercase tracking-widest text-gray-400 mb-1">RÉPUBLIQUE DU NIGER</p>
              <h2 className="text-base font-black uppercase tracking-wide">Fiche de Vérification Administrative Foncière</h2>
              <p className="text-xs text-gray-400 mt-0.5">Direction Générale de l'Urbanisme · Ministère de l'Urbanisme, du Logement</p>
            </div>
            <div className="text-4xl">🇳🇪</div>
          </div>
        </div>

        {/* Sélecteur */}
        <div className="p-4 border-b">
          <DemandePicker demandes={demandes} selected={selected} onSelect={handleSelect} />
        </div>

        {selected && (
          <div className="p-4 bg-blue-50 border-b text-xs text-blue-800 grid grid-cols-4 gap-4">
            <div><span className="text-blue-500 font-semibold block">N° de dossier</span>{selected.nus}</div>
            <div><span className="text-blue-500 font-semibold block">Date de la demande</span>{selected.date_enregistrement?.slice(0, 10)}</div>
            <div><span className="text-blue-500 font-semibold block">Demandeur</span>{selected.nom_prenom}</div>
            <div><span className="text-blue-500 font-semibold block">Objet</span><input className="bg-transparent border-b border-blue-300 w-full text-xs outline-none" value={f.objet_demande} onChange={sf('objet_demande')} /></div>
          </div>
        )}
      </Card>

      {!selected ? (
        <div className="flex flex-col items-center justify-center py-20 text-gray-400 space-y-3">
          <ClipboardCheck className="h-16 w-16 opacity-20" />
          <p className="text-sm">Sélectionnez une demande pour remplir la fiche de vérification</p>
        </div>
      ) : (
        <div className="space-y-3">

          {/* §1 Références de la demande */}
          <SectionHeader n={1} title="Références de la demande" icon={FileText} />
          {(activeSection === 1 || activeSection === null) && (
            <Card className="p-5 space-y-4 border-l-4 border-gray-400">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Numéro de dossier (NUS)</label>
                  <div className="mt-1 px-3 py-2 rounded-lg border bg-gray-50 text-sm font-mono text-gray-700">{selected.nus}</div>
                </div>
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Date de la demande</label>
                  <div className="mt-1 px-3 py-2 rounded-lg border bg-gray-50 text-sm text-gray-700">{selected.date_enregistrement?.slice(0, 10) || '—'}</div>
                </div>
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Demandeur</label>
                  <div className="mt-1 px-3 py-2 rounded-lg border bg-gray-50 text-sm font-semibold text-gray-700">{selected.nom_prenom}</div>
                </div>
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Objet de la demande</label>
                  <Input className="mt-1 text-sm" value={f.objet_demande} onChange={sf('objet_demande')} placeholder="Vérification CCFM — Lot … / Parcelle …" />
                </div>
              </div>
            </Card>
          )}

          {/* §2 Arrêté de lotissement */}
          <SectionHeader n={2} title="Vérification de l'arrêté de lotissement" icon={FileText} />
          {(activeSection === 2 || activeSection === null) && (
            <Card className="p-5 space-y-4 border-l-4 border-orange-400">
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Numéro de l'arrêté</label>
                  <Input className="mt-1 text-sm" placeholder="ARRETE-2024-XXXX" value={f.arrete_numero} onChange={sf('arrete_numero')} />
                </div>
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Date de signature</label>
                  <Input className="mt-1 text-sm" placeholder="JJ/MM/AAAA" value={f.arrete_date_signature} onChange={sf('arrete_date_signature')} />
                </div>
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Autorité signataire</label>
                  <Input className="mt-1 text-sm" placeholder="Ministre de l'Urbanisme" value={f.arrete_autorite} onChange={sf('arrete_autorite')} />
                </div>
              </div>
              <div>
                <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide block mb-2">Publication au Journal Officiel</label>
                <div className="flex gap-6">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="radio" name="jo" checked={f.jo_publie === true} onChange={() => setForm(p => ({ ...p, jo_publie: true }))} className="accent-orange-600" />
                    <span className="text-sm font-semibold text-green-700">☑ Oui</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="radio" name="jo" checked={f.jo_publie === false} onChange={() => setForm(p => ({ ...p, jo_publie: false }))} className="accent-red-600" />
                    <span className="text-sm font-semibold text-red-700">☑ Non</span>
                  </label>
                  {f.jo_publie && (
                    <div className="flex items-center gap-2 flex-1">
                      <span className="text-xs text-gray-500">→ Référence JO :</span>
                      <Input className="text-sm h-7 flex-1" placeholder="JO-AAAA-N°XX" value={f.jo_reference} onChange={sf('jo_reference')} />
                    </div>
                  )}
                </div>
              </div>
            </Card>
          )}

          {/* §3 Assiette foncière */}
          <SectionHeader n={3} title="Vérification de l'assiette foncière" icon={MapPin} />
          {(activeSection === 3 || activeSection === null) && (
            <Card className="p-5 space-y-4 border-l-4 border-blue-400">
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Réf. parcelle / titre foncier</label>
                  <Input className="mt-1 text-sm" placeholder="TF-NIA-2024-XXXX" value={f.parcelle_ref_titre} onChange={sf('parcelle_ref_titre')} />
                </div>
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Superficie déclarée</label>
                  <div className="relative mt-1">
                    <Input className="text-sm pr-8" placeholder="500" value={selected.superficie_m2 || f.superficie_mesuree} readOnly={!!selected.superficie_m2} />
                    <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-gray-400">m²</span>
                  </div>
                </div>
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Coordonnées GPS fournies</label>
                  <Input className="mt-1 text-sm" placeholder="Lat. X — Long. Y"
                    value={selected.latitude ? `${selected.latitude}, ${selected.longitude}` : f.gps_fournis}
                    readOnly={!!selected.latitude} onChange={sf('gps_fournis')} />
                </div>
              </div>
              <div>
                <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide block mb-2">
                  Correspondance avec l'assiette autorisée par arrêté
                </label>
                <div className="flex gap-6">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="radio" name="conforme" checked={f.parcelle_conforme === true} onChange={() => setForm(p => ({ ...p, parcelle_conforme: true }))} className="accent-green-600" />
                    <span className="text-sm font-semibold text-green-700">☑ Conforme</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="radio" name="conforme" checked={f.parcelle_conforme === false} onChange={() => setForm(p => ({ ...p, parcelle_conforme: false }))} className="accent-red-600" />
                    <span className="text-sm font-semibold text-red-700">☑ Non conforme</span>
                  </label>
                </div>
              </div>
            </Card>
          )}

          {/* §4 Titre foncier */}
          <SectionHeader n={4} title="Vérification du titre foncier (si applicable)" icon={Stamp} />
          {(activeSection === 4 || activeSection === null) && (
            <Card className="p-5 space-y-4 border-l-4 border-purple-400">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Numéro du titre foncier</label>
                  <Input className="mt-1 text-sm" placeholder="TF-NIA-AAAA-XXXX" value={f.titre_foncier_numero} onChange={sf('titre_foncier_numero')} />
                </div>
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Émetteur</label>
                  <Input className="mt-1 text-sm" placeholder="Direction Cadastre de Niamey" value={f.titre_foncier_emetteur} onChange={sf('titre_foncier_emetteur')} />
                </div>
              </div>
              <div>
                <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide block mb-2">
                  Authenticité confirmée aux archives domaniales
                </label>
                <div className="flex gap-6">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="radio" name="auth" checked={f.titre_authentique === true} onChange={() => setForm(p => ({ ...p, titre_authentique: true }))} className="accent-green-600" />
                    <span className="text-sm font-semibold text-green-700">☑ Oui</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="radio" name="auth" checked={f.titre_authentique === false} onChange={() => setForm(p => ({ ...p, titre_authentique: false }))} className="accent-red-600" />
                    <span className="text-sm font-semibold text-red-700">☑ Non</span>
                  </label>
                </div>
              </div>
            </Card>
          )}

          {/* §5 Résultats */}
          <SectionHeader n={5} title="Résultats de la vérification" icon={CheckCircle} />
          {(activeSection === 5 || activeSection === null) && (
            <Card className="p-5 space-y-3 border-l-4 border-teal-400 bg-gray-50">
              {[
                {
                  label: 'La parcelle objet de la demande',
                  trueLabel: 'appartient à un lotissement dûment autorisé',
                  falseLabel: "n'appartient pas à un lotissement dûment autorisé",
                  value: f.parcelle_conforme,
                },
                {
                  label: "L'arrêté ministériel correspondant est",
                  trueLabel: 'valide et publié',
                  falseLabel: 'inexistant ou non publié',
                  value: f.jo_publie,
                },
                {
                  label: 'Les coordonnées GPS et/ou la référence cadastrale',
                  trueLabel: "concordent avec l'assiette autorisée",
                  falseLabel: "ne concordent pas avec l'assiette autorisée",
                  value: f.parcelle_conforme,
                },
                {
                  label: 'Le titre foncier présenté est',
                  trueLabel: 'authentique',
                  falseLabel: 'contestable',
                  value: f.titre_authentique,
                },
              ].map((r, i) => (
                <div key={i} className="flex items-center gap-3 text-sm py-1.5 border-b border-gray-200 last:border-0">
                  <span className="text-gray-600 flex-1">{r.label}</span>
                  {r.value === null ? (
                    <span className="text-gray-400 italic text-xs">☐ À compléter</span>
                  ) : r.value ? (
                    <span className="flex items-center gap-1.5 text-green-700 font-semibold">
                      <CheckCircle className="h-4 w-4" />{r.trueLabel}
                    </span>
                  ) : (
                    <span className="flex items-center gap-1.5 text-red-700 font-semibold">
                      <XCircle className="h-4 w-4" />{r.falseLabel}
                    </span>
                  )}
                </div>
              ))}
            </Card>
          )}

          {/* §6 Observations */}
          <SectionHeader n={6} title="Observations de l'administration" icon={Eye} />
          {(activeSection === 6 || activeSection === null) && (
            <Card className="p-5 border-l-4 border-gray-400">
              <Textarea
                className="min-h-24 text-sm"
                placeholder="Observations complémentaires de l'agent vérificateur…"
                value={f.observations_admin}
                onChange={e => setForm(p => ({ ...p, observations_admin: e.target.value }))}
              />
            </Card>
          )}

          {/* §7 Décision */}
          <SectionHeader n={7} title="Décision" icon={Stamp} />
          {(activeSection === 7 || activeSection === null) && (
            <Card className="p-5 space-y-4 border-l-4 border-indigo-400">
              <div className="flex gap-6">
                <label className="flex items-center gap-3 cursor-pointer flex-1 p-3 rounded-lg border-2 transition-colors"
                  style={{ borderColor: f.decision === 'AUTORISATION' ? '#059669' : '#e5e7eb', background: f.decision === 'AUTORISATION' ? '#f0fdf4' : 'white' }}>
                  <input type="radio" name="decision" value="AUTORISATION"
                    checked={f.decision === 'AUTORISATION'}
                    onChange={() => setForm(p => ({ ...p, decision: 'AUTORISATION' }))}
                    className="accent-green-600" />
                  <div>
                    <p className="font-bold text-green-700 text-sm">☑ Autorisation de poursuivre la procédure de constat</p>
                    <p className="text-xs text-gray-500">Le dossier est conforme — la mission terrain peut démarrer</p>
                  </div>
                </label>
                <label className="flex items-center gap-3 cursor-pointer flex-1 p-3 rounded-lg border-2 transition-colors"
                  style={{ borderColor: f.decision === 'REFUS' ? '#dc2626' : '#e5e7eb', background: f.decision === 'REFUS' ? '#fef2f2' : 'white' }}>
                  <input type="radio" name="decision" value="REFUS"
                    checked={f.decision === 'REFUS'}
                    onChange={() => setForm(p => ({ ...p, decision: 'REFUS' }))}
                    className="accent-red-600" />
                  <div>
                    <p className="font-bold text-red-700 text-sm">☑ Refus motivé</p>
                    <p className="text-xs text-gray-500">Arrêté inexistant / non publié / incohérence GPS / faux titre</p>
                  </div>
                </label>
              </div>
              {f.decision === 'REFUS' && (
                <div>
                  <label className="text-xs font-semibold text-red-600 uppercase tracking-wide">Motif du refus</label>
                  <Textarea className="mt-1 border-red-200 text-sm min-h-16"
                    placeholder="Préciser le motif de refus…"
                    value={f.motif_refus}
                    onChange={e => setForm(p => ({ ...p, motif_refus: e.target.value }))} />
                </div>
              )}
            </Card>
          )}

          {/* §8 Signatures */}
          <SectionHeader n={8} title="Signatures et cachets" icon={User} />
          {(activeSection === 8 || activeSection === null) && (
            <Card className="p-5 space-y-4 border-l-4 border-gray-400">
              <div className="grid grid-cols-2 gap-8">
                <div className="space-y-2">
                  <p className="text-xs font-black uppercase text-gray-600 tracking-widest">Agent vérificateur</p>
                  <Input className="text-sm" placeholder="Nom et prénom" value={f.agent_verificateur} onChange={sf('agent_verificateur')} />
                  <Input className="text-sm" placeholder="Fonction" value={f.agent_fonction} onChange={sf('agent_fonction')} />
                  <div className="h-10 border-b border-gray-300 mt-4">
                    <p className="text-xs text-gray-400">Signature & Cachet</p>
                  </div>
                </div>
                <div className="space-y-2">
                  <p className="text-xs font-black uppercase text-gray-600 tracking-widest">Chef de service / Directeur</p>
                  <Input className="text-sm" placeholder="Nom et prénom" value={f.chef_service} onChange={sf('chef_service')} />
                  <Input className="text-sm" placeholder="Fonction" value={f.chef_fonction} onChange={sf('chef_fonction')} />
                  <div className="h-10 border-b border-gray-300 mt-4">
                    <p className="text-xs text-gray-400">Signature & Cachet</p>
                  </div>
                </div>
              </div>
            </Card>
          )}

          {/* §9 Note légale */}
          <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-800 leading-relaxed">
            <strong>9. Note légale obligatoire :</strong> La présente fiche de vérification administrative ne constitue pas un titre de propriété. Elle atteste uniquement de la conformité légale et réglementaire d'une parcelle avec un arrêté de lotissement publié. Elle est exigible avant tout constat d'huissier et/ou intervention technique.
          </div>

          {/* Actions */}
          <div className="flex gap-3 justify-end pb-4">
            <Button variant="outline" onClick={() => setForm(f => ({
              ...f,
              arrete_numero: '', arrete_date_signature: '', arrete_autorite: '',
              jo_publie: null, jo_reference: '',
              parcelle_ref_titre: '', parcelle_conforme: null,
              titre_foncier_numero: '', titre_foncier_emetteur: '', titre_authentique: null,
              observations_admin: '', decision: '', motif_refus: '',
              agent_verificateur: '', agent_fonction: '', chef_service: '', chef_fonction: '',
            }))}>
              Réinitialiser
            </Button>
            <Button className="bg-orange-600 hover:bg-orange-700 gap-2" disabled={saving} onClick={handleSave}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Enregistrer la fiche
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// COMPOSANT 3 — Constat Terrain (Topographe + Huissier)
// ─────────────────────────────────────────────────────────────────────────────
function ConstatTerrainPane({ demandes }: { demandes: any[] }) {
  const [selected, setSelected] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    topographe_nom: '', topographe_superficie_mesuree: '',
    topographe_ecart_m2: '', topographe_conformite: null as boolean | null,
    topographe_date_mission: '', topographe_observations: '',
    huissier_nom: '', huissier_date_constat: '', huissier_observations: '',
    photos_count: 0,
  });

  const handleSelect = async (d: any) => {
    setSelected(d);
    if (!d) return;
    try {
      const c = await mockBackend.getConstatTerrain(d.ccfm_id);
      setForm(prev => ({ ...prev, ...c,
        topographe_superficie_mesuree: c.topographe_superficie_mesuree?.toString() || '',
        topographe_ecart_m2: c.topographe_ecart_m2?.toString() || '',
      }));
    } catch { /* nouveau */ }
  };

  const sf = (k: keyof typeof form) => (e: any) => setForm(p => ({ ...p, [k]: e.target.value }));

  const handleSave = async () => {
    if (!selected) return;
    if (!form.topographe_nom) {
      toast.error('Le nom du topographe est obligatoire');
      return;
    }
    setSaving(true);
    try {
      // B14 — soumettreConstat remplacé par deposerFicheConstat
      await mockBackend.deposerFicheConstat({
        ccfm_id:               selected.ccfm_id,
        topographe_nom:        form.topographe_nom,
        superficie_mesuree_m2: parseFloat(form.topographe_superficie_mesuree) || null,
        observations:          form.topographe_observations,
        topographe_date_mission: form.topographe_date_mission || new Date().toISOString().slice(0, 10),
        parcelle_conforme:     form.topographe_conformite,
        decision:              form.topographe_conformite ? 'AUTORISATION' : 'REFUS',
        huissier_nom:          form.huissier_nom,
        huissier_date_constat: form.huissier_date_constat,
      });
      toast.success(`Constat terrain enregistré — ${selected.nus}`);
    } catch { toast.error("Erreur d'enregistrement"); }
    finally { setSaving(false); }
  };

  const delta = form.topographe_superficie_mesuree && selected?.superficie_m2
    ? parseFloat(form.topographe_superficie_mesuree) - selected.superficie_m2
    : null;
  const ecart = delta !== null ? Math.abs(delta) : null;

  return (
    <div className="space-y-5">
      <Card className="p-4 border-l-4 border-indigo-500">
        <div className="flex items-start gap-3 mb-4">
          <Ruler className="h-5 w-5 text-indigo-600 flex-shrink-0 mt-0.5" />
          <div>
            <h3 className="font-bold text-gray-900">Rapport de Constat Terrain</h3>
            <p className="text-xs text-gray-500">À remplir par le Topographe (obligatoire) et l'Huissier (facultatif) lors de leur mission de vérification sur le terrain</p>
          </div>
        </div>
        <DemandePicker demandes={demandes} selected={selected} onSelect={handleSelect} />
      </Card>

      {!selected ? (
        <div className="flex flex-col items-center justify-center py-20 text-gray-400 space-y-3">
          <Ruler className="h-16 w-16 opacity-20" />
          <p className="text-sm">Sélectionnez un dossier pour saisir le constat terrain</p>
        </div>
      ) : (
        <>
          {/* Info référence */}
          <Card className="p-4 bg-indigo-50 border-indigo-200">
            <div className="grid grid-cols-4 gap-4 text-xs">
              <div><span className="text-indigo-500 font-semibold block">NUS</span><span className="font-mono font-bold">{selected.nus}</span></div>
              <div><span className="text-indigo-500 font-semibold block">Demandeur</span>{selected.nom_prenom}</div>
              <div><span className="text-indigo-500 font-semibold block">Localité / Lot</span>{selected.localite} — {selected.lot}</div>
              <div><span className="text-indigo-500 font-semibold block">Superficie déclarée</span><span className="font-bold">{selected.superficie_m2?.toLocaleString('fr-FR')} m²</span></div>
            </div>
          </Card>

          <div className="grid grid-cols-5 gap-5">
            {/* ── TOPOGRAPHE (colonne gauche, 3/5) */}
            <Card className="col-span-3 p-5 space-y-4 border-t-4 border-blue-600">
              <div className="flex items-center gap-2 mb-1">
                <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-lg">📐</div>
                <div>
                  <h4 className="font-black text-blue-900 text-sm">RAPPORT DU TOPOGRAPHE</h4>
                  <p className="text-xs text-blue-600">Obligatoire — Ordre de mission requis</p>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Nom du Topographe *</label>
                  <Input className="mt-1 text-sm" placeholder="IBRAHIM Souleymane" value={form.topographe_nom} onChange={sf('topographe_nom')} />
                </div>
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Date de mission *</label>
                  <Input className="mt-1 text-sm" type="date" value={form.topographe_date_mission} onChange={sf('topographe_date_mission')} />
                </div>
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Superficie mesurée (m²) *</label>
                  <Input className="mt-1 text-sm" type="number" placeholder="498.5" value={form.topographe_superficie_mesuree} onChange={sf('topographe_superficie_mesuree')} />
                </div>
                <div>
                  <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Écart constaté (m²)</label>
                  <div className={`mt-1 px-3 py-2 rounded-lg border text-sm font-mono ${ecart !== null ? (ecart <= 5 ? 'bg-green-50 border-green-300 text-green-800' : 'bg-red-50 border-red-300 text-red-800') : 'bg-gray-50 border-gray-200 text-gray-400'}`}>
                    {ecart !== null ? `${delta! > 0 ? '+' : ''}${delta!.toFixed(1)} m²` : '— auto calculé'}
                  </div>
                </div>
              </div>

              <div>
                <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide block mb-2">Conformité du terrain</label>
                <div className="flex gap-6">
                  <label className="flex items-center gap-2 cursor-pointer p-2 rounded-lg border-2 transition-colors"
                    style={{ borderColor: form.topographe_conformite === true ? '#059669' : '#e5e7eb', background: form.topographe_conformite === true ? '#f0fdf4' : 'white' }}>
                    <input type="radio" name="topo_conf" checked={form.topographe_conformite === true}
                      onChange={() => setForm(p => ({ ...p, topographe_conformite: true }))} className="accent-green-600" />
                    <span className="text-sm font-bold text-green-700">✅ Conforme</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer p-2 rounded-lg border-2 transition-colors"
                    style={{ borderColor: form.topographe_conformite === false ? '#dc2626' : '#e5e7eb', background: form.topographe_conformite === false ? '#fef2f2' : 'white' }}>
                    <input type="radio" name="topo_conf" checked={form.topographe_conformite === false}
                      onChange={() => setForm(p => ({ ...p, topographe_conformite: false }))} className="accent-red-600" />
                    <span className="text-sm font-bold text-red-700">❌ Non conforme</span>
                  </label>
                </div>
              </div>

              <div>
                <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Observations techniques *</label>
                <Textarea className="mt-1 min-h-24 text-sm" placeholder="Mesures GPS précises. Description du terrain, bornes, accès, état du sol…" value={form.topographe_observations} onChange={sf('topographe_observations')} />
              </div>

              <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg">
                <p className="text-xs font-semibold text-blue-700 mb-1">📷 Photos géolocalisées</p>
                <div className="flex items-center gap-3">
                  <div className="flex-1 bg-white border border-blue-200 rounded h-8 flex items-center px-3 text-xs text-gray-400">
                    {form.photos_count > 0 ? `${form.photos_count} photo(s) attachée(s)` : 'Aucune photo — upload backend requis'}
                  </div>
                  <Button size="sm" variant="outline" className="border-blue-300 text-blue-700 text-xs"
                    onClick={() => { setForm(p => ({ ...p, photos_count: p.photos_count + 3 })); toast.info('Simulation: 3 photos ajoutées'); }}>
                    + Photos
                  </Button>
                </div>
              </div>

              {/* Zone signature topographe */}
              <div className="border-t pt-3 text-center">
                <p className="text-xs text-gray-500 mb-2">Signature & Cachet du Topographe</p>
                <div className="h-14 border border-dashed border-gray-300 rounded-lg flex items-center justify-center text-xs text-gray-400">
                  Zone de signature électronique
                </div>
              </div>
            </Card>

            {/* ── HUISSIER (colonne droite, 2/5) */}
            <Card className="col-span-2 p-5 space-y-4 border-t-4 border-amber-500">
              <div className="flex items-center gap-2 mb-1">
                <div className="w-8 h-8 rounded-full bg-amber-100 text-amber-700 flex items-center justify-center text-lg">⚖️</div>
                <div>
                  <h4 className="font-black text-amber-900 text-sm">CONSTAT HUISSIER</h4>
                  <p className="text-xs text-amber-600">Facultatif</p>
                </div>
              </div>

              <div>
                <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Nom de l'Huissier</label>
                <Input className="mt-1 text-sm" placeholder="Me DIALLO Moussa" value={form.huissier_nom} onChange={sf('huissier_nom')} />
              </div>
              <div>
                <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Date du constat</label>
                <Input className="mt-1 text-sm" type="date" value={form.huissier_date_constat} onChange={sf('huissier_date_constat')} />
              </div>
              <div>
                <label className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Observations / Constat</label>
                <Textarea className="mt-1 min-h-32 text-sm" placeholder="Constat de délimitation effectué en présence du propriétaire. Bornes visibles…" value={form.huissier_observations} onChange={sf('huissier_observations')} />
              </div>

              {/* Alerte si huissier non renseigné */}
              {!form.huissier_nom && (
                <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg flex gap-2">
                  <AlertTriangle className="h-4 w-4 text-amber-500 flex-shrink-0 mt-0.5" />
                  <p className="text-xs text-amber-700">Le constat d'huissier est facultatif mais recommandé pour les parcelles en litige ou de grande superficie.</p>
                </div>
              )}

              {/* Zone signature huissier */}
              <div className="border-t pt-3 text-center">
                <p className="text-xs text-gray-500 mb-2">Signature & Cachet de l'Huissier</p>
                <div className="h-14 border border-dashed border-gray-300 rounded-lg flex items-center justify-center text-xs text-gray-400">
                  Zone de signature électronique
                </div>
              </div>

              {/* GPS coordonnées vérifiées */}
              {selected.latitude && (
                <div className="p-3 bg-green-50 border border-green-200 rounded-lg text-xs text-green-700">
                  <p className="font-semibold mb-1">📍 Coordonnées GPS vérifiées</p>
                  <p>Latitude : <span className="font-mono">{selected.latitude}</span></p>
                  <p>Longitude : <span className="font-mono">{selected.longitude}</span></p>
                </div>
              )}
            </Card>
          </div>

          {/* Actions */}
          <div className="flex gap-3 justify-end">
            <Button variant="outline" className="gap-2" onClick={() => toast.info('Aperçu PDF — backend requis')}>
              <Eye className="h-4 w-4" />Aperçu rapport
            </Button>
            <Button className="bg-indigo-600 hover:bg-indigo-700 gap-2" disabled={saving || !form.topographe_nom} onClick={handleSave}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Soumettre le rapport terrain
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// PAGE PRINCIPALE
// ─────────────────────────────────────────────────────────────────────────────
type DocTab = 'certificat' | 'fiche_verification' | 'constat_terrain';

const DOC_TABS: { id: DocTab; label: string; icon: string; desc: string }[] = [
  { id: 'certificat',        label: 'Certificat CCFM',                icon: '📜', desc: 'Formulaire officiel A4 · Impression · PDF' },
  { id: 'fiche_verification', label: 'Fiche de Vérification Admin.',  icon: '📋', desc: 'Arrêté · Assiette · Titre foncier · Décision' },
  { id: 'constat_terrain',   label: 'Constat Terrain',                icon: '📐', desc: 'Topographe + Huissier · Mission terrain' },
];

export function CCFMDocumentsPage() {
  const [tab, setTab] = useState<DocTab>('certificat');
  const [demandes, setDemandes] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    mockBackend.getDemandesCCFM({}).then(setDemandes).finally(() => setLoading(false));
  }, []);

  if (loading) return (
    <div className="flex justify-center py-20">
      <Loader2 className="h-8 w-8 animate-spin text-orange-500" />
    </div>
  );

  return (
    <div className="space-y-5">
      {/* Sélecteur de document */}
      <div className="grid grid-cols-3 gap-3">
        {DOC_TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`p-4 rounded-xl border-2 text-left transition-all ${tab === t.id
              ? 'border-orange-500 bg-orange-50 shadow-sm'
              : 'border-gray-200 hover:border-orange-200 hover:bg-orange-50/50'
            }`}>
            <div className="flex items-start gap-3">
              <span className="text-2xl">{t.icon}</span>
              <div>
                <p className={`text-sm font-bold ${tab === t.id ? 'text-orange-900' : 'text-gray-800'}`}>{t.label}</p>
                <p className="text-xs text-gray-500 mt-0.5">{t.desc}</p>
              </div>
            </div>
          </button>
        ))}
      </div>

      {/* Contenu */}
      {tab === 'certificat'         && <CertificatCCFMPane demandes={demandes} />}
      {tab === 'fiche_verification'  && <FicheVerificationPane demandes={demandes} />}
      {tab === 'constat_terrain'     && <ConstatTerrainPane demandes={demandes} />}
    </div>
  );
}

export default CCFMDocumentsPage;
