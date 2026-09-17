# Variable dictionary

These variables are **implemented** — `harmonisation/canonical.yaml` defines them, `data/generate.py` generates a synthetic cohort from them, and the adapters resolve canonical names to local columns through them. Running `python data/generate.py` produces `data/sites/<tre_id>.csv` with each site's own column names.

They are **not agreed with any real TRE**. The local column names below are invented for the mock TREs; none of them is a HUNT, Gefion, or Brev schema. `harmonisation/canonical.yaml:2` still labels its content a placeholder pending this dictionary, and the naming stays provisional until each site supplies its real schema.

Site IDs follow `sites.yaml`: `hunt` (REST adapter), `gefion` (DataSHIELD-style), `brev` (SQL). The README also describes Brev as a candidate location for the aggregation server, so its role as a data site is a property of the mock setup, not a commitment.

## Phenotypes

| Canonical | Description | Type | Unit | HUNT | Gefion | Brev |
|---|---|---|---|---|---|---|
| `age` | Age at baseline. Integer-rounded. | continuous | years | `alder` | `age_years` | `AGE` |
| `sex` | Sex, encoded `0` = female, `1` = male. | binary | — | `kjonn` | `sex_male` | `SEX` |
| `bmi` | Body mass index, 1 decimal. | continuous | kg/m² | `bmi_baseline` | `body_mass_index` | `BMI` |
| `ldl` | LDL cholesterol, 2 decimals. | continuous | mmol/L | `ldl_kol` | `ldl_c` | `LDL_MMOL` |
| `sbp` | Systolic blood pressure. Regression outcome, 1 decimal. | continuous | mmHg | `systolisk` | `sbp_mmhg` | `SBP` |
| `case` | Disease case status, 0/1. Logistic regression outcome. | binary | — | `case_status` | `is_case` | `CASE_STATUS` |

`sbp` resolves the `blood_pressure` target left open by the API architecture example — the implementation chose systolic. Which measurement protocol it represents (seated, mean of *n* readings) is still undefined.

## Genotypes

Twenty variants, `snp_rs001` through `snp_rs020`, each a minor-allele dosage of `0`, `1`, or `2` (unit: alleles). Local names follow a per-site pattern:

| Site | Pattern | Example (`snp_rs001`) |
|---|---|---|
| `hunt` | `rs<nnn>_gt` | `rs001_gt` |
| `gefion` | `SNP_<n>` | `SNP_1` |
| `brev` | `g_rs<nnn>` | `g_rs001` |

The `rs001`–`rs020` identifiers are synthetic placeholders, not real dbSNP rsIDs. Allele frequency is computed as `sum(dosage) / (2 × n)`; that denominator fixes ploidy at 2 and says nothing about orientation. Which allele the dosage counts is set by the schema and the generator: `harmonisation/canonical.yaml` describes every column as minor-allele dosage, and the generator draws `Binomial(2, p)` with `p ≤ 0.5` at every site, so the minor allele stays the minor allele throughout the synthetic cohort. Real cohorts give no such guarantee. The synthetic data contains no missing values, so a missing-value encoding has never been exercised.

## Synthetic cohort

`data/generate.py` draws 30,000 rows (seed 7 by default) and splits them non-IID across sites. Ground truth is computed on the pooled data before splitting and written to `data/ground_truth.json`, which is what federated results are checked against.

Generating model:

- `age ~ N(55, 12)` plus a per-site shift of −6 to +6 years, clipped to 18–95; `bmi ~ N(26.5, 4.5)` clipped to 15–55; `ldl ~ N(3.4, 0.9)` clipped to 0.5–9; `sex ~ Bernoulli(0.5)`.
- Each SNP has a global minor-allele frequency drawn from U(0.05, 0.45), multiplied per site by a drift factor in U(0.7, 1.3) and clipped to [0.01, 0.5]. Dosages are `Binomial(2, p)`, so genotypes are in HWE within a site but not across the pooled cohort.
- `sbp = 90 + 0.5·age + 4.0·sex + 0.8·bmi + 1.2·ldl + 2.5·snp_rs001 − 1.5·snp_rs007 + 1.0·snp_rs013 + N(0, 10)`.

Normal parameters are written `N(mean, standard deviation)`, matching the arguments of `numpy.random.Generator.normal`.

Only `snp_rs001`, `snp_rs007`, and `snp_rs013` affect the outcome; the other seventeen are null by construction. Site sizes follow the `weight` field in `sites.yaml` (hunt 5, gefion 3, brev 2), and later sites skew older. Both are deliberate: unequal site sizes and distributions expose errors from averaging site-level results without appropriate weighting. Correctly aggregated allele counts and OLS sufficient statistics should reproduce the pooled ground truth.

Each per-site CSV carries a leading `row_id` column, which `tres/common.py:27` drops at load so it can never be queried or filtered on.

## Still to agree

1. Real local column names for each canonical variable, per TRE, replacing the invented ones above.
2. Real variant identifiers, plus an identified effect allele per variant that every site counts — the minor allele is not necessarily the same allele across real cohorts, so orientation has to be fixed by identity rather than by frequency.
3. Missing-value encoding, and what an adapter should do when a canonical variable is absent at a site.
4. The `sbp` measurement definition, and whether it is the outcome the team wants for the demo.
5. Confirmation of `sex` as a binary `0`/`1` field, including how sites that record additional categories should map.
6. Whether Brev is a data site, the aggregation server, or both.
7. `case`'s local name at `brev` is `CASE_STATUS`, not `CASE` — `CASE` is a reserved word in DuckDB SQL and breaks the SQL TRE's queries. Worth keeping in mind when a real site proposes a local column name: it has to be a valid identifier in that site's own query engine, not just unique.

## Resources to review

Espen suggested these before adding new implementation. Retained as candidates for schema and example reuse; contents not verified for this dictionary.

- NVIDIA FLARE examples: <https://github.com/NVIDIA/NVFlare>
- FedGen: <https://github.com/collaborativebioinformatics/FedGen>
- `federated_architecture`: <https://github.com/cmig-research-group/federated_architecture>
- Pinned `regenie_on_hunt` example: <https://github.com/cmig-research-group/federated_architecture/tree/cd2863afedd3d6a4adb93bdd144e201f15477366/examples/regenie_on_hunt>
