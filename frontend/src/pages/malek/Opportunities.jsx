import { useState } from 'react'
import { fetchAuthSession } from 'aws-amplify/auth'

export default function Opportunities() {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  async function testBackend() {
    setBusy(true)
    setResult(null)
    setError('')

    try {
      const session = await fetchAuthSession()
      const token = session.tokens?.accessToken?.toString()

      if (!token) {
        throw new Error('Please sign in first.')
      }

      const response = await fetch('/api/ngo/me', {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      })

      if (!response.headers.get('content-type')?.includes('application/json')) {
        throw new Error(
          'Unexpected response. Check that Vite and the Docker backend are running.'
        )
      }

      const data = await response.json()

      if (!response.ok) {
        throw new Error(
          typeof data.detail === 'string'
            ? data.detail
            : `Backend returned HTTP ${response.status}.`
        )
      }

      setResult(data)
    } catch (error) {
      setError(error.message || 'Could not reach the backend.')
    } finally {
      setBusy(false)
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

      <div className="empty-state">
        <h3>Opportunities will appear here</h3>
        <p>The opportunities API has not been connected yet.</p>
      </div>

      {import.meta.env.DEV && (
        <div>
          <h3>Local backend connection test</h3>
          <button type="button" onClick={testBackend} disabled={busy}>
            {busy ? 'Checking…' : 'Test authenticated backend'}
          </button>

          {error && <p role="alert">{error}</p>}

          {result && (
            <div role="status">
              <p>Success: the backend verified your Cognito access token.</p>
              <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
                {JSON.stringify(result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
