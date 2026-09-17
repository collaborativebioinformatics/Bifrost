"""Mock TRE #2 -- DataSHIELD-style function-call API (Python stand-in for Opal/R).

POST /ds  {"fn": "ds.mean", "args": {"x": "D$age_years", "filter": {...}}}
Functions mimic DataSHIELD's server-side "assign/aggregate" style; the data
frame is always called D, columns addressed as D$col. DataSHIELD's own
disclosure setting (nfilter.tab, min cell count) is enforced here too, so the
TRE has its *native* protection underneath our Safe Output layer.

  ds.dim(D)                     -> [nrows, ncols]
  ds.colnames(D)                -> [cols]
  ds.class(D$x)                 -> "numeric" | "integer"
  ds.length(D$x)                -> n non-missing
  ds.mean(D$x)                  -> {"EstimatedMean", "Nvalid", "Nmissing", "Ntotal"}
  ds.var(D$x)                   -> {"EstimatedVar", "Nvalid", ...}
  ds.sum(D$x)                   -> {"Sum", "SumSq", "Nvalid"}       (not in real DataSHIELD; needed for federated stats)
  ds.table(D$x)                 -> {"counts": {level: n}, "suppressed": [levels]}   cells < nfilter.tab hidden
  ds.range(D$x)                 -> {"min", "max"}  (DataSHIELD returns jittered range; we return exact + flag)
  ds.crossProd(cols=[D$x,...])  -> {"n", "cols", "matrix"}  cross-product of [1, x...] (cf. ds.glm's score/information exchange)
  ds.irls(outcome=D$y, features=[D$x,...], beta=[...])
                                 -> {"n", "grad", "hess"}  one Newton step's worth of the
                                    logistic log-likelihood at beta (cf. ds.glm's IRLS exchange)
"""
from __future__ import annotations

import os
import re
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from tres.common import DATA_PATH, TRE_ID, apply_filters, dtypes, gram, irls_step, load_data

NFILTER_TAB = int(os.environ.get("DS_NFILTER_TAB", "3"))  # DataSHIELD default min cell count

class Call(BaseModel):
    fn: str
    args: dict[str, Any] = Field(default_factory=dict)


def create_app(tre_id: str = TRE_ID, data_path: str = DATA_PATH) -> FastAPI:
    app = FastAPI(title=f"TRE {tre_id} (DataSHIELD)")

    @app.get("/health")
    def health():
        return {"status": "ok", "tre_id": tre_id, "api": "datashield", "n": int(len(load_data(data_path))), "nfilter.tab": NFILTER_TAB}

    @app.post("/ds")
    def ds(call: Call):
        return _ds(call, data_path)

    return app


def _col(expr: str) -> str:
    m = re.fullmatch(r"D\$([A-Za-z_][A-Za-z0-9_]*)", expr or "")
    if not m:
        raise HTTPException(400, f"expected D$<column>, got {expr!r}")
    return m.group(1)


def _series(args: dict, data_path: str):
    df = apply_filters(load_data(data_path), args.get("filter"))
    col = _col(args.get("x", ""))
    if col not in df.columns:
        raise HTTPException(400, f"unknown column {col!r}")
    return df[col]


def _ds(call: Call, data_path: str):
    fn, a = call.fn, call.args
    try:
        if fn == "ds.dim":
            df = apply_filters(load_data(data_path), a.get("filter"))
            return {"value": [int(len(df)), int(df.shape[1])]}
        if fn == "ds.colnames":
            return {"value": list(load_data(data_path).columns)}
        if fn == "ds.class":
            t = dtypes(load_data(data_path))[_col(a["x"])]
            return {"value": "integer" if t.startswith("int") else "numeric"}
        if fn == "ds.length":
            return {"value": int(_series(a, data_path).count())}
        if fn == "ds.mean":
            s = _series(a, data_path)
            return {"EstimatedMean": float(s.mean()), "Nvalid": int(s.count()), "Nmissing": int(s.isna().sum()), "Ntotal": int(len(s))}
        if fn == "ds.var":
            s = _series(a, data_path)
            return {"EstimatedVar": float(s.var()), "Nvalid": int(s.count()), "Nmissing": int(s.isna().sum()), "Ntotal": int(len(s))}
        if fn == "ds.sum":
            s = _series(a, data_path).astype(float)
            return {"Sum": float(s.sum()), "SumSq": float((s ** 2).sum()), "Nvalid": int(s.count())}
        if fn == "ds.range":
            s = _series(a, data_path)
            return {"min": float(s.min()), "max": float(s.max()), "exact": True}
        if fn == "ds.crossProd":
            df = apply_filters(load_data(data_path), a.get("filter"))
            cols = [_col(x) for x in a.get("cols", [])]
            missing = [c for c in cols if c not in df.columns]
            if missing:
                raise HTTPException(400, f"unknown columns {missing}")
            return gram(df, cols)
        if fn == "ds.irls":
            df = apply_filters(load_data(data_path), a.get("filter"))
            outcome = _col(a["outcome"])
            features = [_col(x) for x in a.get("features", [])]
            missing = [c for c in [outcome, *features] if c not in df.columns]
            if missing:
                raise HTTPException(400, f"unknown columns {missing}")
            return irls_step(df, outcome, features, a["beta"])
        if fn == "ds.table":
            vc = _series(a, data_path).value_counts().sort_index()
            counts = {str(k): int(v) for k, v in vc.items() if v >= NFILTER_TAB}
            suppressed = [str(k) for k, v in vc.items() if v < NFILTER_TAB]
            return {"counts": counts, "suppressed": suppressed, "nfilter.tab": NFILTER_TAB}
    except KeyError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    raise HTTPException(400, f"unknown function {fn!r}")


app = create_app()
