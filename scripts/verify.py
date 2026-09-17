#!/usr/bin/env python
"""Compare a federated result with data/ground_truth.json.

  python scripts/verify.py server/out/<spec_hash>/result.json [--tol 1e-9]

Exit 1 if any released statistic deviates more than --tol from the pooled-data
truth (allele frequencies exact; OLS coefficients exact up to float error).
Sites missing from the run are reported: with partial coverage the federated
value is compared to the truth restricted to the reporting sites where possible.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("result", type=Path)
    ap.add_argument("--truth", type=Path, default=ROOT / "data" / "ground_truth.json")
    ap.add_argument("--tol", type=float, default=1e-9)
    a = ap.parse_args()
    res, gt = json.loads(a.result.read_text()), json.loads(a.truth.read_text())

    print(f"coverage: {res['coverage']}  n={res['n']}  missing={res['sites_missing']}  sites={res['n_per_site']}")
    if res.get("method"):
        print(f"method: {res['method']}")
    if res.get("rejected_per_site"):
        print("suppressions:")
        for s, rej in res["rejected_per_site"].items():
            print(f"  {s}: {rej}")
    full = not res["sites_missing"]
    worst, rows = 0.0, []
    for var, st in res["stats"].items():
        if "allele_freq" in st and st["allele_freq"] is not None:
            sites = st.get("allele_freq_sites", res["sites_reported"])
            if full and set(sites) == set(res["sites_expected"]):
                truth = gt["allele_freq"][var]
            else:  # partial: recompute truth over the contributing sites
                num = sum(gt["site_allele_freq"][s][var] * 2 * gt["n_per_site"][s] for s in sites)
                truth = num / sum(2 * gt["n_per_site"][s] for s in sites)
            err = abs(st["allele_freq"] - truth)
            worst = max(worst, err)
            rows.append((f"{var} allele_freq", st["allele_freq"], truth, err, f"{len(sites)} sites"))
        if "mean" in st and st["mean"] is not None and var in gt["mean"] and full:
            err = abs(st["mean"] - gt["mean"][var])
            worst = max(worst, err)
            rows.append((f"{var} mean", st["mean"], gt["mean"][var], err, ""))
        if "ols" in st and "coef" in st["ols"] and full:
            for k, v in st["ols"]["coef"].items():
                t = gt["ols"]["coef"].get(k)
                if t is None:
                    continue
                err = abs(v - t)
                worst = max(worst, err)
                rows.append((f"ols {k}", v, t, err, ""))
        if "history" in st and full:
            print("fedavg convergence (max |coef - truth| per round):")
            for h in st["history"]:
                e = max(abs(v - gt["ols"]["coef"][k]) for k, v in h["coef"].items() if k in gt["ols"]["coef"])
                if h["round"] in (1, 2, 3, 5, 10, 20, 50, 100) or h["round"] == st["history"][-1]["round"]:
                    print(f"  round {h['round']:3d}  {h['sites']} sites  err {e:.2e}")

    print(f"{'statistic':28s} {'federated':>14s} {'ground truth':>14s} {'abs err':>10s}")
    for name, f, t, e, note in rows:
        fs = f"{f:14.6f}" if f is not None else f"{'-':>14s}"
        ts = f"{t:14.6f}" if t is not None else f"{'-':>14s}"
        print(f"{name:28s} {fs} {ts} {e:10.2e} {note}")
    ok = worst <= a.tol
    print(f"max abs error {worst:.2e} {'<=' if ok else '>'} tol {a.tol:.0e} -> {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
