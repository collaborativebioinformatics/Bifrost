"""M2: heterogeneous APIs -> identical AggregateResult; Safe Output filter; registry."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from adapters import registry, safe_output
from adapters.base import AggregateResult, TREAdapter
from spec.analysis_spec import AnalysisSpec
from tests.conftest import ROOT

GT = json.loads((ROOT / "data" / "ground_truth.json").read_text())
SNPS = ["snp_rs001", "snp_rs002", "snp_rs003"]
PROJECT = "hackathon-test"


def _audit_lines(tre_id):
    p = Path(os.environ["AUDIT_DIR"]) / "audit.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


# ---- registry ------------------------------------------------------------------------
def test_registry_loads_by_name(sites):
    for s in sites:
        assert issubclass(registry.get(s["adapter"]), TREAdapter)
        assert registry.get(s["adapter"]).name == s["adapter"]
    with pytest.raises(KeyError):
        registry.get("nope")


def test_registry_load_by_tre_id_uses_sites_yaml(sites, adapters):
    for s in sites:
        a = adapters[s["tre_id"]]
        assert a.name == s["adapter"] and a.region == s["region"] and a.tre_id == s["tre_id"]


# ---- identical shape across adapters --------------------------------------------------
def test_schema_is_canonical(adapters):
    for tid, a in adapters.items():
        sch = a.schema()
        assert "age" in sch and "snp_rs001" in sch, tid  # canonical names, not local ones


def test_allele_freq_same_shape_and_matches_ground_truth(adapters):
    spec = AnalysisSpec(analysis_type="allele_freq", variables=SNPS, project_id=PROJECT)
    results = {tid: a.run(spec) for tid, a in adapters.items()}
    shapes = set()
    for tid, r in results.items():
        assert isinstance(r, AggregateResult)
        assert r.n == GT["n_per_site"][tid]
        assert r.rejected == [], tid
        shapes.add(tuple((v, tuple(sorted(r.stats[v].present()))) for v in SNPS))
        for v in SNPS:
            ac = r.stats[v].allele_counts
            assert ac.n_alleles == 2 * r.n
            assert abs(ac.minor / ac.n_alleles - GT["site_allele_freq"][tid][v]) < 1e-12
    assert len(shapes) == 1, "adapters must return the same result shape"
    # the server's job, in one line: sums are associative
    for v in SNPS:
        minor = sum(r.stats[v].allele_counts.minor for r in results.values())
        total = sum(r.stats[v].allele_counts.n_alleles for r in results.values())
        assert abs(minor / total - GT["allele_freq"][v]) < 1e-12


def test_fed_stats_sums_to_global_mean(adapters):
    spec = AnalysisSpec(analysis_type="fed_stats", variables=["age", "bmi", "snp_rs005"], project_id=PROJECT)
    results = [a.run(spec) for a in adapters.values()]
    for v in ("age", "bmi"):
        assert all(set(r.stats[v].present()) == {"count", "sum", "sum_sq", "min", "max"} for r in results)
        mean = sum(r.stats[v].sum for r in results) / sum(r.stats[v].count for r in results)
        assert abs(mean - GT["mean"][v]) < 1e-9
    assert all("allele_counts" in r.stats["snp_rs005"] for r in results)


def test_filters_translate_to_local_columns(adapters):
    spec = AnalysisSpec(analysis_type="fed_stats", variables=["age"], filters={"age": {">=": 60}, "sex": {"==": 1}},
                        project_id=PROJECT)
    for tid, a in adapters.items():
        r = a.run(spec)
        assert 0 < r.n < GT["n_per_site"][tid]
        assert r.stats["age"].min >= 60


def test_fed_linreg_gram_reproduces_global_ols(adapters):
    feats = GT["ols"]["features"]
    spec = AnalysisSpec(analysis_type="fed_linreg", variables=feats, outcome=GT["ols"]["outcome"], project_id=PROJECT)
    results = [a.run(spec) for a in adapters.values()]
    G = sum(np.array(r.stats["_linreg"].gram.matrix) for r in results)  # associative
    XtX, Xty = G[np.ix_([0, *range(2, len(feats) + 2)], [0, *range(2, len(feats) + 2)])], G[[0, *range(2, len(feats) + 2)], 1]
    beta = np.linalg.solve(XtX, Xty)
    expected = [GT["ols"]["coef"][k] for k in ("intercept", *feats)]
    assert np.allclose(beta, expected, atol=1e-6)


# ---- Safe Output filter ---------------------------------------------------------------
def test_small_cells_suppressed_and_audited(adapters):
    spec = AnalysisSpec(analysis_type="allele_freq", variables=["snp_rs001"], min_cell_size=100, project_id=PROJECT)
    for tid, a in adapters.items():
        r = a.run(spec)
        assert any(x.startswith("snp_rs001.genotype_counts.2:") for x in r.rejected), tid
        assert "snp_rs001.allele_counts:derived_from_suppressed_cell" in r.rejected
        assert "allele_counts" not in r.stats["snp_rs001"]
        assert "2" not in r.stats["snp_rs001"].genotype_counts
        last = _audit_lines(tid)[-1]
        assert last["decision"] == "PARTIAL" and last["spec_hash"] == spec.spec_hash() and last["tre_id"] == tid


def test_tiny_cohort_releases_nothing(adapters):
    spec = AnalysisSpec(analysis_type="fed_stats", variables=["age"], filters={"age": {">=": 94}, "bmi": {">=": 40}}, project_id=PROJECT)
    for tid, a in adapters.items():
        r = a.run(spec)
        assert r.stats == {} and r.n == 0 and r.rejected and r.rejected[0].startswith("*:n=")
        assert _audit_lines(tid)[-1]["decision"] == "REJECTED"


def test_unknown_project_rejected_before_query(adapters):
    spec = AnalysisSpec(analysis_type="allele_freq", variables=["snp_rs001"], project_id="not-approved")
    for tid, a in adapters.items():
        r = a.run(spec)
        assert r.stats == {} and r.rejected == ["project_id:not-approved:not_allowed"]
        assert _audit_lines(tid)[-1]["decision"] == "REJECTED"


def test_only_allow_listed_keys_leave():
    spec = AnalysisSpec(analysis_type="fed_stats", variables=["age"], project_id=PROJECT)
    raw = {"age": {"count": 50, "sum": 1.0, "rows": [1, 2, 3], "mean": 3.0}}
    r = safe_output.filter("x", "r", spec, 50, raw)
    assert set(r.stats["age"].present()) == {"count", "sum"}
    assert sorted(r.rejected) == ["age.mean:not_allow_listed", "age.rows:not_allow_listed"]


def test_datashield_native_suppression_is_visible(adapters, sites):
    ds = [s["tre_id"] for s in sites if s["adapter"] == "datashield"]
    if not ds:
        pytest.skip("no datashield site")
    a = adapters[ds[0]]
    # tighten an age filter until DataSHIELD's own nfilter.tab (3) bites on some genotype cell
    for age in range(70, 95):
        vc = a.value_counts(a.local("snp_rs001"), {a.local("age"): {">=": age}})
        if any(v is None for v in vc.values()):
            break
    else:
        pytest.skip("no cell small enough for nfilter.tab in this data split")
    spec = AnalysisSpec(analysis_type="allele_freq", variables=["snp_rs001"], filters={"age": {">=": age}},
                        min_cell_size=1, project_id=PROJECT)
    r = a.run(spec)
    assert any(x.endswith(":tre_native") for x in r.rejected), r.rejected


def test_result_roundtrips_as_dict(adapters):
    spec = AnalysisSpec(analysis_type="allele_freq", variables=["snp_rs001"], project_id=PROJECT)
    r = next(iter(adapters.values())).run(spec)
    assert AggregateResult.from_dict(json.loads(json.dumps(r.to_dict()))) == r


def test_policy_floor_overrides_requested_min_cell_size(adapters):
    spec = AnalysisSpec(analysis_type="allele_freq", variables=["snp_x"], min_cell_size=1, project_id=PROJECT)
    r = safe_output.filter("x", "r", spec, 53, {"snp_x": {"genotype_counts": {"0": 50, "1": 3, "2": 0}}})
    assert r.stats["snp_x"].genotype_counts == {"0": 50}
    assert "snp_x.genotype_counts.1:count=3<5" in r.rejected
    assert _audit_lines("x")[-1]["min_cell_size"] == 5


# ---- missing values: regression inputs must be complete -------------------------------
def _client_with_missing_values(site, tmp_path, canonical):
    """This site's real data with a few values of one variable blanked out."""
    import importlib

    import pandas as pd
    from fastapi.testclient import TestClient

    from harmonisation import local_name

    df = pd.read_csv(ROOT / "data" / "sites" / f"{site['tre_id']}.csv")
    df.loc[df.index[:3], local_name(canonical, site["tre_id"])] = float("nan")
    path = tmp_path / f"{site['tre_id']}.csv"
    df.to_csv(path, index=False)
    return TestClient(importlib.import_module(f"tres.{site['adapter']}.app").create_app(site["tre_id"], str(path)))


def test_incomplete_regression_inputs_are_rejected_on_every_backend(adapters, sites, tmp_path):
    feats, outcome = GT["ols"]["features"], GT["ols"]["outcome"]
    spec = AnalysisSpec(analysis_type="fed_linreg", variables=feats, outcome=outcome, project_id=PROJECT)
    for s in sites:
        a = registry.load(s["tre_id"], client=_client_with_missing_values(s, tmp_path, "age"))
        r = a.run(spec)
        assert (r.n, r.stats, r.rejected) == (0, {}, ["_linreg.gram:incomplete_inputs"]), s["tre_id"]
        assert _audit_lines(s["tre_id"])[-1]["decision"] == "REJECTED"
        step = a.irls_step(a.local("case"), [a.local(c) for c in feats], [0.0] * (len(feats) + 1), {})
        assert step["complete"] is False and step.get("grad") is None, s["tre_id"]


def test_a_variable_with_fewer_than_k_observed_values_releases_nothing(adapters):
    spec = AnalysisSpec(analysis_type="fed_stats", variables=["age", "snp_x"], project_id=PROJECT)
    raw = {"age": {"count": 1, "sum": 70.0, "sum_sq": 4900.0, "min": 70.0, "max": 70.0},  # one observed age
           "snp_x": {"genotype_counts": {}}}  # no observed genotype at all
    r = safe_output.filter("x", "r", spec, 10, raw)
    assert r.stats["age"].present() == {} and r.stats["snp_x"].present() == {}
    assert r.rejected == ["age.*:observed=1<5", "snp_x.*:observed=0<5"]
