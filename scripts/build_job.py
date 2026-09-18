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
from scripts.sites import SITES_PATH  # noqa: E402
from spec.analysis_spec import AnalysisSpec  # noqa: E402

CUSTOM_PKGS = ["adapters", "harmonisation", "spec", "server", "flare"]
CUSTOM_FILES = ["projects.yaml", "scripts/__init__.py", "scripts/sites.py"]


def build(spec_path: Path, out: Path, min_clients: int, wait_time: int, task_timeout: int = 300,
          fedavg: dict | None = None, logreg: dict | None = None) -> Path:
    """fedavg = {"rounds", "local_steps", "lr"} switches a fed_linreg spec from the one-round
    exact (Gram matrix) workflow to the multi-round FedAvg workflow where only β leaves.
    logreg = {"rounds", "tol", "ridge"}: fed_logreg always runs multi-round Newton-Raphson
    (there is no one-round "exact" mode for logistic regression)."""
    spec = AnalysisSpec.model_validate_json(spec_path.read_text())
    if fedavg and spec.analysis_type != "fed_linreg":
        raise ValueError("--fedavg needs a fed_linreg spec")
    if spec.analysis_type == "fed_logreg" and not logreg:
        raise ValueError("fed_logreg needs logreg={'rounds', 'tol', 'ridge'}")
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
    shutil.copy(SITES_PATH, custom / "sites.yaml")

    (out / "meta.json").write_text(json.dumps({
        "name": out.name, "resource_spec": {}, "min_clients": min_clients,
        "deploy_map": {"app": ["@ALL"]},
    }, indent=2))
    common = {"spec": spec.model_dump(), "min_clients": min_clients, "wait_time": wait_time, "task_timeout": task_timeout}
    if spec.analysis_type == "fed_logreg":
        workflow = {"id": "fed_logreg", "path": "flare.app.logreg_controller.FedLogregController", "args": {**common, **logreg}}
        executor = {"tasks": ["logreg_init", "logreg_step"], "executor": {"path": "flare.app.logreg_executor.LogregExecutor", "args": {}}}
    elif fedavg:
        workflow = {"id": "fed_linreg", "path": "flare.app.linreg_controller.FedLinregController", "args": {**common, **fedavg}}
        executor = {"tasks": ["linreg_init", "linreg_train"], "executor": {"path": "flare.app.linreg_executor.LinregExecutor", "args": {}}}
    else:
        workflow = {"id": "fed_analysis", "path": "flare.app.controller.FedAnalysisController", "args": common}
        executor = {"tasks": ["analyse"], "executor": {"path": "flare.app.executor.AdapterExecutor", "args": {}}}
    (app / "config" / "config_fed_server.json").write_text(json.dumps({"format_version": 2, "workflows": [workflow], "components": []}, indent=2))
    (app / "config" / "config_fed_client.json").write_text(json.dumps({"format_version": 2, "executors": [executor], "components": []}, indent=2))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--min-clients", type=int, default=2)
    ap.add_argument("--wait-time", type=int, default=60)
    ap.add_argument("--task-timeout", type=int, default=300)
    ap.add_argument("--fedavg", action="store_true", help="fed_linreg: multi-round FedAvg (only β leaves) instead of exact")
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--local-steps", type=int, default=1)
    ap.add_argument("--lr", type=float, default=1.0)
    ap.add_argument("--tol", type=float, default=1e-8, help="fed_logreg: Newton-Raphson convergence tolerance")
    ap.add_argument("--ridge", type=float, default=1e-6, help="fed_logreg: Hessian damping term")
    a = ap.parse_args()
    spec = AnalysisSpec.model_validate_json(a.spec.read_text())
    out = a.out or ROOT / "flare" / "jobs" / (a.spec.stem + ("_fedavg" if a.fedavg else ""))
    logreg = {"rounds": a.rounds, "tol": a.tol, "ridge": a.ridge} if spec.analysis_type == "fed_logreg" else None
    build(a.spec, out, a.min_clients, a.wait_time, a.task_timeout,
          {"rounds": a.rounds, "local_steps": a.local_steps, "lr": a.lr} if a.fedavg else None, logreg)
    print(out)


if __name__ == "__main__":
    main()
