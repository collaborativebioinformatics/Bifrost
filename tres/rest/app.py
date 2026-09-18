"""Mock TRE #1 -- generic REST aggregation API.

POST /query  {"cols": [...], "filters": {col: {op: value}}, "agg": "count|sum|sum_sq|min|max|mean|value_counts"}
GET  /schema -> {col: dtype}
GET  /health
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from tres.common import DATA_PATH, TRE_ID, aggregate, apply_filters, dtypes, gram, irls_step, load_data


class Query(BaseModel):
    cols: list[str]
    filters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    agg: Literal["count", "sum", "sum_sq", "min", "max", "mean", "value_counts", "gram"]


class IrlsQuery(BaseModel):
    outcome: str
    features: list[str]
    beta: list[float]
    filters: dict[str, dict[str, Any]] = Field(default_factory=dict)


def create_app(tre_id: str = TRE_ID, data_path: str = DATA_PATH) -> FastAPI:
    app = FastAPI(title=f"TRE {tre_id} (REST)")

    @app.get("/health")
    def health():
        return {"status": "ok", "tre_id": tre_id, "api": "rest", "n": int(len(load_data(data_path)))}

    @app.get("/schema")
    def schema():
        return dtypes(load_data(data_path))

    @app.post("/query")
    def query(q: Query):
        return _query(q, tre_id, data_path)

    @app.post("/irls")
    def irls(q: IrlsQuery):
        return _irls(q, tre_id, data_path)

    return app


def _query(q: Query, tre_id: str, data_path: str):
    df = load_data(data_path)
    unknown = [c for c in q.cols if c not in df.columns]
    if unknown:
        raise HTTPException(400, f"unknown columns {unknown}")
    try:
        sub = apply_filters(df, q.filters)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    if q.agg == "gram":
        return {"tre_id": tre_id, "n": int(len(sub)), "agg": "gram", "result": gram(sub, q.cols)}
    return {"tre_id": tre_id, "n": int(len(sub)), "agg": q.agg, "result": {c: aggregate(sub[c], q.agg) for c in q.cols}}


def _irls(q: IrlsQuery, tre_id: str, data_path: str):
    df = load_data(data_path)
    unknown = [c for c in [q.outcome, *q.features] if c not in df.columns]
    if unknown:
        raise HTTPException(400, f"unknown columns {unknown}")
    try:
        sub = apply_filters(df, q.filters)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"tre_id": tre_id, **irls_step(sub, q.outcome, q.features, q.beta)}


app = create_app()
