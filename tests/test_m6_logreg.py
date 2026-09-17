"""M6: federated logistic regression (Newton-Raphson / IRLS). Needs one new
adapter primitive (irls_step) since, unlike OLS, the logistic loss isn't
quadratic in beta -- each round must be evaluated fresh at the current beta."""
import json

import numpy as np
import pytest

from flare.app import logreg
from spec.analysis_spec import AnalysisSpec
from tests.conftest import ROOT

GT = json.loads((ROOT / "data" / "ground_truth.json").read_text())
SPEC = AnalysisSpec.model_validate_json((ROOT / "spec" / "examples" / "fed_logreg.json").read_text())
FEATURES = SPEC.variables


def _truth():
    return np.array([GT["logreg"]["coef"][k] for k in ("intercept", *FEATURES)])


def _step(adapter, beta):
    local_feats = [adapter.local(c) for c in FEATURES]
    outcome = adapter.local(SPEC.outcome)
    filters = adapter.local_filters(SPEC)
    return adapter.irls_step(outcome, local_feats, beta, filters)


# ---- pure math -------------------------------------------------------------------------
def test_newton_update_matches_manual_solve():
    beta = [0.0, 0.0]
    grad, hess = [1.0, 2.0], [[4.0, 0.0], [0.0, 4.0]]
    got = logreg.newton_update(beta, grad, hess, ridge=0.0)
    assert np.allclose(got, [0.25, 0.5])


def test_combine_sums_across_sites():
    steps = [{"n": 10, "grad": [1.0, 2.0], "hess": [[1.0, 0.0], [0.0, 1.0]]},
             {"n": 20, "grad": [3.0, 4.0], "hess": [[2.0, 0.0], [0.0, 2.0]]}]
    n, grad, hess = logreg.combine(steps)
    assert n == 30
    assert np.allclose(grad, [4.0, 6.0])
    assert np.allclose(hess, [[3.0, 0.0], [0.0, 3.0]])


def test_max_abs_delta():
    assert logreg.max_abs_delta([0.0, 1.0], [0.1, 1.5]) == pytest.approx(0.5)


# ---- adapter primitive: same shape across REST / DataSHIELD / SQL ---------------------
def test_irls_step_same_shape_across_adapters(adapters):
    beta = [0.0] * (len(FEATURES) + 1)
    steps = {tid: _step(a, beta) for tid, a in adapters.items()}
    shapes = {(len(s["grad"]), len(s["hess"]), len(s["hess"][0])) for s in steps.values()}
    assert shapes == {(len(FEATURES) + 1, len(FEATURES) + 1, len(FEATURES) + 1)}
    for tid, s in steps.items():
        assert s["n"] == GT["n_per_site"][tid]


def test_irls_step_at_zero_matches_closed_form(adapters):
    """At beta=0, p=0.5 everywhere, so grad = X^T(y - 0.5) and hess = 0.25 * X^T X --
    both computable from the plain (unweighted) Gram matrix already used for OLS."""
    beta = [0.0] * (len(FEATURES) + 1)
    for a in adapters.values():
        step = _step(a, beta)
        gram = a.gram([a.local(c) for c in [SPEC.outcome, *FEATURES]], a.local_filters(SPEC))
        G = np.array(gram["matrix"])  # cols = [1, y, *features]
        idx = [0, *range(2, G.shape[0])]  # [1, *features], dropping the y column/row
        grad0 = G[idx, 1] - 0.5 * G[idx, 0]  # X^T y - 0.5 * X^T 1
        hess0 = 0.25 * G[np.ix_(idx, idx)]   # 0.25 * X^T X
        assert np.allclose(step["grad"], grad0, atol=1e-6)
        assert np.allclose(step["hess"], hess0, atol=1e-6)


# ---- full federation converges to the pooled MLE ---------------------------------------
def test_newton_raphson_converges_to_ground_truth(adapters):
    beta = [0.0] * (len(FEATURES) + 1)
    errs = []
    for _ in range(25):
        steps = [_step(a, beta) for a in adapters.values()]
        n, grad, hess = logreg.combine(steps)
        beta = logreg.newton_update(beta, grad, hess)
        errs.append(np.max(np.abs(np.array(beta) - _truth())))
        if errs[-1] < 1e-10:
            break
    assert errs[-1] < 1e-8
    assert errs[0] > errs[len(errs) // 2] > errs[-1]  # monotone-ish convergence


def test_min_clients_gate_matches_analysis_controller(adapters):
    """A cohort too small to pass min_cell_size shouldn't be usable to seed a round."""
    spec = AnalysisSpec(analysis_type="fed_logreg", variables=FEATURES, outcome="case",
                        filters={"age": {">=": 94}, "bmi": {">=": 40}}, project_id=SPEC.project_id)
    for a in adapters.values():
        r = a.run(spec)
        assert r.n == 0
