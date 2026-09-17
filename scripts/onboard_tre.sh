#!/usr/bin/env bash
# Onboard one TRE: register it in sites.yaml (if new), regenerate compose + FLARE
# project, provision, pack its client kit, print the single outbound rule it needs.
#
#   scripts/onboard_tre.sh <tre_id> [adapter=rest] [api_url=http://tre-<id>:8000] [region=nordic]
#
# Output: flare/kits/<tre_id>.tgz -- copy it to the TRE (Gefion, NextCloud, ...),
# untar, set TRE_ID/TRE_API_URL, run scripts/start_client.sh (or the compose flare-<id> service).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python}; [ -x .venv/bin/python ] && PY=.venv/bin/python
TRE_ID=${1:?usage: onboard_tre.sh <tre_id> [adapter] [api_url] [region]}
ADAPTER=${2:-rest}; API_URL=${3:-http://tre-$TRE_ID:8000}; REGION=${4:-nordic}

$PY - "$TRE_ID" "$ADAPTER" "$API_URL" "$REGION" <<'PYEOF'
import sys, yaml
tid, adapter, url, region = sys.argv[1:]
cfg = yaml.safe_load(open("sites.yaml"))
if not any(s["tre_id"] == tid for s in cfg["sites"]):
    with open("sites.yaml", "a") as f:
        f.write(f"  - tre_id: {tid}\n    adapter: {adapter}\n    api_url: {url}\n    region: {region}\n    org: {tid}\n    weight: 1\n")
    print(f"sites.yaml: added {tid} ({adapter}, {region})")
else:
    print(f"sites.yaml: {tid} already registered")
PYEOF

KIT_ROOT=$(scripts/provision.sh)
SERVER=$($PY -c "import yaml; s=yaml.safe_load(open('sites.yaml'))['server']; print(f\"{s['host']}:{s['fed_learn_port']}\")")
mkdir -p flare/kits
tar -czf "flare/kits/$TRE_ID.tgz" -C "$KIT_ROOT" "$TRE_ID"
cat <<MSG

== $TRE_ID onboarded ==
client kit : flare/kits/$TRE_ID.tgz   (mTLS client cert, signed by the project root CA)
compose    : docker-compose.yml regenerated (services tre-$TRE_ID, flare-$TRE_ID)
data       : run 'python data/generate.py' to (re)split synthetic data for the new site count

Firewall rule the TRE must allow (outbound ONLY, nothing inbound):
    allow TCP egress  ->  $SERVER      (FLARE fed_learn, gRPC over mTLS)

Start on the TRE host:
    tar xzf $TRE_ID.tgz && TRE_ID=$TRE_ID TRE_API_URL=$API_URL CLIENT_KIT=\$PWD/$TRE_ID scripts/start_client.sh
MSG
