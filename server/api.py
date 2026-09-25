"""Heimdall HTTP API -- the contract the researcher UI (frontend/) talks to.

    SERVER_OUT=server/api_out uvicorn server.api:app --port 8500 --workers 1

Start mock TREs separately with `python scripts/dev_tres.py`. Neither TREs nor
FLARE are required for startup or /health. API_TRE_TIMEOUT (seconds per HTTP
operation, default 10) and API_TRE_WORKERS (default 8) control adapter fan-out;
API_CORS_ORIGINS (comma-separated, default http://localhost:3000) allows the UI
origin. Use an exclusive SERVER_OUT directory; see analysis_service's
concurrency and demo-versus-secure-deployment notes. Variable names are
canonical (snp_rs001), never local column names; requests carry variable
names, never participant data. Response shapes: server/schemas.py, exported to
docs/schemas/ and served at /openapi.json.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harmonisation import load_canonical
from scripts.sites import ROOT, load_sites
from server import analysis_service, overseer_queue
from server.schemas import (AnalysisResponse, AuditLog, Metadata, OverseerDecision, OverseerQueue, RunRequest,
                            RunStatus)
from spec.analysis_spec import AnalysisSpec

app = FastAPI(title="Heimdall federated analysis API", version="0.2.0", description=__doc__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("API_CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()],
    allow_methods=["*"], allow_headers=["*"],
)
LOG = logging.getLogger(__name__)
EXAMPLES_DIR = ROOT / "spec" / "examples"
FILTER_OPS = {"==", "!=", ">", ">=", "<", "<=", "in"}


class RegressionRequest(BaseModel):
    """Convenience body for POST /linear-regression (same semantics as an AnalysisSpec)."""

    model_config = ConfigDict(extra="forbid")
    features: list[str] = Field(min_length=1)
    target: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    filters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    min_cell_size: int = Field(default=5, ge=5)


class LogisticRegressionRequest(RegressionRequest):
    """POST /logistic-regression: a RegressionRequest plus the Newton-Raphson budget."""

    rounds: int = Field(default=25, ge=1, le=100)
    tol: float = Field(default=1e-8, gt=0)


def _spec(**kwargs) -> AnalysisSpec:
    """Build and validate an AnalysisSpec against the harmonisation map. 422 on any problem."""
    try:
        spec = AnalysisSpec(**kwargs)
        canonical = load_canonical()
        names = [*spec.variables, *spec.filters, *([spec.outcome] if spec.outcome else [])]
        if any(name not in canonical for name in names):
            raise ValueError("Use known canonical variable names")
        if len(set(spec.variables)) != len(spec.variables):
            raise ValueError("Features must be unique")
        if spec.analysis_type == "allele_freq" and any(canonical[v]["type"] != "genotype" for v in spec.variables):
            raise ValueError("Variant must be a genotype variable")
        if spec.analysis_type == "fed_logreg" and canonical[spec.outcome]["type"] != "binary":
            raise ValueError("Logistic regression target must be a binary variable")
        for conditions in spec.filters.values():
            for op, value in conditions.items():
                if op not in FILTER_OPS:
                    raise ValueError("Unsupported filter operator")
                if op == "in" and not isinstance(value, list):
                    raise ValueError("The in operator requires a list")
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return spec


def _analyse(spec: AnalysisSpec, fedavg_rounds: int = 0, logreg: dict | None = None) -> tuple[int, dict]:
    """One dispatcher for the synchronous endpoints and the background /run path."""
    if spec.analysis_type == "fed_logreg":
        return analysis_service.analyse_logreg(spec, **(logreg or {}))
    return analysis_service.analyse(spec, fedavg_rounds)


def _execute(spec: AnalysisSpec, fedavg_rounds: int = 0, logreg: dict | None = None) -> JSONResponse:
    try:
        status, body = _analyse(spec, fedavg_rounds, logreg)
        return JSONResponse(body, status_code=status)
    except Exception:
        # Fail closed if merge, configuration, disclosure or audit persistence fails.
        LOG.exception("Analysis failed")
        return JSONResponse({"status": "failed", "error": "analysis_failed",
                             "spec_hash": spec.spec_hash()}, status_code=500)


@app.get("/health")
def health():
    return {"status": "ok"}


# ---- convenience endpoints (one call, synchronous) ----------------------------------
@app.get("/allele-frequency", response_model=AnalysisResponse, response_model_exclude_none=True,
         responses={503: {"model": AnalysisResponse}})
def allele_frequency(variant: str = Query(min_length=1),
                     project_id: str = Query(min_length=1),
                     min_cell_size: int = Query(default=5, ge=5)):
    return _execute(_spec(analysis_type="allele_freq", variables=[variant], project_id=project_id,
                          min_cell_size=min_cell_size))


@app.post("/linear-regression", response_model=AnalysisResponse, response_model_exclude_none=True,
          responses={503: {"model": AnalysisResponse}})
def linear_regression(request: RegressionRequest):
    return _execute(_spec(analysis_type="fed_linreg", variables=request.features, outcome=request.target,
                          project_id=request.project_id, filters=request.filters,
                          min_cell_size=request.min_cell_size))


@app.post("/logistic-regression", response_model=AnalysisResponse, response_model_exclude_none=True,
          responses={503: {"model": AnalysisResponse}})
def logistic_regression(request: LogisticRegressionRequest):
    """Federated logistic regression (Newton-Raphson/IRLS). Unlike /linear-regression this
    makes up to `rounds` sequential round-trips to every site, so it is not one call."""
    return _execute(_spec(analysis_type="fed_logreg", variables=request.features, outcome=request.target,
                          project_id=request.project_id, filters=request.filters,
                          min_cell_size=request.min_cell_size),
                    logreg={"rounds": request.rounds, "tol": request.tol})


# ---- researcher UI contract ----------------------------------------------------------
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@app.get("/metadata", response_model=Metadata)
def metadata(probe: bool = Query(default=True, description="probe each TRE's /health (2 s timeout)")):
    canonical = load_canonical()
    projects = yaml.safe_load((ROOT / "projects.yaml").read_text()).get("projects") or {}
    sites = load_sites()["sites"]

    def online(site: dict) -> bool | None:
        from adapters import registry  # lazy: metadata must not need adapters at import time

        try:
            adapter = registry.load(site["tre_id"])
            try:
                return adapter.http.get("/health", timeout=2.0).status_code == 200
            finally:
                adapter.http.close()
        except Exception:
            return False

    status: dict[str, bool | None] = {s["tre_id"]: None for s in sites}
    if probe and sites:
        with ThreadPoolExecutor(max_workers=min(8, len(sites))) as pool:
            for site, ok in zip(sites, pool.map(online, sites)):
                status[site["tre_id"]] = ok
    return Metadata(
        variables=[{"name": n, "type": v["type"], "unit": v.get("unit") or "", "description": v.get("description") or ""}
                   for n, v in canonical.items()],
        projects=[{"id": pid, "description": (p or {}).get("description") or ""} for pid, p in projects.items()],
        examples=sorted(p.name for p in EXAMPLES_DIR.glob("*.json")),
        sites=[{"tre_id": s["tre_id"], "adapter": s["adapter"], "region": s["region"], "online": status[s["tre_id"]]} for s in sites],
        analysis_types=["allele_freq", "fed_stats", "fed_linreg", "fed_logreg"],
    )


@app.get("/examples/{name}")
def example(name: str):
    if name not in {p.name for p in EXAMPLES_DIR.glob("*.json")}:  # listing, not path arithmetic
        raise HTTPException(404, "unknown example")
    return json.loads((EXAMPLES_DIR / name).read_text())


_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.Lock()


def _decision_update(approve: bool, result: dict | None) -> dict:
    """How a flagged run reports an overseer's decision."""
    return ({"status": "completed", "decision": "APPROVED", "result": result} if approve
            else {"status": "rejected", "decision": "REJECTED"})


def _decided_meanwhile(spec_hash: str) -> dict:
    """An overseer can decide between record() queuing this run's result and the run's
    status being written. Read under RELEASE_LOCK, the queue and release log say whether
    one did: this run queued the item, so if it is gone, a later log line removed it."""
    if any(i["spec_hash"] == spec_hash for i in overseer_queue._load_queue()):
        return {}
    last = overseer_queue.latest_log_entry(spec_hash)
    if not last or last["check"] != "FLAGGED" or last["decision"] not in ("RELEASED", "REJECTED"):
        return {}  # e.g. superseded by a later, auto-released run of the same spec
    approve = last["decision"] == "RELEASED"
    released = overseer_queue.out_dir() / spec_hash / "released.json"
    return _decision_update(approve, json.loads(released.read_text()) if approve else None)


def _run_in_background(run_id: str, spec: AnalysisSpec, fedavg_rounds: int) -> None:
    with _RUNS_LOCK:
        _RUNS[run_id]["status"] = "running"
    try:
        _, body = _analyse(spec, fedavg_rounds)
    except Exception:
        LOG.exception("Run %s failed", run_id)
        body = {"status": "failed", "error": "analysis_failed", "spec_hash": spec.spec_hash()}
    with overseer_queue.RELEASE_LOCK, _RUNS_LOCK:  # no decision lands between this check and the write
        if body.get("status") == "flagged":
            body = {**body, **_decided_meanwhile(body["spec_hash"])}
        _RUNS[run_id].update(body, finished=_now())


@app.post("/run", response_model=RunStatus, response_model_exclude_none=True, status_code=202)
def submit_run(request: RunRequest):
    spec = _spec(**request.model_dump(exclude={"fedavg_rounds"}))
    if request.fedavg_rounds and spec.analysis_type != "fed_linreg":
        raise HTTPException(422, "fedavg_rounds applies to fed_linreg only")
    run_id = uuid.uuid4().hex[:12]
    with _RUNS_LOCK:
        _RUNS[run_id] = {"run_id": run_id, "status": "queued", "submitted": _now(), "spec_hash": spec.spec_hash()}
    threading.Thread(target=_run_in_background, args=(run_id, spec, request.fedavg_rounds), daemon=True).start()
    return _RUNS[run_id]


@app.get("/run/{run_id}", response_model=RunStatus, response_model_exclude_none=True)
def run_status(run_id: str):
    with _RUNS_LOCK:
        run = _RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")
    return run


@app.get("/overseer", response_model=OverseerQueue)
def overseer():
    return {"items": [{"id": i["spec_hash"], **{k: v for k, v in i.items() if k != "dir"}} for i in overseer_queue._load_queue()]}


@app.post("/overseer/{spec_hash}/{decision}")
def overseer_decide(spec_hash: str, decision: str, body: OverseerDecision | None = None):
    if decision not in ("approve", "reject"):
        raise HTTPException(404, "decision must be approve or reject")
    body = body or OverseerDecision()
    approve = decision == "approve"
    # The decision, reading what it released, and every waiting run's status change as one
    # step: no rerun or finishing background run interleaves (see _decided_meanwhile).
    with overseer_queue.RELEASE_LOCK:
        try:
            d = overseer_queue.decide(spec_hash, approve, body.note, body.by)
        except KeyError:
            raise HTTPException(404, "not in queue")
        update = _decision_update(approve, json.loads((d / "released.json").read_text()) if approve else None)
        # Runs of this spec still waiting on the overseer now report the decision (the queue
        # holds the latest run's result, which is what an approval releases).
        with _RUNS_LOCK:
            for run in _RUNS.values():
                if run.get("spec_hash") == spec_hash and run["status"] == "flagged":
                    run.update(update)
    return {"spec_hash": spec_hash, "decision": "RELEASED" if approve else "REJECTED", "released": approve}


@app.get("/audit", response_model=AuditLog)
def audit(spec_hash: str | None = None, limit: int = Query(default=200, ge=1, le=5000)):
    """Per-site audit lines (newest first). AUDIT_ROOT/<tre_id>/audit.jsonl, default <repo>/audit/;
    AUDIT_DIR (single directory) is honoured for the in-process demo layout."""
    root = Path(os.environ.get("AUDIT_ROOT", ROOT / "audit"))
    files = [root / s["tre_id"] / "audit.jsonl" for s in load_sites()["sites"]]
    if os.environ.get("AUDIT_DIR"):
        files.append(Path(os.environ["AUDIT_DIR"]) / "audit.jsonl")
    entries = []
    for f in files:
        if f.exists():
            for line in f.read_text().splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if spec_hash and e.get("spec_hash") != spec_hash:
                    continue
                e["timestamp"] = e.pop("ts", "")
                entries.append(e)
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return {"items": entries[:limit]}
