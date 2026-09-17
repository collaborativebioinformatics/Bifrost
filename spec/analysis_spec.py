"""The one object a researcher submits. Canonical variable names only; the
adapters translate to local columns. The spec hash keys the audit + release logs."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AnalysisSpec(BaseModel):
    analysis_type: Literal["allele_freq", "fed_stats", "fed_linreg"]
    variables: list[str]  # canonical names only
    filters: dict[str, dict[str, Any]] = Field(default_factory=dict)  # canonical -> {op: value}
    min_cell_size: int = 5
    project_id: str  # must be on the Safe Projects allow-list (projects.yaml)
    outcome: str | None = None  # fed_linreg only: canonical name of the outcome; rest of `variables` are features

    @model_validator(mode="after")
    def _check(self):
        if self.analysis_type == "fed_linreg" and not self.outcome:
            raise ValueError("fed_linreg needs `outcome`")
        if self.outcome and self.outcome in self.variables:
            raise ValueError("`outcome` must not also be listed in `variables`")
        if self.min_cell_size < 1:
            raise ValueError("min_cell_size must be >= 1")
        return self

    def spec_hash(self) -> str:
        payload = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
