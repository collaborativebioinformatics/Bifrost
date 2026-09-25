// The overseer's approve/reject handler, given the page's state setters. After the decision it
// re-reads the displayed run if that is the run decided on: a flagged run has stopped polling,
// so without the re-read the page keeps showing FLAGGED.
export async function decideAndRefresh(api, { item, decision, runId, run }, { setPending, setNotice, setRun }) {
  const specHash = item.spec_hash || item.id
  try {
    await api(`/overseer/${specHash}/${decision}`, { method: 'POST', body: JSON.stringify({ note: '', by: 'researcher' }) })
  } catch (error) {
    setNotice(error.message)
    return
  }
  setPending((items) => items.filter((entry) => (entry.spec_hash || entry.id) !== specHash))
  const notice = `Result ${decision === 'approve' ? 'approved' : 'rejected'}.`
  setNotice(notice)
  if (!runId || run?.spec_hash !== specHash) return
  try {
    setRun(await api(`/run/${runId}`))
  } catch (error) {
    // the decision is recorded; a failed re-read must not hide that
    setNotice(`${notice} The run could not be reloaded: ${error.message}`)
  }
}
