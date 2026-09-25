"""M3: server merge, disclosure check, overseer queue -- pure unit tests (no FLARE)."""
import json
import threading

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


# ---- policy floor and dominance beyond count tables ------------------------------------
def test_requested_min_cell_size_cannot_undercut_the_policy_floor():
    m = combine([_r("a", 100, 80, 18, 2), _r("b", 100, 70, 29, 1)], ["a", "b"])  # merged cell '2' = 3
    assert "k_anon:snp_x.genotype_counts.2=3<5" in disclosure_check.check(m, {**SPEC, "min_cell_size": 1})["reasons"]


def _stats_site(tid, n, count):
    return AggregateResult(tre_id=tid, n=n, region="r", spec_hash="abc",
                           stats={"age": {"count": count, "sum": 50.0 * count, "sum_sq": 2600.0 * count,
                                          "min": 20.0, "max": 80.0}})


def test_dominance_uses_each_variables_observed_count():
    # equal cohorts (50/50), but one site supplies 100 of the 105 observed ages
    m = combine([_stats_site("a", 100, 100), _stats_site("b", 100, 5)], ["a", "b"])
    r = disclosure_check.check(m, {**SPEC, "analysis_type": "fed_stats", "variables": ["age"]})
    assert "dominance:age.count:a=0.95>0.9" in r["reasons"]


def _gram_site(tid, n):
    matrix = [[1.0 * n, 2.0 * n, 1.0 * n], [2.0 * n, 5.0 * n, 2.5 * n], [1.0 * n, 2.5 * n, 2.0 * n]]
    return AggregateResult(tre_id=tid, n=n, region="r", spec_hash="abc",
                           stats={"_linreg": {"gram": {"n": n, "cols": ["intercept", "y", "x"], "matrix": matrix}}})


def test_dominance_on_regression_sample_counts():
    spec = {**SPEC, "analysis_type": "fed_linreg", "variables": ["x"], "outcome": "y"}
    m = combine([_gram_site("a", 950), _gram_site("b", 50)], ["a", "b"])
    assert "dominance:_linreg.n:a=0.95>0.9" in disclosure_check.check(m, spec)["reasons"]
    m = combine([_gram_site("a", 500), _gram_site("b", 500)], ["a", "b"])
    assert not any(x.startswith("dominance") for x in disclosure_check.check(m, spec)["reasons"])


def test_dominance_checks_every_released_round():
    # 90/5/5 is not dominated, but a round the last site missed is: 90/95
    merged = {"sites_reported": ["a", "b", "c"], "stats": {}, "n": 100,
              "contributions": {"_rounds": {"1": {"a": 90, "b": 5, "c": 5}, "2": {"a": 90, "b": 5}}}}
    r = disclosure_check.check(merged, {**SPEC, "analysis_type": "fed_logreg", "variables": ["x"], "outcome": "y"})
    assert r["reasons"] == ["dominance:round=2:a=0.95>0.9"]


# ---- release lifecycle: several runs of one spec hash ----------------------------------
OK = {"decision": "OK", "reasons": [], "signature": "allele_freq|snp_x"}
FLAGGED = {"decision": "FLAGGED", "reasons": ["k_anon:x"], "signature": "allele_freq|snp_x"}


def _merged(spec_hash="abc"):
    return {**combine([_r("a", 100, 80, 18, 2), _r("b", 100, 70, 25, 5)], ["a", "b"]), "spec_hash": spec_hash}


def test_flagged_rerun_withdraws_the_previous_release(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVER_OUT", str(tmp_path))
    d = overseer_queue.record(SPEC, _merged(), OK)
    assert (d / "released.json").exists()
    overseer_queue.record(SPEC, _merged(), FLAGGED)
    assert not (d / "released.json").exists()
    assert [i["spec_hash"] for i in overseer_queue._load_queue()] == ["abc"]


def test_released_rerun_clears_the_stale_queue_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVER_OUT", str(tmp_path))
    overseer_queue.record(SPEC, _merged(), FLAGGED)
    d = overseer_queue.record(SPEC, _merged(), OK)
    assert overseer_queue._load_queue() == [] and (d / "released.json").exists()


def test_rejection_leaves_nothing_released(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVER_OUT", str(tmp_path))
    overseer_queue.record(SPEC, _merged(), OK)
    d = overseer_queue.record(SPEC, _merged(), FLAGGED)
    overseer_queue.decide("abc", approve=False, note="", by="tester")
    assert not (d / "released.json").exists() and overseer_queue._load_queue() == []


def test_concurrent_decisions_do_not_lose_queue_updates(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVER_OUT", str(tmp_path))
    hashes = [f"h{i:02d}" for i in range(20)]
    for h in hashes:
        overseer_queue.record(SPEC, _merged(h), FLAGGED)
    errors = []

    def decide(h):
        try:
            overseer_queue.decide(h, True, "", "tester")
        except Exception as e:  # collected: a torn queue read surfaces here as JSONDecodeError
            errors.append(e)

    threads = [threading.Thread(target=decide, args=(h,)) for h in hashes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and overseer_queue._load_queue() == []
    assert not list(tmp_path.glob("*.tmp"))  # atomic replacement leaves no temp files behind


def test_every_released_round_needs_the_minimum_number_of_sites():
    # two sites joined, but round 2's coefficients come from one site alone
    merged = {"sites_reported": ["a", "b"], "stats": {}, "n": 100,
              "contributions": {"_rounds": {"1": {"a": 50, "b": 50}, "2": {"a": 50}}}}
    r = disclosure_check.check(merged, {**SPEC, "analysis_type": "fed_logreg", "variables": ["x"], "outcome": "y"})
    assert r["reasons"] == ["min_sites:round=2:1<2"]


def test_released_suppressions_name_the_location_but_not_the_count(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVER_OUT", str(tmp_path))
    m = combine([_r("a", 100, 80, 18, 2, rejected=["snp_x.genotype_counts.2:count=2<5"]), _r("b", 100, 70, 25, 5)], ["a", "b"])
    overseer_queue.record(SPEC, m, disclosure_check.check(m, SPEC))  # flagged: site_suppression
    d = overseer_queue.decide("abc", approve=True, note="", by="tester")
    assert json.loads((d / "released.json").read_text())["rejected_per_site"] == {"a": ["snp_x.genotype_counts.2"]}
    # the internal record keeps the detail for the overseer and the audit trail
    assert json.loads((d / "result.json").read_text())["rejected_per_site"] == {"a": ["snp_x.genotype_counts.2:count=2<5"]}


def test_atomic_writes_keep_normal_file_permissions(tmp_path, monkeypatch):
    import os
    import stat

    monkeypatch.setenv("SERVER_OUT", str(tmp_path))
    d = overseer_queue.record(SPEC, _merged(), OK)
    overseer_queue.record({**SPEC, "min_cell_size": 6}, _merged("other"), FLAGGED)
    umask = os.umask(0)
    os.umask(umask)
    for path in (d / "released.json", overseer_queue.queue_path()):
        assert stat.S_IMODE(path.stat().st_mode) == 0o666 & ~umask, path
