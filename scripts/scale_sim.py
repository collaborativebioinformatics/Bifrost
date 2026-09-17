#!/usr/bin/env python
"""Scale evidence: the allele-frequency job in the FLARE simulator with N sites.

  python scripts/scale_sim.py [--sizes 3,10,50,100] [--rows-per-site 1000] [--threads 8]

For each N: generate sites.yaml with N sites (adapters cycled rest/datashield/sql,
regions cycled), split synthetic data, serve every TRE from ONE process
(scripts/dev_tres.py --single-process), run the job with N FLARE clients,
check the result against that dataset's ground truth, record timings.
Everything lives under a scratch dir; the real sites.yaml is untouched.

Out: docs/scaling.png, docs/scaling.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
ADAPTERS = ["rest", "datashield", "sql"]
REGIONS = ["nordic", "west-eu", "south-eu", "north-am"]


def make_sites(n: int, path: Path) -> list[str]:
    base = yaml.safe_load((ROOT / "sites.yaml").read_text())
    sites = [{"tre_id": f"site{i:03d}", "adapter": ADAPTERS[i % len(ADAPTERS)], "region": REGIONS[i % len(REGIONS)],
              "api_url": f"http://tre-site{i:03d}:8000", "org": f"org{i:03d}", "weight": 1 + (i % 3)} for i in range(n)]
    path.write_text(yaml.safe_dump({"server": base["server"], "project": {"name": "scale_sim"}, "sites": sites}, sort_keys=False))
    return [s["tre_id"] for s in sites]


def wait_health(urls: dict[str, str], timeout: float = 120) -> None:
    t0 = time.time()
    for tid, url in urls.items():
        while True:
            try:
                if httpx.get(f"{url}/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.time() - t0 > timeout:
                raise RuntimeError(f"{tid} not up")
            time.sleep(0.2)


def run_one(n: int, rows_per_site: int, threads: int, work: Path, port: int) -> dict:
    d = work / f"n{n}"
    d.mkdir(parents=True)
    sites_yaml, data_dir, out_dir = d / "sites.yaml", d / "data", d / "out"
    ids = make_sites(n, sites_yaml)
    env = {**os.environ, "SITES_PATH": str(sites_yaml), "LOCAL_URLS": str(d / "local_urls.json"),
           "SERVER_OUT": str(out_dir), "AUDIT_DIR": str(d / "audit"), "PYTHONPATH": str(ROOT)}
    subprocess.run([sys.executable, "data/generate.py", "--n", str(rows_per_site * n), "--out", str(data_dir)],
                   cwd=ROOT, env=env, check=True, capture_output=True)
    gt = json.loads((data_dir / "ground_truth.json").read_text())
    # SNPs common enough that no site suppresses a genotype cell at k=5
    snps = [s for s, f in gt["allele_freq"].items() if f > 0.2][:5]
    spec = {"analysis_type": "allele_freq", "variables": snps, "project_id": "hackathon-test", "min_cell_size": 5}
    (d / "spec.json").write_text(json.dumps(spec))

    tres = subprocess.Popen([sys.executable, "scripts/dev_tres.py", "--single-process", "--data-dir", str(data_dir / "sites"), "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(300):
            if (d / "local_urls.json").exists():
                break
            time.sleep(0.2)
        wait_health(json.loads((d / "local_urls.json").read_text()))

        job = d / "job"
        subprocess.run([sys.executable, "scripts/build_job.py", str(d / "spec.json"), "--out", str(job),
                        "--min-clients", str(max(2, int(0.8 * n))), "--wait-time", "120"],
                       cwd=ROOT, env=env, check=True, capture_output=True)

        t0 = time.time()
        r = subprocess.run([sys.executable, "-m", "nvflare.private.fed.app.simulator.simulator", str(job), "-w", str(d / "ws"),
                            "-n", str(n), "-c", ",".join(ids), "-t", str(min(threads, n)), "-l", "concise"],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=1800)
        wall = time.time() - t0
        if r.returncode != 0:
            raise RuntimeError(r.stdout[-2000:] + r.stderr[-2000:])
    finally:
        tres.terminate()
        tres.wait(timeout=10)

    res = json.loads(next(p for p in out_dir.iterdir() if p.is_dir()).joinpath("result.json").read_text())
    # exactness is judged against the pooled truth of the sites that actually reported
    rep = res["sites_reported"]
    err = max(abs(res["stats"][v]["allele_freq"] - sum(gt["site_allele_freq"][t][v] * gt["n_per_site"][t] for t in rep)
                  / sum(gt["n_per_site"][t] for t in rep)) for v in snps)
    row = {"n_sites": n, "rows_total": gt["n_total"], "coverage": res["coverage"], "sites_missing": res["sites_missing"], "round_s": res["timing"]["round_s"],
           "merge_s": res["timing"]["merge_s"], "simulator_wall_s": round(wall, 1), "max_abs_err": err,
           "regions": len(set(REGIONS[i % len(REGIONS)] for i in range(n))), "threads": min(threads, n)}
    print(json.dumps(row), flush=True)
    shutil.rmtree(d / "ws", ignore_errors=True)
    return row


def plot(rows: list[dict], png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = [r["n_sites"] for r in rows]
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.plot(n, [r["round_s"] for r in rows], "o-", label="federated round (broadcast → all results in)")
    ax.plot(n, [r["simulator_wall_s"] for r in rows], "s--", color="grey", label="whole simulator run (incl. start-up)")
    ax.set_xscale("log")
    ax.set_xlabel("number of TREs (FLARE clients)")
    ax.set_ylabel("seconds")
    ax.set_xticks(n)
    ax.set_xticklabels([str(x) for x in n])
    ax.grid(alpha=0.3, which="both")
    ax.legend(loc="upper left", fontsize=9)
    ok = all(r["max_abs_err"] < 1e-9 for r in rows)
    ax.set_title(f"Allele-frequency job vs. federation size  (result exact at every N: {ok}, "
                 f"{rows[0]['threads']}–{rows[-1]['threads']} client threads)", fontsize=10)
    for r in rows:
        ax.annotate(f"{r['round_s']:.1f}s", (r["n_sites"], r["round_s"]), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(png, dpi=150)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="3,10,50,100")
    ap.add_argument("--rows-per-site", type=int, default=1000)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--out", type=Path, default=ROOT / "docs")
    ap.add_argument("--work", type=Path, default=None)
    a = ap.parse_args()
    work = a.work or Path(tempfile.mkdtemp(prefix="scale_sim_"))
    print(f"work dir {work}")
    rows = [run_one(int(n), a.rows_per_site, a.threads, work, 8800 + i * 10) for i, n in enumerate(a.sizes.split(","))]
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "scaling.json").write_text(json.dumps(rows, indent=2))
    plot(rows, a.out / "scaling.png")
    print(f"\n{'N':>5} {'round s':>8} {'sim wall s':>10} {'max err':>9}  coverage   (err = federated vs pooled truth over reporting sites)")
    for r in rows:
        print(f"{r['n_sites']:>5} {r['round_s']:>8.2f} {r['simulator_wall_s']:>10.1f} {r['max_abs_err']:>9.1e}  {r['coverage']}")
    print(f"wrote {a.out / 'scaling.png'}, {a.out / 'scaling.json'}")


if __name__ == "__main__":
    main()
