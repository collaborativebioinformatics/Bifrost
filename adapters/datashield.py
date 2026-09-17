"""Adapter for the DataSHIELD-style function API (tres/datashield).

DataSHIELD applies its own disclosure rule (nfilter.tab) before we see the
table; suppressed levels come back as None so the Safe Output filter can audit
them as `tre_native` suppressions rather than silently treating them as zero.
"""
from __future__ import annotations

from adapters._http import HttpAdapter
from harmonisation import local_map


class DataShieldAdapter(HttpAdapter):
    name = "datashield"

    def _ds(self, fn: str, **args) -> dict:
        return self._post("/ds", {"fn": fn, "args": args})

    def schema(self) -> dict[str, str]:
        cols = set(self._ds("ds.colnames")["value"])
        return {c: self._ds("ds.class", x=f"D${l}")["value"] for c, l in local_map(self.tre_id).items() if l in cols}

    def count(self, filters: dict) -> int:
        return self._ds("ds.dim", filter=filters)["value"][0]

    def describe(self, col: str, filters: dict) -> dict[str, float]:
        s = self._ds("ds.sum", x=f"D${col}", filter=filters)
        r = self._ds("ds.range", x=f"D${col}", filter=filters)
        return {"count": s["Nvalid"], "sum": s["Sum"], "sum_sq": s["SumSq"], "min": r["min"], "max": r["max"]}

    def value_counts(self, col: str, filters: dict) -> dict[str, int]:
        t = self._ds("ds.table", x=f"D${col}", filter=filters)
        out: dict = dict(t["counts"])
        for level in t.get("suppressed", []):
            out[level] = None
        return out

    def gram(self, cols: list[str], filters: dict) -> dict:
        return self._ds("ds.crossProd", cols=[f"D${c}" for c in cols], filter=filters)
