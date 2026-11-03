"""Descriptive same-rerun speedup preparation, plotting, and exports."""
import json

import numpy as np
import pandas as pd

from .utils import (BOOTSTRAP_RESAMPLES, CI_METHOD, bootstrap_ci, cell_seed, has_spread,
                    AVERAGE_PART, FIELLER_METHOD, fieller_interval, file_names, load_ledger, normalize_throughput, output_directory,
                    output_stem, rerun_part, save_figure, sorted_for_csv, strategy_order, run_tag, t_interval, warn, warn_incomplete)


def prepare(frame, config):
    """Pair with base at one thread; aggregate as a ratio of mean scores."""
    observations = normalize_throughput(frame, config.benchmarks, config.labels,
                                       include_bounds=False, exclude=config.exclude)
    keys = ["benchmark_short", "rerun"]
    base = observations[observations["strategy"].eq("base") &
                        observations["threads_blobshuffle"].eq(1)]
    required = observations[keys].drop_duplicates()
    missing = required.merge(base[keys], on=keys, how="left", indicator=True)
    missing = missing[missing["_merge"].eq("left_only")]
    if not missing.empty:
        details = []
        for record in missing[keys].to_dict("records"):
            rows = observations[observations["benchmark_short"].eq(record["benchmark_short"])
                                & observations["rerun"].eq(record["rerun"])]
            details.append(f"{record['benchmark_short']}/{run_tag('base', 1, record['rerun'])}/thrpt missing "
                           f"(ledger row {int(rows['source_row'].iloc[0])} is in that rerun)")
        raise ValueError("Speedup needs base at one thread in every rerun: " + "; ".join(details))
    invalid = base[base["score"].le(0)]
    if not invalid.empty:
        details = [f"{r['benchmark_short']}/{run_tag('base', 1, r['rerun'])}/thrpt (ledger row {int(r['source_row'])})"
                   for _, r in invalid.iterrows()]
        raise ValueError("Baseline score must be positive: " + "; ".join(details))
    # Keep source fields only; unused throughput intervals are not ratio bounds.
    bound_columns = {"score_confidence_low", "score_confidence_high", "score_error"}
    observations = observations.drop(columns=list(bound_columns & set(observations.columns)))
    baseline_fields = ["score", "source_row", "original_score", "original_score_unit", "strategy_original"]
    if "source_file" in observations:
        baseline_fields.append("source_file")
    denominator = base[keys + baseline_fields].rename(columns={c: f"baseline_{c}" for c in baseline_fields})
    observations = observations.merge(denominator, on=keys, how="left", validate="many_to_one", sort=False)
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        observations["speedup"] = observations["score"] / observations["baseline_score"]
    invalid = ~np.isfinite(observations["speedup"])
    if invalid.any():
        raise ValueError(f"Nonfinite speedup at ledger row(s) {observations.loc[invalid, 'source_row'].tolist()}")
    observations["baseline_strategy"] = "base"
    observations["baseline_threads"] = 1
    totals = observations.groupby("benchmark_short")["rerun"].nunique()
    observations["n_reruns"] = 1
    observations["total_reruns"] = observations["benchmark_short"].map(totals)
    observations["contributing_reruns"] = observations["rerun"].map(lambda r: json.dumps([r]))
    observations["summary_kind"] = "individual paired ratio"
    records = []
    for (benchmark, strategy, threads), group in observations.groupby(
        ["benchmark_short", "strategy", "threads_blobshuffle"], sort=False
    ):
        count, total = len(group), int(totals[benchmark])
        if count < total:
            warn_incomplete(benchmark, strategy, threads, group,
                            observations.loc[observations["benchmark_short"].eq(benchmark), "rerun"])
        # Divide before summing to avoid overflowing an otherwise finite mean.
        mean_score = (group["score"] / count).sum()
        mean_baseline = (group["baseline_score"] / count).sum()
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            speedup = mean_score / mean_baseline
        if not np.isfinite(speedup):
            raise ValueError(f"Nonfinite speedup at ledger row(s) {group['source_row'].astype(int).tolist()}")
        ci = {name: np.nan for name in ("ci_low", "ci_high", "t_low", "t_high",
                                        "fieller_low", "fieller_high", "ci_method", "ci_resamples", "ci_seed")}
        ci["ci_seed"] = pd.NA  # Seeds exceed 2**53, so a float column would round them.
        ratios = group["speedup"].to_numpy()
        ordered = group.sort_values("rerun", kind="stable")  # CI only: independent of ledger row order
        if has_spread(ratios):
            seed = cell_seed(benchmark, strategy, threads, "speedup")
            ci["ci_low"], ci["ci_high"] = bootstrap_ci(
                ordered["score"].to_numpy(), ordered["baseline_score"].to_numpy(), seed=seed)
            ci["t_low"], ci["t_high"] = t_interval(ratios)
            bounds = fieller_interval(ordered["score"].to_numpy(), ordered["baseline_score"].to_numpy())
            if bounds is None:
                warn(f"No Fieller interval for {benchmark}/{strategy}/threads={threads}: "
                     "the base mean cannot be told apart from zero or the interval is unbounded")
            else:
                ci["fieller_low"], ci["fieller_high"] = bounds
            ci.update(ci_method=CI_METHOD, ci_resamples=BOOTSTRAP_RESAMPLES, ci_seed=seed)
        elif not (strategy == "base" and threads == 1):
            warn(f"No CI for {benchmark}/{strategy}/threads={threads}: fewer than two reruns or no spread")
        records.append({
            "benchmark_short": benchmark, "strategy": strategy, "threads_blobshuffle": threads,
            "mean_score": mean_score, "mean_baseline_score": mean_baseline,
            "speedup": speedup, **ci, "t_basis": "per-rerun ratios",
            "baseline_strategy": "base", "baseline_threads": 1,
            "summary_kind": "ratio of mean scores across reruns", "n_reruns": count,
            "total_reruns": total, "contributing_reruns": json.dumps(group["rerun"].tolist()),
            "source_rows": json.dumps(group["source_row"].astype(int).tolist()),
            "baseline_source_rows": json.dumps(group["baseline_source_row"].astype(int).tolist()),
            "source_files": json.dumps(group["source_file"].unique().tolist()) if "source_file" in group else "[]",
            "baseline_source_files": json.dumps(group["baseline_source_file"].unique().tolist()) if "baseline_source_file" in group else "[]",
            "original_strategies": json.dumps(group["strategy_original"].unique().tolist()),
        })
    summary = pd.DataFrame.from_records(records)
    summary["ci_seed"] = summary["ci_seed"].astype("Int64")
    summary["ci_resamples"] = summary["ci_resamples"].astype("Int64")
    return observations, summary


def render(table, config, title, *, strategies=None, threads=None):
    """Render prepared dimensionless ratios; across-reruns lines carry the paired Fieller interval."""
    import matplotlib

    if not config.show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    strategies = strategies if strategies is not None else strategy_order(table, config)
    threads = threads if threads is not None else sorted(table["threads_blobshuffle"].unique())
    fig, ax = plt.subplots(figsize=config.figsize)
    ax.plot(threads, threads, "--", color="black", linewidth=1.5, label="Ideal (y=N)", zorder=1)
    ax.axhline(1, color="gray", linestyle=":", linewidth=1.5, label="Base N=1", zorder=1)
    palette = matplotlib.colormaps["tab10"]
    aggregate = table["summary_kind"].iloc[0] == "ratio of mean scores across reruns"
    for i, strategy in enumerate(strategies):
        data = table[table["strategy"].eq(strategy)].set_index("threads_blobshuffle").reindex(threads)
        ax.plot(threads, data["speedup"], marker="o", linewidth=1.8,
                color=config.colors.get(strategy, palette(i % 10)),
                label=config.labels.get(strategy, strategy), zorder=3)
        if aggregate:
            ok = data["fieller_low"].notna() & data["fieller_high"].notna()
            if ok.any():
                valid = data[ok]
                # Never negative, whatever rounding does to a bound that sits on the point.
                lower = np.maximum(valid["speedup"] - valid["fieller_low"], 0)
                upper = np.maximum(valid["fieller_high"] - valid["speedup"], 0)
                ax.errorbar(valid.index, valid["speedup"], yerr=np.vstack([lower, upper]),
                            fmt="none", ecolor="#222", elinewidth=1.2, capsize=3,
                            capthick=1.2, zorder=6)
    ax.set_xticks(threads)
    ax.set_xlabel("BlobShuffle threads")
    ax.set_ylabel("Speedup vs. Base N=1 (dimensionless)")
    drawn = aggregate and (table["fieller_low"].notna() & table["fieller_high"].notna()).any()
    ax.set_title(f"{title}\nError bars: {FIELLER_METHOD}" if drawn else title)
    ax.set_ylim(bottom=0)
    ax.margins(y=0.15)
    ax.grid(axis="y", color="#ddd", zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
    fig.tight_layout()
    return fig


def run(config):
    """Validate every pairing before creating invocation output files."""
    observations, averages = prepare(load_ledger(config.ledger, config.delimiter), config)
    benchmark_names = file_names(config.benchmarks, "benchmark")
    rerun_names = file_names(observations["rerun"].unique(), "rerun")
    directory = output_directory(config.output, "speedup")
    paths = []
    for benchmark in config.benchmarks:
        data = observations[observations["benchmark_short"].eq(benchmark)]
        summary = averages[averages["benchmark_short"].eq(benchmark)]
        order, threads = strategy_order(data, config), sorted(data["threads_blobshuffle"].unique())
        tables = [(rerun_part(rerun_names[rerun]), f"{benchmark} — speedup, rerun {rerun}", group)
                  for rerun, group in data.groupby("rerun", sort=False)]
        tables.append((AVERAGE_PART, f"{benchmark} — speedup, mean of reruns", summary))
        for suffix, title, table in tables:
            stem = directory / output_stem("speedup", benchmark_names[benchmark], suffix)
            csv_path = stem.with_suffix(".csv")
            sorted_for_csv(table, config).to_csv(csv_path, index=False)
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
