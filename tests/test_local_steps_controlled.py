"""Controlled comparison: hold learning rate and round count fixed, vary only `local_steps`.

The two FedAvg tests in test_m4_linreg.py differ in learning rate, local steps and round
count simultaneously (1.0/1/15 versus 0.5/5/60), so neither isolates the effect of local
steps. This one does, and pins the measured value at every checkpoint so that a change in
the optimiser shows up as a test failure rather than a silently different number.

Values recorded in docs/local_steps_controlled.md; keep the two in step.
"""
import json

import numpy as np
import pytest

from flare.app import linreg
from spec.analysis_spec import AnalysisSpec
from tests.conftest import ROOT

GT = json.loads((ROOT / "data" / "ground_truth.json").read_text())
SPEC = AnalysisSpec.model_validate_json((ROOT / "spec" / "examples" / "fed_linreg.json").read_text())

# (lr, rounds, local_steps, expected max abs coefficient deviation from pooled OLS)
CHECKPOINTS = [
    (1.0, 10, 1, 2.171e-11),
    (1.0, 50, 1, 1.633e-11),
    (1.0, 200, 1, 1.633e-11),
    (1.0, 10, 5, 2.296e-02),
    (1.0, 50, 5, 2.296e-02),
    (1.0, 200, 5, 2.296e-02),
    (0.5, 10, 1, 8.791e-02),   # not yet converged at this round count
    (0.5, 60, 1, 1.633e-11),
    (0.5, 200, 1, 1.633e-11),
    (0.5, 10, 5, 2.386e-02),
    (0.5, 60, 5, 2.386e-02),
    (0.5, 200, 5, 2.386e-02),
]


def _truth():
    return np.array([GT["ols"]["coef"][k] for k in ("intercept", *GT["ols"]["features"])])


def _deviation(grams, scaling, lr, steps, rounds) -> float:
    beta = [0.0] * (len(scaling["features"]) + 1)
    for _ in range(rounds):
        beta = linreg.fedavg([linreg.local_update(g, beta, scaling["mean"], scaling["std"], lr=lr, steps=steps)
                              for g in grams])
    return float(np.abs(np.array(linreg.unstandardise(beta, scaling["mean"], scaling["std"])) - _truth()).max())


@pytest.fixture(scope="module")
def grams(adapters):
    return [a.run(SPEC).stats["_linreg"]["gram"] for a in adapters.values()]


@pytest.fixture(scope="module")
def scaling(grams):
    return linreg.global_scaling([linreg.moments(g) for g in grams])


@pytest.mark.parametrize("lr,rounds,steps,expected", CHECKPOINTS,
                         ids=[f"lr{lr}-r{r}-s{s}" for lr, r, s, _ in CHECKPOINTS])
def test_checkpoint_matches_recorded_value(grams, scaling, lr, rounds, steps, expected):
    got = _deviation(grams, scaling, lr, steps, rounds)
    if expected < 1e-9:
        # at numerical precision; the recorded digits are not meaningful, the magnitude is
        assert got < 1e-9, f"lr={lr} rounds={rounds} steps={steps}: {got:.3e} not converged"
    else:
        assert got == pytest.approx(expected, rel=0.05), f"lr={lr} rounds={rounds} steps={steps}: {got:.3e}"


@pytest.mark.parametrize("lr,rounds", [(1.0, 10), (1.0, 50), (1.0, 200), (0.5, 60), (0.5, 200)])
def test_one_step_converges_where_five_does_not(grams, scaling, lr, rounds):
    """The controlled claim: same lr, same rounds, only local_steps differs."""
    one = _deviation(grams, scaling, lr, 1, rounds)
    five = _deviation(grams, scaling, lr, 5, rounds)
    assert one < 1e-9 < 1e-3 < five, f"lr={lr} rounds={rounds}: one={one:.3e} five={five:.3e}"


def test_five_step_residual_unchanged_across_round_counts(grams, scaling):
    """Five-step residual does not shrink with 20x more rounds, at displayed precision.

    This is an observation at the tested checkpoints, not a proof of a fixed point.
    """
    for lr, rounds in [(1.0, (10, 50, 200)), (0.5, (10, 60, 200))]:
        values = [_deviation(grams, scaling, lr, 5, r) for r in rounds]
        assert values[0] == pytest.approx(values[-1], rel=1e-3), f"lr={lr}: {values}"
