# Bifrost

![Bifrost Logo](docs/bifrost.svg)

 
Analyse data across research environments without moving individual records.

Calculate allele frequencies or fit linear regression across participating sites, with disclosure checks before results are released. Each Trusted Research Environment (TRE) computes behind its own API; Bifrost combines permitted aggregates with NVIDIA FLARE.

![Cross-TRE federated analysis flowchart](docs/architecture/flowchart_drawio.svg)

[Edit the diagram](docs/architecture/flowchart.drawio) · [Original external diagram](https://drive.google.com/file/d/1j9t8W-cFVBYgHGrFrLGP5keU2vaFtE-l/view?usp=sharing)

## What you can do

| Use case | What leaves each TRE | Result |
| --- | --- | --- |
| **Allele frequency** | Disclosure-checked genotype counts | Federated allele frequencies |
| **Linear regression** | OLS sufficient statistics or model parameters | Exact federated OLS or FedAvg regression |

Record-level data stays in the TRE. The server merges aggregates, applies a disclosure check, and records the release decision. Passing results are released automatically; flagged results wait for an overseer decision.

## Try it

Run from the repository root with Python 3.11 or 3.12, Bash, and standard Unix tools (macOS or Linux).

```sh
pip install -e ".[dev]"
python data/generate.py
```

Generate the synthetic data explicitly on a fresh checkout: per-site CSVs are gitignored, and `scripts/up.sh` regenerates them only when the tracked `data/ground_truth.json` is absent.

The recommended local path runs a provisioned FLARE server and one client per TRE as separate mTLS gRPC processes, submitting jobs through the admin API. It starts the TREs itself; nothing else should be running.

```sh
scripts/local_federation.sh up
scripts/local_federation.sh job spec/examples/allele_freq.json
scripts/local_federation.sh job spec/examples/fed_linreg.json --fedavg --rounds 10
scripts/local_federation.sh down
```

<details>
<summary>Other launch modes, tests, and verification</summary>

The fast suite needs no network. The FLARE simulator suite is marked `slow` and needs `nvflare` installed:

```sh
python -m pytest -q -m "not slow"    # 31 tests
python -m pytest -q -m slow          # 3 tests, ~50 s
```

The launch modes are alternatives, not steps. `scripts/dev_tres.py` takes ports 8001–8003; `sites.yaml` assigns the FLARE server `fed_learn_port: 8002` and `admin_port: 8003`. Stop `dev_tres.py` before starting the real federation or Docker: both start their own TREs.

For direct adapters and the simulator, start the TREs in their own terminal, wait for `TREs running; Ctrl-C to stop`, then run the analysis from a second terminal:

```sh
python scripts/dev_tres.py
```

**Direct adapters (development only).** This merges site results without the server disclosure check or overseer queue, so its output has not passed release control. One failing TRE aborts the run rather than yielding partial coverage.

```sh
python scripts/run_local.py spec/examples/allele_freq.json
```

**FLARE simulator.** Each run writes its merged result to `server/out/<spec_hash>/result.json`. `released.json` is written only when the disclosure check passes or an overseer approves, and is the output that would leave the server. Run directories are keyed by spec hash, so an earlier `released.json` can survive a later flagged run of the same spec; check `check.json` for the current decision.

`verify.py` compares statistics present in the merged result with pooled truth: allele frequencies even under partial coverage, with truth recomputed over reporting sites; means and OLS coefficients only when every site reports. It skips suppressed statistics, so a run that released nothing can still report `PASS`. It exits non-zero on a deviation beyond `--tol`.

```sh
scripts/run_job.sh spec/examples/allele_freq.json
python scripts/verify.py server/out/<spec_hash>/result.json
```

**Docker.** This builds the TRE and FLARE images, starts each TRE on its own `internal: true` network with only its FLARE client also on `federation`, checks each health endpoint from its client container, and asserts that no TRE can reach the public internet. Submit jobs inside the server container; `server/out/` is a host bind mount.

```sh
scripts/provision.sh && scripts/up.sh --flare
docker compose exec flare-server python scripts/run_job.py --mode prod --admin-kit "/workspace/federated_apis/prod_00/admin@ncfh.org" spec/examples/allele_freq.json
python scripts/verify.py server/out/41174610ab2bece0/result.json
scripts/up.sh --down
```

`spec/examples/` holds allele frequency, a filtered variant, a safe-output rejection case, and federated linear regression.
</details>

## Results

Measured on a real local federation (`local_federation.sh`) over a 30,000-row synthetic cohort split non-IID across three sites with different column names and APIs, 2026-09-17. Allele frequencies match pooled ground truth exactly; exact and FedAvg linear regression agree with pooled OLS to 1.7e-11 and 2.2e-11; a killed client yields `2/3 sites` with exact output for reporting sites; aggregation stays exact at 3, 10, 50, and 100 simulated sites.

FedAvg with `--local-steps 5` converges to the wrong point on this non-IID data (error ≈ 2.4e-2). The evidence is synthetic and local: nobody has run clients on remote Gefion or NextCloud hosts.

**Full figures, scenario detail, caveats, and scaling plot: [docs/results.md](docs/results.md).**

## Five Safes mapping

Local filters enforce per-site output rules before aggregates leave a TRE; the server checks the combined result before release. The detailed control mapping is available below.

<details>
<summary>Five Safes control mapping</summary>

| Safe | Mechanism here |
|---|---|
| **Safe projects** | `project_id` must be in [`projects.yaml`](projects.yaml) (per-site allow-list); otherwise nothing is computed and the refusal is audited. |
| **Safe people** | Provisioned FLARE identities: every client and the admin have mTLS certs signed by the project CA (`scripts/provision.sh`, `scripts/onboard_tre.sh`). |
| **Safe settings** | TRE containers are read-only, data mounted `:ro`, on an `internal: true` network; only the FLARE client dials **out** to the server. `scripts/up.sh` asserts a TRE cannot reach the internet. |
| **Safe data** | Canonical variables only; adapters never expose rows — each mock API refuses row-level requests on its own, and the `gram`/`describe`/`value_counts` primitives are the only things an adapter can ask for. |
| **Safe outputs** | Two layers. Site: [`adapters/safe_output.py`](adapters/safe_output.py) — aggregate allow-list, `n < k` ⇒ nothing leaves, cell `< k` suppressed, suppressed genotype cell ⇒ allele counts withheld, per-round audit. Server: [`server/disclosure_check.py`](server/disclosure_check.py) — k-anonymity on the merged table, dominance (one site > 90 % of a cell), minimum two sites, site-suppression, differencing against previous releases by spec signature. Flagged ⇒ [`server/overseer_queue.py`](server/overseer_queue.py) (`list / show / approve / reject`), human decision logged. |

</details>

## Adding a TRE

`sites.yaml` is the only place sites are listed; `docker-compose.yml` and `flare/project.yml` are generated from it. One command registers a site, regenerates both, provisions the project CA, and packs an mTLS client kit:

```sh
scripts/onboard_tre.sh <tre_id> [adapter] [api_url] [region]
```

<details>
<summary>Remote deployment and adapter details</summary>

For a TRE outside the local Docker network, set `server.host` in `sites.yaml` to an address that TRE can reach **before** running this. The default `flare-server` is Docker DNS, and the client kit contains the address set when packed.

It writes `flare/kits/<tre_id>.tgz` and prints the one required firewall rule: outbound TCP to the FLARE server, nothing inbound. The archive contains only the mTLS client kit, so the TRE host also needs a repository checkout with dependencies installed. There:

```sh
tar xzf <tre_id>.tgz
TRE_ID=<tre_id> TRE_API_URL=<api_url> CLIENT_KIT=$PWD/<tre_id> scripts/start_client.sh
```

Re-run `python data/generate.py` to re-split the synthetic cohort for the new site count. A new API style needs one adapter class implementing four primitives and a registry entry; a different local variable name needs one `local:` mapping in `harmonisation/canonical.yaml` (unlisted sites use the canonical name).

The intended final-demo topology is one client on Gefion, one on NextCloud, and the server on Brev or AWS: set `server.host` to the server FQDN, onboard each site, and ship the kits. This has not been run on remote hosts.

</details>

## Technical reference

See the [documentation index](docs/README.md) for reference material, planning history, architecture proposals, and scaling evidence.

<details>
<summary>Architecture components</summary>

1. **Request** — researcher submits a JSON analysis spec using canonical variable names; the orchestrator resolves them to local columns through the harmonisation map.
2. **Dispatch** — the FLARE server sits outside every TRE. TREs dial out over gRPC/TLS, so secure environments expose no inbound ports.
3. **Local execution** — each TRE runs a FLARE client with an adapter to its native REST, DataSHIELD/R, or SQL gateway. Compute happens where data is; record-level data never leaves.
4. **Safe output** — each site filters results before they leave: aggregates only, small-count suppression (`k = min_cell_size`, default 5).
5. **Aggregation** — the server combines site results into federated statistics; feature selection and logistic regression follow later.
6. **Disclosure check** — passing results are written to `released.json`; the release decision is recorded in the server release log. Flagged results enter the overseer queue for a human decision.

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
</details>

## About the name

Named after the guarded bridge between realms in Norse myth: a single crossing connecting otherwise sealed worlds, where nothing passes without the watchman's approval. It links isolated Nordic TREs so analyses can cross while data does not.

## Roadmap

The original goals are targets and historical intent, not a description of current behaviour. See [docs/roadmap.md](docs/roadmap.md).

## License

Released under the [MIT License](LICENSE).

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
| Interface for non-technical users | frontend under review in [#16](https://github.com/collaborativebioinformatics/Bifrost/pull/16); common API tracked in [#15](https://github.com/collaborativebioinformatics/Bifrost/issues/15). Neither is in this checkout. |

## Team

Built by Team 1 for the NCFH 2026 hackathon.

- Ioannis Christofilogiannis
- Gaurang Sharma
- Marta Menta Czinkoczky
- Udogwu Emiri
- Pedro Gabriel Campana
- Vitalii Babenko
- Melissa Wong
- Espen Hagen
