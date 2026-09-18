# Heimdall Next.js UI

A Next.js interface for the Heimdall federated analysis API. It is intentionally isolated under `frontend/` from the Python TRE and FLARE code.

## Run

```powershell
cd frontend
npm install
npm run dev
```

The UI uses Webpack mode because the native Windows SWC binary may be unavailable. Set `NEXT_PUBLIC_API_BASE_URL` when the API is not running at `http://localhost:8500`:

```powershell
$env:NEXT_PUBLIC_API_BASE_URL = "http://localhost:8500"
```

The expected API surface is:

- `GET /metadata` — canonical variables, safe projects, examples, and TRE status.
- `GET /examples/{filename}` — load an example `AnalysisSpec`.
- `POST /run` — submit an `AnalysisSpec`; returns a run ID.
- `GET /run/{id}` — poll status and read the merged server result.
- `GET /overseer` — read flagged results.
- `POST /overseer/{spec_hash}/approve` and `/reject` — release or reject a queued result.
- `GET /audit` — read per-site audit records.

The result renderer follows the server output fields: `coverage`, `sites_missing`, `n_per_site`, `rejected_per_site`, and `stats` containing allele frequencies, genotype counts, summary statistics, OLS coefficients, and FedAvg history. The TRE list is supplied by the API; the frontend does not read site CSVs or import TRE applications.

(From the docs, I mean the global README)
The Python backend and local TREs are started from the repository root. For the simulated TRE path, run `python scripts/dev_tres.py`, then start the API on port 8500.
