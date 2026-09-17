"""Shared bits for the mock TRE servers: data loading, filter semantics, safe
aggregates. Each TRE exposes a *different* API on top of this; the adapters in
adapters/ are what make them look the same to the federation."""
from __future__ import annotations

import operator
import os
from functools import lru_cache
from typing import Any

import pandas as pd

OPS = {
    "==": operator.eq, "!=": operator.ne,
    ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
    "in": lambda s, v: s.isin(v),
}

TRE_ID = os.environ.get("TRE_ID", "local")
DATA_PATH = os.environ.get("DATA_PATH", f"data/sites/{TRE_ID}.csv")


@lru_cache(maxsize=None)
def _read(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    # Row identifiers are never queryable: drop them at load time.
    return df.drop(columns=[c for c in df.columns if c == "row_id"])


def load_data(path: str = DATA_PATH) -> pd.DataFrame:
    """Each app binds its own DATA_PATH at import and passes it explicitly, so
    several TRE apps can coexist in one process (tests, dev runner)."""
    return _read(path)


def dtypes(df: pd.DataFrame) -> dict[str, str]:
    return {c: str(t) for c, t in df.dtypes.items()}


def apply_filters(df: pd.DataFrame, filters: dict[str, dict[str, Any]] | None) -> pd.DataFrame:
    """filters = {local_col: {op: value, ...}, ...}  e.g. {"AGE": {">=": 40, "<": 65}}"""
    if not filters:
        return df
    mask = pd.Series(True, index=df.index)
    for col, conds in filters.items():
        if col not in df.columns:
            raise KeyError(f"unknown column {col!r}")
        for op, val in conds.items():
            if op not in OPS:
                raise ValueError(f"unsupported operator {op!r}")
            mask &= OPS[op](df[col], val)
    return df[mask]


def _f(x) -> float | None:
    x = float(x)
    return None if x != x else x  # NaN (empty selection) is not JSON


def aggregate(series: pd.Series, agg: str) -> Any:
    if agg == "count":
        return int(series.count())
    if agg == "sum":
        return _f(series.sum())
    if agg == "sum_sq":
        return _f((series.astype(float) ** 2).sum())
    if agg == "min":
        return _f(series.min())
    if agg == "max":
        return _f(series.max())
    if agg == "mean":
        return _f(series.mean())
    if agg == "value_counts":
        return {str(k): int(v) for k, v in series.value_counts().sort_index().items()}
    raise ValueError(f"unsupported agg {agg!r}")


def gram(df: pd.DataFrame, cols: list[str]) -> dict:
    """Cross-product matrix of [1, *cols]; the sufficient statistic for OLS / means / variances."""
    import numpy as np

    X = np.column_stack([np.ones(len(df))] + [df[c].to_numpy(float) for c in cols])
    return {"n": int(len(df)), "cols": ["1", *cols], "matrix": (X.T @ X).tolist()}
