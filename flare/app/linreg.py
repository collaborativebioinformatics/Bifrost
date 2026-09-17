"""Federated linear regression maths. Pure numpy, no I/O, no FLARE.

A site never exposes rows; its adapter returns the Gram matrix
G = [1, y, X]ᵀ[1, y, X] (an allow-listed aggregate). Two ways to get β:

  exact   server sums the Gram matrices and solves the normal equations
          (one round; what server/aggregate.combine already does)
  fedavg  only the model leaves each site, every round:
          round 0  each site reports n and feature sums / sums of squares
                   -> server fixes ONE global standardisation (mean, std)
          round r  site receives global β, runs `local_steps` full-batch
                   gradient steps on ITS OWN loss in standardised space,
                   returns (n_i, β_i); server takes the n-weighted mean.
          With local_steps=1 this is exactly gradient descent on the pooled
          least-squares loss, so it converges to the same β as `exact`.

Full-batch gradient steps on a site's sufficient statistics are identical to
what sklearn's SGDRegressor.partial_fit would do on that site's rows with a
full batch -- the rows just never have to be visible to the FLARE client.
"""
from __future__ import annotations

import numpy as np


def split(gram: dict) -> tuple[int, np.ndarray, np.ndarray, np.ndarray, float, list[str]]:
    """gram cols = [intercept, outcome, *features] -> n, s (Σx), S (XᵀX), t (Σxy), u (Σy), features."""
    G = np.asarray(gram["matrix"], dtype=float)
    n = int(round(G[0, 0]))
    s, u = G[0, 2:], G[0, 1]
    S, t = G[2:, 2:], G[2:, 1]
    return n, s, S, t, u, list(gram["cols"][2:])


def moments(gram: dict) -> dict:
    """What a site reports in round 0: enough for a global mean/std, nothing more."""
    n, s, S, _, _, feats = split(gram)
    return {"n": n, "features": feats, "sum": s.tolist(), "sum_sq": np.diag(S).tolist()}


def global_scaling(reports: list[dict]) -> dict:
    n = sum(r["n"] for r in reports)
    s = sum(np.asarray(r["sum"]) for r in reports)
    ss = sum(np.asarray(r["sum_sq"]) for r in reports)
    mean = s / n
    std = np.sqrt(np.maximum(ss / n - mean**2, 1e-12))
    return {"n": n, "features": reports[0]["features"], "mean": mean.tolist(), "std": std.tolist()}


def standardised_normal_eq(gram: dict, mean, std) -> tuple[int, np.ndarray, np.ndarray]:
    """Gram of [1, (X - mean)/std] and its Xᵀy, from the raw Gram -- no rows needed."""
    n, s, S, t, u, _ = split(gram)
    m, d = np.asarray(mean, float), np.asarray(std, float)
    Dinv = np.diag(1.0 / d)
    sc = s - n * m                                        # 1ᵀ X_c
    Sc = S - np.outer(s, m) - np.outer(m, s) + n * np.outer(m, m)  # X_cᵀ X_c
    tc = t - m * u                                        # X_cᵀ y
    p = len(s)
    A = np.zeros((p + 1, p + 1))
    A[0, 0] = n
    A[0, 1:] = A[1:, 0] = Dinv @ sc
    A[1:, 1:] = Dinv @ Sc @ Dinv
    b = np.concatenate([[u], Dinv @ tc])
    return n, A, b


def local_update(gram: dict, beta: list[float], mean, std, lr: float, steps: int) -> dict:
    """`steps` full-batch gradient steps on this site's MSE, starting from the global β."""
    n, A, b = standardised_normal_eq(gram, mean, std)
    beta = np.asarray(beta, float)
    for _ in range(steps):
        beta = beta - lr * (A @ beta - b) / n
    return {"n": n, "beta": beta.tolist()}


def fedavg(updates: list[dict]) -> list[float]:
    n = sum(u["n"] for u in updates)
    return (sum(u["n"] * np.asarray(u["beta"]) for u in updates) / n).tolist()


def unstandardise(beta: list[float], mean, std) -> list[float]:
    """β in standardised space -> β on the original feature scale."""
    b = np.asarray(beta, float)
    m, d = np.asarray(mean, float), np.asarray(std, float)
    coef = b[1:] / d
    intercept = b[0] - float(coef @ m)
    return [intercept, *coef.tolist()]


def exact(grams: list[dict]) -> list[float]:
    G = sum(np.asarray(g["matrix"], float) for g in grams)
    idx = [0, *range(2, G.shape[0])]
    return np.linalg.solve(G[np.ix_(idx, idx)], G[idx, 1]).tolist()
