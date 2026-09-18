#!/usr/bin/env python
"""Export the wire contracts as JSON Schema into docs/schemas/ (and the OpenAPI
document of the HTTP API). Run after changing any model; tests fail if stale.

  python scripts/export_schemas.py [--check]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from adapters.base import AggregateResult  # noqa: E402
from server import schemas  # noqa: E402
from spec.analysis_spec import AnalysisSpec  # noqa: E402

OUT = ROOT / "docs" / "schemas"
MODELS = {
    "analysis_spec": AnalysisSpec,          # researcher -> server
    "aggregate_result": AggregateResult,    # TRE -> server (the federated wire contract)
    "merged_result": schemas.MergedResult,  # server-internal record
    "released_result": schemas.ReleasedResult,  # server -> researcher / UI
    "analysis_response": schemas.AnalysisResponse,
    "run_status": schemas.RunStatus,
}


def render() -> dict[str, str]:
    files = {f"{name}.json": json.dumps(model.model_json_schema(), indent=2) + "\n" for name, model in MODELS.items()}
    from server.api import app  # imported lazily: only needs FastAPI, not TREs/FLARE

    files["openapi.json"] = json.dumps(app.openapi(), indent=2) + "\n"
    return files


def main() -> None:
    files = render()
    if "--check" in sys.argv:
        stale = [n for n, body in files.items() if not (OUT / n).exists() or (OUT / n).read_text() != body]
        if stale:
            sys.exit(f"stale schemas: {stale} -- run scripts/export_schemas.py")
        print("schemas up to date")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    for n, body in files.items():
        (OUT / n).write_text(body)
        print(f"wrote docs/schemas/{n}")


if __name__ == "__main__":
    main()
