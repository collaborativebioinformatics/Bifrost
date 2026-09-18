"""Federated logistic regression maths. Pure numpy, no I/O, no FLARE.

Unlike OLS, the logistic log-likelihood is not quadratic in beta, so a single
Gram matrix isn't enough: the gradient and Hessian both depend on the current
beta through p = sigmoid(X @ beta). We use distributed Newton-Raphson (IRLS):

  round 0  each site's cohort is validated (count / Safe Output gate) via the
           usual adapter.run() path -- same as fed_linreg's init step.
  round r  every site receives the current global beta, computes its LOCAL
           gradient and Hessian of the logistic log-likelihood at that beta
           (never exposing rows -- only a (p+1) vector and a (p+1)x(p+1)
           matrix leave), and the server sums them:

              grad = sum_i grad_i          hess = sum_i hess_i
              beta <- beta + hess^-1 @ grad

           Summing per-sample gradients/Hessians across disjoint sites before
           taking a Newton step is exactly one step of Newton's method on the
           pooled log-likelihood, so this converges to the same beta as a
           plain (unfederated) logistic regression on the pooled data.

No standardisation is applied (unlike linreg's fedavg mode): each Newton step
already accounts for the local curvature (the Hessian), so it does not need
rescaled features to converge well.
"""
from __future__ import annotations

import numpy as np


def newton_update(beta: list[float], grad, hess, ridge: float = 1e-6) -> list[float]:
    """One global Newton step from summed local gradient/Hessian. `ridge` is a
    small damping term so a still near-singular pooled Hessian (e.g. very few
    events) doesn't blow up early rounds."""
    p = len(beta)
    H = np.asarray(hess, dtype=float) + ridge * np.eye(p)
    delta = np.linalg.solve(H, np.asarray(grad, dtype=float))
    return (np.asarray(beta, dtype=float) + delta).tolist()


def combine(steps: list[dict]) -> tuple[int, np.ndarray, np.ndarray]:
    """Sum n/grad/hess across sites that answered this round."""
    n = sum(s["n"] for s in steps)
    grad = sum(np.asarray(s["grad"], dtype=float) for s in steps)
    hess = sum(np.asarray(s["hess"], dtype=float) for s in steps)
    return n, grad, hess


def max_abs_delta(beta_old: list[float], beta_new: list[float]) -> float:
    return float(np.max(np.abs(np.asarray(beta_new) - np.asarray(beta_old))))
