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
from dataclasses import asdict, dataclass, field
from typing import Any

from adapters import safe_output
from harmonisation import local_name, var_type
from spec.analysis_spec import AnalysisSpec


@dataclass
class AggregateResult:
    tre_id: str
    n: int
    stats: dict[str, dict[str, Any]]  # canonical_var -> {count, sum, sum_sq, min, max, genotype_counts?, allele_counts?}
    rejected: list[str] = field(default_factory=list)  # vars/cells suppressed by the Safe Output filter
    region: str = ""
    spec_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AggregateResult":
        return cls(**d)


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
