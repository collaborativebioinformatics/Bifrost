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


# ---- controller: a run with no completed Newton round is not a result -----------------
def test_controller_does_not_record_a_zero_round_fit(monkeypatch, tmp_path, sites):
    """If no Newton round ever reaches min_clients, the controller must not hand the
    untouched all-zero beta to the disclosure check and overseer queue as a result."""
    from nvflare.apis.fl_context import FLContext
    from nvflare.apis.shareable import Shareable
    from nvflare.apis.signal import Signal

    from flare.app.logreg_controller import FedLogregController
    from server import overseer_queue

    monkeypatch.setenv("SERVER_OUT", str(tmp_path))
    tids = [s["tre_id"] for s in sites]
    p = len(FEATURES) + 1

    def reply(**fields):
        shareable = Shareable()
        shareable.update(fields)
        return shareable

    def fake_round(name, data, fl_ctx, abort_signal, targets=None):
        if name == "logreg_init":
            return {tid: reply(n=100) for tid in tids}
        return {tids[0]: reply(step={"n": 100, "grad": [0.0] * p, "hess": [[0.0] * p] * p})}  # 1 < min_clients

    recorded = []
    monkeypatch.setattr(overseer_queue, "record", lambda *args: recorded.append(args))
    ctl = FedLogregController(SPEC.model_dump(), rounds=3, min_clients=2)
    monkeypatch.setattr(ctl, "_round", fake_round)
    ctl.control_flow(Signal(), FLContext())
    assert recorded == []


def test_controller_records_through_the_result_contract(monkeypatch, tmp_path, sites, adapters):
    """A site that misses one round is recorded under a string round key, and the
    run written by overseer_queue.record satisfies MergedResult and matches the
    pooled MLE once every site is back."""
    from nvflare.apis.fl_context import FLContext
    from nvflare.apis.shareable import Shareable
    from nvflare.apis.signal import Signal

    from flare.app.logreg_controller import FedLogregController
    from server import overseer_queue
    from server.schemas import MergedResult

    monkeypatch.setenv("SERVER_OUT", str(tmp_path))
    tids = [s["tre_id"] for s in sites]
    calls = {"n": 0}

    def reply(**fields):
        shareable = Shareable()
        shareable.update(fields)
        return shareable

    def fake_round(name, data, fl_ctx, abort_signal, targets=None):
        if name == "logreg_init":
            return {tid: reply(n=GT["n_per_site"][tid]) for tid in tids}
        calls["n"] += 1
        answering = tids[:-1] if calls["n"] == 1 else tids  # the last site misses round 1 only
        return {tid: reply(step=_step(adapters[tid], data["beta"])) for tid in answering}

    ctl = FedLogregController(SPEC.model_dump(), rounds=25, min_clients=2)
    monkeypatch.setattr(ctl, "_round", fake_round)
    ctl.control_flow(Signal(), FLContext())
    res = json.loads((overseer_queue.out_dir() / SPEC.spec_hash() / "result.json").read_text())
    MergedResult.model_validate(res)
    assert res["sites_missing_rounds"] == {"1": [tids[-1]]}
    rounds = res["contributions"]["_rounds"]  # the dominance check sees who actually answered each round
    assert set(rounds["1"]) == set(tids[:-1]) and set(rounds["2"]) == set(tids)
    assert rounds["2"] == {tid: GT["n_per_site"][tid] for tid in tids}
    assert res["method"]["mode"] == "newton_raphson" and res["method"]["rounds"] > 1
    assert np.max(np.abs(np.array([res["stats"]["_logreg"]["logreg"]["coef"][k] for k in ("intercept", *FEATURES)]) - _truth())) < 1e-6


def test_result_contract_accepts_logreg_and_still_rejects_unknown_fields():
    from pydantic import ValidationError

    from server.schemas import MergedResult, Method

    fit = {"outcome": "case", "coef": {"intercept": -4.4, "age": 0.02}}
    merged = {"spec_hash": "abc", "coverage": "3/3 sites", "sites_expected": ["a", "b", "c"],
              "sites_reported": ["a", "b", "c"], "sites_missing": [], "n": 300, "n_per_site": {"a": 100, "b": 100, "c": 100},
              "stats": {"_logreg": {"logreg": fit, "n": 300,
                                    "history": [{"round": 1, "sites": 3, "max_delta": 0.5, "coef": fit["coef"]}]}},
              "method": {"mode": "newton_raphson", "rounds": 1, "ridge": 1e-6, "tol": 1e-8},
              "sites_missing_rounds": {"1": ["c"]}}
    MergedResult.model_validate(merged)
    with pytest.raises(ValidationError):
        MergedResult.model_validate({**merged, "stats": {"_logreg": {"logreg": {**fit, "bogus": 1}}}})
    with pytest.raises(ValidationError):
        MergedResult.model_validate({**merged, "sites_missing_rounds": {1: ["c"]}})
    with pytest.raises(ValidationError):
        Method(mode="newton_raphson", bogus=1)


# ---- Safe Output for one Newton round (shared by the FLARE executor and the direct API) ----
def _audit_tail():
    import os
    from pathlib import Path

    return json.loads((Path(os.environ["AUDIT_DIR"]) / "audit.jsonl").read_text().splitlines()[-1])


def _valid_step(n, p):
    return {"n": n, "complete": True, "grad": [0.5] * p, "hess": [[float(i == j) for j in range(p)] for i in range(p)]}


def _needed(p):
    from adapters import safe_output

    # same rule as a Gram matrix over [intercept, outcome, *features]
    return max(safe_output.effective_min_cell_size(SPEC.project_id, SPEC.min_cell_size), p + 2)


def test_irls_release_accepts_and_audits_a_valid_round(adapters):
    from adapters import safe_output

    p = len(FEATURES) + 1
    step = safe_output.release_irls_step("x", SPEC, _valid_step(_needed(p), p), p)
    assert step == {k: v for k, v in _valid_step(_needed(p), p).items() if k != "complete"}
    assert _audit_tail()["decision"] == "OK:newton_raphson_round"


@pytest.mark.parametrize("change,reason", [
    ({"complete": False}, "incomplete_inputs"),
    ({"complete": None}, "incomplete_inputs"),  # a backend that does not say is not trusted
    ({"n": "small"}, "invalid_n"),
    ({"grad": [0.5]}, "invalid_shape"),
    ({"hess": [[1.0]]}, "invalid_shape"),
    ({"grad": [float("nan")] * (len(FEATURES) + 1)}, "non_finite"),
])
def test_irls_release_rejects_and_audits(adapters, change, reason):
    from adapters import safe_output

    p = len(FEATURES) + 1
    with pytest.raises(safe_output.Rejected):
        safe_output.release_irls_step("x", SPEC, {**_valid_step(_needed(p), p), **change}, p)
    assert _audit_tail()["decision"] == "REJECTED" and _audit_tail()["rejected"] == [f"_logreg.step:{reason}"]


def test_irls_release_enforces_the_sample_threshold(adapters):
    from adapters import safe_output

    p = len(FEATURES) + 1
    with pytest.raises(safe_output.Rejected):
        safe_output.release_irls_step("x", SPEC, _valid_step(_needed(p) - 1, p), p)
    assert _audit_tail()["rejected"] == [f"_logreg.step:n={_needed(p) - 1}<{_needed(p)}"]


def test_controller_fit_counts_only_sites_that_contributed_a_round(monkeypatch, tmp_path, sites, adapters):
    from nvflare.apis.fl_context import FLContext
    from nvflare.apis.shareable import Shareable
    from nvflare.apis.signal import Signal

    from flare.app.logreg_controller import FedLogregController
    from server import overseer_queue

    monkeypatch.setenv("SERVER_OUT", str(tmp_path))
    tids = [s["tre_id"] for s in sites]

    def reply(**fields):
        shareable = Shareable()
        shareable.update(fields)
        return shareable

    def fake_round(name, data, fl_ctx, abort_signal, targets=None):
        if name == "logreg_init":
            return {tid: reply(n=GT["n_per_site"][tid]) for tid in tids}
        return {tid: reply(step=_step(adapters[tid], data["beta"])) for tid in tids[:-1]}  # the last never answers

    ctl = FedLogregController(SPEC.model_dump(), rounds=25, min_clients=2)
    monkeypatch.setattr(ctl, "_round", fake_round)
    ctl.control_flow(Signal(), FLContext())
    res = json.loads((overseer_queue.out_dir() / SPEC.spec_hash() / "result.json").read_text())
    assert res["sites_reported"] == sorted(tids[:-1]) and tids[-1] in res["sites_missing"]
    assert res["n"] == res["stats"]["_logreg"]["n"] == sum(GT["n_per_site"][t] for t in tids[:-1])
    assert res["n_per_site"] == {t: GT["n_per_site"][t] for t in tids[:-1]}


def test_controller_never_uses_a_single_site_round(monkeypatch, tmp_path, sites, adapters):
    """Even with min_clients=1 (a supported job setting), a round answered by one site
    would be that site's own Newton step: the fit stops instead of releasing it."""
    from nvflare.apis.fl_context import FLContext
    from nvflare.apis.shareable import Shareable
    from nvflare.apis.signal import Signal

    from flare.app.logreg_controller import FedLogregController
    from server import overseer_queue

    monkeypatch.setenv("SERVER_OUT", str(tmp_path))
    tids = [s["tre_id"] for s in sites]
    calls = {"n": 0}

    def reply(**fields):
        shareable = Shareable()
        shareable.update(fields)
        return shareable

    def fake_round(name, data, fl_ctx, abort_signal, targets=None):
        if name == "logreg_init":
            return {tid: reply(n=GT["n_per_site"][tid]) for tid in tids}
        calls["n"] += 1
        answering = tids if calls["n"] == 1 else tids[:1]  # only one site answers from round 2 on
        return {tid: reply(step=_step(adapters[tid], data["beta"])) for tid in answering}

    ctl = FedLogregController(SPEC.model_dump(), rounds=25, min_clients=1)
    monkeypatch.setattr(ctl, "_round", fake_round)
    ctl.control_flow(Signal(), FLContext())
    res = json.loads((overseer_queue.out_dir() / SPEC.spec_hash() / "result.json").read_text())
    assert [h["sites"] for h in res["stats"]["_logreg"]["history"]] == [len(tids)]
    assert list(res["contributions"]["_rounds"]) == ["1"]
