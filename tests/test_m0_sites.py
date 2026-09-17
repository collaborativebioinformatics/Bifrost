"""M0: sites.yaml is the single source of truth; generators respect it."""
import json
import subprocess
import sys

import yaml

from harmonisation import load_canonical, local_map
from tests.conftest import ROOT


def test_generated_files_match_sites(sites):
    subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_sites.py")], check=True, cwd=ROOT)
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    project = yaml.safe_load((ROOT / "flare" / "project.yml").read_text())
    for s in sites:
        assert f"tre-{s['tre_id']}" in compose["services"]
        assert f"flare-{s['tre_id']}" in compose["services"]
        assert compose["networks"][f"tre_{s['tre_id']}_internal"] == {"internal": True}
        # the TRE container is on its internal network only
        assert compose["services"][f"tre-{s['tre_id']}"]["networks"] == [f"tre_{s['tre_id']}_internal"]
    clients = [p["name"] for p in project["participants"] if p["type"] == "client"]
    assert clients == [s["tre_id"] for s in sites]


def test_every_site_has_region_and_adapter(sites):
    for s in sites:
        assert s["region"]
        assert (ROOT / "tres" / s["adapter"] / "Dockerfile").exists()


def test_canonical_covers_every_site(sites):
    canon = load_canonical()
    for s in sites:
        lm = local_map(s["tre_id"])
        assert set(lm) == set(canon), f"{s['tre_id']} missing local names"
        assert len(set(lm.values())) == len(lm), f"{s['tre_id']} local names not unique"


def test_ground_truth_and_site_files(sites):
    gt = json.loads((ROOT / "data" / "ground_truth.json").read_text())
    assert set(gt["n_per_site"]) == {s["tre_id"] for s in sites}
    assert sum(gt["n_per_site"].values()) == gt["n_total"]
    for f in gt["allele_freq"].values():
        assert 0 < f < 0.5
