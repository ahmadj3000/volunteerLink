import { fetchAuthSession } from 'aws-amplify/auth'

export async function volunteerApi(path, options = {}) {
  const base = import.meta.env.DEV
    ? '/api/volunteer'
    : import.meta.env.VITE_VOLUNTEER_API_URL

  if (!base) {
    throw new Error('The volunteer service is not configured.')
  }

  const session = await fetchAuthSession()
  const token = session.tokens?.accessToken?.toString()

  if (!token) {
    throw new Error('Please sign in again.')
  }

  const headers = new Headers(options.headers)
  headers.set('Authorization', `Bearer ${token}`)

  if (options.body) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(
    `${base.replace(/\/$/, '')}${path}`,
    { ...options, headers },
  )

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    const error = new Error(
      typeof data?.detail === 'string'
        ? data.detail
        : `Request failed (HTTP ${response.status}). Please try again.`,
    )
    error.status = response.status
    throw error
  }

  if (!data || typeof data !== 'object') {
    throw new Error('The volunteer service returned an unexpected response.')
  }

  return data
}