import { useEffect, useState } from 'react'
import { ngoApi } from '../../lib/ngo-api'

export default function Opportunities() {
  const [items, setItems] = useState([])
  const [cursor, setCursor] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [testBusy, setTestBusy] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [testError, setTestError] = useState('')

  useEffect(() => {
    let active = true

    ngoApi('/opportunities')
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
        ? `/opportunities?cursor=${encodeURIComponent(cursor)}`
        : '/opportunities'
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

  async function testBackend(endpoint) {
    setTestBusy(true)
    setTestResult(null)
    setTestError('')

    try {
      setTestResult(await ngoApi(endpoint))
    } catch (error) {
      setTestError(error.message)
    } finally {
      setTestBusy(false)
    }
  }

  return (
    <section className="panel">
      <span className="eyebrow">FIND YOUR NEXT WAY TO HELP</span>
      <h2>Opportunities across Lebanon</h2>
      <p>
        Discover opportunities that connect your time and skills
        with communities that need them.
      </p>

      <button type="button" onClick={() => load()} disabled={loading}>
        Refresh opportunities
      </button>

      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">Loading opportunities…</p>}

      {!loading && !error && items.length === 0 && (
        <div className="empty-state">
          <h3>No open opportunities in this page</h3>
          <p>
            {cursor
              ? 'Choose Load more to check the next page.'
              : 'Check back when an NGO posts an opportunity.'}
          </p>
        </div>
      )}

      {items.map(item => (
        <article
          key={item.opportunityId}
          style={{ borderTop: '1px solid #dce5df', marginTop: 24, paddingTop: 16 }}
        >
          {item.isCrisis && <strong>Urgent crisis response</strong>}
          <h3>{item.title}</h3>
          <p style={{ whiteSpace: 'pre-wrap' }}>{item.description}</p>
          <p>Location: {item.location}</p>
          <p>
            Starts: {new Date(item.startAt).toLocaleString()}
            <br />
            Ends: {new Date(item.endAt).toLocaleString()}
          </p>
          <p>Volunteer capacity: {item.capacity}</p>
          <p>
            Required skills: {item.skills?.length
              ? item.skills.join(', ')
              : 'No specific skills required'}
          </p>
        </article>
      ))}

      {cursor && (
        <button type="button" onClick={() => load(true)} disabled={loading}>
          Load more
        </button>
      )}

      {import.meta.env.DEV && (
        <div style={{ marginTop: 32 }}>
          <h3>Local backend connection test</h3>
          <button onClick={() => testBackend('/me')} disabled={testBusy}>
            Test authenticated backend
          </button>
          <button onClick={() => testBackend('/ngo/access')} disabled={testBusy}>
            Test NGO access
          </button>
          <button onClick={() => testBackend('/admin/access')} disabled={testBusy}>
            Test admin access
          </button>
          {testError && <p role="alert">{testError}</p>}
          {testResult && (
            <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
              {JSON.stringify(testResult, null, 2)}
            </pre>
          )}
        </div>
      )}
    </section>
  )
}
