#!/usr/bin/env bash
# Start the provisioned FLARE client for this TRE and keep the process in the foreground.
# Env: CLIENT_KIT (default /client_kit), TRE_ID, TRE_API_URL, AUDIT_DIR.
set -euo pipefail
KIT=${CLIENT_KIT:-/client_kit}
WORK=${CLIENT_WORK:-/tmp/flare_client}
# kits are mounted read-only; FLARE writes logs/ and pids next to startup/, so work on a copy
rm -rf "$WORK" && mkdir -p "$WORK" && cp -r "$KIT"/. "$WORK"/
cd "$WORK"
echo "starting FLARE client ${TRE_ID:-?} -> $(grep -o '"[^"]*:[0-9]*"' startup/fed_client.json | head -1) (TRE API ${TRE_API_URL:-?})"
bash startup/sub_start.sh
