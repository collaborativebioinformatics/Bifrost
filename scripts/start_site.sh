#!/usr/bin/env bash
# Run ONE site on a remote host (Gefion, NextCloud, ...): its mock TRE API and its
# provisioned FLARE client, both in the foreground of this shell (Ctrl-C stops both).
#
#   scripts/start_site.sh <tre_id> <client_kit_dir> [server_host:port]
#
# Prereqs on the host: this repo checked out, `.venv` created (uv/pip install -e ".[dev]"),
# the kit untarred (flare/kits/<tre_id>.tgz from scripts/onboard_tre.sh), outbound TCP
# to the FLARE server allowed. Nothing inbound is needed. Optional third argument
# overrides the server address baked into the kit (useful when the kit was packed
# with a placeholder host).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
TRE_ID=${1:?usage: start_site.sh <tre_id> <client_kit_dir> [server_host:port]}
KIT=$(cd "${2:?client kit dir}" && pwd)
OVERRIDE=${3:-}
PY=.venv/bin/python; [ -x $PY ] && export PATH="$ROOT/.venv/bin:$PATH" || PY=python
export PYTHONPATH=$ROOT

ADAPTER=$($PY -c "from scripts.sites import site; print(site('$TRE_ID')['adapter'])")
PORT=${TRE_PORT:-8000}
export TRE_ID TRE_API_URL="http://127.0.0.1:$PORT" AUDIT_DIR="$ROOT/audit/$TRE_ID"
export DATA_PATH="$ROOT/data/sites/$TRE_ID.csv"
[ -f "$DATA_PATH" ] || $PY data/generate.py

if [ -n "$OVERRIDE" ]; then  # patch the server address in a writable copy of the kit
  WORK=/tmp/flare_client_$TRE_ID; rm -rf "$WORK"; cp -r "$KIT" "$WORK"; KIT=$WORK
  $PY - "$KIT/startup/fed_client.json" "$OVERRIDE" <<'PYEOF'
import json, sys
p, target = sys.argv[1:]
cfg = json.load(open(p))
for s in cfg["servers"]:
    s["service"]["target"] = target
json.dump(cfg, open(p, "w"), indent=2)
print(f"kit now dials {target}")
PYEOF
fi

echo "== $TRE_ID: mock TRE ($ADAPTER) on $TRE_API_URL, data $DATA_PATH"
$PY -m uvicorn "tres.$ADAPTER.app:app" --host 127.0.0.1 --port "$PORT" --log-level warning &
TRE_PID=$!
trap 'kill $TRE_PID 2>/dev/null || true; (cd "$KIT" && bash startup/stop_fl.sh >/dev/null 2>&1) || true' EXIT
for i in $(seq 1 50); do curl -fsS "$TRE_API_URL/health" >/dev/null 2>&1 && break; sleep 0.2; done
curl -fsS "$TRE_API_URL/health"; echo

echo "== $TRE_ID: FLARE client from $KIT -> $(grep -o '"target": "[^"]*"' "$KIT/startup/fed_client.json")"
CLIENT_KIT=$KIT bash scripts/start_client.sh
