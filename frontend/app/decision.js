// The overseer's approve/reject handler, given the page's state setters. The decision names the
// queue revision the overseer reviewed; if the queue has moved on (409: a newer run replaced the
// result, or it was decided already), nothing was decided and the queue is reloaded for another
// review. After a decision it re-reads the displayed run if that run queued the decided revision
// (the only run the server updates): a flagged run has stopped polling, so without the re-read
// the page keeps showing FLAGGED.
export async function decideAndRefresh(api, { item, decision, runId, run }, { setPending, setNotice, setRun }) {
  const specHash = item.spec_hash || item.id
  try {
    await api(`/overseer/${specHash}/${decision}`, {
      method: 'POST',
      body: JSON.stringify({ revision: item.revision, note: '', by: 'researcher' }),
    })
  } catch (error) {
    if (error.status !== 409) {
      setNotice(error.message)
      return
    }
    const stale = 'The queue changed since it was loaded (a newer run replaced this result, or it was decided '
      + 'already), so this request decided nothing.'
    try {
      setPending((await api('/overseer')).items || [])
      setNotice(`${stale} The queue has been reloaded.`)
    } catch (reloadError) {
      setNotice(`${stale} The queue could not be reloaded: ${reloadError.message}`)
    }
    return
  }
  setPending((items) => items.filter((entry) => (entry.spec_hash || entry.id) !== specHash))
  const notice = `Result ${decision === 'approve' ? 'approved' : 'rejected'}.`
  setNotice(notice)
  if (!runId || run?.revision !== item.revision) return
  try {
    const reloaded = await api(`/run/${runId}`)
    // a run submitted while the reload was in flight owns the view now; keep it
    setRun((current) => (current?.run_id === runId ? reloaded : current))
  } catch (error) {
    // the decision is recorded; a failed re-read must not hide that
    setNotice(`${notice} The run could not be reloaded: ${error.message}`)
  }
}
