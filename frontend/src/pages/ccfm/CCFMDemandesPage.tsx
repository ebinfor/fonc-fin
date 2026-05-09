/**
 * FONCIER+ v3.0 — CCFM Demandes
 * Workflow réorganisé ERRATUM 11/02/2026 :
 *   GUICHETIÈRE : Enregistrement demande (NUS + paiement 50 000 FCFA)
 *   → chaque transition correspond à un acteur précis
 *
 * Bugs corrigés :
 *   B8  — form.nip → nip_passeport (alignement champ backend)
 *   B9  — handleTransition : messages pour RETRAIT_EN_ATTENTE, RETIRE, REJETE
 *   B11 — Bouton téléchargement PDF utilise pdf_url réel
 *   B12 — load() appelé une seule fois après transition (suppression doublon)
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
import { Search, Plus, Loader2, Download, FileText } from 'lucide-react';
import { ETAT_CONF, TRANSITIONS } from './ccfmConstants';

export default function CCFMDemandesPage() {
  const [demandes, setDemandes]               = useState<any[]>([]);
  const [loading, setLoading]                 = useState(true);
  const [search, setSearch]                   = useState('');
  const [filterEtat, setFilterEtat]           = useState('TOUS');
  const [selected, setSelected]               = useState<any>(null);
  const [processing, setProcessing]           = useState<string | null>(null);
  const [showNouveauForm, setShowNouveauForm] = useState(false);

  // Formulaire nouvelle demande (Guichetière)
  const [form, setForm] = useState({
    nom_prenom: '', nip_passeport: '', date_naissance: '', lieu_naissance: '',
    telephone: '', adresse: '', localite: '', lot: '', parcelle: '', superficie: '',
    numero_arrete: '', numero_titre_foncier: '',
    mode_paiement: 'AIRTEL_MONEY', reference_paiement: '',
  });

  const load = async () => {
    setLoading(true);
    try {
      const d = await mockBackend.getDemandesCCFM({ etat: filterEtat });
      setDemandes(d);
    } catch {
      toast.error('Erreur de chargement des demandes');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [filterEtat]);

  const filtered = search
    ? demandes.filter(d =>
        d.nus?.toLowerCase().includes(search.toLowerCase()) ||
        d.nom_prenom?.toLowerCase().includes(search.toLowerCase()) ||
        d.localite?.toLowerCase().includes(search.toLowerCase()))
    : demandes;

  const handleTransition = async (demande: any, newEtat: string) => {
    setProcessing(demande.ccfm_id);
    try {
      await mockBackend.avancerDemandeCCFM(demande.ccfm_id, newEtat);

      // B9 — messages complets pour tous les états
      const msgs: Record<string, string> = {
        TICKET_EMIS:                  `Ticket RNAF+BGU émis — GPS disponible ✓`,
        VERIFICATION_AUTO:            `Vérification RNAF+BGU lancée pour ${demande.nus}…`,
        ORDRE_MISSION_EMIS:           `Ordre de mission émis au topographe ✓`,
        VISITE_EN_COURS:              `Visite terrain démarrée`,
        FICHE_CONSTAT_DEPOSEE:        `Fiche de constat déposée ✓`,
        RAPPORT_APPRECIATION:         `Rapport d'appréciation généré automatiquement ✓`,
        ATTENTE_SIGNATURE_DIRECTEUR:  `Dossier soumis au Directeur pour signature`,
        SIGNE_DIRECTEUR:              `Certificat CCFM signé — PDF disponible ✓`,
        RETRAIT_EN_ATTENTE:           `${demande.nus} — retrait bénéficiaire préparé`,
        ARCHIVE:                      `${demande.nus} archivé ANNF + blockchain ⛓`,
        RETIRE:                       `Certificat ${demande.nus} retiré — procédure terminée ✓`,
        REJETE:                       `Dossier ${demande.nus} rejeté`,
      };

      // B4 — avancerDemandeCCFM peut auto-avancer vers ATTENTE_SIGNATURE_DIRECTEUR
      // → récupérer l'état réel après transition
      toast.success(msgs[newEtat] || `→ ${ETAT_CONF[newEtat]?.label}`);

      // B12 — un seul rechargement, setSelected sur la version à jour
      await load();
      setSelected((prev: any) =>
        prev?.ccfm_id === demande.ccfm_id
          ? (demandes.find(d => d.ccfm_id === demande.ccfm_id) ?? null)
          : prev
      );
    } catch {
      toast.error('Erreur lors de la transition');
    } finally {
      setProcessing(null);
    }
  };

  const handleNouvelleDemandeSubmit = async () => {
    if (!form.nom_prenom || !form.localite || !form.lot || !form.parcelle) {
      toast.error('Champs obligatoires manquants (nom, localité, lot, parcelle)');
      return;
    }
    if (!form.reference_paiement) {
      toast.error('Référence de paiement obligatoire');
      return;
    }
    setProcessing('new');
    try {
      // B8 — nip_passeport aligné avec le schéma backend
      await mockBackend.creerDemandeCCFM({
        ...form,
        nip_passeport: form.nip_passeport,
        superficie_m2: parseFloat(form.superficie) || 0,
      });
      toast.success('Demande CCFM enregistrée — NUS généré automatiquement ✓');
      setShowNouveauForm(false);
      setForm({
        nom_prenom: '', nip_passeport: '', date_naissance: '', lieu_naissance: '',
        telephone: '', adresse: '', localite: '', lot: '', parcelle: '', superficie: '',
        numero_arrete: '', numero_titre_foncier: '', mode_paiement: 'AIRTEL_MONEY', reference_paiement: '',
      });
      await load();
    } catch {
      toast.error('Erreur lors de l\'enregistrement');
    } finally {
      setProcessing(null);
    }
  };

  // B11 — téléchargement réel du PDF
  const handleDownloadPDF = (demande: any, type: 'certificat' | 'demande' = 'certificat') => {
    const url = type === 'demande' ? demande.pdf_demande_url : demande.pdf_url;
    if (url && !url.startsWith('/tmp')) {
      window.open(url, '_blank');
    } else {
      toast.info(`PDF ${type === 'demande' ? 'formulaire' : 'certificat'} : ${url || 'En cours de génération…'}`);
    }
  };

  return (
    <div className="flex gap-4">
      {/* Liste principale */}
      <div className="flex-1 space-y-4">
        {/* Barre filtres */}
        <Card className="p-4">
          <div className="flex gap-3 items-center">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
              <Input className="pl-9" placeholder="NUS, demandeur, localité…" value={search}
                onChange={e => setSearch(e.target.value)} />
            </div>
            <select className="border rounded-lg px-3 py-2 text-sm" value={filterEtat}
              onChange={e => setFilterEtat(e.target.value)}>
              <option value="TOUS">Tous les états</option>
              {Object.entries(ETAT_CONF).map(([k, v]) => (
                <option key={k} value={k}>{v.label}</option>
              ))}
            </select>
            <Button className="bg-orange-600 hover:bg-orange-700 gap-2"
              onClick={() => setShowNouveauForm(true)}>
              <Plus className="h-4 w-4" />Nouvelle demande
            </Button>
          </div>
        </Card>

        {/* Formulaire enregistrement — GUICHETIÈRE */}
        {showNouveauForm && (
          <Card className="p-5 border-orange-200 bg-orange-50">
            <div className="flex justify-between items-center mb-4">
              <div>
                <h3 className="font-semibold text-orange-800">🪟 Enregistrement — Guichetière</h3>
                <p className="text-xs text-orange-600">Étape 1 sur 7 · Paiement forfaitaire 50 000 FCFA obligatoire</p>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setShowNouveauForm(false)}>✕</Button>
            </div>

            <div className="grid grid-cols-2 gap-3 mb-4">
              <div className="col-span-2">
                <p className="text-xs font-bold text-gray-500 uppercase mb-2">Identité du demandeur</p>
              </div>
              <div>
                <label className="text-xs text-gray-600 font-medium">Nom et prénom(s) *</label>
                <Input className="mt-1" placeholder="MOUSSA Amadou"
                  value={form.nom_prenom} onChange={e => setForm(f => ({ ...f, nom_prenom: e.target.value }))} />
              </div>
              {/* B8 — champ nip_passeport (pas 'nip') */}
              <div>
                <label className="text-xs text-gray-600 font-medium">NIP / Passeport / CNI</label>
                <Input className="mt-1" placeholder="NE-1990-00001"
                  value={form.nip_passeport} onChange={e => setForm(f => ({ ...f, nip_passeport: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-gray-600 font-medium">Date de naissance</label>
                <Input className="mt-1" type="date"
                  value={form.date_naissance} onChange={e => setForm(f => ({ ...f, date_naissance: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-gray-600 font-medium">Lieu de naissance</label>
                <Input className="mt-1" placeholder="Niamey"
                  value={form.lieu_naissance} onChange={e => setForm(f => ({ ...f, lieu_naissance: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-gray-600 font-medium">Téléphone</label>
                <Input className="mt-1" placeholder="+227 90 00 00 00"
                  value={form.telephone} onChange={e => setForm(f => ({ ...f, telephone: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-gray-600 font-medium">Adresse</label>
                <Input className="mt-1" placeholder="Niamey, Quartier…"
                  value={form.adresse} onChange={e => setForm(f => ({ ...f, adresse: e.target.value }))} />
              </div>

              <div className="col-span-2 mt-2">
                <p className="text-xs font-bold text-gray-500 uppercase mb-2">Références de la parcelle</p>
              </div>
              <div>
                <label className="text-xs text-gray-600 font-medium">Localité *</label>
                <Input className="mt-1" placeholder="Niamey"
                  value={form.localite} onChange={e => setForm(f => ({ ...f, localite: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-gray-600 font-medium">Superficie (m²)</label>
                <Input className="mt-1" type="number" placeholder="500"
                  value={form.superficie} onChange={e => setForm(f => ({ ...f, superficie: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-gray-600 font-medium">Lot *</label>
                <Input className="mt-1" placeholder="LOT-1234"
                  value={form.lot} onChange={e => setForm(f => ({ ...f, lot: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-gray-600 font-medium">Parcelle *</label>
                <Input className="mt-1" placeholder="PARC-5678"
                  value={form.parcelle} onChange={e => setForm(f => ({ ...f, parcelle: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-gray-600 font-medium">N° Arrêté de lotissement</label>
                <Input className="mt-1" placeholder="Arrêté n°…"
                  value={form.numero_arrete} onChange={e => setForm(f => ({ ...f, numero_arrete: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-gray-600 font-medium">N° Titre foncier</label>
                <Input className="mt-1" placeholder="TF-…"
                  value={form.numero_titre_foncier} onChange={e => setForm(f => ({ ...f, numero_titre_foncier: e.target.value }))} />
              </div>

              <div className="col-span-2 mt-2">
                <p className="text-xs font-bold text-gray-500 uppercase mb-2">Paiement</p>
              </div>
              <div>
                <label className="text-xs text-gray-600 font-medium">Mode de paiement</label>
                <select className="w-full mt-1 border rounded-lg px-3 py-2 text-sm"
                  value={form.mode_paiement} onChange={e => setForm(f => ({ ...f, mode_paiement: e.target.value }))}>
                  <option value="AIRTEL_MONEY">Airtel Money</option>
                  <option value="MOOV_MONEY">Moov Money</option>
                  <option value="BANQUE">Banque</option>
                  <option value="TRESOR_PUBLIC">Trésor Public</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-600 font-medium">Référence paiement *</label>
                <Input className="mt-1" placeholder="AM-2026-XXXXX"
                  value={form.reference_paiement} onChange={e => setForm(f => ({ ...f, reference_paiement: e.target.value }))} />
              </div>
            </div>

            <div className="p-3 bg-amber-100 rounded text-xs text-amber-800 mb-3">
              💰 Montant forfaitaire : <strong>50 000 FCFA</strong> — Le NUS et le PDF formulaire seront générés automatiquement
            </div>

            <div className="flex gap-2">
              <Button className="bg-orange-600 hover:bg-orange-700" disabled={processing === 'new'}
                onClick={handleNouvelleDemandeSubmit}>
                {processing === 'new' ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                Enregistrer la demande
              </Button>
              <Button variant="outline" onClick={() => setShowNouveauForm(false)}>Annuler</Button>
            </div>
          </Card>
        )}

        {/* Table demandes */}
        <Card className="overflow-hidden">
          <div className="grid grid-cols-7 bg-gray-50 px-4 py-3 text-xs font-semibold text-gray-500 uppercase border-b">
            <span>NUS</span><span>Demandeur</span><span>Localité</span>
            <span>Lot / Parcelle</span><span className="text-right">m²</span>
            <span>Date</span><span>État</span>
          </div>
          {loading
            ? <div className="flex justify-center py-10"><Loader2 className="h-6 w-6 animate-spin text-orange-500" /></div>
            : filtered.length === 0
              ? <div className="py-10 text-center text-gray-500">Aucune demande trouvée</div>
              : filtered.map(d => {
                  const ec = ETAT_CONF[d.etat] || { label: d.etat, cls: 'bg-gray-100 text-gray-600' };
                  return (
                    <div key={d.ccfm_id}
                      className={`grid grid-cols-7 px-4 py-3 border-b items-center hover:bg-gray-50 cursor-pointer transition-colors ${selected?.ccfm_id === d.ccfm_id ? 'bg-orange-50 ring-1 ring-inset ring-orange-200' : ''}`}
                      onClick={() => setSelected(d)}>
                      <span className="font-mono text-xs font-bold text-orange-700">{d.nus}</span>
                      <span className="text-sm font-medium truncate pr-2">{d.nom_prenom}</span>
                      <span className="text-sm text-gray-600">{d.localite}</span>
                      <span className="font-mono text-xs text-gray-500">{d.lot}/{d.parcelle}</span>
                      <span className="text-sm text-right">{d.superficie_m2?.toLocaleString('fr-FR')}</span>
                      <span className="text-xs text-gray-400">{d.date_enregistrement?.slice(0, 10)}</span>
                      <Badge className={`${ec.cls} text-xs`}>{ec.label}</Badge>
                    </div>
                  );
                })
          }
          <div className="px-4 py-2 bg-gray-50 border-t text-xs text-gray-400">{filtered.length} demande(s)</div>
        </Card>
      </div>

      {/* Panneau détail + actions */}
      {selected && (
        <div className="w-80 flex-shrink-0">
          <Card className="p-5 sticky top-4 space-y-4 max-h-[calc(100vh-12rem)] overflow-y-auto">
            <div className="flex justify-between items-start">
              <h3 className="font-bold text-gray-900">Détail CCFM</h3>
              <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>✕</Button>
            </div>

            <div>
              <p className="font-mono text-xs font-bold text-orange-700">{selected.nus}</p>
              {selected.reference_ccfm && (
                <p className="text-xs text-gray-500 font-mono">{selected.reference_ccfm}</p>
              )}
            </div>

            {/* Identité */}
            <div className="space-y-2 text-sm">
              <div className="p-2 bg-gray-50 rounded">
                <p className="text-xs text-gray-400 mb-1 font-semibold">DEMANDEUR</p>
                <p className="font-semibold">{selected.nom_prenom}</p>
                <p className="text-xs text-gray-500">{selected.nip_passeport}</p>
                <p className="text-xs text-gray-500">{selected.telephone}</p>
              </div>
              <div className="p-2 bg-gray-50 rounded">
                <p className="text-xs text-gray-400 mb-1 font-semibold">PARCELLE</p>
                <p className="text-xs"><span className="text-gray-500">Localité :</span> <strong>{selected.localite}</strong></p>
                <p className="text-xs"><span className="text-gray-500">Lot :</span> {selected.lot} · <span className="text-gray-500">Parcelle :</span> {selected.parcelle}</p>
                <p className="text-xs"><span className="text-gray-500">Superficie :</span> <strong>{selected.superficie_m2?.toLocaleString('fr-FR')} m²</strong></p>
              </div>

              {/* Ticket RNAF+BGU */}
              {(selected.rnaf_conforme !== null && selected.rnaf_conforme !== undefined) && (
                <div className="p-2 bg-blue-50 border border-blue-200 rounded">
                  <p className="text-xs text-blue-700 font-semibold mb-1">🎫 Ticket RNAF+BGU</p>
                  <p className="text-xs">RNAF : {selected.rnaf_conforme ? '✅ Conforme' : '❌ Non conforme'}</p>
                  <p className="text-xs">BGU  : {selected.bgu_conforme  ? '✅ Concordant' : '❌ Non concordant'}</p>
                  {selected.latitude && (
                    <p className="text-xs text-blue-600 font-mono mt-1">📍 {selected.latitude}, {selected.longitude}</p>
                  )}
                </div>
              )}

              {/* Rapport d'appréciation */}
              {selected.rapport_appreciation && (
                <div className="p-2 bg-purple-50 border border-purple-200 rounded">
                  <p className="text-xs text-purple-700 font-semibold mb-1">📊 Rapport d'appréciation</p>
                  <p className="text-xs">
                    Verdict : <strong className={
                      selected.rapport_appreciation.verdict === 'CONFORME' ? 'text-green-600' :
                      selected.rapport_appreciation.verdict === 'A_VERIFIER' ? 'text-amber-600' : 'text-red-600'
                    }>{selected.rapport_appreciation.verdict || selected.rapport_appreciation.avis_systeme}</strong>
                  </p>
                  {selected.rapport_appreciation.ecart_pct && (
                    <p className="text-xs text-gray-500">Écart superficie : {selected.rapport_appreciation.ecart_pct}%</p>
                  )}
                </div>
              )}

              <div className="p-2 bg-gray-50 rounded">
                <p className="text-xs text-gray-400 mb-1 font-semibold">PAIEMENT</p>
                <p className="text-xs">{selected.mode_paiement} · {selected.montant_paye?.toLocaleString('fr-FR')} FCFA</p>
                <p className="text-xs text-gray-500 font-mono">{selected.reference_paiement}</p>
              </div>

              {selected.qr_code && (
                <div className="p-2 bg-emerald-50 border border-emerald-200 rounded">
                  <p className="text-xs text-emerald-700 font-semibold">⛓ QR Code · Blockchain</p>
                  <p className="font-mono text-xs text-gray-500 break-all">{selected.qr_code}</p>
                  {selected.annf_code && (
                    <p className="text-xs text-teal-700 font-mono mt-1">📁 {selected.annf_code}</p>
                  )}
                </div>
              )}
            </div>

            {/* État + actions */}
            <div className="border-t pt-3">
              <Badge className={`${ETAT_CONF[selected.etat]?.cls || 'bg-gray-100 text-gray-600'} w-full justify-center py-1.5 text-sm mb-3`}>
                {ETAT_CONF[selected.etat]?.label || selected.etat}
              </Badge>

              {/* B11 — Téléchargement PDF formulaire demande (toujours disponible) */}
              {selected.pdf_demande_url && (
                <Button size="sm" variant="outline"
                  className="w-full mb-2 gap-2 border-orange-300 text-orange-700 hover:bg-orange-50"
                  onClick={() => handleDownloadPDF(selected, 'demande')}>
                  <FileText className="h-3 w-3" />Formulaire de demande (A4)
                </Button>
              )}

              {/* B11 — Téléchargement certificat PDF (après signature) */}
              {['SIGNE_DIRECTEUR', 'RETRAIT_EN_ATTENTE', 'ARCHIVE', 'RETIRE'].includes(selected.etat) && (
                <Button size="sm" className="w-full bg-orange-600 hover:bg-orange-700 mb-2 gap-2"
                  onClick={() => handleDownloadPDF(selected, 'certificat')}>
                  <Download className="h-3 w-3" />Certificat CCFM (A5)
                </Button>
              )}

              {/* Transitions workflow */}
              {(TRANSITIONS[selected.etat] || []).map(t => (
                <Button key={t.action} size="sm"
                  className={`${t.color} w-full mb-2 gap-2 text-white`}
                  disabled={processing === selected.ccfm_id}
                  onClick={() => handleTransition(selected, t.action)}>
                  {processing === selected.ccfm_id
                    ? <Loader2 className="h-3 w-3 animate-spin" />
                    : <span>{t.icon}</span>}
                  {t.label}
                </Button>
              ))}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
