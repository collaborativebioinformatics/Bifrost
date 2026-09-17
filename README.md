# Bifrost — cross-TRE federated analysis

Run one analysis across several Trusted Research Environments (TREs) without moving any record-level data. Each TRE computes locally behind its **own native API** and returns only disclosure-checked aggregates; an NVIDIA FLARE server outside every TRE combines them. Built by Team 1 at the NCFH 2026 hackathon.

Two use cases, one pipeline:

1. **Allele frequency** — federated *analysis*: each TRE returns genotype counts, the server sums.
2. **Linear regression** — federated *learning*: each TRE returns model parameters (or the OLS sufficient statistics), the server averages / solves.

## Status

Milestones M0–M5 are implemented: three mock TREs behind different APIs, adapters that normalise them, FLARE jobs, server-side aggregation with disclosure control, and scale evidence up to 100 simulated sites.

| Component | State |
| --- | --- |
| Mock TREs — `tres/rest`, `tres/datashield`, `tres/sql` | implemented, covered by tests |
| Adapters, registry, safe-output filter, audit log | implemented, covered by tests |
| Analysis spec and Safe Projects allow-list | implemented, covered by tests |
| FLARE jobs — allele frequency, federated statistics, linear regression (exact and FedAvg) | implemented, simulator-tested |
| Server aggregation, disclosure check, overseer queue | implemented |
| Allele frequency and federated OLS | verified against `data/ground_truth.json` |
| Scale evidence — 3, 10, 50 and 100 simulated sites | `docs/scaling.png` |
| Run against real Docker containers or a live FLARE server | not yet performed |
| Interface for non-technical users | not started |

Everything verified so far runs either in-process or through the FLARE simulator. Nobody has yet run the stack against real Docker containers or a non-simulator FLARE server.

## Running it

Needs Python 3.11 or 3.12, run from the repository root.

**Install:**

```
pip install -e ".[dev]"
```

**Generate the synthetic data.** The per-site CSVs are gitignored, and `scripts/up.sh` only regenerates them when `data/ground_truth.json` is absent — which it never is, because that file is tracked. So run this explicitly on a fresh checkout, before any launch path below:

```
python data/generate.py
```

**Tests.** The fast suite needs no network. The FLARE simulator suite is marked `slow` and needs `nvflare` installed:

```
python -m pytest -q -m "not slow"    # 31 tests
python -m pytest -q -m slow          # 3 tests, ~50 s
```

**Start the TREs.** Each runs as a local uvicorn process on ports 8001 upwards. In its own terminal, since it stays in the foreground until Ctrl-C:

```
python scripts/dev_tres.py
```

Wait for `TREs running; Ctrl-C to stop`. Everything below runs from a second terminal.

**One analysis across all three, without FLARE:**

```
python scripts/run_local.py spec/examples/allele_freq.json
```

**The same analysis through FLARE, in the simulator.** Every run writes the merged result to `server/out/<spec_hash>/result.json`. A sibling `released.json` is written only when the disclosure check passes or an overseer approves, and that is what would leave the server. Run directories are keyed by spec hash, so a `released.json` from an earlier run of the same spec survives a later flagged one — check `check.json` for the current decision.

`verify.py` reads the merged result and compares whichever statistics are present against the pooled truth: allele frequencies even under partial coverage, with the truth recomputed over the reporting sites, and means and OLS coefficients only when every site reported. Statistics the filter suppressed are not checked, so a run that released nothing still reports `PASS`. It exits non-zero on any deviation beyond `--tol`:

```
scripts/run_job.sh spec/examples/allele_freq.json
python scripts/verify.py server/out/<spec_hash>/result.json
```

**With Docker** — builds the TRE containers, checks each one's health endpoint from its own client container, and asserts that no TRE can reach the public internet:

```
scripts/up.sh
```

`spec/examples/` holds four specs: allele frequency, a filtered variant, one the safe-output filter is meant to reject, and federated linear regression.

## Adding a TRE

`sites.yaml` is the only place sites are listed; `docker-compose.yml` and `flare/project.yml` are generated from it. One command registers a site, regenerates both, provisions the project CA and packs an mTLS client kit:

```
scripts/onboard_tre.sh <tre_id> [adapter] [api_url] [region]
```

For a TRE outside the local Docker network, set `server.host` in `sites.yaml` to an address that TRE can actually reach **before** running this. The default `flare-server` is a Docker DNS name, and the client kit bakes in whatever address is set when it is packed.

It writes `flare/kits/<tre_id>.tgz` and prints the one firewall rule the TRE needs — outbound TCP to the FLARE server, nothing inbound. The archive holds only the mTLS client kit, so the TRE host needs a checkout of this repository with its dependencies installed as well. There:

```
tar xzf <tre_id>.tgz
TRE_ID=<tre_id> TRE_API_URL=<api_url> CLIENT_KIT=$PWD/<tre_id> scripts/start_client.sh
```

Re-run `python data/generate.py` afterwards to re-split the synthetic cohort across the new site count.

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
