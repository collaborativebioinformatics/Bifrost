"""M1: three different native APIs, each returning counts; none leaks rows."""
import json

import pytest

from harmonisation import local_name
from tests.conftest import ROOT


def test_health_returns_count(tre_clients):
    gt = json.loads((ROOT / "data" / "ground_truth.json").read_text())
    for tid, (site, c) in tre_clients.items():
        r = c.get("/health")
        assert r.status_code == 200, tid
        assert r.json()["n"] == gt["n_per_site"][tid]
        assert r.json()["api"] == site["adapter"]


def _count(site, c, col):
    """Ask each TRE, in its own dialect, for genotype counts of one local column."""
    if site["adapter"] == "rest":
        return c.post("/query", json={"cols": [col], "agg": "value_counts"}).json()["result"][col]
    if site["adapter"] == "datashield":
        return c.post("/ds", json={"fn": "ds.table", "args": {"x": f"D${col}"}}).json()["counts"]
    if site["adapter"] == "sql":
        rows = c.post("/sql", json={"sql": f"SELECT {col} AS g, count(*) AS n FROM cohort GROUP BY g"}).json()["rows"]
        return {str(g): n for g, n in rows}
    pytest.fail(f"no test dialect for adapter {site['adapter']}")


def test_genotype_counts_match_ground_truth(tre_clients):
    gt = json.loads((ROOT / "data" / "ground_truth.json").read_text())
    for tid, (site, c) in tre_clients.items():
        col = local_name("snp_rs001", tid)
        counts = _count(site, c, col)
        n = gt["n_per_site"][tid]
        assert sum(counts.values()) == n, tid
        af = (counts.get("1", 0) + 2 * counts.get("2", 0)) / (2 * n)
        assert abs(af - gt["site_allele_freq"][tid]["snp_rs001"]) < 1e-9, tid


def test_no_row_level_access(tre_clients):
    for tid, (site, c) in tre_clients.items():
        if site["adapter"] == "sql":
            assert c.post("/sql", json={"sql": "SELECT * FROM cohort"}).status_code == 400
            assert c.post("/sql", json={"sql": "SELECT AGE FROM cohort LIMIT 5"}).status_code == 400
            assert c.post("/sql", json={"sql": "DELETE FROM cohort"}).status_code == 400
        elif site["adapter"] == "rest":
            assert c.post("/query", json={"cols": ["row_id"], "agg": "count"}).status_code == 400
            assert c.post("/query", json={"cols": ["alder"], "agg": "rows"}).status_code == 422
        elif site["adapter"] == "datashield":
            assert c.post("/ds", json={"fn": "ds.rows", "args": {}}).status_code == 400
            assert "row_id" not in c.post("/ds", json={"fn": "ds.colnames"}).json()["value"]


def test_filters_reduce_n(tre_clients):
    for tid, (site, c) in tre_clients.items():
        age = local_name("age", tid)
        if site["adapter"] == "rest":
            n = c.post("/query", json={"cols": [age], "filters": {age: {">=": 60}}, "agg": "count"}).json()["n"]
        elif site["adapter"] == "datashield":
            n = c.post("/ds", json={"fn": "ds.dim", "args": {"filter": {age: {">=": 60}}}}).json()["value"][0]
        else:
            n = c.post("/sql", json={"sql": f"SELECT count(*) FROM cohort WHERE {age} >= 60"}).json()["rows"][0][0]
        total = c.get("/health").json()["n"]
        assert 0 < n < total, tid
