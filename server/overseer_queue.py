"""Overseer queue: flagged results wait here for a human decision.

  python -m server.overseer_queue list
  python -m server.overseer_queue show <spec_hash>
  python -m server.overseer_queue approve <spec_hash> [--note "..."]
  python -m server.overseer_queue reject  <spec_hash> [--note "..."]

Files (under SERVER_OUT, default server/out/):
  overseer_queue.json   pending items
  release_log.jsonl     every release decision (auto-OK, approved, rejected), keyed by spec hash
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


def _load_queue() -> list[dict]:
    p = queue_path()
    return json.loads(p.read_text()) if p.exists() else []


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


def latest_log_entry(spec_hash: str) -> dict | None:
    """The newest release-log line for a spec hash (QUEUED / RELEASED / REJECTED), if any."""
    p = release_log_path()
    lines = p.read_text().splitlines() if p.exists() else []
    return next((e for e in map(json.loads, reversed(lines)) if e.get("spec_hash") == spec_hash), None)


def log_release(spec: dict, merged: dict, check: dict, decision: str, note: str = "", by: str = "auto") -> None:
    entry = {
        "ts": _now(), "spec_hash": merged.get("spec_hash"), "signature": check.get("signature"),
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
    release or pending queue entry does not survive it."""
    merged = MergedResult.model_validate(merged).model_dump(exclude_none=True)
    d = out_dir() / (merged.get("spec_hash") or "unknown")
    with RELEASE_LOCK:
        d.mkdir(parents=True, exist_ok=True)
        (d / "spec.json").write_text(json.dumps(spec, indent=2))
        (d / "result.json").write_text(json.dumps(merged, indent=2))
        (d / "check.json").write_text(json.dumps(check, indent=2))
        q = [i for i in _load_queue() if i["spec_hash"] != merged.get("spec_hash")]
        if check["decision"] == "OK":
            _write_atomic(d / "released.json", json.dumps(_releasable(merged), indent=2))
            log_release(spec, merged, check, "RELEASED")
        else:
            (d / "released.json").unlink(missing_ok=True)
            q.append({"spec_hash": merged.get("spec_hash"), "queued": _now(), "project_id": spec.get("project_id"),
                      "analysis_type": spec.get("analysis_type"), "reasons": check["reasons"], "dir": str(d)})
            log_release(spec, merged, check, "QUEUED")
        _save_queue(q)
    return d


def decide(spec_hash: str, approve: bool, note: str, by: str) -> Path:
    """Approve or reject a queued result. Raises KeyError if it is not queued."""
    with RELEASE_LOCK:
        q = _load_queue()
        item = next((i for i in q if i["spec_hash"] == spec_hash), None)
        if item is None:
            raise KeyError(f"{spec_hash}: not in queue")
        d = Path(item["dir"])
        spec, merged, check = (json.loads((d / f).read_text()) for f in ("spec.json", "result.json", "check.json"))
        if approve:
            _write_atomic(d / "released.json", json.dumps(_releasable(merged), indent=2))
        else:
            (d / "released.json").unlink(missing_ok=True)
        log_release(spec, merged, check, "RELEASED" if approve else "REJECTED", note, by)
        _save_queue([i for i in q if i["spec_hash"] != spec_hash])
    return d


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("show").add_argument("spec_hash")
    for c in ("approve", "reject"):
        p = sub.add_parser(c)
        p.add_argument("spec_hash")
        p.add_argument("--note", default="")
        p.add_argument("--by", default=os.environ.get("USER", "overseer"))
    a = ap.parse_args()
    if a.cmd == "list":
        q = _load_queue()
        print(f"{len(q)} pending" + ("" if not q else ":"))
        for i in q:
            print(f"  {i['spec_hash']}  {i['analysis_type']:12s} {i['project_id']:16s} {i['queued']}  {'; '.join(i['reasons'])}")
    elif a.cmd == "show":
        item = next((i for i in _load_queue() if i["spec_hash"] == a.spec_hash), None)
        if item is None:
            sys.exit("not in queue")
        print((Path(item["dir"]) / "result.json").read_text())
    else:
        try:
            d = decide(a.spec_hash, a.cmd == "approve", a.note, a.by)
        except KeyError as e:
            sys.exit(str(e))
        print(f"{a.spec_hash}: {'APPROVED -> ' + str(d / 'released.json') if a.cmd == 'approve' else 'REJECTED'}")


if __name__ == "__main__":
    main()
