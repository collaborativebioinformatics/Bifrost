"""Test fixtures: every TRE from sites.yaml served in-process (no Docker).

`tre_clients` -> {tre_id: (site_cfg, fastapi TestClient)}.  Tests iterate over
sites.yaml, never over a hardcoded list, so adding a site adds test coverage.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.sites import load_sites  # noqa: E402


@pytest.fixture(scope="session")
def sites() -> list[dict]:
    return load_sites()["sites"]


@pytest.fixture(scope="session", autouse=True)
def synthetic_data(sites):
    missing = [s for s in sites if not (ROOT / "data" / "sites" / f"{s['tre_id']}.csv").exists()]
    if missing or not (ROOT / "data" / "ground_truth.json").exists():
        subprocess.run([sys.executable, str(ROOT / "data" / "generate.py")], check=True, cwd=ROOT)


def make_client(site: dict):
    """Fresh app instance bound to this site's data (env is read at import time)."""
    from fastapi.testclient import TestClient

    os.environ["TRE_ID"] = site["tre_id"]
    os.environ["DATA_PATH"] = str(ROOT / "data" / "sites" / f"{site['tre_id']}.csv")
    import tres.common

    importlib.reload(tres.common)
    mod = importlib.import_module(f"tres.{site['adapter']}.app")
    mod = importlib.reload(mod)
    return TestClient(mod.app)


@pytest.fixture(scope="session")
def tre_clients(sites):
    return {s["tre_id"]: (s, make_client(s)) for s in sites}
