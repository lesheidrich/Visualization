"""Hand-calculated tests of the pauses_max dot plot: medians, drawn figure and `run` outputs.

Every expected value is a literal with its arithmetic in a comment. Ledger row numbers: a
programmatic frame has its first row at ledger row 2, so row i (0-based) is ledger row i + 2.
Geometry (contract v2): w = 0.8 / S; P_i = strategies with a plottable value in group i; the strategy at
index j of P_i sits at x = i + (j - (len(P_i) - 1) / 2) * w; a median line (black) spans x -/+ 0.5 * w.
Colours use the global strategy index.
"""
from dataclasses import replace
import json
import warnings

import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection, PathCollection
from matplotlib.colors import to_rgba
import numpy as np
import pandas as pd
import pytest

from conftest import gc_observation
from src.gc_metrics import pause_medians, prepare, render_pauses, run

BENCH = "mb1_rawNoFinalize"
BENCH2 = "mb2_rawFinalize"
PROXY = "median (per strategy and threads)"
MEDIAN_COLUMNS = ["benchmark_short", "strategy", "threads_blobshuffle", "pauses_max_median",
                  "n_reruns", "total_reruns", "contributing_reruns"]


@pytest.fixture(autouse=True)
def close_figures():
    yield
    plt.close("all")


def ledger_rows(specs, benchmark=BENCH):
    """specs: (strategy, threads, rerun, pauses_max); pauses_max may be "" for a blank."""
    return [gc_observation(benchmark_short=benchmark, strategy=s, threads=t, rerun=r,
                           pauses_max=str(p)) for s, t, r, p in specs]


def observations_of(rows, config):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return prepare(pd.DataFrame(rows), config)[0]


def medians_of(observations):
    """Return (medians frame, list of warning messages)."""
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        frame = pause_medians(observations)
    return frame, [str(w.message) for w in record if issubclass(w.category, UserWarning)]


def drawn(config, specs, strategies, threads, medians_edit=None, **changes):
    config = replace(config, **changes)
    observations = observations_of(ledger_rows(specs), config)
    medians, _ = medians_of(observations)
    if medians_edit is not None:
        medians = medians_edit(medians)
    return render_pauses(observations, medians, config, "My title",
                         strategies=strategies, threads=threads)


def parts(fig):
    """(scatter collections, line collections) of the single axes, in drawing order."""
    ax = fig.axes[0]
    scatters = [c for c in ax.collections if isinstance(c, PathCollection)]
    lines = [c for c in ax.collections if isinstance(c, LineCollection)]
    assert len(scatters) + len(lines) == len(ax.collections)
    assert len(scatters) == len(lines)
    return scatters, lines


def offsets(collection):
    return np.asarray(collection.get_offsets(), dtype=float)


def segments(collection):
    return [np.asarray(s, dtype=float) for s in collection.get_segments()]


# ================================================================ pause_medians

@pytest.mark.parametrize("values, median", [
    ([1, 2, 8], 2.0),             # odd: middle of 1, 2, 8
    ([1, 2, 4, 8], 3.0),          # even: (2 + 4) / 2
    ([7], 7.0),                   # single value
    ([3, 4], 3.5),                # two values: (3 + 4) / 2
    ([8, 1, 4, 2], 3.0),          # unsorted input; sorted 1, 2, 4, 8 -> (2 + 4) / 2
    ([1, 2, 4, 8, 16], 4.0),      # odd, five values
    ([0.5, 0.25, 2], 0.5),        # sorted 0.25, 0.5, 2
    ([5, 5, 5, 5], 5.0),          # equal values
])
def test_median_values(config, values, median):
    rows = ledger_rows([("base", 1, f"r{i}", v) for i, v in enumerate(values)])
    frame, messages = medians_of(observations_of(rows, config))
    assert messages == []
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["pauses_max_median"] == median
    assert isinstance(float(row["pauses_max_median"]), float)
    assert row["n_reruns"] == len(values)
    assert row["total_reruns"] == len(values)


def test_columns_row_content_and_order_of_first_appearance(config):
    # 1a at 2 threads appears first, then base at 1 thread; reruns 01..03 form the benchmark total.
    rows = ledger_rows([
        ("1a", 2, "01", 10), ("1a", 2, "02", 30),     # median (10 + 30) / 2 = 20
        ("base", 1, "01", 1), ("base", 1, "02", 2), ("base", 1, "03", 8),   # median 2
    ])
    frame, _ = medians_of(observations_of(rows, config))
    assert frame.columns.tolist() == MEDIAN_COLUMNS
    assert frame["strategy"].tolist() == ["1a", "base"]
    assert frame["threads_blobshuffle"].tolist() == [2, 1]
    assert frame["benchmark_short"].tolist() == [BENCH, BENCH]
    assert frame["pauses_max_median"].tolist() == [20.0, 2.0]
    assert frame["n_reruns"].tolist() == [2, 3]
    assert frame["total_reruns"].tolist() == [3, 3]       # reruns 01, 02, 03 exist in the benchmark
    assert [json.loads(x) for x in frame["contributing_reruns"]] == [["01", "02"], ["01", "02", "03"]]


def test_threads_are_separate_groups(config):
    rows = ledger_rows([("base", 1, "01", 4), ("base", 1, "02", 6),     # median 5
                        ("base", 2, "01", 100), ("base", 2, "02", 300)])  # median 200
    frame, _ = medians_of(observations_of(rows, config))
    assert frame["threads_blobshuffle"].tolist() == [1, 2]
    assert frame["pauses_max_median"].tolist() == [5.0, 200.0]


def test_zero_omitted_n_is_two_and_warning_names_the_row(config):
    rows = ledger_rows([("base", 1, "01", 0), ("base", 1, "02", 4), ("base", 1, "03", 10)])
    frame, messages = medians_of(observations_of(rows, config))
    assert messages == ["Non-positive pauses_max omitted from log plot at ledger row(s) [2]"]
    row = frame.iloc[0]
    assert row["pauses_max_median"] == 7.0               # (4 + 10) / 2, the zero is not counted
    assert row["n_reruns"] == 2
    assert row["total_reruns"] == 3
    assert json.loads(row["contributing_reruns"]) == ["02", "03"]


def test_zero_warning_lists_every_row_once(config):
    rows = ledger_rows([("base", 1, "01", 0), ("base", 1, "02", 5), ("base", 1, "03", 0),
                        ("1a", 1, "01", 3), ("1a", 1, "02", 0), ("1a", 1, "03", 9)])
    frame, messages = medians_of(observations_of(rows, config))
    # Zeros are at frame positions 0, 2 and 4 -> ledger rows 2, 4 and 6.
    assert messages == ["Non-positive pauses_max omitted from log plot at ledger row(s) [2, 4, 6]"]
    assert frame["pauses_max_median"].tolist() == [5.0, 6.0]      # base: 5; 1a: (3 + 9) / 2
    assert frame["n_reruns"].tolist() == [1, 2]


def test_no_zero_no_warning(config):
    rows = ledger_rows([("base", 1, "01", 1), ("base", 1, "02", 2)])
    assert medians_of(observations_of(rows, config))[1] == []


def test_blank_omitted_without_pause_medians_warning(config):
    rows = ledger_rows([("base", 1, "01", ""), ("base", 1, "02", 4), ("base", 1, "03", 10)])
    frame, messages = medians_of(observations_of(rows, config))
    assert messages == []                                # blanks are warned about by prepare only
    row = frame.iloc[0]
    assert row["pauses_max_median"] == 7.0               # (4 + 10) / 2
    assert row["n_reruns"] == 2 and row["total_reruns"] == 3
    assert json.loads(row["contributing_reruns"]) == ["02", "03"]


@pytest.mark.parametrize("unplottable", [0, ""], ids=["all-zero", "all-blank"])
def test_group_without_plottable_value_has_no_row(config, unplottable):
    rows = ledger_rows([("base", 1, "01", unplottable), ("base", 1, "02", unplottable),
                        ("1a", 1, "01", 3), ("1a", 1, "02", 5)])    # median 4
    frame, _ = medians_of(observations_of(rows, config))
    assert frame["strategy"].tolist() == ["1a"]
    assert frame["pauses_max_median"].tolist() == [4.0]


def test_zero_in_one_thread_group_does_not_remove_the_other(config):
    rows = ledger_rows([("base", 1, "01", 0), ("base", 2, "01", 6)])
    frame, _ = medians_of(observations_of(rows, config))
    assert frame["threads_blobshuffle"].tolist() == [2]
    assert frame["pauses_max_median"].tolist() == [6.0]


def test_benchmarks_are_kept_apart(config):
    config = replace(config, benchmarks=(BENCH, BENCH2))
    rows = (ledger_rows([("base", 1, "01", 1), ("base", 1, "02", 2), ("base", 1, "03", 8)])    # median 2
            + ledger_rows([("base", 1, "01", 40), ("base", 1, "02", 60)], benchmark=BENCH2))   # (40 + 60) / 2
    frame, _ = medians_of(observations_of(rows, config))
    assert frame["benchmark_short"].tolist() == [BENCH, BENCH2]
    assert frame["pauses_max_median"].tolist() == [2.0, 50.0]
    assert frame["n_reruns"].tolist() == [3, 2]
    assert frame["total_reruns"].tolist() == [3, 2]      # per benchmark, not 5


def test_total_reruns_exceeds_n_when_group_misses_reruns(config):
    # The benchmark has reruns 01..04 (from the 1a rows); base has only 01 and 03.
    rows = ledger_rows([("base", 1, "01", 2), ("base", 1, "03", 6),
                        ("1a", 1, "01", 1), ("1a", 1, "02", 1), ("1a", 1, "03", 1), ("1a", 1, "04", 1)])
    frame, _ = medians_of(observations_of(rows, config))
    base = frame[frame["strategy"].eq("base")].iloc[0]
    assert base["pauses_max_median"] == 4.0              # (2 + 6) / 2
    assert base["n_reruns"] == 2 and base["total_reruns"] == 4
    assert json.loads(base["contributing_reruns"]) == ["01", "03"]


# ================================================================ render_pauses: positions

def test_one_strategy_dots_and_median_segments(config):
    # S = 1, w = 0.8, x = i + (0 - 0) * 0.8 = i. Half-width 0.5 * 0.8 = 0.4.
    fig = drawn(config, [("base", 1, "r1", 1), ("base", 1, "r2", 2), ("base", 1, "r3", 8),
                         ("base", 2, "r1", 3)], ["base"], [1, 2])
    scatters, lines = parts(fig)
    assert len(scatters) == 1
    np.testing.assert_allclose(offsets(scatters[0]), [
        [0.0, 1], [0.0, 2], [0.0, 8],      # threads 1 -> group 0
        [1.0, 3],                          # threads 2 -> group 1
    ], rtol=1e-9, atol=1e-12)
    got = segments(lines[0])
    assert len(got) == 2
    np.testing.assert_allclose(got[0], [[-0.4, 2], [0.4, 2]], rtol=1e-9, atol=1e-12)   # 0 -/+ 0.4, median of 1, 2, 8
    np.testing.assert_allclose(got[1], [[0.6, 3], [1.4, 3]], rtol=1e-9, atol=1e-12)    # 1 -/+ 0.4, single value


def test_two_strategies_offsets_minus_and_plus_point_two(config):
    # S = 2, w = 0.4, both strategies in both groups: base at i - 0.2, 1a at i + 0.2. Half-width 0.5 * 0.4 = 0.2.
    specs = [("base", 1, "r1", 4), ("base", 1, "r2", 16), ("1a", 1, "r1", 1), ("1a", 1, "r2", 3),
             ("base", 2, "r1", 6), ("1a", 2, "r1", 9), ("1a", 2, "r2", 11)]
    fig = drawn(config, specs, ["base", "1a"], [1, 2])
    scatters, lines = parts(fig)
    assert [c.get_label() for c in scatters] == ["Base", "S1_A"]
    np.testing.assert_allclose(offsets(scatters[0]), [
        [-0.2, 4], [-0.2, 16],             # 0 - 0.2
        [0.8, 6],                          # 1 - 0.2
    ], rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(offsets(scatters[1]), [
        [0.2, 1], [0.2, 3],                # 0 + 0.2
        [1.2, 9], [1.2, 11],               # 1 + 0.2
    ], rtol=1e-9, atol=1e-12)
    base, alpha = segments(lines[0]), segments(lines[1])
    np.testing.assert_allclose(base, [
        [[-0.4, 10], [0.0, 10]],           # -0.2 -/+ 0.2, median (4 + 16) / 2
        [[0.6, 6], [1.0, 6]],              # 0.8 -/+ 0.2, single value
    ], rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(alpha, [
        [[0.0, 2], [0.4, 2]],              # 0.2 -/+ 0.2, median (1 + 3) / 2
        [[1.0, 10], [1.4, 10]],            # 1.2 -/+ 0.2, median (9 + 11) / 2
    ], rtol=1e-9, atol=1e-12)


def test_three_strategies_with_gaps_in_the_thread_groups(config):
    # S = 3, w = 0.8 / 3 = 0.2666667; half-width 0.5 * w = 0.1333333.
    # Threads [1, 2, 4] -> groups 0, 1, 2. base and 1b are in groups 0 and 2, 1a only in group 1.
    # Group 0: P = [base, 1b] -> j = 0, 1, x = 0 + (j - 0.5) * w = -0.1333333 and +0.1333333.
    # Group 1: P = [1a] -> x = 1. Group 2: P = [base, 1b] -> 2 -/+ 0.1333333.
    specs = [("base", 1, "r1", 2), ("base", 4, "r1", 20),
             ("1a", 2, "r1", 3), ("1a", 2, "r2", 5),
             ("1b", 1, "r1", 7), ("1b", 4, "r1", 9), ("1b", 4, "r2", 11)]
    fig = drawn(config, specs, ["base", "1a", "1b"], [1, 2, 4])
    scatters, lines = parts(fig)
    assert [c.get_label() for c in scatters] == ["Base", "S1_A", "S1_B"]
    np.testing.assert_allclose(offsets(scatters[0]), [
        [-0.1333333333, 2],                # 0 - 0.1333333
        [1.8666666667, 20],                # 2 - 0.1333333
    ], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(offsets(scatters[1]), [
        [1.0, 3], [1.0, 5],                # alone in group 1: 1
    ], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(offsets(scatters[2]), [
        [0.1333333333, 7],                 # 0 + 0.1333333
        [2.1333333333, 9], [2.1333333333, 11],  # 2 + 0.1333333
    ], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(segments(lines[0]), [
        [[-0.2666666667, 2], [0.0, 2]],        # -0.1333333 -/+ 0.1333333
        [[1.7333333333, 20], [2.0, 20]],       # 1.8666667 -/+ 0.1333333
    ], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(segments(lines[1]), [
        [[0.8666666667, 4], [1.1333333333, 4]],   # 1 -/+ 0.1333333, median (3 + 5) / 2
    ], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(segments(lines[2]), [
        [[0.0, 7], [0.2666666667, 7]],            # 0.1333333 -/+ 0.1333333
        [[2.0, 10], [2.2666666667, 10]],          # 2.1333333 -/+ 0.1333333, median (9 + 11) / 2
    ], rtol=1e-9, atol=1e-9)


def test_dots_follow_observation_order_and_segments_follow_thread_order(config):
    # Threads 2 is listed before threads 1 in the ledger.
    fig = drawn(config, [("base", 2, "r1", 5), ("base", 1, "r1", 3), ("base", 2, "r2", 9)],
                ["base"], [1, 2])
    scatters, lines = parts(fig)
    np.testing.assert_allclose(offsets(scatters[0]), [[1.0, 5], [0.0, 3], [1.0, 9]],
                               rtol=1e-9, atol=1e-12)           # observation order
    np.testing.assert_allclose(segments(lines[0]), [
        [[-0.4, 3], [0.4, 3]],             # threads 1 first: 0 -/+ 0.4, single value
        [[0.6, 7], [1.4, 7]],              # threads 2: 1 -/+ 0.4, median (5 + 9) / 2
    ], rtol=1e-9, atol=1e-12)


def test_zero_and_blank_values_are_not_drawn(config):
    fig = drawn(config, [("base", 1, "r1", 0), ("base", 1, "r2", ""), ("base", 1, "r3", 4),
                         ("base", 1, "r4", 10)], ["base"], [1])
    scatters, lines = parts(fig)
    np.testing.assert_allclose(offsets(scatters[0]), [[0.0, 4], [0.0, 10]], rtol=1e-9)
    np.testing.assert_allclose(segments(lines[0]), [[[-0.4, 7], [0.4, 7]]], rtol=1e-9)   # (4 + 10) / 2


def test_strategy_without_plottable_values_draws_nothing_and_keeps_its_slot(config):
    # S = 3, w = 0.2666667. 1a (k = 1) has only zeros: no collections, no legend entry.
    # Group 0: P = [base, 1b] -> base at -0.5 * w = -0.1333333, 1b at +0.1333333.
    # Colours come from tab10 by the GLOBAL index k (0 and 2), not by j.
    specs = [("base", 1, "r1", 4), ("1a", 1, "r1", 0), ("1a", 1, "r2", 0), ("1b", 1, "r1", 6)]
    fig = drawn(config, specs, ["base", "1a", "1b"], [1])
    ax = fig.axes[0]
    scatters, lines = parts(fig)
    assert len(ax.collections) == 4
    assert [c.get_label() for c in scatters] == ["Base", "S1_B"]
    np.testing.assert_allclose(offsets(scatters[0]), [[-0.1333333333, 4]], rtol=1e-9)
    np.testing.assert_allclose(offsets(scatters[1]), [[0.1333333333, 6]], rtol=1e-9)
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["Base", "S1_B", PROXY]
    np.testing.assert_allclose(scatters[0].get_facecolor()[0][:3], to_rgba("#1f77b4")[:3])   # tab10[0]
    np.testing.assert_allclose(scatters[1].get_facecolor()[0][:3], to_rgba("#2ca02c")[:3])   # tab10[2], not [1]
    np.testing.assert_allclose(lines[0].get_color()[0], to_rgba("black"))
    np.testing.assert_allclose(lines[1].get_color()[0], to_rgba("black"))


def test_absent_combination_draws_nothing(config):
    # base exists at threads 1 only; 1a at both. Nothing may be drawn for base in group 1.
    # S = 2, w = 0.4. Group 0: P = [base, 1a] -> -0.2 and +0.2. Group 1: P = [1a] -> x = 1 (compact).
    fig = drawn(config, [("base", 1, "r1", 3), ("1a", 1, "r1", 2), ("1a", 2, "r1", 5)],
                ["base", "1a"], [1, 2])
    scatters, lines = parts(fig)
    assert len(offsets(scatters[0])) == 1 and len(segments(lines[0])) == 1
    np.testing.assert_allclose(offsets(scatters[0]), [[-0.2, 3]], rtol=1e-9)
    np.testing.assert_allclose(segments(lines[0]), [[[-0.4, 3], [0.0, 3]]], rtol=1e-9)   # -0.2 -/+ 0.2
    np.testing.assert_allclose(offsets(scatters[1]), [[0.2, 2], [1.0, 5]], rtol=1e-9)
    np.testing.assert_allclose(segments(lines[1]), [
        [[0.0, 2], [0.4, 2]],              # 0.2 -/+ 0.2
        [[0.8, 5], [1.2, 5]],              # 1 -/+ 0.2
    ], rtol=1e-9, atol=1e-12)


def test_delta_example_base_1a_2a(config):
    # strategies [base, 1a, 2a], S = 3, w = 0.8 / 3 = 0.2666667, half-width 0.5 * w = 0.1333333.
    # base only at threads 1 (group 0); 1a and 2a at threads 1 and 2 (groups 0 and 1).
    # Group 0: P = [base, 1a, 2a], x = 0 + (j - 1) * w -> base -0.2666667, 1a 0, 2a +0.2666667.
    # Group 1: P = [1a, 2a], x = 1 + (j - 0.5) * w -> 1a 1 - 0.1333333 = 0.8666667, 2a 1.1333333.
    specs = [("base", 1, "r1", 2), ("1a", 1, "r1", 3), ("1a", 2, "r1", 5),
             ("2a", 1, "r1", 7), ("2a", 2, "r1", 11)]
    fig = drawn(config, specs, ["base", "1a", "2a"], [1, 2])
    scatters, lines = parts(fig)
    np.testing.assert_allclose(offsets(scatters[0]), [[-0.2666666667, 2]], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(offsets(scatters[1]), [[0.0, 3], [0.8666666667, 5]], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(offsets(scatters[2]), [[0.2666666667, 7], [1.1333333333, 11]],
                               rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(segments(lines[0]), [
        [[-0.4, 2], [-0.1333333333, 2]],       # -0.2666667 -/+ 0.1333333
    ], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(segments(lines[1]), [
        [[-0.1333333333, 3], [0.1333333333, 3]],   # 0 -/+ 0.1333333
        [[0.7333333333, 5], [1.0, 5]],             # 0.8666667 -/+ 0.1333333
    ], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(segments(lines[2]), [
        [[0.1333333333, 7], [0.4, 7]],             # 0.2666667 -/+ 0.1333333
        [[1.0, 11], [1.2666666667, 11]],           # 1.1333333 -/+ 0.1333333
    ], rtol=1e-9, atol=1e-9)


def test_gap_strategy_in_the_middle_of_the_order(config):
    # strategies [a, b, c], S = 3, w = 0.2666667; b is absent at threads 2 (group 1).
    # Group 0: P = [a, b, c] -> a -0.2666667, b 0, c +0.2666667.
    # Group 1: P = [a, c] -> x = 1 + (j - 0.5) * w -> a 0.8666667, c 1.1333333 (b leaves no hole).
    # Colours use the global index k: a = tab10(0) #1f77b4, b = tab10(1) #ff7f0e, c = tab10(2) #2ca02c.
    specs = [("a", 1, "r1", 2), ("a", 2, "r1", 3),
             ("b", 1, "r1", 4),
             ("c", 1, "r1", 6), ("c", 2, "r1", 8)]
    fig = drawn(config, specs, ["a", "b", "c"], [1, 2])
    scatters, lines = parts(fig)
    np.testing.assert_allclose(offsets(scatters[0]), [[-0.2666666667, 2], [0.8666666667, 3]],
                               rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(offsets(scatters[1]), [[0.0, 4]], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(offsets(scatters[2]), [[0.2666666667, 6], [1.1333333333, 8]],
                               rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(segments(lines[0]), [
        [[-0.4, 2], [-0.1333333333, 2]],       # -0.2666667 -/+ 0.1333333
        [[0.7333333333, 3], [1.0, 3]],         # 0.8666667 -/+ 0.1333333
    ], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(segments(lines[1]), [[[-0.1333333333, 4], [0.1333333333, 4]]],
                               rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(segments(lines[2]), [
        [[0.1333333333, 6], [0.4, 6]],         # 0.2666667 -/+ 0.1333333
        [[1.0, 8], [1.2666666667, 8]],         # 1.1333333 -/+ 0.1333333
    ], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(scatters[0].get_facecolor()[0][:3], to_rgba("#1f77b4")[:3])
    np.testing.assert_allclose(scatters[1].get_facecolor()[0][:3], to_rgba("#ff7f0e")[:3])
    np.testing.assert_allclose(scatters[2].get_facecolor()[0][:3], to_rgba("#2ca02c")[:3])


# ================================================================ render_pauses: styling

def test_configured_colours_alpha_zorder_and_linewidth(config):
    specs = [("base", 1, "r1", 3), ("1a", 1, "r1", 4), ("1b", 1, "r1", 5)]
    fig = drawn(config, specs, ["base", "1a", "1b"], [1], colors={"base": "red", "1a": "#00ff00"})
    scatters, lines = parts(fig)
    expected = [to_rgba("red"), to_rgba("#00ff00"), to_rgba("#2ca02c")]    # 1b: tab10[2] fallback
    for scatter, line, colour in zip(scatters, lines, expected):
        np.testing.assert_allclose(scatter.get_facecolor()[0][:3], colour[:3])
        np.testing.assert_allclose(line.get_color()[0], to_rgba("black"))    # medians are black
        assert scatter.get_alpha() == 0.7
        assert scatter.get_zorder() == 3
        assert line.get_zorder() == 4
        assert list(line.get_linewidth()) == [2]
    assert scatters[0].get_facecolor()[0][0] == 1.0 and scatters[0].get_facecolor()[0][1] == 0.0


def test_median_lines_are_black_even_with_configured_colours(config):
    fig = drawn(config, [("base", 1, "r1", 3), ("1a", 1, "r1", 4)], ["base", "1a"], [1],
                colors={"base": "red", "1a": "#00ff00"})
    scatters, lines = parts(fig)
    for line in lines:
        assert len(line.get_color()) == 1
        np.testing.assert_allclose(line.get_color()[0], to_rgba("black"))   # (0, 0, 0, 1)
    np.testing.assert_allclose(scatters[0].get_facecolor()[0][:3], to_rgba("red")[:3])   # dots keep the colour
    np.testing.assert_allclose(scatters[1].get_facecolor()[0][:3], to_rgba("#00ff00")[:3])


def test_median_line_is_drawn_above_the_dots(config):
    fig = drawn(config, [("base", 1, "r1", 3), ("1a", 1, "r1", 4)], ["base", "1a"], [1])
    scatters, lines = parts(fig)
    for scatter, line in zip(scatters, lines):
        assert scatter.get_zorder() == 3
        assert line.get_zorder() == 4
        assert line.get_zorder() > scatter.get_zorder()


def test_tab10_fallback_without_configuration(config):
    fig = drawn(config, [("base", 1, "r1", 3), ("1a", 1, "r1", 4)], ["base", "1a"], [1])
    scatters, lines = parts(fig)
    np.testing.assert_allclose(scatters[0].get_facecolor()[0][:3], to_rgba("#1f77b4")[:3])   # tab10[0]
    np.testing.assert_allclose(scatters[1].get_facecolor()[0][:3], to_rgba("#ff7f0e")[:3])   # tab10[1]
    np.testing.assert_allclose(lines[0].get_color()[0], to_rgba("black"))
    np.testing.assert_allclose(lines[1].get_color()[0], to_rgba("black"))


def test_legend_labels_in_order_with_median_proxy_last(config):
    fig = drawn(config, [("base", 1, "r1", 3), ("1a", 1, "r1", 4), ("2a", 1, "r1", 5)],
                ["base", "1a", "2a"], [1])
    legend = fig.axes[0].get_legend()
    assert [t.get_text() for t in legend.get_texts()] == ["Base", "S1_A", "S2_A", PROXY]
    proxy = legend.legend_handles[-1]
    assert proxy.get_color() == "black"
    assert proxy.get_linewidth() == 2


def test_legend_uses_configured_labels_and_falls_back_to_the_id(config):
    config_labels = {"base": "Baseline"}                    # 1a has no label -> id
    fig = drawn(config, [("base", 1, "r1", 3), ("1a", 1, "r1", 4)], ["base", "1a"], [1],
                labels=config_labels)
    legend = fig.axes[0].get_legend()
    assert [t.get_text() for t in legend.get_texts()] == ["Baseline", "1a", PROXY]


def test_strategy_order_follows_the_argument(config):
    fig = drawn(config, [("base", 1, "r1", 3), ("1a", 1, "r1", 4)], ["1a", "base"], [1])
    scatters, _ = parts(fig)
    assert [c.get_label() for c in scatters] == ["S1_A", "Base"]
    np.testing.assert_allclose(offsets(scatters[0]), [[-0.2, 4]], rtol=1e-9)   # 1a is k = 0
    np.testing.assert_allclose(offsets(scatters[1]), [[0.2, 3]], rtol=1e-9)    # base is k = 1


def test_axes_scale_ticks_labels_and_title(config):
    fig = drawn(config, [("base", 1, "r1", 3), ("base", 2, "r1", 5), ("base", 8, "r1", 9)],
                ["base"], [1, 2, 8])
    ax = fig.axes[0]
    assert ax.get_yscale() == "log"
    assert ax.get_xscale() == "linear"
    assert list(ax.get_xticks()) == [0, 1, 2]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1", "2", "8"]
    assert ax.get_xlabel() == "BlobShuffle threads"
    assert ax.get_ylabel() == "pauses_max (ms, log scale)"
    assert ax.get_title() == "My title"
    assert not ax.spines["top"].get_visible()
    assert not ax.spines["right"].get_visible()


def test_figure_size_comes_from_the_configuration(config):
    fig = drawn(config, [("base", 1, "r1", 3)], ["base"], [1], figsize=(5, 3))
    assert list(fig.get_size_inches()) == [5, 3]
    assert len(plt.get_fignums()) == 1


# ================================================================ render_pauses: log tick labels

def visible_labels(fig, low, high):
    """(location, text) of non-empty y tick labels (major and minor) with low * 0.8 <= loc <= high * 1.2."""
    ax = fig.axes[0]
    fig.canvas.draw()
    found = []
    for minor in (False, True):
        locs = ax.yaxis.get_ticklocs(minor=minor)
        texts = [t.get_text() for t in ax.yaxis.get_ticklabels(minor=minor)]
        assert len(locs) == len(texts)
        found += [(loc, text) for loc, text in zip(locs, texts)
                  if text != "" and low * 0.8 <= loc <= high * 1.2]
    return [text for _, text in sorted(found)]


def tick_figure(config, low, high):
    return drawn(config, [("base", 1, "r1", low), ("base", 1, "r2", high)], ["base"], [1])


def assert_plain(labels):
    for label in labels:
        assert not any(ch in label for ch in ("e", "×", "−", "$")), label


def test_log_tick_labels_narrow_range(config):
    # Data 2 .. 4.2 -> window [1.6, 5.04]: minor ticks 2, 3, 4, 5 only.
    labels = visible_labels(tick_figure(config, 2, 4.2), 2, 4.2)
    assert labels == ["2", "3", "4", "5"]
    assert_plain(labels)


def test_log_tick_labels_middle_range(config):
    # Data 0.5 .. 30 -> window [0.4, 36]: majors 1 and 10 plus minors.
    labels = visible_labels(tick_figure(config, 0.5, 30), 0.5, 30)
    for expected in ["1", "10", "0.4", "0.6", "2", "3", "4", "6", "20", "30"]:
        assert expected in labels
    assert_plain(labels)


def test_log_tick_labels_wide_range(config):
    # Data 0.01 .. 500 -> window [0.008, 600]: the decades 0.01 .. 100 only.
    labels = visible_labels(tick_figure(config, 0.01, 500), 0.01, 500)
    assert labels == ["0.01", "0.1", "1", "10", "100"]
    assert_plain(labels)


# ================================================================ render_pauses: medians come from the frame

def test_drawn_median_heights_equal_the_medians_frame(config):
    specs = [("base", 1, "r1", 1), ("base", 1, "r2", 2), ("base", 1, "r3", 8),     # 2
             ("base", 2, "r1", 1), ("base", 2, "r2", 2), ("base", 2, "r3", 4), ("base", 2, "r4", 8),   # 3
             ("1a", 1, "r1", 20), ("1a", 1, "r2", 40)]                              # 30
    config = replace(config)
    observations = observations_of(ledger_rows(specs), config)
    medians, _ = medians_of(observations)
    assert medians["pauses_max_median"].tolist() == [2.0, 3.0, 30.0]
    fig = render_pauses(observations, medians, config, "t", strategies=["base", "1a"], threads=[1, 2])
    scatters, lines = parts(fig)
    assert [s[0][1] for s in segments(lines[0])] == [2.0, 3.0]
    assert [s[0][1] for s in segments(lines[1])] == [30.0]
    assert all(s[0][1] == s[1][1] for line in lines for s in segments(line))       # horizontal


def test_heights_are_read_from_the_medians_frame_not_recomputed(config):
    def tampered(frame):
        frame = frame.copy()
        frame["pauses_max_median"] = [123.0, 456.0]
        return frame
    fig = drawn(config, [("base", 1, "r1", 1), ("base", 1, "r2", 3), ("base", 2, "r1", 5)],
                ["base"], [1, 2], medians_edit=tampered)
    scatters, lines = parts(fig)
    assert [s[0][1] for s in segments(lines[0])] == [123.0, 456.0]
    np.testing.assert_allclose(offsets(scatters[0])[:, 1], [1, 3, 5])      # dots keep the raw values


def test_only_the_benchmark_passed_in_is_drawn(config):
    config = replace(config, benchmarks=(BENCH, BENCH2))
    rows = (ledger_rows([("base", 1, "r1", 3)])
            + ledger_rows([("base", 1, "r1", 70), ("base", 1, "r2", 90)], benchmark=BENCH2))
    observations = observations_of(rows, config)
    medians, _ = medians_of(observations)
    keep = observations["benchmark_short"].eq(BENCH2)
    fig = render_pauses(observations[keep], medians[medians["benchmark_short"].eq(BENCH2)], config,
                        "t", strategies=["base"], threads=[1])
    scatters, lines = parts(fig)
    np.testing.assert_allclose(offsets(scatters[0]), [[0.0, 70], [0.0, 90]], rtol=1e-9)
    np.testing.assert_allclose(segments(lines[0]), [[[-0.4, 80], [0.4, 80]]], rtol=1e-9)   # (70 + 90) / 2


# ================================================================ run

def output_name(benchmark, suffix):
    return f"gc_{benchmark}_{suffix}"


OLD_SUFFIXES = ["r_all.csv", "avg.csv"] + [f"{m}_pivot.csv" for m in (
    "gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct", "pauses_max", "pauses_p99", "batch_fill")]


def write_ledger(config, rows):
    pd.DataFrame(rows).to_csv(config.ledger, index=False)


def small_fixture():
    return ledger_rows([
        ("base", 1, "r1", 1), ("base", 1, "r2", 2), ("base", 1, "r3", 8),     # median 2
        ("base", 2, "r1", 5), ("base", 2, "r2", 0), ("base", 2, "r3", 9),     # zero omitted: (5 + 9) / 2 = 7
        ("1a", 1, "r1", 4), ("1a", 1, "r2", 16), ("1a", 1, "r3", ""),         # blank omitted: (4 + 16) / 2 = 10
        ("1a", 2, "r1", 0), ("1a", 2, "r2", 0), ("1a", 2, "r3", 0),           # all zero: no row
    ])


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_median_csv_content(config):
    write_ledger(config, small_fixture())
    paths = run(config)
    path = next(p for p in paths if p.name == output_name(BENCH, "pauses_max_median.csv"))
    assert path.parent.parent == config.output
    assert path.read_text().splitlines()[0] == ",".join(MEDIAN_COLUMNS)
    frame = pd.read_csv(path, dtype=str)
    assert len(frame) == 3
    assert frame[MEDIAN_COLUMNS[:-1]].values.tolist() == [
        [BENCH, "base", "1", "2.0", "3", "3"],        # median of 1, 2, 8
        [BENCH, "base", "2", "7.0", "2", "3"],        # (5 + 9) / 2; r2 is zero
        [BENCH, "1a", "1", "10.0", "2", "3"],         # (4 + 16) / 2; r3 is blank
    ]
    assert [json.loads(x) for x in frame["contributing_reruns"]] == [
        ["r1", "r2", "r3"], ["r1", "r3"], ["r1", "r2"]]


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_zero_warning_is_raised_by_run(config):
    write_ledger(config, small_fixture())
    with pytest.warns(UserWarning, match=r"Non-positive pauses_max omitted from log plot at ledger row\(s\) \[6, 11, 12, 13\]"):
        run(config)                                   # rows: base/2/r2 -> 6; 1a/2/r1..r3 -> 11, 12, 13


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize("formats, magic", [
    (("png",), {"png": b"\x89PNG"}),
    (("pdf",), {"pdf": b"%PDF"}),
    (("svg",), {"svg": b"<?xml"}),
    (("png", "pdf", "svg"), {"png": b"\x89PNG", "pdf": b"%PDF", "svg": b"<?xml"}),
    (("svg", "png"), {"svg": b"<?xml", "png": b"\x89PNG"}),
])
def test_formats_saved_in_configured_order_and_figures_closed(config, formats, magic):
    write_ledger(config, small_fixture())
    paths = run(replace(config, formats=formats))
    assert [p.name for p in paths] == (
        [output_name(BENCH, s) for s in OLD_SUFFIXES]
        + [output_name(BENCH, "pauses_max_median.csv")]
        + [output_name(BENCH, f"pauses_max.{f}") for f in formats]
        + [output_name(BENCH, f"gc_alloc_rate_norm.{f}") for f in formats]
        + [output_name(BENCH, f"gc_time_pct.{f}") for f in formats])
    assert len(set(paths)) == len(paths) == 10 + 3 * len(formats)
    assert len({p.parent for p in paths}) == 1
    for path in paths:
        assert path.is_file() and path.stat().st_size > 0
        if path.suffix[1:] in magic:
            assert path.read_bytes().lstrip()[:len(magic[path.suffix[1:]])] == magic[path.suffix[1:]]
    assert plt.get_fignums() == []


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_two_benchmarks_give_every_csv_and_png_in_contract_order(config):
    config = replace(config, benchmarks=(BENCH, BENCH2))
    write_ledger(config, small_fixture() + ledger_rows(
        [("base", 1, "r1", 3), ("base", 1, "r2", 5), ("1a", 1, "r1", 2), ("1a", 1, "r2", 4)], benchmark=BENCH2))
    paths = run(config)
    assert len(paths) == 27 and len(set(paths)) == 27
    expected = []
    for benchmark in (BENCH, BENCH2):
        expected += ([output_name(benchmark, s) for s in OLD_SUFFIXES]
                     + [output_name(benchmark, "pauses_max_median.csv"),
                        output_name(benchmark, "pauses_max.png"),
                        output_name(benchmark, "gc_alloc_rate_norm.png"),
                        output_name(benchmark, "gc_time_pct.png")]
                     + ([output_name(benchmark, "batch_fill.png")] if benchmark == BENCH2 else []))
    assert [p.name for p in paths] == expected
    assert sum(p.suffix == ".csv" for p in paths) == 20
    assert sum(p.suffix == ".png" for p in paths) == 7
    second = pd.read_csv(paths[-5])
    assert second["pauses_max_median"].tolist() == [4.0, 3.0]     # base (3 + 5) / 2; 1a (2 + 4) / 2
    assert plt.get_fignums() == []


def _mutate(rows, **change):
    rows = [dict(r) for r in rows]
    rows[-1].update(change)
    return rows


@pytest.mark.parametrize("make", [
    lambda rows: _mutate(rows, pauses_max="-1"),             # negative pause
    lambda rows: _mutate(rows, forks=""),                    # blank setting
    lambda rows: _mutate(rows, threads_blobshuffle="0"),     # invalid thread count
    lambda rows: rows + [dict(rows[0])],                     # duplicate observation
])
def test_invalid_input_creates_no_output_directory_and_no_figure(config, make):
    write_ledger(config, make(small_fixture()))
    with pytest.raises(ValueError):
        run(config)
    assert not config.output.exists()
    assert plt.get_fignums() == []


def test_invalid_second_benchmark_creates_nothing_for_the_first(config):
    config = replace(config, benchmarks=(BENCH, BENCH2))
    bad = ledger_rows([("base", 1, "r1", -2)], benchmark=BENCH2)
    write_ledger(config, small_fixture() + bad)
    with pytest.raises(ValueError, match="pauses_max"):
        run(config)
    assert not config.output.exists()


def test_requested_benchmark_without_rows_creates_nothing(config):
    config = replace(config, benchmarks=(BENCH, BENCH2))
    write_ledger(config, small_fixture())
    with pytest.raises(ValueError, match="mb2_rawFinalize"):
        run(config)
    assert not config.output.exists()


def test_missing_pause_column_creates_nothing(config):
    write_ledger(config, [{k: v for k, v in row.items() if k != "pauses_max"} for row in small_fixture()])
    with pytest.raises(ValueError, match="pauses_max"):
        run(config)
    assert not config.output.exists()


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_figure_closed_even_when_saving_fails(config, monkeypatch):
    write_ledger(config, small_fixture())

    def boom(self, *args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr("matplotlib.figure.Figure.savefig", boom)
    with pytest.raises(RuntimeError, match="boom"):
        run(config)
    assert plt.get_fignums() == []


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize("show, calls", [(False, 0), (True, 1)])
def test_show_only_when_requested_and_figure_still_closed(config, monkeypatch, show, calls):
    seen = []
    monkeypatch.setattr(plt, "show", lambda *a, **k: seen.append(len(plt.get_fignums())))
    write_ledger(config, small_fixture())
    run(replace(config, show=show))
    assert seen == ([1] * 3 * calls)                 # one figure open at show time, for the pauses, allocation and gc_time_pct plots
    assert plt.get_fignums() == []
