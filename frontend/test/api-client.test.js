import assert from 'node:assert/strict'
import test from 'node:test'
import { api } from '../app/api-client.js'

function respond(t, status, body) {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    const json = async () => {
      if (body === undefined) throw new SyntaxError('no JSON body')
      return body
    }
    return { ok: status >= 200 && status < 300, status, json }
  })
  return requests
}

test('a successful request goes through the proxy as JSON and returns the body', async (t) => {
  const requests = respond(t, 200, { items: [] })

  assert.deepEqual(await api('/overseer', { method: 'POST', body: '{}' }), { items: [] })
  assert.equal(requests[0].url, '/api/overseer')
  assert.equal(requests[0].options.method, 'POST')
  assert.equal(requests[0].options.headers['Content-Type'], 'application/json')
})

test('a failed request throws with the HTTP status and the API detail', async (t) => {
  respond(t, 409, { detail: 'stale revision: the queue changed since it was listed; reload it and review again' })

  await assert.rejects(api('/overseer/a1b2c3/approve', { method: 'POST', body: '{}' }), (error) => {
    assert.equal(error.status, 409)
    assert.match(error.message, /^stale revision/)
    return true
  })
})

test('a failed request without a JSON body still carries its status', async (t) => {
  respond(t, 502)

  await assert.rejects(api('/run/x'), { status: 502, message: 'API returned 502' })
})
