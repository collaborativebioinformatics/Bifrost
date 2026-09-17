#!/usr/bin/env python
"""Run every TRE from sites.yaml as a local uvicorn process (no Docker), for
adapter development on a laptop. Ports: 8001, 8002, ... in sites.yaml order.
Writes data/sites/local_urls.json {tre_id: url} which adapters pick up when
TRE_API_URL is unset. Ctrl-C stops all.

  python scripts/dev_tres.py
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.sites import load_sites  # noqa: E402

BASE_PORT = int(os.environ.get("DEV_BASE_PORT", "8000"))


def main() -> None:
    sites = load_sites()["sites"]
    procs, urls = [], {}
    for i, s in enumerate(sites, 1):
        port = BASE_PORT + i
        env = {**os.environ, "TRE_ID": s["tre_id"], "DATA_PATH": str(ROOT / "data" / "sites" / f"{s['tre_id']}.csv"),
               "TRE_REGION": s["region"], "PYTHONPATH": str(ROOT)}
        cmd = [sys.executable, "-m", "uvicorn", f"tres.{s['adapter']}.app:app", "--port", str(port), "--log-level", "warning"]
        procs.append(subprocess.Popen(cmd, env=env, cwd=ROOT))
        urls[s["tre_id"]] = f"http://127.0.0.1:{port}"
    (ROOT / "data" / "sites" / "local_urls.json").write_text(json.dumps(urls, indent=2))

    for tid, url in urls.items():
        for _ in range(50):
            try:
                r = httpx.get(f"{url}/health", timeout=1.0)
                print(f"{tid:8s} {url}  {r.json()}", flush=True)
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            print(f"{tid}: did not come up", file=sys.stderr)

    def stop(*_):
        for p in procs:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print("TREs running; Ctrl-C to stop", flush=True)
    for p in procs:
        p.wait()


if __name__ == "__main__":
    main()
