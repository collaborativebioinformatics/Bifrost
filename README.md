# Bifrost

<!-- FLAG: this naming paragraph is flavour rather than reference content. It explains the project name, so it is not redundant — recommend: keep if the README is also a public front page, cut if it is purely an operator entry point. Author's call. -->
Named after the guarded bridge between realms in Norse myth: a single crossing that connects otherwise sealed worlds, where nothing passes without the watchman's approval. Here it links isolated Nordic TREs so analyses can cross between them while the data itself never does.

Cross-TRE federated analysis: run an analysis across several Trusted Research Environments (TREs) without moving any record-level data. Each TRE computes locally behind its own native API and returns only disclosure-checked aggregates; the orchestrator (an NVIDIA FLARE server outside every TRE) combines them. Built by Team 1 at the NCFH 2026 hackathon.

See the [documentation index](docs/README.md) for reference material, planning history, architecture proposals, and scaling evidence.

Two use cases, one pipeline:

1. **Allele frequency** — federated *analysis*: each TRE returns genotype counts, the server sums.
2. **Linear regression** — federated *learning*: each TRE returns model parameters (or the OLS sufficient statistics), the server averages / solves.

## Status

<!-- FLAG: this sentence summarises the table directly below it. Not provably redundant — it names the M0–M6 milestone span, which the table does not — recommend: keep, or cut the milestone list and retain only "Milestones M0–M6 are implemented." Author's call. -->
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
| Interface for non-technical users | frontend under review in [#16](https://github.com/collaborativebioinformatics/Bifrost/pull/16); common API tracked in [#15](https://github.com/collaborativebioinformatics/Bifrost/issues/15). Neither is in this checkout. |

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

Wait for `TREs running; Ctrl-C to stop`. The two paths immediately below run from a second terminal while it keeps running.

The launch paths are alternatives, not steps: `dev_tres.py` takes ports 8001–8003, and `sites.yaml` gives the FLARE server `fed_learn_port: 8002` and `admin_port: 8003`, so **stop `dev_tres.py` before starting the real federation or Docker** — both start their own TREs.

**One analysis across all three, without FLARE.** Development only: it merges site results but does not run the server disclosure check or the overseer queue, so its output has not passed release control. One failing TRE aborts the whole run rather than yielding partial coverage:

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

Measured on a real federation (`local_federation.sh`) over a 30 000-row synthetic cohort split non-IID across three sites with different column names and different APIs, 2026-09-17. Allele frequencies match pooled ground truth exactly; exact and FedAvg linear regression agree with pooled OLS to 1.7e-11 and 2.2e-11; a killed client yields `2/3 sites` and still-exact output; aggregation stays exact at 3, 10, 50 and 100 simulated sites.

**Full figures, per-scenario detail and the scaling plot: [docs/results.md](docs/results.md).**

<!-- FLAG: placement. "Five Safes mapping" and "Adding a TRE" were not named in the entry-point keep-list, and both are detailed reference material that could move to docs/. Kept here because Five Safes is the governance argument a reader needs early, and Adding a TRE is operational setup — recommend: confirm, then move both to docs/ if the README should be shorter still. -->
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

![Cross-TRE federated analysis flowchart](docs/architecture/flowchart_drawio.svg)

### Flow

1. **Request** — researcher submits an analysis spec (JSON) using canonical variable names; the orchestrator resolves them to each site's local columns via the harmonisation map.
2. **Dispatch** — the orchestrator (FLARE server) sits outside every TRE. TREs dial out over gRPC/TLS, so no inbound ports are opened in the secure environments.
3. **Local execution** — each TRE runs a FLARE client with an adapter onto its native API (REST, DataSHIELD/R, SQL gateway). Compute happens where the data is; record-level data never leaves.
4. **Safe output** — each site filters results before they leave: aggregates only, small-count suppression (k ≥ 5).
5. **Aggregation** — the server combines site results into federated statistics; feature selection and logistic regression follow later.
6. **Disclosure check** — passing results plus an audit log are released; flagged results go to the overseer queue, where a human approves or rejects them, and every decision is recorded in the release log.

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

## Roadmap

The original goals — targets and historical intent, not a description of what runs today; see [Status](#status) for that. Kept in full, with their caveats, in [docs/roadmap.md](docs/roadmap.md).

## License

Released under the [MIT License](LICENSE).
