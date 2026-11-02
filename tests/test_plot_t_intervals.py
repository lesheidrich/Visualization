"""Change plot-t-intervals: across-reruns plots draw t (throughput, gc_alloc_rate_norm) or paired
Fieller (speedup) intervals; the bootstrap stays in the tables. Expected values are literal or come
from calculations written here (a grid search for Fieller, hand formulas for t), not from src."""
import io
import json
import math
from contextlib import redirect_stdout
from pathlib import Path
import warnings

import numpy as np
import pandas as pd
import pytest
import yaml
from matplotlib.collections import LineCollection

from conftest import gc_observation, observation
from src import cli, compare, gc_metrics, speedup, thrpt
from src.config import Config
from src.utils import FIELLER_METHOD, T_METHOD, fieller_interval, load_ledger

BENCH = "mb1_rawNoFinalize"
BENCH2 = "mb2_rawFinalize"
ROOT = Path(__file__).parents[1]
RAW3 = ROOT / "docs" / "raw3.csv"
T9 = 2.262157  # Student t, 0.975 quantile, 9 degrees of freedom (printed table value)

BASE = [100, 104, 96, 103, 98, 101, 99, 103, 97, 99]
STRATEGY = [200, 210, 190, 205, 195, 202, 198, 208, 192, 200]
SKEW = [1] * 9 + [10000]  # mean 1000.9, SD 3162.1: its t interval starts below zero
TEN = list(range(100, 200, 10))

THRPT_AVG_COLUMNS = ["benchmark_short", "strategy", "threads_blobshuffle", "score", "score_unit", "ci_low",
                     "ci_high", "t_low", "t_high", "ci_method", "ci_resamples", "ci_seed", "interval_kind",
                     "n_reruns", "total_reruns", "contributing_reruns", "source_rows", "source_files",
                     "original_strategies"]
SPEEDUP_AVG_COLUMNS = ["benchmark_short", "strategy", "threads_blobshuffle", "mean_score",
                       "mean_baseline_score", "speedup", "ci_low", "ci_high", "t_low", "t_high",
                       "fieller_low", "fieller_high", "ci_method", "ci_resamples", "ci_seed", "t_basis",
                       "baseline_strategy", "baseline_threads", "summary_kind", "n_reruns", "total_reruns",
                       "contributing_reruns", "source_rows", "baseline_source_rows", "source_files",
                       "baseline_source_files", "original_strategies"]
GC_COLUMNS_BEFORE = ["benchmark_short", "strategy", "threads_blobshuffle", *gc_metrics.AVERAGED,
                     "n_reruns", "total_reruns", "contributing_reruns", "source_rows", "source_files",
                     "original_strategies", "summary_kind", "gc_alloc_rate_norm_ci_low",
                     "gc_alloc_rate_norm_ci_high"]
GC_T_COLUMNS = [f"{m}_t_{e}" for m in gc_metrics.AVERAGED for e in ("low", "high")]


# ------------------------------------------------------------------------------ helpers

def t_by_hand(values):
    values = np.asarray(values, dtype=float)
    half = T9 * values.std(ddof=1) / math.sqrt(len(values))
    return values.mean() - half, values.mean() + half


def grid_fieller(strategy, base, t, low, high, steps=600_001):
    """Every theta whose t interval of (strategy - theta * base) contains zero, by brute force."""
    strategy, base = np.asarray(strategy, float), np.asarray(base, float)
    theta = np.linspace(low, high, steps)
    inside = np.zeros(steps, dtype=bool)
    for start in range(0, steps, 100_000):  # chunks keep memory small
        chunk = theta[start:start + 100_000, None]
        d = strategy[None, :] - chunk * base[None, :]
        inside[start:start + 100_000] = np.abs(d.mean(axis=1)) <= t * d.std(axis=1, ddof=1) / math.sqrt(len(base))
    return theta[inside].min(), theta[inside].max(), inside[0], inside[-1]


def collect(function, *args, **kwargs):
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        result = function(*args, **kwargs)
    return result, [str(w.message) for w in seen]


def ten_rerun_rows(benchmark=BENCH, extra_strategies=True):
    """base at 1 thread (BASE x 1000), 1a at 2 threads (STRATEGY x 1000), 2a at 2 threads (SKEW)."""
    rows = []
    for i in range(10):
        rerun = f"{i + 1:02d}"
        alloc = str(1000 + 100 * i)
        rows.append(gc_observation(strategy="base", rerun=rerun, threads=1, score=BASE[i] * 1000,
                                   benchmark_short=benchmark, gc_alloc_rate_norm=alloc,
                                   gc_count=str(100 + 10 * i)))
        rows.append(gc_observation(strategy="1a", rerun=rerun, threads=2, score=STRATEGY[i] * 1000,
                                   benchmark_short=benchmark, gc_alloc_rate_norm=alloc))
        if extra_strategies:
            rows.append(gc_observation(strategy="2a", rerun=rerun, threads=2, score=SKEW[i],
                                       benchmark_short=benchmark, gc_alloc_rate_norm=str(SKEW[i])))
    return rows


@pytest.fixture
def cfg(tmp_path):
    return Config(tmp_path / "ledger.csv", tmp_path / "out", benchmarks=(BENCH,), figsize=(5, 3), dpi=40)


def project(tmp_path, ledger_path, benchmarks=(BENCH,), delimiter="auto"):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "input": {"path": str(ledger_path), "delimiter": delimiter},
        "output": {"directory": str(tmp_path / "out"), "formats": ["png"]},
        "benchmarks": list(benchmarks), "plot": {"figsize": [6, 4], "dpi": 40},
        "comparison": {"margin": 0.05}}))
    return path


def run_entry(monkeypatch, config_path, name, module):
    """Run a console entry point in-process; return (exit code, written paths, {stem: figure})."""
    figures = {}
    real = module.save_figure

    def spy(fig, stem, config):
        figures[Path(stem).name] = fig
        return real(fig, stem, config)

    monkeypatch.setattr(module, "save_figure", spy)
    out = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with redirect_stdout(out):
            code = getattr(cli, name)(["--config", str(config_path)])
    return code, [Path(line) for line in out.getvalue().splitlines()], figures


def bars(fig):
    """Sorted (low, high) of every error bar on the first axes."""
    ax = fig.axes[0]
    return sorted((float(s[0][1]), float(s[1][1])) for c in ax.collections if isinstance(c, LineCollection)
                  for s in c.get_segments())


def same_bars(found, expected, atol=1e-9):
    assert len(found) == len(expected)
    np.testing.assert_allclose(np.array(found, float), np.array(expected, float), rtol=1e-7, atol=atol)


def csv_for(paths, ending):
    return pd.read_csv(next(p for p in paths if p.name.endswith(ending)), dtype={"strategy": str})


def pairs(frame, low, high):
    rows = frame[frame[low].notna() & frame[high].notna()]
    return sorted(zip(rows[low].astype(float), rows[high].astype(float)))


# ================================================================================ unit

def test_unit_fieller_literal_scenario():
    low, high = fieller_interval(STRATEGY, BASE)
    assert (low, high) == pytest.approx((1.98867, 2.01105), abs=1e-5)
    assert np.mean(STRATEGY) / np.mean(BASE) == pytest.approx(2.0)


def test_unit_fieller_matches_a_grid_search_on_the_literal_scenario():
    # Smallest and largest theta for which a t interval on (s - theta * b) contains zero.
    low, high, left_inside, right_inside = grid_fieller(STRATEGY, BASE, T9, 1.9, 2.1)
    assert not left_inside and not right_inside  # the search range brackets the interval
    got = fieller_interval(STRATEGY, BASE)
    assert got == pytest.approx((low, high), abs=1e-6)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_unit_fieller_matches_a_grid_search_on_correlated_random_data(seed):
    rng = np.random.default_rng(seed)
    base = rng.normal(100, 5, 10)
    strategy = 1.5 * base + rng.normal(0, 3, 10)
    point = strategy.mean() / base.mean()
    low, high, left_inside, right_inside = grid_fieller(strategy, base, T9, point - 0.3, point + 0.3)
    assert not left_inside and not right_inside
    assert fieller_interval(strategy, base) == pytest.approx((low, high), abs=1e-6)
    assert low < point < high


def test_unit_fieller_uses_the_covariance_not_independent_variances():
    # Strategy follows base exactly plus a tiny wobble: a paired interval is very tight,
    # while one that ignored the pairing (independent variances) would be wide.
    base = np.array([100, 120, 80, 110, 90, 105, 95, 115, 85, 100], float)
    strategy = 2 * base + np.array([0.1, -0.1, 0.2, -0.2, 0.1, -0.1, 0.0, 0.1, -0.1, 0.0])
    low, high = fieller_interval(strategy, base)
    assert high - low < 0.01
    independent = (strategy.mean() / base.mean()) * math.sqrt(strategy.var(ddof=1) / strategy.mean() ** 2
                                                              + base.var(ddof=1) / base.mean() ** 2)
    assert independent > 0.05


def test_unit_fieller_unbounded_when_base_mean_cannot_be_told_from_zero():
    base = [1, 100, 1, 100]  # mean 50.5, SD 57.2, t(3) 3.182: 50.5 < 3.182 * 57.2 / 2
    strategy = [2, 200, 2, 200]
    assert fieller_interval(strategy, base) is None


def test_unit_fieller_interval_always_contains_the_point_when_bounded():
    # With a positive theta^2 coefficient the quadratic is not above zero at the point, so a bounded
    # interval exists and holds the ratio of means (the discriminant branch is a defensive guard).
    rng = np.random.default_rng(99)
    for _ in range(300):
        base = rng.normal(100, rng.uniform(1, 30), 10)
        strategy = rng.normal(150, rng.uniform(1, 30), 10)
        interval = fieller_interval(strategy, base)
        if interval is not None:
            assert interval[0] <= strategy.mean() / base.mean() <= interval[1]


def test_unit_fieller_no_spread_through_prepare(cfg):
    # No spread: strategy is exactly 2x base in every rerun -> no interval, existing warning.
    rows = []
    for i, b in enumerate(BASE):
        rows += [observation(rerun=f"r{i}", score=b), observation("1a", f"r{i}", 2, 2 * b)]
    (_, summary), messages = collect(speedup.prepare, pd.DataFrame(rows), cfg)
    row = summary[summary.strategy.eq("1a")].iloc[0]
    assert pd.isna(row.fieller_low) and pd.isna(row.fieller_high)
    assert f"No CI for {BENCH}/1a/threads=2: fewer than two reruns or no spread" in messages
    assert not any("Fieller" in m for m in messages)


def test_unit_fieller_row_order_does_not_change_the_interval(cfg):
    frame = pd.DataFrame([r for rerun in range(10) for r in (
        observation(rerun=f"r{rerun}", score=BASE[rerun]), observation("1a", f"r{rerun}", 2, STRATEGY[rerun]))])
    _, ordered = speedup.prepare(frame, cfg)
    _, shuffled = speedup.prepare(frame.sample(frac=1, random_state=7).reset_index(drop=True), cfg)
    a = ordered[ordered.strategy.eq("1a")].iloc[0]
    b = shuffled[shuffled.strategy.eq("1a")].iloc[0]
    assert (a.fieller_low, a.fieller_high) == (b.fieller_low, b.fieller_high)
    assert (a.fieller_low, a.fieller_high) == pytest.approx((1.98867, 2.01105), abs=1e-5)


def test_unit_gc_t_columns_on_a_literal_case(cfg):
    rows = [gc_observation(strategy="base", rerun=f"r{i}", threads=1, gc_count=str(100 + 10 * i),
                           gc_alloc_rate_norm="1000") for i in range(10)]
    (_, averages), messages = collect(gc_metrics.prepare, pd.DataFrame(rows), cfg)
    row = averages.iloc[0]
    # 145 +/- 2.262157 * 30.2765 / sqrt(10)
    assert (row.gc_count_t_low, row.gc_count_t_high) == pytest.approx((123.34, 166.66), abs=0.01)
    # Constant metrics have no spread: blank, and no warning.
    assert pd.isna(row.gc_time_t_low) and pd.isna(row.gc_time_t_high)
    assert pd.isna(row.gc_alloc_rate_norm_t_low)
    assert messages.count(f"No gc_alloc_rate_norm CI for {BENCH}/base/threads=1: "
                          "fewer than two reruns or no spread") == 1
    assert len(messages) == 1


def test_unit_gc_t_columns_blank_with_a_blank_contributor_and_no_extra_warning(cfg):
    rows = [gc_observation(strategy="base", rerun=f"r{i}", threads=1, gc_count=str(100 + 10 * i),
                           pauses_max="" if i == 3 else str(i + 1), gc_alloc_rate_norm="1000")
            for i in range(10)]
    (_, averages), messages = collect(gc_metrics.prepare, pd.DataFrame(rows), cfg)
    row = averages.iloc[0]
    assert pd.isna(row.pauses_max) and pd.isna(row.pauses_max_t_low) and pd.isna(row.pauses_max_t_high)
    assert pd.notna(row.gc_count_t_low)
    # Existing warnings only: blank pauses_max, its missing mean, and the no-spread alloc CI.
    assert len(messages) == 3
    assert sum("pauses_max" in m for m in messages) == 2
    assert sum("gc_alloc_rate_norm" in m for m in messages) == 1
    assert not any("_t_" in m or "t interval" in m for m in messages)


def test_unit_gc_t_for_all_averaged_metrics_exist_and_follow_the_formula(cfg):
    rows = []
    for i in range(10):
        rows.append(gc_observation(strategy="base", rerun=f"r{i}", threads=1, score=100 + i,
                                   gc_alloc_rate_norm=str(1000 + 100 * i), gc_count=str(100 + 10 * i),
                                   gc_time=str(2500 + 100 * i), pauses_max=str(5 + i), pauses_p99=str(2 + i)))
    _, averages = gc_metrics.prepare(pd.DataFrame(rows), cfg)
    row = averages.iloc[0]
    observations = gc_metrics.prepare(pd.DataFrame(rows), cfg)[0]
    for metric in gc_metrics.AVERAGED:
        assert f"{metric}_t_low" in averages and f"{metric}_t_high" in averages
        values = observations[metric].to_numpy(dtype=float)
        if values.max() - values.min() > 1e-9:
            assert (row[f"{metric}_t_low"], row[f"{metric}_t_high"]) == pytest.approx(t_by_hand(values), rel=1e-6)
        else:
            assert pd.isna(row[f"{metric}_t_low"])


def test_unit_simulation_paired_fieller_covers_about_95_percent_at_ten_reruns():
    """Bivariate normal (base, strategy), true ratio of means 2.0, correlation 0.7 and 0.9."""
    for correlation, seed in ((0.7, 11), (0.9, 12)):
        rng = np.random.default_rng(seed)
        sd_b, sd_s = 5.0, 12.0
        cov = [[sd_b ** 2, correlation * sd_b * sd_s], [correlation * sd_b * sd_s, sd_s ** 2]]
        draws = rng.multivariate_normal([100.0, 200.0], cov, size=(4000, 10))
        hits = bounded = 0
        for sample in draws:
            interval = fieller_interval(sample[:, 1], sample[:, 0])
            if interval is not None:
                bounded += 1
                hits += interval[0] <= 2.0 <= interval[1]
        assert bounded == 4000  # base mean is 20 SDs from zero: always bounded
        assert 0.935 <= hits / bounded <= 0.965, (correlation, hits / bounded)


# ================================================================================ integration

def test_integration_throughput_avg_csv_gains_nothing_and_has_literal_values(cfg, tmp_path):
    frame = pd.DataFrame([observation(rerun=f"r{i}", score=s) for i, s in enumerate(TEN)])
    frame.to_csv(cfg.ledger, index=False)
    paths = thrpt.run(cfg)
    table = pd.read_csv(next(p for p in paths if p.name.endswith("_avg.csv")))
    assert table.columns.tolist() == THRPT_AVG_COLUMNS
    row = table.iloc[0]
    assert row.score == 145
    assert (row.t_low, row.t_high) == pytest.approx((123.34, 166.66), abs=0.01)
    assert row.interval_kind == "95% t interval of reruns"
    assert row.ci_method == "95% percentile bootstrap of reruns" and row.ci_resamples == 10000
    assert pd.notna(row.ci_low) and pd.notna(row.ci_high) and pd.notna(row.ci_seed)


def test_integration_speedup_avg_csv_gains_only_the_two_fieller_columns(cfg):
    rows = [r for i in range(10) for r in (observation(rerun=f"r{i}", score=BASE[i]),
                                          observation("1a", f"r{i}", 2, STRATEGY[i]))]
    pd.DataFrame(rows).to_csv(cfg.ledger, index=False)
    paths = speedup.run(cfg)
    table = pd.read_csv(next(p for p in paths if p.name.endswith("_avg.csv")), dtype={"strategy": str})
    assert table.columns.tolist() == SPEEDUP_AVG_COLUMNS
    assert [c for c in table.columns if c.startswith("fieller")] == ["fieller_low", "fieller_high"]
    one_a = table[table.strategy.eq("1a")].iloc[0]
    assert one_a.speedup == pytest.approx(2.0)
    assert (one_a.fieller_low, one_a.fieller_high) == pytest.approx((1.98867, 2.01105), abs=1e-5)
    assert (one_a.mean_score, one_a.mean_baseline_score) == (pytest.approx(200.0), pytest.approx(100.0))
    assert one_a.n_reruns == one_a.total_reruns == 10
    base = table[table.strategy.eq("base")].iloc[0]
    assert base.speedup == 1 and base[["fieller_low", "fieller_high", "ci_low", "t_low"]].isna().all()


def test_integration_gc_avg_csv_gains_only_t_columns(cfg):
    pd.DataFrame(ten_rerun_rows(extra_strategies=False)).to_csv(cfg.ledger, index=False)
    paths = gc_metrics.run(cfg)
    table = pd.read_csv(next(p for p in paths if p.name.endswith("_avg.csv")), dtype={"strategy": str})
    assert table.columns.tolist() == GC_COLUMNS_BEFORE + GC_T_COLUMNS
    base = table[table.strategy.eq("base")].iloc[0]
    assert (base.gc_count_t_low, base.gc_count_t_high) == pytest.approx((123.34, 166.66), abs=0.01)
    assert (base.gc_alloc_rate_norm, base.gc_alloc_rate_norm_t_low, base.gc_alloc_rate_norm_t_high) == \
        pytest.approx((1450, *t_by_hand([1000 + 100 * i for i in range(10)])), rel=1e-6)
    assert pd.notna(base.gc_alloc_rate_norm_ci_low)
    assert base.score == pytest.approx(100000.0)  # mean of BASE (100.0) x 1000
    assert pd.isna(base.gc_time_t_low)  # constant gc_time: no spread
    pivot = pd.read_csv(next(p for p in paths if p.name.endswith("gc_alloc_rate_norm_pivot.csv")), index_col=0)
    assert pivot.loc["base", "1"] == 1450 and not any("_t_" in c for c in pivot.columns)


# ================================================================================ feature

def test_feature_throughput_bars_titles_axis_and_per_rerun_outputs(tmp_path, monkeypatch):
    pd.DataFrame(ten_rerun_rows()).to_csv(tmp_path / "ledger.csv", index=False)
    config = project(tmp_path, tmp_path / "ledger.csv")
    code, paths, figures = run_entry(monkeypatch, config, "main", thrpt)
    assert code == 0
    avg = csv_for(paths, "_avg.csv")
    fig = figures[f"thrpt_{BENCH}_avg"]
    same_bars(bars(fig), pairs(avg, "t_low", "t_high"))
    assert len(bars(fig)) == 3
    assert fig.axes[0].get_title().split("\n") == [f"{BENCH} — throughput, mean of reruns",
                                                   "Error bars: 95% t interval of reruns"]
    assert (avg.interval_kind == "95% t interval of reruns").all()
    assert fig.axes[0].get_ylim()[0] == 0
    skew = avg[avg.strategy.eq("2a")].iloc[0]
    assert skew.t_low < 0 and skew.t_low == pytest.approx(t_by_hand(SKEW)[0])  # the CSV keeps the true value
    assert min(low for low, _ in bars(fig)) < 0  # and the bar really goes below zero while the axis stays at 0
    # Per-rerun plots keep the ledger's own 99.9% bounds.
    rerun = figures[f"thrpt_{BENCH}_r01"]
    assert rerun.axes[0].get_title().split("\n")[1] == "Error bars: 99.9% ledger confidence bounds"
    same_bars(bars(rerun), sorted((s * 0.9, s * 1.1) for s in (BASE[0] * 1000, STRATEGY[0] * 1000, 1)))


def test_feature_speedup_bars_titles_axis_and_per_rerun_outputs(tmp_path, monkeypatch):
    pd.DataFrame(ten_rerun_rows()).to_csv(tmp_path / "ledger.csv", index=False)
    config = project(tmp_path, tmp_path / "ledger.csv")
    code, paths, figures = run_entry(monkeypatch, config, "speedup_main", speedup)
    assert code == 0
    avg = csv_for(paths, "_avg.csv")
    fig = figures[f"speedup_{BENCH}_avg"]
    same_bars(bars(fig), pairs(avg, "fieller_low", "fieller_high"))
    one_a = avg[avg.strategy.eq("1a")].iloc[0]
    assert (one_a.fieller_low, one_a.fieller_high) == pytest.approx((1.98867, 2.01105), abs=1e-5)
    assert fig.axes[0].get_title().split("\n") == [f"{BENCH} — speedup, mean of reruns",
                                                   "Error bars: 95% paired Fieller interval of reruns"]
    assert fig.axes[0].get_ylim()[0] == 0
    skew = avg[avg.strategy.eq("2a")].iloc[0]
    assert skew.fieller_low < 0  # CSV keeps the negative bound
    assert min(low for low, _ in bars(fig)) < 0
    rerun = figures[f"speedup_{BENCH}_r01"]
    assert bars(rerun) == [] and "Error bars" not in rerun.axes[0].get_title()
    assert "fieller_low" not in pd.read_csv(next(p for p in paths if p.name.endswith("_r01.csv"))).columns
    # Style of the bars is the one the plots had.
    collection = next(c for c in fig.axes[0].collections if isinstance(c, LineCollection))
    assert collection.get_linewidth()[0] == 1.2 and collection.get_zorder() == 6


def test_feature_gc_alloc_bars_titles_axis_and_pauses_plot(tmp_path, monkeypatch):
    pd.DataFrame(ten_rerun_rows()).to_csv(tmp_path / "ledger.csv", index=False)
    config = project(tmp_path, tmp_path / "ledger.csv")
    code, paths, figures = run_entry(monkeypatch, config, "gc_main", gc_metrics)
    assert code == 0
    avg = csv_for(paths, "_avg.csv")
    fig = figures[f"gc_{BENCH}_gc_alloc_rate_norm"]
    same_bars(bars(fig), pairs(avg, "gc_alloc_rate_norm_t_low", "gc_alloc_rate_norm_t_high"))
    assert len(bars(fig)) == 3
    assert fig.axes[0].get_title().split("\n") == [f"{BENCH} — gc_alloc_rate_norm, mean of reruns",
                                                   "Error bars: 95% t interval of reruns"]
    assert fig.axes[0].get_ylim()[0] == 0
    skew = avg[avg.strategy.eq("2a")].iloc[0]
    assert skew.gc_alloc_rate_norm_t_low < 0 and skew.gc_alloc_rate_norm_ci_low > 0  # bootstrap stays inside the data
    assert min(low for low, _ in bars(fig)) < 0
    assert f"gc_{BENCH}_pauses_max" in figures


def _raw3_checks(tmp_path, monkeypatch):
    config = project(tmp_path, RAW3, benchmarks=(BENCH, BENCH2), delimiter="auto")
    for name, module, stem, low, high, title in (
            ("main", thrpt, "thrpt", "t_low", "t_high", "95% t interval of reruns"),
            ("speedup_main", speedup, "speedup", "fieller_low", "fieller_high", FIELLER_METHOD),
            ("gc_main", gc_metrics, "gc", "gc_alloc_rate_norm_t_low", "gc_alloc_rate_norm_t_high", T_METHOD)):
        code, paths, figures = run_entry(monkeypatch, config, name, module)
        assert code == 0
        monkeypatch.undo()
        for benchmark in (BENCH, BENCH2):
            avg = csv_for(paths, f"{stem}_{benchmark}_avg.csv")
            figure = figures[f"{stem}_{benchmark}_gc_alloc_rate_norm" if stem == "gc" else f"{stem}_{benchmark}_avg"]
            same_bars(bars(figure), pairs(avg, low, high))
            lines = figure.axes[0].get_title().split("\n")
            assert lines[1] == f"Error bars: {title}" if bars(figure) else len(lines) == 1
            assert figure.axes[0].get_ylim()[0] == 0
            assert len(avg) == len(avg.drop_duplicates(["strategy", "threads_blobshuffle"]))


@pytest.mark.skipif(not RAW3.is_file(), reason="docs/raw3.csv is not present")
def test_feature_real_format_ledger_raw3(tmp_path, monkeypatch):
    _raw3_checks(tmp_path, monkeypatch)


# ================================================================================ spec: throughput

def thrpt_avg(scores, config):
    frame = pd.DataFrame([observation(rerun=f"r{i:02d}", score=s) for i, s in enumerate(scores)])
    return thrpt.prepare(frame, config)[1].iloc[0]


def test_spec_throughput_two_reruns(cfg):
    row = thrpt_avg([100, 200], cfg)
    assert (row.ci_low, row.ci_high) == (100, 200)


def test_spec_throughput_t_interval(cfg):
    row = thrpt_avg(TEN, cfg)
    assert (row.t_low, row.t_high) == pytest.approx((123.34, 166.66), abs=0.01)
    assert (row.t_low, row.t_high) == pytest.approx(145 + np.array([-1, 1]) * 2.262157 * 30.2765 / math.sqrt(10), abs=1e-3)


def test_spec_throughput_reproducible(cfg):
    a, b = thrpt_avg(TEN, cfg), thrpt_avg(TEN, cfg)
    assert (a.ci_low, a.ci_high, a.t_low, a.t_high, a.ci_seed) == (b.ci_low, b.ci_high, b.t_low, b.t_high, b.ci_seed)


def test_spec_throughput_row_order(cfg):
    frame = pd.DataFrame([observation(rerun=f"r{i:02d}", score=s) for i, s in enumerate(TEN)])
    ordered = thrpt.prepare(frame, cfg)[1].iloc[0]
    shuffled = thrpt.prepare(frame.sample(frac=1, random_state=3), cfg)[1].iloc[0]
    for column in ("ci_low", "ci_high", "t_low", "t_high"):
        assert ordered[column] == shuffled[column]
    assert ordered.contributing_reruns == json.dumps([f"r{i:02d}" for i in range(10)])
    assert shuffled.contributing_reruns != ordered.contributing_reruns  # keeps ledger order


def test_spec_throughput_plot_draws_the_t_interval(cfg):
    frame = pd.DataFrame([observation(rerun=f"r{i:02d}", score=s) for i, s in enumerate(TEN)])
    _, summary = thrpt.prepare(frame, cfg)
    fig = thrpt.render(summary, cfg, "t", strategies=["base"], threads=[1])
    same_bars(bars(fig), [(123.34, 166.66)], atol=0.01)
    assert fig.axes[0].get_title().endswith("Error bars: 95% t interval of reruns")
    assert summary.interval_kind.iloc[0] == "95% t interval of reruns"


def test_spec_throughput_lower_bound_below_zero(cfg, tmp_path):
    frame = pd.DataFrame([observation(rerun=f"r{i:02d}", score=s) for i, s in enumerate(SKEW)])
    frame.to_csv(cfg.ledger, index=False)
    paths = thrpt.run(cfg)
    avg = pd.read_csv(next(p for p in paths if p.name.endswith("_avg.csv")))
    assert avg.t_low.iloc[0] == pytest.approx(t_by_hand(SKEW)[0], abs=1e-3) and avg.t_low.iloc[0] < 0
    summary = thrpt.prepare(frame, cfg)[1]
    fig = thrpt.render(summary, cfg, "t", strategies=["base"], threads=[1])
    assert fig.axes[0].get_ylim()[0] == 0


# ================================================================================ spec: gc

def alloc_group(values, cfg, **extra):
    rows = [gc_observation(strategy="base", rerun=f"r{i}", threads=1, gc_alloc_rate_norm=v, **extra)
            for i, v in enumerate(values)]
    return collect(gc_metrics.prepare, pd.DataFrame(rows), cfg)


def test_spec_gc_no_spread(cfg):
    (_, averages), messages = alloc_group(["1000"] * 3, cfg)
    row = averages.iloc[0]
    assert pd.isna(row.gc_alloc_rate_norm_t_low) and pd.isna(row.gc_alloc_rate_norm_ci_low)
    assert [m for m in messages if "gc_alloc_rate_norm" in m] == [
        f"No gc_alloc_rate_norm CI for {BENCH}/base/threads=1: fewer than two reruns or no spread"]
    fig = gc_metrics.render_alloc(averages, cfg, BENCH, strategies=["base"], threads=[1])
    assert bars(fig) == [] and len(fig.axes[0].patches) == 1
    assert fig.axes[0].get_title() == f"{BENCH} — gc_alloc_rate_norm, mean of reruns"


def test_spec_gc_blank_rerun_value(cfg):
    (_, averages), messages = alloc_group(["1000", "", "1400"], cfg)
    row = averages.iloc[0]
    assert pd.isna(row.gc_alloc_rate_norm) and pd.isna(row.gc_alloc_rate_norm_ci_low)
    assert pd.isna(row.gc_alloc_rate_norm_t_low) and pd.isna(row.gc_alloc_rate_norm_t_high)
    assert any(m.startswith("Mean of reruns of gc_alloc_rate_norm missing for") and "base" in m for m in messages)
    fig = gc_metrics.render_alloc(averages, cfg, BENCH, strategies=["base"], threads=[1])
    assert len(fig.axes[0].patches) == 0 and bars(fig) == []


def test_spec_gc_row_order(cfg):
    rows = [gc_observation(strategy="base", rerun=f"r{i:02d}", threads=1, gc_alloc_rate_norm=str(1000 + 100 * i))
            for i in range(10)]
    frame = pd.DataFrame(rows)
    ordered = gc_metrics.prepare(frame, cfg)[1].iloc[0]
    shuffled = gc_metrics.prepare(frame.sample(frac=1, random_state=5), cfg)[1].iloc[0]
    assert (ordered.gc_alloc_rate_norm_ci_low, ordered.gc_alloc_rate_norm_ci_high) == \
        (shuffled.gc_alloc_rate_norm_ci_low, shuffled.gc_alloc_rate_norm_ci_high)
    assert (ordered.gc_alloc_rate_norm_t_low, ordered.gc_alloc_rate_norm_t_high) == \
        (shuffled.gc_alloc_rate_norm_t_low, shuffled.gc_alloc_rate_norm_t_high)


def test_spec_gc_plot_draws_the_t_interval(cfg):
    (_, averages), _ = alloc_group([str(1000 + 100 * i) for i in range(10)], cfg)
    fig = gc_metrics.render_alloc(averages, cfg, BENCH, strategies=["base"], threads=[1])
    low, high = t_by_hand([1000 + 100 * i for i in range(10)])
    same_bars(bars(fig), [(low, high)])
    assert fig.axes[0].get_title().split("\n")[1] == "Error bars: 95% t interval of reruns"
    row = averages.iloc[0]
    assert (row.gc_alloc_rate_norm_ci_low, row.gc_alloc_rate_norm_ci_high) != (low, high)  # bootstrap kept apart


def test_spec_gc_mean_over_reruns(cfg):
    (_, averages), _ = alloc_group(["1000", "1200", "1400"], cfg)
    row = averages.iloc[0]
    assert row.gc_alloc_rate_norm == 1200 and row.n_reruns == 3


def test_spec_gc_incomplete_coverage(cfg):
    rows = [gc_observation(strategy="base", rerun=r, threads=1) for r in ("r1", "r2", "r3")]
    rows += [gc_observation(strategy="1a", rerun=r, threads=4, gc_alloc_rate_norm=v)
             for r, v in (("r1", "1000"), ("r2", "1200"))]
    (_, averages), messages = collect(gc_metrics.prepare, pd.DataFrame(rows), cfg)
    row = averages[averages.strategy.eq("1a")].iloc[0]
    assert (row.n_reruns, row.total_reruns) == (2, 3) and row.gc_alloc_rate_norm == 1100
    assert any("missing" in m and "1a_n04_" in m for m in messages)


def test_spec_gc_contributor_lacks_a_metric(cfg):
    rows = [gc_observation(strategy="base", rerun=f"r{i}", threads=1, pauses_max="" if i == 1 else "5")
            for i in range(3)]
    (_, averages), _ = collect(gc_metrics.prepare, pd.DataFrame(rows), cfg)
    row = averages.iloc[0]
    assert pd.isna(row.pauses_max) and pd.isna(row.pauses_max_t_low)
    assert row.gc_count == 10 and row.gc_time == 2500


def test_spec_gc_t_columns(cfg):
    rows = [gc_observation(strategy="base", rerun=f"r{i}", threads=1, gc_count=str(100 + 10 * i)) for i in range(10)]
    row = gc_metrics.prepare(pd.DataFrame(rows), cfg)[1].iloc[0]
    assert (row.gc_count_t_low, row.gc_count_t_high) == pytest.approx((123.34, 166.66), abs=0.01)


# ================================================================================ spec: speedup

def test_spec_speedup_pairing_is_kept(cfg):
    frame = pd.DataFrame([observation(rerun="a", score=100), observation("1a", "a", 2, 200),
                          observation(rerun="b", score=200), observation("1a", "b", 2, 300)])
    row = speedup.prepare(frame, cfg)[1].query("strategy == '1a'").iloc[0]
    assert (row.ci_low, row.ci_high) == (1.5, 2)


def test_spec_speedup_per_rerun_plots(cfg):
    frame = pd.DataFrame([r for i in range(3) for r in (observation(rerun=f"r{i}", score=BASE[i]),
                                                       observation("1a", f"r{i}", 2, STRATEGY[i]))])
    observations, _ = speedup.prepare(frame, cfg)
    fig = speedup.render(observations[observations.rerun.eq("r0")], cfg, "r0", strategies=["base", "1a"],
                         threads=[1, 2])
    assert bars(fig) == [] and "Error bars" not in fig.axes[0].get_title()
    assert not {"fieller_low", "ci_low", "t_low"} & set(observations.columns)


def test_spec_speedup_row_order(cfg):
    frame = pd.DataFrame([r for i in range(10) for r in (observation(rerun=f"r{i:02d}", score=BASE[i]),
                                                        observation("1a", f"r{i:02d}", 2, STRATEGY[i]))])
    ordered = speedup.prepare(frame, cfg)[1].query("strategy == '1a'").iloc[0]
    shuffled = speedup.prepare(frame.sample(frac=1, random_state=9), cfg)[1].query("strategy == '1a'").iloc[0]
    for column in ("ci_low", "ci_high", "t_low", "t_high", "fieller_low", "fieller_high", "speedup"):
        assert ordered[column] == shuffled[column]


def test_spec_speedup_paired_fieller(cfg):
    frame = pd.DataFrame([r for i in range(10) for r in (observation(rerun=f"r{i:02d}", score=BASE[i]),
                                                        observation("1a", f"r{i:02d}", 2, STRATEGY[i]))])
    row = speedup.prepare(frame, cfg)[1].query("strategy == '1a'").iloc[0]
    assert row.speedup == pytest.approx(2.0)
    assert (row.fieller_low, row.fieller_high) == pytest.approx((1.98867, 2.01105), abs=1e-5)


def test_spec_speedup_interval_cannot_be_bounded(cfg):
    base, strategy = [1, 100, 1, 100], [2, 200, 2, 200.5]
    frame = pd.DataFrame([r for i in range(4) for r in (observation(rerun=f"r{i}", score=base[i]),
                                                       observation("1a", f"r{i}", 2, strategy[i]))])
    (_, summary), messages = collect(speedup.prepare, frame, cfg)
    row = summary.query("strategy == '1a'").iloc[0]
    assert pd.isna(row.fieller_low) and pd.isna(row.fieller_high)
    assert pd.notna(row.speedup) and pd.notna(row.ci_low) and pd.notna(row.t_low)  # other columns stay
    assert [m for m in messages if "Fieller" in m and f"{BENCH}/1a/threads=2" in m]
    fig = speedup.render(summary, cfg, "t", strategies=["base", "1a"], threads=[1, 2])
    assert bars(fig) == []
    assert "Error bars" not in fig.axes[0].get_title()
    assert len(fig.axes[0].lines) >= 1  # the point is still drawn


def test_spec_speedup_base_against_itself(cfg):
    frame = pd.DataFrame([observation(rerun=f"r{i}", score=BASE[i]) for i in range(10)])
    (_, summary), messages = collect(speedup.prepare, frame, cfg)
    row = summary.iloc[0]
    assert row.speedup == 1 and pd.isna(row.fieller_low) and pd.isna(row.fieller_high)
    assert messages == []


def test_spec_speedup_yerr_is_never_negative(cfg):
    # A point nudged outside its bar must still draw with a non-negative error length.
    frame = pd.DataFrame([r for i in range(10) for r in (observation(rerun=f"r{i:02d}", score=BASE[i]),
                                                        observation("1a", f"r{i:02d}", 2, STRATEGY[i]))])
    _, summary = speedup.prepare(frame, cfg)
    summary.loc[summary.strategy.eq("1a"), "speedup"] += 1.0  # point above fieller_high
    fig = speedup.render(summary, cfg, "t", strategies=["base", "1a"], threads=[1, 2])
    (low, high), = bars(fig)
    assert low <= high  # matplotlib would raise on a negative yerr


# ================================================================================ spec: compare

def rows_2a_2b(a, b, threads=(1,)):
    return pd.DataFrame([observation(strategy, f"{i + 1:02d}", t, score)
                         for t in threads for i, (x, y) in enumerate(zip(a, b))
                         for strategy, score in (("2a", x), ("2b", y))])


A = [101, 103, 98, 102, 100, 103, 98, 101, 102, 100]
B = [100, 102, 98, 101, 99, 103, 97, 100, 102, 98]


def compare_row(a, b, margin, threads=(1,), thread_count=None):
    config = Config(Path("l.csv"), Path("out"), benchmarks=(BENCH,), comparison_margin=margin)
    table, messages = collect(compare.prepare, rows_2a_2b(a, b, threads), config)
    return table[BENCH].iloc[0], messages


def test_spec_compare_equivalent_and_different():
    stats = compare.paired_statistics(A, B, 5)
    assert compare.decide(stats, 0.05) == ("equivalent", "2A 0.8% faster, below margin")


def test_spec_compare_no_margin():
    stats = compare.paired_statistics(A, B, 5)
    assert compare.decide(stats, None)[0] == "2A faster"
    _, messages = compare_row(A, B, None)
    assert [m for m in messages if "equivalence cannot be claimed" in m] == ["No comparison margin set: equivalence cannot be claimed"]


def test_spec_compare_faster_at_the_edge():
    a = [105, 97, 103, 98, 97, 101, 98, 100, 97, 103]
    b = [104, 96, 102, 97, 96, 100, 98, 98, 96, 104]
    stats = compare.paired_statistics(a, b, 5)
    assert stats["diff_low"] == pytest.approx(1.000039, abs=5e-6)
    assert compare.decide(stats, None)[0] == "2A faster"


def test_spec_compare_not_within_margin():
    a = [99, 100, 104, 104, 100, 99, 99, 104, 100, 99]
    b = [100, 98, 104, 103, 101, 98, 96, 103, 98, 100]
    stats = compare.paired_statistics(a, b, 5)
    assert stats["equiv_high"] == pytest.approx(1.020070, abs=5e-6)
    assert compare.decide(stats, 0.02)[0] != "equivalent"


def test_spec_compare_no_spread_from_rounding_keeps_the_configured_margin():
    a = [1.1, 2.2, 3.3, 4.4, 5.5, 6.6]
    b = [x / 1.1 for x in a]
    row, messages = compare_row(a, b, 0.05)
    assert row.verdict == "no spread observed"
    assert pd.isna(row.diff_low) and pd.isna(row.diff_high) and pd.isna(row.smallest_provable_margin)
    assert row.margin == 0.05
