"""The routes the researcher UI (frontend/) calls, against the OpenAPI-declared shapes."""
import json
import subprocess
import sys
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
    assert [i["id"] for i in queue["items"]] == [body["spec_hash"]]
    d = client.post(f"/overseer/{body['spec_hash']}/approve", json={"note": "cells withheld by design", "by": "tester"})
    assert d.status_code == 200 and d.json()["released"] is True
    assert client.get("/overseer").json()["items"] == []
    assert client.post(f"/overseer/{body['spec_hash']}/approve", json={}).status_code == 404
    assert client.post(f"/overseer/{body['spec_hash']}/maybe", json={}).status_code == 404
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
