# AGENTS.md

Instructions for AI coding agents working in this repository. Humans should start with [README.md](README.md).

## Project

Bifrost — run one analysis across several Trusted Research Environments (TREs) without any record-level data leaving a TRE. Each TRE computes locally behind its own native API and returns disclosure-checked aggregates; a FLARE server combines them. Built by Team 1 for the NCFH 2026 hackathon.

Python 3.11–3.12, NVIDIA FLARE, FastAPI, DuckDB, Docker Compose. Dependencies are pinned in [pyproject.toml](pyproject.toml); ask before adding one.

## Status (2026-09-17)

Implemented:

| Area | Where |
| --- | --- |
| Site definitions | `sites.yaml` — three TREs (`hunt`/rest, `gefion`/datashield, `brev`/sql) |
| Generators | `scripts/gen_sites.py` emits `docker-compose.yml` and `flare/project.yml` from `sites.yaml` |
| Synthetic data | `data/generate.py` → per-site files + `data/ground_truth.json` |
| Mock TREs | `tres/rest`, `tres/datashield`, `tres/sql`, each containerised with its own API |
| Adapters | `adapters/` behind `adapters/registry.py`, plus `adapters/safe_output.py` |
| Analysis spec | `spec/analysis_spec.py`, examples in `spec/examples/` |
| Harmonisation | `harmonisation/canonical.yaml` |
| Tests | `tests/` — 21 passing |

Not implemented: FLARE jobs (`flare/` has only Dockerfiles and `project.yml`; there is no `jobs/`), server-side disclosure check, overseer queue, scale simulation, UI.

Known discrepancies — do not treat these as existing:

- `pyproject.toml` lists `server` in `[tool.setuptools] packages`, but there is no `server/` directory. An editable install will fail until one is added or the entry is removed.
- `sites.yaml` documents `scripts/onboard_tre.sh`; that script does not exist.
- `docs/variables.md` states the repository has no generator, harmonisation map, or site configuration. That was true when written and is now outdated.
- `flowchart.drawio` does not match the rendered `flowchart_drawio.svg`.

Planning documents: [BUILD_PLAN.md](BUILD_PLAN.md) (provisional, milestone-based) and [docs/variables.md](docs/variables.md) (draft dictionary, many fields still TBD).

## Plans and code disagree often here

`README.md`, `BUILD_PLAN.md`, and `docs/variables.md` describe intended behavior, some of it unbuilt. When code and documentation disagree, identify the discrepancy rather than silently following one: use the code to establish current behavior, and confirm intended behavior before changing it. A plan may legitimately describe the change being requested.

Confirm a path, command, module, or function exists before referencing it — the status table above is a snapshot and the repository moves fast. Update it in the same change that makes it wrong.

## Verification

Checks that exist and were confirmed working on 2026-09-17:

- `python -m pytest -q` — 21 passed. Test paths come from `[tool.pytest.ini_options]`.
- `scripts/up.sh` — builds and starts all TREs, then smoke-tests a count from each and asserts no TRE can reach the public internet. Requires Docker; not run during this check.

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
