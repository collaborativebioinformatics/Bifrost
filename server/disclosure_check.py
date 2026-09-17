"""Server-side disclosure check on the MERGED result (N-aware).

  k-anonymity   every released count cell in the merged table >= k
  dominance     no single site contributes more than `dominance` of a cell
                (a near-single-contributor cell is effectively that site's own output)
  min sites     a federated release must combine >= min_sites sites
  site suppression  any site's Safe Output filter suppressed something: the
                merged table is incomplete and a human should see why
  differencing  a previous release with the same analysis + variables whose n
                differs by < k lets the two be subtracted to a small cell; looked
                up in release_log.jsonl by spec hash / signature

Returns {"decision": "OK" | "FLAGGED", "reasons": [...]}. FLAGGED results go to
the overseer queue; nothing is released until a human approves.
"""
from __future__ import annotations

import json
from pathlib import Path


def _signature(spec: dict) -> str:
    vars_ = sorted(spec.get("variables", []) + ([spec["outcome"]] if spec.get("outcome") else []))
    return f"{spec['analysis_type']}|{','.join(vars_)}"


def check(merged: dict, spec: dict, release_log: Path | None = None, *, dominance: float = 0.9, min_sites: int = 2) -> dict:
    k = int(spec.get("min_cell_size", 5))
    reasons: list[str] = []

    if len(merged["sites_reported"]) < min_sites:
        reasons.append(f"min_sites:{len(merged['sites_reported'])}<{min_sites}")

    for site, rejected in merged.get("rejected_per_site", {}).items():
        reasons.append(f"site_suppression:{site}:{len(rejected)}")

    for var, st in merged["stats"].items():
        for table in ("genotype_counts", "histogram"):
            for lvl, c in (st.get(table) or {}).items():
                if c < k:
                    reasons.append(f"k_anon:{var}.{table}.{lvl}={c}<{k}")
                contrib = merged.get("contributions", {}).get(var, {}).get(lvl, {})
                if c and contrib:
                    top_site, top = max(contrib.items(), key=lambda kv: kv[1])
                    if len(contrib) > 1 and top / c > dominance:
                        reasons.append(f"dominance:{var}.{table}.{lvl}:{top_site}={top / c:.2f}>{dominance}")
        if st.get("count") is not None and st["count"] < k:
            reasons.append(f"k_anon:{var}.count={st['count']}<{k}")

    if release_log and release_log.exists():
        sig, n = _signature(spec), merged["n"]
        for line in release_log.read_text().splitlines():
            prev = json.loads(line)
            if prev.get("signature") == sig and prev.get("spec_hash") != merged.get("spec_hash"):
                if 0 < abs(prev["n"] - n) < k:
                    reasons.append(f"differencing:prev={prev['spec_hash']}:|{prev['n']}-{n}|<{k}")

    return {"decision": "OK" if not reasons else "FLAGGED", "reasons": reasons, "signature": _signature(spec)}
