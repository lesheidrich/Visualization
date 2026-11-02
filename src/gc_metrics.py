"""GC, pause and batch-fill table, pauses_max dot plot and gc_alloc_rate_norm bar plot: preparation, averages, pivots, CSV and figure export."""
import json
import re

import numpy as np
import pandas as pd
from matplotlib.ticker import LogFormatter

from .utils import (ALL_RERUNS_PART, AVERAGE_PART, CI_METHOD, bootstrap_ci,
                    cell_seed, duration_seconds, file_names, has_spread, load_ledger,
                    normalize_throughput, numeric, output_directory, output_stem, save_figure,
                    sorted_for_csv, strategy_order, warn, warn_incomplete)

PIVOT_METRICS = ("gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct",
                 "pauses_max", "pauses_p99", "batch_fill")
AVERAGED = ("score",) + PIVOT_METRICS[:-1] + ("batch_bytes", "batch_fill")

LEDGER_METRICS = ("gc_alloc_rate_norm", "gc_count", "gc_time", "pauses_max", "pauses_p99",
                  "aux_recordBytes", "aux_batches")
SETTINGS = ("forks", "measurement_iterations", "measurement_time", "cfg_batchDesiredBytes")
REQUIRED = LEDGER_METRICS[:5] + SETTINGS[:3] + (
    "aux_recordBytes", "aux_recordBytes_unit", "aux_batches", "aux_batches_unit",
    "cfg_batchDesiredBytes")
LEADING = ("benchmark_short", "strategy", "strategy_original", "threads_blobshuffle", "rerun",
           "score", "score_unit", "gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct",
           "forks", "measurement_iterations", "measurement_time", "measurement_seconds",
           "pauses_max", "pauses_p99", "aux_recordBytes", "aux_recordBytes_unit",
           "aux_batches", "aux_batches_unit", "cfg_batchDesiredBytes", "batch_bytes",
           "batch_fill", "n_reruns", "total_reruns", "contributing_reruns", "source_row",
           "source_file")
# Missing-value consequence appended to the "Blank <col>" warning.
BLANK_EFFECT = {"gc_time": "; gc_time_pct missing",
                "aux_recordBytes": "; batch_bytes and batch_fill missing",
                "aux_batches": "; batch_bytes and batch_fill missing"}
ALLOC = "gc_alloc_rate_norm"
ALLOC_CI = (f"{ALLOC}_ci_low", f"{ALLOC}_ci_high")
AUX_UNITS = (re.compile(r"bytes/(ns|us|ms|s)"), re.compile(r"batches/(ns|us|ms|s)"))


def _rows(frame, mask):
    return frame.loc[mask, "source_row"].astype(int).tolist()


def _text(frame, column):
    return frame[column].fillna("").astype(str).str.strip()


def _parse_metrics(frame):
    for column in LEDGER_METRICS:
        frame[column] = frame[column].fillna("")
        frame[column] = numeric(frame, column, optional=True)
        negative = frame[column] < 0
        if negative.any():
            raise ValueError(f"{column} must be nonnegative at ledger row(s) {_rows(frame, negative)}")
        blank = frame[column].isna()
        if blank.any():
            warn(f"Blank {column} at ledger row(s) {_rows(frame, blank)}{BLANK_EFFECT.get(column, '')}")


def _parse_settings(frame):
    for column in SETTINGS:
        blank = _text(frame, column).eq("")
        if blank.any():
            raise ValueError(f"Blank {column} at ledger row(s) {_rows(frame, blank)}")
    for column in SETTINGS:
        if column == "measurement_time":
            seconds = []
            for row, text in zip(frame["source_row"], _text(frame, column)):
                try:
                    seconds.append(duration_seconds(text))
                except ValueError:
                    raise ValueError(f"Malformed {column} at ledger row(s) [{int(row)}]: {text}") from None
            frame["measurement_seconds"] = seconds
            values = frame["measurement_seconds"]
        else:
            frame[column] = values = numeric(frame, column)
        if (values <= 0).any():
            raise ValueError(f"{column} must be positive at ledger row(s) {_rows(frame, values <= 0)}")


def _check_aux_units(frame):
    both = frame["aux_recordBytes"].notna() & frame["aux_batches"].notna()
    clean = {c: _text(frame, c).str.replace("µ", "u").str.replace("μ", "u").str.replace(r"\s+", "", regex=True)
             for c in ("aux_recordBytes_unit", "aux_batches_unit")}
    def incompatible(index):
        matches = [pattern.fullmatch(clean[c][index]) for pattern, c in zip(AUX_UNITS, clean)]
        return not all(matches) or matches[0][1] != matches[1][1]

    mismatch = pd.Series([bool(both[i]) and incompatible(i) for i in frame.index],
                         index=frame.index, dtype=bool)
    if mismatch.any():
        units = list(zip(frame.loc[mismatch, "aux_recordBytes_unit"], frame.loc[mismatch, "aux_batches_unit"]))
        raise ValueError(f"Incompatible aux units at ledger row(s) {_rows(frame, mismatch)}: {units}")


def prepare(frame, config):
    """Return per-rerun observations and all-rerun averages of every metric."""
    missing = set(REQUIRED) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing ledger column(s): {sorted(missing)}")
    observations = normalize_throughput(frame, config.benchmarks, config.labels,
                                        include_bounds=False)
    # Left in the ledger's unit, so they don't belong beside a score in ops/s.
    observations = observations.drop(columns=[c for c in ("score_error", "score_confidence_low", "score_confidence_high")
                                              if c in observations])
    _parse_metrics(observations)
    _parse_settings(observations)
    _check_aux_units(observations)

    observations["gc_time_pct"] = observations["gc_time"] / (
        observations["forks"] * observations["measurement_iterations"]
        * observations["measurement_seconds"] * 1000) * 100
    over = observations["gc_time_pct"] > 100
    if over.any():
        warn(f"gc_time_pct above 100 % at ledger row(s) {_rows(observations, over)}")
    zero = observations["aux_batches"].eq(0)
    all_zero = {b for b, z in zero.groupby(observations["benchmark_short"]) if z.all()}
    # NoFinalize benchmarks close no batch by design, so all zeros say nothing is wrong there.
    expected = {b for b in all_zero if "NoFinalize" in b}
    for benchmark in sorted(all_zero - expected):
        warn(f"{benchmark}: aux_batches is 0 on every row; batch_bytes and batch_fill are blank")
    partial = zero & ~observations["benchmark_short"].isin(all_zero)
    if partial.any():
        warn(f"Zero aux_batches at ledger row(s) {_rows(observations, partial)}; "
             "batch_bytes and batch_fill missing")
    with np.errstate(divide="ignore", invalid="ignore"):
        observations["batch_bytes"] = (observations["aux_recordBytes"]
                                       / observations["aux_batches"].mask(zero))
    observations["batch_fill"] = observations["batch_bytes"] / observations["cfg_batchDesiredBytes"]

    totals = observations.groupby("benchmark_short")["rerun"].nunique()
    observations["n_reruns"] = 1
    observations["total_reruns"] = observations["benchmark_short"].map(totals)
    observations["contributing_reruns"] = observations["rerun"].map(lambda r: json.dumps([r]))
    leading = [c for c in LEADING if c in observations]
    observations = observations[leading + [c for c in observations if c not in leading]]

    records = []
    keys = ["benchmark_short", "strategy", "threads_blobshuffle"]
    for (benchmark, strategy, threads), group in observations.groupby(keys, sort=False):
        count, total = len(group), int(totals[benchmark])
        name = f"{benchmark}/{strategy}/threads={threads}"
        if count < total:
            warn_incomplete(benchmark, strategy, threads, group,
                            observations.loc[observations["benchmark_short"].eq(benchmark), "rerun"])
        record = {"benchmark_short": benchmark, "strategy": strategy, "threads_blobshuffle": threads}
        for metric in AVERAGED:
            if group[metric].notna().all():
                record[metric] = group[metric].mean()
            elif benchmark in all_zero and metric in ("batch_bytes", "batch_fill"):
                record[metric] = np.nan
            else:
                record[metric] = np.nan
                warn(f"Mean of reruns of {metric} missing for {name}: a contributor lacks {metric}")
        record.update({
            "n_reruns": count, "total_reruns": total,
            "contributing_reruns": json.dumps(group["rerun"].tolist()),
            "source_rows": json.dumps(group["source_row"].astype(int).tolist()),
            "source_files": json.dumps(group["source_file"].unique().tolist()) if "source_file" in group else "[]",
            "original_strategies": json.dumps(group["strategy_original"].unique().tolist()),
            "summary_kind": "mean of reruns",
        })
        record[ALLOC_CI[0]], record[ALLOC_CI[1]] = _alloc_ci(group, name, benchmark, strategy, threads)
        records.append(record)
    return observations, pd.DataFrame.from_records(records)


def _alloc_ci(group, name, benchmark, strategy, threads):
    """Resampled CI of one group's per-rerun gc_alloc_rate_norm, or (nan, nan) with a warning."""
    values = group.sort_values("rerun", kind="stable")[ALLOC].to_numpy(dtype=float)  # CI only
    if np.isnan(values).any():
        warn(f"No {ALLOC} CI for {name}: a contributor lacks {ALLOC}")
    elif not has_spread(values):
        warn(f"No {ALLOC} CI for {name}: fewer than two reruns or no spread")
    else:
        return bootstrap_ci(values, seed=cell_seed(benchmark, strategy, threads, ALLOC))
    return np.nan, np.nan


def pivots(averages, config):
    """benchmark -> metric -> strategy x threads table of averages."""
    result = {}
    for benchmark, data in averages.groupby("benchmark_short", sort=False):
        order = strategy_order(data, config)
        threads = sorted(data["threads_blobshuffle"].unique())
        result[benchmark] = {}
        for metric in PIVOT_METRICS:
            table = data.pivot(index="strategy", columns="threads_blobshuffle", values=metric)
            table = table.reindex(index=order, columns=threads).astype(float)
            table.columns.name = None
            result[benchmark][metric] = table
    return result


def pause_medians(observations):
    """Median pauses_max per benchmark, strategy and threads over plottable reruns."""
    zero = observations["pauses_max"].eq(0)
    if zero.any():
        warn("Non-positive pauses_max omitted from log plot at ledger row(s) "
             f"{_rows(observations, zero)}")
    plottable = observations[observations["pauses_max"] > 0]
    totals = observations.groupby("benchmark_short")["total_reruns"].first()
    records = []
    keys = ["benchmark_short", "strategy", "threads_blobshuffle"]
    for (benchmark, strategy, threads), group in plottable.groupby(keys, sort=False):
        records.append({
            "benchmark_short": benchmark, "strategy": strategy, "threads_blobshuffle": threads,
            "pauses_max_median": float(np.median(group["pauses_max"])),
            "n_reruns": len(group), "total_reruns": int(totals[benchmark]),
            "contributing_reruns": json.dumps(group["rerun"].tolist()),
        })
    return pd.DataFrame.from_records(records, columns=[
        "benchmark_short", "strategy", "threads_blobshuffle", "pauses_max_median",
        "n_reruns", "total_reruns", "contributing_reruns"])


def _compact_slots(strategies, threads, present, width):
    """x position of each (strategy, threads) slot: only strategies in `present` take a slot,
    centred on the thread group."""
    slots = {}
    for i, n in enumerate(threads):
        here = [s for s in strategies if present(s, n)]
        for j, s in enumerate(here):
            slots[(s, n)] = i + (j - (len(here) - 1) / 2) * width
    return slots


class _PlainLogFormatter(LogFormatter):
    """Log tick labels as plain numbers (no scientific or mathtext notation)."""

    def _num_to_string(self, x, vmin, vmax):
        return f"{x:g}"


def render_pauses(observations, medians, config, title, *, strategies, threads):
    """One dot per rerun with a median line per strategy and thread count."""
    import matplotlib

    if not config.show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    fig, ax = plt.subplots(figsize=config.figsize)
    count = len(strategies)
    width = 0.8 / count
    palette = matplotlib.colormaps["tab10"]
    plottable = observations[observations["pauses_max"] > 0]
    # Compact slots: per thread group only strategies with plottable values take a slot.
    slots = _compact_slots(
        strategies, threads,
        lambda s, n: (plottable["strategy"].eq(s) & plottable["threads_blobshuffle"].eq(n)).any(),
        width)
    handles = []
    for k, strategy in enumerate(strategies):
        data = plottable[plottable["strategy"].eq(strategy)]
        if data.empty:
            continue
        colour = config.colors.get(strategy, palette(k % 10))
        xs = [slots[(strategy, n)] for n in data["threads_blobshuffle"]]
        handles.append(ax.scatter(xs, data["pauses_max"], color=colour, alpha=0.7, zorder=3,
                                  label=config.labels.get(strategy, strategy)))
        line = medians[medians["strategy"].eq(strategy)].sort_values("threads_blobshuffle")
        x = np.array([slots[(strategy, n)] for n in line["threads_blobshuffle"]])
        ax.hlines(line["pauses_max_median"], x - 0.5 * width, x + 0.5 * width,
                  colors="black", linewidth=2, zorder=4)
    handles.append(Line2D([], [], color="black", linewidth=2,
                          label="median (per strategy and threads)"))
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(_PlainLogFormatter(labelOnlyBase=False))
    ax.yaxis.set_minor_formatter(_PlainLogFormatter(labelOnlyBase=False, minor_thresholds=(2, 0.4)))
    ax.set_xticks(np.arange(len(threads)), threads)
    ax.set_xlabel("BlobShuffle threads")
    ax.set_ylabel("pauses_max (ms, log scale)")
    ax.set_title(title)
    ax.grid(axis="y", color="#ddd", zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(handles=handles, bbox_to_anchor=(1.02, 1), loc="upper left")
    fig.tight_layout()
    return fig


def render_alloc(averages, config, benchmark, *, strategies, threads):
    """Bars of the mean of reruns of gc_alloc_rate_norm (B/op) with resampled CI error bars."""
    import matplotlib

    if not config.show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=config.figsize)
    width = 0.8 / len(strategies)
    palette = matplotlib.colormaps["tab10"]
    known = averages[averages[ALLOC].notna()]
    slots = _compact_slots(
        strategies, threads,
        lambda s, n: (known["strategy"].eq(s) & known["threads_blobshuffle"].eq(n)).any(), width)
    bars = 0
    for k, strategy in enumerate(strategies):
        data = known[known["strategy"].eq(strategy)].sort_values("threads_blobshuffle")
        if data.empty:
            continue
        x = np.array([slots[(strategy, n)] for n in data["threads_blobshuffle"]])
        ax.bar(x, data[ALLOC], width=width, color=config.colors.get(strategy, palette(k % 10)),
               edgecolor="white", label=config.labels.get(strategy, strategy), zorder=2)
        complete = (data[ALLOC_CI[0]].notna() & data[ALLOC_CI[1]].notna()).to_numpy()
        if complete.any():
            valid = data[complete]
            err = np.vstack([valid[ALLOC] - valid[ALLOC_CI[0]], valid[ALLOC_CI[1]] - valid[ALLOC]])
            ax.errorbar(x[complete], valid[ALLOC], yerr=err, fmt="none", color="#444",
                        elinewidth=1.5, capsize=4, zorder=3)
            bars += int(complete.sum())
    ax.set_xticks(np.arange(len(threads)), threads)
    ax.set_xlabel("BlobShuffle threads")
    ax.set_ylabel(f"{ALLOC} (B/op)")
    title = f"{benchmark} \u2014 {ALLOC}, mean of reruns"
    ax.set_title(f"{title}\nError bars: {CI_METHOD}" if bars else title)
    ax.margins(y=0.05)
    ax.set_yscale("linear")
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", color="#ddd", zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    if ax.patches:
        ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
    fig.tight_layout()
    return fig


def run(config):
    """Validate all requested results before creating output files."""
    observations, averages = prepare(load_ledger(config.ledger, config.delimiter), config)
    tables = pivots(averages, config)
    medians = pause_medians(observations)
    benchmark_names = file_names(config.benchmarks, "benchmark")
    directory = output_directory(config.output, "gc_pauses")
    paths = []
    for benchmark in config.benchmarks:
        name = benchmark_names[benchmark]
        stem = lambda part, name=name: output_stem("gc", name, part)
        outputs = [
            (f"{stem(ALL_RERUNS_PART)}.csv", observations[observations["benchmark_short"].eq(benchmark)], False),
            (f"{stem(AVERAGE_PART)}.csv", averages[averages["benchmark_short"].eq(benchmark)], False),
        ] + [(f"{stem(f'{metric}_pivot')}.csv", tables[benchmark][metric], True)
             for metric in PIVOT_METRICS]
        data = observations[observations["benchmark_short"].eq(benchmark)]
        benchmark_medians = medians[medians["benchmark_short"].eq(benchmark)]
        outputs.append((f"{stem('pauses_max_median')}.csv", benchmark_medians, False))
        for filename, table, index in outputs:
            (table if index else sorted_for_csv(table, config)).to_csv(directory / filename, index=index)
            paths.append(directory / filename)
        fig = render_pauses(data, benchmark_medians, config,
                            f"{benchmark} \u2014 pauses_max, one dot per rerun",
                            strategies=strategy_order(data, config),
                            threads=sorted(int(n) for n in data["threads_blobshuffle"].unique()))
        import matplotlib.pyplot as plt

        try:
            paths.extend(save_figure(fig, directory / stem("pauses_max"), config))
            if config.show:
                plt.show()
        finally:
            plt.close(fig)
        fig = render_alloc(averages[averages["benchmark_short"].eq(benchmark)], config, benchmark,
                           strategies=strategy_order(data, config),
                           threads=sorted(int(n) for n in data["threads_blobshuffle"].unique()))
        try:
            paths.extend(save_figure(fig, directory / stem(ALLOC), config))
            if config.show:
                plt.show()
        finally:
            plt.close(fig)
    return paths
