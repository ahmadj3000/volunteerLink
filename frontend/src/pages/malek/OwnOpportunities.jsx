import { useEffect, useState } from 'react'
import { ngoApi } from '../../lib/ngo-api'

export default function OwnOpportunities() {
  const [items, setItems] = useState([])
  const [cursor, setCursor] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  useEffect(() => {
    let active = true
    ngoApi('/ngo/opportunities')
      .then(data => {
        if (!active) return
        setItems(data.items)
        setCursor(data.nextCursor)
      })
      .catch(error => {
        if (active) setError(error.message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [])

  async function load(more = false) {
    setLoading(true)
    setError('')
    try {
      const path = more && cursor
        ? `/ngo/opportunities?cursor=${encodeURIComponent(cursor)}`
        : '/ngo/opportunities'
      const data = await ngoApi(path)
      setItems(previous => {
        const combined = more ? [...previous, ...data.items] : data.items
        return [...new Map(
          combined.map(item => [item.opportunityId, item])
        ).values()]
      })
      setCursor(data.nextCursor)
    } catch (error) {
      setError(error.message)
    } finally {
      setLoading(false)
    }
  }

  async function close(item) {
    if (!window.confirm(
      `Close "${item.title}"? It will no longer appear in Explore opportunities.`
    )) return

    setBusy(true)
    setError('')
    setMessage('')
    try {
      await ngoApi(
        `/ngo/opportunities/${encodeURIComponent(item.opportunityId)}/close`,
        {
          method: 'PATCH',
          body: JSON.stringify({ expectedUpdatedAt: item.updatedAt }),
        }
      )
      setItems(previous => previous.map(post =>
        post.opportunityId === item.opportunityId
          ? { ...post, status: 'closed' }
          : post
      ))
      setMessage(`Closed: ${item.title}`)
    } catch (error) {
      setError(error.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section style={{ borderTop: '1px solid #dce5df', marginTop: 32 }}>
      <h3>Your opportunities</h3>
      <button onClick={() => load()} disabled={loading || busy}>
        Refresh your opportunities
      </button>
      {loading && <p role="status">Loading your posts…</p>}
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {!loading && !error && items.length === 0 && (
        <p>
          {cursor
            ? 'No posts in this page. Choose Load more.'
            : 'You have no posted opportunities yet.'}
        </p>
      )}
      {items.map(item => (
        <article key={item.opportunityId} style={{ marginTop: 24 }}>
          <h4>{item.title}</h4>
          <p>{item.location}</p>
          <p>Starts: {new Date(item.startAt).toLocaleString()}</p>
          <p>Status: <strong>{item.status}</strong></p>
          {item.status === 'open' && (
            <button onClick={() => close(item)} disabled={busy || loading}>
              Close opportunity
            </button>
          )}
        </article>
      ))}
      {cursor && (
        <button onClick={() => load(true)} disabled={loading || busy}>
          Load more
        </button>
      )}
    </section>
  )
}
