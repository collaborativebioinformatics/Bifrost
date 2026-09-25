import assert from 'node:assert/strict'
import test from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import { decideAndRefresh } from '../app/decision.js'
import { ResultView } from '../app/result-view.jsx'

const SPEC = 'a1b2c3'
// GET /run/{id} shapes (server/schemas.py RunStatus, ReleasedResult)
const flaggedRun = {
  run_id: 'run-1', status: 'flagged', submitted: '2026-09-25T10:00:00Z', spec_hash: SPEC,
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

// Answers like server/api.py: a decision on a queued spec updates the flagged run that
// GET /run/{id} returns (_decision_update); any other spec is a 404, which the page's api() throws.
// holdReload: a promise GET /run/{id} waits on, so a test can act while the reload is in flight
function fakeApi({ failReload = false, holdReload = null } = {}) {
  const calls = []
  const bodies = []
  let run = flaggedRun
  async function api(path, options = {}) {
    calls.push(`${options.method || 'GET'} ${path}`)
    if (options.body) bodies.push(JSON.parse(options.body))
    const decided = path.match(/^\/overseer\/([^/]+)\/(approve|reject)$/)
    if (decided) {
      if (decided[1] !== SPEC) throw new Error('not in queue')
      const approve = decided[2] === 'approve'
      if (run.status === 'flagged') {
        run = approve
          ? { ...run, status: 'completed', decision: 'APPROVED', result: releasedResult }
          : { ...run, status: 'rejected', decision: 'REJECTED' }
      }
      return { spec_hash: SPEC, decision: approve ? 'RELEASED' : 'REJECTED', released: approve }
    }
    if (path === `/run/${run.run_id}` && !failReload) return holdReload ? holdReload.then(() => run) : run
    throw new Error('API returned 502')
  }
  return { api, calls, bodies }
}

// The page's state, driven through the same setters page.jsx passes in.
function fakePage(run) {
  const page = { pending: [{ spec_hash: SPEC }, { spec_hash: 'queued-too' }], notices: [], run }
  const setters = {
    setPending: (update) => { page.pending = update(page.pending) },
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

  await decideAndRefresh(api, { item: { spec_hash: SPEC }, decision: 'approve', runId: 'run-1', run: page.run }, setters)

  assert.deepEqual(calls, [`POST /overseer/${SPEC}/approve`, 'GET /run/run-1'])
  assert.deepEqual(bodies, [{ note: '', by: 'researcher' }])
  assert.deepEqual(page.pending, [{ spec_hash: 'queued-too' }])
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

  await decideAndRefresh(api, { item: { spec_hash: SPEC }, decision: 'reject', runId: 'run-1', run: page.run }, setters)

  assert.deepEqual(calls, [`POST /overseer/${SPEC}/reject`, 'GET /run/run-1'])
  assert.deepEqual(page.pending, [{ spec_hash: 'queued-too' }])
  assert.deepEqual(page.notices, ['Result rejected.'])
  const html = render(page.run)
  assert.match(html, /REJECTED/)
  assert.match(html, /Output rejected by the overseer/)
  assert.match(html, /A result did not meet the minimum cell-size policy/)
  assert.doesNotMatch(html, /pending release|Records analysed/)
})

test('a decision on another spec leaves the displayed run alone', async () => {
  const { api, calls } = fakeApi()
  const other = { ...flaggedRun, spec_hash: 'other' }
  const { page, setters } = fakePage(other)

  await decideAndRefresh(api, { item: { spec_hash: SPEC }, decision: 'approve', runId: 'run-1', run: other }, setters)

  assert.deepEqual(calls, [`POST /overseer/${SPEC}/approve`])
  assert.equal(page.run, other)
  assert.deepEqual(page.notices, ['Result approved.'])
})

test('nothing is reloaded without a run id', async () => {
  const { api, calls } = fakeApi()
  const { page, setters } = fakePage(flaggedRun)

  await decideAndRefresh(api, { item: { spec_hash: SPEC }, decision: 'reject', runId: '', run: flaggedRun }, setters)

  assert.deepEqual(calls, [`POST /overseer/${SPEC}/reject`])
  assert.equal(page.run, flaggedRun)
})

test('a failed decision is shown and changes nothing else', async () => {
  const { api, calls } = fakeApi()
  const gone = { ...flaggedRun, spec_hash: 'gone' }
  const { page, setters } = fakePage(gone)

  await decideAndRefresh(api, { item: { spec_hash: 'gone' }, decision: 'approve', runId: 'run-1', run: gone }, setters)

  assert.deepEqual(calls, ['POST /overseer/gone/approve'])
  assert.deepEqual(page.notices, ['not in queue'])
  assert.deepEqual(page.pending, [{ spec_hash: SPEC }, { spec_hash: 'queued-too' }])
  assert.equal(page.run, gone)
})

test('a failed reload still reports the recorded decision', async () => {
  const { api, calls } = fakeApi({ failReload: true })
  const { page, setters } = fakePage(flaggedRun)

  await decideAndRefresh(api, { item: { spec_hash: SPEC }, decision: 'reject', runId: 'run-1', run: flaggedRun }, setters)

  assert.deepEqual(calls, [`POST /overseer/${SPEC}/reject`, 'GET /run/run-1'])
  assert.deepEqual(page.pending, [{ spec_hash: 'queued-too' }])
  assert.deepEqual(page.notices, ['Result rejected.', 'Result rejected. The run could not be reloaded: API returned 502'])
  assert.equal(page.run, flaggedRun)
})

const newerRun = { run_id: 'run-2', status: 'completed', submitted: '2026-09-25T10:05:00Z', spec_hash: 'd4e5f6', decision: 'OK' }
for (const [label, newer] of [['still loading', null], ['already shown', newerRun]]) {
  test(`a run submitted during the reload is not replaced (${label})`, async () => {
    const reload = deferred()
    const { api, calls } = fakeApi({ holdReload: reload.promise })
    const { page, setters } = fakePage(flaggedRun)

    const deciding = decideAndRefresh(api, { item: { spec_hash: SPEC }, decision: 'approve', runId: 'run-1', run: flaggedRun }, setters)
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
