/**
 * FONCIER+ v3.0 — CCFM Dashboard
 * Certificat de Conformité Foncière Mixte — Workflow 7 étapes
 *
 * 1. GUICHETIÈRE   → Enregistrement demande + NUS
 * 2. SYSTÈME       → Vérification RNAF+BGU → Ticket (GPS)
 * 3. SECRÉTAIRE    → Consulte ticket → Ordre de mission (ticket joint)
 * 4. TOPOGRAPHE    → Fiche de Vérification Administrative Foncière
 * 5. SYSTÈME       → Rapport d'appréciation (ticket + fiche)
 * 6. DIRECTEUR     → Signe → PDF certificat
 * 7. ARCHIVISTE    → ANNF + blockchain
 */
import { useState, useEffect } from 'react';
// Données principales via useDataFetch (architecture v3.4.7)
// Actions POST/PATCH conservent fetch() direct
import { useNavigate } from 'react-router-dom';
import { mockBackend } from '@/services/mockBackend';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Loader2, FileCheck, Clock, CheckCircle, XCircle, AlertTriangle, TrendingUp } from 'lucide-react';
import { ETAT_CONF, WORKFLOW_STEPS } from './ccfmConstants';


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

export default function CCFMDashboard() {
  const navigate = useNavigate();
  const [stats, setStats]     = useState<any>(null);
  const [demandes, setDemandes] = useState<any[]>([]);
  const [loading, setLoading]  = useState(true);

  useEffect(() => {
    // B18 — gestion d'erreur individuelle + rechargement propre
    Promise.all([
      mockBackend.getStatsCCFM().catch(() => null),
      mockBackend.getDemandesCCFM({}).catch(() => []),
    ]).then(([s, d]) => {
      if (s) setStats(s);
      setDemandes(d);
    }).finally(() => setLoading(false));
  }, []);

  if (loading) return (
    <div className="flex justify-center h-48 items-center">
      <Loader2 className="h-8 w-8 animate-spin text-orange-500" />
    </div>
  );

  const enAttenteDirecteur = demandes.filter(d => d.etat === 'ATTENTE_SIGNATURE_DIRECTEUR');
  const ficheADeposer      = demandes.filter(d => d.etat === 'VISITE_EN_COURS');
  const rapportAGenerer    = demandes.filter(d => d.etat === 'FICHE_CONSTAT_DEPOSEE');

  return (
    <div className="space-y-6">

      {/* ── Alertes actives ── */}
      {enAttenteDirecteur.length > 0 && (
        <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-lg flex items-center gap-3">
          <AlertTriangle className="h-5 w-5 text-emerald-600 flex-shrink-0" />
          <div className="flex-1">
            <p className="font-semibold text-emerald-800">
              {enAttenteDirecteur.length} dossier(s) en attente de signature du Directeur
            </p>
            <p className="text-sm text-emerald-600">Rapport d'appréciation disponible — signature + PDF à générer</p>
          </div>
          <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700 text-xs"
            onClick={() => navigate('/dashboard/ccfm/demandes')}>Traiter →</Button>
        </div>
      )}
      {ficheADeposer.length > 0 && (
        <div className="p-4 bg-indigo-50 border border-indigo-200 rounded-lg flex items-center gap-3">
          <AlertTriangle className="h-5 w-5 text-indigo-500 flex-shrink-0" />
          <div className="flex-1">
            <p className="font-semibold text-indigo-800">
              {ficheADeposer.length} fiche(s) de constat en attente de dépôt
            </p>
            <p className="text-sm text-indigo-600">Le topographe est sur le terrain — fiche à retourner</p>
          </div>
        </div>
      )}
      {rapportAGenerer.length > 0 && (
        <div className="p-4 bg-purple-50 border border-purple-200 rounded-lg flex items-center gap-3">
          <AlertTriangle className="h-5 w-5 text-purple-500 flex-shrink-0" />
          <div className="flex-1">
            <p className="font-semibold text-purple-800">
              {rapportAGenerer.length} rapport(s) d'appréciation à générer
            </p>
            <p className="text-sm text-purple-600">Fiche de constat déposée — compilation ticket + fiche requise</p>
          </div>
        </div>
      )}

      {/* ── KPIs ── */}
      <div className="grid grid-cols-5 gap-4">
        {[
          { label: 'Total demandes',    value: stats?.total,      icon: <FileCheck className="h-5 w-5 text-orange-500" />,  color: 'text-orange-700' },
          { label: 'Signés (PDF prêt)', value: stats?.signes,     icon: <CheckCircle className="h-5 w-5 text-emerald-500" />, color: 'text-emerald-700' },
          { label: 'En cours',          value: stats?.en_cours,   icon: <Clock className="h-5 w-5 text-amber-500" />,       color: 'text-amber-700' },
          { label: 'Archivés ANNF',     value: stats?.archives,   icon: <TrendingUp className="h-5 w-5 text-teal-500" />,   color: 'text-teal-700' },
          { label: 'Rejetés',           value: stats?.rejetes,    icon: <XCircle className="h-5 w-5 text-red-500" />,       color: 'text-red-700' },
        ].map(k => (
          <Card key={k.label} className="p-4">
            {k.icon}
            <p className={`text-2xl font-bold mt-2 ${k.color}`}>{k.value ?? '—'}</p>
            <p className="text-xs text-gray-500 mt-0.5">{k.label}</p>
          </Card>
        ))}
      </div>

      {/* ── Workflow + métriques ── */}
      <div className="grid grid-cols-3 gap-6">
        <div className="col-span-2">
          <Card className="p-5">
            <h3 className="font-semibold text-gray-800 mb-4">Workflow CCFM — 7 étapes · 15 états</h3>
            <div className="space-y-2">
              {WORKFLOW_STEPS.map(s => {
                const count = demandes.filter(d => s.etats.includes(d.etat)).length;
                const mainEtat = s.etats[0];
                return (
                  <div key={s.n} className="flex items-center gap-3 p-3 rounded-lg bg-gray-50 hover:bg-gray-100 transition-colors">
                    <div className={`w-8 h-8 rounded-full ${s.color} flex items-center justify-center text-white font-bold text-sm flex-shrink-0`}>{s.n}</div>
                    <div className="text-lg flex-shrink-0">{s.icon}</div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-bold text-gray-700">{s.acteur}</p>
                      <p className="text-xs text-gray-500 truncate">{s.titre}</p>
                    </div>
                    <span className="text-xs text-gray-400 flex-shrink-0">{s.duree}</span>
                    <Badge className={`${ETAT_CONF[mainEtat]?.cls || 'bg-gray-100 text-gray-600'} text-xs flex-shrink-0`}>
                      {count > 0 ? `${count} en cours` : ETAT_CONF[mainEtat]?.label}
                    </Badge>
                  </div>
                );
              })}
            </div>

            {/* Ticket de réponse — note visuelle */}
            <div className="mt-4 p-3 bg-cyan-50 border border-cyan-200 rounded-lg text-xs text-cyan-800">
              <strong>🎫 Ticket de réponse (étape 2) :</strong> Visible par la secrétaire avant émission de l'ordre,
              joint automatiquement au topographe, et inclus dans le rapport d'appréciation du Directeur.
            </div>
          </Card>
        </div>

        <div className="space-y-4">
          {/* Qualité */}
          <Card className="p-5">
            <h3 className="font-semibold text-gray-800 mb-3">Qualité</h3>
            <div className="space-y-3">
              {[
                { label: 'RNAF conforme',       value: stats?.taux_rnaf_ok    ?? 98.1, color: 'bg-blue-500' },
                { label: 'BGU concordant',       value: stats?.taux_bgu_ok     ?? 94.5, color: 'bg-indigo-500' },
                { label: 'GPS valide',           value: stats?.taux_gps_ok     ?? 96.8, color: 'bg-cyan-500' },
                { label: 'Taux conformité final',value: stats?.taux_conformite ?? 93.2, color: 'bg-emerald-500' },
              ].map(m => (
                <div key={m.label}>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-gray-600">{m.label}</span>
                    <span className="font-bold">{m.value}%</span>
                  </div>
                  <div className="w-full h-2 bg-gray-100 rounded-full">
                    <div className={`h-2 rounded-full ${m.color}`} style={{ width: `${m.value}%` }} />
                  </div>
                </div>
              ))}
            </div>
          </Card>

          {/* Délais */}
          <Card className="p-5">
            <h3 className="font-semibold text-gray-800 mb-3">Délais moyens</h3>
            <div className="space-y-2 text-sm">
              {[
                { label: 'Délai total',            value: '8–15 j' },
                { label: 'Vérif. RNAF+BGU',        value: '< 1 h' },
                { label: 'Ticket → Ordre mission', value: 'J+1' },
                { label: 'Visite terrain',          value: '2–4 j' },
                { label: 'Rapport + Signature',     value: '1–2 j' },
              ].map(d => (
                <div key={d.label} className="flex justify-between">
                  <span className="text-gray-500">{d.label}</span>
                  <span className="font-semibold text-orange-700">{d.value}</span>
                </div>
              ))}
            </div>
          </Card>

          {/* Paiements */}
          <Card className="p-4">
            <h3 className="text-sm font-semibold text-gray-700 mb-2">Modes de paiement</h3>
            <div className="flex flex-wrap gap-1">
              {[
                { m: 'Airtel Money',   c: 'bg-red-100 text-red-700' },
                { m: 'Moov Money',     c: 'bg-blue-100 text-blue-700' },
                { m: 'Banque',         c: 'bg-green-100 text-green-700' },
                { m: 'Trésor Public',  c: 'bg-amber-100 text-amber-700' },
              ].map(p => <Badge key={p.m} className={`${p.c} text-xs`}>{p.m}</Badge>)}
            </div>
            <p className="text-xs text-gray-400 mt-2">Montant forfaitaire : 50 000 FCFA · Validité 2 ans</p>
          </Card>
        </div>
      </div>

      {/* ── Demandes récentes ── */}
      <Card className="p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-gray-800">Demandes récentes</h3>
          <Button size="sm" variant="outline" onClick={() => navigate('/dashboard/ccfm/demandes')}>
            Toutes →
          </Button>
        </div>
        <div className="space-y-1">
          {demandes.slice(0, 6).map(d => {
            const ec = ETAT_CONF[d.etat] || { label: d.etat, cls: 'bg-gray-100 text-gray-600' };
            return (
              <div key={d.ccfm_id} className="flex items-center gap-4 p-3 rounded-lg border hover:bg-orange-50 transition-colors">
                <span className="font-mono text-xs font-bold text-orange-700 w-36 flex-shrink-0">{d.nus}</span>
                <span className="text-sm font-medium flex-1 truncate">{d.nom_prenom}</span>
                <span className="text-xs text-gray-500 hidden md:block">{d.localite} — {d.lot}/{d.parcelle}</span>
                <span className="text-xs text-gray-400">{d.superficie_m2?.toLocaleString('fr-FR')} m²</span>
                {/* Ticket GPS disponible ? */}
                {d.latitude && <span title="GPS ticket disponible" className="text-xs text-cyan-600">📍</span>}
                {d.qr_code && <span title="QR Code ancré" className="text-xs text-gray-400">⛓</span>}
                <Badge className={`${ec.cls} text-xs flex-shrink-0`}>{ec.label}</Badge>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}
