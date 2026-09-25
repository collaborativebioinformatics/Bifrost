import assert from 'node:assert/strict'
import test from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import { ResultView } from '../app/result-view.jsx'

function render(result) {
  return renderToStaticMarkup(createElement(ResultView, { result }))
}

for (const [decision, projectionKey, heading] of [
  ['FLAGGED', 'released', /Output withheld pending release/],
  ['REJECTED', 'released_result', /Output rejected by the overseer/],
]) {
  test(`holds total and per-site counts with ${decision.toLowerCase()} output`, () => {
    const html = render({
      decision,
      reasons: ['Small cell requires review'],
      result: {
        coverage: '3/3 sites',
        sites_missing: [],
        n: 42,
        n_per_site: { hunt: 21, gefion: 21 },
        rejected_per_site: { hunt: ['age'] },
        stats: { age: { mean: 48 } },
      },
      [projectionKey]: {
        n: 999,
        n_per_site: { stale: 999 },
        stats: { age: { mean: 999 } },
      },
    })

    assert.match(html, heading)
    assert.doesNotMatch(html, /Records analysed/)
    assert.doesNotMatch(html, /hunt: 21/)
    assert.doesNotMatch(html, /stale: 999/)
    assert.doesNotMatch(html, /mean/)
  })
}

test('renders a completed response without statistics', () => {
  const html = render({ status: 'complete' })

  assert.match(html, /The API returned no released statistics for this run/)
})

test('uses released result counts and statistics over the raw result', () => {
  const html = render({
    decision: 'APPROVED',
    result: {
      coverage: '9/9 sites',
      sites_missing: [],
      n: 99,
      n_per_site: { raw: 99 },
      stats: { age: { mean: 99 } },
    },
    released_result: {
      coverage: '2/2 sites',
      sites_missing: [],
      n: 24,
      n_per_site: { hunt: 12, gefion: 12 },
      stats: { age: { mean: 48 } },
    },
  })

  assert.match(html, /Records analysed/)
  assert.match(html, /24/)
  assert.match(html, /hunt: 12/)
  assert.match(html, /48/)
  assert.doesNotMatch(html, /raw: 99/)
  assert.doesNotMatch(html, />99</)
})
test('maps held disclosure reasons without exposing raw values', () => {
  const html = render({
    decision: 'FLAGGED',
    reasons: [
      'min_sites:1<2',
      'site_suppression:hunt:1',
      'k_anon:age.count=2<5',
      'dominance:age.count:hunt=0.95>0.9',
      'differencing:prev=secret:|11-12|<5',
      'unknown:42',
      'unknown:42',
    ],
    result: {
      n: 2,
      n_per_site: { hunt: 2 },
      rejected_per_site: { hunt: ['age.count:count=2<5'] },
      stats: { age: { count: 2 } },
    },
  })

  for (const explanation of [
    'Insufficient site coverage for release.',
    'A site-level result requires disclosure review.',
    'A result did not meet the minimum cell-size policy.',
    'A result did not meet the dominance policy.',
    'A result did not meet the differencing policy.',
    'Disclosure review required.',
  ]) assert.match(html, new RegExp(explanation.replace(/[.]/g, '\\.')))

  assert.doesNotMatch(html, /min_sites:1<2|count=2|secret|Suppressed or rejected items|hunt: 2/)
})

test('shows rejected items after release', () => {
  const html = render({
    decision: 'APPROVED',
    result: { rejected_per_site: { hunt: ['age.count:count=2<5'] }, stats: {} },
  })

  assert.match(html, /Suppressed or rejected items/)
  assert.match(html, /age.count:count=2&lt;5/)
})
