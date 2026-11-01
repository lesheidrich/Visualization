# Plots and tables

What we are building and where each piece stands. There are four features, and all the GC-style metrics live in one table.

**Status marks**

| Mark | Meaning |
|---|---|
| ✓ | Done |
| X | Not done |
| X_resampling | Done except for the resampled CI |

**Terms**

| Term | Meaning |
|---|---|
| Rerun value | The one number a rerun gives for a strategy, thread count and metric. |
| Mean of reruns | The mean of those rerun values. This is the bar height. |
| Resampled CI | A confidence interval calculated by resampling the rerun values. This is the error bar. The method is a 95% percentile bootstrap, with a t interval reported beside it. |

The resampling method is documented in [resampling.md](resampling.md), and the thesis changes it needs in [notes_for_thesis.md](notes_for_thesis.md).

We never average CI bounds.

## Features

| # | Feature | Part | Status | Notes |
|---|---|---|---|---|
| 1 | Throughput | Plot per rerun | ✓ | `uv run thrpt`. Error bars are the ledger CI of that rerun. |
| | | Plot across reruns | ✓ | Bars are the mean of reruns. Error bars are the resampled CI. The t interval is in the CSV. |
| 2 | GC and co | One table | See below | `uv run gc`. |
| | | Plots from that table | See below | |
| 3 | Speedup | Plot per rerun | ✓ | `uv run speedup`. Values only, no CI possible. |
| | | Plot across reruns | ✓ | Line is a ratio of means, mean(strategy) / mean(base). Error bars resample whole reruns. The t interval is in the CSV. |
| 4 | 2A vs 2B comparison | Table | X | Plan: `add-2a-2b-comparison`. Ratio of means, paired Fieller intervals and a verdict per thread count. Research in [comparison_2a_2b.md](comparison_2a_2b.md). |

**Idea for later, not planned:** cost/latency graph. Cost is not measurable yet.

## Feature 2: the one table

One row per benchmark, strategy, thread count and rerun, plus the mean of reruns for each group.

| Metric | Ledger rows | In table | Plot | Plot needs a CI? |
|---|---|---|---|---|
| `gc_alloc_rate_norm` (B/op) | `thrpt` | ✓ | X | Yes |
| `gc_count` | `thrpt` | ✓ | X | Yes |
| `gc_time` (ms) | `thrpt` | ✓ | X | Yes |
| `gc_time_pct` | `thrpt` | ✓ | X | Yes |
| `pauses_max` (ms) | `thrpt` | ✓ | ✓ | No: one dot per rerun plus a median line |
| `pauses_p99` (ms) | `thrpt` | ✓ | X | Undecided whether it stays |
| `batch_bytes`, `batch_fill` | `thrpt` | ✓ | X | Yes |
| `stdev_agg_forks` | `thrpt` | X | X | Plot form not decided |
| Tail latency (`p50`, `p95`, `p99`) | `sample` | X | X | Plot form not decided |

Tail latency is the one metric from different rows: it comes from the separate `sample` measurement and is matched on benchmark, strategy, thread count and rerun. The supplied `docs/jmh_sample.csv` has no `sample` rows yet.

Which metrics finally appear as a plot or only in the table is still to be decided.

## Dropped

Listed as dropped in `research_questions.txt`: cleanups per 1M records, CV %, self-relative speedup and `comp_time_*`.
