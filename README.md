# Visualization

The new package lives in `src`. The original `blobplot.py` remains
an untouched reference; the new package does not import it.

Tests and their fixtures live in `tests`.

The plots and tables being built, with their status, are listed in
[`strategies/plots_and_tables.md`](strategies/plots_and_tables.md).

From the project root (the `visualization` directory):

```bash
uv sync
uv run help
uv run thrpt --help
uv run thrpt
uv run speedup
uv run gc
uv run compare
uv run pytest
```

`uv run help` lists all project commands in an ASCII table with `command` and
`description` columns. Each command's options appear underneath it; for example,
`--config PATH` selects a YAML configuration (default: `config.yaml`). This listing
prints every command and option in full, with a horizontal separator between
command groups. It does not read a configuration or ledger or generate plots.

Edit the `config.yaml` in the project root to choose the ledger, benchmarks, output directory,
image formats, strategy labels/order/colours, and plot presentation. To use another
configuration, run `uv run thrpt --config /path/to/config.yaml`.
Speedup accepts the same settings with `uv run speedup --config /path/to/config.yaml`;
no additional YAML settings are required. Its reference is fixed to `base` at one
BlobShuffle thread.
Paths inside YAML resolve relative to that file. Outside the project, use the
installed `thrpt` command (for example `/path/to/visualization/.venv/bin/thrpt`).

Each invocation saves into a new subdirectory under the configured output
directory and prints the resulting paths. Execution is headless by default.
The sample configuration points to illustrative data; it is not a thesis result.
The supplied `docs/jmh_sample.csv` contains 820 deliberately synthetic throughput rows:
two benchmarks, ten reruns, and nine strategies. Base runs at one thread; the other
strategies cover 1, 2, 4, 6, and 8 threads. Its original 70-column schema is retained.
Every row is marked `SYNTHETIC` in provenance fields. These invented values are
software test data, including their supplied bounds and auxiliary metrics; they
do not establish performance rankings or valid statistical confidence intervals.
All of `uv run thrpt`, `speedup`, `gc` and `compare` work with the supplied configuration and sample.
`docs/` is not in the repository (it is gitignored), so a clone has no sample ledger: set `input.path` in `config.yaml` to your own ledger.

## Ledger input

CSV and TSV/text input is supported, with automatic comma/tab detection or an
explicit delimiter in YAML. Excel is not supported yet. The unnamed first column
holds strategy identifiers; an unambiguous named `strategy` column also works.
Identifiers such as `rerun` are text: `01`, `10`, and `instance-A` are distinct.
No run count, timestamp format, or run-tag naming pattern is assumed.

Throughput requires `benchmark_short`, `mode`, `rerun`, `threads_blobshuffle`,
`score`, and `score_unit`, plus the strategy column. Standard decimal/scientific
numeric notation is accepted. Threads must be positive integral numbers, including
spellings such as `2.0`. Scores must be nonnegative finite numbers. Units `ops/s`,
`ops/ms`, `ops/us`, `ops/µs`, and `ops/μs` are converted to `ops/s` before the mean of reruns is taken.
Other metrics are left untouched, so `stdev_agg_forks`, `stdevs`, `stdev_p99_est` and `p0` to `p100` in the
per-rerun CSVs stay in the ledger's unit beside a `score` in ops/s. `score` in the GC tables and `mean_2a` and
`mean_2b` in the comparison table are ops/s. Blank confidence bounds omit the relevant error
bars with a warning; malformed nonblank values fail with row/column diagnostics.
`source_row` is the ledger line (header is line 1, blank lines count). A data row with more
fields than the header, such as a trailing comma, fails with its line number.

One observation is allowed per benchmark/strategy/thread/rerun. Duplicate rows
fail rather than overweight a rerun. Comparison-defining workload/JMH settings
must agree within each benchmark; a mismatch lists the offending fields. Numeric
settings compare numerically; warmup/measurement durations accept ns/us/µs/μs/ms/s/m/h
(a bare number means seconds). `-Dbench.threads` and `-Dbench.partitionBufferBytes`
in `jvm_args` differ by design and are ignored, but a thread flag that contradicts its row's
`threads_blobshuffle`, or a buffer flag that varies within a strategy, still fails. Revision, host, timestamp and run metadata can vary.
Different thread counts are never combined into one mean. Unknown strategy identifiers
remain visible with a warning. Labels, order and colour keys use canonical
strategy identifiers (trimmed lowercase, with a final `.0` removed), not labels.

## Output names

Each run writes to `out/YYYY-MM-DD_HH-MM-SS_<feature>/` (UTC, to the second), where the feature
is `thrpt`, `speedup`, `gc_pauses` or `compare` (`uv run gc` writes to `..._gc_pauses/`). If that folder
exists, the run waits for the next free second, so it never overwrites anything. All features name files
`<feature>_<benchmark>_<part>`:

| File | Contents |
|---|---|
| `thrpt_mb1_rawNoFinalize_r05.png` | Throughput, rerun 05 |
| `speedup_mb1_rawNoFinalize_avg.csv` | Speedup, summary across all reruns |
| `compare_mb1_rawNoFinalize_avg.csv` | 2A vs 2B comparison, one row per thread count |
| `gc_mb1_rawNoFinalize_r_all.csv` | GC metrics, one row per strategy, thread count and rerun |
| `gc_mb1_rawNoFinalize_gc_time_pivot.csv` | GC time, strategy by thread count |
| `gc_mb1_rawNoFinalize_gc_alloc_rate_norm.png` | gc_alloc_rate_norm, mean of reruns |

Unsafe characters in a benchmark or rerun name become underscores. If two names would then
match, ignoring case, the command stops with an error before writing anything.

## Throughput results

For each benchmark, the command writes one grouped bar chart and CSV per rerun,
plus a mean-of-reruns chart and CSV over **all** reruns present for that benchmark.
The x-axis is BlobShuffle threads, bars distinguish strategies, and the y-axis
uses ops/s. Counts are dynamic. There is no rerun selector, outlier removal,
median option, or weighting by fork count. Missing strategy/thread combinations
stay missing; incomplete coverage produces a warning and contributor counts.

Per-rerun error bars use `score_confidence_low` and `score_confidence_high` from
the ledger, JMH's 99.9% interval; a row without them gets `interval_kind` `none`. The across-reruns bar is the **mean of reruns**. Its error bars are a
95% percentile bootstrap of the rerun scores: 10,000 resamples of whole reruns,
with a fixed seed (42, combined with benchmark, strategy and threads) and the scores
taken sorted by rerun name, so repeat runs and a reordered ledger give identical values. Ledger bounds do not contribute. For scores 100 and
200 the resampled means are 100, 150 or 200, so the CI is [100, 200]. The CSV
also holds a 95% t interval on the same scores; the plot draws only the bootstrap
CI. With ten reruns the real coverage of the percentile bootstrap is about 90% on
normal data (our own calculation), not 95%, so the t interval is reported beside
it. The reruns share one instance, so the CI describes variation on this machine
in this period. A group with fewer than two reruns, or with no spread, gets no CI
and a warning (`No CI for <bench>/<strategy>/threads=<N>: ...`). If a ledger score
has no bounds, only its per-rerun bar loses error bars. Plots carry no n labels.
A missing rerun gives an `Incomplete coverage` warning, and the CSV keeps
`n_reruns` and `total_reruns`.

CSV fields include benchmark, canonical strategy, `threads_blobshuffle`, score,
bounds, `score_unit`, `interval_kind`, `n_reruns`, `total_reruns`, and JSON-encoded
`contributing_reruns`. Per-rerun CSVs retain input metadata, original score/unit
values and source file/row. Aggregate CSVs include source files, source rows and
original strategy identifiers so contributions can be traced to per-rerun CSVs.
Across-reruns CSVs hold `ci_low`, `ci_high`, `t_low`, `t_high`, `ci_method`,
`ci_resamples` and `ci_seed` instead of ledger bounds.
Do not interpret absent observations as zero or overlapping error bars as
significance tests. Plot and export values come from the same prepared result tables.

Validation errors exit nonzero and identify missing columns, invalid units,
duplicate observations, incompatible settings or missing requested benchmarks.
Fix the ledger or configuration and rerun; values are not silently discarded.

## Speedup results

`uv run speedup` writes one line chart and CSV per benchmark/rerun, plus a ratio-of-means
chart and CSV across all reruns. It pairs within the same benchmark and rerun:

```
speedup(strategy, N, rerun) = throughput(strategy, N, rerun) / throughput(base, 1, rerun)
```

Scores are normalized to ops/s before division; speedup is dimensionless.
The aggregate is a ratio of mean scores over the same reruns:

```
speedup(strategy, N) = mean(strategy score) / mean(base score at N=1)
```

The base mean uses only the reruns that contribute a strategy score for that
point. For base scores [100, 200] and strategy scores [200, 300], the aggregate
is 250 / 150 ≈ **1.667**. This differs from the mean of per-rerun ratios
([2, 1.5] gives 1.75), which is not used. All available reruns contribute; there
is no rerun filter or hardcoded run count. Missing numerator observations stay
missing, produce a coverage warning, and contribute no invented zero; their
base scores are left out of the base mean. Plots show no n labels; check coverage in the `Incomplete coverage` warning and the `n_reruns` and `total_reruns` columns.

Each benchmark/rerun must have exactly one positive, finite base score at one
thread. Missing, duplicate, zero, or negative baselines fail before output
creation. Zero strategy throughput remains a zero speedup. Base at one thread
has speedup one; base at other thread counts is retained if present. Rerun IDs
remain text. Speedup ignores ledger confidence-bound/error fields,
including missing or malformed values; throughput keeps its own validation.

Charts use numeric thread counts, one strategy line each, dashed ideal scaling
y=N, and a horizontal reference y=1. Missing points leave gaps. Configured
labels, order, and colors are consistent across a benchmark's outputs. The ideal
line is a scaling reference; the shape alone does not establish a cause such as
contention. Filename stems begin with `speedup_` and distinguish benchmark,
rerun, and ratio of means across reruns; each invocation gets a new directory.

Individual CSVs contain `benchmark_short`, `strategy`, `threads_blobshuffle`,
`rerun`, normalized numerator `score` and `score_unit`, `baseline_score` in ops/s,
`speedup`, `baseline_strategy`, `baseline_threads`, `source_row`,
`baseline_source_row`, source-file references, and original score/unit values.
Aggregate CSVs contain `speedup`, `mean_score` and `mean_baseline_score` (both
ops/s), `n_reruns`, `total_reruns`, JSON-encoded
`contributing_reruns`, numerator/baseline source-row lists, source files, and
`summary_kind`. Plots and exports use the same prepared tables.

Across-reruns speedup lines have the same 95% percentile bootstrap CI (10,000
resamples, fixed seed). Each resample picks whole contributing reruns, so every
strategy score stays with its base score from the same rerun, and the ratio of
means is taken. For base scores [100, 200] and strategy scores [200, 300], the
resampled ratios are only 2, 500/300 or 1.5, so the CI is [1.5, 2]. The CSV adds
`ci_low`, `ci_high`, `t_low`, `t_high`, `ci_method`, `ci_resamples`, `ci_seed`
and `t_basis`; the t interval uses the per-rerun ratios, a check on a slightly
different estimator. Real coverage is about 90% on normal data at ten reruns
(our own calculation), so the t interval is reported beside the bootstrap CI.
Groups with fewer than two reruns or no spread have no CI and a warning; base at
one thread is always ratio 1, with no CI and no warning. Ledger bounds never
contribute. Per-rerun speedup charts and CSVs stay values only.

This remains a descriptive visualization, without significance tests or
equivalence verdicts. Same-rerun pairing does not replace experimental
execution-order, warm-up, profiler-overhead, or drift checks.

## GC table

`uv run gc [--config PATH]` tabulates GC, pause and batch-fill metrics from the
`thrpt` rows of the requested benchmarks. It uses the same row selection and
checks as `thrpt` (benchmark filter, strategy keys, thread counts, duplicate
and settings-compatibility checks) and writes CSVs plus one figure per benchmark
(see below; the figure honours `output.formats`). Each invocation gets a new directory with, per
benchmark: `gc_<benchmark>_r_all.csv` (one row per strategy, thread count and
rerun, with every calculation input and the source row/file),
`gc_<benchmark>_avg.csv`, and `gc_<benchmark>_<metric>_pivot.csv` (strategy
by thread count, built from the means of reruns) for `gc_alloc_rate_norm`, `gc_count`,
`gc_time`, `gc_time_pct`, `pauses_max`, `pauses_p99` and `batch_fill`.

Taken from the ledger without rescaling: `gc_alloc_rate_norm` (B/op),
`gc_count`, `gc_time`, `pauses_max`, `pauses_p99`. The ledger carries no units
for the last three, so **milliseconds are assumed** (JMH profiler default).
`gc_time` and `gc_count` are totals over all measured forks and iterations.

```
gc_time_pct = gc_time / (forks * measurement_iterations * measurement_seconds * 1000) * 100
batch_bytes = aux_recordBytes / aux_batches
batch_fill  = batch_bytes / cfg_batchDesiredBytes
```

Example: `gc_time` 2500 with 5 forks, 5 iterations of `10 s` gives 1.0 %;
`gc_time` 50 with 1 fork, 1 iteration of `500 ms` gives 10.0 %.
`measurement_time` accepts the spellings of the settings check (`10 s`, `500ms`,
`1 m`; a bare number is seconds). `batch_fill` 1.0 is a full batch; larger
values are kept, never clamped. The aux units must be `bytes/<t>` and
`batches/<t>` with the same `<t>` (ns, us, ms, s; `µs` is read as `us`), so
the time unit cancels. Example: 1677.7216 bytes/ms over 0.0001 batches/ms with
`cfg_batchDesiredBytes` 16777216 gives `batch_bytes` 16777216, `batch_fill` 1.0.

Missing values: a missing required column, a malformed or negative value, a
blank `forks`, `measurement_iterations`, `measurement_time` or
`cfg_batchDesiredBytes`, or incompatible aux units fail before any output is
written. A blank `gc_alloc_rate_norm`, `gc_count`, `gc_time`, `pauses_max`,
`pauses_p99`, `aux_recordBytes` or `aux_batches` stays empty with a warning
naming the rows (a blank `gc_time` leaves `gc_time_pct` empty; a blank aux value
leaves `batch_bytes` and `batch_fill` empty). Zero `aux_batches` leaves
`batch_bytes` and `batch_fill` empty with a warning. `gc_time_pct` above 100 %
warns.

The mean of reruns is the unweighted arithmetic mean over all reruns of a benchmark,
strategy and thread count (`gc_<benchmark>_avg.csv`), with `n_reruns`, `total_reruns` and the contributing
reruns (incomplete coverage warns, as in `thrpt`). If any contributor lacks a
metric, that metric's mean is empty and a warning is printed. Note that the
mean `pauses_max` is the **mean of the per-rerun maxima**, not a maximum;
use the per-rerun CSV for the worst case. Only `gc_alloc_rate_norm` has a resampled CI,
in `gc_alloc_rate_norm_ci_low` and `gc_alloc_rate_norm_ci_high` (last two columns of the
`_avg` CSV): the 95% percentile bootstrap of the rerun values, 10,000 resamples, seeded per
benchmark, strategy and thread count as for throughput. A group with fewer than two reruns,
no spread, or a blank rerun value has blank CI values and a warning naming the group.
No other metric has a CI.

### pauses_max dot plot

Per benchmark, `gc` also writes `gc_<benchmark>_pauses_max.<fmt>` (one file per
format in `output.formats`) and `gc_<benchmark>_pauses_max_median.csv`. The plot
shows one dot per rerun (`pauses_max`, ms, log y-axis) for each strategy and
BlobShuffle thread count, with a black horizontal line at the median of the dots
in that group. Within a thread group, only strategies that have a plottable value
there take a slot, so the dots are centred with no empty slot for an absent
strategy; the median line spans the full slot. Y-axis tick labels are plain
numbers. The CSV holds those medians (`pauses_max_median`) with `n_reruns`,
`total_reruns` and `contributing_reruns`; the drawn line heights come from it.

Zero and blank `pauses_max` values cannot be shown on a log axis and are omitted
from the plot and the medians (zeros print a warning naming the ledger rows; blanks
already warn). A group without any positive value has no dots, no median line and
no CSV row. The median is of the per-rerun maxima, not a maximum.

### gc_alloc_rate_norm bar plot

Per benchmark, `gc` also writes `gc_<benchmark>_gc_alloc_rate_norm.<fmt>` (one file per
format in `output.formats`). Bars are the mean of reruns in B/op, raw, on a linear
y-axis from zero (no log scale). X-axis is BlobShuffle threads; strategy order, labels
and colours are the configured ones. Error bars are the resampled CI from the `_avg`
CSV and are drawn only where it is not blank; the title says so on a second line. A
strategy with no mean at a thread count has no bar there, and the strategies present
are centred in that group, as on the pauses_max plot. There are no per-rerun allocation plots.

## 2A vs 2B comparison

`uv run compare [--config PATH]` compares strategies 2A and 2B (they differ only in merge
order) per benchmark and thread count. It draws no plot and writes one table per benchmark,
`compare_<benchmark>_avg.csv`, into `..._compare/`, thread counts ascending.

For each rerun it takes y = ln(2a score / 2b score) from the same rerun. With n reruns, the mean
of y and the sample SD s give the paired log-t interval exp(mean y -/+ t * s / sqrt(n)), t with
n - 1 degrees of freedom. `logt_ratio` is exp(mean y); `ratio_of_means` is mean(2a) / mean(2b),
the same number the speedup plots use. With F thread counts in the benchmark, alpha = 0.05 / F:
the difference interval has level 1 - alpha and the equivalence interval 1 - 2 * alpha (99% and
98% for five thread counts). The two benchmarks are analysed separately. `smallest_provable_margin`
is the smallest margin for which the row would count as equivalent. The 95% bootstrap, SD of log
ratios (`sd_log_ratio_pct`, in percent) and `reruns_2a_gt_2b` are for reference and never decide.

The verdict is the first match of: `no spread observed` (the per-rerun ratios are identical apart
from rounding, so intervals and margin are empty); `equivalent` (equivalence interval inside
1/(1+m) to 1+m); `2A faster` (difference interval above 1); `2B faster` (below 1);
`inconclusive`. The margin m is `comparison.margin` in `config.yaml` (a fraction, 0.05 shipped,
reasoning in the comment there). Without it, `equivalent` is never issued and a warning says so.
Error rates are nominal: they assume independent reruns and roughly normal log ratios.

Every rerun seen for either strategy must exist for both at every thread count, with positive
scores and at least two reruns; otherwise the command fails naming benchmark, thread count and
rerun, and writes nothing. It warns when there are not 10 reruns. `flags` (separated by `;`)
never change numbers: `host_differs`, `sha_differs`, `dirty_build` (`git_dirty` not false, blank
included), `work_differs` (mean `aux_recordBytes` / original score differs by more than 0.1%),
`order_unbalanced` (counts of 2A-first and 2B-first differ by more than 2) and `order_unknown`
(a missing, unparsable or tied `run_timestamp`). `order_2a_first` and
`order_adjusted_ratio_geo` (exp of the intercept of y regressed on +1/-1 for 2A first/second)
are reported only. The sample ledger is synthetic and has no spread between 2A and 2B, so every
row there says `no spread observed`.
