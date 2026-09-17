"""Common demo API. From the repository root:

    SERVER_OUT=server/api_out uvicorn server.api:app --port 8080 --workers 1

Start mock TREs separately with `python scripts/dev_tres.py`. Neither TREs nor
FLARE are required for startup or /health. API_TRE_TIMEOUT (seconds per HTTP
operation, default 10) and API_TRE_WORKERS (default 8) control adapter fan-out.
Use an exclusive SERVER_OUT directory; see analysis_service's concurrency and
demo-versus-secure-deployment notes. Variant names are canonical (snp_rs001),
not arbitrary rsIDs. Requests contain variable names, never participant data.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harmonisation import load_canonical
from server import analysis_service
from spec.analysis_spec import AnalysisSpec

app = FastAPI(title="Bifrost federated analysis demo", description=__doc__)
LOG = logging.getLogger(__name__)


class RegressionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    features: list[str] = Field(min_length=1)
    target: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    filters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    min_cell_size: int = Field(default=5, ge=5)


def _execute(**kwargs) -> JSONResponse:
    try:
        spec = AnalysisSpec(**kwargs)
        canonical = load_canonical()
        names = [*spec.variables, *spec.filters, *([spec.outcome] if spec.outcome else [])]
        if any(name not in canonical for name in names):
            raise ValueError("Use known canonical variable names")
        if len(set(spec.variables)) != len(spec.variables):
            raise ValueError("Features must be unique")
        if spec.analysis_type == "allele_freq" and canonical[spec.variables[0]]["type"] != "genotype":
            raise ValueError("Variant must be a genotype variable")
        for conditions in spec.filters.values():
            for op, value in conditions.items():
                if op not in {"==", "!=", ">", ">=", "<", "<=", "in"}:
                    raise ValueError("Unsupported filter operator")
                if op == "in" and not isinstance(value, list):
                    raise ValueError("The in operator requires a list")
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        status, body = analysis_service.analyse(spec)
        return JSONResponse(body, status_code=status)
    except Exception:
        # Fail closed if merge, configuration, disclosure or audit persistence fails.
        LOG.exception("Analysis failed")
        return JSONResponse({"status": "failed", "error": "analysis_failed",
                             "spec_hash": spec.spec_hash()}, status_code=500)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/allele-frequency")
def allele_frequency(variant: str = Query(min_length=1),
                     project_id: str = Query(min_length=1),
                     min_cell_size: int = Query(default=5, ge=5)):
    return _execute(analysis_type="allele_freq", variables=[variant],
                    project_id=project_id, min_cell_size=min_cell_size)


@app.post("/linear-regression")
def linear_regression(request: RegressionRequest):
    return _execute(analysis_type="fed_linreg", variables=request.features,
                    outcome=request.target, project_id=request.project_id,
                    filters=request.filters, min_cell_size=request.min_cell_size)
