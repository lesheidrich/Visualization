"""Paired 2A vs 2B comparison: log-t intervals, equivalence verdict and flags (table only)."""
from datetime import datetime
import json
import math

import numpy as np
import pandas as pd

from .utils import (row_tag, run_tag, bootstrap_ci, cell_seed, file_names, has_spread, load_ledger,
                    normalize_throughput, output_directory, output_stem, t_quantile, warn,
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


def run(config):
    """All validation happens before the output folder exists."""
    tables = prepare(load_ledger(config.ledger, config.delimiter), config)
    names = file_names(config.benchmarks, "benchmark")
    directory = output_directory(config.output, FEATURE)
    paths = []
    for benchmark in config.benchmarks:
        path = directory / (output_stem(FEATURE, names[benchmark], AVERAGE_PART) + ".csv")
        tables[benchmark].to_csv(path, index=False)
        paths.append(path)
    return paths
