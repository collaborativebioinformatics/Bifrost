export const API_BASE = '/api'

// JSON in and out through the Next.js proxy. A failed request throws an Error that carries the
// HTTP status, so a caller can tell a 409 (e.g. a stale overseer revision) from other failures.
export async function api(path, options) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) },
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(body.detail || body.error || body.message || `API returned ${response.status}`)
    error.status = response.status
    throw error
  }
  return body
}
