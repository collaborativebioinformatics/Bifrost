import { Check, Info, X } from 'lucide-react'

function Stat({ label, value, detail, accent = '' }) {
  return <div className={`stat-card ${accent}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>
}

function heldReason(reason) {
  // the API sends bare codes (server/schemas.py PublicReason); detail after ':' is ignored
  const code = reason.split(":", 1)[0]
  if (code === "min_sites") return "Insufficient site coverage for release."
  if (code === "site_suppression") return "A site-level result requires disclosure review."
  if (code === "k_anon") return "A result did not meet the minimum cell-size policy."
  if (code === "dominance") return "A result did not meet the dominance policy."
  if (code === "differencing") return "A result did not meet the differencing policy."
  return "Disclosure review required."
}

export function ResultView({ result }) {
  const rawStats = result?.result || result || {}
  const decision = result?.decision || result?.check?.decision || (result?.status === 'complete' ? 'RESULT AVAILABLE' : 'RUNNING')
  const released = ['OK', 'RELEASED', 'APPROVED', 'RESULT AVAILABLE'].includes(decision)
  const releasedProjection = result?.released || result?.released_result || {}
  const stats = released ? { ...rawStats, ...releasedProjection } : rawStats
  const releasedStats = stats.stats || {}
  const rows = Object.entries(releasedStats).flatMap(([variable, value]) => Object.entries(value || {}).map(([metric, output]) => ({ variable, metric, output })))
  const rejected = Object.entries(rawStats.rejected_per_site || {}).flatMap(([site, items]) => (items || []).map((item) => ({ site, item })))
  const sites = Object.entries(stats.n_per_site || {})
  const coverage = stats.coverage || `${stats.sites_reported?.length || 0}/${stats.sites_expected?.length || 0} sites`
  const reasons = result?.reasons || result?.check?.reasons || []
  const heldReasons = [...new Set(reasons.map(heldReason))]

  return <>
    <div className="results-heading">
      <div><span className="panel-kicker">Step 3</span><h2>Federated result</h2><p>Spec hash: <strong>{result?.spec_hash || result?.run?.spec_hash || stats.spec_hash || 'pending'}</strong></p></div>
      <span className={`result-status ${!released ? 'danger' : ''}`}>{!released ? <X size={15} /> : <Check size={15} />} {decision}</span>
    </div>
    <div className="results-card">
      {released ? <div className="result-stats">
        <Stat label="Coverage" value={coverage} detail={`${(stats.sites_missing || []).length} missing`} accent="blue-accent" />
        <Stat label="Records analysed" value={stats.n ?? '—'} detail="aggregate only" />
        <Stat label="Disclosure items" value={reasons.length + rejected.length} detail="none rejected" />
      </div> : null}
      {!released ? <div className="held-result">
        <strong>{decision === 'REJECTED' ? 'Output rejected by the overseer.' : 'Output withheld pending release.'}</strong>
        {heldReasons.length ? heldReasons.map((reason) => <span key={reason}><Info size={13} /> {reason}</span>) : <span><Info size={13} /> The disclosure decision has not released statistics for this run.</span>}
      </div> : rows.length ? <div className="table-wrap"><table>
        <thead><tr><th>Variable</th><th>Statistic</th><th>Released value</th></tr></thead>
        <tbody>{rows.map((row, index) => <tr key={`${row.variable}-${row.metric}-${index}`}><td><strong>{row.variable}</strong></td><td>{row.metric}</td><td>{typeof row.output === 'object' ? JSON.stringify(row.output) : String(row.output)}</td></tr>)}</tbody>
      </table></div> : <div className="empty-state"><Info size={18} /> The API returned no released statistics for this run.</div>}
      {released && sites.length ? <div className="site-summary">
        <strong>Per-site coverage</strong>
        {sites.map(([site, count]) => <span key={site}>{site}: {count}</span>)}
        {(stats.sites_missing || []).map((site) => <span className="missing-site" key={site}>{site}: missing</span>)}
      </div> : null}
      {released && rejected.length ? <div className="rejected-list">
        <strong>Suppressed or rejected items</strong>
        {rejected.map((item, index) => <span key={`${item.site}-${index}`}><X size={13} /> {item.site}: {item.item}</span>)}
      </div> : null}
    </div>
  </>
}
