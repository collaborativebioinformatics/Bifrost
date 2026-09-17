# Bifrost

Named after the guarded bridge between realms in Norse myth: a single crossing that connects otherwise sealed worlds, where nothing passes without the watchman's approval. Here it links isolated Nordic TREs so analyses can cross between them while the data itself never does.

Cross-TRE federated analysis: run an analysis across several Trusted Research Environments (TREs) without moving any record-level data. Each TRE computes locally behind its own native API and returns only disclosure-checked aggregates; the orchestrator combines them.

## Status

The local pipeline runs without FLARE: three mock TREs speaking different APIs, adapters that normalise them, a safe-output filter, and aggregation that reproduces pooled ground truth. The federated path does not run yet.

| Component | State |
| --- | --- |
| Mock TREs — `tres/rest`, `tres/datashield`, `tres/sql` | implemented, covered by tests |
| Adapters, registry, safe-output filter | implemented, covered by tests |
| Allele frequency and federated OLS | verified against `data/ground_truth.json` |
| Local runner without FLARE — `scripts/run_local.py` | implemented |
| FLARE orchestration | does not build |
| Server disclosure check, overseer queue | not started |
| Interface for non-technical users | not started |

`flare/server/Dockerfile` copies a `server/` directory and calls `scripts/start_server.sh`, and neither exists, so the orchestrator cannot start. Everything in the next section runs without it.

## Running it

Needs Python 3.11 or 3.12, run from the repository root.

**Install dependencies.** `pyproject.toml` declares a `server` package that is not in the tree, so `pip install -e .` fails. Until that entry is fixed, install them directly:

```
pip install "numpy>=1.26" "pandas>=2.2" "pyyaml>=6.0" "pydantic>=2.6" "httpx>=0.27" \
            "fastapi>=0.110" "uvicorn>=0.29" "duckdb>=1.0" "nvflare>=2.5,<2.10" "pytest>=8.0"
```

**Generate the synthetic data.** The per-site CSVs are gitignored, and `scripts/up.sh` only regenerates them when `data/ground_truth.json` is absent — which it never is, because that file is tracked. So run this explicitly on a fresh checkout, before either launch path below:

```
python data/generate.py
```

**Tests:**

```
python -m pytest -q
```

**Without Docker**, in two terminals. Each TRE runs as a local uvicorn process on ports 8001 upwards. In the first terminal:

```
python scripts/dev_tres.py
```

Wait for `TREs running; Ctrl-C to stop`, then send one analysis spec to every site from a second terminal:

```
python scripts/run_local.py spec/examples/allele_freq.json
```

**With Docker** — builds the TRE containers, checks each one's health endpoint from its own client container, and asserts that no TRE can reach the public internet:

```
scripts/up.sh
```

`spec/examples/` holds four specs: allele frequency, a filtered variant, one the safe-output filter is meant to reject, and federated linear regression. `sites.yaml` is the only place sites are listed — `scripts/gen_sites.py` regenerates `docker-compose.yml` and `flare/project.yml` from it.

## Team

- Ioannis Christofilogiannis
- Gaurang Sharma
- Marta Menta Czinkoczky
- Udogwu Emiri
- Pedro Gabriel Campana
- Vitalii Babenko
- Melissa Wong
- Espen Hagen

## Architecture

Diagram source: [flowchart.drawio](https://drive.google.com/file/d/1j9t8W-cFVBYgHGrFrLGP5keU2vaFtE-l/view?usp=sharing)

![Cross-TRE federated analysis flowchart](flowchart_drawio.svg)

### Flow

1. **Request** — researcher submits an analysis spec (JSON) using canonical variable names; the orchestrator resolves them to each site's local columns via the harmonisation map.
2. **Dispatch** — the orchestrator (FLARE server) sits outside every TRE. TREs dial out over gRPC/TLS, so no inbound ports are opened in the secure environments.
3. **Local execution** — each TRE runs a FLARE client with an adapter onto its native API (REST, DataSHIELD/R, SQL gateway). Compute happens where the data is; record-level data never leaves.
4. **Safe output** — each site filters results before they leave: aggregates only, small-count suppression (k ≥ 5).
5. **Aggregation** — the server combines site results into federated statistics; feature selection and logistic regression follow later.
6. **Disclosure check** — passing results plus an audit log are published; failures are rejected and the spec is refined.

## Goals

These are the project's targets, not a description of what runs today — see [Status](#status) for that.

### 0. Simulate TREs
- with various levels of security

### 1. Federated Infrastructure
1.1. Create client + server kits (certificates)

1.2. Distribute them

1.3. Connect them via IP addresses

### 2. A simple ML/AI training task
Use case: Calculate Allele Frequency & Linear Regression


- Use case 1: Calculate Allele Frequency
  federated analysis — computation happens inside each TRE; aggregate statistic leaves.
- Use case 2: Linear regression 
  federated learning — training happens inside each TRE; model information leaves.
- Use case X: iterative model training (DL or similar)

[View API Architecture Use Case](API_architecture_use_case.txt)

                 federation_client.py
                         │
               ┌─────────┴─────────┐
               │                   │
        federated analysis   federated learning
               │                   │
       GET allele freq         POST train
               │                   │
       ┌───────┼───────┐   ┌───────┼───────┐
       ↓       ↓       ↓   ↓       ↓       ↓
      TRE1    TRE2    TRE3 TRE1    TRE2    TRE3
       │       │       │   │       │       │
      DB      DB      DB   DB      DB      DB
       🔒      🔒      🔒   🔒      🔒      🔒
       │       │       │   │       │       │
     counts  counts  counts β₁    β₂      β₃
       └───────┼───────┘   └───────┼───────┘
               ↓                   ↓
          aggregate          aggregate model
          
ML jobs:
- Select dataset and distribute it
- Create a simulated NVFlare job
- Distribute the job to clients
- Run the job on the connected clients

### 3. Weights aggregation
- Collect model weights from each TRE after local training
- Aggregate the local weights on the FLARE server using FedAvg
- Generate a single global model from the aggregated weights
- Redistribute the global model to participating TREs
- Run a small number of federated training rounds and track the results
- Log the aggregation process without exposing local data

### 4. Interface that allows API usage for non-technical users

### 5. Deployment on real-world TREs
- HUNT Cloud clients (multiple users)
- Gefion clients (multiple users)
- Server for model aggregation (Brev or AWS)
- Admin - FLARE Dashboard/deployment kits (Brev or AWS)

### X. Nice interface
- User interface that allows API Usage for non-technical users

## License

Released under the [MIT License](LICENSE).
