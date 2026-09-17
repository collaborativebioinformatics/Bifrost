#!/usr/bin/env bash
# A REAL FLARE federation on one machine, no Docker: provisioned server + one
# provisioned client per site, separate processes, mTLS gRPC on localhost.
# Use it when Docker is unavailable, or as the demo fallback.
#
#   scripts/local_federation.sh up        provision (server host = localhost), start TREs, server, clients
#   scripts/local_federation.sh job <spec.json> [run_job.py args...]   submit via the admin API
#   scripts/local_federation.sh status
#   scripts/local_federation.sh down
#
# State lives in .local_fed/ (gitignored). TREs listen on 8101.., FLARE on 8002/8003.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
PY=.venv/bin/python; [ -x $PY ] && export PATH="$PWD/.venv/bin:$PATH" || PY=python   # kits call plain python3
FED=.local_fed
export SITES_PATH=$PWD/$FED/sites.yaml PYTHONPATH=$PWD SERVER_OUT=$PWD/server/out LOCAL_URLS=$PWD/$FED/local_urls.json
PROJECT=$($PY -c "import yaml; print(yaml.safe_load(open('sites.yaml')).get('project',{}).get('name','federated_apis'))")
KITS=$FED/workspace/$PROJECT/prod_00
SERVER_ORG=$($PY -c "import yaml; print(yaml.safe_load(open('sites.yaml'))['server']['org'])")
site_ids() { $PY -c "from scripts.sites import load_sites; print(' '.join(s['tre_id'] for s in load_sites()['sites']))"; }

case "${1:-}" in
  up)
    mkdir -p $FED
    sed -E 's/^(  host:) .*/\1 localhost/' sites.yaml > $FED/sites.yaml
    [ -f data/ground_truth.json ] || $PY data/generate.py
    $PY scripts/gen_sites.py > /dev/null
    rm -rf $FED/workspace/$PROJECT/prod_*
    $PY -m nvflare.lighter.provision -p flare/project.yml -w $FED/workspace --force > /dev/null
    git checkout -q docker-compose.yml flare/project.yml 2>/dev/null || true   # generated files stay as in sites.yaml
    nohup $PY scripts/dev_tres.py --port 8100 > $FED/tres.log 2>&1 &
    echo $! > $FED/tres.pid
    for i in $(seq 1 60); do grep -q "TREs running" $FED/tres.log 2>/dev/null && break; sleep 0.5; done
    (cd $KITS/localhost && bash startup/start.sh > /dev/null 2>&1); sleep 6
    for tid in $(site_ids); do
      (cd $KITS/$tid && AUDIT_DIR=$ROOT/audit/$tid bash startup/start.sh > /dev/null 2>&1)
    done
    sleep 8
    "$0" status
    ;;
  status)
    echo "TREs:"; tr -d '{}' < $FED/local_urls.json 2>/dev/null | sed 's/^/  /'
    for tid in $(site_ids); do
      if grep -q "Successfully registered client:$tid" $KITS/$tid/log.txt 2>/dev/null; then echo "  FLARE client $tid: registered"; else echo "  FLARE client $tid: NOT registered"; fi
    done
    ;;
  job)
    shift; SPEC=${1:?spec.json}; shift
    $PY scripts/run_job.py "$SPEC" --mode prod --admin-kit "$KITS/admin@$SERVER_ORG.org" "$@"
    ;;
  down)
    for d in localhost $(site_ids); do (cd $KITS/$d 2>/dev/null && bash startup/stop_fl.sh > /dev/null 2>&1) || true; done
    [ -f $FED/tres.pid ] && kill "$(cat $FED/tres.pid)" 2>/dev/null || true
    sleep 3
    for p in $(pgrep -f "nvflare.private.fed.app" || true); do kill "$p" 2>/dev/null || true; done
    echo "federation stopped"
    ;;
  *) sed -n 2,12p "$0"; exit 1;;
esac
