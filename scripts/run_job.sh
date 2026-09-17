#!/usr/bin/env bash
# scripts/run_job.sh <spec.json> [--mode simulator|prod] ...   (see scripts/run_job.py)
cd "$(dirname "$0")/.."
PY=${PY:-python}; [ -x .venv/bin/python ] && PY=.venv/bin/python
exec $PY scripts/run_job.py "$@"
