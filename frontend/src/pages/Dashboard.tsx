/**
 * FONCIER+ v3.4.7 — Dashboard Principal
 * Layout complet : Sidebar + Header + Contenu dynamique
 * Tous les 11 modules accessibles depuis le menu
 */
import { useState, ReactNode, lazy, Suspense } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { useDataFetch } from '../hooks/useDataFetch'
import {
  getKpisNationaux,
  getActiviteRecente,
  getStatsByModule,
  getWorkflowsEnCours,
  getAlertesAntifraude,
} from '../services/mockBackend'

// ── Types ─────────────────────────────────────────────────────────

type ModuleKey =
  | 'accueil' | 'urbanisme' | 'commune' | 'jo'
  | 'bgu' | 'ccfm' | 'domaine' | 'notaire'
  | 'banque' | 'justice' | 'annf' | 'dar' | 'wf30'

interface NavItem {
  key: ModuleKey
  label: string
  icon: string
  badge?: number
}

// ── Navigation ────────────────────────────────────────────────────

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: 'Principal',
    items: [
      { key: 'accueil',   label: 'Tableau de bord', icon: '🏠' },
    ]
  },
  {
    label: 'Institution',
    items: [
      { key: 'urbanisme', label: 'Urbanisme',       icon: '🏛️' },
      { key: 'commune',   label: 'Commune',         icon: '🏘️' },
      { key: 'jo',        label: 'Journal Officiel',icon: '📰' },
    ]
  },
  {
    label: 'Géospatial & Titres',
    items: [
      { key: 'bgu',       label: 'BGU',             icon: '🗺️' },
      { key: 'ccfm',      label: 'CCFM',            icon: '📜', badge: 3 },
      { key: 'domaine',   label: 'Domaine',         icon: '🏗️' },
    ]
  },
  {
    label: 'Transactions',
    items: [
      { key: 'notaire',   label: 'Notaire',         icon: '✍️' },
      { key: 'banque',    label: 'Banque',          icon: '🏦' },
      { key: 'justice',   label: 'Justice',         icon: '⚖️', badge: 47 },
    ]
  },
  {
    label: 'Archives',
    items: [
      { key: 'annf',      label: 'ANNF',            icon: '🗄️' },
      { key: 'dar',       label: 'DAR',             icon: '📁' },
      { key: 'wf30',      label: 'Migration WF30',  icon: '🔄' },
    ]
  },
]

// ── Composants partagés ───────────────────────────────────────────

function Card({ children, style = {} }: { children: ReactNode; style?: object }) {
  return (
    <div style={{
      background: 'white',
      border: '1px solid var(--gray-200)',
      borderRadius: 'var(--radius-lg)',
      padding: '24px',
      ...style,
    }}>
      {children}
    </div>
  )
}

function Badge({ n, color = 'red' }: { n: number; color?: string }) {
  const bg = color === 'red' ? 'var(--red)' : 'var(--green)'
  return (
    <span style={{
      background: bg, color: 'white',
      fontSize: '10px', fontWeight: 700,
      padding: '2px 6px', borderRadius: '10px',
      minWidth: '18px', textAlign: 'center',
    }}>{n > 99 ? '99+' : n}</span>
  )
}

function StatusPill({ statut }: { statut: string }) {
  const map: Record<string, { bg: string; color: string; label: string }> = {
    actif:         { bg: 'var(--green-pale)', color: 'var(--green)',   label: 'Actif' },
    EN_COURS:      { bg: 'var(--amber-light)',color: 'var(--amber)',   label: 'En cours' },
    COMPLETE:      { bg: 'var(--green-pale)', color: 'var(--green)',   label: 'Complété' },
    litige:        { bg: 'var(--red-light)',  color: 'var(--red)',     label: 'Litige' },
    gele:          { bg: 'var(--red-light)',  color: 'var(--red)',     label: 'Gelé' },
    demande:       { bg: 'var(--gray-100)',   color: 'var(--gray-600)','label': 'Demande' },
    verification:  { bg: 'var(--amber-light)',color: 'var(--amber)',   label: 'Vérification' },
    certificat:    { bg: 'var(--green-pale)', color: 'var(--green)',   label: 'Certifié' },
    archive:       { bg: 'var(--gray-100)',   color: 'var(--gray-500)','label': 'Archivé' },
  }
  const s = map[statut] || { bg: 'var(--gray-100)', color: 'var(--gray-500)', label: statut }
  return (
    <span style={{
      background: s.bg, color: s.color,
      fontSize: '11px', fontWeight: 600,
      padding: '3px 10px', borderRadius: 'var(--radius-sm)',
      letterSpacing: '.02em',
    }}>{s.label}</span>
  )
}

function SkeletonRow() {
  return (
    <div style={{ display: 'flex', gap: '12px', padding: '12px 0', borderBottom: '1px solid var(--gray-100)' }}>
      {[60,150,100,80].map((w,i) => (
        <div key={i} className="skeleton" style={{ height: 14, width: w, borderRadius: 4 }} />
      ))}
    </div>
  )
}

// ── Module : Accueil ──────────────────────────────────────────────

function AccueilModule() {
  const { data: kpis }     = useDataFetch(getKpisNationaux)
  const { data: activite } = useDataFetch(getActiviteRecente)
  const { data: stats }    = useDataFetch(getStatsByModule)
  const { data: workflows }= useDataFetch(getWorkflowsEnCours)
  const { data: alertes }  = useDataFetch(getAlertesAntifraude)

  return (
    <div style={{ display: 'grid', gap: '20px' }}>

      {/* KPIs */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: '16px' }}>
        {kpis ? [
          { v: kpis.parcelles_totales.toLocaleString('fr-FR'), l: 'Parcelles', icon: '🗺️', color: 'var(--green)' },
          { v: kpis.ccfm_actifs.toLocaleString('fr-FR'),       l: 'CCFM actifs', icon: '📜', color: 'var(--green)' },
          { v: kpis.conflits_ouverts.toString(),               l: 'Litiges ouverts', icon: '⚠️', color: 'var(--red)' },
          { v: kpis.taux_conformite_pct.toFixed(1) + '%',      l: 'Taux conformité', icon: '✅', color: 'var(--green)' },
        ].map((k, i) => (
          <Card key={i}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--gray-500)', letterSpacing: '.05em', textTransform: 'uppercase', marginBottom: '8px' }}>
                  {k.l}
                </div>
                <div style={{ fontFamily: 'var(--font-display)', fontSize: '30px', fontWeight: 700, color: k.color, lineHeight: 1 }}>
                  {k.v}
                </div>
              </div>
              <span style={{ fontSize: '24px' }}>{k.icon}</span>
            </div>
          </Card>
        )) : Array.from({length:4}, (_,i) => (
          <Card key={i}><div className="skeleton" style={{ height: 60 }} /></Card>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>

        {/* Activité récente */}
        <Card>
          <h3 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--ink)', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span>⚡</span> Activité récente
          </h3>
          {activite ? activite.map(a => (
            <div key={a.id} style={{
              display: 'flex', alignItems: 'center', gap: '12px',
              padding: '8px 0',
              borderBottom: '1px solid var(--gray-100)',
            }}>
              <div style={{
                width: 8, height: 8, borderRadius: '50%',
                background: a.color === 'green' ? 'var(--green)' : a.color === 'red' ? 'var(--red)' : 'var(--amber)',
                flexShrink: 0,
              }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {a.action}
                </div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--gray-400)' }}>
                  {a.ref}
                </div>
              </div>
              <div style={{ fontSize: '11px', color: 'var(--gray-400)', flexShrink: 0 }}>{a.time}</div>
            </div>
          )) : Array.from({length:6}, (_,i) => <SkeletonRow key={i} />)}
        </Card>

        {/* Workflows en cours */}
        <Card>
          <h3 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--ink)', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span>🔄</span> Workflows en cours
            {workflows && <Badge n={workflows.length} color="green" />}
          </h3>
          {workflows ? workflows.map(wf => (
            <div key={wf.id} style={{
              padding: '10px 0',
              borderBottom: '1px solid var(--gray-100)',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--ink)' }}>
                  {wf.type.replace(/_/g,' ')}
                </span>
                <StatusPill statut="EN_COURS" />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ fontSize: '11px', color: 'var(--gray-400)' }}>
                  Étape : {wf.etape_courante}
                </span>
                <span style={{ fontSize: '11px', color: 'var(--amber)', fontWeight: 500 }}>
                  {wf.depuis}
                </span>
              </div>
            </div>
          )) : Array.from({length:6}, (_,i) => <SkeletonRow key={i} />)}
        </Card>
      </div>

      {/* Alertes antifraude */}
      {alertes && alertes.length > 0 && (
        <Card style={{ borderColor: 'rgba(184,34,14,.2)', background: 'var(--red-light)' }}>
          <h3 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--red)', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span>🚨</span> Alertes antifraude actives
            <Badge n={alertes.length} />
          </h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
            {alertes.map(a => (
              <div key={a.id} style={{
                background: 'white', borderRadius: 'var(--radius-md)',
                padding: '12px', border: '1px solid rgba(184,34,14,.15)',
              }}>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
                  <span style={{ fontSize: '16px' }}>
                    {a.gravite === 'CRITIQUE' ? '🔴' : a.gravite === 'BLOQUANT' ? '🟠' : a.gravite === 'WARN' ? '🟡' : 'ℹ️'}
                  </span>
                  <div>
                    <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--ink)' }}>{a.message}</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--gray-400)', marginTop: '2px' }}>
                      {a.parcelle} · {a.timestamp}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Stats modules */}
      <Card>
        <h3 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--ink)', marginBottom: '16px' }}>
          📊 Volume par module — ce mois
        </h3>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr>
                {['Module','Libellé','Total','Ce mois','Actifs'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontSize: '11px', fontWeight: 600, color: 'var(--gray-500)', letterSpacing: '.04em', textTransform: 'uppercase', borderBottom: '2px solid var(--gray-200)' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {stats ? stats.map((s, i) => (
                <tr key={i} style={{ background: i%2 ? 'var(--gray-50)' : 'transparent' }}>
                  <td style={{ padding: '10px 12px', fontWeight: 600, color: 'var(--green)' }}>{s.module}</td>
                  <td style={{ padding: '10px 12px', color: 'var(--gray-500)', fontSize: '12px' }}>{s.label}</td>
                  <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)' }}>{s.total.toLocaleString('fr-FR')}</td>
                  <td style={{ padding: '10px 12px', color: 'var(--green)', fontWeight: 500, fontFamily: 'var(--font-mono)' }}>+{s.ce_mois.toLocaleString('fr-FR')}</td>
                  <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)' }}>{s.actifs.toLocaleString('fr-FR')}</td>
                </tr>
              )) : Array.from({length:5}, (_,i) => (
                <tr key={i}><td colSpan={5}><SkeletonRow /></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}

// ── Module générique (stub fonctionnel) ───────────────────────────

function ModuleStub({ moduleKey }: { moduleKey: ModuleKey }) {
  const labels: Record<ModuleKey, { title: string; icon: string; desc: string; features: string[] }> = {
    accueil:   { title: 'Tableau de bord', icon: '🏠', desc: '', features: [] },
    urbanisme: {
      title: 'Urbanisme', icon: '🏛️',
      desc: 'Gestion des arrêtés fonciers et du Registre National des Arrêtés Fonciers (RNAF).',
      features: ['Arrêtés fonciers — cycle de vie complet','RNAF — publication et scellement','Lotissements — workflow 14 étapes','Zonage urbain — régions & communes','Permis de construire','Conformité urbanistique','Archivage DAR → ANNF'],
    },
    commune: {
      title: 'Commune', icon: '🏘️',
      desc: 'Module DAD (Direction des Affaires Domaniales) + DAR (Direction des Archives) au niveau communal.',
      features: ['DAD — dépôts et mandats locaux','DAR — archives communales vers ANNF','Régularisation foncière (WF12)','Récépissés de dépôt','Suivi arrêtés locaux'],
    },
    jo: {
      title: 'Journal Officiel', icon: '📰',
      desc: 'Publication des actes officiels et parutions au Journal Officiel de la République.',
      features: ['Publications au JO — RNAF & arrêtés','Numéros officiels sequentiels','Archivage DAR → ANNF','Consultation publique','Workflow éditorial'],
    },
    bgu: {
      title: 'Base Géospatiale Unique (BGU)', icon: '🗺️',
      desc: 'Module géospatial indépendant — scellement des géométries parcellaires.',
      features: ['Import géométries WKT/GeoJSON','Contrôle qualité PostGIS','Validation et scellement officiel','Détection conflits spatiaux','Synchronisation BGU → ANNF'],
    },
    ccfm: {
      title: 'CCFM — Certificats de Conformité Foncière', icon: '📜',
      desc: 'Émission et gestion des certificats NUS avec QR Code scannable.',
      features: ['Demandes de certification','Contrôles F1-F7 automatiques','Génération NUS CCF-AAAA-NNNNNNN','Certificat PDF A5 officiel','QR Code scannable → /verify','Archivage SHA-256 chaîné'],
    },
    domaine: {
      title: 'Domaine National', icon: '🏗️',
      desc: 'Gestion des dossiers domaniaux nationaux, redevances et expropriations.',
      features: ['Dossiers 6 étapes','Redevances foncières','Expropriation (WF11)','Lotissement étendu (WF14)','Mutations de dossiers','Archives DAR → ANNF'],
    },
    notaire: {
      title: 'Notaire', icon: '✍️',
      desc: 'Actes notariaux, mutations, successions et messagerie sécurisée inter-études.',
      features: ['Actes de vente (WF17)','Donations foncières (WF18)','Successions (WF19)','Hypothèques notariales','Messagerie SHA-256 chaînée','Répertoire mensuel'],
    },
    banque: {
      title: 'Banque', icon: '🏦',
      desc: 'Registre des banques agréées BCEAO, inscriptions hypothécaires et mainlevées.',
      features: ['Banques agréées BCEAO','Dossiers hypothèque (WF8)','Inscription au registre','Mainlevées officielles','Vérification CCFM gate','Archives DAR → ANNF'],
    },
    justice: {
      title: 'Justice', icon: '⚖️',
      desc: 'Litiges fonciers, annulations judiciaires et gel de biens.',
      features: ['Litiges fonciers (WF25)','Levée de litige (WF26)','Annulation judiciaire (WF24)','Gel / dégel automatique','Greffe judiciaire (9 TGI)','Décisions PKI signées'],
    },
    annf: {
      title: 'ANNF — Archives Nationales', icon: '🗄️',
      desc: 'Archives Nationales Numériques Foncières — système WORM, indicateurs, supervision.',
      features: ['Archives WORM irréversibles','Tableau de bord v_sante_systeme','Indicateurs KPIs nationaux','Alertes antifraude auto','Sauvegardes AES-256','WF27 archivage définitif','Vue v_annf_par_module'],
    },
    dar: {
      title: 'DAR — Direction des Archives', icon: '📁',
      desc: 'Direction des Archives locale de chaque module — liaison automatique ANNF.',
      features: ['Archives par module (10 DAR)','Liaison DAR → ANNF automatique','IDA format ANNF-AAAA-NNNNNNN','SHA-256 intégrité','Scellement RESPONSABLE_ANNF','Vue d'intégrité par module'],
    },
    wf30: {
      title: 'Migration WF30', icon: '🔄',
      desc: 'Pipeline national de numérisation des archives foncières historiques — 10 étapes.',
      features: ['30.1 Collecte sources','30.2 Inventaire plans','30.3 Numérisation PDF/A','30.4 Géoréférencement GCPs','30.5 Vectorisation WKT','30.6 Génération NICAD','30.7 Association propriétaires','30.8 Validation CCFM','30.9 Conflits spatiaux','30.10 Intégration BGU + ANNF'],
    },
  }

  const info = labels[moduleKey]
  if (!info) return null

  return (
    <div style={{ animation: 'fadeIn .3s ease' }}>
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '28px', fontWeight: 700, color: 'var(--ink)', display: 'flex', alignItems: 'center', gap: '12px' }}>
          <span>{info.icon}</span> {info.title}
        </h2>
        {info.desc && (
          <p style={{ color: 'var(--gray-600)', fontSize: '14px', marginTop: '8px', maxWidth: '600px' }}>
            {info.desc}
          </p>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
        <Card>
          <h3 style={{ fontSize: '14px', fontWeight: 600, marginBottom: '16px', color: 'var(--ink)' }}>
            Fonctionnalités disponibles
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {info.features.map((f, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '13px', color: 'var(--gray-700)' }}>
                <span style={{ color: 'var(--green)', fontWeight: 700, flexShrink: 0 }}>✓</span>
                {f}
              </div>
            ))}
          </div>
        </Card>

        <Card style={{ background: 'var(--green-pale)', border: '1px solid rgba(60,79,51,.1)' }}>
          <h3 style={{ fontSize: '14px', fontWeight: 600, marginBottom: '16px', color: 'var(--green)' }}>
            Accès API
          </h3>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--green)', lineHeight: 1.8 }}>
            <div>GET  /v1/{moduleKey === 'jo' ? 'jo' : moduleKey}/</div>
            <div>POST /v1/{moduleKey === 'jo' ? 'jo' : moduleKey}/</div>
            <div>GET  /v1/{moduleKey === 'jo' ? 'jo' : moduleKey}/dar/archives</div>
            <div>POST /v1/{moduleKey === 'jo' ? 'jo' : moduleKey}/dar/archiver</div>
          </div>
          <div style={{ marginTop: '20px', padding: '12px', background: 'white', borderRadius: 'var(--radius-md)' }}>
            <div style={{ fontSize: '11px', color: 'var(--gray-500)', marginBottom: '6px' }}>Statut backend</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--green)' }} />
              <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--green)' }}>Opérationnel</span>
            </div>
          </div>
        </Card>
      </div>
    </div>
  )
}

// ── DASHBOARD PRINCIPAL ───────────────────────────────────────────

export default function Dashboard() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [activeModule, setActiveModule] = useState<ModuleKey>('accueil')
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [userMenuOpen, setUserMenuOpen] = useState(false)

  if (!user) { navigate('/login'); return null }

  const handleLogout = () => { logout(); navigate('/') }

  const activeInfo = NAV_GROUPS
    .flatMap(g => g.items)
    .find(i => i.key === activeModule)

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', fontFamily: 'var(--font-body)' }}>

      {/* ══ SIDEBAR ══════════════════════════════════════════════ */}
      <aside style={{
        width: sidebarOpen ? 'var(--sidebar-w)' : '64px',
        background: 'var(--green)',
        display: 'flex', flexDirection: 'column',
        transition: 'width .25s ease',
        flexShrink: 0,
        zIndex: 10,
        overflowY: 'auto', overflowX: 'hidden',
      }}>
        {/* Logo */}
        <div style={{
          padding: sidebarOpen ? '20px 20px 16px' : '20px 14px 16px',
          borderBottom: '1px solid rgba(255,255,255,.1)',
          display: 'flex', alignItems: 'center',
          justifyContent: sidebarOpen ? 'space-between' : 'center',
          flexShrink: 0,
        }}>
          {sidebarOpen && (
            <div>
              <div style={{
                fontFamily: 'var(--font-display)',
                fontSize: '22px', fontWeight: 700, color: 'white', lineHeight: 1,
              }}>
                FONCIER<span style={{ color: 'var(--red)' }}>+</span>
              </div>
              <div style={{ fontSize: '9px', color: 'rgba(245,239,224,.5)', letterSpacing: '.06em', textTransform: 'uppercase', marginTop: '2px' }}>
                DNCD · Niger
              </div>
            </div>
          )}
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            style={{
              background: 'rgba(255,255,255,.1)', border: 'none',
              color: 'rgba(255,255,255,.7)', width: 28, height: 28,
              borderRadius: 'var(--radius-sm)', cursor: 'pointer',
              fontSize: '14px', flexShrink: 0,
            }}
          >
            {sidebarOpen ? '◂' : '▸'}
          </button>
        </div>

        {/* Navigation */}
        <nav style={{ flex: 1, padding: '8px 0', overflowY: 'auto' }}>
          {NAV_GROUPS.map(group => (
            <div key={group.label}>
              {sidebarOpen && (
                <div style={{
                  padding: '12px 20px 4px',
                  fontSize: '9px', fontWeight: 700,
                  color: 'rgba(245,239,224,.4)',
                  letterSpacing: '.08em', textTransform: 'uppercase',
                }}>
                  {group.label}
                </div>
              )}
              {group.items.map(item => {
                const isActive = activeModule === item.key
                return (
                  <button
                    key={item.key}
                    onClick={() => setActiveModule(item.key)}
                    title={!sidebarOpen ? item.label : undefined}
                    style={{
                      width: '100%', padding: sidebarOpen ? '9px 20px' : '9px',
                      display: 'flex', alignItems: 'center',
                      gap: sidebarOpen ? '10px' : '0',
                      justifyContent: sidebarOpen ? 'flex-start' : 'center',
                      background: isActive ? 'rgba(255,255,255,.15)' : 'transparent',
                      border: 'none', cursor: 'pointer',
                      borderLeft: isActive ? '3px solid var(--red)' : '3px solid transparent',
                      transition: 'all .15s',
                      color: isActive ? 'white' : 'rgba(245,239,224,.7)',
                      fontFamily: 'var(--font-body)',
                    }}
                    onMouseEnter={e => { if (!isActive) (e.currentTarget.style.background = 'rgba(255,255,255,.07)') }}
                    onMouseLeave={e => { if (!isActive) (e.currentTarget.style.background = 'transparent') }}
                  >
                    <span style={{ fontSize: '16px', flexShrink: 0 }}>{item.icon}</span>
                    {sidebarOpen && (
                      <>
                        <span style={{ fontSize: '13px', fontWeight: isActive ? 600 : 400, flex: 1, textAlign: 'left', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {item.label}
                        </span>
                        {item.badge && <Badge n={item.badge} />}
                      </>
                    )}
                  </button>
                )
              })}
            </div>
          ))}
        </nav>

        {/* User info bas sidebar */}
        {sidebarOpen && (
          <div style={{
            padding: '16px 20px',
            borderTop: '1px solid rgba(255,255,255,.1)',
            flexShrink: 0,
          }}>
            <div style={{ fontSize: '12px', fontWeight: 600, color: 'white', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {user.name}
            </div>
            <div style={{ fontSize: '10px', color: 'rgba(245,239,224,.5)', marginTop: '2px' }}>
              {user.role} · {user.region}
            </div>
          </div>
        )}
      </aside>

      {/* ══ CONTENU PRINCIPAL ════════════════════════════════════ */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        {/* ── Header ── */}
        <header style={{
          height: 'var(--header-h)',
          background: 'white',
          borderBottom: '1px solid var(--gray-200)',
          display: 'flex', alignItems: 'center',
          padding: '0 24px',
          justifyContent: 'space-between',
          flexShrink: 0,
          zIndex: 5,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <span style={{ fontSize: '18px' }}>{activeInfo?.icon}</span>
            <h1 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--ink)' }}>
              {activeInfo?.label || 'Tableau de bord'}
            </h1>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            {/* Recherche */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: '8px',
              background: 'var(--gray-50)', border: '1px solid var(--gray-200)',
              borderRadius: 'var(--radius-md)', padding: '6px 12px',
            }}>
              <span style={{ color: 'var(--gray-400)', fontSize: '13px' }}>🔍</span>
              <input
                placeholder="NICAD, NUS, référence…"
                style={{
                  border: 'none', background: 'transparent',
                  fontSize: '13px', color: 'var(--ink)',
                  outline: 'none', width: '200px',
                  fontFamily: 'var(--font-body)',
                }}
              />
            </div>

            {/* Menu utilisateur */}
            <div style={{ position: 'relative' }}>
              <button
                onClick={() => setUserMenuOpen(!userMenuOpen)}
                style={{
                  display: 'flex', alignItems: 'center', gap: '8px',
                  background: 'var(--gray-50)', border: '1px solid var(--gray-200)',
                  borderRadius: 'var(--radius-md)', padding: '6px 12px',
                  cursor: 'pointer', fontFamily: 'var(--font-body)',
                }}
              >
                <div style={{
                  width: 28, height: 28, borderRadius: '50%',
                  background: 'var(--green)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  color: 'white', fontSize: '12px', fontWeight: 700,
                }}>
                  {user.name.charAt(0)}
                </div>
                <div style={{ textAlign: 'left' }}>
                  <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--ink)' }}>
                    {user.name.split(' ')[0]}
                  </div>
                  <div style={{ fontSize: '10px', color: 'var(--gray-400)' }}>{user.role}</div>
                </div>
                <span style={{ color: 'var(--gray-400)', fontSize: '10px' }}>▾</span>
              </button>

              {userMenuOpen && (
                <div style={{
                  position: 'absolute', right: 0, top: '100%', marginTop: '4px',
                  background: 'white', border: '1px solid var(--gray-200)',
                  borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-lg)',
                  minWidth: '200px', zIndex: 100,
                  animation: 'scaleIn .15s ease',
                  transformOrigin: 'top right',
                }}>
                  <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--gray-100)' }}>
                    <div style={{ fontSize: '13px', fontWeight: 600 }}>{user.name}</div>
                    <div style={{ fontSize: '11px', color: 'var(--gray-400)', fontFamily: 'var(--font-mono)' }}>{user.email}</div>
                  </div>
                  {[
                    { label: 'Mon profil', icon: '👤' },
                    { label: 'Paramètres', icon: '⚙️' },
                    { label: 'Documentation', icon: '📖' },
                  ].map(item => (
                    <button key={item.label} style={{
                      width: '100%', padding: '10px 16px',
                      background: 'none', border: 'none',
                      cursor: 'pointer', textAlign: 'left',
                      fontFamily: 'var(--font-body)',
                      display: 'flex', alignItems: 'center', gap: '10px',
                      fontSize: '13px', color: 'var(--gray-700)',
                      transition: 'background .15s',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--gray-50)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'none')}
                    >
                      <span>{item.icon}</span> {item.label}
                    </button>
                  ))}
                  <div style={{ borderTop: '1px solid var(--gray-100)' }}>
                    <button
                      onClick={handleLogout}
                      style={{
                        width: '100%', padding: '10px 16px',
                        background: 'none', border: 'none',
                        cursor: 'pointer', textAlign: 'left',
                        fontFamily: 'var(--font-body)',
                        display: 'flex', alignItems: 'center', gap: '10px',
                        fontSize: '13px', color: 'var(--red)',
                        transition: 'background .15s',
                      }}
                      onMouseEnter={e => (e.currentTarget.style.background = 'var(--red-light)')}
                      onMouseLeave={e => (e.currentTarget.style.background = 'none')}
                    >
                      <span>🚪</span> Déconnexion
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </header>

        {/* ── Contenu scrollable ── */}
        <main style={{
          flex: 1, overflowY: 'auto',
          padding: '24px',
          background: 'var(--sand-light)',
        }}
          onClick={() => userMenuOpen && setUserMenuOpen(false)}
        >
          {activeModule === 'accueil'
            ? <AccueilModule />
            : <ModuleStub moduleKey={activeModule} />
          }
        </main>
      </div>
    </div>
  )
}
