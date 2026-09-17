#!/usr/bin/env bash
# (Re)provision the whole federation from the generated flare/project.yml.
# Keeps flare/workspace/<project>/state (root CA) so previously issued kits stay valid;
# always writes a fresh prod_00 so compose paths are stable.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python}; [ -x .venv/bin/python ] && PY=.venv/bin/python
PROJECT=$($PY -c "import yaml; print(yaml.safe_load(open('flare/project.yml'))['name'])")
$PY scripts/gen_sites.py >/dev/null
rm -rf "flare/workspace/$PROJECT"/prod_*
$PY -m nvflare.lighter.provision -p flare/project.yml -w flare/workspace >/dev/null
echo "flare/workspace/$PROJECT/prod_00"
