#!/usr/bin/env bash
# Bring up all TREs (from sites.yaml) with Docker Compose and smoke-test them.
#   scripts/up.sh            build + start TREs + FLARE client containers, run checks
#   scripts/up.sh --flare    also start the FLARE server (needs a provisioned workspace, see onboard_tre.sh)
#   scripts/up.sh --down     stop everything
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python}
[ -x .venv/bin/python ] && PY=.venv/bin/python

if [ "${1:-}" = "--down" ]; then docker compose --profile flare down; exit 0; fi

$PY scripts/gen_sites.py
[ -f data/ground_truth.json ] || $PY data/generate.py

PROFILE=()
[ "${1:-}" = "--flare" ] && PROFILE=(--profile flare)
docker compose "${PROFILE[@]}" up --build -d --wait

echo; echo "== smoke test: a count from every TRE (via its own FLARE client container) =="
for tid in $($PY -c "from scripts.sites import load_sites; print(' '.join(s['tre_id'] for s in load_sites()['sites']))"); do
  url=$(docker compose exec -T "flare-$tid" printenv TRE_API_URL)
  printf "%-8s %s -> " "$tid" "$url"
  docker compose exec -T "flare-$tid" curl -fsS "$url/health"; echo
done

echo; echo "== isolation test: TREs must NOT reach the internet =="
for tid in $($PY -c "from scripts.sites import load_sites; print(' '.join(s['tre_id'] for s in load_sites()['sites']))"); do
  if docker compose exec -T "tre-$tid" curl -sS -m 5 https://example.com >/dev/null 2>&1; then
    echo "FAIL: tre-$tid reached example.com"; exit 1
  else
    echo "ok: tre-$tid cannot reach example.com"
  fi
done
echo "all sites up and isolated"
