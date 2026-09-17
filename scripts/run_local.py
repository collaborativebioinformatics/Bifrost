#!/usr/bin/env python
"""Run one AnalysisSpec against every site in sites.yaml *without* FLARE
(adapters called directly over HTTP) and combine. Handy for adapter dev and for
checking a spec before submitting it as a FLARE job.

  python scripts/dev_tres.py &                        # start the TREs
  python scripts/run_local.py spec/examples/allele_freq.json
  python scripts/run_local.py - <<< '{"analysis_type":"allele_freq","variables":["snp_rs001"],"project_id":"ncfh-2026-demo"}'
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from adapters import registry  # noqa: E402
from scripts.sites import load_sites  # noqa: E402
from server.aggregate import combine  # noqa: E402
from spec.analysis_spec import AnalysisSpec  # noqa: E402


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else "-"
    spec = AnalysisSpec.model_validate_json(sys.stdin.read() if src == "-" else Path(src).read_text())
    results = []
    for s in load_sites()["sites"]:
        a = registry.load(s["tre_id"])
        r = a.run(spec)
        results.append(r)
        print(f"{r.tre_id:8s} n={r.n:6d} released={sorted(r.stats)} rejected={r.rejected}", file=sys.stderr)
    merged = combine(results, [s['tre_id'] for s in load_sites()['sites']])
    merged.pop('contributions')
    print(json.dumps(merged, indent=2))


if __name__ == "__main__":
    main()
