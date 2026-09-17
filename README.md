# Bifrost — cross-TRE federated analysis

Run one analysis across several Trusted Research Environments (TREs) without moving any record-level data. Each TRE computes locally behind its **own native API** and returns only disclosure-checked aggregates; an NVIDIA FLARE server outside every TRE combines them. Built by Team 1 at the NCFH 2026 hackathon.

Two use cases, one pipeline:

1. **Allele frequency** — federated *analysis*: each TRE returns genotype counts, the server sums.
2. **Linear regression** — federated *learning*: each TRE returns model parameters (or the OLS sufficient statistics), the server averages / solves.

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

```
researcher ──spec (canonical names)──▶ FLARE server (orchestrator, outside every TRE)
                                          │  broadcast spec        ▲ aggregates only
              ┌───────────────────────────┼────────────────────────┼──────────────┐
              ▼                           ▼                        ▼              │
     ┌── TRE hunt ─────────┐   ┌── TRE gefion ───────┐   ┌── TRE brev ──────────┐ │
     │ FLARE client        │   │ FLARE client        │   │ FLARE client         │ │
     │  └ rest adapter     │   │  └ datashield adapter│  │  └ sql adapter       │ │
     │     └ Safe Output ──┼───┤     └ Safe Output ──┼───┤     └ Safe Output ───┼─┘
     │        └ POST /query│   │        └ POST /ds    │  │        └ POST /sql   │
     │ data (read-only)    │   │ data (read-only)    │   │ DuckDB (read-only)   │
     │ audit.jsonl         │   │ audit.jsonl         │   │ audit.jsonl          │
     └─────────────────────┘   └─────────────────────┘   └──────────────────────┘
        outbound-only mTLS gRPC to the server; no inbound ports; internal-only Docker network
```

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

## Run it

```bash
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python scripts/gen_sites.py && .venv/bin/python data/generate.py     # compose + FLARE project + synthetic cohort
.venv/bin/python -m pytest -q                                                    # 34 tests (3 run FLARE simulator jobs)
```

**One command, real federation, no Docker** — provisioned FLARE server + one provisioned client per TRE as separate processes, mTLS gRPC:

```bash
scripts/local_federation.sh up
scripts/local_federation.sh job spec/examples/allele_freq.json
scripts/local_federation.sh job spec/examples/fed_linreg.json --fedavg --rounds 10
scripts/local_federation.sh down
```

**Docker** (each TRE on its own `internal: true` network; only its FLARE client also joins the `federation` network):

```bash
scripts/provision.sh && scripts/up.sh --flare      # builds, starts, health-checks, asserts TREs cannot reach the internet
docker compose exec flare-server python scripts/run_job.py --mode prod spec/examples/allele_freq.json
```

**Simulator** (fastest loop while developing): `.venv/bin/python scripts/dev_tres.py` in one terminal, `scripts/run_job.sh spec/examples/allele_freq.json` in another.

Every run ends with [`scripts/verify.py`](scripts/verify.py) against `data/ground_truth.json` (computed on the pooled data before it was split) and the overseer queue status.

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

```bash
scripts/onboard_tre.sh <tre_id> [adapter] [api_url] [region]
```

Appends the site to `sites.yaml`, regenerates `docker-compose.yml` and `flare/project.yml`, re-provisions, packs `flare/kits/<tre_id>.tgz` (client cert + startup kit) and prints the single outbound firewall rule the TRE must allow (`TCP egress → <server>:8002`). On the TRE host: untar, `TRE_ID=<id> TRE_API_URL=<its API> scripts/start_client.sh`. Measured: ~1.4 s for a fourth site, tests green with four.

A new API style = one adapter class implementing the four primitives + one entry in the registry. A variable the site names differently = one line under `local:` in `canonical.yaml` (unlisted sites use the canonical name).

For the final demo the same steps put one client on **Gefion** and one on **NextCloud** with the server on Brev/AWS: change `server.host` in `sites.yaml` to the server's FQDN, onboard each site, ship the kits.

## Repository layout

```
sites.yaml  projects.yaml  harmonisation/canonical.yaml      configuration (sites, projects, variables)
spec/          AnalysisSpec + examples
adapters/      base, registry, safe_output, rest, datashield, sql
tres/          mock TRE APIs + Dockerfiles
flare/         app/ (controllers, executors, linreg maths), client/ server/ Dockerfiles, project.yml (generated)
server/        aggregate, disclosure_check, overseer_queue      → server/out/ (generated)
scripts/       gen_sites, provision, onboard_tre, local_federation, up, run_job, verify, scale_sim, dev_tres
data/          generate.py → data/sites/*.csv (generated) + ground_truth.json
docs/          variables.md, scaling.png
tests/
```

Agent/contributor rules: [AGENTS.md](AGENTS.md). Build plan: [BUILD_PLAN.md](BUILD_PLAN.md).
