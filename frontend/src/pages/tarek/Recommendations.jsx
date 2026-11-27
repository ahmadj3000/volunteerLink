import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { volunteerApi } from '../../lib/volunteer-api'
import ApplyButton from '../ahmad/ApplyButton'

// GET /me/recommendations -> { items: [{opportunityId, title, location, startAt, score}],
//                              ranked: bool, reason: null | 'profile_missing' | 'matching_unavailable' }
// The backend takes the volunteer from the access token; this page never sends an id.

const LIMIT = 10

function MatchScore({ score }) {
  if (typeof score !== 'number') return null
  // Cosine similarity: closer to 1 = closer in meaning. Not a probability.
  const width = `${Math.round(Math.max(0, Math.min(1, score)) * 100)}%`
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '8px 0' }}>
      <span className="badge">Match score {score.toFixed(2)}</span>
      <span
        aria-hidden="true"
        style={{ flex: '0 0 120px', height: 6, background: '#eaf2eb', borderRadius: 3, overflow: 'hidden' }}
      >
        <span style={{ display: 'block', width, height: '100%', background: '#24664e' }} />
      </span>
    </div>
  )
}

function FallbackNotice({ reason }) {
  if (reason === 'profile_missing') {
    return (
      <p className="notice">
        Add your skills and availability on <Link to="/profile">My profile</Link> to get
        personal matches. Until then, here are the soonest open opportunities.
      </p>
    )
  }
  if (reason === 'matching_unavailable') {
    return (
      <p className="notice">
        Personal matching is unavailable right now, so these are the soonest open
        opportunities. Try refreshing in a minute.
      </p>
    )
  }
  return null
}

export default function Recommendations() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async (isActive = () => true) => {
    setLoading(true)
    setError('')
    try {
      const result = await volunteerApi(`/me/recommendations?limit=${LIMIT}`)
      if (!Array.isArray(result.items)) {
        throw new Error('The volunteer service returned an unexpected response.')
      }
      if (isActive()) setData(result)
    } catch (err) {
      if (isActive()) setError(err.message)
    } finally {
      if (isActive()) setLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    void load(() => active)
    return () => {
      active = false
    }
  }, [load])

  const items = data?.items ?? []

  return (
    <section className="panel">
      <span className="eyebrow">MAKE YOUR SKILLS COUNT</span>
      <h2>Recommended for you</h2>
      <p>
        Open opportunities ranked by how closely they match your skills,
        availability, location and bio.
      </p>

      <button type="button" onClick={() => load()} disabled={loading}>
        Refresh recommendations
      </button>

      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">Finding your matches…</p>}

      {!loading && data && <FallbackNotice reason={data.reason} />}

      {!loading && !error && data && items.length === 0 && (
        <div className="empty-state">
          <h3>No open opportunities right now</h3>
          <p>Check back when an NGO posts a new opportunity.</p>
        </div>
      )}

      {items.map((item, index) => (
        <article
          key={item.opportunityId}
          style={{ borderTop: '1px solid #dce5df', marginTop: 24, paddingTop: 16 }}
        >
          {data.ranked && index === 0 && <span className="eyebrow">BEST MATCH</span>}
          <h3>{item.title}</h3>
          <MatchScore score={item.score} />
          <p>Location: {item.location}</p>
          <p>Starts: {new Date(item.startAt).toLocaleString()}</p>
          <ApplyButton opportunity={item} />
        </article>
      ))}
    </section>
  )
}
