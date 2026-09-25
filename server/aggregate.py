"""Server-side merge of AggregateResults. Everything here is a SUM (counts,
sums, Gram matrices) -- associative and order-independent -- so a regional
relay can pre-merge its region's sites and the root merges relays with the
same function. No row-level data ever enters this module."""
from __future__ import annotations

from typing import Iterable

import numpy as np

from adapters.base import AggregateResult


def combine(results: Iterable[AggregateResult], expected_sites: list[str] | None = None) -> dict:
    results = list(results)
    reported = [r.tre_id for r in results]
    expected = expected_sites or reported
    out: dict = {
        "sites_expected": expected,
        "sites_reported": reported,
        "sites_missing": [s for s in expected if s not in reported],
        "coverage": f"{len(reported)}/{len(expected)} sites",
        "n": int(sum(r.n for r in results)),
        "n_per_site": {r.tre_id: r.n for r in results},
        "region_per_site": {r.tre_id: r.region for r in results},
        "rejected_per_site": {r.tre_id: r.rejected for r in results if r.rejected},
        "spec_hash": next((r.spec_hash for r in results if r.spec_hash), ""),
        "stats": {},
        # var -> cell -> {site: count}; used by the dominance check, never released. Cells are
        # genotype levels, "count" (observed values of a scalar variable) or "n" (regression sample)
        "contributions": {},
    }
    variables = sorted({v for r in results for v in r.stats})
    for v in variables:
        parts = {r.tre_id: r.stats[v].present() for r in results if v in r.stats}
        agg: dict = {}
        if parts and all("genotype_counts" in p for p in parts.values()):
            levels = sorted({lvl for p in parts.values() for lvl in p["genotype_counts"]})
            agg["genotype_counts"] = {lvl: int(sum(p["genotype_counts"].get(lvl, 0) for p in parts.values())) for lvl in levels}
            out["contributions"][v] = {lvl: {s: p["genotype_counts"].get(lvl, 0) for s, p in parts.items()} for lvl in levels}
        ac_parts = {s: p["allele_counts"] for s, p in parts.items() if "allele_counts" in p}
        if ac_parts:
            minor = sum(a["minor"] for a in ac_parts.values())
            total = sum(a["n_alleles"] for a in ac_parts.values())
            agg["allele_counts"] = {"minor": int(minor), "major": int(total - minor), "n_alleles": int(total)}
            agg["allele_freq"] = minor / total if total else None
            agg["allele_freq_sites"] = sorted(ac_parts)
        if parts and all("sum" in p for p in parts.values()):
            n = sum(p["count"] for p in parts.values())
            s, ss = sum(p["sum"] for p in parts.values()), sum(p["sum_sq"] for p in parts.values())
            agg.update(count=int(n), sum=s, sum_sq=ss, mean=s / n if n else None,
                       var=(ss - s * s / n) / (n - 1) if n > 1 else None,
                       min=min(p["min"] for p in parts.values()), max=max(p["max"] for p in parts.values()))
            out["contributions"].setdefault(v, {})["count"] = {site: int(p["count"]) for site, p in parts.items()}
        if parts and all("gram" in p for p in parts.values()):
            G = sum(np.array(p["gram"]["matrix"]) for p in parts.values())
            cols = next(iter(parts.values()))["gram"]["cols"]
            idx = [0, *range(2, len(cols))]  # intercept + features; column 1 is the outcome
            try:
                beta = np.linalg.solve(G[np.ix_(idx, idx)], G[idx, 1])
                agg["ols"] = {"outcome": cols[1], "coef": dict(zip([cols[i] for i in idx], map(float, beta)))}
            except np.linalg.LinAlgError as e:
                agg["ols"] = {"error": str(e)}
            agg["n"] = int(sum(p["gram"]["n"] for p in parts.values()))
            out["contributions"].setdefault(v, {})["n"] = {site: int(p["gram"]["n"]) for site, p in parts.items()}
        out["stats"][v] = agg
    return out
