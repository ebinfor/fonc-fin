import { z } from 'zod'
/**
 * FONCIER+ v3.4.7 — Page Login
 */
import { useState, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

const COMPTES_TEST = [
  { email: 'admin@foncier.gov.ne',        role: 'Administrateur National' },
  { email: 'ccfm@foncier.gov.ne',         role: 'Chef CCFM Niamey' },
  { email: 'urbaniste@foncier.gov.ne',    role: 'Directeur Urbanisme' },
  { email: 'cadastre@foncier.gov.ne',     role: 'Directeur Cadastre' },
  { email: 'notaire@foncier.gov.ne',      role: 'Notaire agréé' },
  { email: 'juge@justice.foncier.ne',     role: 'Juge foncier' },
  { email: 'directeur@banque.foncier.ne', role: 'Directeur Banque' },
  { email: 'editeur.jo@foncier.gov.ne',   role: 'Éditeur JO' },
]


// Schéma de validation login
const loginSchema = z.object({
  email:    z.string().email('Email invalide').min(5),
  password: z.string().min(8, 'Mot de passe minimum 8 caractères'),
})
type LoginForm = z.infer<typeof loginSchema>

function validateLoginForm(data: LoginForm): string | null {
  try {
    loginSchema.parse(data)
    return null
  } catch (err: unknown) {
    if (err instanceof z.ZodError) return err.errors[0]?.message ?? 'Formulaire invalide'
    return 'Erreur de validation'
  }
}

export default function LoginPage() {
  const navigate = useNavigate()
  const { login }  = useAuth()

  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [error,    setError]    = useState('')
  const [loading,  setLoading]  = useState(false)
  const [showDemo, setShowDemo] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!email || !password) { setError('Remplissez tous les champs'); return }
    setLoading(true)
    setError('')
    await new Promise(r => setTimeout(r, 600)) // UX: feedback visuel
    const ok = login(email, password)
    setLoading(false)
    if (ok) navigate('/dashboard')
    else setError('Identifiants incorrects')
  }

  const fillDemo = (e: string) => {
    setEmail(e)
    setPassword('Admin@2026!')
    setShowDemo(false)
    setError('')
  }

  return (
    <div style={{
      minHeight: '100vh',
      background: `linear-gradient(160deg, var(--green) 0%, #1E2E18 100%)`,
      display: 'flex', alignItems: 'stretch',
      fontFamily: 'var(--font-body)',
    }}>
      {/* ── PANNEAU GAUCHE — visuel ── */}
      <div style={{
        flex: 1, display: 'none',
        background: 'rgba(0,0,0,.12)',
        padding: '64px',
        flexDirection: 'column',
        justifyContent: 'space-between',
        borderRight: '1px solid rgba(255,255,255,.08)',
        '@media (min-width: 900px)': { display: 'flex' },
      }} className="login-left">
        <div>
          <div style={{
            fontFamily: 'var(--font-display)',
            fontSize: '52px', fontWeight: 700,
            color: 'white', lineHeight: 1.0,
          }}>
            FONCIER<span style={{ color: 'var(--red)' }}>+</span>
          </div>
          <p style={{ color: 'rgba(245,239,224,.6)', marginTop: '12px', fontSize: '14px' }}>
            Plateforme Nationale de Gestion Foncière<br />
            République du Niger — DNCD
          </p>
        </div>

        <div>
          {[
            { icon: '🔐', text: '82 triggers antifraude PostgreSQL' },
            { icon: '📜', text: '33 workflows fonciers nationaux' },
            { icon: '🗄️', text: 'Archives WORM immuables ANNF' },
            { icon: '🌍', text: '8 régions du Niger couvertes' },
          ].map((item, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: '12px',
              padding: '10px 0',
              borderBottom: i < 3 ? '1px solid rgba(255,255,255,.06)' : 'none',
            }}>
              <span style={{ fontSize: '16px' }}>{item.icon}</span>
              <span style={{ color: 'rgba(245,239,224,.75)', fontSize: '13px' }}>{item.text}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── PANNEAU DROIT — formulaire ── */}
      <div style={{
        width: '100%', maxWidth: '480px',
        padding: '48px 40px',
        background: 'var(--sand-light)',
        display: 'flex', flexDirection: 'column',
        justifyContent: 'center',
        margin: '0 auto',
        animation: 'fadeIn .5s ease',
      }}>
        {/* Header */}
        <button
          onClick={() => navigate('/')}
          style={{
            alignSelf: 'flex-start', marginBottom: '32px',
            background: 'none', border: 'none', cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: '8px',
            color: 'var(--gray-500)', fontSize: '13px',
            fontFamily: 'var(--font-body)',
            transition: 'color .2s',
          }}
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--green)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'var(--gray-500)')}
        >
          ← Accueil
        </button>

        <div style={{ marginBottom: '32px' }}>
          <div style={{
            width: 48, height: 48, borderRadius: 'var(--radius-lg)',
            background: 'var(--green)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: '22px', marginBottom: '16px',
          }}>🏛️</div>
          <h1 style={{
            fontFamily: 'var(--font-display)',
            fontSize: '28px', fontWeight: 700,
            color: 'var(--ink)', marginBottom: '6px',
          }}>Connexion sécurisée</h1>
          <p style={{ color: 'var(--gray-500)', fontSize: '14px' }}>
            Accès réservé aux agents agréés DNCD
          </p>
        </div>

        {/* Formulaire */}
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div>
            <label style={{
              display: 'block', fontSize: '12px', fontWeight: 600,
              color: 'var(--gray-700)', marginBottom: '6px',
              letterSpacing: '.03em', textTransform: 'uppercase',
            }}>
              Adresse e-mail institutionnelle
            </label>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="prenom.nom@foncier.gov.ne"
              autoComplete="email"
              style={{
                width: '100%', padding: '12px 14px',
                border: `1px solid ${error ? 'var(--red)' : 'var(--gray-300)'}`,
                borderRadius: 'var(--radius-md)',
                fontSize: '14px', fontFamily: 'var(--font-body)',
                background: 'white', color: 'var(--ink)',
                outline: 'none', transition: 'border-color .2s',
              }}
              onFocus={e => e.target.style.borderColor = 'var(--green)'}
              onBlur={e => e.target.style.borderColor = error ? 'var(--red)' : 'var(--gray-300)'}
            />
          </div>

          <div>
            <label style={{
              display: 'block', fontSize: '12px', fontWeight: 600,
              color: 'var(--gray-700)', marginBottom: '6px',
              letterSpacing: '.03em', textTransform: 'uppercase',
            }}>
              Mot de passe
            </label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="••••••••••••"
              autoComplete="current-password"
              style={{
                width: '100%', padding: '12px 14px',
                border: `1px solid ${error ? 'var(--red)' : 'var(--gray-300)'}`,
                borderRadius: 'var(--radius-md)',
                fontSize: '14px', fontFamily: 'var(--font-body)',
                background: 'white', color: 'var(--ink)',
                outline: 'none', transition: 'border-color .2s',
              }}
              onFocus={e => e.target.style.borderColor = 'var(--green)'}
              onBlur={e => e.target.style.borderColor = error ? 'var(--red)' : 'var(--gray-300)'}
            />
          </div>

          {/* Erreur */}
          {error && (
            <div style={{
              background: 'var(--red-light)',
              border: '1px solid rgba(184,34,14,.2)',
              borderRadius: 'var(--radius-md)',
              padding: '10px 14px',
              display: 'flex', alignItems: 'center', gap: '8px',
              fontSize: '13px', color: 'var(--red)',
              animation: 'fadeIn .2s ease',
            }}>
              <span>⚠</span> {error}
            </div>
          )}

          {/* Bouton connexion */}
          <button
            type="submit"
            disabled={loading}
            style={{
              background: loading ? 'var(--gray-400)' : 'var(--green)',
              color: 'white', border: 'none',
              padding: '14px', borderRadius: 'var(--radius-md)',
              fontSize: '15px', fontWeight: 600,
              fontFamily: 'var(--font-body)',
              cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'all .2s',
              display: 'flex', alignItems: 'center',
              justifyContent: 'center', gap: '8px',
            }}
            onMouseEnter={e => { if (!loading) (e.currentTarget.style.background = 'var(--green-mid)') }}
            onMouseLeave={e => { if (!loading) (e.currentTarget.style.background = 'var(--green)') }}
          >
            {loading ? (
              <>
                <span style={{
                  width: 16, height: 16, border: '2px solid rgba(255,255,255,.3)',
                  borderTopColor: 'white', borderRadius: '50%',
                  animation: 'spin .6s linear infinite',
                  display: 'inline-block',
                }} />
                Connexion en cours…
              </>
            ) : 'Se connecter'}
          </button>
        </form>

        {/* Comptes démo */}
        <div style={{ marginTop: '24px' }}>
          <button
            onClick={() => setShowDemo(!showDemo)}
            style={{
              background: 'none', border: 'none',
              color: 'var(--gray-500)', fontSize: '12px',
              cursor: 'pointer', fontFamily: 'var(--font-body)',
              display: 'flex', alignItems: 'center', gap: '6px',
              padding: '0',
            }}
          >
            <span style={{
              display: 'inline-block',
              transform: showDemo ? 'rotate(90deg)' : 'none',
              transition: 'transform .2s',
            }}>▶</span>
            Comptes de démonstration
          </button>

          {showDemo && (
            <div style={{
              marginTop: '12px',
              border: '1px solid var(--gray-200)',
              borderRadius: 'var(--radius-md)',
              overflow: 'hidden',
              animation: 'fadeIn .2s ease',
            }}>
              {COMPTES_TEST.map((c, i) => (
                <button
                  key={i}
                  onClick={() => fillDemo(c.email)}
                  style={{
                    width: '100%', padding: '10px 14px',
                    background: 'white', border: 'none',
                    borderBottom: i < COMPTES_TEST.length - 1 ? '1px solid var(--gray-100)' : 'none',
                    cursor: 'pointer', textAlign: 'left',
                    fontFamily: 'var(--font-body)',
                    display: 'flex', justifyContent: 'space-between',
                    alignItems: 'center',
                    transition: 'background .15s',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--green-pale)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'white')}
                >
                  <span style={{ fontSize: '12px', color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>
                    {c.email}
                  </span>
                  <span style={{
                    fontSize: '11px', color: 'var(--green)',
                    background: 'var(--green-pale)',
                    padding: '2px 8px', borderRadius: 'var(--radius-sm)',
                  }}>
                    {c.role}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Note sécurité */}
        <p style={{
          marginTop: '32px', fontSize: '11px',
          color: 'var(--gray-400)', textAlign: 'center',
          lineHeight: 1.6,
        }}>
          🔒 Connexion chiffrée TLS 1.3 · JWT HS256 2h<br />
          RBAC 29 rôles · Audit trail complet
        </p>
      </div>
    </div>
  )
}
