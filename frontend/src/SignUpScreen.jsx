import { useState } from 'react'
import {
  signUp,
  confirmSignUp,
  resendSignUpCode,
  autoSignIn,
} from 'aws-amplify/auth'
import './SignUpScreen.css'

// Self sign-up for volunteers and NGOs.
// The browser only sends custom:accountType ("volunteer" or "ngo").
// Group membership is decided server-side by the vl-post-confirmation
// Lambda, which never grants "admin". NGOs still need admin approval
// of their registration before they can post.
export default function SignUpScreen({
  initialEmail = '',
  startAtConfirm = false,
  onBack,
}) {
  const [step, setStep] = useState(startAtConfirm ? 'confirm' : 'details')
  const [email, setEmail] = useState(initialEmail)
  const [password, setPassword] = useState('')
  const [accountType, setAccountType] = useState('volunteer')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  async function handleSignUp(event) {
    event.preventDefault()
    setBusy(true)
    setMessage('')
    try {
      const result = await signUp({
        username: email.trim(),
        password,
        options: {
          userAttributes: {
            email: email.trim(),
            'custom:accountType': accountType,
          },
          autoSignIn: true,
        },
      })
      setPassword('')
      if (result.nextStep.signUpStep === 'CONFIRM_SIGN_UP') {
        setStep('confirm')
        setMessage(`We emailed a 6-digit code to ${email.trim()}.`)
      } else if (result.nextStep.signUpStep === 'COMPLETE_AUTO_SIGN_IN') {
        await autoSignIn()
      }
    } catch (error) {
      setPassword('')
      setMessage(error.message || 'Sign-up failed.')
    } finally {
      setBusy(false)
    }
  }

  async function handleConfirm(event) {
    event.preventDefault()
    setBusy(true)
    setMessage('')
    try {
      const result = await confirmSignUp({
        username: email.trim(),
        confirmationCode: code.trim(),
      })
      if (result.nextStep.signUpStep === 'COMPLETE_AUTO_SIGN_IN') {
        // App.jsx's Hub 'signedIn' listener takes it from here.
        await autoSignIn()
      } else {
        // Came here from the sign-in page, so there's no stored password
        // for auto sign-in. Send them back to sign in normally.
        onBack('Email verified. Please sign in.')
      }
    } catch (error) {
      setMessage(error.message || 'Verification failed.')
    } finally {
      setBusy(false)
    }
  }

  async function handleResend() {
    setBusy(true)
    setMessage('')
    try {
      await resendSignUpCode({ username: email.trim() })
      setMessage(`New code sent to ${email.trim()}.`)
    } catch (error) {
      setMessage(error.message || 'Could not resend the code.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {step === 'details' ? (
        <form className="signup" onSubmit={handleSignUp}>
          <h2>Create an account</h2>

          <div className="account-type" role="group" aria-labelledby="account-type-label">
            <span id="account-type-label" className="account-type-label">
              I am signing up as
            </span>
            <div className="account-type-options">
              {[
                ['volunteer', 'Volunteer', 'Find and apply to opportunities'],
                ['ngo', 'NGO', 'Post opportunities after admin approval'],
              ].map(([value, title, note]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={accountType === value}
                  onClick={() => setAccountType(value)}
                  disabled={busy}
                >
                  {title}
                  <small>{note}</small>
                </button>
              ))}
            </div>
          </div>

          <label>
            Email
            <input
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              disabled={busy}
              required
            />
          </label>

          <label>
            Password
            <input
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={busy}
              minLength={10}
              required
            />
          </label>
          <p className="hint">
            At least 10 characters, including uppercase, lowercase, and a
            number.
          </p>

          <button type="submit" disabled={busy}>
            {busy ? 'Please wait…' : 'Create account'}
          </button>
          <button
            type="button"
            className="link-button"
            onClick={() => onBack('')}
            disabled={busy}
          >
            Back to sign in
          </button>
        </form>
      ) : (
        <form className="signup" onSubmit={handleConfirm}>
          <h2>Verify your email</h2>
          <p>Enter the 6-digit code sent to {email.trim()}.</p>

          <label>
            Code
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              disabled={busy}
              required
            />
          </label>

          <button type="submit" disabled={busy}>
            {busy ? 'Please wait…' : 'Verify'}
          </button>
          <button type="button" className="secondary" onClick={handleResend} disabled={busy}>
            Resend code
          </button>
          <button
            type="button"
            className="link-button"
            onClick={() => onBack('')}
            disabled={busy}
          >
            Back to sign in
          </button>
        </form>
      )}

      {message && <p role="alert">{message}</p>}
    </>
  )
}
