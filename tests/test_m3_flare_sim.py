"""M3 end-to-end in the FLARE simulator: real TRE processes over HTTP, one FLARE
client per site (client name == tre_id), controller merges + checks + records.
Slow (~10 s); skipped with -m 'not slow'."""
import json
import os
import subprocess
import sys
import time

import httpx
import pytest

from tests.conftest import ROOT

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def tres():
    env = {**os.environ, "DEV_BASE_PORT": "8900"}
    p = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev_tres.py")], env=env, cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    urls = ROOT / "data" / "sites" / "local_urls.json"
    for _ in range(100):
        try:
            if urls.exists() and all(httpx.get(u + "/health", timeout=1).status_code == 200 for u in json.loads(urls.read_text()).values()):
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    else:
        p.terminate()
        pytest.fail("TREs did not start")
    yield json.loads(urls.read_text())
    p.terminate()
    p.wait(timeout=10)


def _simulate(job, workspace, out, clients):
    env = {**os.environ, "SERVER_OUT": str(out), "AUDIT_DIR": str(out / "audit")}
    r = subprocess.run([sys.executable, "-m", "nvflare.private.fed.app.simulator.simulator", str(job), "-w", str(workspace),
                        "-n", str(len(clients)), "-c", ",".join(clients), "-t", str(len(clients)), "-l", "concise"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r.stdout


def test_allele_freq_end_to_end(tres, sites, tmp_path):
    from scripts.build_job import build

    job = build(ROOT / "spec" / "examples" / "allele_freq.json", tmp_path / "job", min_clients=2, wait_time=2)
    out = tmp_path / "out"
    _simulate(job, tmp_path / "ws", out, [s["tre_id"] for s in sites])
    runs = [d for d in out.iterdir() if d.is_dir() and d.name != "audit"]
    assert len(runs) == 1
    res = json.loads((runs[0] / "result.json").read_text())
    assert res["coverage"] == f"{len(sites)}/{len(sites)} sites"
    gt = json.loads((ROOT / "data" / "ground_truth.json").read_text())
    for v, st in res["stats"].items():
        assert abs(st["allele_freq"] - gt["allele_freq"][v]) < 1e-12
    assert (runs[0] / "released.json").exists()
    v = subprocess.run([sys.executable, "scripts/verify.py", str(runs[0] / "result.json")], cwd=ROOT, capture_output=True, text=True)
    assert v.returncode == 0 and "PASS" in v.stdout


def test_straggler_yields_partial_coverage(tres, sites, tmp_path):
    """A client whose TRE is unreachable fails; the round still completes with k/N coverage."""
    from scripts.build_job import build

    job = build(ROOT / "spec" / "examples" / "allele_freq.json", tmp_path / "job", min_clients=2, wait_time=2)
    out = tmp_path / "out"
    clients = [s["tre_id"] for s in sites] + ["ghost"]  # a provisioned site that never registered a TRE
    _simulate(job, tmp_path / "ws", out, clients)
    res = json.loads(next(d for d in out.iterdir() if d.is_dir() and d.name != "audit").joinpath("result.json").read_text())
    assert res["coverage"] == f"{len(sites)}/{len(clients)} sites" and res["sites_missing"] == ["ghost"]
