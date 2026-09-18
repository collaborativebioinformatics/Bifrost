#!/usr/bin/env bash
# Counterpart of kit_export.sh: read a pasted base64 kit from stdin, unpack it to
# .remote/<tre_id>/ and show where it will dial. Then: scripts/start_site.sh <tre_id> .remote/<tre_id>
#   scripts/kit_import.sh <tre_id>            (paste, Enter, Ctrl-D)
set -euo pipefail
cd "$(dirname "$0")/.."
TRE_ID=${1:?usage: kit_import.sh <tre_id>  < pasted kit}
mkdir -p .remote flare/kits
grep -v -- '-----' | tr -d ' \n\r' | base64 -d > "flare/kits/$TRE_ID.tgz"
rm -rf ".remote/$TRE_ID" && tar -xzf "flare/kits/$TRE_ID.tgz" -C .remote
echo "kit for $TRE_ID unpacked to .remote/$TRE_ID, dials $(grep -o '"target": "[^"]*"' ".remote/$TRE_ID/startup/fed_client.json")"
echo "start it with:  scripts/start_site.sh $TRE_ID .remote/$TRE_ID"
