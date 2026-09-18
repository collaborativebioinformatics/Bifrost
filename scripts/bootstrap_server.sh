#!/usr/bin/env bash
# One-shot setup of the FLARE orchestrator on a fresh cloud VM (AWS, Brev, ...).
#
#   scripts/bootstrap_server.sh <public-dns-or-ip>
#
# Does: python 3.11 venv (via uv), deps, sets server.host in sites.yaml, provisions
# the project (root CA + all kits), starts the FLARE server, prints what to open in
# the firewall and where the client kits are. Idempotent: re-run to re-provision.
# Nothing here touches TRE data; the server only ever sees aggregates.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=${1:?usage: bootstrap_server.sh <public-dns-or-ip clients will dial>}

if [ ! -x .venv/bin/python ]; then
  command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
  uv venv --python 3.11 .venv
  uv pip install --python .venv/bin/python -e ".[dev]"
fi
export PATH="$PWD/.venv/bin:$PATH" PYTHONPATH=$PWD

# server.host is what every client dials and what the server certificate is issued for
sed -i.bak -E "s/^(  host:) .*/\1 $HOST/" sites.yaml && rm -f sites.yaml.bak
grep -A3 "^server:" sites.yaml
ls data/sites/*.csv >/dev/null 2>&1 || python data/generate.py >/dev/null

KITS=$(scripts/provision.sh)
PORT=$(python -c "import yaml; print(yaml.safe_load(open('sites.yaml'))['server']['fed_learn_port'])")
ADMIN=$(python -c "import yaml; print(yaml.safe_load(open('sites.yaml'))['server']['admin_port'])")

(cd "$KITS/$HOST" && bash startup/start.sh >/dev/null 2>&1)
sleep 6
if ss -ltn 2>/dev/null | grep -qE ":$PORT "; then echo "FLARE server listening on :$PORT (fed_learn) and :$ADMIN (admin)"; else echo "server not listening yet -- check $KITS/$HOST/log.txt"; fi

mkdir -p flare/kits
for d in "$KITS"/*/; do
  n=$(basename "$d"); [ "$n" = "$HOST" ] && continue
  tar -czf "flare/kits/$n.tgz" -C "$KITS" "$n"
done
cat <<MSG

== orchestrator up on $HOST ==
open inbound TCP $PORT (clients, mTLS gRPC) and $ADMIN (admin API) in the security group
client kits (copy each to its TRE):  $(ls flare/kits/*.tgz | tr '\n' ' ')
on a TRE host:  tar xzf <tre_id>.tgz && scripts/start_site.sh <tre_id> ./<tre_id>
submit a job:   python scripts/run_job.py --mode prod spec/examples/allele_freq.json
stop:           (cd $KITS/$HOST && bash startup/stop_fl.sh)
MSG
