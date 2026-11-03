"""Paired 2A vs 2B comparison: log-t intervals, equivalence verdict and flags and the ratio plot."""
from datetime import datetime
import json
import math

import numpy as np
import pandas as pd

from .utils import (row_tag, run_tag, bootstrap_ci, cell_seed, file_names, has_spread, load_ledger,
                    normalize_throughput, output_directory, output_stem, save_figure, t_quantile, warn,
                    AVERAGE_PART)

FAMILY_ALPHA = 0.05
EXPECTED_RERUNS = 10
WORK_TOLERANCE = 0.001   # advisor's proposal, not a sourced value
ORDER_TOLERANCE = 2      # advisor's proposal, not a sourced value
FEATURE = "compare"
COLUMNS = ["benchmark_short", "threads_blobshuffle", "n_reruns", "mean_2a", "mean_2b",
           "ratio_of_means", "logt_ratio", "diff_level", "diff_low", "diff_high", "equiv_level",
           "equiv_low", "equiv_high", "smallest_provable_margin", "sd_log_ratio_pct",
           "reruns_2a_gt_2b", "margin", "verdict", "note", "flags", "boot95_low", "boot95_high",
           "ci_seed", "ci_resamples", "order_2a_first", "order_adjusted_ratio_geo",
           "contributing_reruns", "source_rows"]


def levels(thread_count):
    """(difference level, equivalence level) for a family of this many thread counts."""
    alpha = FAMILY_ALPHA / thread_count
    return 1 - alpha, 1 - 2 * alpha


def paired_statistics(a, b, thread_count):
    """Paired log-t numbers for per-rerun scores a (2A) and b (2B); intervals None without spread."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    n = len(a)
    ratios = a / b
    y = np.log(ratios)
    mean, sd = float(y.mean()), float(y.std(ddof=1))
    diff_level, equiv_level = levels(thread_count)
    result = {"n": n, "mean_2a": float(a.mean()), "mean_2b": float(b.mean()),
              "ratio_of_means": float(a.mean() / b.mean()), "logt_ratio": math.exp(mean),
              "diff_level": diff_level, "equiv_level": equiv_level, "sd_log": sd,
              "reruns_2a_gt_2b": int((a > b).sum()), "spread": bool(has_spread(ratios)),
              "diff_low": None, "diff_high": None, "equiv_low": None, "equiv_high": None,
              "smallest_margin": None, "y": y}
    if result["spread"]:
        se = sd / math.sqrt(n)
        for prefix, level in (("diff", diff_level), ("equiv", equiv_level)):
            half = t_quantile(1 - (1 - level) / 2, n - 1) * se
            result[f"{prefix}_low"], result[f"{prefix}_high"] = math.exp(mean - half), math.exp(mean + half)
        result["smallest_margin"] = math.exp(abs(mean) + t_quantile(1 - (1 - equiv_level) / 2, n - 1) * se) - 1
    return result


def _percent(x):
    return f"{x * 100:.1f}%"


def decide(stats, margin):
    """(verdict, note); first matching rule wins. Pure function of the interval numbers."""
    if not stats["spread"]:
        return "no spread observed", ""
    low, high = stats["diff_low"], stats["diff_high"]
    ratio = stats["logt_ratio"]
    if margin is not None and (stats["equiv_low"] > 1 / (1 + margin) and stats["equiv_high"] < 1 + margin):
        if low > 1:
            return "equivalent", f"2A {_percent(ratio - 1)} faster, below margin"
        if high < 1:
            return "equivalent", f"2B {_percent(1 / ratio - 1)} faster, below margin"
        return "equivalent", ""
    if low > 1:
        verdict, outside = "2A faster", low > 1 + (margin or 0)
    elif high < 1:
        verdict, outside = "2B faster", high < 1 / (1 + (margin or 0))
    else:
        return "inconclusive", ""
    if margin is None:
        return verdict, ""
    return verdict, "beyond margin" if outside else "size unclear"


def _timestamp(value):
    try:
        return datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None


def ran_first(row_a, row_b):
    """+1 when 2A ran first, -1 when 2B did, None when missing, unparsable, tied or incomparable."""
    first, second = _timestamp(row_a.get("run_timestamp", "")), _timestamp(row_b.get("run_timestamp", ""))
    if first is None or second is None:
        return None
    try:
        if first == second:
            return None
        return 1 if first < second else -1
    except TypeError:  # naive against aware
        return None


def order_columns(y, order):
    """(2A-first count, order-adjusted geometric ratio, unknown?, unbalanced?)."""
    known = [o for o in order if o is not None]
    first_2a, first_2b = known.count(1), known.count(-1)
    unknown = len(known) < len(order)
    adjusted = None
    o = np.asarray(order, dtype=float) if not unknown else None
    if o is not None and o.max() != o.min():
        slope = np.cov(o, y, bias=True)[0, 1] / o.var()
        adjusted = math.exp(float(y.mean() - slope * o.mean()))
    return first_2a, adjusted, unknown, abs(first_2a - first_2b) > ORDER_TOLERANCE


def _work(rows):
    """Mean aux_recordBytes / original_score, or None when it cannot be computed."""
    if "aux_recordBytes" not in rows or "original_score" not in rows:
        return None
    try:
        bytes_ = pd.to_numeric(rows["aux_recordBytes"].astype(str).str.strip(), errors="raise")
        score = pd.to_numeric(rows["original_score"].astype(str).str.strip(), errors="raise")
        work = float((bytes_ / score).mean())
    except (ValueError, TypeError):
        return None
    return work if math.isfinite(work) else None


def flag_list(rows_a, rows_b, unknown, unbalanced):
    """Flags for one row; they never alter numbers. Rows are the contributing ledger rows."""
    both = pd.concat([rows_a, rows_b])
    flags = []
    if "hostname" in both and both["hostname"].astype(str).str.strip().nunique() > 1:
        flags.append("host_differs")
    if any(r["git_sha"].astype(str).str.strip().nunique() > 1 for r in (rows_a, rows_b) if "git_sha" in r):
        flags.append("sha_differs")
    dirty = both["git_dirty"].astype(str).str.strip().str.lower() if "git_dirty" in both else pd.Series([""])
    if (dirty != "false").any():
        flags.append("dirty_build")
    work_a, work_b = _work(rows_a), _work(rows_b)
    if work_a is None or work_b is None or abs(work_a - work_b) > WORK_TOLERANCE * (work_a + work_b) / 2:
        flags.append("work_differs")
    if unbalanced:
        flags.append("order_unbalanced")
    if unknown:
        flags.append("order_unknown")
    return flags


def prepare(frame, config, first="2a", second="2b"):
    """Validate every pairing and build one summary table per benchmark."""
    observations = normalize_throughput(frame, config.benchmarks, config.labels, include_bounds=False)
    margin = getattr(config, "comparison_margin", None)
    if margin is None:
        warn("No comparison margin set: equivalence cannot be claimed")
    tables = {}
    for benchmark in config.benchmarks:
        data = observations[observations["benchmark_short"].eq(benchmark)]
        for strategy in (first, second):
            if not data["strategy"].eq(strategy).any():
                raise ValueError(f"{benchmark}: no throughput rows for strategy {strategy}")
        pair = data[data["strategy"].isin([first, second])]
        reruns = sorted(pair["rerun"].unique())
        thread_counts = sorted(pair["threads_blobshuffle"].unique())
        if len(reruns) < 2:
            raise ValueError(f"{benchmark}: need at least 2 reruns to compare, found {len(reruns)}")
        if len(reruns) != EXPECTED_RERUNS:
            warn(f"{benchmark}: {len(reruns)} reruns instead of {EXPECTED_RERUNS}")
        keyed = {(r.strategy, r.threads_blobshuffle, r.rerun): i for i, r in pair.iterrows()}
        for threads in thread_counts:
            for rerun in reruns:
                for strategy in (first, second):
                    if (strategy, threads, rerun) not in keyed:
                        other = second if strategy == first else first
                        partner = keyed.get((other, threads, rerun))
                        if partner is None:
                            raise ValueError(f"{benchmark}/{run_tag(first, threads, rerun)}/thrpt and "
                                             f"{run_tag(second, threads, rerun)} are both missing.")
                        row = pair.loc[partner]
                        raise ValueError(f"{benchmark}/{row_tag(row)}/thrpt missing {strategy} partner "
                                         f"(ledger row {int(row['source_row'])}).")
                    row = pair.loc[keyed[(strategy, threads, rerun)]]
                    if row["score"] <= 0:
                        raise ValueError(f"{benchmark}/{row_tag(row)}/thrpt score must be positive "
                                         f"(ledger row {int(row['source_row'])}).")
        records = []
        for threads in thread_counts:
            rows_a = pair.loc[[keyed[(first, threads, r)] for r in reruns]]
            rows_b = pair.loc[[keyed[(second, threads, r)] for r in reruns]]
            stats = paired_statistics(rows_a["score"], rows_b["score"], len(thread_counts))
            verdict, note = decide(stats, margin)
            order = [ran_first(x, y) for (_, x), (_, y) in zip(rows_a.iterrows(), rows_b.iterrows())]
            count_first, adjusted, unknown, unbalanced = order_columns(stats["y"], order)
            flags = flag_list(rows_a, rows_b, unknown, unbalanced)
            record = {"benchmark_short": benchmark, "threads_blobshuffle": int(threads), "n_reruns": stats["n"],
                      "mean_2a": stats["mean_2a"], "mean_2b": stats["mean_2b"],
                      "ratio_of_means": stats["ratio_of_means"], "logt_ratio": stats["logt_ratio"],
                      "diff_level": stats["diff_level"], "diff_low": stats["diff_low"],
                      "diff_high": stats["diff_high"], "equiv_level": stats["equiv_level"],
                      "equiv_low": stats["equiv_low"], "equiv_high": stats["equiv_high"],
                      "smallest_provable_margin": stats["smallest_margin"],
                      "sd_log_ratio_pct": stats["sd_log"] * 100,
                      "reruns_2a_gt_2b": stats["reruns_2a_gt_2b"], "margin": margin,
                      "verdict": verdict, "note": note, "flags": ";".join(flags),
                      "boot95_low": None, "boot95_high": None, "ci_seed": pd.NA, "ci_resamples": pd.NA,
                      "order_2a_first": count_first, "order_adjusted_ratio_geo": adjusted,
                      "contributing_reruns": json.dumps(reruns),
                      "source_rows": json.dumps(sorted(int(r) for r in
                                                       pd.concat([rows_a, rows_b])["source_row"]))}
            if stats["spread"]:
                seed = cell_seed(benchmark, f"{first}_vs_{second}", threads, "ratio")
                record["boot95_low"], record["boot95_high"] = bootstrap_ci(
                    rows_a["score"].to_numpy(), rows_b["score"].to_numpy(), seed=seed)
                record["ci_seed"], record["ci_resamples"] = seed, 10000
            else:
                warn(f"No spread observed for {benchmark}/threads={threads}: no intervals")
            records.append(record)
        table = pd.DataFrame.from_records(records, columns=COLUMNS)
        table["ci_seed"] = table["ci_seed"].astype("Int64")
        table["ci_resamples"] = table["ci_resamples"].astype("Int64")
        tables[benchmark] = table
    return tables


def _level(value):
    return f"{round(value * 100, 6):g}%"


def render_ratio(table, config, benchmark):
    """Dot at logt_ratio per thread count, thick bar equiv_low..equiv_high, thin bar diff_low..diff_high.

    Everything comes from the prepared table and `config.comparison_margin`; nothing is recalculated."""
    import matplotlib

    if not config.show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    data = table.sort_values("threads_blobshuffle")
    margin = getattr(config, "comparison_margin", None)
    x = np.arange(len(data))
    ratio = data["logt_ratio"].to_numpy(dtype=float)
    bounds = {c: data[c].to_numpy(dtype=float) for c in ("equiv_low", "equiv_high", "diff_low", "diff_high")}
    fig, ax = plt.subplots(figsize=config.figsize)
    colour, grey = "#1f77b4", "#555"
    drawn = {"equiv": np.isfinite(bounds["equiv_low"]) & np.isfinite(bounds["equiv_high"]),
             "diff": np.isfinite(bounds["diff_low"]) & np.isfinite(bounds["diff_high"])}
    for key, width in (("diff", 1.5), ("equiv", 6)):
        keep = drawn[key]
        ax.vlines(x[keep], bounds[f"{key}_low"][keep], bounds[f"{key}_high"][keep], color=colour,
                  linewidth=width, alpha=0.45 if key == "equiv" else 1, zorder=2)
    ax.plot(x, ratio, linestyle="none", marker="o", color="black", markersize=5, zorder=3)
    values = [ratio, np.array([1.0])]
    values += [bounds[c][drawn[c.split("_")[0]]] for c in bounds]
    handles = [Line2D([], [], linestyle="none", marker="o", color="black", markersize=5,
                      label="geometric mean ratio"),
               Line2D([], [], color=colour, linewidth=6, alpha=0.45,
                      label=f"{_level(data['equiv_level'].iloc[0])} equivalence interval"),
               Line2D([], [], color=colour, linewidth=1.5,
                      label=f"{_level(data['diff_level'].iloc[0])} difference interval")]
    if margin is not None:
        for line in (1 + margin, 1 / (1 + margin)):
            ax.axhline(line, color=grey, linestyle="--", linewidth=1, zorder=1)
        values.append(np.array([1 + margin, 1 / (1 + margin)]))
        handles.append(Line2D([], [], color=grey, linestyle="--", linewidth=1,
                              label=f"margin {margin * 100:g}%"))
    ax.axhline(1, color=grey, linestyle="-", linewidth=1, zorder=1)
    handles.append(Line2D([], [], color=grey, linewidth=1, label="no difference"))
    every = np.concatenate(values)
    low, high = float(every.min()), float(every.max())
    pad = (high - low) * 0.05 or 0.01
    ax.set_yscale("linear")
    ax.set_ylim(low - pad, high + pad)
    ax.set_xticks(x, [str(int(n)) for n in data["threads_blobshuffle"]])
    ax.set_xlabel("BlobShuffle threads")
    ax.set_ylabel("2A / 2B throughput ratio")
    ax.set_title(f"{benchmark} \u2014 2A / 2B, geometric mean of per-rerun ratios")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(handles=handles, bbox_to_anchor=(1.02, 1), loc="upper left")
    return fig


def run(config):
    """All validation, and every figure, happens before the output folder exists."""
    import matplotlib.pyplot as plt

    tables = prepare(load_ledger(config.ledger, config.delimiter), config)
    names = file_names(config.benchmarks, "benchmark")
    figures = {}
    try:
        for benchmark in config.benchmarks:
            figures[benchmark] = render_ratio(tables[benchmark], config, benchmark)
        directory = output_directory(config.output, FEATURE)
        paths = []
        for benchmark in config.benchmarks:
            path = directory / (output_stem(FEATURE, names[benchmark], AVERAGE_PART) + ".csv")
            tables[benchmark].to_csv(path, index=False)
            paths.append(path)
            paths.extend(save_figure(figures[benchmark], directory / output_stem(FEATURE, names[benchmark], "ratio"),
                                     config))
        if config.show:
            plt.show()      # After every save: a window blocks until it is closed.
    finally:
        for fig in figures.values():
            plt.close(fig)
    return paths
