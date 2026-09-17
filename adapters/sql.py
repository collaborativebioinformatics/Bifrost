"""Adapter for the read-only SQL gateway (tres/sql). Filters are rendered to a
WHERE clause with literal values (the gateway only accepts aggregating SELECTs)."""
from __future__ import annotations

import re

from adapters._http import HttpAdapter
from harmonisation import local_map

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _lit(v) -> str:
    if isinstance(v, (list, tuple)):
        return "(" + ", ".join(_lit(x) for x in v) + ")"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def where(filters: dict) -> str:
    if not filters:
        return ""
    ops = {"==": "=", "!=": "<>", ">": ">", ">=": ">=", "<": "<", "<=": "<=", "in": "IN"}
    parts = []
    for col, conds in filters.items():
        if not _IDENT.match(col):
            raise ValueError(f"bad column {col!r}")
        for op, val in conds.items():
            parts.append(f"{col} {ops[op]} {_lit(val)}")
    return " WHERE " + " AND ".join(parts)


class SqlAdapter(HttpAdapter):
    name = "sql"

    def _sql(self, sql: str) -> list[list]:
        return self._post("/sql", {"sql": sql})["rows"]

    def schema(self) -> dict[str, str]:
        local = self._get("/schema")
        return {c: local[l] for c, l in local_map(self.tre_id).items() if l in local}

    def count(self, filters: dict) -> int:
        return int(self._sql(f"SELECT count(*) FROM cohort{where(filters)}")[0][0])

    def describe(self, col: str, filters: dict) -> dict[str, float]:
        if not _IDENT.match(col):
            raise ValueError(f"bad column {col!r}")
        row = self._sql(
            f"SELECT count({col}), sum({col}), sum({col}*{col}), min({col}), max({col}) FROM cohort{where(filters)}"
        )[0]
        return dict(zip(("count", "sum", "sum_sq", "min", "max"), (int(row[0]), *map(float, row[1:]))))

    def value_counts(self, col: str, filters: dict) -> dict[str, int]:
        if not _IDENT.match(col):
            raise ValueError(f"bad column {col!r}")
        rows = self._sql(f"SELECT {col}, count(*) FROM cohort{where(filters)} GROUP BY {col} ORDER BY {col}")
        return {str(level): int(n) for level, n in rows}

    def gram(self, cols: list[str], filters: dict) -> dict:
        for c in cols:
            if not _IDENT.match(c):
                raise ValueError(f"bad column {c!r}")
        terms = ["1", *cols]
        exprs = [f"sum(({a})*({b}))" for a in terms for b in terms]
        row = self._sql(f"SELECT count(*), {', '.join(exprs)} FROM cohort{where(filters)}")[0]
        k = len(terms)
        vals = [float(v) for v in row[1:]]
        return {"n": int(row[0]), "cols": terms, "matrix": [vals[i * k:(i + 1) * k] for i in range(k)]}

    def irls_step(self, outcome_col: str, feature_cols: list[str], beta: list[float], filters: dict) -> dict:
        # Nonlinear per-row transform parameterised by beta -- not expressible as one
        # aggregating SELECT, so this goes through the gateway's dedicated /irls endpoint
        # instead of the generic SQL passthrough (same trust boundary as /schema).
        return self._post("/irls", {"outcome": outcome_col, "features": feature_cols, "beta": beta, "filters": filters})
