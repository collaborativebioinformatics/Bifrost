# Bifrost

Named after the guarded bridge between realms in Norse myth: a single crossing that connects otherwise sealed worlds, where nothing passes without the watchman's approval. Here it links isolated Nordic TREs so analyses can cross between them while the data itself never does.

Cross-TRE federated analysis: run an analysis across several Trusted Research Environments (TREs) without moving any record-level data. Each TRE computes locally behind its own native API and returns only disclosure-checked aggregates; the orchestrator (an NVIDIA FLARE server outside every TRE) combines them. Built by Team 1 at the NCFH 2026 hackathon.

Two use cases, one pipeline:

1. **Allele frequency** — federated *analysis*: each TRE returns genotype counts, the server sums.
2. **Linear regression** — federated *learning*: each TRE returns model parameters (or the OLS sufficient statistics), the server averages / solves.

## Status

Milestones M0–M6 are implemented: three mock TREs behind different APIs, adapters that normalise them, FLARE jobs, server-side aggregation with disclosure control, and scale evidence up to 100 simulated sites.

| Component | State |
| --- | --- |
| Mock TREs — `tres/rest`, `tres/datashield`, `tres/sql` | implemented, covered by tests |
| Adapters, registry, safe-output filter, audit log | implemented, covered by tests |
| Analysis spec and Safe Projects allow-list | implemented, covered by tests |
| FLARE jobs — allele frequency, federated statistics, linear regression (exact and FedAvg) | implemented, simulator-tested |
| Server aggregation, disclosure check, overseer queue | implemented |
| Allele frequency and federated OLS | verified against `data/ground_truth.json` |
| Scale evidence — 3, 10, 50 and 100 simulated sites | `docs/scaling.png` |
| Live (non-simulator) FLARE federation — provisioned server + clients, separate processes, mTLS | verified on one machine via `scripts/local_federation.sh` (see [Results](#results)) |
| Docker: 7 images, isolated TRE networks, containerised FLARE server + clients | verified (`scripts/up.sh --flare`, jobs from inside `flare-server`, stopped container ⇒ `2/3 sites`) |
| Interface for non-technical users | not started |

Everything verified so far runs on one machine: in-process, through the FLARE simulator, or as a real provisioned FLARE federation of separate processes over mTLS (`scripts/local_federation.sh`). Nobody has yet run clients on remote hosts (Gefion / NextCloud).

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

**Real federation, no Docker** — provisioned FLARE server plus one provisioned client per TRE as separate processes, mTLS gRPC, jobs submitted through the admin API. Starts the TREs itself; needs nothing running beforehand:

```
scripts/local_federation.sh up
scripts/local_federation.sh job spec/examples/allele_freq.json
scripts/local_federation.sh job spec/examples/fed_linreg.json --fedavg --rounds 10
scripts/local_federation.sh down
```

**With Docker** — builds the TRE and FLARE images, starts everything (each TRE on its own `internal: true` network; only its FLARE client also joins `federation`), checks each TRE's health endpoint from its own client container, and asserts that no TRE can reach the public internet. Jobs are submitted from inside the server container; verify on the host (`server/out/` is a bind mount):

```
scripts/provision.sh && scripts/up.sh --flare
docker compose exec flare-server python scripts/run_job.py --mode prod --admin-kit "/workspace/federated_apis/prod_00/admin@ncfh.org" spec/examples/allele_freq.json
python scripts/verify.py server/out/41174610ab2bece0/result.json
scripts/up.sh --down
```

`spec/examples/` holds four specs: allele frequency, a filtered variant, one the safe-output filter is meant to reject, and federated linear regression.

## Results

Synthetic cohort: 30 000 rows, 20 SNPs, age/sex/BMI/LDL, outcome SBP; split non-IID over three sites with **different column names and different APIs** (hunt 14 892 · gefion 8 953 · brev 6 155; per-site allele-frequency drift and age skew). All numbers below are from the real federation (`local_federation.sh`), 2026-09-17.

### Allele frequency — `spec/examples/allele_freq.json`

| SNP | federated (3/3 sites, n = 30 000) | ground truth | abs error |
|---|---|---|---|
| snp_rs001 | 0.074617 | 0.074617 | 0 |
| snp_rs002 | 0.237933 | 0.237933 | 0 |
| snp_rs003 | 0.230083 | 0.230083 | 0 |
| snp_rs004 | 0.154550 | 0.154550 | 0 |
| snp_rs005 | 0.131500 | 0.131500 | 0 |

Round time 11.9 s (job submit → merged result), of which the federated round itself is ~1.4 s; the rest is FLARE job deployment.

### Linear regression — `spec/examples/fed_linreg.json`, `sbp ~ age + sex + bmi + ldl + rs001 + rs007 + rs013`

| coefficient | exact (1 round, Gram matrices leave) | FedAvg (10 rounds, only β leaves) | pooled OLS |
|---|---|---|---|
| intercept | 89.2729 | 89.2729 | 89.2729 |
| age | 0.5055 | 0.5055 | 0.5055 |
| sex | 4.2034 | 4.2034 | 4.2034 |
| bmi | 0.8062 | 0.8062 | 0.8062 |
| ldl | 1.2189 | 1.2189 | 1.2189 |
| snp_rs001 | 2.3934 | 2.3934 | 2.3934 |
| snp_rs007 | −1.2817 | −1.2817 | −1.2817 |
| snp_rs013 | 0.9003 | 0.9003 | 0.9003 |
| max abs error | 1.7e-11 | 2.2e-11 | — |

FedAvg convergence (max |β − truth|): round 1 3.5e-1 → round 3 1.9e-3 → round 5 7.3e-6 → round 10 2.2e-11. With `--local-steps 5` the run converges to the *wrong* point (err ≈ 2.4e-2) — the classic non-IID FedAvg bias, kept as a test ([`tests/test_m4_linreg.py`](tests/test_m4_linreg.py)).

Both modes use the same adapters: each site's adapter returns the Gram matrix `[1, y, X]ᵀ[1, y, X]` (an allow-listed aggregate). In FedAvg mode the FLARE client keeps it inside the TRE and only sends p+1 parameters per round; local training is full-batch gradient steps on the site's own sufficient statistics (same update as `SGDRegressor.partial_fit` on that site's rows, without the rows).

### Suppression, overseer, straggler

| Scenario | What happened |
|---|---|
| `allele_freq_rejected.json` (`min_cell_size: 100`) | Every site withheld the hom-minor cell (`count=51/89/27<100`) **and** the derived allele counts (`derived_from_suppressed_cell`); audit `decision: PARTIAL` at all three sites. Server flagged `site_suppression:*` → overseer queue → approved by `lead` with a note → `released.json` written, release log records `QUEUED` then `RELEASED`. |
| `allele_freq_filtered.json` (`age ≥ 60`, `sex = 1`) | 3/3 sites, n = 4 690; DataSHIELD's own `nfilter.tab` and our k-rule both applied; nothing flagged. |
| brev's FLARE client killed before submission | Result marked **`2/3 sites`, `missing: ['brev']`**, n = 23 845, still exact for the reporting sites (verify recomputes the truth over them). Job completed in 11.8 s — nobody waited for the dead site. |
| Unknown `project_id` | Rejected by every adapter before any query; audited as `REJECTED`. |

Audit trail from the run above: hunt 16 lines, gefion 16, brev 15 (`OK` / `PARTIAL`), one line per spec per site plus one per FedAvg round. Server: `server/out/release_log.jsonl` (7 entries) and `server/out/<spec_hash>/{spec,result,check,released}.json`.

### Scaling — `scripts/scale_sim.py`

![round time vs number of TREs](docs/scaling.png)

| TREs | federated round | whole simulator run | exact vs pooled truth |
|---|---|---|---|
| 3 | 4.1 s | 10.6 s | yes |
| 10 | 7.7 s | 16.6 s | yes |
| 50 | 21.4 s | 30.7 s | yes |
| 100 | 38.3 s | 47.7 s | yes |

Adapters cycled rest/datashield/sql across four `region`s, 1 000 rows per site, 8 client threads in the FLARE simulator (the slope is thread scheduling; merging 100 results took < 1 ms). Everything the server combines is a sum, so a regional relay aggregator (FLARE hierarchical topology, grouped on `region`) needs no change to adapters or analyses.


## Five Safes mapping

| Safe | Mechanism here |
|---|---|
| **Safe projects** | `project_id` must be in [`projects.yaml`](projects.yaml) (per-site allow-list); otherwise nothing is computed and the refusal is audited. |
| **Safe people** | Provisioned FLARE identities: every client and the admin have mTLS certs signed by the project CA (`scripts/provision.sh`, `scripts/onboard_tre.sh`). |
| **Safe settings** | TRE containers are read-only, data mounted `:ro`, on an `internal: true` network; only the FLARE client dials **out** to the server. `scripts/up.sh` asserts a TRE cannot reach the internet. |
| **Safe data** | Canonical variables only; adapters never expose rows — each mock API refuses row-level requests on its own, and the `gram`/`describe`/`value_counts` primitives are the only things an adapter can ask for. |
| **Safe outputs** | Two layers. Site: [`adapters/safe_output.py`](adapters/safe_output.py) — aggregate allow-list, `n < k` ⇒ nothing leaves, cell `< k` suppressed, suppressed genotype cell ⇒ allele counts withheld, per-round audit. Server: [`server/disclosure_check.py`](server/disclosure_check.py) — k-anonymity on the merged table, dominance (one site > 90 % of a cell), minimum two sites, site-suppression, differencing against previous releases by spec signature. Flagged ⇒ [`server/overseer_queue.py`](server/overseer_queue.py) (`list / show / approve / reject`), human decision logged. |


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

A new API style = one adapter class implementing the four primitives + one entry in the registry. A variable the site names differently = one line under `local:` in `canonical.yaml` (unlisted sites use the canonical name).

For the final demo the same steps put one client on Gefion and one on NextCloud with the server on Brev/AWS: set `server.host` to the server's FQDN, onboard each site, ship the kits.

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

| Component | Where | What it does |
|---|---|---|
| Site list | [`sites.yaml`](sites.yaml) | **The only place sites are listed.** `tre_id`, `adapter`, `api_url`, `region`. Compose file and FLARE project are generated from it. |
| Analysis spec | [`spec/analysis_spec.py`](spec/analysis_spec.py), [`spec/examples/`](spec/examples) | `analysis_type`, canonical `variables`, `filters`, `min_cell_size`, `project_id`, `outcome`. Hashed to key every log. |
| Harmonisation | [`harmonisation/canonical.yaml`](harmonisation/canonical.yaml), [`docs/variables.md`](docs/variables.md) | canonical variable → local column per TRE (`age` = `alder` / `age_years` / `AGE`). |
| Mock TREs | [`tres/rest`](tres/rest), [`tres/datashield`](tres/datashield), [`tres/sql`](tres/sql) | Three deliberately different APIs over the same kind of data. Each rejects row-level access on its own. |
| Adapters | [`adapters/`](adapters) | One class per API style, four primitives (`count`, `describe`, `value_counts`, `gram`); `run(spec)` is shared. Discovered by name via [`adapters/registry.py`](adapters/registry.py). |
| Safe Output | [`adapters/safe_output.py`](adapters/safe_output.py) | Runs **inside** the TRE before the FLARE client sees anything: project allow-list, aggregate allow-list, k-suppression, audit log. |
| FLARE jobs | [`flare/app/`](flare/app) | Executor loads the adapter for its own `tre_id`; controllers broadcast, gather with `min_clients` + `wait_time`, merge. |
| Server side | [`server/`](server) | Associative merge, N-aware disclosure check, overseer queue + release log. |
| Ops | [`scripts/`](scripts) | generate, provision, onboard a TRE, start server/client, run a job, verify, scale simulation. |

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
