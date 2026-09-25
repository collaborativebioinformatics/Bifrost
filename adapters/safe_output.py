"""Safe Output filter -- runs wherever the adapter runs: inside the TRE on the FLARE
path (the executor calls the adapter), but inside the API server process on the
direct-API demo path (server/analysis_service.py), so there the unfiltered
aggregates reach the server before this filter applies.

Rules (all decisions are appended to audit.jsonl):
  1. project_id must be on the Safe Projects allow-list (projects.yaml)
  2. only allow-listed aggregate keys leave; anything else is dropped
  3. k = max(requested min_cell_size, the project's floor in projects.yaml);
     if the filtered cohort has n < k, nothing leaves
  4. any count cell < k is suppressed; if a genotype cell is
     suppressed, derived allele counts for that variable are withheld too
     (they would let the cell be back-calculated); a variable observed in
     fewer than k rows (scalar count < k, or a table with no observed level)
     releases nothing at all
  5. a regression Gram matrix leaves only if n >= max(k, #cols + 1), and only
     if every row has every regression column (no missing values)
  6. a Newton-Raphson round (gradient + Hessian) leaves under the same two
     conditions, via release_irls_step
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

import yaml

from spec.analysis_spec import AnalysisSpec

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_PATH = Path(os.environ.get("PROJECTS_PATH", ROOT / "projects.yaml"))

ALLOWED_SCALARS = {"count", "sum", "sum_sq", "min", "max"}
ALLOWED_TABLES = {"genotype_counts", "histogram"}
ALLOWED_MATRIX = {"gram"}
DEFAULT_MIN_CELL_SIZE = 5  # floor for a project that does not set its own


class Rejected(Exception):
    """A release the Safe Output rules refused; already audited."""


def _projects() -> dict:
    if not PROJECTS_PATH.exists():
        return {}
    return yaml.safe_load(PROJECTS_PATH.read_text()).get("projects") or {}


def project_allowed(project_id: str, tre_id: str) -> bool:
    p = _projects().get(project_id)
    if p is None:
        return False
    sites = p.get("sites", "all")
    return sites == "all" or tre_id in sites


def effective_min_cell_size(project_id: str | None, requested: int) -> int:
    """The researcher may ask for a larger k, never a smaller one than policy allows."""
    floor = (_projects().get(project_id) or {}).get("min_cell_size", DEFAULT_MIN_CELL_SIZE)
    return max(int(requested), int(floor))


def audit_path(tre_id: str) -> Path:
    d = Path(os.environ.get("AUDIT_DIR", ROOT / "audit" / tre_id))
    d.mkdir(parents=True, exist_ok=True)
    return d / "audit.jsonl"


def _audit(tre_id: str, spec: AnalysisSpec, n: int, released: dict, rejected: list[str], decision: str) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tre_id": tre_id,
        "project_id": spec.project_id,
        "spec_hash": spec.spec_hash(),
        "analysis_type": spec.analysis_type,
        "variables": spec.variables + ([spec.outcome] if spec.outcome else []),
        "filters": spec.filters,
        "min_cell_size": effective_min_cell_size(spec.project_id, spec.min_cell_size),
        "n": n,
        "released": {v: sorted(s.keys()) for v, s in released.items()},
        "rejected": rejected,
        "decision": decision,
    }
    with open(audit_path(tre_id), "a") as f:
        f.write(json.dumps(entry) + "\n")


def _observed(stats: dict[str, Any]) -> int | None:
    """Rows in which a variable was observed, where that decides the whole variable:
    a scalar variable's count, or 0 for a table without a single observed level
    (a table with levels is checked cell by cell)."""
    if stats.get("count") is not None:
        return int(stats["count"])
    if any(key in ALLOWED_TABLES and not table for key, table in stats.items()):
        return 0
    return None


def reject_all(tre_id: str, region: str, spec: AnalysisSpec, reason: str):
    from adapters.base import AggregateResult  # local import: base imports this module

    _audit(tre_id, spec, 0, {}, [reason], "REJECTED")
    return AggregateResult(tre_id=tre_id, n=0, stats={}, rejected=[reason], region=region, spec_hash=spec.spec_hash())


def filter(tre_id: str, region: str, spec: AnalysisSpec, n: int, raw: dict[str, dict[str, Any]]):  # noqa: A001
    from adapters.base import AggregateResult

    k = effective_min_cell_size(spec.project_id, spec.min_cell_size)
    rejected: list[str] = []
    out: dict[str, dict[str, Any]] = {}

    if n < k:
        rejected.append(f"*:n={n}<{k}")
        _audit(tre_id, spec, n, {}, rejected, "REJECTED")
        return AggregateResult(tre_id=tre_id, n=0, stats={}, rejected=rejected, region=region, spec_hash=spec.spec_hash())

    for var, stats in raw.items():
        observed = _observed(stats)
        if observed is not None and observed < k:  # e.g. one person's value in an otherwise large cohort
            rejected.append(f"{var}.*:observed={observed}<{k}")
            out[var] = {}
            continue
        kept: dict[str, Any] = {}
        for key, val in stats.items():
            if key in ALLOWED_SCALARS:
                kept[key] = float(val) if key != "count" else int(val)
            elif key in ALLOWED_TABLES:
                table, cell_rejected = {}, False
                for level, c in val.items():
                    if c is None:  # suppressed by the TRE's own native rule (e.g. DataSHIELD nfilter.tab)
                        rejected.append(f"{var}.{key}.{level}:tre_native")
                        cell_rejected = True
                    elif c < k:
                        rejected.append(f"{var}.{key}.{level}:count={c}<{k}")
                        cell_rejected = True
                    else:
                        table[level] = int(c)
                kept[key] = table
                if key == "genotype_counts":
                    if cell_rejected:
                        rejected.append(f"{var}.allele_counts:derived_from_suppressed_cell")
                    else:
                        c0, c1, c2 = (table.get(g, 0) for g in ("0", "1", "2"))
                        kept["allele_counts"] = {"minor": c1 + 2 * c2, "major": 2 * c0 + c1, "n_alleles": 2 * (c0 + c1 + c2)}
            elif key in ALLOWED_MATRIX:
                p = len(val["cols"])
                if val["n"] < max(k, p + 1):
                    rejected.append(f"{var}.{key}:n={val['n']}<max({k},{p + 1})")
                else:
                    kept[key] = val
            else:
                rejected.append(f"{var}.{key}:not_allow_listed")
        out[var] = kept

    decision = "OK" if not rejected else "PARTIAL"
    _audit(tre_id, spec, n, out, rejected, decision)
    return AggregateResult(tre_id=tre_id, n=n, stats=out, rejected=rejected, region=region, spec_hash=spec.spec_hash())


def audit_release(tre_id: str, spec: AnalysisSpec, n: int, released: dict[str, list[str]], note: str) -> None:
    """Audit something derived inside the TRE from an already-filtered result
    (e.g. per-round model parameters in federated learning)."""
    _audit(tre_id, spec, n, {k: {x: None for x in v} for k, v in released.items()}, [], f"OK:{note}")


def release_irls_step(tre_id: str, spec: AnalysisSpec, step: Any, p: int) -> dict:
    """Safe Output for one Newton-Raphson round from one site: the adapter's
    irls_step answer (n, complete, grad, hess) at a p-parameter beta. Returns
    only {n, grad, hess}; raises Rejected (after auditing) otherwise. Same
    sample rule as a Gram matrix over [intercept, outcome, *features]."""
    n = step.get("n") if isinstance(step, dict) else None

    def reject(reason: str):
        _audit(tre_id, spec, n if type(n) is int else 0, {}, [f"_logreg.step:{reason}"], "REJECTED")
        raise Rejected(reason)

    if type(n) is not int or n < 0:
        reject("invalid_n")
    if step.get("complete") is not True:
        reject("incomplete_inputs")
    grad, hess = step.get("grad"), step.get("hess")
    if (not isinstance(grad, list) or len(grad) != p or not isinstance(hess, list) or len(hess) != p
            or any(not isinstance(row, list) or len(row) != p for row in hess)):
        reject("invalid_shape")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in [*grad, *(v for row in hess for v in row)]):
        reject("non_finite")
    needed = max(effective_min_cell_size(spec.project_id, spec.min_cell_size), p + 2)
    if n < needed:
        reject(f"n={n}<{needed}")
    audit_release(tre_id, spec, n, {"_logreg": ["grad", "hess"]}, "newton_raphson_round")
    return {"n": n, "grad": grad, "hess": hess}
