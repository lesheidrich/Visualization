"""Throughput preparation and rendering, independent of CLI parsing."""
import json

import numpy as np
import pandas as pd

from .utils import (BOOTSTRAP_RESAMPLES, CI_METHOD, bootstrap_ci, cell_seed, has_spread,
                    AVERAGE_PART, file_names, load_ledger, normalize_throughput, output_directory,
                    output_stem, rerun_part, save_figure, strategy_order, t_interval, warn)


def prepare(frame, config):
    """Return normalized observations and mean of reruns with a resampled CI."""
    observations = normalize_throughput(frame, config.benchmarks, config.labels)
    observations["interval_kind"] = "ledger confidence bounds"
    observations["n_reruns"] = 1
    totals = observations.groupby("benchmark_short")["rerun"].nunique()
    observations["total_reruns"] = observations["benchmark_short"].map(totals)
    observations["contributing_reruns"] = observations["rerun"].map(lambda r: json.dumps([r]))
    records = []
    keys = ["benchmark_short", "strategy", "threads_blobshuffle"]
    for (benchmark, strategy, threads), group in observations.groupby(keys, sort=False):
        count = len(group)
        total = int(totals[benchmark])
        if count < total:
            warn(f"Incomplete coverage: {benchmark}/{strategy}/threads={threads}: {count}/{total} reruns")
        scores = group["score"].to_numpy()
        ci = {name: np.nan for name in ("ci_low", "ci_high", "t_low", "t_high",
                                        "ci_method", "ci_resamples", "ci_seed")}
        ci["ci_seed"] = pd.NA  # Seeds exceed 2**53, so a float column would round them.
        if has_spread(scores):
            seed = cell_seed(benchmark, strategy, threads, "score")
            ci["ci_low"], ci["ci_high"] = bootstrap_ci(scores, seed=seed)
            ci["t_low"], ci["t_high"] = t_interval(scores)
            ci.update(ci_method=CI_METHOD, ci_resamples=BOOTSTRAP_RESAMPLES, ci_seed=seed)
        else:
            warn(f"No CI for {benchmark}/{strategy}/threads={threads}: fewer than two reruns or no spread")
        records.append({
            "benchmark_short": benchmark, "strategy": strategy, "threads_blobshuffle": threads,
            "score": group["score"].mean(), "score_unit": "ops/s",
            **ci,
            "interval_kind": CI_METHOD, "n_reruns": count, "total_reruns": total,
            "contributing_reruns": json.dumps(group["rerun"].tolist()),
            "source_rows": json.dumps(group["source_row"].astype(int).tolist()),
            "source_files": json.dumps(group["source_file"].unique().tolist()) if "source_file" in group else "[]",
            "original_strategies": json.dumps(group["strategy_original"].unique().tolist()),
        })
    summary = pd.DataFrame.from_records(records)
    summary["ci_seed"] = summary["ci_seed"].astype("Int64")
    summary["ci_resamples"] = summary["ci_resamples"].astype("Int64")
    return observations, summary


def render(table, config, title, *, strategies=None, threads=None):
    """Build a figure from prepared values; return it for saving or inspection."""
    import matplotlib

    if not config.show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    strategies = strategies if strategies is not None else strategy_order(table, config)
    threads = threads if threads is not None else sorted(table["threads_blobshuffle"].unique())
    fig, ax = plt.subplots(figsize=config.figsize)
    positions = {n: i for i, n in enumerate(threads)}
    width = 0.8 / len(strategies)
    palette = matplotlib.colormaps["tab10"]
    for i, strategy in enumerate(strategies):
        data = table[table["strategy"].eq(strategy)].sort_values("threads_blobshuffle")
        x = np.array([positions[n] for n in data["threads_blobshuffle"]]) + (i - (len(strategies) - 1) / 2) * width
        color = config.colors.get(strategy, palette(i % 10))
        ax.bar(x, data["score"], width=width, color=color, edgecolor="white",
               label=config.labels.get(strategy, strategy), zorder=2)
        aggregate = "ci_low" in data
        low_col, high_col = ("ci_low", "ci_high") if aggregate else ("score_confidence_low", "score_confidence_high")
        complete = data[low_col].notna() & data[high_col].notna()
        if complete.any():
            valid = data.loc[complete]
            err = np.vstack([valid["score"] - valid[low_col], valid[high_col] - valid["score"]])
            ax.errorbar(x[complete.to_numpy()], valid["score"], yerr=err, fmt="none",
                        color="#444", elinewidth=1.5, capsize=4, zorder=3)
        if aggregate:
            for xi, (_, row) in zip(x, data.iterrows()):
                y = row["ci_high"] if pd.notna(row["ci_high"]) else row["score"]
                ax.annotate(f"n={row['n_reruns']}/{row['total_reruns']}", (xi, y),
                            xytext=(0, 4), textcoords="offset points", ha="center", fontsize=7)
    ax.set_xticks(np.arange(len(threads)), threads)
    ax.set_xlabel("BlobShuffle threads")
    ax.set_ylabel("Throughput (ops/s)")
    ax.set_title(f"{title}\nError bars: {table['interval_kind'].iloc[0]}")
    ax.set_ylim(bottom=0)
    ax.margins(y=0.15)
    ax.grid(axis="y", color="#ddd", zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
    fig.tight_layout()
    return fig


def run(config):
    """Validate all requested results before creating output files."""
    observations, averages = prepare(load_ledger(config.ledger, config.delimiter), config)
    benchmark_names = file_names(config.benchmarks, "benchmark")
    rerun_names = file_names(observations["rerun"].unique(), "rerun")
    directory = output_directory(config.output)
    paths = []
    for benchmark in config.benchmarks:
        data = observations[observations["benchmark_short"].eq(benchmark)]
        summary = averages[averages["benchmark_short"].eq(benchmark)]
        order = strategy_order(data, config)
        threads = sorted(data["threads_blobshuffle"].unique())
        tables = [(rerun_part(rerun_names[rerun]), f"{benchmark} — rerun {rerun}", group)
                  for rerun, group in data.groupby("rerun", sort=False)]
        tables.append((AVERAGE_PART, f"{benchmark} — average across all reruns", summary))
        for suffix, title, table in tables:
            stem = directory / output_stem("thrpt", benchmark_names[benchmark], suffix)
            csv_path = stem.with_suffix(".csv")
            table.to_csv(csv_path, index=False)
            paths.append(csv_path)
            fig = render(table, config, title, strategies=order, threads=threads)
            import matplotlib.pyplot as plt

            try:
                paths.extend(save_figure(fig, stem, config))
                if config.show:
                    plt.show()
            finally:
                plt.close(fig)
    return paths
