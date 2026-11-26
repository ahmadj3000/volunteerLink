import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { volunteerApi } from '../../lib/volunteer-api'

export default function ApplyButton({ opportunity }) {
  const [expanded, setExpanded] = useState(false)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState('')
  const submitting = useRef(false)

  async function apply(event) {
    event.preventDefault()
    if (submitting.current || submitted) return

    submitting.current = true
    setBusy(true)
    setError('')

    try {
      await volunteerApi('/applications', {
        method: 'POST',
        body: JSON.stringify({
          opportunityId: opportunity.opportunityId,
          message: message.trim(),
        }),
      })
      setSubmitted(true)
      setMessage('')
    } catch (error) {
      setError(error.message)
    } finally {
      submitting.current = false
      setBusy(false)
    }
  }

  if (submitted) {
    return (
      <div role="status">
        <p>Application submitted. The NGO will review it.</p>
        <Link to="/applications">View my applications</Link>
      </div>
    )
  }

  if (!expanded) {
    return (
      <button type="button" onClick={() => setExpanded(true)}>
        Apply
      </button>
    )
  }

  return (
    <form onSubmit={apply} style={{ maxWidth: 600, marginTop: 16 }}>
      <label>
        Message to the NGO (optional)
        <textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          maxLength={500}
          rows={3}
          disabled={busy}
          style={{ display: 'block', width: '100%' }}
        />
      </label>

      <p>{message.length}/500 characters</p>
      {error && <p role="alert">{error}</p>}

      <button type="submit" disabled={busy}>
        {busy ? 'Submitting…' : 'Submit application'}
      </button>

      <button
        type="button"
        className="secondary"
        disabled={busy}
        onClick={() => {
          setExpanded(false)
          setError('')
        }}
      >
        Cancel
      </button>
    </form>
  )
}