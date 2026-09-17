# AGENTS.md

Instructions for AI coding agents working in this repository. Humans should start with [README.md](README.md).

## Project

Bifrost — run one analysis across several Trusted Research Environments (TREs) without any record-level data leaving a TRE. Each TRE computes locally behind its own native API and returns disclosure-checked aggregates; a FLARE server combines them. Built by Team 1 for the NCFH 2026 hackathon.

Python 3.11–3.12, NVIDIA FLARE, FastAPI, DuckDB, Docker Compose. Dependency version constraints are declared in [pyproject.toml](pyproject.toml); ask before adding one.

## Status (2026-09-17, after M5)

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
| FLARE jobs | `flare/app/` — `controller.py`/`executor.py` (allele_freq, fed_stats, exact fed_linreg), `linreg_controller.py`/`linreg_executor.py`/`linreg.py` (FedAvg fed_linreg). `scripts/build_job.py` assembles a job folder; `flare/jobs/` is generated and gitignored |
| Server side | `server/aggregate.py` (associative merge), `server/disclosure_check.py`, `server/overseer_queue.py` (CLI); outputs under `server/out/` (gitignored, `SERVER_OUT` env) |
| Ops scripts | `scripts/local_federation.sh` (real FLARE, no Docker), `scripts/provision.sh`, `scripts/onboard_tre.sh`, `scripts/start_server.sh`, `scripts/start_client.sh`, `scripts/run_job.py` (`--mode simulator|prod`, `--fedavg`), `scripts/run_local.py`, `scripts/verify.py`, `scripts/scale_sim.py` → `docs/scaling.png` |
| Tests | `tests/` — 31 fast + 3 FLARE-simulator (`-m slow`) |

A real (non-simulator) FLARE federation — provisioned server + clients as separate processes over mTLS — runs on one machine with `scripts/local_federation.sh`; all example specs, the overseer flow and a dead-client run were verified on it. Not implemented: UI. Not yet run anywhere: the Docker path (`scripts/up.sh`, images) — no Docker on the development machine.

Known discrepancies — do not treat these as existing:

- `flowchart.drawio` does not match the rendered `flowchart_drawio.svg`.

Planning documents: [BUILD_PLAN.md](BUILD_PLAN.md) (milestone-based; M0–M6 done) and [docs/variables.md](docs/variables.md) (documents the implemented variables; the local column names in it are invented for the mock TREs and not agreed with any real site).

## Plans and code disagree often here

`README.md`, `BUILD_PLAN.md`, and `docs/variables.md` describe intended behavior, some of it unbuilt. When code and documentation disagree, identify the discrepancy rather than silently following one: use the code to establish current behavior, and confirm intended behavior before changing it. A plan may legitimately describe the change being requested.

Confirm a path, command, module, or function exists before referencing it — the status table above is a snapshot and the repository moves fast. Update it in the same change that makes it wrong.

## Verification

Checks that exist and were confirmed working on 2026-09-17:

- `python -m pytest -q -m "not slow"` — 31 passed, ~1 s, no network.
- `python -m pytest -q -m slow` — 3 passed, ~50 s; starts the TREs as local processes and runs FLARE simulator jobs (allele_freq, straggler, FedAvg linreg).
- `scripts/dev_tres.py` + `scripts/run_job.sh spec/examples/<spec>.json` — simulator end-to-end with `scripts/verify.py` against ground truth.
- `scripts/local_federation.sh up && scripts/local_federation.sh job spec/examples/allele_freq.json` — real FLARE server + 3 clients on localhost; ~12 s per job.
- `scripts/scale_sim.py` — N ∈ {3, 10, 50, 100} clients, exact at every N; ~2 min.
- `scripts/onboard_tre.sh <id>` — adds a site, regenerates, provisions, packs a kit (~2 s).
- `scripts/up.sh` — builds and starts all TREs, then checks each one's `/health` endpoint from its own FLARE client container and asserts that no TRE container can reach the public internet. Requires Docker; not run during this check.

No linter or formatter is configured, and there is no CI.

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
