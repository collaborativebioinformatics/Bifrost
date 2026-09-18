#!/usr/bin/env bash
# Print a client kit as one base64 line, for pasting into a terminal on the TRE host
# when scp is not available (browser terminals, Teleport web shells). Kits are ~10 KB.
#   scripts/kit_export.sh <tre_id>            # on the server host, after bootstrap_server.sh / onboard_tre.sh
# On the TRE host:  scripts/kit_import.sh <tre_id>   then paste the line, Enter, Ctrl-D.
set -euo pipefail
cd "$(dirname "$0")/.."
TRE_ID=${1:?usage: kit_export.sh <tre_id>}
KIT=flare/kits/$TRE_ID.tgz
[ -f "$KIT" ] || { echo "no $KIT -- run scripts/bootstrap_server.sh or scripts/onboard_tre.sh first" >&2; exit 1; }
echo "== paste everything between the markers into: scripts/kit_import.sh $TRE_ID" >&2
echo "-----BEGIN HEIMDALL KIT $TRE_ID-----"
base64 < "$KIT" | tr -d '\n'; echo
echo "-----END HEIMDALL KIT $TRE_ID-----"
