#!/usr/bin/env python
"""Build a FLARE job folder for one AnalysisSpec.

  python scripts/build_job.py spec/examples/allele_freq.json [--out flare/jobs/allele_freq] [--min-clients 2] [--wait-time 60]

The job is self-contained: app/custom/ carries adapters, harmonisation, spec,
server code and sites.yaml, so the same job runs in the simulator, POC and
production. Job name = spec file stem.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from spec.analysis_spec import AnalysisSpec  # noqa: E402

CUSTOM_PKGS = ["adapters", "harmonisation", "spec", "server", "flare"]
CUSTOM_FILES = ["sites.yaml", "projects.yaml", "scripts/__init__.py", "scripts/sites.py"]


def build(spec_path: Path, out: Path, min_clients: int, wait_time: int, task_timeout: int = 300) -> Path:
    spec = AnalysisSpec.model_validate_json(spec_path.read_text())
    if out.exists():
        shutil.rmtree(out)
    app = out / "app"
    (app / "config").mkdir(parents=True)
    custom = app / "custom"
    custom.mkdir()
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "workspace", "jobs", "out", "examples")
    for pkg in CUSTOM_PKGS:
        shutil.copytree(ROOT / pkg, custom / pkg, ignore=ignore)
    for f in CUSTOM_FILES:
        (custom / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / f, custom / f)

    (out / "meta.json").write_text(json.dumps({
        "name": out.name, "resource_spec": {}, "min_clients": min_clients,
        "deploy_map": {"app": ["@ALL"]},
    }, indent=2))
    (app / "config" / "config_fed_server.json").write_text(json.dumps({
        "format_version": 2,
        "workflows": [{"id": "fed_analysis", "path": "flare.app.controller.FedAnalysisController",
                       "args": {"spec": spec.model_dump(), "min_clients": min_clients, "wait_time": wait_time,
                                "task_timeout": task_timeout}}],
        "components": [],
    }, indent=2))
    (app / "config" / "config_fed_client.json").write_text(json.dumps({
        "format_version": 2,
        "executors": [{"tasks": ["analyse"], "executor": {"path": "flare.app.executor.AdapterExecutor", "args": {}}}],
        "components": [],
    }, indent=2))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--min-clients", type=int, default=2)
    ap.add_argument("--wait-time", type=int, default=60)
    ap.add_argument("--task-timeout", type=int, default=300)
    a = ap.parse_args()
    out = a.out or ROOT / "flare" / "jobs" / a.spec.stem
    build(a.spec, out, a.min_clients, a.wait_time, a.task_timeout)
    print(out)


if __name__ == "__main__":
    main()
