"""Safe Output filter -- runs INSIDE the TRE, before anything reaches the FLARE client.

Rules (all decisions are appended to audit.jsonl):
  1. project_id must be on the Safe Projects allow-list (projects.yaml)
  2. only allow-listed aggregate keys leave; anything else is dropped
  3. if the filtered cohort has n < min_cell_size, nothing leaves
  4. any count cell < min_cell_size is suppressed; if a genotype cell is
     suppressed, derived allele counts for that variable are withheld too
     (they would let the cell be back-calculated)
  5. a regression Gram matrix leaves only if n >= max(min_cell_size, #cols + 1)
"""
from __future__ import annotations

import json
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


def project_allowed(project_id: str, tre_id: str) -> bool:
    if not PROJECTS_PATH.exists():
        return False
    projects = yaml.safe_load(PROJECTS_PATH.read_text()).get("projects") or {}
    p = projects.get(project_id)
    if p is None:
        return False
    sites = p.get("sites", "all")
    return sites == "all" or tre_id in sites


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
        "min_cell_size": spec.min_cell_size,
        "n": n,
        "released": {v: sorted(s.keys()) for v, s in released.items()},
        "rejected": rejected,
        "decision": decision,
    }
    with open(audit_path(tre_id), "a") as f:
        f.write(json.dumps(entry) + "\n")


def reject_all(tre_id: str, region: str, spec: AnalysisSpec, reason: str):
    from adapters.base import AggregateResult  # local import: base imports this module

    _audit(tre_id, spec, 0, {}, [reason], "REJECTED")
    return AggregateResult(tre_id=tre_id, n=0, stats={}, rejected=[reason], region=region, spec_hash=spec.spec_hash())


def filter(tre_id: str, region: str, spec: AnalysisSpec, n: int, raw: dict[str, dict[str, Any]]):  # noqa: A001
    from adapters.base import AggregateResult

    k = spec.min_cell_size
    rejected: list[str] = []
    out: dict[str, dict[str, Any]] = {}

    if n < k:
        rejected.append(f"*:n={n}<{k}")
        _audit(tre_id, spec, n, {}, rejected, "REJECTED")
        return AggregateResult(tre_id=tre_id, n=0, stats={}, rejected=rejected, region=region, spec_hash=spec.spec_hash())

    for var, stats in raw.items():
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
