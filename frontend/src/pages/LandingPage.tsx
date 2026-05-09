/**
 * FONCIER+ v3.4.7 — Landing Page
 * Page d'accueil publique institutionnelle
 * Palette Niger : COL_GREEN #3C4F33, COL_SAND #F5EFE0, COL_RED #B8220E
 */
import { useDataFetch } from '../hooks/useDataFetch'
import { useNavigate } from 'react-router-dom'
import { useEffect, useState } from 'react'

export default function LandingPage() {

  // SEO — Meta tags pour indexation
  React.useEffect(() => {
    document.title = 'FONCIER+ — Plateforme Nationale de Gestion Foncière Niger'
    const desc = document.querySelector('meta[name="description"]')
    if (desc) desc.setAttribute('content',
      'Plateforme nationale de gestion foncière du Niger — DNCD — Certificat CCFM')
  }, [])

  const navigate = useNavigate()
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const t = setTimeout(() => setVisible(true), 80)
    return () => clearTimeout(t)
  }, [])

  const modules = [
    { icon: '🏛️', label: 'Urbanisme',     desc: 'Arrêtés & RNAF' },
    { icon: '🗺️', label: 'Cadastre / BGU', desc: 'Parcellaire national' },
    { icon: '📜', label: 'CCFM',           desc: 'Certificats fonciers' },
    { icon: '⚖️', label: 'Justice',        desc: 'Litiges & décisions' },
    { icon: '🏦', label: 'Banque',         desc: 'Hypothèques BCEAO' },
    { icon: '📋', label: 'Notaire',        desc: 'Actes & successions' },
    { icon: '🏘️', label: 'Commune',        desc: 'DAD & DAR locales' },
    { icon: '📰', label: 'Journal Officiel','desc': 'Publications' },
    { icon: '🗄️', label: 'ANNF',           desc: 'Archives WORM' },
  ]

  const stats = [
    { value: '148 372', label: 'Parcelles enregistrées' },
    { value: '89 441',  label: 'Certificats CCFM actifs' },
    { value: '33',      label: 'Workflows nationaux' },
    { value: '8',       label: 'Régions couvertes' },
  ]

  return (
    <div style={{
      minHeight: '100vh',
      background: 'var(--sand-light)',
      fontFamily: 'var(--font-body)',
    }}>

      {/* ── BANDEAU INSTITUTIONNEL ── */}
      <div style={{
        background: 'var(--green)',
        padding: '8px 32px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <span style={{ color: 'var(--sand)', fontSize: '11px', letterSpacing: '.06em', textTransform: 'uppercase' }}>
          République du Niger · Ministère de l'Urbanisme et de l'Habitat · DNCD
        </span>
        <span style={{ color: 'var(--sand)', fontSize: '11px', opacity: .7 }}>
          Version 1.0.1 · Production Ready
        </span>
      </div>

      {/* ── HERO ── */}
      <header style={{
        background: `linear-gradient(160deg, var(--green) 0%, #2A3A22 100%)`,
        padding: '80px 48px 96px',
        position: 'relative',
        overflow: 'hidden',
      }}>
        {/* Motif géométrique de fond */}
        <div style={{
          position: 'absolute', inset: 0, opacity: .04,
          backgroundImage: `repeating-linear-gradient(
            45deg, white 0, white 1px, transparent 1px, transparent 50%
          )`,
          backgroundSize: '32px 32px',
        }} />

        {/* Accent rouge */}
        <div style={{
          position: 'absolute', top: 0, right: 0,
          width: '480px', height: '6px',
          background: 'var(--red)',
        }} />

        <div style={{
          maxWidth: '900px', margin: '0 auto',
          opacity: visible ? 1 : 0,
          transform: visible ? 'none' : 'translateY(24px)',
          transition: 'all .7s cubic-bezier(.22,1,.36,1)',
        }}>
          {/* Emblème */}
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: '12px',
            marginBottom: '32px',
            padding: '8px 16px',
            border: '1px solid rgba(245,239,224,.2)',
            borderRadius: 'var(--radius-md)',
            background: 'rgba(245,239,224,.06)',
          }}>
            <div style={{
              width: 36, height: 36, borderRadius: '50%',
              background: 'var(--sand)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '18px',
            }}>🗺️</div>
            <span style={{ color: 'var(--sand)', fontSize: '13px', fontWeight: 500 }}>
              Plateforme Nationale de Gestion Foncière
            </span>
          </div>

          {/* Titre principal */}
          <h1 style={{
            fontFamily: 'var(--font-display)',
            fontSize: 'clamp(48px, 8vw, 88px)',
            fontWeight: 700,
            color: 'var(--white)',
            lineHeight: 1.0,
            marginBottom: '8px',
            letterSpacing: '-.02em',
          }}>
            FONCIER
            <span style={{ color: 'var(--red)' }}>+</span>
          </h1>

          <p style={{
            color: 'var(--sand)',
            fontSize: '20px',
            fontWeight: 300,
            maxWidth: '560px',
            lineHeight: 1.6,
            marginBottom: '48px',
            opacity: .9,
          }}>
            Sécurisation et traçabilité de la chaîne foncière nationale —
            de l'arrêté communal à l'archive WORM.
          </p>

          {/* CTA */}
          <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
            <button
              onClick={() => navigate('/login')}
              style={{
                background: 'var(--red)',
                color: 'white',
                border: 'none',
                padding: '14px 32px',
                borderRadius: 'var(--radius-md)',
                fontSize: '15px',
                fontWeight: 600,
                fontFamily: 'var(--font-body)',
                cursor: 'pointer',
                letterSpacing: '.01em',
                boxShadow: '0 4px 24px rgba(184,34,14,.35)',
                transition: 'all .2s',
              }}
              onMouseEnter={e => {
                (e.target as HTMLButtonElement).style.transform = 'translateY(-2px)'
                ;(e.target as HTMLButtonElement).style.boxShadow = '0 8px 32px rgba(184,34,14,.45)'
              }}
              onMouseLeave={e => {
                (e.target as HTMLButtonElement).style.transform = 'none'
                ;(e.target as HTMLButtonElement).style.boxShadow = '0 4px 24px rgba(184,34,14,.35)'
              }}
            >
              Accéder à la plateforme
            </button>
            <button
              onClick={() => navigate('/verify')}
              style={{
                background: 'transparent',
                color: 'var(--sand)',
                border: '1px solid rgba(245,239,224,.3)',
                padding: '14px 32px',
                borderRadius: 'var(--radius-md)',
                fontSize: '15px',
                fontFamily: 'var(--font-body)',
                cursor: 'pointer',
                transition: 'all .2s',
              }}
              onMouseEnter={e => {
                (e.target as HTMLButtonElement).style.borderColor = 'rgba(245,239,224,.7)'
                ;(e.target as HTMLButtonElement).style.background = 'rgba(245,239,224,.06)'
              }}
              onMouseLeave={e => {
                (e.target as HTMLButtonElement).style.borderColor = 'rgba(245,239,224,.3)'
                ;(e.target as HTMLButtonElement).style.background = 'transparent'
              }}
            >
              Vérifier un certificat CCFM
            </button>
          </div>
        </div>
      </header>

      {/* ── STATS BANDE ── */}
      <div style={{
        background: 'var(--ink)',
        padding: '32px 48px',
      }}>
        <div style={{
          maxWidth: '900px', margin: '0 auto',
          display: 'grid', gridTemplateColumns: 'repeat(4,1fr)',
          gap: '0',
        }}>
          {stats.map((s, i) => (
            <div key={i} style={{
              textAlign: 'center',
              padding: '0 24px',
              borderRight: i < 3 ? '1px solid rgba(255,255,255,.1)' : 'none',
              animation: `fadeIn .4s ease ${i * .1}s both`,
            }}>
              <div style={{
                fontFamily: 'var(--font-display)',
                fontSize: '36px', fontWeight: 700,
                color: 'var(--sand)',
                lineHeight: 1.1,
              }}>{s.value}</div>
              <div style={{ color: 'var(--gray-400)', fontSize: '12px', marginTop: '4px' }}>
                {s.label}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── MODULES ── */}
      <section style={{ padding: '80px 48px', maxWidth: '960px', margin: '0 auto' }}>
        <div style={{ marginBottom: '48px' }}>
          <div style={{
            display: 'inline-block',
            background: 'var(--green-pale)',
            color: 'var(--green)',
            fontSize: '11px', fontWeight: 600,
            letterSpacing: '.08em', textTransform: 'uppercase',
            padding: '4px 12px', borderRadius: 'var(--radius-sm)',
            marginBottom: '16px',
          }}>11 Modules Métier</div>
          <h2 style={{
            fontFamily: 'var(--font-display)',
            fontSize: '38px', fontWeight: 700,
            color: 'var(--ink)',
            lineHeight: 1.15,
          }}>
            La chaîne foncière nationale,<br/>
            <span style={{ color: 'var(--green)' }}>de bout en bout</span>
          </h2>
        </div>

        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 1fr)',
          gap: '16px',
        }}>
          {modules.map((m, i) => (
            <div
              key={i}
              style={{
                background: 'white',
                border: '1px solid var(--gray-200)',
                borderRadius: 'var(--radius-lg)',
                padding: '24px',
                cursor: 'pointer',
                transition: 'all .2s',
                animation: `fadeIn .4s ease ${i * .05}s both`,
              }}
              onMouseEnter={e => {
                const el = e.currentTarget
                el.style.borderColor = 'var(--green)'
                el.style.boxShadow = 'var(--shadow-md)'
                el.style.transform = 'translateY(-2px)'
              }}
              onMouseLeave={e => {
                const el = e.currentTarget
                el.style.borderColor = 'var(--gray-200)'
                el.style.boxShadow = 'none'
                el.style.transform = 'none'
              }}
              onClick={() => navigate('/login')}
            >
              <div style={{ fontSize: '28px', marginBottom: '12px' }}>{m.icon}</div>
              <div style={{ fontWeight: 600, fontSize: '15px', color: 'var(--ink)' }}>
                {m.label}
              </div>
              <div style={{ color: 'var(--gray-500)', fontSize: '13px', marginTop: '4px' }}>
                {m.desc}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── CHAÎNE FONCIÈRE ── */}
      <section style={{
        background: 'var(--green-pale)',
        padding: '64px 48px',
        borderTop: '1px solid rgba(60,79,51,.1)',
        borderBottom: '1px solid rgba(60,79,51,.1)',
      }}>
        <div style={{ maxWidth: '960px', margin: '0 auto' }}>
          <div style={{
            display: 'inline-block',
            background: 'var(--green)',
            color: 'var(--sand)',
            fontSize: '11px', fontWeight: 600,
            letterSpacing: '.08em', textTransform: 'uppercase',
            padding: '4px 12px', borderRadius: 'var(--radius-sm)',
            marginBottom: '24px',
          }}>Chaîne foncière — 9 maillons</div>

          <div style={{
            display: 'flex', alignItems: 'center',
            gap: '0', flexWrap: 'wrap',
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
          }}>
            {['COMMUNE','ARRÊTÉ','JO','RNAF','CADASTRE','RNP','ATTRIBUTION','CCFM','TRANSACTION'].map((m, i, arr) => (
              <>
                <div key={m} style={{
                  background: 'var(--green)',
                  color: 'var(--sand)',
                  padding: '8px 14px',
                  borderRadius: 'var(--radius-sm)',
                  fontWeight: 600,
                  letterSpacing: '.04em',
                }}>{m}</div>
                {i < arr.length - 1 && (
                  <div key={`arrow-${i}`} style={{
                    color: 'var(--green-light)',
                    fontSize: '16px',
                    padding: '0 4px',
                  }}>→</div>
                )}
              </>
            ))}
          </div>
        </div>
      </section>

      {/* ── SÉCURITÉ ── */}
      <section style={{ padding: '80px 48px', maxWidth: '960px', margin: '0 auto' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '48px', alignItems: 'center' }}>
          <div>
            <div style={{
              display: 'inline-block',
              background: 'var(--red-light)',
              color: 'var(--red)',
              fontSize: '11px', fontWeight: 600,
              letterSpacing: '.08em', textTransform: 'uppercase',
              padding: '4px 12px', borderRadius: 'var(--radius-sm)',
              marginBottom: '16px',
            }}>Antifraude multicouche</div>
            <h2 style={{
              fontFamily: 'var(--font-display)',
              fontSize: '32px', fontWeight: 700, lineHeight: 1.2,
              color: 'var(--ink)', marginBottom: '20px',
            }}>
              82 triggers PostgreSQL<br/>
              <span style={{ color: 'var(--red)' }}>au cœur du système</span>
            </h2>
            <p style={{ color: 'var(--gray-600)', lineHeight: 1.7, fontSize: '14px' }}>
              Aucune mutation foncière non conforme ne peut être enregistrée,
              même en cas d'accès direct à la base. Les règles métier sont
              gravées dans le moteur PostgreSQL lui-même.
            </p>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            {[
              { n: '82',   label: 'Triggers DB' },
              { n: '30',   label: 'Tests AF E2E' },
              { n: '957',  label: 'Cas RBAC' },
              { n: '29',   label: 'Rôles sécurité' },
            ].map((s,i) => (
              <div key={i} style={{
                background: 'white',
                border: '1px solid var(--gray-200)',
                borderRadius: 'var(--radius-lg)',
                padding: '20px',
                textAlign: 'center',
              }}>
                <div style={{
                  fontFamily: 'var(--font-display)',
                  fontSize: '36px', fontWeight: 700,
                  color: 'var(--green)',
                }}>{s.n}</div>
                <div style={{ color: 'var(--gray-500)', fontSize: '12px', marginTop: '4px' }}>
                  {s.label}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── FOOTER ── */}
      <footer style={{
        background: 'var(--green)',
        padding: '32px 48px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: '12px',
      }}>
        <div>
          <div style={{ fontFamily: 'var(--font-display)', fontSize: '22px', color: 'white', fontWeight: 700 }}>
            FONCIER<span style={{ color: 'var(--red)' }}>+</span>
          </div>
          <div style={{ color: 'var(--sand)', fontSize: '11px', opacity: .7, marginTop: '2px' }}>
            Direction Nationale du Cadastre et du Domaine · v1.0.1
          </div>
        </div>
        <div style={{ display: 'flex', gap: '24px' }}>
          {['Contact','Mentions légales','Portail citoyen'].map(l => (
            <button
              key={l}
              style={{
                background: 'none', border: 'none',
                color: 'var(--sand)', fontSize: '12px',
                cursor: 'pointer', fontFamily: 'var(--font-body)',
                opacity: .7, transition: 'opacity .2s',
              }}
              onMouseEnter={e => (e.target as HTMLButtonElement).style.opacity = '1'}
              onMouseLeave={e => (e.target as HTMLButtonElement).style.opacity = '.7'}
            >{l}</button>
          ))}
        </div>
      </footer>
    </div>
  )
}
