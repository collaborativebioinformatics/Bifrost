#!/usr/bin/env python
"""Run every TRE from sites.yaml locally (no Docker), for adapter development.

  python scripts/dev_tres.py                  one uvicorn process per site, ports 8001, 8002, ...
  python scripts/dev_tres.py --single-process one process, every TRE mounted at /<tre_id> (scale sims)

Writes <data-dir>/local_urls.json {tre_id: url}, which adapters pick up when
TRE_API_URL is unset (path overridable with LOCAL_URLS). Ctrl-C stops all.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import argparse
import importlib

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.sites import load_sites  # noqa: E402

BASE_PORT = int(os.environ.get("DEV_BASE_PORT", "8000"))


def single_process_app(sites: list[dict], data_dir: Path):
    """One FastAPI app with every TRE mounted under /<tre_id>."""
    from fastapi import FastAPI

    root = FastAPI(title="all TREs (dev)")
    for s in sites:
        mod = importlib.import_module(f"tres.{s['adapter']}.app")
        root.mount(f"/{s['tre_id']}", mod.create_app(s["tre_id"], str(data_dir / f"{s['tre_id']}.csv")))
    return root


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--single-process", action="store_true")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data" / "sites")
    ap.add_argument("--port", type=int, default=BASE_PORT)
    a = ap.parse_args()
    sites = load_sites()["sites"]
    procs, urls = [], {}
    if a.single_process:
        port = a.port + 1
        env = {**os.environ, "PYTHONPATH": str(ROOT), "DEV_SITES_DATA_DIR": str(a.data_dir)}
        cmd = [sys.executable, "-m", "uvicorn", "scripts.dev_tres:_app", "--factory", "--port", str(port), "--log-level", "warning"]
        procs.append(subprocess.Popen(cmd, env=env, cwd=ROOT))
        urls = {s["tre_id"]: f"http://127.0.0.1:{port}/{s['tre_id']}" for s in sites}
    else:
        for i, s in enumerate(sites, 1):
            port = a.port + i
            env = {**os.environ, "TRE_ID": s["tre_id"], "DATA_PATH": str(a.data_dir / f"{s['tre_id']}.csv"),
                   "TRE_REGION": s["region"], "PYTHONPATH": str(ROOT)}
            cmd = [sys.executable, "-m", "uvicorn", f"tres.{s['adapter']}.app:app", "--port", str(port), "--log-level", "warning"]
            procs.append(subprocess.Popen(cmd, env=env, cwd=ROOT))
            urls[s["tre_id"]] = f"http://127.0.0.1:{port}"
    Path(os.environ.get("LOCAL_URLS", a.data_dir / "local_urls.json")).write_text(json.dumps(urls, indent=2))

    for tid, url in urls.items():
        for _ in range(100):
            try:
                r = httpx.get(f"{url}/health", timeout=1.0)
                if r.status_code != 200:
                    raise httpx.HTTPError(f"{r.status_code}: {r.text[:200]}")
                if len(urls) <= 10:
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
    print(f"{len(urls)} TREs running; Ctrl-C to stop", flush=True)
    for p in procs:
        p.wait()


def _app():
    """uvicorn factory for --single-process."""
    return single_process_app(load_sites()["sites"], Path(os.environ["DEV_SITES_DATA_DIR"]))


if __name__ == "__main__":
    main()
