# Provisional variable dictionary

This dictionary is a handoff draft, not an implemented schema. The repository
does not yet contain the planned generator, harmonisation map, site
configuration, or local datasets. Fields marked **TBD** need agreement before
they are used in an analysis specification or mapping.

Sources distinguish the current repository from planning input:

- [README](../README.md) describes requests in canonical variable names and a
  harmonisation map to each site's local columns.
- [API architecture use case](../API_architecture_use_case.txt) gives an
  example regression request with `age` and `bmi` predicting `blood_pressure`.
- [BUILD_PLAN.md](../BUILD_PLAN.md) is a shared working draft, initially
  written by Ioannis Christofilogiannis. It proposes 20 genotype columns with
  values `0`/`1`/`2`, plus age, sex, BMI, LDL, and a continuous regression
  outcome. It is not evidence of an existing dataset or agreed schema.

## Canonical variables

| Canonical name | Definition | Unit | Type / encoding | HUNT local name | Gefion local name | Brev local name | Status / source |
|---|---|---|---|---|---|---|---|
| `genotype.<variant_id>` | One of the proposed per-variant genotype columns used for allele-frequency analysis. There are 20 planned columns; their identifiers are TBD. | TBD | Planned values: integer `0`, `1`, or `2`. Allele orientation, ploidy, and missing-value encoding are TBD. | TBD | TBD | TBD | Proposed in BUILD_PLAN |
| `age` | Participant age. | TBD | TBD | TBD | TBD | TBD | Proposed phenotype in BUILD_PLAN; used as a feature in the API example |
| `sex` | Participant sex variable. Definition and permitted categories are TBD. | Not applicable | TBD | TBD | TBD | TBD | Proposed phenotype in BUILD_PLAN |
| `bmi` | Body mass index. | TBD | TBD | TBD | TBD | TBD | Proposed phenotype in BUILD_PLAN; used as a feature in the API example |
| `ldl` | LDL measurement. The analyte definition and reporting unit are TBD. | TBD | TBD | TBD | TBD | TBD | Proposed phenotype in BUILD_PLAN |
| `blood_pressure` | Proposed regression target in the API example. It is not specified as systolic, diastolic, or another pressure measure. | TBD | Planned continuous outcome; exact representation is TBD. | TBD | TBD | TBD | API example; continuous outcome proposed in BUILD_PLAN |

`genotype.<variant_id>` is a proposed normalized naming pattern, not an
existing canonical name. `bmi` and `ldl` are lowercase choices for this draft;
the API example uses `bmi`, while `ldl` is not a sourced canonical name. Do
not create 20 concrete variable identifiers until the variant list is supplied.

## Site and alias status

No implemented per-site schemas or canonical-to-local mappings exist in the
repository. The API example displays `age`, `BMI`, and `BP`, but those example
labels do not establish HUNT, Gefion, or Brev aliases. BUILD_PLAN uses `hunt`,
`gefion`, and `brev` as planned site IDs, while the README describes Brev as a
possible aggregation-server location. Therefore the Brev-as-data-site label,
all site ownership, and every local alias remain provisional.

## Decisions needed

1. Provide the 20 variant identifiers and state which allele the `0`/`1`/`2`
   values count. Confirm ploidy, missing-value encoding, and the denominator
   for allele-frequency calculations.
2. Confirm the regression target and its unit; if it is blood pressure,
   specify the measurement (for example, systolic or diastolic) and unit.
3. Define units and valid encodings for age, sex, BMI, and LDL.
4. Confirm participating data sites, their owners, and each canonical-to-local
   alias after the local schemas are available.

## Resources to review

Espen suggested these project resources to check before adding new
implementation. They are retained here as candidates for schema/example reuse;
their contents have not been verified for this draft.

- NVIDIA FLARE examples: <https://github.com/NVIDIA/NVFlare>
- FedGen: <https://github.com/collaborativebioinformatics/FedGen>
- `federated_architecture`: <https://github.com/cmig-research-group/federated_architecture>
- Pinned `regenie_on_hunt` example: <https://github.com/cmig-research-group/federated_architecture/tree/cd2863afedd3d6a4adb93bdd144e201f15477366/examples/regenie_on_hunt>
