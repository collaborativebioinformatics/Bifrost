"""Common API integration tests: real adapters/TRE apps, no FLARE or network."""
import json
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from adapters import registry
from server import analysis_service, overseer_queue
from server.api import app
from tests.conftest import ROOT, make_client

PROJECT = "hackathon-test"
QUERY = {"variant": "snp_rs001", "project_id": PROJECT}
GT = json.loads((ROOT / "data/ground_truth.json").read_text())


@pytest.fixture
def api(monkeypatch, tmp_path, sites):
    monkeypatch.setenv("SERVER_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "audit"))
    original_load = registry.load
    failed, called = set(), []
    by_id = {s["tre_id"]: s for s in sites}

    def load(tre_id):
        called.append(tre_id)
        if tre_id in failed:
            raise httpx.ConnectError("private TRE connection details")
        return original_load(tre_id, client=make_client(by_id[tre_id]))

    monkeypatch.setattr(registry, "load", load)
    with TestClient(app) as client:
        yield client, failed, called


def assert_safe(response, has_result=False):
    body = response.json()
    assert "contributions" not in response.text
    assert ("result" in body) == has_result
    if not has_result:
        assert "stats" not in body and "n_per_site" not in body
    return body


def test_allele_frequency_and_release_audit(api, sites):
    client, _, called = api
    response = client.get("/allele-frequency", params=QUERY)
    assert response.status_code == 200
    body = assert_safe(response, True)
    assert body["decision"] == "OK"
    assert sorted(called) == sorted(s["tre_id"] for s in sites)
    assert body["result"]["stats"]["snp_rs001"]["allele_freq"] == pytest.approx(GT["allele_freq"]["snp_rs001"])
    run = overseer_queue.out_dir() / body["spec_hash"]
    assert "contributions" in json.loads((run / "result.json").read_text())
    assert "contributions" not in (run / "released.json").read_text()
    assert json.loads(overseer_queue.release_log_path().read_text())["decision"] == "RELEASED"


def test_linear_regression_matches_ground_truth(api):
    response = api[0].post("/linear-regression", json={
        "features": GT["ols"]["features"], "target": GT["ols"]["outcome"], "project_id": PROJECT})
    assert response.status_code == 200
    body = assert_safe(response, True)
    assert body["decision"] == "OK"
    assert body["result"]["stats"]["_linreg"]["ols"]["coef"] == pytest.approx(GT["ols"]["coef"], abs=1e-6)


@pytest.mark.parametrize("analysis", ["allele", "regression"])
def test_partial_outage(api, sites, analysis):
    client, failed, called = api
    failed.add(sites[-1]["tre_id"])
    response = (client.get("/allele-frequency", params=QUERY) if analysis == "allele" else
                client.post("/linear-regression", json={"features": ["age", "bmi"], "target": "sbp", "project_id": PROJECT}))
    assert response.status_code == 200
    body = assert_safe(response, True)
    assert body["decision"] == "OK"
    assert set(called) == {s["tre_id"] for s in sites}
    assert body["sites_failed"] == {sites[-1]["tre_id"]: "unavailable"}
    assert len(body["sites_reported"]) == len(sites) - 1
    if analysis == "allele":
        reporting = body["sites_reported"]
        expected = sum(GT["site_allele_freq"][s]["snp_rs001"] * GT["n_per_site"][s] for s in reporting)
        expected /= sum(GT["n_per_site"][s] for s in reporting)
        assert body["result"]["stats"]["snp_rs001"]["allele_freq"] == pytest.approx(expected)


def test_single_response_is_flagged_and_queued(api, sites):
    client, failed, _ = api
    failed.update(s["tre_id"] for s in sites[1:])
    response = client.get("/allele-frequency", params=QUERY)
    assert response.status_code == 200
    body = assert_safe(response)
    assert body["decision"] == "FLAGGED"
    assert "min_sites" in body["reasons"]
    assert overseer_queue._load_queue()[0]["spec_hash"] == body["spec_hash"]


@pytest.mark.parametrize("analysis", ["allele", "regression"])
def test_zero_responses_and_independent_health(api, sites, analysis):
    client, failed, called = api
    failed.update(s["tre_id"] for s in sites)
    assert client.get("/health").json() == {"status": "ok"}
    assert called == []
    response = (client.get("/allele-frequency", params=QUERY) if analysis == "allele" else
                client.post("/linear-regression", json={"features": ["age"], "target": "sbp", "project_id": PROJECT}))
    assert response.status_code == 503
    body = assert_safe(response)
    assert body["error"] == "no_tres_responded" and body["sites_reported"] == []
    assert set(body["sites_failed"]) == failed
    assert "private TRE" not in response.text
    assert (overseer_queue.out_dir() / body["spec_hash"] / "check.json").exists()
    assert client.get("/health").status_code == 200


def test_suppression_withholds_statistics(api):
    response = api[0].get("/allele-frequency", params={**QUERY, "min_cell_size": 100})
    body = assert_safe(response)
    assert body["decision"] == "FLAGGED"
    assert "site_suppression" in body["reasons"]


def test_previous_release_never_served_after_flag(api, sites):
    client, failed, _ = api
    first = client.get("/allele-frequency", params=QUERY).json()
    assert first["decision"] == "OK"
    failed.update(s["tre_id"] for s in sites[1:])
    body = assert_safe(client.get("/allele-frequency", params=QUERY))
    assert body["spec_hash"] == first["spec_hash"] and body["decision"] == "FLAGGED"


def test_project_rejection_is_not_an_outage(api):
    body = assert_safe(api[0].get("/allele-frequency", params={**QUERY, "project_id": "unapproved"}))
    assert body["decision"] == "FLAGGED" and body["sites_failed"] == {}
    assert body["sites_reported"]


@pytest.mark.parametrize("variant", ["unknown", "age"])
def test_invalid_variant_fails_before_calls(api, variant):
    assert api[0].get("/allele-frequency", params={**QUERY, "variant": variant}).status_code == 422
    assert api[2] == []


def test_invalid_regression_fails_before_calls(api):
    response = api[0].post("/linear-regression", json={"features": ["sbp"], "target": "sbp", "project_id": PROJECT})
    assert response.status_code == 422 and api[2] == []


def test_singular_regression_withheld(api):
    response = api[0].post("/linear-regression", json={
        "features": ["sex"], "target": "sbp", "project_id": PROJECT,
        "filters": {"sex": {"==": 1}}})
    body = assert_safe(response)
    assert response.status_code == 200 and body["decision"] == "FLAGGED"
    assert "analysis" in body["reasons"]


def test_no_configured_sites(api, monkeypatch):
    monkeypatch.setattr(analysis_service, "load_sites", lambda: {"sites": []})
    response = api[0].get("/allele-frequency", params=QUERY)
    assert response.status_code == 503
    assert assert_safe(response)["coverage"] == "0/0 sites"
    assert api[2] == []


def test_audit_failure_fails_closed(api, monkeypatch):
    def broken(*args):
        raise OSError("private filesystem details")
    monkeypatch.setattr(overseer_queue, "record", broken)
    response = api[0].get("/allele-frequency", params=QUERY)
    assert response.status_code == 500
    assert assert_safe(response)["error"] == "analysis_failed"
    assert "private filesystem" not in response.text


def test_timeout_is_isolated(api, monkeypatch, sites):
    original = registry.load
    def load(tre_id):
        if tre_id == sites[-1]["tre_id"]:
            raise httpx.ReadTimeout("private timeout")
        return original(tre_id)
    monkeypatch.setattr(registry, "load", load)
    body = assert_safe(api[0].get("/allele-frequency", params=QUERY), True)
    assert body["sites_failed"] == {sites[-1]["tre_id"]: "timeout"}


@pytest.mark.parametrize("malformation", ["shape", "ragged", "nonnumeric", "columns", "nonfinite"])
def test_malformed_gram_is_isolated(api, monkeypatch, sites, malformation):
    client, failed, called = api
    bad_site = sites[-1]["tre_id"]
    request = {"features": ["age", "bmi"], "target": "sbp", "project_id": PROJECT}
    # Compare to the existing calculation with the same site unavailable.
    failed.add(bad_site)
    expected = client.post("/linear-regression", json=request).json()["result"]["stats"]
    failed.clear()
    called.clear()
    original = registry.load

    def load(tre_id):
        adapter = original(tre_id)
        run = adapter.run
        if tre_id == bad_site:
            def malformed(spec):
                result = run(spec)
                gram = result.stats["_linreg"]["gram"]
                if malformation == "shape":
                    gram["matrix"] = [[1, 2], [3, 4]]
                elif malformation == "ragged":
                    gram["matrix"][0].pop()
                elif malformation == "nonnumeric":
                    gram["matrix"][0][0] = "private invalid value"
                elif malformation == "columns":
                    gram["cols"] = list(reversed(gram["cols"]))
                else:
                    gram["matrix"][0][0] = float("nan")
                return result
            adapter.run = malformed
        return adapter

    monkeypatch.setattr(registry, "load", load)
    response = client.post("/linear-regression", json=request)
    assert response.status_code == 200
    body = assert_safe(response, True)
    assert body["decision"] == "OK"
    assert body["sites_failed"] == {bad_site: "adapter_error"}
    assert set(body["sites_reported"]) == {s["tre_id"] for s in sites} - {bad_site}
    assert set(called) == {s["tre_id"] for s in sites}
    assert body["result"]["stats"] == expected


@pytest.mark.parametrize("reason,code", [
    ("min_sites:1<2", "min_sites"),
    ("site_suppression:hunt:17", "site_suppression"),
    ("k_anon:snp_rs001.genotype_counts.2=3<5", "k_anon"),
    ("dominance:snp_rs001.genotype_counts.0:hunt=0.99>0.9", "dominance"),
    ("differencing:prev=private-hash:|30001-30000|<5", "differencing"),
    ("analysis:no_valid_statistics", "analysis"),
    ("future_rule:private_count=314159", "disclosure_review_required"),
])
def test_flagged_reasons_sanitized_but_internal_records_preserved(api, monkeypatch, reason, code):
    original = analysis_service.disclosure_check.check

    def flagged(*args, **kwargs):
        check = original(*args, **kwargs)
        return {**check, "decision": "FLAGGED", "reasons": [reason]}

    monkeypatch.setattr(analysis_service.disclosure_check, "check", flagged)
    response = api[0].get("/allele-frequency", params=QUERY)
    assert response.status_code == 200
    body = assert_safe(response)
    assert body["decision"] == "FLAGGED" and body["reasons"] == [code]
    # The remaining metadata describes request identity and site availability,
    # never cohort counts, genotype cells, or contributor fractions.
    assert set(body) == {"spec_hash", "status", "decision", "reasons", "sites_expected",
                         "sites_reported", "sites_missing", "sites_failed", "coverage"}
    assert not any(char.isdigit() for char in "".join(body["reasons"]))
    run = overseer_queue.out_dir() / body["spec_hash"]
    assert json.loads((run / "check.json").read_text())["reasons"] == [reason]
    assert overseer_queue._load_queue()[0]["reasons"] == [reason]
    assert json.loads(overseer_queue.release_log_path().read_text())["reasons"] == [reason]
    assert not (run / "released.json").exists()


def test_startup_without_flare_or_tres():
    script = '''
import sys
sys.modules["nvflare"] = None
from adapters import registry
def fail(*args, **kwargs):
    raise AssertionError("startup must not load adapters")
registry.load = fail
from server.api import app
from fastapi.testclient import TestClient
with TestClient(app) as client:
    assert client.get("/health").json() == {"status": "ok"}
'''
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True)
