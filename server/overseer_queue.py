"""Overseer queue: flagged results wait here for a human decision.

  python -m server.overseer_queue list
  python -m server.overseer_queue show <spec_hash>
  python -m server.overseer_queue approve <spec_hash> [--note "..."]
  python -m server.overseer_queue reject  <spec_hash> [--note "..."]

Files (under SERVER_OUT, default server/out/):
  overseer_queue.json   pending items
  release_log.jsonl     every release decision (auto-OK, approved, rejected), keyed by spec hash
  <spec_hash>/result.json          merged result (written for every run)
  <spec_hash>/released.json        only after OK / approval -- this is what leaves the server
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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


def _save_queue(q: list[dict]) -> None:
    queue_path().write_text(json.dumps(q, indent=2))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _releasable(merged: dict) -> dict:
    """What actually leaves: strip per-site contributions (server-internal)."""
    return {k: v for k, v in merged.items() if k != "contributions"}


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
    """Called by the FLARE controller after the disclosure check. Returns the run dir."""
    d = out_dir() / (merged.get("spec_hash") or "unknown")
    d.mkdir(parents=True, exist_ok=True)
    (d / "spec.json").write_text(json.dumps(spec, indent=2))
    (d / "result.json").write_text(json.dumps(merged, indent=2))
    (d / "check.json").write_text(json.dumps(check, indent=2))
    if check["decision"] == "OK":
        (d / "released.json").write_text(json.dumps(_releasable(merged), indent=2))
        log_release(spec, merged, check, "RELEASED")
    else:
        q = [i for i in _load_queue() if i["spec_hash"] != merged.get("spec_hash")]
        q.append({"spec_hash": merged.get("spec_hash"), "queued": _now(), "project_id": spec.get("project_id"),
                  "analysis_type": spec.get("analysis_type"), "reasons": check["reasons"], "dir": str(d)})
        _save_queue(q)
        log_release(spec, merged, check, "QUEUED")
    return d


def decide(spec_hash: str, approve: bool, note: str, by: str) -> None:
    q = _load_queue()
    item = next((i for i in q if i["spec_hash"] == spec_hash), None)
    if item is None:
        sys.exit(f"{spec_hash}: not in queue")
    d = Path(item["dir"])
    spec, merged, check = (json.loads((d / f).read_text()) for f in ("spec.json", "result.json", "check.json"))
    if approve:
        (d / "released.json").write_text(json.dumps(_releasable(merged), indent=2))
    log_release(spec, merged, check, "RELEASED" if approve else "REJECTED", note, by)
    _save_queue([i for i in q if i["spec_hash"] != spec_hash])
    print(f"{spec_hash}: {'APPROVED -> ' + str(d / 'released.json') if approve else 'REJECTED'}")


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
        decide(a.spec_hash, a.cmd == "approve", a.note, a.by)


if __name__ == "__main__":
    main()
