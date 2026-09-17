#!/usr/bin/env python
"""Run one AnalysisSpec as a FLARE job.

  python scripts/run_job.py spec/examples/allele_freq.json                 # simulator (default): needs dev_tres.py running
  python scripts/run_job.py spec/examples/allele_freq.json --mode prod     # submit to a live FLARE server via the admin kit

Both modes end with scripts/verify.py on the merged result and the overseer queue status.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.build_job import build  # noqa: E402
from scripts.sites import load_sites  # noqa: E402


def out_dir() -> Path:
    return Path(os.environ.get("SERVER_OUT", ROOT / "server" / "out"))


def run_simulator(job: Path, clients: list[str], workspace: Path) -> None:
    cmd = [sys.executable, "-m", "nvflare.private.fed.app.simulator.simulator", str(job), "-w", str(workspace),
           "-n", str(len(clients)), "-c", ",".join(clients), "-t", str(len(clients)), "-l", "concise"]
    env = {**os.environ, "SERVER_OUT": str(out_dir())}
    subprocess.run(cmd, check=True, cwd=ROOT, env=env)


def run_prod(job: Path, admin_kit: Path, username: str, timeout: int) -> None:
    from nvflare.fuel.flare_api.flare_api import new_secure_session

    sess = new_secure_session(username, str(admin_kit))
    try:
        job_id = sess.submit_job(str(job))
        print(f"submitted job {job_id}; waiting (timeout {timeout}s)")
        rc = sess.monitor_job(job_id, timeout=timeout)
        print(f"job {job_id} finished: {rc}")
    finally:
        sess.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", type=Path)
    ap.add_argument("--mode", choices=["simulator", "prod"], default="simulator")
    ap.add_argument("--min-clients", type=int, default=2)
    ap.add_argument("--wait-time", type=int, default=60)
    ap.add_argument("--task-timeout", type=int, default=300)
    ap.add_argument("--timeout", type=int, default=600, help="prod: how long to wait for the job")
    ap.add_argument("--admin-kit", type=Path)
    a = ap.parse_args()

    cfg = load_sites()
    project = cfg.get("project", {}).get("name", "federated_apis")
    clients = [s["tre_id"] for s in cfg["sites"]]
    job = build(a.spec, ROOT / "flare" / "jobs" / a.spec.stem, a.min_clients, a.wait_time, a.task_timeout)
    before = {p.name for p in out_dir().iterdir()} if out_dir().exists() else set()
    t0 = time.time()
    if a.mode == "simulator":
        run_simulator(job, clients, Path(os.environ.get("SIM_WORKSPACE", "/tmp/flare_sim")) / a.spec.stem)
    else:
        admin = a.admin_kit or ROOT / "flare" / "workspace" / project / "prod_00" / f"admin@{cfg['server']['org']}.org"
        run_prod(job, admin, admin.name, a.timeout)
    print(f"round time: {time.time() - t0:.1f}s")

    # newest result dir written by the controller
    runs = sorted((p for p in out_dir().iterdir() if p.is_dir() and (p / "result.json").exists()), key=lambda p: p.stat().st_mtime)
    if not runs:
        sys.exit("no result written (in prod mode the result lives on the server: see server/out/ or /server_out)")
    res = runs[-1] / "result.json"
    print(f"\nresult: {res}")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "verify.py"), str(res)], cwd=ROOT)
    print()
    subprocess.run([sys.executable, "-m", "server.overseer_queue", "list"], cwd=ROOT, env={**os.environ, "SERVER_OUT": str(out_dir())})


if __name__ == "__main__":
    main()
