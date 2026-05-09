/**
 * FONCIER+ v3.0 — CCFM Layout
 * Navigation par onglets — Workflow 7 étapes
 */
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';


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

const tabs = [
  { id: 'dashboard',          label: 'Tableau de bord',       path: '/dashboard/ccfm',                       icon: '📊' },
  { id: 'demandes',           label: 'Demandes',              path: '/dashboard/ccfm/demandes',              icon: '📋' },
  { id: 'workflow',           label: 'Workflow',              path: '/dashboard/ccfm/workflow',              icon: '🔄',
    badge: '7 étapes', badgeColor: 'bg-orange-100 text-orange-700' },
  { id: 'suivi',              label: 'Suivi',                 path: '/dashboard/ccfm/suivi',                 icon: '📍' },
  { id: 'certificats',        label: 'Certificats',           path: '/dashboard/ccfm/certificats',           icon: '📜' },
  { id: 'verification',       label: 'Vérification',          path: '/dashboard/ccfm/verification',          icon: '🔍' },
  { id: 'documents',          label: 'Documents',             path: '/dashboard/ccfm/documents',             icon: '📄',
    badge: 'Officiel', badgeColor: 'bg-orange-100 text-orange-700' },
  { id: 'constat-topographe', label: 'Fiche Constat',         path: '/dashboard/ccfm/constat-topographe',    icon: '📐',
    badge: 'TOPOGRAPHE', badgeColor: 'bg-indigo-100 text-indigo-700' },
  { id: 'archive-dar',        label: 'Archives ANNF',         path: '/dashboard/ccfm/archive-dar',           icon: '🏛️',
    badge: 'ANNF', badgeColor: 'bg-teal-100 text-teal-700' },
];

export default function CCFMLayout() {
  const navigate  = useNavigate();
  const location  = useLocation();
  const isActive  = (path: string) => location.pathname === path;

  return (
    <div className="p-8">
      <div className="mb-8">

        {/* En-tête */}
        <div className="flex items-center gap-4 mb-4">
          <div className="w-16 h-16 bg-orange-100 rounded-lg flex items-center justify-center text-4xl">📜</div>
          <div>
            <h1 className="text-3xl font-bold text-gray-900">Certificat de Conformité Foncière Mixte</h1>
            <p className="text-gray-600 mt-1">CCFM · Condition préalable obligatoire à tout titre foncier et transaction légale</p>
          </div>
        </div>

        {/* Banner officiel */}
        <div className="h-36 rounded-xl relative overflow-hidden flex items-center px-8"
          style={{ background: 'linear-gradient(135deg, #7c2d12 0%, #c2410c 40%, #ea580c 80%, #f97316 100%)' }}>
          <div className="absolute inset-0 opacity-10"
            style={{ backgroundImage: 'repeating-linear-gradient(45deg, white 0, white 1px, transparent 0, transparent 50%)', backgroundSize: '20px 20px' }} />
          <div className="w-16 h-16 rounded-full bg-white/20 flex items-center justify-center text-3xl mr-6 flex-shrink-0">🇳🇪</div>
          <div className="relative text-white">
            <p className="text-xs font-bold uppercase tracking-widest text-orange-200 mb-1">
              République du Niger · Ministère de l'Urbanisme, du Logement et de l'Habitat
            </p>
            <h2 className="text-xl font-bold mb-1">Certificat de Conformité Foncière Mixte (CCFM)</h2>
            <p className="text-orange-100 text-sm">
              50 000 FCFA · Validité 2 ans · QR Code · Ancrage blockchain · Archivage ANNF
            </p>
          </div>
          <div className="ml-auto relative flex gap-4 text-white text-center">
            {[
              { label: '50 000 FCFA',  sub: 'Montant forfaitaire' },
              { label: '7 étapes',     sub: 'Workflow officiel'   },
              { label: 'RNAF + BGU',   sub: 'Ticket de réponse'  },
            ].map(s => (
              <div key={s.label} className="bg-white/15 rounded-lg px-4 py-3">
                <p className="font-bold text-sm">{s.label}</p>
                <p className="text-xs text-orange-200 mt-0.5">{s.sub}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Workflow résumé — 7 étapes */}
        <div className="mt-4 grid grid-cols-7 gap-1 text-xs">
          {[
            { n: '1', label: 'Guichetière',  icon: '🪟', color: 'bg-gray-100 text-gray-600' },
            { n: '2', label: 'Ticket RNAF+BGU+GPS', icon: '🔍', color: 'bg-blue-100 text-blue-700' },
            { n: '3', label: 'Secrétaire',   icon: '📤', color: 'bg-amber-100 text-amber-700' },
            { n: '4', label: 'Topographe',   icon: '📐', color: 'bg-indigo-100 text-indigo-700' },
            { n: '5', label: 'Rapport auto', icon: '📊', color: 'bg-purple-100 text-purple-700' },
            { n: '6', label: 'Directeur',    icon: '✅', color: 'bg-emerald-100 text-emerald-700' },
            { n: '7', label: 'Archiviste',   icon: '🏛️', color: 'bg-teal-100 text-teal-700' },
          ].map((s, i, arr) => (
            <div key={s.n} className="flex items-center gap-1">
              <div className={`flex-1 flex items-center gap-1 px-2 py-1.5 rounded ${s.color}`}>
                <span>{s.icon}</span>
                <div>
                  <p className="font-bold">{s.n}</p>
                  <p className="leading-tight opacity-80">{s.label}</p>
                </div>
              </div>
              {i < arr.length - 1 && <span className="text-gray-300">›</span>}
            </div>
          ))}
        </div>

        {/* Note légale */}
        <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-800">
          <strong>⚠️ Note :</strong> Le CCFM n'est ni un acte de cession, ni un titre foncier,
          mais une <strong>condition préalable obligatoire</strong> à toute établissement de ces derniers
          et à toute transaction légale. (Arrêté n°…/MULH/… — Ministère de l'Urbanisme)
        </div>
      </div>

      {/* Onglets */}
      <Card className="mb-6">
        <div className="flex border-b overflow-x-auto">
          {tabs.map(tab => (
            <Button key={tab.id} variant="ghost" onClick={() => navigate(tab.path)}
              className={`flex-1 min-w-max rounded-none border-b-2 gap-2 ${
                isActive(tab.path)
                  ? 'border-orange-600 text-orange-700 bg-orange-50'
                  : 'border-transparent text-gray-600 hover:bg-gray-50'
              }`}>
              <span>{tab.icon}</span>
              <span>{tab.label}</span>
              {tab.badge && (
                <span className={`text-xs px-1.5 py-0.5 rounded font-semibold ${tab.badgeColor}`}>
                  {tab.badge}
                </span>
              )}
            </Button>
          ))}
        </div>
      </Card>

      <Outlet />
    </div>
  );
}
