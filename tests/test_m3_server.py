"""M3: server merge, disclosure check, overseer queue -- pure unit tests (no FLARE)."""
import json

import pytest

from adapters.base import AggregateResult
from server import disclosure_check, overseer_queue
from server.aggregate import combine


def _r(tid, n, c0, c1, c2, rejected=()):
    return AggregateResult(tre_id=tid, n=n, region="nordic", spec_hash="abc",
                           stats={"snp_x": {"genotype_counts": {"0": c0, "1": c1, "2": c2},
                                            "allele_counts": {"minor": c1 + 2 * c2, "major": 2 * c0 + c1, "n_alleles": 2 * n}}},
                           rejected=list(rejected))


SPEC = {"analysis_type": "allele_freq", "variables": ["snp_x"], "project_id": "p", "min_cell_size": 5, "filters": {}, "outcome": None}


def test_combine_is_associative_and_reports_coverage():
    a, b, c = _r("a", 100, 80, 18, 2), _r("b", 50, 40, 8, 2), _r("c", 10, 6, 3, 1)
    m_all = combine([a, b, c], ["a", "b", "c"])
    m_relay = combine([a, b], ["a", "b"])  # a regional relay pre-merges a+b ...
    relay_stats = {k: v for k, v in m_relay["stats"]["snp_x"].items() if k in ("genotype_counts", "allele_counts")}
    m_root = combine([AggregateResult(tre_id="relay", n=m_relay["n"], stats={"snp_x": relay_stats}, region="r"), c], ["relay", "c"])
    assert m_all["stats"]["snp_x"]["allele_freq"] == m_root["stats"]["snp_x"]["allele_freq"]
    assert m_all["coverage"] == "3/3 sites" and m_all["sites_missing"] == []
    m_part = combine([a, b], ["a", "b", "c"])
    assert m_part["coverage"] == "2/3 sites" and m_part["sites_missing"] == ["c"]


def test_check_ok_on_clean_result(tmp_path):
    m = combine([_r("a", 100, 80, 18, 2), _r("b", 100, 70, 25, 5)], ["a", "b"])
    assert disclosure_check.check(m, SPEC, tmp_path / "none.jsonl")["decision"] == "OK"


def test_check_flags_k_anonymity_dominance_single_site_and_site_suppression():
    m = combine([_r("a", 100, 80, 18, 2)], ["a", "b"])  # 1 site; merged cell '2' = 2 < 5
    r = disclosure_check.check(m, SPEC)
    assert r["decision"] == "FLAGGED"
    assert any(x.startswith("min_sites:") for x in r["reasons"])
    assert "k_anon:snp_x.genotype_counts.2=2<5" in r["reasons"]
    m = combine([_r("a", 1000, 900, 90, 10), _r("b", 100, 89, 10, 1)], ["a", "b"])  # a dominates every cell
    r = disclosure_check.check(m, SPEC)
    assert any(x.startswith("dominance:snp_x.genotype_counts.2:a=") for x in r["reasons"])
    m = combine([_r("a", 100, 80, 18, 2, rejected=["snp_x.genotype_counts.2:count=2<5"]), _r("b", 100, 70, 25, 5)], ["a", "b"])
    assert "site_suppression:a:1" in disclosure_check.check(m, SPEC)["reasons"]


def test_check_flags_differencing_against_release_log(tmp_path):
    log = tmp_path / "release_log.jsonl"
    log.write_text(json.dumps({"spec_hash": "prev", "signature": "allele_freq|snp_x", "n": 203}) + "\n")
    m = combine([_r("a", 100, 80, 18, 2), _r("b", 100, 70, 25, 5)], ["a", "b"])  # n=200; |203-200| < 5
    r = disclosure_check.check(m, SPEC, log)
    assert any(x.startswith("differencing:prev=prev") for x in r["reasons"])


def test_overseer_queue_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVER_OUT", str(tmp_path))
    m = combine([_r("a", 100, 80, 18, 2)], ["a", "b"])
    chk = disclosure_check.check(m, SPEC)
    d = overseer_queue.record(SPEC, m, chk)
    assert not (d / "released.json").exists()
    assert [i["spec_hash"] for i in overseer_queue._load_queue()] == ["abc"]
    overseer_queue.decide("abc", approve=True, note="reviewed", by="tester")
    assert (d / "released.json").exists()
    assert "contributions" not in json.loads((d / "released.json").read_text())
    assert overseer_queue._load_queue() == []
    log = [json.loads(l) for l in overseer_queue.release_log_path().read_text().splitlines()]
    assert [e["decision"] for e in log] == ["QUEUED", "RELEASED"] and log[-1]["by"] == "tester"
    with pytest.raises(KeyError):
        overseer_queue.decide("abc", approve=False, note="", by="x")
