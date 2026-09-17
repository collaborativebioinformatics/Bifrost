"""Mock TRE #3 -- read-only SQL gateway over DuckDB.

POST /sql    {"sql": "SELECT count(*) AS n FROM cohort WHERE AGE >= 40"}
GET  /schema -> {col: dtype}
GET  /health

Guardrails: single read-only SELECT statement, no row-level SELECT * (the
statement must aggregate), row cap on the result. Table name is always `cohort`.
"""
from __future__ import annotations

import re

import duckdb
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from tres.common import DATA_PATH, TRE_ID, load_data

MAX_ROWS = 1000
AGG_FUNCS = re.compile(r"\b(count|sum|avg|min|max|var_samp|var_pop|stddev|group by)\b", re.I)
FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|create|alter|copy|export|attach|install|load|pragma|call)\b|;", re.I)

_cons: dict[str, duckdb.DuckDBPyConnection] = {}


def con(data_path: str) -> duckdb.DuckDBPyConnection:
    if data_path not in _cons:
        c = duckdb.connect(database=":memory:")
        c.register("cohort", load_data(data_path))
        _cons[data_path] = c
    return _cons[data_path]


class SQL(BaseModel):
    sql: str


def create_app(tre_id: str = TRE_ID, data_path: str = DATA_PATH) -> FastAPI:
    app = FastAPI(title=f"TRE {tre_id} (SQL)")

    @app.get("/health")
    def health():
        return {"status": "ok", "tre_id": tre_id, "api": "sql", "n": int(len(load_data(data_path)))}

    @app.get("/schema")
    def schema():
        rows = con(data_path).execute("DESCRIBE cohort").fetchall()
        return {r[0]: r[1] for r in rows}

    @app.post("/sql")
    def sql(q: SQL):
        return _sql(q, tre_id, data_path)

    return app


def _sql(q: SQL, tre_id: str, data_path: str):
    stmt = q.sql.strip()
    if not stmt.lower().startswith("select"):
        raise HTTPException(400, "only SELECT statements are allowed")
    if FORBIDDEN.search(stmt):
        raise HTTPException(400, "statement contains a forbidden keyword or ';'")
    if not AGG_FUNCS.search(stmt):
        raise HTTPException(400, "statement must aggregate (count/sum/avg/min/max/... or GROUP BY); row-level SELECT is not allowed")
    try:
        cur = con(data_path).execute(stmt)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS + 1)
    except duckdb.Error as e:
        raise HTTPException(400, f"sql error: {e}")
    if len(rows) > MAX_ROWS:
        raise HTTPException(400, f"result exceeds {MAX_ROWS} rows")
    return {"tre_id": tre_id, "columns": cols, "rows": [list(r) for r in rows]}


app = create_app()
