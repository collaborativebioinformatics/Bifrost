import assert from 'node:assert/strict'
import test from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import { api as realApi } from '../app/api-client.js'
import { decideAndRefresh } from '../app/decision.js'
import { ResultView } from '../app/result-view.jsx'

const SPEC = 'a1b2c3'
const STALE = 'The queue changed since it was loaded (a newer run replaced this result, or it was decided already), '
  + 'so this request decided nothing.'
// GET /overseer item and GET /run/{id} shapes (server/schemas.py OverseerItem, RunStatus, ReleasedResult)
const queued = { id: SPEC, spec_hash: SPEC, revision: 'rev-1', queued: '2026-09-25T10:00:01Z', reasons: ['k_anon:x'] }
const queuedToo = { id: 'queued-too', spec_hash: 'queued-too', revision: 'rev-9', queued: '2026-09-25T09:00:00Z', reasons: [] }
const flaggedRun = {
  run_id: 'run-1', status: 'flagged', submitted: '2026-09-25T10:00:00Z', spec_hash: SPEC, revision: 'rev-1',
  decision: 'FLAGGED', reasons: ['k_anon'],
}
const releasedResult = {
  spec_hash: SPEC,
  coverage: '3/3 sites',
  sites_expected: ['brev', 'gefion', 'hunt'],
  sites_reported: ['brev', 'gefion', 'hunt'],
  sites_missing: [],
  n: 60,
  n_per_site: { hunt: 20, gefion: 20, brev: 20 },
  stats: { age: { mean: 48 } },
}

// Answers like server/api.py: a decision must name the queued revision. One queued before but not
// now (a rerun replaced it, or it was decided already) is a 409 that changes nothing; one never
// queued for a spec that is not queued is a 404. A decision updates the flagged run of that
// revision (_decision_update). Errors carry .status, as api-client.js sets it.
// holdReload: a promise GET /run/{id} waits on, so a test can act while the reload is in flight
function fakeApi({ failReload = false, failQueue = false, holdReload = null } = {}) {
  const calls = []
  const bodies = []
  let run = flaggedRun
  let queue = [queued, queuedToo]
  const queuedOnce = new Set(queue.map((entry) => entry.revision))
  const fail = (status, message) => Object.assign(new Error(message), { status })
  async function api(path, options = {}) {
    calls.push(`${options.method || 'GET'} ${path}`)
    if (options.body) bodies.push(JSON.parse(options.body))
    if (path === '/overseer') {
      if (failQueue) throw fail(502, 'API returned 502')
      return { items: queue }
    }
    const decided = path.match(/^\/overseer\/([^/]+)\/(approve|reject)$/)
    if (decided) {
      const item = queue.find((entry) => entry.spec_hash === decided[1])
      const { revision } = JSON.parse(options.body)
      if (!item && !queuedOnce.has(revision)) throw fail(404, 'not in queue')
      if (revision !== item?.revision) throw fail(409, 'stale revision')
      queue = queue.filter((entry) => entry !== item)
      const approve = decided[2] === 'approve'
      if (run.status === 'flagged' && run.revision === revision) {
        run = approve
          ? { ...run, status: 'completed', decision: 'APPROVED', result: releasedResult }
          : { ...run, status: 'rejected', decision: 'REJECTED' }
      }
      return { spec_hash: item.spec_hash, revision, decision: approve ? 'RELEASED' : 'REJECTED', released: approve }
    }
    if (path === `/run/${run.run_id}` && !failReload) return holdReload ? holdReload.then(() => run) : run
    throw fail(502, 'API returned 502')
  }
  // a rerun of the spec replaces its queued result under a fresh revision
  function rerun() {
    queue = queue.map((entry) => (entry.spec_hash === SPEC ? { ...entry, revision: 'rev-2' } : entry))
    queuedOnce.add('rev-2')
  }
  return { api, calls, bodies, rerun }
}

// The page's state, driven through the same setters page.jsx passes in.
function fakePage(run) {
  const page = { pending: [queued, queuedToo], notices: [], run }
  const setters = {
    setPending: (next) => { page.pending = typeof next === 'function' ? next(page.pending) : next },
    setNotice: (notice) => { page.notices.push(notice) },
    setRun: (next) => { page.run = typeof next === 'function' ? next(page.run) : next },  // like React's updater form
  }
  return { page, setters }
}

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function render(run) {
  return renderToStaticMarkup(createElement(ResultView, { result: run }))
}

test('approving the displayed run reloads it and shows the released result', async () => {
  const { api, calls, bodies } = fakeApi()
  const { page, setters } = fakePage(flaggedRun)
  assert.match(render(page.run), /Output withheld pending release/)

  await decideAndRefresh(api, { item: queued, decision: 'approve', runId: 'run-1', run: page.run }, setters)

  assert.deepEqual(calls, [`POST /overseer/${SPEC}/approve`, 'GET /run/run-1'])
  assert.deepEqual(bodies, [{ revision: 'rev-1', note: '', by: 'researcher' }])
  assert.deepEqual(page.pending, [queuedToo])
  assert.deepEqual(page.notices, ['Result approved.'])
  const html = render(page.run)
  assert.match(html, /APPROVED/)
  assert.match(html, /Records analysed/)
  assert.match(html, /hunt: 20/)
  assert.match(html, />48</)
  assert.doesNotMatch(html, /withheld/)
})

test('rejecting the displayed run reloads it and says it was rejected', async () => {
  const { api, calls } = fakeApi()
  const { page, setters } = fakePage(flaggedRun)

  await decideAndRefresh(api, { item: queued, decision: 'reject', runId: 'run-1', run: page.run }, setters)

  assert.deepEqual(calls, [`POST /overseer/${SPEC}/reject`, 'GET /run/run-1'])
  assert.deepEqual(page.pending, [queuedToo])
  assert.deepEqual(page.notices, ['Result rejected.'])
  const html = render(page.run)
  assert.match(html, /REJECTED/)
  assert.match(html, /Output rejected by the overseer/)
  assert.match(html, /A result did not meet the minimum cell-size policy/)
  assert.doesNotMatch(html, /pending release|Records analysed/)
})

test('a decision on a result a rerun replaced changes nothing and reloads the queue', async () => {
  const { api, calls, bodies, rerun } = fakeApi()
  const { page, setters } = fakePage(flaggedRun)
  rerun()  // after the overseer loaded the queue: the page still shows rev-1

  await decideAndRefresh(api, { item: queued, decision: 'approve', runId: 'run-1', run: flaggedRun }, setters)

  assert.deepEqual(calls, [`POST /overseer/${SPEC}/approve`, 'GET /overseer'])
  assert.equal(bodies[0].revision, 'rev-1')
  assert.deepEqual(page.pending.map((item) => item.revision), ['rev-2', 'rev-9'])
  assert.equal(page.run, flaggedRun)
  assert.deepEqual(page.notices,
    [`${STALE} The queue has been reloaded.`])

  // the reloaded row carries the current revision, and deciding it goes through; the displayed
  // run queued rev-1, which nobody decided, so it is not re-read
  await decideAndRefresh(api, { item: page.pending[0], decision: 'approve', runId: 'run-1', run: flaggedRun }, setters)
  assert.deepEqual(calls.slice(2), [`POST /overseer/${SPEC}/approve`])
  assert.equal(bodies.at(-1).revision, 'rev-2')
  assert.equal(page.notices.at(-1), 'Result approved.')
  assert.deepEqual(page.pending, [queuedToo])
  assert.equal(page.run, flaggedRun)
})

test('a stale decision whose queue reload fails says so', async () => {
  const { api, calls, rerun } = fakeApi({ failQueue: true })
  const { page, setters } = fakePage(flaggedRun)
  rerun()

  await decideAndRefresh(api, { item: queued, decision: 'reject', runId: 'run-1', run: flaggedRun }, setters)

  assert.deepEqual(calls, [`POST /overseer/${SPEC}/reject`, 'GET /overseer'])
  assert.deepEqual(page.pending, [queued, queuedToo])
  assert.deepEqual(page.notices,
    [`${STALE} The queue could not be reloaded: API returned 502`])
})

test('deciding an item that was decided already reloads the queue and says so', async () => {
  const { api, calls } = fakeApi()
  const { page, setters } = fakePage(flaggedRun)
  await decideAndRefresh(api, { item: queued, decision: 'approve', runId: '', run: null }, setters)

  // a second click on the same row, or another overseer's list loaded before the first decision
  await decideAndRefresh(api, { item: queued, decision: 'reject', runId: '', run: null }, setters)

  assert.deepEqual(calls, [`POST /overseer/${SPEC}/approve`, `POST /overseer/${SPEC}/reject`, 'GET /overseer'])
  assert.deepEqual(page.notices, ['Result approved.', `${STALE} The queue has been reloaded.`])
  assert.deepEqual(page.pending, [queuedToo])
})

test('a 409 from the real API client reloads the queue', async (t) => {
  const current = { ...queued, revision: 'rev-2' }
  t.mock.method(globalThis, 'fetch', async (url, options) => (options.method === 'POST'
    ? { ok: false, status: 409, json: async () => ({ detail: 'stale revision: the queue changed since it was listed' }) }
    : { ok: true, status: 200, json: async () => ({ items: [current] }) }))
  const { page, setters } = fakePage(flaggedRun)

  await decideAndRefresh(realApi, { item: queued, decision: 'approve', runId: 'run-1', run: flaggedRun }, setters)

  assert.deepEqual(globalThis.fetch.mock.calls.map((call) => call.arguments[0]), [`/api/overseer/${SPEC}/approve`, '/api/overseer'])
  assert.deepEqual(page.pending, [current])
  assert.deepEqual(page.notices, [`${STALE} The queue has been reloaded.`])
  assert.equal(page.run, flaggedRun)
})

test('a decision on another revision leaves the displayed run alone', async () => {
  const { api, calls } = fakeApi()
  const other = { ...flaggedRun, revision: 'rev-0' }  // an earlier run of the same spec
  const { page, setters } = fakePage(other)

  await decideAndRefresh(api, { item: queued, decision: 'approve', runId: 'run-1', run: other }, setters)

  assert.deepEqual(calls, [`POST /overseer/${SPEC}/approve`])
  assert.equal(page.run, other)
  assert.deepEqual(page.notices, ['Result approved.'])
})

test('nothing is reloaded without a run id', async () => {
  const { api, calls } = fakeApi()
  const { page, setters } = fakePage(flaggedRun)

  await decideAndRefresh(api, { item: queued, decision: 'reject', runId: '', run: flaggedRun }, setters)

  assert.deepEqual(calls, [`POST /overseer/${SPEC}/reject`])
  assert.equal(page.run, flaggedRun)
})

test('a failed decision is shown and changes nothing else', async () => {
  const { api, calls } = fakeApi()
  const gone = { ...flaggedRun, spec_hash: 'gone' }
  const { page, setters } = fakePage(gone)

  await decideAndRefresh(api, { item: { ...queued, id: 'gone', spec_hash: 'gone' }, decision: 'approve', runId: 'run-1', run: gone }, setters)

  assert.deepEqual(calls, ['POST /overseer/gone/approve'])
  assert.deepEqual(page.notices, ['not in queue'])
  assert.deepEqual(page.pending, [queued, queuedToo])
  assert.equal(page.run, gone)
})

test('a failed reload still reports the recorded decision', async () => {
  const { api, calls } = fakeApi({ failReload: true })
  const { page, setters } = fakePage(flaggedRun)

  await decideAndRefresh(api, { item: queued, decision: 'reject', runId: 'run-1', run: flaggedRun }, setters)

  assert.deepEqual(calls, [`POST /overseer/${SPEC}/reject`, 'GET /run/run-1'])
  assert.deepEqual(page.pending, [queuedToo])
  assert.deepEqual(page.notices, ['Result rejected.', 'Result rejected. The run could not be reloaded: API returned 502'])
  assert.equal(page.run, flaggedRun)
})

const newerRun = { run_id: 'run-2', status: 'completed', submitted: '2026-09-25T10:05:00Z', spec_hash: 'd4e5f6', decision: 'OK' }
for (const [label, newer] of [['still loading', null], ['already shown', newerRun]]) {
  test(`a run submitted during the reload is not replaced (${label})`, async () => {
    const reload = deferred()
    const { api, calls } = fakeApi({ holdReload: reload.promise })
    const { page, setters } = fakePage(flaggedRun)

    const deciding = decideAndRefresh(api, { item: queued, decision: 'approve', runId: 'run-1', run: flaggedRun }, setters)
    await new Promise((resume) => setImmediate(resume))
    assert.deepEqual(calls, [`POST /overseer/${SPEC}/approve`, 'GET /run/run-1'])
    // what submitRun, then the new run's first poll, do meanwhile (page.jsx)
    setters.setRun(null)
    if (newer) setters.setRun(newer)
    reload.resolve()
    await deciding

    assert.equal(page.run, newer)
    assert.deepEqual(page.notices, ['Result approved.'])
  })
}
