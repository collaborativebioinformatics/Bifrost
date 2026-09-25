"""Server-side disclosure check on the MERGED result (N-aware).

  k-anonymity   every released count cell in the merged table >= k, where k is
                the request's min_cell_size raised to the project's floor
  dominance     no single site contributes more than `dominance` of a cell, of a
                variable's observed values, of a regression's sample, or of any
                released federated round (a near-single-contributor release is
                effectively that site's own output)
  min sites     a federated release must combine >= min_sites sites, and so must
                every released round of an iterative fit
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

from adapters.safe_output import effective_min_cell_size


def _signature(spec: dict) -> str:
    vars_ = sorted(spec.get("variables", []) + ([spec["outcome"]] if spec.get("outcome") else []))
    return f"{spec['analysis_type']}|{','.join(vars_)}"


def _dominant(contrib: dict[str, int], dominance: float) -> tuple[str, float] | None:
    """(site, share) when one of several contributing sites holds more than `dominance`."""
    total = sum(contrib.values())
    if len(contrib) < 2 or not total:
        return None
    site, top = max(contrib.items(), key=lambda kv: kv[1])
    return (site, top / total) if top / total > dominance else None


def check(merged: dict, spec: dict, release_log: Path | None = None, *, dominance: float = 0.9, min_sites: int = 2) -> dict:
    k = effective_min_cell_size(spec.get("project_id"), int(spec.get("min_cell_size", 5)))
    contributions = merged.get("contributions", {})
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
                if hit := _dominant(contributions.get(var, {}).get(lvl, {}), dominance):
                    reasons.append(f"dominance:{var}.{table}.{lvl}:{hit[0]}={hit[1]:.2f}>{dominance}")
        if st.get("count") is not None and st["count"] < k:
            reasons.append(f"k_anon:{var}.count={st['count']}<{k}")
        for cell in ("count", "n"):  # a variable's observed values; a regression's sample
            if hit := _dominant(contributions.get(var, {}).get(cell, {}), dominance):
                reasons.append(f"dominance:{var}.{cell}:{hit[0]}={hit[1]:.2f}>{dominance}")

    # every round's coefficients are released (history), so every round is checked
    rounds = contributions.get("_rounds", {})
    if thin := [(r, len(contrib)) for r, contrib in rounds.items() if len(contrib) < min_sites]:
        r, count = min(thin, key=lambda x: x[1])
        reasons.append(f"min_sites:round={r}:{count}<{min_sites}")
    per_round = [(r, *hit) for r, contrib in rounds.items() if (hit := _dominant(contrib, dominance))]
    if per_round:
        r, site, share = max(per_round, key=lambda x: x[2])
        reasons.append(f"dominance:round={r}:{site}={share:.2f}>{dominance}")

    if release_log and release_log.exists():
        sig, n = _signature(spec), merged["n"]
        for line in release_log.read_text().splitlines():
            prev = json.loads(line)
            if prev.get("signature") == sig and prev.get("spec_hash") != merged.get("spec_hash"):
                if 0 < abs(prev["n"] - n) < k:
                    reasons.append(f"differencing:prev={prev['spec_hash']}:|{prev['n']}-{n}|<{k}")

    return {"decision": "OK" if not reasons else "FLAGGED", "reasons": reasons, "signature": _signature(spec)}
