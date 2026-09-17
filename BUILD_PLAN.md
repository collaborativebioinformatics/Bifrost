# Build plan: cross-TRE federated analysis (Federated_APIs — NCFH 2026 hackathon, Team 1)

> **Working draft.** Initial draft by Ioannis Christofilogiannis. It is "very generic for now but we will build on it." Architecture, ownership, timings, interfaces, and scope remain subject to team review. The implementation described below is mostly proposed; consult the repository before treating a path or command as available.

This draft targets a three-day hackathon prototype and proposes an end-to-end demo across three TREs. The estimates are planning targets, not commitments. Proposed tools are Python 3.11, uv or pip, Docker Compose, and NVIDIA FLARE with minimal dependencies.

## Proposed handoffs

| Target time | From → To | Proposed output |
|---|---|---|
| Day 1, 19:00 | Writer → Adapter dev | `docs/variables.md`: canonical variables, definitions, units, per-TRE local names |
| Day 2, 10:00 | Sysadmin → Adapter dev | `docker compose up` works on a clean machine |
| Day 2, 12:00 | Adapter dev → Lead | Adapter tests green |
| Day 2, 14:00 | Lead → Writer | First aggregated result + `verify.py` output, for mid-term slides |
| Day 3, 13:00 | Lead/Sysadmin → Writer | `audit.jsonl` from all TREs + results table + scaling plot |

## Problem

Trusted Research Environments (TREs) hold de-identified health/genomic data behind **different APIs and different schemas**. Run one analysis across all of them without any row-level data leaving its TRE. Preserve the Five Safes: outbound-only networking from each TRE (clients dial out, nothing dials in), read-only data, disclosure control before any output leaves, human overseer on flagged outputs.

Use cases (from README):

1. **Allele frequency** — federated *analysis*: each TRE computes allele counts locally; only counts leave; server sums.
2. **Linear regression** — federated *learning*: each TRE trains locally; only model parameters leave; server aggregates (FedAvg).

## Proposed architecture

- **Orchestrator**: NVIDIA FLARE server outside the TREs. The team may evaluate `nvflare provision` and a `project.yml` for the hackathon setup to ease a later production transition; this has not been validated and is not a guaranteed configuration-only switch.
- **One FLARE client per TRE** inside a TRE container, connecting outward over mutual-TLS gRPC.
- **Adapter pattern**: one adapter per TRE behind a common interface. A FLARE executor would call the adapter, which would speak the TRE's native API.
- **Harmonisation map**: proposed `harmonisation/canonical.yaml` maps canonical variables to local columns per TRE. The current variable-dictionary handoff is [docs/variables.md](docs/variables.md).
- **Safe-output filter** inside each TRE before results reach the FLARE client.
- **Server-side disclosure check** on combined results; a flagged result could enter an overseer queue before release.
- **Audit log** per TRE, proposed as `audit.jsonl`.

Proposed adapter contract; this is not implemented code:

```python
# adapters/base.py
@dataclass
class AggregateResult:
    tre_id: str
    n: int
    stats: dict            # canonical_var -> {count, sum, sum_sq, min, max, histogram?, allele_counts?}
    rejected: list[str]    # vars/cells suppressed by Safe Output filter

class TREAdapter(ABC):
    def schema(self) -> dict[str, str]: ...        # canonical_var -> local dtype
    def run(self, spec: AnalysisSpec) -> AggregateResult: ...
```

Proposed analysis-spec contract; this is not implemented code:

```python
# spec/analysis_spec.py
class AnalysisSpec(BaseModel):
    analysis_type: Literal["allele_freq", "fed_stats", "fed_linreg"]
    variables: list[str]           # canonical names only
    filters: dict[str, Any] = {}
    min_cell_size: int = 5
    project_id: str                # Safe Projects allow-list
```

## Scalability goals to evaluate

These are design goals for discussion. They require implementation and validation; none establishes that scaling to hundreds of TREs is a configuration-only change.

1. Keep site configuration in a proposed `sites.yaml` with `tre_id`, adapter, API URL, and region. The target is for `scripts/gen_sites.py` to generate Compose configuration, FLARE `project.yml`, and tests from it.
2. Discover adapters through a proposed `adapters/registry.py`, using entry points or a dictionary, and load the configured adapter by `tre_id`.
3. Provide a proposed `scripts/onboard_tre.sh <tre_id>` that provisions a client kit and states the required outbound network rule.
4. Configure proposed `min_clients` and `wait_time` settings so an analysis can report missing sites instead of requiring every site.
5. Restrict proposed server combinations to sums, counts, and weighted parameter vectors so they can be combined hierarchically, subject to the chosen analysis interface and FLARE capabilities.
6. Include a proposed `region` field from the first site configuration, even if all initial sites are `nordic`.
7. Apply proposed N-aware k-anonymity and dominance rules on merged tables, and key a proposed release log by specification hash for repeated-query checking.

## Proposed repository layout

The following is a sketch. Except for the existing documentation and diagram files, do not assume these paths or generated artifacts exist.

```
Federated_APIs/
  README.md, flowchart_drawio.svg
  docs/  variables.md, demo_specs.md
  sites.yaml                                  # the only place sites are listed
  docker-compose.yml                          # generated
  data/generate.py                            # synthetic genotypes + phenotypes, N splits, ground truth
  harmonisation/canonical.yaml
  spec/analysis_spec.py
  adapters/  base.py, registry.py, safe_output.py, rest.py, datashield.py, sql.py
  tres/      rest/, datashield/, sql/         # mock TRE images
  flare/     project.yml, jobs/allele_freq/, jobs/fed_linreg/
  server/    disclosure_check.py, overseer_queue.py, release_log.jsonl
  scripts/   gen_sites.py, onboard_tre.sh, up.sh, run_job.sh, verify.py, scale_sim.py
  tests/
```

## Proposed milestones

### M0 — Skeleton + synthetic data (target: ≤ 1h) — Lead + Sysadmin

- Layout above, `pyproject.toml`, `sites.yaml` with three entries (`hunt`, `gefion`, `brev`), `gen_sites.py` producing compose + FLARE project.yml from it.
- `data/generate.py`: ~30k rows; 20 SNP genotype columns (0/1/2), phenotypes (age, sex, BMI, LDL), continuous outcome for regression. Split non-IID across N sites (N from `sites.yaml`), **different local column names per site**. Save `ground_truth.json` (global allele frequencies, global OLS coefficients).
- Use placeholder canonical variables until the Writer's table arrives at 19:00.
- **Done when**: `python scripts/gen_sites.py && python data/generate.py` produces N data files + ground truth.

### M1 — Three mock TREs with different APIs (target: ≤ 2h) — Adapter dev (APIs) + Sysadmin (containers)

- `rest`: FastAPI `POST /query` `{cols, filters, agg}`.
- `datashield`: `POST /ds` `{fn, args}` mimicking DataSHIELD call style (Python stand-in).
- `sql`: `POST /sql` read-only against DuckDB.
- Each container: data `:ro`, own Docker network `internal: true`; only the FLARE client process routes to the server network.
- **Done when**: `docker compose up` starts all sites; `curl` returns a count from each; `curl https://example.com` from inside a TRE fails.

### M2 — Adapters + Safe Output filter (target: ≤ 2h) — Adapter dev

- Three adapters via the registry; canonical → local via `canonical.yaml`.
- `safe_output.py`: suppress `count < min_cell_size`, allow-listed aggregates only, no row-level, append to `audit.jsonl`.
- Tests: identical `AggregateResult` shape across adapters; suppression test; registry loads by name.
- **Done when**: tests green.

### M3 — Allele frequency end-to-end on FLARE (target: ≤ 3h) — Lead + Sysadmin — mid-term target

- **Gate**: do not begin M3 until the adapter tests from M2 are green and the Lead confirms the handoff.
- `nvflare provision` from generated project.yml, POC startup, one client per container.
- `jobs/allele_freq`: executor loads adapter by `tre_id`, returns allele counts; server sums and divides. `min_clients=2`, `wait_time=60s`.
- `server/disclosure_check.py` on merged result → OK / flag → overseer queue; `verify.py` vs ground truth.
- **Done when**: one spec → three heterogeneous APIs → correct global allele frequencies; a deliberate rejection visible in every `audit.jsonl`; killing one container mid-round still yields a result marked "2/3 sites".

### M4 — Federated linear regression (target: ≤ 3h) — Lead

- `jobs/fed_linreg`: FLARE scikit-learn linear model example with FedAvg; adapter returns local gradient/parameters only. Compare to global OLS in ground truth.
- **Done when**: second analysis type runs through the same adapters with zero adapter changes.

### M5 — Scale evidence (target: ≤ 2h) — Sysadmin (stretch)

- `scripts/scale_sim.py`: run the allele-frequency job in the FLARE simulator with N ∈ {3, 10, 50, 100} lightweight clients (same adapter, generated data), record round time and result correctness. Emit `docs/scaling.png`.
- **Done when**: one plot of round time vs. N, and `onboard_tre.sh newsite` adds a fourth TRE in under a minute.

### M6 — README + results (target: ≤ 1h) — Writer (with Lead)

- README: diagram, one-command run, results table (per-site n, global stats vs ground truth, suppression events, overseer decisions), Five Safes mapping, scaling plot, "adding a TRE" section.

### W — Writer track (parallel)

- **Day 1**: `docs/variables.md` (canonical name, definition, unit, type, local name per site). Commit the flowchart.
- **Day 2**: draft `docs/demo_specs.md` with allele-frequency, filtered, and deliberately rejected specifications (`min_cell_size` too high); draft the README "Five Safes + overseer" section; proposed 13:30 writers meeting. Verification and result claims depend on the corresponding code.
- **Day 3**: use available `audit.jsonl`, `release_log.jsonl`, results, and validation outputs to write "What left each TRE". Prepare proposed slides from the README and present.

## Open decisions

1. Confirm team roles and named owners. The `Adapter dev` role is provisional and may overlap with Lead or Sysadmin work.
2. Confirm Brev's intended role or roles: M0 proposes it as a simulated data site, while the [README](README.md) describes it as a possible aggregation-server location. Define the required separation if both are retained.
3. Agree the variable contract and per-TRE aliases in [docs/variables.md](docs/variables.md) before implementing the harmonisation map.
4. Review Espen's suggested resources before settling interfaces: NVIDIA FLARE (<https://github.com/NVIDIA/NVFlare>), FedGen (<https://github.com/collaborativebioinformatics/FedGen>), [`federated_architecture`](https://github.com/cmig-research-group/federated_architecture), and its [pinned `regenie_on_hunt` example](https://github.com/cmig-research-group/federated_architecture/tree/cd2863afedd3d6a4adb93bdd144e201f15477366/examples/regenie_on_hunt). Their contents have not been assessed in this draft.
5. Resolve whether the adapter contract is statistics-only (`AggregateResult`) or must also carry gradients or parameters for M4. The answer determines whether M4 can use the same adapters unchanged.

## Proposed implementation rules

- No analysis code imports a TRE data file directly; everything goes through an adapter.
- No adapter returns anything but an `AggregateResult`.
- No file lists sites except `sites.yaml`.
- Prefer FLARE built-ins (federated statistics, sklearn examples, provisioning) over hand-rolled code.
- Every milestone independently runnable; if M4/M5 slip, M3 is still the demo.
