import { useEffect, useRef, useState } from 'react'
import { ngoApi } from '../../lib/ngo-api'

function mergeItems(previous, incoming, key) {
  return [...new Map(
    [...previous, ...incoming].map((item) => [item[key], item]),
  ).values()]
}

function ApplicantList({ opportunityId }) {
  const [items, setItems] = useState([])
  const [cursor, setCursor] = useState(null)
  const [capacity, setCapacity] = useState(0)
  const [acceptedCount, setAcceptedCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [deciding, setDeciding] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const busy = useRef(false)
  const version = useRef(0)

  useEffect(() => {
    let active = true
    busy.current = true

    ngoApi(
      `/ngo/opportunities/${encodeURIComponent(opportunityId)}/applicants`,
    )
      .then((data) => {
        if (!active) return
        setItems(data.items)
        setCursor(data.nextCursor ?? null)
        setCapacity(data.capacity)
        setAcceptedCount(data.acceptedCount)
      })
      .catch((error) => {
        if (active) setError(error.message)
      })
      .finally(() => {
        if (active) {
          busy.current = false
          setLoading(false)
        }
      })

    return () => {
      active = false
      version.current += 1
    }
  }, [opportunityId])

  async function load(more = false) {
    if (busy.current) return
    busy.current = true
    const current = ++version.current
    setLoading(true)
    setError('')
    setNotice('')

    try {
      const query = more && cursor
        ? `?cursor=${encodeURIComponent(cursor)}`
        : ''
      const data = await ngoApi(
        `/ngo/opportunities/${encodeURIComponent(opportunityId)}/applicants${query}`,
      )

      if (current !== version.current) return

      setItems((previous) => more
        ? mergeItems(previous, data.items, 'applicationId')
        : data.items)
      setCursor(data.nextCursor ?? null)
      setCapacity(data.capacity)
      setAcceptedCount(data.acceptedCount)
    } catch (error) {
      if (current === version.current) setError(error.message)
    } finally {
      if (current === version.current) {
        busy.current = false
        setLoading(false)
      }
    }
  }

  async function decide(applicationId, decision) {
    if (busy.current) return
    busy.current = true
    const current = ++version.current
    setDeciding(applicationId)
    setError('')
    setNotice('')

    try {
      const data = await ngoApi(
        `/ngo/applications/${encodeURIComponent(applicationId)}/decision`,
        {
          method: 'POST',
          body: JSON.stringify({ decision }),
        },
      )

      if (current !== version.current) return

      setItems((previous) => previous.map((item) =>
        item.applicationId === applicationId
          ? { ...item, ...data.application }
          : item))

      if (decision === 'accepted') {
        setAcceptedCount((previous) => previous + 1)
      }

      setNotice(
        decision === 'accepted'
          ? 'Applicant accepted.'
          : 'Applicant rejected.',
      )
    } catch (error) {
      if (current === version.current) {
        setError(`${error.message} Refresh applicants before trying again.`)
      }
    } finally {
      if (current === version.current) {
        busy.current = false
        setDeciding('')
      }
    }
  }

  const locked = loading || Boolean(deciding)
  const full = acceptedCount >= capacity

  return (
    <div>
      <button
        type="button"
        onClick={() => load()}
        disabled={locked}
      >
        Refresh applicants
      </button>

      {!loading && (
        <p>Accepted: {acceptedCount} / {capacity}</p>
      )}

      {loading && <p role="status">Loading applicants…</p>}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      {!loading && !error && items.length === 0 && (
        <p>
          {cursor
            ? 'No applicants on this page. Choose Load more.'
            : 'No applications yet for this opportunity.'}
        </p>
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
          <h3>{item.fullName || 'Volunteer'}</h3>
          <p>Location: {item.location || 'Not provided'}</p>
          <p>
            Skills: {item.skills?.length
              ? item.skills.join(', ')
              : 'Not provided'}
          </p>
          <p>
            Availability: {item.availability?.length
              ? item.availability.join(', ')
              : 'Not provided'}
          </p>

          {item.message && (
            <p style={{ whiteSpace: 'pre-wrap' }}>{item.message}</p>
          )}

          <p><strong>Status: {item.status}</strong></p>
          <p>Applied: {new Date(item.appliedAt).toLocaleString()}</p>

          {item.status === 'pending' && (
            <div>
              <button
                type="button"
                disabled={locked || full}
                onClick={() => decide(item.applicationId, 'accepted')}
              >
                {deciding === item.applicationId ? 'Saving…' : 'Accept'}
              </button>

              <button
                type="button"
                className="secondary"
                disabled={locked}
                onClick={() => decide(item.applicationId, 'rejected')}
              >
                Reject
              </button>

              {full && <p>All places are filled.</p>}
            </div>
          )}
        </article>
      ))}

      {cursor && (
        <button
          type="button"
          onClick={() => load(true)}
          disabled={locked}
        >
          Load more applicants
        </button>
      )}
    </div>
  )
}

export default function Applicants() {
  const [opportunities, setOpportunities] = useState([])
  const [cursor, setCursor] = useState(null)
  const [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const busy = useRef(false)

  useEffect(() => {
    let active = true
    busy.current = true

    ngoApi('/ngo/opportunities')
      .then((data) => {
        if (!active) return
        setOpportunities(data.items)
        setCursor(data.nextCursor ?? null)
      })
      .catch((error) => {
        if (active) setError(error.message)
      })
      .finally(() => {
        if (active) {
          busy.current = false
          setLoading(false)
        }
      })

    return () => {
      active = false
    }
  }, [])

  async function loadMore() {
    if (busy.current || !cursor) return
    busy.current = true
    setLoading(true)
    setError('')

    try {
      const data = await ngoApi(
        `/ngo/opportunities?cursor=${encodeURIComponent(cursor)}`,
      )
      setOpportunities((previous) =>
        mergeItems(previous, data.items, 'opportunityId'))
      setCursor(data.nextCursor ?? null)
    } catch (error) {
      setError(error.message)
    } finally {
      busy.current = false
      setLoading(false)
    }
  }

  return (
    <section className="panel">
      <span className="eyebrow">NGO WORKSPACE</span>
      <h2>Review applicants</h2>
      <p>Select one of your opportunities to review its applications.</p>

      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">Loading opportunities…</p>}

      <label>
        Opportunity
        <select
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
          disabled={loading}
          style={{ display: 'block', maxWidth: '100%', marginBottom: 24 }}
        >
          <option value="">Choose an opportunity</option>
          {opportunities.map((item) => (
            <option key={item.opportunityId} value={item.opportunityId}>
              {item.title} ({item.status})
            </option>
          ))}
        </select>
      </label>

      {!loading && !error && opportunities.length === 0 && (
        <p>
          {cursor
            ? 'No opportunities on this page. Choose Load more.'
            : 'Your organization has no opportunities yet.'}
        </p>
      )}

      {cursor && (
        <button type="button" onClick={loadMore} disabled={loading}>
          Load more opportunities
        </button>
      )}

      {selected && (
        <ApplicantList key={selected} opportunityId={selected} />
      )}
    </section>
  )
}