import { fetchAuthSession } from 'aws-amplify/auth'

export async function ngoApi(path, options = {}) {
  const base = import.meta.env.DEV
    ? '/api/ngo'
    : import.meta.env.VITE_NGO_API_URL

  if (!base) {
    throw new Error('The hosted NGO API is not configured yet. Use the local website for testing.')
  }

  const session = await fetchAuthSession()
  const token = session.tokens?.accessToken?.toString()
  if (!token) throw new Error('Please sign in again.')

  const response = await fetch(`${base.replace(/\/$/, '')}${path}`, {
    ...options,
    headers: {
      ...options.headers,
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      Authorization: `Bearer ${token}`,
    },
  })

  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('Unexpected response. Check that the Docker backend is running.')
  }

  const data = await response.json()
  if (!response.ok) {
    throw new Error(
      typeof data.detail === 'string'
        ? data.detail
        : `Request failed (HTTP ${response.status}). Check your form fields.`
    )
  }

  return data
}
