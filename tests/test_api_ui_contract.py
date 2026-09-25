"""The routes the researcher UI (frontend/) calls, against the OpenAPI-declared shapes."""
import json
import subprocess
import sys
import threading
import time

import pytest

from server import overseer_queue
from server.schemas import AuditLog, Metadata, OverseerQueue, ReleasedResult, RunStatus
from tests.conftest import ROOT
from tests.test_analysis_api import PROJECT, api  # noqa: F401  (fixture: real adapters, in-process TREs)

GT = json.loads((ROOT / "data/ground_truth.json").read_text())


def _wait(client, run_id, timeout=60):
    for _ in range(int(timeout / 0.2)):
        body = client.get(f"/run/{run_id}").json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.2)
    pytest.fail("run did not finish")


def test_metadata_lists_variables_projects_examples_sites(api, sites):
    body = api[0].get("/metadata", params={"probe": False}).json()
    Metadata.model_validate(body)
    assert {v["name"] for v in body["variables"]} >= {"age", "sex", "snp_rs001"}
    assert {p["id"] for p in body["projects"]} >= {PROJECT}
    assert "allele_freq.json" in body["examples"]
    assert [s["tre_id"] for s in body["sites"]] == [s["tre_id"] for s in sites]
    assert all(s["online"] is None for s in body["sites"])  # not probed


def test_metadata_probes_tres(api, sites):
    body = api[0].get("/metadata").json()
    assert all(s["online"] is True for s in body["sites"]), body["sites"]


def test_examples_are_served_and_path_safe(api):
    assert api[0].get("/examples/allele_freq.json").json()["analysis_type"] == "allele_freq"
    assert api[0].get("/examples/..%2Fanalysis_spec.py").status_code == 404
    assert api[0].get("/examples/nope.json").status_code == 404


def test_run_allele_freq_end_to_end(api):
    client = api[0]
    r = client.post("/run", json={"analysis_type": "allele_freq", "variables": ["snp_rs001", "snp_rs002"],
                                  "project_id": PROJECT, "min_cell_size": 5})
    assert r.status_code == 202
    first = RunStatus.model_validate(r.json())
    assert first.status in ("queued", "running")
    body = _wait(client, first.run_id)
    run = RunStatus.model_validate(body)
    assert run.status == "completed" and run.decision == "OK" and run.coverage.endswith("/3 sites")
    ReleasedResult.model_validate(body["result"])
    assert body["result"]["stats"]["snp_rs001"]["allele_freq"] == pytest.approx(GT["allele_freq"]["snp_rs001"])
    assert "contributions" not in json.dumps(body)


def test_run_fedavg_regression(api):
    client = api[0]
    r = client.post("/run", json={"analysis_type": "fed_linreg", "variables": GT["ols"]["features"],
                                  "outcome": GT["ols"]["outcome"], "project_id": PROJECT, "fedavg_rounds": 8})
    body = _wait(client, r.json()["run_id"])
    assert body["status"] == "completed" and body["result"]["method"]["mode"] == "fedavg"
    assert len(body["result"]["stats"]["_linreg"]["history"]) == 8
    assert body["result"]["stats"]["_linreg"]["ols"]["coef"] == pytest.approx(GT["ols"]["coef"], abs=1e-4)


def test_run_logistic_regression(api):
    """The background /run path dispatches fed_logreg to the multi-round Newton-Raphson
    service; the recorded and released result passes the strict contracts."""
    client = api[0]
    r = client.post("/run", json={"analysis_type": "fed_logreg", "variables": GT["logreg"]["features"],
                                  "outcome": GT["logreg"]["outcome"], "project_id": PROJECT})
    assert r.status_code == 202
    body = _wait(client, r.json()["run_id"])
    assert body["status"] == "completed" and body["decision"] == "OK"
    ReleasedResult.model_validate(body["result"])
    assert body["result"]["method"]["mode"] == "newton_raphson"
    assert body["result"]["stats"]["_logreg"]["logreg"]["coef"] == pytest.approx(GT["logreg"]["coef"], abs=1e-6)


def test_run_rejects_non_binary_logistic_outcome_before_any_tre_call(api):
    client, _, called = api
    assert client.post("/run", json={"analysis_type": "fed_logreg", "variables": ["age", "bmi"], "outcome": "sbp",
                                     "project_id": PROJECT}).status_code == 422
    assert called == []


def test_metadata_offers_logistic_regression(api):
    body = api[0].get("/metadata", params={"probe": False}).json()
    assert "fed_logreg" in body["analysis_types"] and "fed_logreg.json" in body["examples"]


def test_run_rejects_bad_specs_before_any_tre_call(api):
    client, _, called = api
    assert client.post("/run", json={"analysis_type": "allele_freq", "variables": ["age"], "project_id": PROJECT}).status_code == 422
    assert client.post("/run", json={"analysis_type": "allele_freq", "variables": ["snp_rs001"], "project_id": PROJECT,
                                     "fedavg_rounds": 3}).status_code == 422
    assert client.post("/run", json={"analysis_type": "fed_linreg", "variables": ["age"], "project_id": PROJECT}).status_code == 422
    assert client.get("/run/doesnotexist").status_code == 404
    assert called == []


def test_flagged_run_goes_to_overseer_and_can_be_approved(api):
    client = api[0]
    r = client.post("/run", json={"analysis_type": "allele_freq", "variables": ["snp_rs001"], "project_id": PROJECT,
                                  "min_cell_size": 100})
    body = _wait(client, r.json()["run_id"])
    assert body["status"] == "flagged" and "site_suppression" in body["reasons"] and "result" not in body
    queue = client.get("/overseer").json()
    OverseerQueue.model_validate(queue)
    [item] = queue["items"]
    assert item["id"] == body["spec_hash"] and item["revision"] == body["revision"]
    assert client.post(f"/overseer/{body['spec_hash']}/approve", json={"by": "tester"}).status_code == 422  # no revision
    d = client.post(f"/overseer/{body['spec_hash']}/approve",
                    json={"revision": item["revision"], "note": "cells withheld by design", "by": "tester"})
    assert d.status_code == 200 and d.json()["released"] is True and d.json()["revision"] == item["revision"]
    assert client.get("/overseer").json()["items"] == []
    assert client.post(f"/overseer/{body['spec_hash']}/approve", json={"revision": item["revision"]}).status_code == 409
    assert client.post(f"/overseer/{body['spec_hash']}/approve", json={"revision": "made-up"}).status_code == 404
    assert client.post(f"/overseer/{body['spec_hash']}/maybe", json={"revision": item["revision"]}).status_code == 404
    log = [json.loads(l) for l in overseer_queue.release_log_path().read_text().splitlines()]
    assert [e["decision"] for e in log][-2:] == ["QUEUED", "RELEASED"] and log[-1]["by"] == "tester"


def test_audit_lists_site_entries_newest_first(api):
    client = api[0]
    body = _wait(client, client.post("/run", json={"analysis_type": "allele_freq", "variables": ["snp_rs001"],
                                                    "project_id": PROJECT}).json()["run_id"])
    log = client.get("/audit", params={"spec_hash": body["spec_hash"]}).json()
    AuditLog.model_validate(log)
    assert {e["tre_id"] for e in log["items"]} == set(body["sites_reported"])
    assert all(e["decision"] == "OK" and e["timestamp"] for e in log["items"])
    everything = client.get("/audit").json()["items"]
    assert everything == sorted(everything, key=lambda e: e["timestamp"], reverse=True)


def test_exported_schemas_are_current():
    subprocess.run([sys.executable, "scripts/export_schemas.py", "--check"], cwd=ROOT, check=True)


def test_run_fed_stats_releases_valid_statistics(api):
    client = api[0]
    body = _wait(client, client.post("/run", json={"analysis_type": "fed_stats", "variables": ["age", "bmi"],
                                                    "project_id": PROJECT}).json()["run_id"])
    RunStatus.model_validate(body)
    assert body["status"] == "completed" and body["decision"] == "OK", body
    age = body["result"]["stats"]["age"]
    assert age["count"] == GT["n_total"] and abs(age["mean"] - GT["mean"]["age"]) < 1e-9


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_overseer_decision_updates_the_polled_run(api, decision):
    client = api[0]
    run_id = client.post("/run", json={"analysis_type": "allele_freq", "variables": ["snp_rs001"], "project_id": PROJECT,
                                       "min_cell_size": 100}).json()["run_id"]
    body = _wait(client, run_id)
    assert body["status"] == "flagged"
    decided = client.post(f"/overseer/{body['spec_hash']}/{decision}", json={"revision": body["revision"], "by": "tester"})
    assert decided.status_code == 200
    after = client.get(f"/run/{run_id}").json()
    RunStatus.model_validate(after)
    if decision == "approve":
        assert after["status"] == "completed" and after["decision"] == "APPROVED"
        ReleasedResult.model_validate(after["result"])
    else:
        assert after["status"] == "rejected" and after["decision"] == "REJECTED" and "result" not in after


FLAGGED_RUN = {"analysis_type": "allele_freq", "variables": ["snp_rs001"], "project_id": PROJECT, "min_cell_size": 100}


def _pause_first_run(monkeypatch):
    """Hold the first background run after record() queued its result and before it writes
    its status; later runs go straight through. Returns (recorded, proceed) events."""
    from server import api as api_module

    recorded, proceed = threading.Event(), threading.Event()
    real = api_module._analyse

    def analyse_then_pause(*args, **kwargs):
        result = real(*args, **kwargs)  # record() has run: the item is queued
        if not recorded.is_set():
            recorded.set()
            proceed.wait(30)
        return result

    monkeypatch.setattr(api_module, "_analyse", analyse_then_pause)
    return recorded, proceed


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_decision_between_record_and_run_update_is_not_lost(api, monkeypatch, decision):
    """record() makes the flagged item visible before the background thread writes the
    run's status; a decision landing in that gap must still reach GET /run/{id}."""
    client = api[0]
    recorded, proceed = _pause_first_run(monkeypatch)
    submitted = client.post("/run", json=FLAGGED_RUN).json()
    assert recorded.wait(30)
    [item] = client.get("/overseer").json()["items"]
    assert client.post(f"/overseer/{submitted['spec_hash']}/{decision}",
                       json={"revision": item["revision"], "by": "tester"}).status_code == 200
    proceed.set()
    body = _wait(client, submitted["run_id"])
    if decision == "approve":
        assert body["status"] == "completed" and body["decision"] == "APPROVED", body
        ReleasedResult.model_validate(body["result"])
    else:
        assert body["status"] == "rejected" and body["decision"] == "REJECTED" and "result" not in body, body


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_an_early_decision_survives_a_rerun_before_its_run_finishes(api, monkeypatch, decision):
    """A is decided in that gap, then a rerun B records before A writes its status: A still
    reports its own decision (and, approved, its own released result)."""
    client = api[0]
    recorded, proceed = _pause_first_run(monkeypatch)
    run_a = client.post("/run", json=FLAGGED_RUN).json()
    assert recorded.wait(30)
    [item] = client.get("/overseer").json()["items"]
    assert client.post(f"/overseer/{item['spec_hash']}/{decision}",
                       json={"revision": item["revision"], "by": "tester"}).status_code == 200
    run_b = _wait(client, client.post("/run", json=FLAGGED_RUN).json()["run_id"])
    assert run_b["status"] == "flagged" and run_b["revision"] != item["revision"]
    proceed.set()
    body = _wait(client, run_a["run_id"])
    if decision == "approve":
        assert body["status"] == "completed" and body["decision"] == "APPROVED", body
        ReleasedResult.model_validate(body["result"])
    else:
        assert body["status"] == "rejected" and body["decision"] == "REJECTED", body


def test_a_decision_on_the_rerun_does_not_reach_the_run_it_replaced(api, monkeypatch):
    """A queues its result and has not written its status when rerun B replaces it; B is
    approved. B reports the approval; A, never decided, stays flagged."""
    client = api[0]
    recorded, proceed = _pause_first_run(monkeypatch)
    run_a = client.post("/run", json=FLAGGED_RUN).json()
    assert recorded.wait(30)
    run_b = _wait(client, client.post("/run", json=FLAGGED_RUN).json()["run_id"])
    [item] = client.get("/overseer").json()["items"]
    assert item["revision"] == run_b["revision"]
    assert client.post(f"/overseer/{item['spec_hash']}/approve",
                       json={"revision": item["revision"], "by": "tester"}).status_code == 200
    assert client.get(f"/run/{run_b['run_id']}").json()["decision"] == "APPROVED"
    proceed.set()
    body = _wait(client, run_a["run_id"])
    assert body["status"] == "flagged" and body["decision"] == "FLAGGED" and body["revision"] != run_b["revision"], body


def test_approving_a_replaced_result_is_refused_and_changes_nothing(api):
    """View A, rerun the same spec to produce B, approve A: 409, nothing released or
    changed. Approving B with its current revision succeeds."""
    client = api[0]
    run_a = _wait(client, client.post("/run", json=FLAGGED_RUN).json()["run_id"])
    [viewed] = client.get("/overseer").json()["items"]  # the overseer reviews A
    assert viewed["revision"] == run_a["revision"]
    run_b = _wait(client, client.post("/run", json=FLAGGED_RUN).json()["run_id"])  # a rerun queues B
    assert run_b["status"] == "flagged" and run_b["spec_hash"] == run_a["spec_hash"]
    assert run_b["revision"] != viewed["revision"]
    out = overseer_queue.out_dir()
    before = {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}

    stale = client.post(f"/overseer/{viewed['spec_hash']}/approve", json={"revision": viewed["revision"], "by": "tester"})
    assert stale.status_code == 409, stale.json()
    assert {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()} == before
    assert not (out / viewed["spec_hash"] / "released.json").exists()
    assert [client.get(f"/run/{r['run_id']}").json()["status"] for r in (run_a, run_b)] == ["flagged", "flagged"]

    [current] = client.get("/overseer").json()["items"]
    assert current["revision"] == run_b["revision"]
    approved = client.post(f"/overseer/{current['spec_hash']}/approve", json={"revision": current["revision"], "by": "tester"})
    assert approved.status_code == 200 and (out / current["spec_hash"] / "released.json").exists()
    after_b = client.get(f"/run/{run_b['run_id']}").json()
    assert after_b["status"] == "completed" and after_b["decision"] == "APPROVED"
    # A's own result was replaced before anyone decided on it: it stays flagged
    assert client.get(f"/run/{run_a['run_id']}").json()["status"] == "flagged"
