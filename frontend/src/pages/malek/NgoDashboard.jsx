import PostOpportunity from './PostOpportunity'
import { useEffect, useState } from 'react'
import { ngoApi } from '../../lib/ngo-api'

export default function NgoDashboard() {
  const [ngo, setNgo] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [form, setForm] = useState({ name: '', description: '', location: '' })

  useEffect(() => {
    let active = true
    ngoApi('/ngo/registration')
      .then((data) => { if (active) setNgo(data.ngo) })
      .catch((error) => { if (active) setError(error.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  async function refresh() {
    setBusy(true)
    setError('')
    try {
      const data = await ngoApi('/ngo/registration')
      setNgo(data.ngo)
    } catch (error) {
      setError(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const data = await ngoApi('/ngo/registration', {
        method: 'POST',
        body: JSON.stringify(form),
      })
      setNgo(data.ngo)
    } catch (error) {
      setError(error.message)
    } finally {
      setBusy(false)
    }
  }

  function field(event) {
    setForm({ ...form, [event.target.name]: event.target.value })
  }

  return (
    <section className="panel">
      <span className="eyebrow">NGO WORKSPACE</span>
      <h2>Your organization</h2>
      {loading ? <p>Loading registration…</p> : ngo ? (
        <div>
          <h3>{ngo.name}</h3>
          <p>{ngo.description}</p>
          <p>Location: {ngo.location}</p>
          <p><strong>Approval status: {ngo.status}</strong></p>
          {ngo.status === 'pending' && <p>An administrator will review your registration.</p>}
          {ngo.status === 'approved' && <PostOpportunity />}
          {ngo.status === 'rejected' && <p>Your registration was rejected. Contact an administrator.</p>}
        </div>
      ) : !error ? (
        <form onSubmit={submit} style={{ display: 'grid', gap: '1rem', maxWidth: 600 }}>
          <p>Register your organization for administrator review.</p>
          <label>
            Organization name
            <input name="name" value={form.name} onChange={field}
              minLength={2} maxLength={120} required disabled={busy} />
          </label>
          <label>
            Location in Lebanon
            <input name="location" value={form.location} onChange={field}
              minLength={2} maxLength={120} required disabled={busy} />
          </label>
          <label>
            Description
            <textarea name="description" value={form.description} onChange={field}
              minLength={10} maxLength={2000} rows={5} required disabled={busy}
              style={{ display: 'block', width: '100%' }} />
          </label>
          <button type="submit" disabled={busy}>
            {busy ? 'Submitting…' : 'Submit registration'}
          </button>
        </form>
      ) : null}
      {error && <p role="alert">{error}</p>}
      <button type="button" onClick={refresh} disabled={loading || busy}>
        Refresh status
      </button>
    </section>
  )
}
