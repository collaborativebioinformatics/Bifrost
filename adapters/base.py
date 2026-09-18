"""Adapter interface. One adapter per TRE API style. The FLARE executor only
ever calls `adapter.run(spec)` and gets an `AggregateResult` back; nothing
else crosses the boundary.

Concrete adapters implement four *primitives* in their TRE's native dialect;
`run()` is shared and turns a canonical AnalysisSpec into primitive calls, then
pushes everything through the Safe Output filter. That is why a new analysis
type (M4) needs no adapter changes: it composes the same primitives.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

from adapters import safe_output
from harmonisation import local_name, var_type
from spec.analysis_spec import AnalysisSpec


class _Strict(BaseModel):
    """Every wire model: unknown keys are rejected, NaN/inf never serialise."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AlleleCounts(_Strict):
    minor: int = Field(ge=0)
    major: int = Field(ge=0)
    n_alleles: int = Field(ge=0)


class Gram(_Strict):
    """Cross-product matrix of [intercept, outcome, *features]: the OLS sufficient statistic."""

    n: int = Field(ge=0)
    cols: list[str] = Field(min_length=1)
    matrix: list[list[float]]

    @model_validator(mode="after")
    def _square_and_finite(self):
        k = len(self.cols)
        if len(self.matrix) != k or any(len(row) != k for row in self.matrix):
            raise ValueError(f"gram matrix must be {k}x{k}")
        if any(not math.isfinite(v) for row in self.matrix for v in row):
            raise ValueError("gram matrix must be finite")
        return self


class VariableStats(_Strict):
    """Everything a site may release about one canonical variable. Absent fields
    were either not requested or withheld by the Safe Output filter (see
    AggregateResult.rejected). The field list IS the aggregate allow-list."""

    count: int | None = Field(default=None, ge=0)
    sum: float | None = None
    sum_sq: float | None = None
    min: float | None = None
    max: float | None = None
    genotype_counts: dict[str, int] | None = None  # level -> n, small cells removed
    histogram: dict[str, int] | None = None
    allele_counts: AlleleCounts | None = None
    gram: Gram | None = None

    def present(self) -> dict:
        """The released fields as a plain dict (what the merge works on)."""
        return self.model_dump(exclude_none=True)

    def __contains__(self, key: str) -> bool:
        return getattr(self, key, None) is not None


class AggregateResult(_Strict):
    """The only thing that crosses from a TRE to the federation."""

    tre_id: str
    n: int = Field(ge=0)  # rows in the (filtered) cohort; 0 when nothing was released
    stats: dict[str, VariableStats] = Field(default_factory=dict)  # canonical variable -> released stats
    rejected: list[str] = Field(default_factory=list)  # "<var>.<field>[.<cell>]:<reason>" suppressed by Safe Output
    region: str = ""
    spec_hash: str = ""

    def to_dict(self) -> dict:
        return self.model_dump(exclude_none=True)

    @classmethod
    def from_dict(cls, d: dict) -> "AggregateResult":
        return cls.model_validate(d)


class TREAdapter(ABC):
    name: str = "base"  # registry key

    def __init__(self, tre_id: str, region: str = ""):
        self.tre_id = tre_id
        self.region = region

    # ---- primitives (native dialect) -------------------------------------------------
    @abstractmethod
    def schema(self) -> dict[str, str]:
        """canonical_var -> local dtype (only variables this site has)."""

    @abstractmethod
    def count(self, filters: dict) -> int:
        """Rows matching local filters."""

    @abstractmethod
    def describe(self, col: str, filters: dict) -> dict[str, float]:
        """{count, sum, sum_sq, min, max} of one local column."""

    @abstractmethod
    def value_counts(self, col: str, filters: dict) -> dict[str, int]:
        """{level: count} of one local column."""

    @abstractmethod
    def gram(self, cols: list[str], filters: dict) -> dict:
        """Cross-product matrix of [1, *cols]: {"n", "cols", "matrix"}; matrix[i][j] = sum(x_i * x_j)."""

    # ---- shared logic ----------------------------------------------------------------
    def local(self, canonical: str) -> str:
        return local_name(canonical, self.tre_id)

    def local_filters(self, spec: AnalysisSpec) -> dict:
        return {self.local(v): conds for v, conds in spec.filters.items()}

    def run(self, spec: AnalysisSpec) -> AggregateResult:
        if not safe_output.project_allowed(spec.project_id, self.tre_id):
            return safe_output.reject_all(self.tre_id, self.region, spec, reason=f"project_id:{spec.project_id}:not_allowed")

        filters = self.local_filters(spec)
        n = self.count(filters)
        raw: dict[str, dict[str, Any]] = {}
        if n < spec.min_cell_size:  # don't even compute on a cohort that can't be released
            return safe_output.filter(self.tre_id, self.region, spec, n, raw)

        if spec.analysis_type in ("allele_freq", "fed_stats"):
            for v in spec.variables:
                if var_type(v) == "genotype":
                    raw[v] = {"genotype_counts": self.value_counts(self.local(v), filters)}
                elif spec.analysis_type == "fed_stats":
                    raw[v] = self.describe(self.local(v), filters)
                else:
                    raw[v] = {}  # allele_freq ignores non-genotype variables
        elif spec.analysis_type == "fed_linreg":
            cols = [spec.outcome, *spec.variables]
            g = self.gram([self.local(c) for c in cols], filters)
            raw["_linreg"] = {"gram": {"n": g["n"], "cols": ["intercept", *cols], "matrix": g["matrix"]}}

        return safe_output.filter(self.tre_id, self.region, spec, n, raw)
