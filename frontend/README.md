# Heimdall Next.js UI

A Next.js interface for the Heimdall federated analysis API. It is intentionally isolated under `frontend/` from the Python TRE and FLARE code.

## Run

```bash
cd frontend
npm install
npm run dev
```

The UI uses Webpack mode because the native Windows SWC binary may be unavailable. Browser requests use the same-origin `/api` proxy, so the browser does not need CORS access to the Python API. The proxy targets `http://127.0.0.1:8500` by default.

```bash
export BACKEND_API_URL="http://127.0.0.1:8500"
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



Start the Python API from the repository root on port 8500, then start this frontend:

```bash
SERVER_OUT=server/api_out uvicorn server.api:app --port 8500
cd frontend
npm run dev
```
