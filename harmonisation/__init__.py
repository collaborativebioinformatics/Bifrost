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
    var = load_canonical(path).get(canonical)
    if var is None:
        raise KeyError(f"unknown canonical variable {canonical!r}")
    try:
        return var["local"][tre_id]
    except KeyError:
        raise KeyError(f"canonical variable {canonical!r} has no local name for site {tre_id!r}")


def local_map(tre_id: str, path: Path = CANONICAL_PATH) -> dict[str, str]:
    """canonical -> local for one site (only variables that site has)."""
    return {c: v["local"][tre_id] for c, v in load_canonical(path).items() if tre_id in v["local"]}


def var_type(canonical: str, path: Path = CANONICAL_PATH) -> str:
    return load_canonical(path)[canonical]["type"]
