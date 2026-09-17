"""Adapter discovery. `adapter` names in sites.yaml resolve here; the FLARE
executor never imports a concrete adapter class."""
from __future__ import annotations

import json
import os
from pathlib import Path

from adapters.base import TREAdapter
from adapters.datashield import DataShieldAdapter
from adapters.rest import RestAdapter
from adapters.sql import SqlAdapter
from scripts.sites import ROOT, site

REGISTRY: dict[str, type[TREAdapter]] = {
    RestAdapter.name: RestAdapter,
    DataShieldAdapter.name: DataShieldAdapter,
    SqlAdapter.name: SqlAdapter,
}


def get(name: str) -> type[TREAdapter]:
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown adapter {name!r}; known: {sorted(REGISTRY)}")


def resolve_api_url(tre_id: str, default: str) -> str:
    """TRE_API_URL env (inside the TRE container) > data/sites/local_urls.json (dev_tres.py) > sites.yaml."""
    if os.environ.get("TRE_API_URL"):
        return os.environ["TRE_API_URL"]
    local = Path(ROOT) / "data" / "sites" / "local_urls.json"
    if local.exists():
        urls = json.loads(local.read_text())
        if tre_id in urls:
            return urls[tre_id]
    return default


def load(tre_id: str, **kwargs) -> TREAdapter:
    """Build the adapter for one site from sites.yaml."""
    s = site(tre_id)
    cls = get(s["adapter"])
    kwargs.setdefault("api_url", resolve_api_url(tre_id, s["api_url"]))
    return cls(tre_id=tre_id, region=s["region"], **kwargs)
