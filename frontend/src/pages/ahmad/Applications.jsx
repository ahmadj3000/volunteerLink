import { useEffect, useRef, useState } from 'react'
import { volunteerApi } from '../../lib/volunteer-api'

function displayDate(value) {
  return value ? new Date(value).toLocaleString() : 'Not available'
}

export default function Applications() {
  const [items, setItems] = useState([])
  const [cursor, setCursor] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const requestVersion = useRef(0)
  const busy = useRef(false)

  useEffect(() => {
    const version = ++requestVersion.current
    busy.current = true

    volunteerApi('/me/applications')
      .then((data) => {
        if (version !== requestVersion.current) return
        if (!Array.isArray(data.items)) {
          throw new Error('Could not read your applications.')
        }
        setItems(data.items)
        setCursor(data.nextCursor ?? null)
      })
      .catch((error) => {
        if (version === requestVersion.current) {
          setError(error.message)
        }
      })
      .finally(() => {
        if (version === requestVersion.current) {
          busy.current = false
          setLoading(false)
        }
      })

    return () => {
      requestVersion.current += 1
      busy.current = false
    }
  }, [])

  async function load(more = false) {
    if (busy.current) return
    busy.current = true

    const version = ++requestVersion.current
    setLoading(true)
    setError('')

    try {
      const path = more && cursor
        ? `/me/applications?cursor=${encodeURIComponent(cursor)}`
        : '/me/applications'

      const data = await volunteerApi(path)
      if (version !== requestVersion.current) return

      if (!Array.isArray(data.items)) {
        throw new Error('Could not read your applications.')
      }

      setItems((previous) => {
        const combined = more ? [...previous, ...data.items] : data.items
        return [...new Map(
          combined.map((item) => [item.applicationId, item]),
        ).values()]
      })
      setCursor(data.nextCursor ?? null)
    } catch (error) {
      if (version === requestVersion.current) {
        setError(error.message)
      }
    } finally {
      if (version === requestVersion.current) {
        busy.current = false
        setLoading(false)
      }
    }
  }

  return (
    <section className="panel">
      <span className="eyebrow">YOUR VOLUNTEERING JOURNEY</span>
      <h2>My applications</h2>
      <p>Follow the progress of your volunteering applications.</p>

      <button
        type="button"
        onClick={() => load()}
        disabled={loading}
      >
        Refresh applications
      </button>

      {loading && <p role="status">Loading applications…</p>}
      {error && <p role="alert">{error}</p>}

      {!loading && !error && items.length === 0 && (
        <div className="empty-state">
          <h3>No applications on this page</h3>
          <p>
            {cursor
              ? 'Choose Load more to check the next page.'
              : 'Explore opportunities and apply to one that interests you.'}
          </p>
        </div>
      )}

      {items.map((item) => (
        <article
          key={item.applicationId}
          style={{
            borderTop: '1px solid #dce5df',
            marginTop: 24,
            paddingTop: 16,
          }}
        >
          <h3>{item.opportunityTitle}</h3>
          <p><strong>Status: {item.status}</strong></p>
          <p>Opportunity starts: {displayDate(item.opportunityStartAt)}</p>
          <p>Applied: {displayDate(item.appliedAt)}</p>

          {item.message && (
            <p style={{ whiteSpace: 'pre-wrap' }}>
              Your message: {item.message}
            </p>
          )}

          {item.decidedAt && (
            <p>Decision recorded: {displayDate(item.decidedAt)}</p>
          )}
        </article>
      ))}

      {cursor && (
        <button
          type="button"
          onClick={() => load(true)}
          disabled={loading}
        >
          Load more
        </button>
      )}
    </section>
  )
}