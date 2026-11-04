# Plots and tables

What we are building and where each piece stands. There are five features, and all the GC-style metrics live in one table.

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
| t interval of reruns | The 95% interval mean -/+ t * s / sqrt(n) of the rerun values. This is the error bar on throughput, `gc_alloc_rate_norm`, `gc_time_pct` and `batch_fill` (mb2 only) mean-of-reruns plots with `ci.method: t`, the default (`add-ci-module`). |
| Paired Fieller interval | The t-based interval for a ratio of means that keeps each strategy score with its base score from the same rerun. This is the error bar on speedup mean-of-reruns plots. |
| Resampled CI | A 95% percentile bootstrap of the rerun values. It is always in the tables, and those same plots draw it with `ci.method: resampled`. |

The resampling method is documented in [resampling.md](resampling.md), and the thesis changes it needs in [notes_for_thesis.md](notes_for_thesis.md).

We never average CI bounds.

## Features

| # | Feature | Part | Status | Notes |
|---|---|---|---|---|
| 1 | Throughput | Plot per rerun | ✓ | `uv run thrpt`. Error bars are the ledger CI of that rerun. |
| | | Plot across reruns | ✓ | Bars are the mean of reruns. Error bars follow `ci.method`: the 95% t interval (`t_low`, `t_high`, default) or the resampled CI (`ci_low`, `ci_high`). The CSV holds both. |
| 2 | GC and co | One table | See below | `uv run gc`. |
| | | Plots from that table | See below | |
| 3 | Speedup | Plot per rerun | ✓ | `uv run speedup`. Values only, no CI possible. |
| | | Plot across reruns | ✓ | Line is a ratio of means, mean(strategy) / mean(base). Error bars are the 95% paired Fieller interval (`fieller_low`, `fieller_high`). The resampled CI (`ci_low`, `ci_high`) and the t interval on the per-rerun ratios (`t_low`, `t_high`) are in the CSV for checks, not drawn. |
| 4 | 2A vs 2B comparison | Table | ✓ | Done: `uv run compare` (`add-2a-2b-comparison`). Paired log-t intervals at 99% and 98% (five thread counts), the smallest provable margin and a verdict per thread count. Research in [comparison_2a_2b.md](comparison_2a_2b.md). |
| | | Plot of the ratio | ✓ | Done: `uv run compare` also saves `compare_<benchmark>_ratio` (`add-compare-plot`): per thread count a dot at `logt_ratio`, a thick bar for the equivalence interval, a thin bar for the difference interval, and lines at 1 and at the margin. Drawn from the same table; the CSV is unchanged. |

| 5 | Tail latency | Two tables | ✓ | `uv run latency` (`add-tail-latency`). Per rerun, and mean of reruns with both CI methods, from `sample` rows. |
| | | Two plots | ✓ | Percentile spectrum per thread count, and `p50`, `p95`, `p99` against threads. Log axis, line alpha 1, bands of `ci.method` at alpha 0.3. |

**Excluded strategy:** on 2026-11-03 the owner said "we're removing 2_0 from visualization". S2_0 (ledger key `2null`) is set in `config.yaml` as `strategies.exclude: [2null]`, so none of the four commands draws or tabulates it. `total_reruns` then counts only reruns of the kept strategies, so a rerun or thread count held only by S2_0 disappears; neither happens in `docs/raw3.csv`. The ledger keeps its rows, and the other strategies keep their values.

**Idea for later, not planned:** cost/latency graph. Cost is not measurable yet.

## Feature 2: the one table

One row per benchmark, strategy, thread count and rerun, plus the mean of reruns for each group.

| Metric | Ledger rows | In table | Plot | Plot needs a CI? |
|---|---|---|---|---|
| `gc_alloc_rate_norm` (B/op) | `thrpt` | ✓ | ✓ | Yes: `add-gc-alloc-plot`. Bars are the mean of reruns, raw values, linear axis from zero, no log axis. Error bars are the 95% t interval, in the `_avg` CSV as `gc_alloc_rate_norm_t_low` and `_t_high`. The resampled CI stays there as `gc_alloc_rate_norm_ci_low` and `_ci_high`, for checks, not drawn. |
| `gc_count` | `thrpt` | ✓ | X | Yes |
| `gc_time` (ms) | `thrpt` | ✓ | X | Table only. The plot is `gc_time_pct`. |
| `gc_time_pct` | `thrpt` | ✓ | ✓ | Yes: `add-gc-time-plot`. Bars are the mean of reruns on a linear axis from zero. Error bars are the 95% t interval, in the `_avg` CSV as `gc_time_pct_t_low` and `_t_high`. The axis top is 1.1 times the highest drawn value, rounded up to the next 1, 2 or 5 times a power of ten (0 gives 1). |
| `pauses_max` (ms) | `thrpt` | ✓ | ✓ | No: one dot per rerun plus a median line |
| `pauses_p99` (ms) | `thrpt` | ✓ | X | Owner decision: stays in the GC table, not plotted |
| `batch_fill` | `thrpt` | ✓ | ✓ | Yes: `add-batch-fill-plot`, `mb2_rawFinalize` only. Bars are the mean of reruns on a linear axis from zero, with its top at the larger of 1 and the automatic top. Error bars are the 95% t interval, in the `_avg` CSV as `batch_fill_t_low` and `_t_high`. |
| `batch_bytes` | `thrpt` | ✓ | X | Yes |
| `stdev_agg_forks` | `thrpt` | X | X | Plot form not decided |
| Tail latency (`p50`, `p90`, `p95`, `p99`, `p99_9`, `p99_99`, `p100`) | `sample` | X | ✓ | Yes: `add-tail-latency`, `uv run latency`, its own tables and two plots: percentile spectrum per thread count, and `p50`, `p95`, `p99` against threads. Mean of reruns, log axis, bands of `ci.method` at alpha 0.3. |

Tail latency is the one metric from different rows: it comes from the separate `sample` measurement. It gets its own table and plot and is never joined onto `thrpt` rows. The supplied `docs/jmh_sample.csv` has no `sample` rows; `docs/raw3.csv` has them for 3 reruns.

Which metrics finally appear as a plot or only in the table is still to be decided.

## Dropped

Listed as dropped in `research_questions.txt`: cleanups per 1M records, CV %, self-relative speedup and `comp_time_*`.
