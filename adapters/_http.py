"""Tiny shared HTTP base so adapters can be pointed at a live URL or an
in-process ASGI app (tests) without any code change."""
from __future__ import annotations

import httpx

from adapters.base import TREAdapter


class HttpAdapter(TREAdapter):
    def __init__(self, tre_id: str, region: str = "", api_url: str | None = None, client: httpx.Client | None = None):
        super().__init__(tre_id, region)
        if client is None:
            if not api_url:
                raise ValueError(f"{tre_id}: need api_url or client")
            client = httpx.Client(base_url=api_url, timeout=60)
        self.http = client

    def _post(self, path: str, payload: dict) -> dict:
        r = self.http.post(path, json=payload)
        if r.status_code != 200:
            raise RuntimeError(f"{self.tre_id} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def _get(self, path: str) -> dict:
        r = self.http.get(path)
        r.raise_for_status()
        return r.json()
