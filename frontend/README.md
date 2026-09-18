# Heimdall Next.js UI

A Next.js interface for the Heimdall federated analysis API. It is intentionally isolated under `frontend/` from the Python TRE and FLARE code.

## Run

```powershell
cd frontend
npm install
npm run dev
```

The UI uses Webpack mode because the native Windows SWC binary may be unavailable. Browser requests use the same-origin `/api` proxy, so the browser does not need CORS access to the Python API. The proxy targets `http://127.0.0.1:8080` by default. 

```powershell
$env:BACKEND_API_URL = "http://127.0.0.1:8080"
```

The current backend API surface is:

- `GET /health`
- `GET /allele-frequency?variant=...&project_id=...&min_cell_size=...`
- `POST /linear-regression` with `features`, `target`, `project_id`, `filters`, and `min_cell_size`.
- `POST /logistic-regression` with `features`, `target`, `project_id`, `filters`, and `min_cell_size`.
- `GET /metadata`
- `GET /examples/{name}`
- `POST /run`
- `GET /run/{run_id}`
- `GET /overseer`
- `POST /overseer/{spec_hash}/{decision}`
- `GET /audit`



Start the Python API from the repository root on port 8080, then start this frontend:

```powershell
python -m uvicorn server.api:app --host 127.0.0.1 --port 8080 --workers 1
cd frontend
npm run dev
```
