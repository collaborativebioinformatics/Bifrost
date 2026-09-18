"""M4: federated linear regression through the SAME adapters (no adapter changes)."""
import json

import numpy as np
import pytest

from flare.app import linreg
from spec.analysis_spec import AnalysisSpec
from tests.conftest import ROOT

GT = json.loads((ROOT / "data" / "ground_truth.json").read_text())
SPEC = AnalysisSpec.model_validate_json((ROOT / "spec" / "examples" / "fed_linreg.json").read_text())


@pytest.fixture(scope="module")
def grams(adapters):
    out = [a.run(SPEC).stats["_linreg"].gram.model_dump() for a in adapters.values()]
    return out


def _truth():
    return np.array([GT["ols"]["coef"][k] for k in ("intercept", *GT["ols"]["features"])])


def test_exact_matches_ground_truth(grams):
    assert np.allclose(linreg.exact(grams), _truth(), atol=1e-6)


def test_moments_reveal_only_sums(grams):
    m = linreg.moments(grams[0])
    assert set(m) == {"n", "features", "sum", "sum_sq"} and len(m["sum"]) == len(m["features"])


def test_fedsgd_converges_to_exact_ols(grams):
    sc = linreg.global_scaling([linreg.moments(g) for g in grams])
    beta = [0.0] * (len(sc["features"]) + 1)
    errs = []
    for _ in range(15):
        beta = linreg.fedavg([linreg.local_update(g, beta, sc["mean"], sc["std"], lr=1.0, steps=1) for g in grams])
        errs.append(np.abs(np.array(linreg.unstandardise(beta, sc["mean"], sc["std"])) - _truth()).max())
    assert errs[-1] < 1e-8 and errs[0] > errs[4] > errs[-1]


def test_local_steps_gt1_shows_non_iid_bias(grams):
    """Classic FedAvg (several local steps) converges to a *different* point on non-IID sites."""
    sc = linreg.global_scaling([linreg.moments(g) for g in grams])
    beta = [0.0] * (len(sc["features"]) + 1)
    for _ in range(60):
        beta = linreg.fedavg([linreg.local_update(g, beta, sc["mean"], sc["std"], lr=0.5, steps=5) for g in grams])
    err = np.abs(np.array(linreg.unstandardise(beta, sc["mean"], sc["std"])) - _truth()).max()
    assert 1e-4 < err < 1.0


def test_unstandardise_roundtrip(grams):
    sc = linreg.global_scaling([linreg.moments(g) for g in grams])
    G = sum(np.array(g["matrix"]) for g in grams)
    n, A, b = zip(*[linreg.standardised_normal_eq(g, sc["mean"], sc["std"]) for g in grams])
    beta_std = np.linalg.solve(sum(A), sum(b))
    assert np.allclose(linreg.unstandardise(beta_std, sc["mean"], sc["std"]), linreg.exact(grams), atol=1e-8)
