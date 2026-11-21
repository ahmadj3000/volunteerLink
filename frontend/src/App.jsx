import { useState } from 'react'
import { signIn, signOut, confirmSignIn } from 'aws-amplify/auth'
import './App.css'

export default function App() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [signedIn, setSignedIn] = useState(false)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [needsNewPassword, setNeedsNewPassword] = useState(false)

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
      } else {
        setMessage(
          `Additional sign-in step: ${result.nextStep.signInStep}`
        )
      }
    } catch (error) {
      setPassword('')
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

      {signedIn ? (
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
        </form>
      )}

      {message && <p role="alert">{message}</p>}
    </main>
  )
}