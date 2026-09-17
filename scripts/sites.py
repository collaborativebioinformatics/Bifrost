"""Single loader for sites.yaml. Nothing else may list sites."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = Path(os.environ.get("SITES_PATH", ROOT / "sites.yaml"))  # override only for simulations


def load_sites(path: Path = SITES_PATH) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    ids = [s["tre_id"] for s in cfg["sites"]]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate tre_id in {path}: {ids}")
    for s in cfg["sites"]:
        for k in ("tre_id", "adapter", "api_url", "region"):
            if k not in s:
                raise ValueError(f"site {s} missing required field {k!r}")
        s.setdefault("weight", 1)
        s.setdefault("org", s["tre_id"])
    return cfg


def site(tre_id: str, path: Path = SITES_PATH) -> dict:
    for s in load_sites(path)["sites"]:
        if s["tre_id"] == tre_id:
            return s
    raise KeyError(f"unknown tre_id {tre_id!r} (not in {path})")
