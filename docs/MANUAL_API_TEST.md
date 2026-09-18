# Common API: local manual test

This guide tests the researcher-facing API against three local mock TREs, using synthetic data. It does **not** set up or validate a NVIDIA FLARE federation.

## 1. Prepare the Python environment

Use **Python 3.11 or 3.12**, as required by [pyproject.toml](../pyproject.toml). Run from the repository root:

```bash
cd /path/to/Bifrost
python3 --version
# If python3 is not 3.11 or 3.12, use an installed python3.12 below instead.
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install \
  'numpy>=1.26' \
  'pandas>=2.2' \
  'pyyaml>=6.0' \
  'pydantic>=2.6' \
  'httpx>=0.27' \
  'fastapi>=0.110' \
  'uvicorn>=0.29' \
  'duckdb>=1.0'
python -m pip install --no-deps -e .
python -c 'from server.api import app; print(app.title)'
```

If a working `.venv` already exists, reuse it; skip its creation. Activate it separately in each terminal. Keep `.venv` local and gitignored, rather than storing the environment in a temporary directory.

These dependencies were checked against the API, service, adapters, mock TREs and launcher imports: NumPy/Pandas handle calculations and CSVs; PyYAML loads configuration; Pydantic validates contracts; HTTPX calls TREs; FastAPI/Uvicorn serve HTTP; DuckDB powers the SQL mock. No additional runtime package was found necessary for this demo.

This is deliberately an **API-only environment**. `--no-deps` skips the project's declared NVFLARE dependency; it is not a complete project/development installation. Do not substitute `pip install -e '.[dev]'` for this procedure: that also installs the FLARE dependency tree, which previously failed building `cryptography` on macOS.

## 2. Check existing synthetic data

```bash
ls data/sites/hunt.csv data/sites/gefion.csv data/sites/brev.csv
```

At inspection, all three existed and supported the allele-frequency and age/BMI regression requests below. They are generated, gitignored files, so another checkout may lack them. **Do not regenerate existing data for the core demo.**

Only if the CSVs are missing:

```bash
python data/generate.py
```

**Warning:** this regenerates/overwrites the synthetic CSVs and may update the tracked `data/ground_truth.json`. It is not a read-only setup check. Restart the mock TREs after generation because they cache loaded data.

The current generator also creates the newer logistic-regression outcome. The CSVs inspected in this checkout lacked that column; see the optional endpoints section before demonstrating logistic regression.

## 3. Terminal 1: start all mock TREs

```bash
cd /path/to/Bifrost
source .venv/bin/activate
unset TRE_API_URL LOCAL_URLS SITES_PATH
python scripts/dev_tres.py --port 8000
```

The launcher reads [sites.yaml](../sites.yaml), uses `data/sites/<tre_id>.csv`, and starts:

| TRE | Mock implementation | URL |
| --- | --- | --- |
| HUNT | REST | `http://127.0.0.1:8001` |
| Gefion | DataSHIELD-like | `http://127.0.0.1:8002` |
| Brev | SQL/DuckDB | `http://127.0.0.1:8003` |

Expect three successful health reports followed by `3 TREs running; Ctrl-C to stop`. Check the individual reports: the final message alone does not guarantee that every service started.

The launcher writes `data/sites/local_urls.json`; adapters discover these local URLs automatically. Clearing `TRE_API_URL` is important because it otherwise overrides the URL for **every** adapter. Clearing `LOCAL_URLS` and `SITES_PATH` selects the standard local URL file and site configuration. No Docker or FLARE process is needed.

## 4. Terminal 2: start the Common API

```bash
cd /path/to/Bifrost
source .venv/bin/activate
unset TRE_API_URL LOCAL_URLS SITES_PATH PROJECTS_PATH
API_DEMO_OUT=$(mktemp -d /tmp/common-api-demo.XXXXXX)
printf 'Demo records: %s\n' "$API_DEMO_OUT"
SERVER_OUT="$API_DEMO_OUT/server" \
AUDIT_DIR="$API_DEMO_OUT/audit" \
AUDIT_ROOT="$API_DEMO_OUT/site-audit" \
python -m uvicorn server.api:app \
  --host 127.0.0.1 --port 8500 --workers 1
```

Expect Uvicorn listening on `http://127.0.0.1:8500`. The API can start and serve `/health` even when every TRE is unavailable.

`SERVER_OUT` isolates disclosure decisions, overseer records and release history. `AUDIT_DIR` isolates adapter audit records; `AUDIT_ROOT` keeps `/audit` from also reading older repository audit files. These temporary directories contain demo records, **not the Python environment**. A fresh directory prevents previous runs' disclosure history from affecting a new demonstration.

Keep `--workers 1`: release coordination uses a process-local lock and background run status is held in memory. No other environment variables need to be set for this procedure. Optional `API_TRE_TIMEOUT` and `API_TRE_WORKERS` default to 10 seconds per HTTP operation and 8 workers.

## 5. Swagger

Open **http://127.0.0.1:8500/docs**. Select an endpoint, click **Try it out**, enter the query parameters or JSON body below, then **Execute**.

Use `project_id=ncfh-2026-demo`, permitted for all sites in [projects.yaml](../projects.yaml). It identifies the approved analysis project; it is request metadata, not a required CSV column. Use canonical variable names such as `snp_rs001`, `age`, `bmi` and `sbp`; adapters map them to each site's local columns.

## 6. Terminal 3: core smoke test

```bash
cd /path/to/Bifrost
source .venv/bin/activate

curl -i 'http://127.0.0.1:8500/health'

curl -sS 'http://127.0.0.1:8500/metadata?probe=true' | python -m json.tool

curl -i 'http://127.0.0.1:8500/allele-frequency?variant=snp_rs001&project_id=ncfh-2026-demo&min_cell_size=5'

curl -i 'http://127.0.0.1:8500/linear-regression' \
  -H 'Content-Type: application/json' \
  --data '{"features":["age","bmi"],"target":"sbp","project_id":"ncfh-2026-demo","filters":{},"min_cell_size":5}'
```

Health should return HTTP 200:

```json
{"status":"ok"}
```

Metadata should show all three sites online. `probe=false` skips network checks; metadata describes configured variables and does not verify that every column exists in the CSVs.

With the existing synthetic data, all three TREs and fresh output storage, the two analysis requests should return HTTP 200 with `decision: "OK"`, successful coverage, and a `result`. Allele frequency is under `result.stats.snp_rs001`; the fitted regression is under `result.stats._linreg`. Internal `contributions` must not appear. A `FLAGGED` analysis response instead contains sanitized reason codes and **no result**.

The regression request fits an intercept plus `age` and `bmi` to predict `sbp`. It is a small, reliable demo; do not compare its coefficients directly to the seven-feature model's coefficients in `data/ground_truth.json`. Ground truth must use the same dataset, features, target and filters as the request. Ground truth is not an API input.

## Optional / newer endpoints

These routes also exist in [server/api.py](../server/api.py). Keep them separate from the core smoke test.

| Endpoint | Demo suitability and data requirements |
| --- | --- |
| `POST /logistic-regression` | Conditional: use features `age`, `bmi`, target `case`, the same project and empty filters. Defaults: `rounds=25`, `tol=1e-8`, `min_cell_size=5`. Requires local outcome columns `case_status` (HUNT), `is_case` (Gefion), `CASE_STATUS` (Brev). The inspected CSVs lack these; regenerate deliberately with the current generator and restart TREs first. Not part of the basic demo. |
| `GET /examples/{name}` | Suitable, read-only; e.g. `/examples/allele_freq.json` and `/examples/fed_linreg.json`. Returns a full `AnalysisSpec`, not the convenience endpoint's request schema. No data generation needed. |
| `POST /run` | Optional background submission of a full `AnalysisSpec`; returns HTTP 202 and a `run_id`. Use an allele-frequency or linear-regression example for existing data. Optional `fedavg_rounds` applies only to linear regression; omit it for the exact solve. Avoid `fed_stats` in this demo: the current service's final statistics validation assumes a regression fit for non-allele analyses. |
| `GET /run/{run_id}` | Suitable for polling a submitted run. Run status is in memory and disappears on API restart; no extra data needed. |
| `GET /overseer` | Optional internal governance view. May be empty after successful requests. Contains detailed internal disclosure reasons; use only with local synthetic demo data. |
| `POST /overseer/{spec_hash}/{decision}` | Administrative mutation, with `decision` equal to `approve` or `reject` and optional `note`/`by` body. Skip in the core demo. It changes stored decisions; already cached run status does not refresh automatically. |
| `GET /audit` | Optional read-only internal audit view after requests. No extra data needed; contains internal records, not the sanitized researcher response. |
| `GET /redoc`, `GET /openapi.json` | Suitable documentation/schema views; no TREs or data required. |

The governance endpoints currently have no authentication layer. Keep this procedure on localhost with synthetic data.

## What this validates: direct adapter communication


```text

         Researcher 
             |                ← Swagger / curl
         Common API
             |
      analysis_service
             |
        TRE adapters
        /    |    \
  +------+ +------+ +------+
  | HUNT | |Gefion| | Brev |   ← TRE boundaries
  | API  | | API  | | API  |
  |  ↓   | |  ↓   | |  ↓   |
  |local | |local | |local |
  | data | | data | | data |
  +--|---+ +--|---+ +--|---+
     |        |        |       ← aggregate responses
     +--------+--------+       
              |
      combine aggregates
              |
   server disclosure + records
              |
    releasable API response
              |
          Researcher
```

The actual allele-frequency/linear-regression path is `server/api.py` → `_execute()` → `_analyse()` → `analysis_service.analyse()` → `_run_site()` → `registry.load(tre_id).run(spec)`. Each adapter calls its native mock HTTP API. The service validates successful `AggregateResult`s, calls `server.aggregate.combine()`, then the existing disclosure check and overseer recording mechanism before formatting the external response. Failures are collected independently.

`analysis_service` imports numerical helpers from `flare/app/linreg.py` and `flare/app/logreg.py`; these helpers do not import NVFLARE. These routes do **not** submit FLARE jobs or use a FLARE server/client/controller. Even the optional in-process FedAvg mode is not a FLARE transport demonstration. The target secure deployment may instead use outbound TRE-side/FLARE communication; this guide validates only the direct-adapter prototype.

## Troubleshooting

| Symptom | Check/action |
| --- | --- |
| `.venv/bin/activate` missing | Confirm you are in the repository root; create `.venv` with Python 3.11/3.12 using section 1. A previous environment stored under `/tmp` may have been cleaned up. |
| Full editable install fails building `cryptography` | For this API demo, use the explicit dependencies and `--no-deps -e .` above. A full FLARE environment needs separate dependency/platform troubleshooting; this guide does not prescribe unverified compiler/system changes. |
| Port already in use | Check for another demo, Docker or FLARE process using 8001–8003 or 8500. Stop your previous process with Ctrl-C. If changing the TRE base port, rerun the launcher so it updates local URLs; if changing the API port, update Swagger/curl URLs too. |
| Synthetic CSV missing | Check the three paths in section 2. Generate only if needed, acknowledging the overwrite warning, then restart TREs. |
| TRE unavailable | Inspect Terminal 1 errors and `/metadata?probe=true`. Confirm overrides were cleared and `data/sites/local_urls.json` was written before analysis requests. `/health` on the Common API alone does not prove TRE availability. |
| HTTP 503 | For these convenience analysis routes, **zero valid responding TREs** yields a structured failure with `error: "no_tres_responded"`. Check site failure details and launcher logs. |
| Only one or two TREs respond | One valid site normally gives HTTP 200 with `FLAGGED`/`min_sites`, not 503. Two may be releasable if the unchanged minimum-site and other disclosure checks pass. Partial success does not guarantee disclosure approval. |
| Import/module errors | Run from the repository root with `.venv` activated. Use `python -m pip` and `python -m uvicorn` to select the same interpreter; repeat the API-only install if needed. |
| Old results after changing data | Stop Terminal 1 with Ctrl-C and restart all mocks after regeneration. Restart the API for a fresh demo/output directory if desired; in-memory `/run` records will be lost. |

## Verification scope

Commands, schemas, ports, configuration and imports were checked against the current source. Imports of the API, analysis service, all three adapters, all three mock apps, launcher and generator succeeded in the existing Python 3.12 environment with NVFLARE absent. The documented shell blocks were syntax-checked. A fresh package installation and live HTTP demo were not executed when writing this document; the expected responses above are checks for the person running it, not a claim of a completed end-to-end run.
