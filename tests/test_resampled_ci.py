"""Resampled CI regressions written from the add-resampled-ci spec only.

Every expected value is a literal. Bootstrap literals were calculated once with a
separate numpy script that follows the contract recipe (default_rng(seed), an
n x 10000 index matrix from `integers`, `numpy.quantile` at 0.025 / 0.975).
"""
from dataclasses import replace
import hashlib
import random
import re
import warnings

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest
from matplotlib.colors import to_rgba

from conftest import observation
from src import speedup, thrpt
from src.utils import (bootstrap_ci, cell_seed, has_spread, t_interval, t_quantile)

METHOD = "95% percentile bootstrap of reruns"
T_METHOD = "95% t interval of reruns"
CI_COLUMNS = ["ci_low", "ci_high", "t_low", "t_high", "ci_method", "ci_resamples", "ci_seed"]
SPEEDUP_COLUMNS = ["ci_low", "ci_high", "t_low", "t_high", "fieller_low", "fieller_high",
                   "ci_method", "ci_resamples", "ci_seed"]
BENCH = "mb1_rawNoFinalize"
# cell_seed(BENCH, "base", 1, "score") and cell_seed(BENCH, "1a", 2, "speedup"),
# from hashlib in the independent script (see test_cell_seed_literal for the recipe).
SEED_BASE_1_SCORE = 5721083545952246177
SEED_1A_2_SPEEDUP = 7026562183407568956


def collect(function, *args, **kwargs):
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        result = function(*args, **kwargs)
    return result, [str(item.message) for item in seen]


def segments(ax):
    """Finite error-bar line segments (n x 2 arrays of x,y points) on an axes."""
    found = [np.asarray(s) for c in ax.collections if hasattr(c, "get_segments") for s in c.get_segments()]
    return [s for s in found if not np.isnan(s).all()]


# ----------------------------------------------------------------- helpers

@pytest.mark.parametrize("df,expected", [
    (1, 12.7062),     # table value t(0.975, 1)
    (2, 4.30265),     # table value t(0.975, 2)
    (9, 2.26216),     # table value t(0.975, 9)
    (29, 2.04523),    # table value t(0.975, 29)
])
def test_t_quantile_table_values(df, expected):
    assert t_quantile(0.975, df) == pytest.approx(expected, abs=1e-4)


def test_t_quantile_is_symmetric_and_centered():
    assert t_quantile(0.5, 5) == pytest.approx(0.0, abs=1e-6)
    assert t_quantile(0.025, 9) == pytest.approx(-2.26216, abs=1e-4)


def test_t_interval_hundred_to_hundred_ninety():
    # mean 145, sample SD 30.2765, t(9) 2.262157: 145 -/+ 2.262157 * 30.2765 / sqrt(10) = 145 -/+ 21.658
    low, high = t_interval([100, 110, 120, 130, 140, 150, 160, 170, 180, 190])
    assert low == pytest.approx(123.342, abs=0.01)
    assert high == pytest.approx(166.658, abs=0.01)


def test_t_interval_two_values():
    # mean 15, SD sqrt(50) = 7.0711, SE = 7.0711 / sqrt(2) = 5; t(1) = 12.7062; half = 63.531
    low, high = t_interval([10, 20])
    assert low == pytest.approx(-48.531, abs=0.01)
    assert high == pytest.approx(78.531, abs=0.01)


def test_t_interval_three_values_and_level():
    # mean 2, SD 1, SE 1 / sqrt(3) = 0.57735; t(0.975, 2) = 4.30265 -> half 2.48414
    low, high = t_interval([1, 2, 3])
    assert (low, high) == (pytest.approx(-0.48414, abs=1e-3), pytest.approx(4.48414, abs=1e-3))
    # level 0.90 -> t(0.95, 2) = 2.91999; half = 2.91999 * 0.57735 = 1.68588
    low, high = t_interval([1, 2, 3], level=0.9)
    assert (low, high) == (pytest.approx(0.31412, abs=1e-3), pytest.approx(3.68588, abs=1e-3))


def test_cell_seed_literal():
    # Recipe: sha256(b"42|a|b").hexdigest() starts with 90c4afad93af8e4f (computed with hashlib
    # outside the project). The top bit is set, so the modulo 2**63 matters:
    # 0x90c4afad93af8e4f - 2**63 = 0x10c4afad93af8e4f = 1208283760084094543.
    assert cell_seed("a", "b") == 1208283760084094543
    # The same independent calculation with non-string parts joined by str().
    assert cell_seed(BENCH, "base", 1, "score") == SEED_BASE_1_SCORE


def test_cell_seed_deterministic_and_part_sensitive():
    assert cell_seed("a", "b", 1) == cell_seed("a", "b", 1)
    assert cell_seed("a", "b", 1) != cell_seed("a", "b", 2)
    assert cell_seed("a", "b", 1) != cell_seed("a", "c", 1)
    assert cell_seed("a", "b", 1, "score") != cell_seed("a", "b", 1, "speedup")
    assert 0 <= cell_seed("anything") < 2 ** 63
    # The base seed 42 is part of the hash input.
    assert cell_seed("a", "b") != int.from_bytes(hashlib.sha256(b"a|b").digest()[:8], "big") % 2 ** 63


def test_bootstrap_two_values():
    # Resampled means are 100, 150 or 200; the 2.5% / 97.5% quantiles are the extremes.
    low, high = bootstrap_ci([100, 200], seed=1)
    assert (low, high) == (pytest.approx(100.0), pytest.approx(200.0))


def test_bootstrap_pair_case_keeps_pairing():
    # Pairs (200,100) and (300,200). Same index for both: ratios 200/100 = 2, 500/300, 300/200 = 1.5.
    # UNPAIRED indices could also give 200/200 = 1 or 300/100 = 3, so an unpaired
    # implementation would report about [1, 3], not [1.5, 2].
    low, high = bootstrap_ci([200, 300], [100, 200], seed=5)
    assert (low, high) == (pytest.approx(1.5), pytest.approx(2.0))


def test_bootstrap_paired_five_pairs_differs_from_unpaired_and_mean_of_ratios():
    # Independent script: ratio-of-sums with shared indices, seed 21:
    # [1.765957446809, 2.962025316456]. Unpaired indices would give [1.6633, 2.9863];
    # mean of per-rerun ratios would give [1.8476, 3.0595].
    numerators = [200, 300, 250, 260, 310, 180, 420]
    denominators = [100, 200, 100, 150, 120, 90, 100]
    low, high = bootstrap_ci(numerators, denominators, seed=21)
    assert low == pytest.approx(1.765957446809, abs=1e-9)
    assert high == pytest.approx(2.962025316456, abs=1e-9)


def test_bootstrap_matches_independent_loop_with_contract_recipe():
    values = np.array([103.5, 98.2, 121.7, 87.4, 110.9, 95.3, 130.1, 102.6, 118.8, 91.0, 107.2, 99.9])
    rng = np.random.default_rng(11)
    index = rng.integers(0, len(values), size=(len(values), 10000))
    reference = np.quantile(values[index].mean(axis=0), [0.025, 0.975])
    low, high = bootstrap_ci(values, seed=11)
    assert (low, high) == (pytest.approx(reference[0], rel=1e-12), pytest.approx(reference[1], rel=1e-12))
    # The same recipe, evaluated once outside the test run.
    assert low == pytest.approx(98.958333333333, abs=1e-9)
    assert high == pytest.approx(112.6, abs=1e-9)


def test_bootstrap_repeat_identical_and_other_seed_differs():
    values = [103.5, 98.2, 121.7, 87.4, 110.9, 95.3, 130.1, 102.6, 118.8, 91.0, 107.2, 99.9]
    first, second = bootstrap_ci(values, seed=11), bootstrap_ci(values, seed=11)
    assert first == second
    other = bootstrap_ci(values, seed=12)
    assert other != first
    assert other[0] == pytest.approx(98.799375, abs=1e-9)  # independent script, seed 12


def test_bootstrap_stays_within_possible_statistics():
    # The statistic is a ratio of sums, so it lies between the smallest and largest per-pair ratio.
    numerators, denominators = [200, 300, 250, 260], [100, 200, 100, 150]
    ratios = [2.0, 1.5, 2.5, 260 / 150]
    low, high = bootstrap_ci(numerators, denominators, seed=3)
    assert min(ratios) <= low <= high <= max(ratios)


def test_bootstrap_custom_resamples_and_level_accepted():
    low, high = bootstrap_ci([100, 200], seed=1, resamples=500, level=0.9)
    assert (low, high) == (pytest.approx(100.0), pytest.approx(200.0))


def test_has_spread_rules():
    assert not has_spread([5.0])
    assert not has_spread([])
    assert not has_spread([500, 500])
    assert not has_spread([0, 0])
    # mean ~100, tolerance 1e-9 * 100 = 1e-7. Difference 5e-8 is within, 5e-7 is beyond.
    assert not has_spread([100, 100 + 5e-8])
    assert has_spread([100, 100 + 5e-7])
    assert has_spread([1, 2])


# ------------------------------------------------------ throughput tables

def two_reruns(strategy="base", threads=1, scores=(100, 200)):
    return [observation(strategy, str(i), threads, s) for i, s in enumerate(scores)]


def test_throughput_across_reruns_literal_ci_and_columns(config):
    _, result = thrpt.prepare(pd.DataFrame(two_reruns()), config)
    row = result.iloc[0]
    assert row.ci_low == pytest.approx(100.0) and row.ci_high == pytest.approx(200.0)
    # mean 150, SD 70.7107, SE 50, t(1) 12.7062 -> 150 -/+ 635.31
    assert row.t_low == pytest.approx(-485.31, abs=0.01)
    assert row.t_high == pytest.approx(785.31, abs=0.01)
    assert row.ci_method == METHOD
    assert row.ci_resamples == 10000
    # Seeds exceed 2**53, and a pandas row (or a NaN-bearing column) is float64, so compare relatively.
    assert row.ci_seed == pytest.approx(SEED_BASE_1_SCORE, rel=1e-15)
    assert row.interval_kind == T_METHOD
    assert row.score == 150
    columns = result.columns.tolist()
    start = columns.index("ci_low")
    assert columns[start:start + 7] == CI_COLUMNS
    assert start > columns.index("score")
    assert "score_confidence_low" not in columns and "score_confidence_high" not in columns


def test_throughput_ten_reruns_t_interval_and_bootstrap(config):
    scores = list(range(100, 200, 10))
    _, result = thrpt.prepare(pd.DataFrame(two_reruns(scores=scores)), config)
    row = result.iloc[0]
    assert row.t_low == pytest.approx(123.342, abs=0.01) and row.t_high == pytest.approx(166.658, abs=0.01)
    # Independent script: default_rng(SEED_BASE_1_SCORE), 10 x 10000 indices, quantiles of the means.
    assert row.ci_low == pytest.approx(127.0, abs=1e-9) and row.ci_high == pytest.approx(163.0, abs=1e-9)


def test_throughput_no_spread_warns_and_leaves_fields_empty(config):
    frame = pd.DataFrame(two_reruns(scores=(500, 500)))
    (_, result), messages = collect(thrpt.prepare, frame, config)
    assert f"No CI for {BENCH}/base/threads=1: fewer than two reruns or no spread" in messages
    assert result[CI_COLUMNS].isna().all().all()
    assert result.score.iloc[0] == 500


def test_throughput_one_rerun_has_no_ci_and_warns(config):
    (_, result), messages = collect(thrpt.prepare, pd.DataFrame([observation()]), config)
    assert f"No CI for {BENCH}/base/threads=1: fewer than two reruns or no spread" in messages
    assert result[CI_COLUMNS].isna().all().all()
    assert result.n_reruns.iloc[0] == 1


def test_throughput_partial_coverage_uses_contributing_reruns(config):
    frame = pd.DataFrame(two_reruns() + [observation("1a", "0", 2, 400), observation("1a", "1", 2, 600),
                                         observation(rerun="2", score=300)])
    (_, result), messages = collect(thrpt.prepare, frame, config)
    assert any("/thrpt missing" in text for text in messages)
    row = result[result.strategy.eq("1a")].iloc[0]
    assert row.n_reruns == 2 and row.total_reruns == 3
    # Two contributing scores 400, 600: resampled means 400, 500, 600.
    assert row.ci_low == pytest.approx(400.0) and row.ci_high == pytest.approx(600.0)


def cell_columns(result, strategy):
    return result[result.strategy.eq(strategy)][["score"] + CI_COLUMNS].reset_index(drop=True)


def test_throughput_row_order_does_not_matter(config):
    # Ten reruns with non-lattice scores on a shuffled ledger: see also tests/test_review_fixes.py, which
    # compares against a separate resampling. Three values would always give min and max, which proves nothing.
    scores = [103.7, 98.2, 121.9, 87.4, 110.3, 95.8, 130.1, 101.6, 92.7, 116.4]
    rows = [observation("base", f"{i:02d}", 1, s) for i, s in enumerate(scores, 1)] \
        + [observation("1a", f"{i:02d}", 2, 4 * s) for i, s in enumerate(scores, 1)]
    order = list(range(len(rows)))
    random.Random(3).shuffle(order)
    _, forward = thrpt.prepare(pd.DataFrame(rows), config)
    _, backward = thrpt.prepare(pd.DataFrame([rows[i] for i in order]), config)
    for strategy in ("base", "1a"):
        pd.testing.assert_frame_equal(cell_columns(forward, strategy), cell_columns(backward, strategy))
    assert forward[forward.strategy.eq("1a")].ci_low.notna().all()


def test_throughput_adding_a_strategy_leaves_other_cells_unchanged(config):
    base = two_reruns(scores=(100, 130, 170, 190))
    _, alone = thrpt.prepare(pd.DataFrame(base), config)
    extra = two_reruns("2a", 4, (900, 950, 990, 1010))
    _, together = thrpt.prepare(pd.DataFrame(base + extra), config)
    pd.testing.assert_frame_equal(cell_columns(alone, "base"), cell_columns(together, "base"))
    assert cell_columns(together, "2a").ci_seed.iloc[0] != cell_columns(together, "base").ci_seed.iloc[0]


def test_throughput_per_rerun_table_unchanged(config):
    observations, _ = thrpt.prepare(pd.DataFrame(two_reruns()), config)
    assert not set(CI_COLUMNS) & set(observations.columns)
    assert observations.interval_kind.unique().tolist() == ["ledger confidence bounds"]
    assert observations.score_confidence_low.tolist() == pytest.approx([90, 180])   # 0.9 * score
    assert observations.score_confidence_high.tolist() == pytest.approx([110, 220])  # 1.1 * score
    assert observations.n_reruns.tolist() == [1, 1]


def test_throughput_average_csv_has_empty_ci_fields_for_no_spread(config):
    pd.DataFrame(two_reruns(scores=(500, 500)) + two_reruns("1a", 2, (400, 600))).to_csv(config.ledger, index=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        paths = thrpt.run(config)
    table = pd.read_csv(next(p for p in paths if p.name.endswith("_avg.csv")))
    base, other = table[table.strategy.eq("base")].iloc[0], table[table.strategy.eq("1a")].iloc[0]
    assert base[CI_COLUMNS].isna().all()
    assert (other.ci_low, other.ci_high) == (400.0, 600.0)
    assert other.ci_method == METHOD and other.ci_resamples == 10000
    assert "score_confidence_low" not in table.columns


# -------------------------------------------------------- throughput render

def render_throughput(result, config, title="Average", **kwargs):
    return thrpt.render(result, config, title, strategies=["base", "1a"], threads=[1], **kwargs)


def test_throughput_render_bars_match_ci_and_skip_nan(config):
    frame = pd.DataFrame(two_reruns() + two_reruns("1a", 1, (500, 500)))
    (_, result), _ = collect(thrpt.prepare, frame, config)
    fig = render_throughput(result, config)
    try:
        ax = fig.axes[0]
        found = segments(ax)
        assert len(found) == 1  # "1a" has no spread, so no bar, in particular no zero-width bar
        np.testing.assert_allclose(found[0][:, 1], [-485.31023680874375, 785.3102368087438])  # t_low, t_high
        assert ax.get_title().endswith(f"Error bars: {T_METHOD}")
        assert not ax.texts  # No n labels on the plot.
    finally:
        plt.close(fig)


def test_throughput_render_ten_reruns_uses_t_interval_not_bootstrap(config):
    _, result = thrpt.prepare(pd.DataFrame(two_reruns(scores=list(range(100, 200, 10)))), config)
    fig = thrpt.render(result, config, "Average", strategies=["base"], threads=[1])
    try:
        found = segments(fig.axes[0])
        np.testing.assert_allclose(found[0][:, 1], [123.34149410331793, 166.65850589668207])
    finally:
        plt.close(fig)


def test_throughput_per_rerun_bars_still_use_ledger_bounds(config):
    frame = pd.DataFrame([observation(), observation("1a", score=500)])
    observations, _ = thrpt.prepare(frame, config)
    fig = render_throughput(observations, config, "Rerun")
    try:
        ax = fig.axes[0]
        found = sorted(segments(ax), key=lambda s: s[0, 1])
        np.testing.assert_allclose([s[:, 1] for s in found], [[90, 110], [450, 550]])
        assert ax.get_title().endswith("Error bars: 99.9% ledger confidence bounds")
    finally:
        plt.close(fig)


# ------------------------------------------------------- speedup tables

def pair_rows(extra=()):
    return pd.DataFrame([
        observation(rerun="01", score=100), observation("1a", "01", 2, 200),
        observation(rerun="02", score=200), observation("1a", "02", 2, 300), *extra,
    ])


def test_speedup_across_reruns_literal_ci_t_interval_and_columns(config):
    _, summary = speedup.prepare(pair_rows(), config)
    row = summary[summary.strategy.eq("1a")].iloc[0]
    assert row.ci_low == pytest.approx(1.5) and row.ci_high == pytest.approx(2.0)
    # Per-rerun ratios 2 and 1.5: mean 1.75, SD 0.353553, SE 0.25, t(1) 12.7062 -> half 3.17655
    assert row.t_low == pytest.approx(-1.42655, abs=1e-4) and row.t_high == pytest.approx(4.92655, abs=1e-4)
    assert row.t_basis == "per-rerun ratios"
    assert row.ci_method == METHOD and row.ci_resamples == 10000
    assert row.ci_seed == pytest.approx(SEED_1A_2_SPEEDUP, rel=1e-15)  # float64 column, see above
    assert row.speedup == pytest.approx(250 / 150)
    columns = summary.columns.tolist()
    start = columns.index("ci_low")
    assert columns[start:start + 9] == SPEEDUP_COLUMNS
    assert start > columns.index("speedup")


def test_speedup_base_at_one_thread_has_no_ci_and_no_warning(config):
    (_, summary), messages = collect(speedup.prepare, pair_rows(), config)
    base = summary[summary.strategy.eq("base")].iloc[0]
    assert base.speedup == 1.0
    assert base[CI_COLUMNS].isna().all()
    assert base.t_basis == "per-rerun ratios"
    assert not any("No CI" in text for text in messages)


def test_speedup_no_spread_warns_for_other_groups(config):
    # Strategy ratios are 200/100 = 2 and 400/200 = 2: identical, so no spread.
    frame = pd.DataFrame([observation(rerun="01", score=100), observation("1a", "01", 2, 200),
                          observation(rerun="02", score=200), observation("1a", "02", 2, 400)])
    (_, summary), messages = collect(speedup.prepare, frame, config)
    assert f"No CI for {BENCH}/1a/threads=2: fewer than two reruns or no spread" in messages
    assert not any("base/threads=1" in text for text in messages)
    row = summary[summary.strategy.eq("1a")].iloc[0]
    assert row[CI_COLUMNS].isna().all()
    assert row.t_basis == "per-rerun ratios"  # stays filled
    assert row.speedup == 2.0


def test_speedup_single_rerun_warns_for_strategy_only(config):
    frame = pd.DataFrame([observation(score=100), observation("1a", threads=2, score=250)])
    (_, summary), messages = collect(speedup.prepare, frame, config)
    assert [t for t in messages if t.startswith("No CI")] == [
        f"No CI for {BENCH}/1a/threads=2: fewer than two reruns or no spread"]
    assert summary[CI_COLUMNS].isna().all().all()


def test_speedup_partial_coverage_uses_only_contributing_reruns(config):
    # The third rerun has only a base score (900); it must not enter the bootstrap or t interval.
    frame = pair_rows([observation(rerun="03", score=900)])
    (_, summary), messages = collect(speedup.prepare, frame, config)
    assert any("/thrpt missing" in text for text in messages)
    row = summary[summary.strategy.eq("1a")].iloc[0]
    assert row.n_reruns == 2 and row.total_reruns == 3
    assert row.ci_low == pytest.approx(1.5) and row.ci_high == pytest.approx(2.0)
    assert row.t_low == pytest.approx(-1.42655, abs=1e-4)


def test_speedup_malformed_ledger_bounds_change_nothing(config):
    _, clean = speedup.prepare(pair_rows(), config)
    broken = pair_rows()
    broken["score_confidence_low"] = "bad"
    broken["score_confidence_high"] = "not-an-interval"
    broken["score_error"] = "unused"
    (_, summary), messages = collect(speedup.prepare, broken, config)
    pd.testing.assert_frame_equal(summary[SPEEDUP_COLUMNS + ["speedup"]], clean[SPEEDUP_COLUMNS + ["speedup"]])
    # Two reruns: the base mean is not told apart from zero, so only the Fieller warning appears.
    assert len(messages) == 1 and messages[0].startswith("No Fieller interval for ")


def test_speedup_per_rerun_table_unchanged(config):
    individual, _ = speedup.prepare(pair_rows(), config)
    assert not set(CI_COLUMNS) & set(individual.columns)
    assert "t_basis" not in individual.columns
    assert individual.summary_kind.unique().tolist() == ["individual paired ratio"]
    assert individual[individual.strategy.eq("1a")].speedup.tolist() == [2, 1.5]


def test_speedup_average_csv_exports_ci(config):
    pair_rows().to_csv(config.ledger, index=False)
    paths = speedup.run(config)
    average = pd.read_csv(next(p for p in paths if p.name.endswith("_avg.csv")))
    row = average[average.strategy.eq("1a")].iloc[0]
    assert (row.ci_low, row.ci_high) == (pytest.approx(1.5), pytest.approx(2.0))
    assert row.t_basis == "per-rerun ratios"
    assert average[average.strategy.eq("base")][CI_COLUMNS].isna().all().all()
    individual = pd.read_csv(next(p for p in paths if p.stem.endswith("_r01") and p.suffix == ".csv"))
    assert "ci_low" not in individual.columns


# ---------------------------------------------------------- speedup render

def speedup_inputs(config):
    # 1a at 2 threads: ratios 2 and 1.5 (CI [1.5, 2]). 2a at 2 threads: ratios 2 and 2 (no spread).
    frame = pd.DataFrame([
        observation(rerun="01", score=100), observation("1a", "01", 2, 200), observation("2a", "01", 2, 200),
        observation(rerun="02", score=200), observation("1a", "02", 2, 300), observation("2a", "02", 2, 400),
    ])
    config = replace(config, colors={"1a": "red", "2a": "blue"})
    individual, summary = speedup.prepare(frame, config)
    return config, individual, summary


def test_speedup_render_error_bars_on_across_reruns_chart(config):
    # Ten reruns, so Fieller is bounded. 1a at 2 threads has ratios 2 and about 1.5; 2a has ratio 2 throughout.
    rows = []
    for i in range(10):
        base = 100 + 10 * i
        rows += [observation(rerun=f"r{i:02d}", score=base),
                 observation("1a", f"r{i:02d}", 2, 2 * base - (30 if i % 2 else 0)),
                 observation("2a", f"r{i:02d}", 2, 2 * base)]
    config = replace(config, colors={"1a": "red", "2a": "blue"})
    _, summary = speedup.prepare(pd.DataFrame(rows), config)
    fig = speedup.render(summary, config, "Average", strategies=["base", "1a", "2a"], threads=[1, 2])
    try:
        ax = fig.axes[0]
        found = segments(ax)
        assert len(found) == 1  # only 1a at 2 threads; base and 2a have no CI
        np.testing.assert_allclose(found[0][:, 0], [2, 2])
        row = summary[summary.strategy.eq("1a")].iloc[0]
        np.testing.assert_allclose(found[0][:, 1], [row.fieller_low, row.fieller_high])
        bars = [c for c in ax.collections if hasattr(c, "get_segments") and len(c.get_segments())]
        assert len(bars) == 1
        assert tuple(bars[0].get_colors()[0]) == to_rgba("#222")  # dark bars, not the line colour
        assert bars[0].get_linewidth()[0] == 1.2
        assert "Error bars: 95% paired Fieller interval of reruns" in ax.get_title()
    finally:
        plt.close(fig)


def test_speedup_render_per_rerun_chart_has_no_error_bars(config):
    config, individual, _ = speedup_inputs(config)
    fig = speedup.render(individual[individual.rerun.eq("01")], config, "Rerun 01",
                         strategies=["base", "1a", "2a"], threads=[1, 2])
    try:
        ax = fig.axes[0]
        assert segments(ax) == []
        assert len(ax.containers) == 0
        assert "Error bars" not in ax.get_title()
    finally:
        plt.close(fig)


def test_speedup_csv_keeps_exact_seed_next_to_an_empty_one(config, tmp_path):
    # Base at N=1 has no CI, so its seed is empty. A float column would round the other
    # seed: 7026562183407... has more digits than a float64 can hold.
    rows = [observation("base", "r1", 1, 100), observation("base", "r2", 1, 200),
            observation("1a", "r1", 2, 200), observation("1a", "r2", 2, 300)]
    _, summary = speedup.prepare(pd.DataFrame(rows), config)
    path = tmp_path / "out.csv"
    summary.to_csv(path, index=False)
    seeds = pd.read_csv(path, dtype=str).set_index("strategy").ci_seed
    expected = hashlib.sha256(b"42|mb1_rawNoFinalize|1a|2|speedup").digest()[:8]
    assert seeds["1a"] == str(int.from_bytes(expected, "big") % 2**63)
    assert pd.isna(seeds["base"])


def test_throughput_title_uses_method_from_a_row_that_has_a_ci(config):
    # "base" has no spread (no CI) and sorts first; "1a" has a CI.
    frame = pd.DataFrame(two_reruns("base", 1, (100, 100)) + two_reruns("1a", 1, (100, 200)))
    (_, result), _ = collect(thrpt.prepare, frame, config)
    assert result.set_index("strategy").interval_kind.to_dict() == {"base": "none", "1a": T_METHOD}
    fig = render_throughput(result, config)
    try:
        assert fig.axes[0].get_title().endswith(f"Error bars: {T_METHOD}")
    finally:
        plt.close(fig)


def test_throughput_title_has_no_error_bars_line_without_any_ci(config):
    frame = pd.DataFrame(two_reruns("base", 1, (100, 100)) + [observation("1a", "01", 1, 500)])
    (_, result), _ = collect(thrpt.prepare, frame, config)
    assert result.interval_kind.unique().tolist() == ["none"]
    fig = render_throughput(result, config)
    try:
        assert "Error bars" not in fig.axes[0].get_title()
    finally:
        plt.close(fig)


def test_speedup_title_has_no_error_bars_line_without_any_ci(config):
    frame = pd.DataFrame([observation(rerun="01", score=100), observation("1a", "01", 2, 200)])
    (_, summary), _ = collect(speedup.prepare, frame, config)
    assert summary.ci_low.isna().all()
    fig = speedup.render(summary, config, "Average", strategies=["base", "1a"], threads=[1, 2])
    try:
        assert "Error bars" not in fig.axes[0].get_title()
    finally:
        plt.close(fig)
