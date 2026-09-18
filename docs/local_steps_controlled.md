# Controlled comparison: local steps in federated averaging

Recorded 2026-09-17. Guarded by [`tests/test_local_steps_controlled.py`](../tests/test_local_steps_controlled.py),
which checks the behaviour below within regression tolerances — the O(1e-2) residuals to
within 5%, the converged cases by magnitude (< 1e-9). A passing test does not reproduce the
exact digits in the table.

## Why this exists

[`results.md`](results.md) reports a coefficient residual of about 2.4e-2 for federated
averaging with `--local-steps 5`, but the run that produced it did not record its learning
rate or round count. The two FedAvg tests in [`tests/test_m4_linreg.py`](../tests/test_m4_linreg.py)
vary three parameters between them:

| test | lr | local steps | rounds |
|---|---|---|---|
| `test_fedsgd_converges_to_exact_ols` | 1.0 | 1 | 15 |
| `test_local_steps_gt1_shows_non_iid_bias` | 0.5 | 5 | 60 |

Neither isolates `local_steps`, and the second asserts only `1e-4 < err < 1.0`, so it does
not produce the reported figure. This comparison holds learning rate and round count fixed
and varies only the number of local steps.

## Method

Site Gram matrices from the three mock TREs via the usual adapters, spec
[`spec/examples/fed_linreg.json`](../spec/examples/fed_linreg.json)
(`sbp ~ age + sex + bmi + ldl + snp_rs001 + snp_rs007 + snp_rs013`). One global
standardisation from the round-0 moments, then `rounds` iterations of
`fedavg(local_update(..., lr, steps))`. Metric: maximum absolute deviation of the
recovered coefficients from the pooled OLS solution in [`data/ground_truth.json`](../data/ground_truth.json).

```
python -m pytest tests/test_local_steps_controlled.py -q
```

## Results

| lr | rounds | local steps | max abs coefficient deviation |
|---|---|---|---|
| 1.0 | 10 | 1 | 2.171e-11 |
| 1.0 | 50 | 1 | 1.633e-11 |
| 1.0 | 200 | 1 | 1.633e-11 |
| 1.0 | 10 | 5 | 2.296e-02 |
| 1.0 | 50 | 5 | 2.296e-02 |
| 1.0 | 200 | 5 | 2.296e-02 |
| 0.5 | 10 | 1 | 8.791e-02 |
| 0.5 | 60 | 1 | 1.633e-11 |
| 0.5 | 200 | 1 | 1.633e-11 |
| 0.5 | 10 | 5 | 2.386e-02 |
| 0.5 | 60 | 5 | 2.386e-02 |
| 0.5 | 200 | 5 | 2.386e-02 |

Exact cross-product aggregation on the same Gram matrices: 1.688e-11.

## What these values support

- With learning rate and round count held fixed, one local step reaches pooled OLS while
  five leaves a residual of about 2.3e-2. Only `local_steps` differs.
- The five-step residual is unchanged at the precision shown across 10, 50 and 200 rounds,
  so over the range tested the gap does not close with more rounds.
- The historical ~2.4e-2 in `results.md` is reproduced at lr 0.5 with five steps, which
  identifies the configuration that figure most likely came from.
- At lr 0.5 and 10 rounds, one step has not converged either (8.791e-02). Round count and
  learning rate both matter; the five-step residual is not simply "the error".

## What they do not support

- **Not a fixed point.** Three round counts are an observation, not a proof that the
  iterate has stopped moving.
- **Not learning-rate independence.** Two learning rates give 2.296e-02 and 2.386e-02 —
  close, but different, and two points establish nothing about the general case.
- **Not a new result.** Client drift under several local steps on non-IID data is a known
  property of federated averaging. This records that our implementation reproduces it, and
  quantifies it for this cohort; it is not a finding about federated averaging in general.

## Environment

| | |
|---|---|
| commit | `ac8119f` |
| Python | 3.13.5 |
| numpy | 1.26.4 |
| pandas | 2.2.3 |

`pyproject.toml` declares `requires-python = ">=3.11,<3.13"`, so the 3.13.5 used to record
the table is outside the supported range. Two checks cover that:

- Every value in the table was also obtained on Python 3.11.14 through a path that builds
  the Gram matrices directly from `data/sites/*.csv` without the adapters, and was
  identical to all digits shown. The figures do not depend on the interpreter or on the
  adapter layer.
- CI runs the fast suite on 3.11 and 3.12 ([run 35318612599](https://github.com/collaborativebioinformatics/Bifrost/actions/runs/35318612599)):
  79 passed, 3 deselected on both. The tests here pass on supported interpreters.

CI establishes that the assertions hold on supported versions, within the tolerances above.
It does not establish that the exact digits in the table reproduce across environments.
