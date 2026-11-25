import { useEffect, useState } from 'react'
import {
  signIn,
  signOut,
  confirmSignIn,
  fetchAuthSession,
} from 'aws-amplify/auth'
import SignUpScreen from './SignUpScreen'
import './App.css'

export default function App() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [signedIn, setSignedIn] = useState(false)
  const [checkingSession, setCheckingSession] = useState(true)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [needsNewPassword, setNeedsNewPassword] = useState(false)
  // 'signin' | 'signup' | 'confirm' (unverified user trying to sign in)
  const [mode, setMode] = useState('signin')

  useEffect(() => {
    let active = true

    async function restoreSession() {
      try {
        const session = await fetchAuthSession()

        if (active) {
          setSignedIn(Boolean(session.tokens?.accessToken))
        }
      } catch {
        if (active) {
          setSignedIn(false)
          setMessage(
            'Could not restore your session. Please sign in again.'
          )
        }
      } finally {
        if (active) {
          setCheckingSession(false)
        }
      }
    }

    restoreSession()

    return () => {
      active = false
    }
  }, [])

  async function handleLogin(event) {
    event.preventDefault()
    setBusy(true)
    setMessage('')

    try {
      const result = needsNewPassword
        ? await confirmSignIn({ challengeResponse: password })
        : await signIn({
            username: email.trim(),
            password,
          })

      setPassword('')

      if (result.isSignedIn) {
        setSignedIn(true)
        setNeedsNewPassword(false)
      } else if (
        result.nextStep.signInStep ===
        'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED'
      ) {
        setNeedsNewPassword(true)
        setMessage(
          'Choose a permanent password: at least 10 characters, including uppercase, lowercase, and a number.'
        )
      } else if (result.nextStep.signInStep === 'CONFIRM_SIGN_UP') {
        setMode('confirm')
      } else {
        setMessage(
          `Additional sign-in step: ${result.nextStep.signInStep}`
        )
      }
    } catch (error) {
      setPassword('')
      if (error.name === 'UserNotConfirmedException') {
        setMode('confirm')
        return
      }
      setMessage(error.message || 'Sign-in failed.')
    } finally {
      setBusy(false)
    }
  }

  async function handleLogout() {
    setBusy(true)
    setMessage('')

    try {
      await signOut()
      setSignedIn(false)
      setNeedsNewPassword(false)
      setPassword('')
    } catch (error) {
      setMessage(error.message || 'Sign-out failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main>
      <h1>VolunteerLink</h1>
      <p>Connecting volunteers and NGOs across Lebanon.</p>

      {checkingSession ? (
        <section>
          <p role="status">Checking your session…</p>
        </section>
      ) : signedIn ? (
        <section>
          <h2>You’re signed in</h2>
          <button
            type="button"
            onClick={handleLogout}
            disabled={busy}
          >
            {busy ? 'Signing out…' : 'Sign out'}
          </button>
        </section>
      ) : mode !== 'signin' ? (
        <SignUpScreen
          initialEmail={email}
          startAtConfirm={mode === 'confirm'}
          onBack={(note) => {
            setMode('signin')
            setMessage(note)
          }}
        />
      ) : (
        <form onSubmit={handleLogin}>
          <h2>
            {needsNewPassword ? 'Set your password' : 'Sign in'}
          </h2>

          <label>
            Email
            <input
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              disabled={busy || needsNewPassword}
              required
            />
          </label>

          <label>
            {needsNewPassword ? 'New password' : 'Password'}
            <input
              type="password"
              autoComplete={
                needsNewPassword
                  ? 'new-password'
                  : 'current-password'
              }
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={busy}
              minLength={needsNewPassword ? 10 : undefined}
              required
            />
          </label>

          <button type="submit" disabled={busy}>
            {busy
              ? 'Please wait…'
              : needsNewPassword
                ? 'Save password'
                : 'Sign in'}
          </button>

          {!needsNewPassword && (
            <button
              type="button"
              className="secondary"
              onClick={() => {
                setMessage('')
                setMode('signup')
              }}
              disabled={busy}
            >
              Create an account
            </button>
          )}
        </form>
      )}

      {message && <p role="alert">{message}</p>}
    </main>
  )
}