#!/usr/bin/env python
"""Synthetic cohort: genotypes + phenotypes, split non-IID across the sites in
sites.yaml, each site saved with its OWN local column names (from
harmonisation/canonical.yaml). Ground truth (global allele frequencies, global
OLS coefficients) is computed on the pooled data BEFORE splitting.

Run:  python data/generate.py [--n 30000] [--seed 7]
Out:  data/sites/<tre_id>.csv, data/ground_truth.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harmonisation import load_canonical, local_map  # noqa: E402
from scripts.sites import load_sites  # noqa: E402

OUTCOME = "sbp"
COVARIATES = ["age", "sex", "bmi", "ldl"]
# Causal SNPs for the regression outcome (effect per minor allele, mmHg)
CAUSAL = {"snp_rs001": 2.5, "snp_rs007": -1.5, "snp_rs013": 1.0}


def generate(n: int, seed: int, sites: list[dict]) -> tuple[pd.DataFrame, dict]:
    """Pooled cohort with a `site` column. Non-IID by design:
    - unequal site sizes (sites.yaml `weight`)
    - age skew: earlier sites younger, later sites older
    - allele-frequency drift: each site's MAF is the global MAF times a site factor
    """
    rng = np.random.default_rng(seed)
    canon = load_canonical()
    snps = [v for v, d in canon.items() if d["type"] == "genotype"]
    ids = [s["tre_id"] for s in sites]

    w = np.array([s["weight"] for s in sites], dtype=float)
    w /= w.sum()
    site = rng.choice(ids, size=n, p=w)
    site_idx = np.searchsorted(ids, site, sorter=np.argsort(ids))
    rank = {t: i for i, t in enumerate(ids)}
    age_shift = np.array([(rank[t] - (len(ids) - 1) / 2) * 6.0 for t in site])  # -6..+6 years for 3 sites

    df = pd.DataFrame({"site": site})
    df["age"] = np.clip(rng.normal(55, 12, n) + age_shift, 18, 95).round(0)
    df["sex"] = rng.integers(0, 2, n)
    df["bmi"] = np.clip(rng.normal(26.5, 4.5, n), 15, 55).round(1)
    df["ldl"] = np.clip(rng.normal(3.4, 0.9, n), 0.5, 9).round(2)

    maf = {s: float(rng.uniform(0.05, 0.45)) for s in snps}
    drift = {t: float(rng.uniform(0.7, 1.3)) for t in ids}  # per-site MAF multiplier
    for s in snps:
        p = np.clip(np.array([maf[s] * drift[t] for t in site]), 0.01, 0.5)
        df[s] = rng.binomial(2, p)  # HWE within site, minor-allele dosage

    beta = {"intercept": 90.0, "age": 0.5, "sex": 4.0, "bmi": 0.8, "ldl": 1.2, **CAUSAL}
    y = beta["intercept"] + sum(beta[c] * df[c] for c in COVARIATES + list(CAUSAL)) + rng.normal(0, 10, n)
    df[OUTCOME] = y.round(1)
    return df, {"maf_true": maf, "maf_site_drift": drift, "beta_true": beta}


def split(df: pd.DataFrame, sites: list[dict]) -> dict[str, pd.DataFrame]:
    return {s["tre_id"]: df[df["site"] == s["tre_id"]].drop(columns="site") for s in sites}


def ground_truth(df: pd.DataFrame, parts: dict[str, pd.DataFrame], truth: dict) -> dict:
    snps = [c for c in df.columns if c.startswith("snp_")]
    allele_freq = {s: float(df[s].sum() / (2 * len(df))) for s in snps}
    allele_counts = {s: {str(g): int((df[s] == g).sum()) for g in (0, 1, 2)} for s in snps}
    X = np.column_stack([np.ones(len(df))] + [df[c].to_numpy(float) for c in COVARIATES + list(CAUSAL)])
    coef, *_ = np.linalg.lstsq(X, df[OUTCOME].to_numpy(float), rcond=None)
    names = ["intercept"] + COVARIATES + list(CAUSAL)
    return {
        "n_total": int(len(df)),
        "n_per_site": {k: int(len(v)) for k, v in parts.items()},
        "allele_freq": allele_freq,
        "allele_counts": allele_counts,
        "maf_true": truth["maf_true"],
        "maf_site_drift": truth["maf_site_drift"],
        "site_allele_freq": {k: {s: float(v[s].sum() / (2 * len(v))) for s in snps} for k, v in parts.items()},
        "ols": {"outcome": OUTCOME, "features": names[1:], "coef": dict(zip(names, map(float, coef)))},
        "beta_true": truth["beta_true"],
        "mean": {c: float(df[c].mean()) for c in COVARIATES + [OUTCOME]},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=ROOT / "data")
    args = ap.parse_args()

    sites = load_sites()["sites"]
    df, truth = generate(args.n, args.seed, sites)
    parts = split(df, sites)
    df = df.drop(columns="site")

    (args.out / "sites").mkdir(parents=True, exist_ok=True)
    for s in sites:
        tid = s["tre_id"]
        local = parts[tid].rename(columns=local_map(tid))  # canonical -> this site's local names
        local.insert(0, "row_id", [f"{tid}-{i:06d}" for i in range(len(local))])
        path = args.out / "sites" / f"{tid}.csv"
        local.to_csv(path, index=False)
        if len(sites) <= 10:
            print(f"{path}: {len(local)} rows, cols e.g. {list(local.columns[:6])}")

    gt = ground_truth(df, parts, truth)
    (args.out / "ground_truth.json").write_text(json.dumps(gt, indent=2))
    print(f"{args.out / 'ground_truth.json'}: n={gt['n_total']} over {len(sites)} sites, {len(gt['allele_freq'])} SNPs, OLS coef {gt['ols']['coef']}")


if __name__ == "__main__":
    main()
