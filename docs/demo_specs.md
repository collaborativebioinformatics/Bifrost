# Demo specs

Four example analysis specifications live in `spec/examples/`. Together they walk through the full path — one request, three heterogeneous TREs, local computation, safe output, server-side aggregation and disclosure control — and they cover the release case, the filtered case, the suppression case and the federated-learning case.

Every spec is an `AnalysisSpec` (`spec/analysis_spec.py`): canonical variable names only, a `project_id` that must appear in the Safe Projects allow-list (`projects.yaml`), and a `min_cell_size` that sets the suppression threshold. The spec hash keys the audit and release logs.

## Running them

Start the TREs in their own terminal and leave them running:

```
python scripts/dev_tres.py
```

Then either path, from a second terminal:

```
python scripts/run_local.py spec/examples/<spec>.json    # adapters directly, no FLARE
scripts/run_job.sh spec/examples/<spec>.json             # through the FLARE simulator
```

The FLARE path writes `server/out/<spec_hash>/`: `result.json` (merged result, every run), `check.json` (disclosure decision) and `released.json` (only when the check passes or an overseer approves). Each TRE appends its own decision to `audit/<tre_id>/audit.jsonl`.

## `allele_freq.json`

**Purpose.** The baseline release case: federated allele frequency over five variants.

```json
{"analysis_type": "allele_freq", "variables": ["snp_rs001" … "snp_rs005"], "project_id": "ncfh-2026-demo", "min_cell_size": 5}
```

**What should happen.** Each TRE counts genotypes locally, the filter derives allele counts from the genotype table, and only those counts leave. The server sums them and divides. With all three sites reporting and no cell below five, the audit decision is `OK` at each site and the disclosure check should return `OK`.

**What it demonstrates.** One spec, three different native APIs — REST, DataSHIELD-style and SQL — reaching the same answer through the adapter layer. `scripts/verify.py` compares the result with the pooled truth in `data/ground_truth.json`; allele frequencies are exact sums, so agreement should be to floating-point error.

## `allele_freq_filtered.json`

**Purpose.** The same analysis over a cohort subset: two variants restricted to `age >= 60` and `sex == 1`.

**What should happen.** Filters are written in canonical names and each adapter translates them to that site's local column — `alder`/`age_years`/`AGE` for age — before the query runs. The cohort shrinks, so counts fall and the suppression threshold starts to matter.

**What it demonstrates.** That harmonisation applies to filters and not only to output variables. Note that this spec cannot be checked with `verify.py`: `data/ground_truth.json` is computed on the unfiltered cohort and the verifier applies no cohort filters, so it would compare a filtered result against unfiltered truth. Validating a filtered spec needs a truth computed over the same subset, which nothing in the repository currently produces.

## `allele_freq_rejected.json`

**Purpose.** Suppression, deliberately triggered by raising `min_cell_size` to 100 for a single variant.

**What should happen.** This is a *partial* release, not a total rejection. Verified by `tests/test_m2_adapters.py::test_small_cells_suppressed_and_audited`: at every site the homozygous-minor genotype cell (`2`) falls below 100 and is withheld, and because allele counts could be back-calculated from the remaining cells, they are withheld too — `snp_rs001.allele_counts:derived_from_suppressed_cell`. Each site audits the run as `PARTIAL`.

Because every site suppressed something, the server-side check adds a `site_suppression` reason per site (`server/disclosure_check.py`), so the merged result is `FLAGGED`. This flagged result requires overseer approval before release, via `server/overseer_queue.py`. An older `released.json` can remain from a previous run of the same spec, since run directories are keyed by spec hash — `check.json` carries the current decision.

**What it demonstrates.** How the disclosure controls handle small cells end to end: a local rule fires, the reason is recorded in each TRE's audit log, the server notices the incomplete table, and release waits on a person.

## `fed_linreg.json`

**Purpose.** Federated linear regression: `sbp` on age, sex, BMI, LDL and the three causal variants.

**What should happen.** Each TRE returns a Gram matrix — the sufficient statistic for OLS — and nothing else; the server sums them and solves once. The filter releases a Gram matrix only when `n >= max(min_cell_size, #cols + 1)`. That is a sample-count threshold, not a test for near-singularity: it withholds output from a site with too few rows to fit the model, but it does not detect an ill-conditioned one.

A FedAvg variant runs through `scripts/run_job.py --fedavg`. Rows still never leave, but more than coefficients does: in round 0 each site reports its `n` plus feature sums and sums of squares, so the server can fix one global standardisation; in each later round a site returns its `n` and its updated coefficients (`flare/app/linreg.py`).

**What it demonstrates.** That a second analysis type runs through unchanged adapters, and the distinction the project rests on: federated *analysis* releases counts, federated *learning* releases model parameters. The exact path should reproduce the pooled OLS coefficients in `data/ground_truth.json`.

## Limits on what these show

Read this before quoting any of it as a result.

- **No captured run is recorded here.** The expected outcomes above are derived from the specs, the filter and check implementations, and the test suite — not from a stored transcript of a demo run. The M6 results table (per-site n, released statistics against ground truth, suppression events, overseer decisions) still needs figures from a real run.
- **Simulator only.** Everything verified so far has run in-process or through the FLARE simulator. Nobody has yet run the stack against real Docker containers or a live, non-simulator FLARE server.
- **`verify.py` checks what is present.** It reads the merged result, not the released artifact. It compares allele frequencies even under partial coverage, and means and OLS coefficients only when every site reported. Statistics the filter suppressed are skipped entirely. `PASS` means no compared statistic exceeded tolerance — zero comparisons still produce `PASS`.
- **Synthetic data, invented column names.** The cohort comes from `data/generate.py` and the per-site local names in `docs/variables.md` are placeholders for the mock TREs, agreed with no real site.
- **`released.json` can be stale.** Run directories are keyed by spec hash, so a release from an earlier run of the same spec survives a later flagged run. `check.json` carries the current decision.
