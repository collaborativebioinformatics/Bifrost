"""Server-side wire contracts: what the orchestrator writes and what the HTTP API
serves. The site-side contract (AggregateResult) lives in adapters/base.py
because it ships inside the TRE. JSON Schemas for all of them are exported by
scripts/export_schemas.py into docs/schemas/."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from adapters.base import AlleleCounts


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OLS(_Strict):
    outcome: str | None = None
    coef: dict[str, float] | None = None  # intercept + features, original scale
    error: str | None = None  # e.g. singular normal equations


class LogReg(_Strict):
    outcome: str | None = None
    coef: dict[str, float] | None = None  # intercept + features, log-odds scale


class Round(_Strict):
    """One FedAvg (fed_linreg) or Newton-Raphson (fed_logreg) round."""

    round: int
    sites: int
    max_delta: float
    coef: dict[str, float]


class MergedVariableStats(_Strict):
    """One canonical variable after the associative merge. Same allow-list as
    VariableStats plus the derived quantities the server is allowed to publish."""

    genotype_counts: dict[str, int] | None = None
    histogram: dict[str, int] | None = None
    allele_counts: AlleleCounts | None = None
    allele_freq: float | None = None
    allele_freq_sites: list[str] | None = None
    count: int | None = None
    sum: float | None = None
    sum_sq: float | None = None
    mean: float | None = None
    var: float | None = None
    min: float | None = None
    max: float | None = None
    ols: OLS | None = None
    logreg: LogReg | None = None
    n: int | None = None
    history: list[Round] | None = None


class Timing(_Strict):
    round_s: float | None = None
    merge_s: float | None = None


class Method(_Strict):
    mode: Literal["exact", "fedavg", "newton_raphson"]
    rounds: int | None = None
    local_steps: int | None = None  # fedavg
    lr: float | None = None  # fedavg
    ridge: float | None = None  # newton_raphson: Hessian damping
    tol: float | None = None  # newton_raphson: convergence tolerance on max |Δβ|


class ReleasedResult(_Strict):
    """What leaves the server after the disclosure check / overseer approval."""

    spec_hash: str
    coverage: str  # "k/N sites"
    sites_expected: list[str]
    sites_reported: list[str]
    sites_missing: list[str]
    sites_failed: dict[str, str] = Field(default_factory=dict)  # tre_id -> timeout | unavailable | adapter_error | rc
    n: int = Field(ge=0)
    n_per_site: dict[str, int]
    region_per_site: dict[str, str] = Field(default_factory=dict)
    rejected_per_site: dict[str, list[str]] = Field(default_factory=dict)
    stats: dict[str, MergedVariableStats]
    method: Method | None = None
    timing: Timing | None = None
    sites_missing_rounds: dict[str, list[str]] | None = None


class MergedResult(ReleasedResult):
    """ReleasedResult plus server-internal per-site contributions (never released)."""

    contributions: dict[str, dict[str, dict[str, int]]] = Field(default_factory=dict)


class DisclosureCheck(_Strict):
    decision: Literal["OK", "FLAGGED"]
    reasons: list[str]
    signature: str | None = None


# ---- HTTP API ---------------------------------------------------------------------
PublicReason = Literal["min_sites", "site_suppression", "k_anon", "dominance", "differencing",
                       "analysis", "disclosure_review_required"]
QUEUED_REVISION = "overseer queue revision, present when the result was queued for review"


class AnalysisResponse(_Strict):
    """Response of the analysis endpoints. `result` is present only when the
    disclosure check passed; flagged runs wait in the overseer queue."""

    spec_hash: str
    status: Literal["completed", "flagged", "failed"]
    decision: Literal["OK", "FLAGGED"]
    reasons: list[PublicReason]
    sites_expected: list[str]
    sites_reported: list[str]
    sites_missing: list[str]
    sites_failed: dict[str, str]
    coverage: str
    result: ReleasedResult | None = None
    revision: str | None = Field(default=None, description=QUEUED_REVISION)
    error: str | None = None


class RunRequest(_Strict):
    """POST /run body: an AnalysisSpec plus how to fit a fed_linreg. A fed_logreg
    runs Newton-Raphson with the service defaults (25 rounds, tol 1e-8)."""

    analysis_type: Literal["allele_freq", "fed_stats", "fed_linreg", "fed_logreg"]
    variables: list[str] = Field(min_length=1)
    filters: dict[str, dict[str, object]] = Field(default_factory=dict)
    min_cell_size: int = Field(default=5, ge=1)
    project_id: str = Field(min_length=1)
    outcome: str | None = None
    fedavg_rounds: int = Field(default=0, ge=0, description="fed_linreg only: 0 = exact (summed Gram), >0 = FedAvg rounds")


class RunStatus(_Strict):
    run_id: str
    # a flagged run becomes completed (APPROVED, with its result) or rejected once an overseer decides
    status: Literal["queued", "running", "completed", "flagged", "rejected", "failed"]
    submitted: str
    finished: str | None = None
    spec_hash: str | None = None
    decision: Literal["OK", "FLAGGED", "APPROVED", "REJECTED"] | None = None
    reasons: list[PublicReason] = Field(default_factory=list)
    sites_expected: list[str] = Field(default_factory=list)
    sites_reported: list[str] = Field(default_factory=list)
    sites_missing: list[str] = Field(default_factory=list)
    sites_failed: dict[str, str] = Field(default_factory=dict)
    coverage: str | None = None
    result: ReleasedResult | None = None
    revision: str | None = Field(default=None, description=QUEUED_REVISION)
    error: str | None = None


class VariableInfo(_Strict):
    name: str
    type: Literal["continuous", "binary", "genotype"]
    unit: str = ""
    description: str = ""


class ProjectInfo(_Strict):
    id: str
    description: str = ""


class SiteInfo(_Strict):
    tre_id: str
    adapter: str
    region: str
    online: bool | None = None  # None = not probed


class Metadata(_Strict):
    variables: list[VariableInfo]
    projects: list[ProjectInfo]
    examples: list[str]
    sites: list[SiteInfo]
    analysis_types: list[str]


class OverseerItem(_Strict):
    id: str  # == spec_hash
    spec_hash: str
    revision: str = Field(description="fresh per queued result; a decision must name it, and a rerun of the spec replaces it")
    queued: str
    project_id: str | None = None
    analysis_type: str | None = None
    reasons: list[str]


class OverseerQueue(_Strict):
    items: list[OverseerItem]


class OverseerDecision(_Strict):
    revision: str = Field(min_length=1, description="revision of the queued result that was reviewed (GET /overseer)")
    note: str = ""
    by: str = "overseer-ui"


class AuditEntry(_Strict):
    timestamp: str
    tre_id: str
    project_id: str | None = None
    spec_hash: str
    analysis_type: str | None = None
    variables: list[str] = Field(default_factory=list)
    filters: dict[str, dict[str, object]] = Field(default_factory=dict)
    min_cell_size: int | None = None
    n: int | None = None
    released: dict[str, list[str]] = Field(default_factory=dict)
    rejected: list[str] = Field(default_factory=list)
    decision: str


class AuditLog(_Strict):
    items: list[AuditEntry]
