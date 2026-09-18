# AGENTS.md

Instructions for AI coding agents working in this repository. Humans should start with [README.md](README.md).

## Project

Bifrost — run one analysis across several Trusted Research Environments (TREs) without any record-level data leaving a TRE. Each TRE computes locally behind its own native API and returns disclosure-checked aggregates; a FLARE server combines them. Built by Team 1 for the NCFH 2026 hackathon.

Python 3.11–3.12, NVIDIA FLARE, FastAPI, DuckDB, Docker Compose. Dependency version constraints are declared in [pyproject.toml](pyproject.toml); ask before adding one.


A real (non-simulator) FLARE federation runs two verified ways: `scripts/local_federation.sh` with separate processes over mTLS, and Docker via `scripts/provision.sh && scripts/up.sh --flare`; both support the example specs, overseer flow, and partial-site coverage. The Next.js UI is implemented in `frontend/`; the Python API service exposing its documented contract remains separate. Not yet run: clients on remote hosts (Gefion / NextCloud).
Implemented:


| Area | Where |
| --- | --- |
| Site definitions | `sites.yaml` — three TREs (`hunt`/rest, `gefion`/datashield, `brev`/sql); `scripts/sites.py` is the only loader (`SITES_PATH` env overrides it for simulations) |
| Generators | `scripts/gen_sites.py` emits `docker-compose.yml` and `flare/project.yml` from `sites.yaml` |
| Synthetic data | `data/generate.py` → `data/sites/<tre_id>.csv` (gitignored) + `data/ground_truth.json` |
| Mock TREs | `tres/rest`, `tres/datashield`, `tres/sql` (`create_app(tre_id, data_path)` factories + Dockerfiles); `scripts/dev_tres.py` runs them without Docker |
| Adapters | `adapters/` behind `adapters/registry.py`; `adapters/safe_output.py` filters + writes `audit/<tre_id>/audit.jsonl` |
| Analysis spec | `spec/analysis_spec.py`, examples in `spec/examples/`; Safe Projects allow-list in `projects.yaml` |
| Harmonisation | `harmonisation/canonical.yaml` (unlisted site ⇒ local name = canonical name) |
| FLARE jobs | `flare/app/` — `controller.py`/`executor.py` (allele_freq, fed_stats, exact fed_linreg), `linreg_controller.py`/`linreg_executor.py`/`linreg.py` (FedAvg fed_linreg), `logreg_controller.py`/`logreg_executor.py`/`logreg.py` (Newton-Raphson fed_logreg). `scripts/build_job.py` assembles a job folder; `flare/jobs/` is generated and gitignored |
| Server side | `server/aggregate.py` (associative merge), `server/disclosure_check.py`, `server/overseer_queue.py` (CLI); `server/api.py` + `server/analysis_service.py` (synchronous HTTP API: `/allele-frequency`, `/linear-regression`, `/logistic-regression`); outputs under `server/out/` (gitignored, `SERVER_OUT` env) |
| Researcher UI | `frontend/` — Next.js researcher, overseer, and audit workspace;  the API surface documented in `frontend/README.md` |
| Ops scripts | `scripts/local_federation.sh` (real FLARE, no Docker), `scripts/provision.sh`, `scripts/onboard_tre.sh`, `scripts/start_server.sh`, `scripts/start_client.sh`, `scripts/run_job.py` (`--mode simulator|prod`, `--fedavg`), `scripts/run_local.py`, `scripts/verify.py`, `scripts/scale_sim.py` → `docs/scaling.png` |
| Tests | `tests/` — 97 fast + 4 FLARE-simulator (`-m slow`) |

A real (non-simulator) FLARE federation runs two verified ways: `scripts/local_federation.sh` with separate processes over mTLS, and Docker via `scripts/provision.sh && scripts/up.sh --flare`; both support the example specs, overseer flow, and partial-site coverage. The Next.js UI is implemented in `frontend/`; the Python API service exposing its documented contract remains separate. Not yet run: clients on remote hosts (Gefion / NextCloud).

`docs/architecture/flowchart.drawio` is the editable source for the implemented-flow overview. When changing that overview, regenerate both `docs/architecture/flowchart_drawio.svg` and `docs/architecture/flowchart.png` from it.

Planning documents: [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) (milestone-based; M0–M6 done) and [docs/variables.md](docs/variables.md) (documents the implemented variables; the local column names in it are invented for the mock TREs and not agreed with any real site). See [docs/README.md](docs/README.md) for the documentation index.

## Plans and code disagree often here

`README.md`, `docs/BUILD_PLAN.md`, and `docs/variables.md` describe intended behavior, some of it unbuilt. When code and documentation disagree, identify the discrepancy rather than silently following one: use the code to establish current behavior, and confirm intended behavior before changing it. A plan may legitimately describe the change being requested.

Confirm a path, command, module, or function exists before referencing it — the status table above is a snapshot and the repository moves fast. Update it in the same change that makes it wrong.

## Verification

Checks that exist and were confirmed working on 2026-09-17:

- `python -m pytest -q -m "not slow"` — 97 passed, ~2 s, no network.
- `python -m pytest -q -m slow` — 4 tests, ~75 s; starts the TREs as local processes and runs FLARE simulator jobs (allele_freq, straggler, FedAvg linreg, Newton-Raphson logreg). 3 pass; `test_straggler_yields_partial_coverage` has failed since 36cd9f8 made expected sites come from `sites.yaml`, because its simulator-only `ghost` client can no longer count as missing (pre-existing on `main`, checked 2026-09-18).
- `scripts/dev_tres.py` + `scripts/run_job.sh spec/examples/<spec>.json` — simulator end-to-end with `scripts/verify.py` against ground truth.
- `scripts/local_federation.sh up && scripts/local_federation.sh job spec/examples/allele_freq.json` — real FLARE server + 3 clients on localhost; ~12 s per job.
- `scripts/scale_sim.py` — N ∈ {3, 10, 50, 100} clients, exact at every N; ~2 min.
- `scripts/onboard_tre.sh <id>` — adds a site, regenerates, provisions, packs a kit (~2 s).
- `scripts/provision.sh && scripts/up.sh --flare` — builds 7 images (~5 min cold), starts everything, checks each TRE's `/health` from its own FLARE client container, asserts no TRE container can reach the public internet. Then `docker compose exec flare-server python scripts/run_job.py --mode prod --admin-kit "/workspace/federated_apis/prod_00/admin@ncfh.org" spec/examples/allele_freq.json` (~11 s) and `python scripts/verify.py server/out/<spec_hash>/result.json` on the host. Verified 2026-09-17 with Docker Desktop 29.8 on macOS.

CI: `.github/workflows/ci.yml` runs `python -m pytest -q -m "not slow"` on every push and pull request, against Python 3.11 and 3.12. No linter or formatter is configured.

For documentation changes, check source accuracy, links, and `git diff --check`.

Report exactly which checks ran. Do not invent commands or claim runtime validation you did not perform.

## Working rules

- `sites.yaml` is the only place sites are listed. `docker-compose.yml` and `flare/project.yml` are generated — edit the YAML and re-run `scripts/gen_sites.py`, never the generated files.
- Reach TRE data only through an adapter; adapters return only an `AggregateResult`.
- Prefer NVIDIA FLARE built-ins (provisioning, federated statistics, the scikit-learn examples) over hand-rolled equivalents.
- Read the existing adapters and tests before adding a new one, and match their shape. Make the minimal change that works.

## Conventions

- Branch as `<name>/<scope>`, e.g. `babenko/docs`. Branch from `main`.
- `main` is shared and several people push to it directly. Pull before starting work and expect divergence.
- Several teammates run different agents against this repo at once. Before editing a file outside your assigned area, check whether it belongs to someone else's task.
- `.claude/`, `.claude-plugin/`, and `.codex/` are gitignored local agent config — never commit them. To share a specific skill later, add a narrow exception for that path rather than un-ignoring the folder, which also holds local settings and generated records.
