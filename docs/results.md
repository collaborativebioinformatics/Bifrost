# Results

Measured output from Bifrost. Summarised in the root [README](../README.md#status); this file holds the full figures.

Synthetic cohort: 30 000 rows, 20 SNPs, age/sex/BMI/LDL, outcome SBP; split non-IID over three sites with **different column names and different APIs** (hunt 14 892 · gefion 8 953 · brev 6 155; per-site allele-frequency drift and age skew). All numbers below are from the real federation (`local_federation.sh`), 2026-09-17.

## Allele frequency — `spec/examples/allele_freq.json`

| SNP | federated (3/3 sites, n = 30 000) | ground truth | abs error |
|---|---|---|---|
| snp_rs001 | 0.074617 | 0.074617 | 0 |
| snp_rs002 | 0.237933 | 0.237933 | 0 |
| snp_rs003 | 0.230083 | 0.230083 | 0 |
| snp_rs004 | 0.154550 | 0.154550 | 0 |
| snp_rs005 | 0.131500 | 0.131500 | 0 |

Round time 11.9 s (job submit → merged result), of which the federated round itself is ~1.4 s; the rest is FLARE job deployment.

## Linear regression — `spec/examples/fed_linreg.json`, `sbp ~ age + sex + bmi + ldl + rs001 + rs007 + rs013`

| coefficient | exact (1 round, Gram matrices leave) | FedAvg (10 rounds, β per round after an init exchange) | pooled OLS |
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

FedAvg convergence (max |β − truth|): round 1 3.5e-1 → round 3 1.9e-3 → round 5 7.3e-6 → round 10 2.2e-11. With `--local-steps 5` the run converges to the *wrong* point (err ≈ 2.4e-2) — the classic non-IID FedAvg bias, kept as a test ([`tests/test_m4_linreg.py`](../tests/test_m4_linreg.py)).

Both modes use the same adapters: each site's adapter returns the Gram matrix `[1, y, X]ᵀ[1, y, X]` (an allow-listed aggregate). In FedAvg mode the FLARE client keeps that matrix inside the TRE: at initialisation a site sends `{n, features, sum, sum_sq}` so the server can fix one global standardisation, and each training round it sends the p+1 model coefficients β plus the sample count n. Local training is full-batch gradient steps on the site's own sufficient statistics (same update as `SGDRegressor.partial_fit` on that site's rows, without the rows).

## Suppression, overseer, straggler

| Scenario | What happened |
|---|---|
| `allele_freq_rejected.json` (`min_cell_size: 100`) | Every site withheld the hom-minor cell (`count=51/89/27<100`) **and** the derived allele counts (`derived_from_suppressed_cell`); audit `decision: PARTIAL` at all three sites. Server flagged `site_suppression:*` → overseer queue → approved by `lead` with a note → `released.json` written, release log records `QUEUED` then `RELEASED`. |
| `allele_freq_filtered.json` (`age ≥ 60`, `sex = 1`) | 3/3 sites, n = 4 690; DataSHIELD's own `nfilter.tab` and our k-rule both applied; nothing flagged. |
| brev's FLARE client killed before submission | Result marked **`2/3 sites`, `missing: ['brev']`**, n = 23 845, still exact for the reporting sites (verify recomputes the truth over them). Job completed in 11.8 s — nobody waited for the dead site. |
| Unknown `project_id` | Rejected by every adapter before any query; audited as `REJECTED`. |

Audit trail from the run above: hunt 16 lines, gefion 16, brev 15 (`OK` / `PARTIAL`), one line per spec per site plus one per FedAvg round. Server: `server/out/release_log.jsonl` (7 entries) and `server/out/<spec_hash>/{spec,result,check,released}.json`.

## Scaling — `scripts/scale_sim.py`

![round time vs number of TREs](scaling.png)

| TREs | federated round | whole simulator run | exact vs pooled truth |
|---|---|---|---|
| 3 | 4.1 s | 10.6 s | yes |
| 10 | 7.7 s | 16.6 s | yes |
| 50 | 21.4 s | 30.7 s | yes |
| 100 | 38.3 s | 47.7 s | yes |

Adapters cycled rest/datashield/sql across four `region`s, `1 000 × N` rows distributed unequally across the N sites (`scale_sim.py` sets `weight` 1/2/3 in turn), up to 8 client threads in the FLARE simulator. The timings above are not decomposed: `round_s` covers broadcast, all site responses and the merge, while the `merge_s` field records the disclosure check rather than the merge ([`flare/app/controller.py`](../flare/app/controller.py) starts that timer after `combine`). There is no separate merge benchmark, so no claim is made about aggregation cost on its own. Everything the server combines is a sum, so a regional relay aggregator (FLARE hierarchical topology, grouped on `region`) needs no change to adapters or analyses.
