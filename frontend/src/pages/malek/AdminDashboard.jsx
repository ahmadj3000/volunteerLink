import { useEffect, useState } from 'react'
import { ngoApi } from '../../lib/ngo-api'

export default function AdminDashboard() {
  const [items, setItems] = useState([])
  const [cursor, setCursor] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    ngoApi('/admin/ngos')
      .then((data) => {
        if (active) {
          setItems(data.items)
          setCursor(data.nextCursor)
        }
      })
      .catch((error) => { if (active) setError(error.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  async function load(more = false) {
    setBusy(true)
    setError('')
    try {
      const query = more && cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
      const data = await ngoApi(`/admin/ngos${query}`)
      setItems((previous) => more ? [...previous, ...data.items] : data.items)
      setCursor(data.nextCursor)
    } catch (error) {
      setError(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function decide(ngoId, status) {
    setBusy(true)
    setError('')
    try {
      const data = await ngoApi(`/admin/ngos/${encodeURIComponent(ngoId)}`, {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      })
      setItems((previous) => previous.map(
        (item) => item.ngoId === ngoId ? data.ngo : item
      ))
    } catch (error) {
      setError(error.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <span className="eyebrow">ADMINISTRATION</span>
      <h2>NGO approvals</h2>
      <p>Review organization registrations before they can publish opportunities.</p>
      <button type="button" onClick={() => load()} disabled={loading || busy}>
        Refresh registrations
      </button>
      {error && <p role="alert">{error}</p>}
      {loading ? <p>Loading…</p> : items.length === 0 ? <p>No registrations found.</p> : (
        items.map((ngo) => (
          <article key={ngo.ngoId} style={{ borderTop: '1px solid #ddd', marginTop: 20, paddingTop: 16 }}>
            <h3>{ngo.name}</h3>
            <p>{ngo.description}</p>
            <p>Location: {ngo.location}</p>
            <p>Status: <strong>{ngo.status}</strong></p>
            {ngo.status === 'pending' && (
              <div style={{ display: 'flex', gap: 12 }}>
                <button disabled={busy} onClick={() => decide(ngo.ngoId, 'approved')}>
                  Approve
                </button>
                <button disabled={busy} onClick={() => decide(ngo.ngoId, 'rejected')}>
                  Reject
                </button>
              </div>
            )}
          </article>
        ))
      )}
      {cursor && (
        <button disabled={loading || busy} onClick={() => load(true)}>
          Load more
        </button>
      )}
    </section>
  )
}
