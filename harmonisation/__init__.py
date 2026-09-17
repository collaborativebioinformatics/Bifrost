"""Harmonisation map loader: canonical variable <-> local column per TRE."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CANONICAL_PATH = ROOT / "harmonisation" / "canonical.yaml"


@lru_cache(maxsize=1)
def load_canonical(path: Path = CANONICAL_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)["variables"]


def local_name(canonical: str, tre_id: str, path: Path = CANONICAL_PATH) -> str:
    """Local column for a canonical variable at one site. A site with no entry
    under `local:` is assumed to use the canonical name itself (the onboarding
    default; the site's data steward then edits canonical.yaml)."""
    var = load_canonical(path).get(canonical)
    if var is None:
        raise KeyError(f"unknown canonical variable {canonical!r}")
    return (var.get("local") or {}).get(tre_id, canonical)


def local_map(tre_id: str, path: Path = CANONICAL_PATH) -> dict[str, str]:
    """canonical -> local for one site."""
    return {c: local_name(c, tre_id, path) for c in load_canonical(path)}


def var_type(canonical: str, path: Path = CANONICAL_PATH) -> str:
    return load_canonical(path)[canonical]["type"]
