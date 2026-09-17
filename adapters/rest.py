"""Adapter for the generic REST aggregation API (tres/rest)."""
from __future__ import annotations

from adapters._http import HttpAdapter
from harmonisation import local_map


class RestAdapter(HttpAdapter):
    name = "rest"

    def schema(self) -> dict[str, str]:
        local = self._get("/schema")
        return {c: local[l] for c, l in local_map(self.tre_id).items() if l in local}

    def _query(self, cols: list[str], filters: dict, agg: str) -> dict:
        return self._post("/query", {"cols": cols, "filters": filters, "agg": agg})

    def count(self, filters: dict) -> int:
        return self._query([], filters, "count")["n"]

    def describe(self, col: str, filters: dict) -> dict[str, float]:
        return {agg: self._query([col], filters, agg)["result"][col] for agg in ("count", "sum", "sum_sq", "min", "max")}

    def value_counts(self, col: str, filters: dict) -> dict[str, int]:
        return self._query([col], filters, "value_counts")["result"][col]

    def gram(self, cols: list[str], filters: dict) -> dict:
        return self._query(cols, filters, "gram")["result"]

    def irls_step(self, outcome_col: str, feature_cols: list[str], beta: list[float], filters: dict) -> dict:
        return self._post("/irls", {"outcome": outcome_col, "features": feature_cols, "beta": beta, "filters": filters})
