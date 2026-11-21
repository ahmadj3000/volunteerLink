import { useEffect, useState } from 'react'
import { fetchAuthSession, signOut } from 'aws-amplify/auth'
import { Hub } from 'aws-amplify/utils'
import {
  HashRouter,
  NavLink,
  Navigate,
  Route,
  Routes,
} from 'react-router-dom'
import LoginScreen from './LoginScreen'
import Opportunities from './pages/malek/Opportunities'
import NgoDashboard from './pages/malek/NgoDashboard'
import AdminDashboard from './pages/malek/AdminDashboard'
import Applications from './pages/ahmad/Applications'
import Applicants from './pages/ahmad/Applicants'
import Recommendations from './pages/tarek/Recommendations'
import './App.css'

export default function App() {
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    let revision = 0

    async function loadSession() {
      const current = ++revision
      try {
        const next = await fetchAuthSession()
        if (active && current === revision) {
          setSession(next.tokens ? next : null)
          setError('')
        }
      } catch {
        if (active && current === revision) {
          setSession(null)
          setError('Your session could not be restored. Please sign in again.')
        }
      } finally {
        if (active && current === revision) setLoading(false)
      }
    }

    const stop = Hub.listen('auth', ({ payload }) => {
      if (payload.event === 'signedIn') {
        void loadSession()
      } else if (
        payload.event === 'signedOut' ||
        payload.event === 'tokenRefresh_failure'
      ) {
        revision += 1
        if (active) {
          setSession(null)
          setLoading(false)
        }
      }
    })

    void loadSession()

    return () => {
      active = false
      stop()
    }
  }, [])

  async function logout() {
    setBusy(true)
    setError('')
    try {
      await signOut()
      setSession(null)
    } catch {
      setError('Could not sign out. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return <div className="loading" role="status">Opening VolunteerLink…</div>
  }

  if (!session) {
    return (
      <div className="login-layout">
        {error && <p role="alert">{error}</p>}
        <LoginScreen />
      </div>
    )
  }

  const claims = session.tokens.accessToken.payload
  const rawGroups = claims['cognito:groups']
  const groups = Array.isArray(rawGroups) ? rawGroups : []
  const isAdmin = groups.includes('admin')
  const isNgo = groups.includes('ngo')
  const isVolunteer = groups.includes('volunteer')
  const email = session.tokens.idToken?.payload.email || 'Signed-in user'

  function restricted(allowed, page) {
    return allowed ? page : <Navigate to="/" replace />
  }

  return (
    <HashRouter>
      <div className="app-shell">
        <aside className="sidebar">
          <NavLink className="brand" to="/">VolunteerLink<span>LEBANON</span></NavLink>
          <p className="sidebar-caption">Small actions. Shared impact.</p>

          <nav aria-label="Main navigation">
            <NavLink to="/" end>Explore opportunities</NavLink>
            {isVolunteer && (
              <>
                <NavLink to="/recommendations">Recommended for you</NavLink>
                <NavLink to="/applications">My applications</NavLink>
              </>
            )}
            {isNgo && (
              <>
                <NavLink to="/ngo">NGO dashboard</NavLink>
                <NavLink to="/applicants">Review applicants</NavLink>
              </>
            )}
            {isAdmin && <NavLink to="/admin">NGO approvals</NavLink>}
          </nav>

          <div className="sidebar-footer">
            <span className="eyebrow">YOUR ACCOUNT</span>
            <p className="account-email">{String(email)}</p>
            <span className="badge">
              {groups.length ? groups.join(' · ') : 'No role assigned'}
            </span>
            <button className="secondary" onClick={logout} disabled={busy}>
              {busy ? 'Signing out…' : 'Sign out'}
            </button>
          </div>
        </aside>

        <main className="workspace">
          <header className="topbar">
            <span>Community starts with you.</span>
            <span className="badge">Lebanon</span>
          </header>

          <div className="hero">
            <span className="eyebrow">TOGETHER, WE SHOW UP</span>
            <h1>Your time can make<br />a difference.</h1>
            <p>Connect with local NGOs. Put your skills into action.</p>
          </div>

          {!groups.length && (
            <p className="notice">
              You’re signed in. Your account has no application role yet.
              Role-specific pages will appear once a role is assigned.
            </p>
          )}

          {error && <p role="alert">{error}</p>}

          <Routes>
            <Route path="/" element={<Opportunities />} />
            <Route path="/recommendations" element={
              restricted(isVolunteer, <Recommendations />)
            } />
            <Route path="/applications" element={
              restricted(isVolunteer, <Applications />)
            } />
            <Route path="/ngo" element={
              restricted(isNgo, <NgoDashboard />)
            } />
            <Route path="/applicants" element={
              restricted(isNgo, <Applicants />)
            } />
            <Route path="/admin" element={
              restricted(isAdmin, <AdminDashboard />)
            } />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </HashRouter>
  )
}
