#!/usr/bin/env bash
# Start the provisioned FLARE server (orchestrator) in the foreground.
set -euo pipefail
PROJECT=$(python -c "import yaml; print(yaml.safe_load(open('flare/project.yml'))['name'])" 2>/dev/null || echo federated_apis)
KIT=${SERVER_KIT:-/workspace/$PROJECT/prod_00/flare-server}
WORK=${SERVER_WORK:-/tmp/flare_server}
rm -rf "$WORK" && mkdir -p "$WORK" && cp -r "$KIT"/. "$WORK"/
cd "$WORK"
echo "starting FLARE server from $KIT"
bash startup/sub_start.sh
