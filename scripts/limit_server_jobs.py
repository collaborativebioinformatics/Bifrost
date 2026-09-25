#!/usr/bin/env python
"""Cap the provisioned FLARE server's job scheduler at 1 concurrent job.

NVFlare's provisioned server kit ships local/resources.json.default with
DefaultJobScheduler's max_jobs=4 (nvflare/lighter/templates/master_template.yml,
local_server_resources). At runtime NVFlare uses local/resources.json if it
exists, otherwise falls back to local/resources.json.default -- it does not
merge the two (nvflare.apis.workspace.Workspace._fallback_path). The server's
overseer queue and release log (server/overseer_queue.py) are not safe for
concurrent writer processes, so the federation must run one job at a time.

This writes local/resources.json next to the server kit's .default file, a
copy of the complete default document with components[job_scheduler].args
.max_jobs forced to 1. The .default file is left untouched. Idempotent: reruns
overwrite resources.json with the same result as long as the .default is
unchanged.

  python scripts/limit_server_jobs.py flare/workspace/<project>/prod_00
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_NAME = "resources.json.default"
TARGET_NAME = "resources.json"


def find_server_kit_default(prod_dir: Path) -> Path:
    """Return the single */local/resources.json.default under prod_dir whose
    components include a job_scheduler; fail loudly if that isn't exactly one."""
    matches = []
    for default_path in sorted(prod_dir.glob(f"*/local/{DEFAULT_NAME}")):
        doc = json.loads(default_path.read_text())
        if any(c.get("id") == "job_scheduler" for c in doc.get("components", [])):
            matches.append(default_path)
    if len(matches) != 1:
        raise SystemExit(
            f"expected exactly one server kit ({DEFAULT_NAME} with a job_scheduler component) "
            f"under {prod_dir}, found {len(matches)}: {matches}"
        )
    return matches[0]


def limit_max_jobs(default_path: Path, max_jobs: int = 1) -> Path:
    """Write local/resources.json next to default_path: the full default document
    with components[job_scheduler].args.max_jobs set to max_jobs."""
    doc = json.loads(default_path.read_text())
    schedulers = [c for c in doc.get("components", []) if c.get("id") == "job_scheduler"]
    if len(schedulers) != 1:
        raise SystemExit(f"expected exactly one job_scheduler component in {default_path}, found {len(schedulers)}")
    schedulers[0]["args"]["max_jobs"] = max_jobs
    target = default_path.with_name(TARGET_NAME)
    target.write_text(json.dumps(doc, indent=2) + "\n")
    return target


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prod_dir", type=Path, help="provisioned prod_XX dir, e.g. flare/workspace/<project>/prod_00")
    ap.add_argument("--max-jobs", type=int, default=1)
    a = ap.parse_args()
    default_path = find_server_kit_default(a.prod_dir)
    target = limit_max_jobs(default_path, a.max_jobs)
    print(f"wrote {target} (max_jobs={a.max_jobs})")


if __name__ == "__main__":
    main()
