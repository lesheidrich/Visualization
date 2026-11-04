"""Tail latency from `sample` rows: tables, percentile spectrum plot and percentile vs N plot."""
import json
import math

import numpy as np
import pandas as pd

from .ci import (BOOTSTRAP_RESAMPLES, CI_METHOD, bootstrap_ci, cell_seed, has_spread, interval_columns,
                 method_title, t_interval)
from .utils import (ALL_RERUNS_PART, AVERAGE_PART, PlainLogFormatter, file_names, load_ledger, numeric,
                    output_directory, output_stem, save_figure, sorted_for_csv, strategy_key, strategy_order,
                    validate_comparability, warn, warn_incomplete)


SPECTRUM = {"p50": 0.5, "p90": 0.9, "p95": 0.95, "p99": 0.99, "p99_9": 0.999, "p99_99": 0.9999}
PERCENTILES = (*SPECTRUM, "p100")
PANELS = ("p50", "p95", "p99")
TICK_LABELS = ("50", "90", "95", "99", "99.9", "99.99", "100")
# Minus log10 of (1 - fraction); 100 has no place on that scale, so it sits one step after 99.99.
X_POSITIONS = tuple(-math.log10(1 - fraction) for fraction in SPECTRUM.values()) + (5.0,)
UNIT = "ns/op"
UNIT_FACTORS = {"ns/op": 1, "us/op": 1_000, "ms/op": 1_000_000, "s/op": 1_000_000_000}
LINE_ALPHA = 1
BAND_ALPHA = 0.3
LIMIT_FACTOR = 1.25
REQUIRED = {"strategy", "benchmark_short", "mode", "rerun", "threads_blobshuffle", "score_unit", *PERCENTILES}


def _rows(frame, mask):
    return frame.loc[mask, "source_row"].tolist()


def normalize_sample(frame, config):
    """Select and check `sample` rows; percentiles come out in ns per op."""
    missing = REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"Missing ledger column(s): {sorted(missing)}")
    frame = frame.copy()
    if "source_row" not in frame:
        frame["source_row"] = np.arange(len(frame)) + 2
    for column in ("strategy", "benchmark_short", "mode", "rerun", "score_unit"):
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    frame = frame[frame["mode"].eq("sample") & frame["benchmark_short"].isin(config.benchmarks)].copy()
    if frame["strategy"].eq("").any():
        raise ValueError(f"Missing strategy at ledger row(s) {_rows(frame, frame['strategy'].eq(''))}")
    frame["strategy_original"] = frame["strategy"]
    frame["strategy"] = frame["strategy"].map(strategy_key)
    if config.exclude:  # configured exclusion: gone before any later check or warning
        frame = frame[~frame["strategy"].isin({strategy_key(key) for key in config.exclude})].copy()
    absent = set(config.benchmarks) - set(frame["benchmark_short"])
    if absent:
        raise ValueError(f"No sample results for benchmark(s): {sorted(absent)}")
    if frame["rerun"].eq("").any():
        raise ValueError(f"Missing rerun at ledger row(s) {_rows(frame, frame['rerun'].eq(''))}")
    unknown = sorted(set(frame["strategy"]) - set(config.labels))
    if unknown:
        warn(f"Unknown strategies retained: {unknown}")
    threads = numeric(frame, "threads_blobshuffle")
    invalid = (threads <= 0) | (threads % 1 != 0) | (threads >= 2**63)
    if invalid.any():
        raise ValueError("threads_blobshuffle must contain positive integers within int64 range "
                         f"at ledger row(s) {_rows(frame, invalid)}")
    frame["threads_blobshuffle"] = threads.astype("int64")
    factors = frame["score_unit"].str.replace("µ", "u").str.replace("μ", "u").map(UNIT_FACTORS)
    if factors.isna().any():
        bad = factors.isna()
        raise ValueError(f"Unsupported score_unit at ledger row(s) {_rows(frame, bad)}: "
                         f"{frame.loc[bad, 'score_unit'].tolist()}")
    for column in PERCENTILES:
        frame[column] = numeric(frame, column)
        negative = frame[column] < 0
        if negative.any():
            raise ValueError(f"{column} must be nonnegative at ledger row(s) {_rows(frame, negative)}")
    values = frame[list(PERCENTILES)].to_numpy(dtype=float)
    decreasing = pd.Series((np.diff(values, axis=1) < 0).any(axis=1), index=frame.index)
    if decreasing.any():
        warn(f"Percentiles decrease at ledger row(s) {_rows(frame, decreasing)}")
    frame["original_score_unit"] = frame["score_unit"]
    for column in PERCENTILES:
        frame[column] = frame[column] * factors
    frame["latency_unit"] = UNIT
    identity = ["benchmark_short", "strategy", "threads_blobshuffle", "rerun"]
    duplicated = frame.duplicated(identity, keep=False)
    if duplicated.any():
        raise ValueError(f"Duplicate sample observations at ledger row(s) {_rows(frame, duplicated)}")
    validate_comparability(frame)
    return frame


def prepare(frame, config):
    """Return per-rerun rows and, per group, the mean of reruns of each percentile with both CI methods."""
    observations = normalize_sample(frame, config)
    totals = observations.groupby("benchmark_short")["rerun"].nunique()
    keep = ["benchmark_short", "strategy", "strategy_original", "threads_blobshuffle", "rerun", *PERCENTILES,
            "latency_unit", "original_score_unit", "source_row"]
    if "source_file" in observations:
        keep.append("source_file")
    records = []
    keys = ["benchmark_short", "strategy", "threads_blobshuffle"]
    for (benchmark, strategy, threads), group in observations.groupby(keys, sort=False):
        count, total = len(group), int(totals[benchmark])
        if count < total:
            warn_incomplete(benchmark, strategy, threads, group,
                            observations.loc[observations["benchmark_short"].eq(benchmark), "rerun"], mode="sample")
        ordered = group.sort_values("rerun", kind="stable")  # CI only: independent of ledger row order
        record = {"benchmark_short": benchmark, "strategy": strategy, "threads_blobshuffle": threads,
                  "n_reruns": count, "total_reruns": total}
        without = []
        for column in PERCENTILES:
            values = ordered[column].to_numpy(dtype=float)
            record[column] = values.mean()
            t_low = t_high = ci_low = ci_high = np.nan
            if has_spread(values):
                t_low, t_high = t_interval(values)
                ci_low, ci_high = bootstrap_ci(values, seed=cell_seed(benchmark, strategy, threads, f"latency_{column}"))
            else:
                without.append(column)
            record.update({f"{column}_t_low": t_low, f"{column}_t_high": t_high,
                           f"{column}_ci_low": ci_low, f"{column}_ci_high": ci_high})
        if without:
            warn(f"No latency CI for {benchmark}/{strategy}/threads={threads} at {', '.join(without)}: "
                 "fewer than two reruns or no spread")
        record.update({
            "latency_unit": UNIT, "summary_kind": "mean of reruns", "ci_method": CI_METHOD,
            "ci_resamples": BOOTSTRAP_RESAMPLES,
            "contributing_reruns": json.dumps(group["rerun"].tolist()),
            "source_rows": json.dumps(group["source_row"].astype(int).tolist()),
            "source_files": json.dumps(group["source_file"].unique().tolist()) if "source_file" in group else "[]",
            "original_strategies": json.dumps(group["strategy_original"].unique().tolist()),
        })
        records.append(record)
    return observations[keep], pd.DataFrame.from_records(records)


def axis_limits(table, columns, config):
    """(bottom, top) of a log axis from the drawn means and the chosen method's upper ends."""
    means = table[list(columns)].to_numpy(dtype=float)
    means = means[means > 0]
    highs = table[[interval_columns(config.ci_method, c)[1] for c in columns]].to_numpy(dtype=float)
    highs = highs[np.isfinite(highs)]
    if not len(means):
        return 1.0, 10.0
    return float(means.min()) / LIMIT_FACTOR, float(max(means.max(), *highs, 0) if len(highs) else means.max()) * LIMIT_FACTOR


def warn_unplottable(averages, benchmark, config):
    """Plot step: one warning per group and percentile the log axis cannot show as it is."""
    for _, row in averages.iterrows():
        name = f"{benchmark}/{row['strategy']}/threads={row['threads_blobshuffle']}"
        for column in PERCENTILES:
            low = row[interval_columns(config.ci_method, column)[0]]
            if row[column] <= 0:
                warn(f"Zero {column} mean left out of the log plot for {name}")
            elif pd.notna(low) and low <= 0:
                warn(f"Latency band low end at or below zero for {name} at {column}: "
                     "drawn at the axis bottom, the table keeps the true value")


def _series(data, columns, method, bottom):
    """Means (blank when not positive) and band ends (lows raised to the axis bottom), in `columns` order."""
    means = data[list(columns)].to_numpy(dtype=float)
    means = np.where(means > 0, means, np.nan)
    lows = data[[interval_columns(method, c)[0] for c in columns]].to_numpy(dtype=float)
    highs = data[[interval_columns(method, c)[1] for c in columns]].to_numpy(dtype=float)
    return means, np.where(np.isnan(lows), np.nan, np.maximum(lows, bottom)), highs


def _colour(config, strategy, position, palette):
    return config.colors.get(strategy, palette(position % 10))


def _finish(fig, axes, config, strategies, palette, present, title, drawn, ylabel_axis):
    from matplotlib.lines import Line2D

    for ax in axes:
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(PlainLogFormatter(labelOnlyBase=False))
        ax.yaxis.set_minor_formatter(PlainLogFormatter(labelOnlyBase=False, minor_thresholds=(2, 0.4)))
        ax.grid(color="#ddd", zorder=0)
        ax.spines[["top", "right"]].set_visible(False)
    ylabel_axis.set_ylabel("Latency (ns per op, log scale)")
    fig.suptitle(f"{title}\nBands: {method_title(config.ci_method)}" if drawn else title)
    handles = [Line2D([], [], color=_colour(config, s, k, palette), marker="o", markersize=4, linewidth=1.8,
                      label=config.labels.get(s, s))
               for k, s in enumerate(strategies) if s in present]
    fig.tight_layout()
    fig.legend(handles=handles, loc="center left", bbox_to_anchor=(1.0, 0.5))
    return fig


def render_spectrum(averages, config, benchmark, *, strategies, threads):
    """One panel per thread count: x = percentile on a stretched tail axis, one line per strategy."""
    import matplotlib

    if not config.show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    width, height = config.figsize
    fig, axes = plt.subplots(1, len(threads), sharey=True, squeeze=False,
                             figsize=(max(width, 3.2 * len(threads)), height))
    axes = axes[0]
    palette = matplotlib.colormaps["tab10"]
    bottom, top = axis_limits(averages, PERCENTILES, config)
    drawn = False
    present = set()
    for ax, n in zip(axes, threads):
        for k, strategy in enumerate(strategies):
            data = averages[averages["strategy"].eq(strategy) & averages["threads_blobshuffle"].eq(n)]
            if data.empty:
                continue
            means, lows, highs = _series(data, PERCENTILES, config.ci_method, bottom)
            colour = _colour(config, strategy, k, palette)
            present.add(strategy)
            ax.plot(X_POSITIONS, means[0], marker="o", markersize=4, linewidth=1.8, color=colour, alpha=LINE_ALPHA,
                    zorder=3)
            if np.isfinite(lows).any():
                ax.fill_between(X_POSITIONS, lows[0], highs[0], color=colour, alpha=BAND_ALPHA, linewidth=0, zorder=2)
                drawn = True
        ax.set_title(f"N = {n}")
        ax.set_xticks(X_POSITIONS, TICK_LABELS, rotation=45, fontsize=8)
        ax.set_xlabel("Percentile")
    axes[0].set_ylim(bottom, top)
    title = f"{benchmark} — latency percentile spectrum, mean of reruns"
    return _finish(fig, axes, config, strategies, palette, present, title, drawn, axes[0])


def render_percentiles(averages, config, benchmark, *, strategies, threads):
    """Panels p50, p95 and p99: x = BlobShuffle threads; one thread count draws a marker and error bar."""
    import matplotlib

    if not config.show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(PANELS), sharey=True, squeeze=False, figsize=config.figsize)
    axes = axes[0]
    palette = matplotlib.colormaps["tab10"]
    bottom, top = axis_limits(averages, PANELS, config)
    drawn = False
    present = set()
    for ax, column in zip(axes, PANELS):
        for k, strategy in enumerate(strategies):
            data = averages[averages["strategy"].eq(strategy)].sort_values("threads_blobshuffle")
            if data.empty:
                continue
            means, lows, highs = _series(data, (column,), config.ci_method, bottom)
            means, lows, highs = means[:, 0], lows[:, 0], highs[:, 0]
            x = data["threads_blobshuffle"].to_numpy()
            colour = _colour(config, strategy, k, palette)
            present.add(strategy)
            if len(x) == 1:
                error = None
                if np.isfinite(lows[0]):
                    error = [[max(means[0] - lows[0], 0)], [max(highs[0] - means[0], 0)]]
                    drawn = True
                ax.errorbar(x, means, yerr=error, fmt="o", markersize=5, color=colour, capsize=4, zorder=3)
            else:
                ax.plot(x, means, marker="o", markersize=4, linewidth=1.8, color=colour, alpha=LINE_ALPHA, zorder=3)
                if np.isfinite(lows).any():
                    ax.fill_between(x, lows, highs, color=colour, alpha=BAND_ALPHA, linewidth=0, zorder=2)
                    drawn = True
        ax.set_title(column)
        ax.set_xticks(threads)
        ax.set_xlabel("BlobShuffle threads")
    axes[0].set_ylim(bottom, top)
    title = f"{benchmark} — latency percentiles, mean of reruns"
    return _finish(fig, axes, config, strategies, palette, present, title, drawn, axes[0])


def run(config):
    """Validate all requested results before creating output files."""
    observations, averages = prepare(load_ledger(config.ledger, config.delimiter), config)
    benchmark_names = file_names(config.benchmarks, "benchmark")
    directory = output_directory(config.output, "latency")
    paths = []
    import matplotlib.pyplot as plt

    for benchmark in config.benchmarks:
        name = benchmark_names[benchmark]
        data = observations[observations["benchmark_short"].eq(benchmark)]
        summary = averages[averages["benchmark_short"].eq(benchmark)]
        for part, table in ((ALL_RERUNS_PART, data), (AVERAGE_PART, summary)):
            path = directory / f"{output_stem('latency', name, part)}.csv"
            sorted_for_csv(table, config).to_csv(path, index=False)
            paths.append(path)
        warn_unplottable(summary, benchmark, config)
        strategies = strategy_order(data, config)
        threads = sorted(int(n) for n in data["threads_blobshuffle"].unique())
        for part, render in (("spectrum", render_spectrum), ("percentiles", render_percentiles)):
            fig = render(summary, config, benchmark, strategies=strategies, threads=threads)
            try:
                paths.extend(save_figure(fig, directory / output_stem("latency", name, part), config))
                if config.show:
                    plt.show()
            finally:
                plt.close(fig)
    return paths
