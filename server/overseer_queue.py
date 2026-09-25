"""Overseer queue: flagged results wait here for a human decision.

  python -m server.overseer_queue list
  python -m server.overseer_queue show <spec_hash>
  python -m server.overseer_queue approve <spec_hash> --revision <rev> [--by <name>] [--note "..."]
  python -m server.overseer_queue reject  <spec_hash> --revision <rev> [--by <name>] [--note "..."]

Each queued result gets a fresh revision (shown by list/show). A decision names the
revision the overseer reviewed; if that is no longer the queued one (a rerun of the spec
replaced it, or it was decided already), the decision is refused and nothing changes.

Files (under SERVER_OUT, default server/out/):
  overseer_queue.json   pending items, one per spec hash, each with its revision
  release_log.jsonl     every release decision (auto-OK, approved, rejected), keyed by spec hash
                        (and revision, for queued results and decisions on them)
  <spec_hash>/result.json          merged result of the latest run of that spec
  <spec_hash>/released.json        only while the latest run is OK / approved -- what left the server

One process owns a SERVER_OUT: RELEASE_LOCK serialises this process's check ->
record and decisions, and queue writes are atomic, but nothing coordinates two
processes (API, FLARE server, this CLI) writing the same directory.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path

from server.schemas import MergedResult, ReleasedResult

ROOT = Path(__file__).resolve().parent.parent
# Held across disclosure check -> record (the check reads the release log that record
# and decide append to) and around every decision. Re-entrant: record/decide take it too.
RELEASE_LOCK = threading.RLock()


def out_dir() -> Path:
    d = Path(os.environ.get("SERVER_OUT", ROOT / "server" / "out"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def release_log_path() -> Path:
    return out_dir() / "release_log.jsonl"


def queue_path() -> Path:
    return out_dir() / "overseer_queue.json"


class StaleRevision(Exception):
    """The reviewed result is no longer the queued one: a later run of the spec replaced it,
    or it was decided already."""


class NotQueued(KeyError):
    """Neither the spec is queued nor was it ever queued under the given revision."""


def _load_queue() -> list[dict]:
    p = queue_path()
    q = json.loads(p.read_text()) if p.exists() else []
    for item in q:  # queued before revisions existed: a stable token from what was queued
        item.setdefault("revision", f"legacy-{item['queued']}")
    return q


def queued_item(spec_hash: str) -> dict | None:
    return next((i for i in _load_queue() if i["spec_hash"] == spec_hash), None)


def _write_atomic(path: Path, text: str) -> None:
    """Readers see the old file or the new one, never a partial write. The temp file is
    opened normally (not mkstemp's 0600) so the result keeps the usual umask permissions."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with open(tmp, "x") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _save_queue(q: list[dict]) -> None:
    _write_atomic(queue_path(), json.dumps(q, indent=2))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _releasable(merged: dict) -> dict:
    """What actually leaves: the ReleasedResult projection -- no per-site contributions, and each
    per-site suppression named by its location only (the reason detail holds the small count)."""
    released = {k: v for k, v in merged.items() if k != "contributions"}
    released["rejected_per_site"] = {site: [reason.split(":", 1)[0] for reason in reasons]
                                     for site, reasons in merged.get("rejected_per_site", {}).items()}
    return ReleasedResult.model_validate(released).model_dump(exclude_none=True)


def _was_queued(spec_hash: str, revision: str) -> bool:
    p = release_log_path()
    lines = p.read_text().splitlines() if p.exists() else []
    return any(e.get("spec_hash") == spec_hash and e.get("revision") == revision for e in map(json.loads, lines))


def log_release(spec: dict, merged: dict, check: dict, decision: str, note: str = "", by: str = "auto",
                revision: str | None = None) -> None:
    entry = {
        "ts": _now(), "spec_hash": merged.get("spec_hash"), "revision": revision, "signature": check.get("signature"),
        "project_id": spec.get("project_id"), "analysis_type": spec.get("analysis_type"),
        "n": merged["n"], "coverage": merged["coverage"], "sites_missing": merged["sites_missing"],
        "check": check["decision"], "reasons": check.get("reasons", []), "decision": decision, "by": by, "note": note,
    }
    with open(release_log_path(), "a") as f:
        f.write(json.dumps(entry) + "\n")


def record(spec: dict, merged: dict, check: dict) -> Path:
    """Called by the FLARE controller / HTTP API after the disclosure check. Returns the run dir.
    The merged result must satisfy the MergedResult contract; a shape drift fails here, loudly.
    The run dir and queue describe this (latest) run of the spec hash: an earlier run's
    release or pending queue entry does not survive it. A flagged result is queued under a
    fresh revision, so a decision taken on the earlier entry cannot release this one."""
    merged = MergedResult.model_validate(merged).model_dump(exclude_none=True)
    d = out_dir() / (merged.get("spec_hash") or "unknown")
    with RELEASE_LOCK:
        # Withdraw the earlier run's queue entry and release before overwriting its files: if a
        # write below fails, no revision is left pointing at files it was not issued for.
        queue = _load_queue()
        q = [i for i in queue if i["spec_hash"] != merged.get("spec_hash")]
        if len(q) != len(queue):
            _save_queue(q)
        (d / "released.json").unlink(missing_ok=True)
        d.mkdir(parents=True, exist_ok=True)
        (d / "spec.json").write_text(json.dumps(spec, indent=2))
        (d / "result.json").write_text(json.dumps(merged, indent=2))
        (d / "check.json").write_text(json.dumps(check, indent=2))
        if check["decision"] == "OK":
            # log first: the differencing check reads the log, so a release must never exist unlogged
            log_release(spec, merged, check, "RELEASED")
            _write_atomic(d / "released.json", json.dumps(_releasable(merged), indent=2))
        else:
            revision = secrets.token_hex(8)
            q.append({"spec_hash": merged.get("spec_hash"), "revision": revision, "queued": _now(),
                      "project_id": spec.get("project_id"), "analysis_type": spec.get("analysis_type"),
                      "reasons": check["reasons"], "dir": str(d)})
            log_release(spec, merged, check, "QUEUED", revision=revision)
        _save_queue(q)
    return d


def decide(spec_hash: str, revision: str, approve: bool, note: str, by: str) -> Path:
    """Approve or reject the queued result the overseer reviewed, named by its revision.
    Raises StaleRevision, changing nothing, if the spec is queued under another revision or
    this revision was queued before (a later run replaced it, or it was decided already);
    NotQueued if neither the spec is queued nor this revision ever was."""
    with RELEASE_LOCK:
        q = _load_queue()
        item = next((i for i in q if i["spec_hash"] == spec_hash), None)
        if item is None and not _was_queued(spec_hash, revision):
            raise NotQueued(f"{spec_hash}: not in queue")
        if item is None or item["revision"] != revision:
            raise StaleRevision(f"{spec_hash}: stale revision {revision}: the queue changed since it was listed (a newer "
                                "run replaced the result, or it was decided already); list the queue and review again")
        d = Path(item["dir"])
        spec, merged, check = (json.loads((d / f).read_text()) for f in ("spec.json", "result.json", "check.json"))
        # Dequeue, then log, then release: if a step fails, the revision cannot be decided again
        # and no release exists without its log line (which the differencing check reads).
        _save_queue([i for i in q if i["spec_hash"] != spec_hash])
        log_release(spec, merged, check, "RELEASED" if approve else "REJECTED", note, by, revision)
        if approve:
            _write_atomic(d / "released.json", json.dumps(_releasable(merged), indent=2))
        else:
            (d / "released.json").unlink(missing_ok=True)
    return d


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("show").add_argument("spec_hash")
    for c in ("approve", "reject"):
        p = sub.add_parser(c)
        p.add_argument("spec_hash")
        p.add_argument("--revision", required=True, help="the revision list/show printed for the result you reviewed")
        p.add_argument("--note", default="")
        p.add_argument("--by", default=os.environ.get("USER", "overseer"))
    a = ap.parse_args()
    if a.cmd == "list":
        q = _load_queue()
        print(f"{len(q)} pending" + ("" if not q else ":"))
        for i in q:
            print(f"  {i['spec_hash']}  rev {i['revision']}  {i['analysis_type']:12s} {i['project_id']:16s} {i['queued']}  "
                  f"{'; '.join(i['reasons'])}")
    elif a.cmd == "show":
        item = queued_item(a.spec_hash)
        if item is None:
            sys.exit("not in queue")
        print(f"revision {item['revision']}", file=sys.stderr)  # stdout stays the result JSON
        print((Path(item["dir"]) / "result.json").read_text())
    else:
        try:
            d = decide(a.spec_hash, a.revision, a.cmd == "approve", a.note, a.by)
        except (NotQueued, StaleRevision) as e:
            sys.exit(str(e))
        print(f"{a.spec_hash}: {'APPROVED -> ' + str(d / 'released.json') if a.cmd == 'approve' else 'REJECTED'}")


if __name__ == "__main__":
    main()
