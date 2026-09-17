#!/usr/bin/env python
"""Run one AnalysisSpec against every site in sites.yaml *without* FLARE
(adapters called directly over HTTP) and combine. Handy for adapter dev and for
checking a spec before submitting it as a FLARE job.

  python scripts/dev_tres.py &                        # start the TREs
  python scripts/run_local.py spec/examples/allele_freq.json
  python scripts/run_local.py - <<< '{"analysis_type":"allele_freq","variables":["snp_rs001"],"project_id":"ncfh-2026-demo"}'
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from adapters import registry  # noqa: E402
from adapters.base import AggregateResult  # noqa: E402
from scripts.sites import load_sites  # noqa: E402
from spec.analysis_spec import AnalysisSpec  # noqa: E402


def combine(results: list[AggregateResult]) -> dict:
    """Associative merge: sums of counts / sums / Gram matrices. Same code the server uses."""
    import numpy as np

    out: dict = {"n_sites": len(results), "sites": {r.tre_id: r.n for r in results}, "n": sum(r.n for r in results),
                 "rejected": {r.tre_id: r.rejected for r in results if r.rejected}, "stats": {}}
    vars_ = {v for r in results for v in r.stats}
    for v in sorted(vars_):
        parts = [r.stats[v] for r in results if v in r.stats]
        agg: dict = {}
        if all("allele_counts" in p for p in parts):
            minor = sum(p["allele_counts"]["minor"] for p in parts)
            total = sum(p["allele_counts"]["n_alleles"] for p in parts)
            agg["allele_freq"] = minor / total
            agg["n_alleles"] = total
        if all("sum" in p for p in parts):
            n = sum(p["count"] for p in parts)
            s, ss = sum(p["sum"] for p in parts), sum(p["sum_sq"] for p in parts)
            agg.update(count=n, mean=s / n, var=(ss - s * s / n) / (n - 1), min=min(p["min"] for p in parts), max=max(p["max"] for p in parts))
        if all("gram" in p for p in parts):
            G = sum(np.array(p["gram"]["matrix"]) for p in parts)
            cols = parts[0]["gram"]["cols"]
            idx = [0, *range(2, len(cols))]  # intercept + features; column 1 is the outcome
            beta = np.linalg.solve(G[np.ix_(idx, idx)], G[idx, 1])
            agg["ols"] = dict(zip([cols[i] for i in idx], map(float, beta)))
            agg["n"] = int(sum(p["gram"]["n"] for p in parts))
        out["stats"][v] = agg
    return out


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else "-"
    spec = AnalysisSpec.model_validate_json(sys.stdin.read() if src == "-" else Path(src).read_text())
    results = []
    for s in load_sites()["sites"]:
        a = registry.load(s["tre_id"])
        r = a.run(spec)
        results.append(r)
        print(f"{r.tre_id:8s} n={r.n:6d} released={sorted(r.stats)} rejected={r.rejected}", file=sys.stderr)
    print(json.dumps(combine(results), indent=2))


if __name__ == "__main__":
    main()
