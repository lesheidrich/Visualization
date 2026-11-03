"""Hand-calculated speedup regressions; the draft is not a test oracle."""
from dataclasses import replace
import json
from pathlib import Path
import warnings

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from conftest import observation
from src.utils import normalize_throughput


def test_score_only_normalization_ignores_unused_bounds(config):
    frame = pd.DataFrame([observation(score_confidence_low="bad", score_error="bad")])
    with warnings.catch_warnings(record=True) as seen:
        actual = normalize_throughput(frame, config.benchmarks, config.labels, include_bounds=False)
    assert actual.score.tolist() == [100]
    assert not seen
    with pytest.raises(ValueError, match="score_confidence_low"):
        normalize_throughput(frame, config.benchmarks, config.labels)


def test_score_only_normalization_needs_no_bound_columns(config):
    frame = pd.DataFrame([observation()]).drop(columns=["score_confidence_low", "score_confidence_high"])
    with warnings.catch_warnings(record=True) as seen:
        actual = normalize_throughput(frame, config.benchmarks, config.labels, include_bounds=False)
    assert actual.score.tolist() == [100]
    assert not seen

from src.speedup import prepare, render, run


def pair_fixture():
    return pd.DataFrame([
        observation(rerun="01", score=100), observation("1a", "01", 2, 200),
        observation(rerun="instance-B", score=200), observation("1a", "instance-B", 2, 300),
    ])


def test_paired_ratio_regression_and_provenance(config):
    individual, average = prepare(pair_fixture(), config)
    strategy = individual[individual.strategy.eq("1a")]
    assert strategy.speedup.tolist() == [2, 1.5]
    assert strategy.baseline_score.tolist() == [100, 200]
    assert strategy.source_row.tolist() == [3, 5]
    assert strategy.baseline_source_row.tolist() == [2, 4]
    assert individual[individual.strategy.eq("base")].speedup.tolist() == [1, 1]
    summary = average[average.strategy.eq("1a")].iloc[0]
    assert summary.speedup == 1.6666666666666667  # 250 / 150
    assert summary.speedup != 1.75  # Old mean of ratios (2 + 1.5) / 2.
    assert summary.mean_score == 250.0  # (200 + 300) / 2
    assert summary.mean_baseline_score == 150.0  # (100 + 200) / 2
    assert summary.summary_kind == "ratio of mean scores across reruns"
    assert summary.n_reruns == summary.total_reruns == 2
    assert json.loads(summary.contributing_reruns) == ["01", "instance-B"]
    assert json.loads(summary.baseline_source_rows) == [2, 4]
    assert not any("confidence" in col or col == "score_error" for col in individual.columns)


def test_pairing_isolates_benchmarks_and_normalizes_units(config):
    config = replace(config, benchmarks=("mb1_rawNoFinalize", "mb2_rawFinalize"))
    rows = [observation(score=0.1, score_unit="ops/ms"),
            observation("1a", score=0.0002, score_unit="ops/µs"),
            observation(score=1000, benchmark_short="mb2_rawFinalize"),
            observation("1a", score=3000, benchmark_short="mb2_rawFinalize")]
    individual, _ = prepare(pd.DataFrame(rows), config)
    actual = individual[individual.strategy.eq("1a")]
    assert actual.speedup.tolist() == [2, 3]
    assert actual.baseline_score.tolist() == [100, 1000]
    assert actual.score_unit.tolist() == ["ops/s", "ops/s"]


@pytest.mark.parametrize("count,expected", [
    (1, 1.0),  # 100 / 100
    (3, 2.3333333333333335),  # (100 + 400 + 900) / (100 + 200 + 300) = 1400 / 600
    (10, 7.0),  # 3850 / 550
])
def test_all_reruns_and_separate_threads(config, count, expected):
    rows = [row for n in range(1, count + 1) for row in
            [observation(rerun=f"run-{n}", score=100 * n),
             observation("1a", f"run-{n}", 2, 100 * n * n),
             observation("1a", f"run-{n}", 4, 200 * n * n)]]
    individual, summary = prepare(pd.DataFrame(rows), config)
    assert len(individual) == count * 3
    for threads, multiplier in [(2, 1), (4, 2)]:
        row = summary[summary.threads_blobshuffle.eq(threads)].iloc[0]
        assert row.speedup == pytest.approx(expected * multiplier)
        assert row.n_reruns == row.total_reruns == count
        assert len(json.loads(row.contributing_reruns)) == count


def test_missing_numerator_warns_and_uses_available_ratios(config):
    rows = pair_fixture().to_dict("records") + [observation(rerun="third", score=100)]
    with pytest.warns(UserWarning, match="/thrpt missing"):
        individual, summary = prepare(pd.DataFrame(rows), config)
    row = summary[summary.strategy.eq("1a")].iloc[0]
    assert row.speedup == 1.6666666666666667  # 250 / 150; base 900 of "third" must not count
    assert row.mean_baseline_score == 150.0  # (100 + 200) / 2
    assert row.n_reruns == 2 and row.total_reruns == 3
    assert set(individual.rerun) == {"01", "instance-B", "third"}


@pytest.mark.parametrize("rows,match", [
    ([observation("1a", "unpaired", 2, 100)], "base_n01_runpaired/thrpt missing"),
    ([observation(score=0)], "Baseline score must be positive.*base_n01_r01"),
    ([observation(score=-1)], "score must be nonnegative.*mb1_rawNoFinalize.*01"),
    ([observation(), observation()], "Duplicate throughput observations.*mb1_rawNoFinalize.*01"),
    ([observation(score=1e-300), observation("1a", score=1e300)], "Nonfinite speedup"),
])
def test_invalid_pairings_fail(config, rows, match):
    with pytest.raises(ValueError, match=match):
        prepare(pd.DataFrame(rows), config)


def test_zero_numerator_and_base_at_other_threads(config):
    rows = [observation(), observation("1a", threads=2, score=0), observation(threads=2, score=150)]
    individual, _ = prepare(pd.DataFrame(rows), config)
    assert individual.speedup.tolist() == [1, 0, 1.5]


def test_ratio_of_means_edge_cases(config):
    def strategy_speedup(base, other):
        rows = [row for i, (b, o) in enumerate(zip(base, other)) for row in
                [observation(rerun=f"r{i}", score=b), observation("1a", f"r{i}", 2, o)]]
        _, summary = prepare(pd.DataFrame(rows), config)
        return summary, summary[summary.strategy.eq("1a")].speedup.iloc[0]

    assert strategy_speedup([100, 200], [0, 0])[1] == 0.0  # 0 / 150
    assert strategy_speedup([100], [250])[1] == 2.5  # 250 / 100
    assert strategy_speedup([100, 300], [100, 900])[1] == 2.5  # 500 / 200; mean of ratios would be 2.0
    summary, _ = strategy_speedup([100, 200], [200, 300])
    assert summary[summary.strategy.eq("base")].speedup.tolist() == [1.0]  # 150 / 150


def test_finite_large_means_do_not_overflow(config):
    rows = [observation(rerun="a", score=1e308), observation("1a", "a", 2, 1e308),
            observation(rerun="b", score=1e308), observation("1a", "b", 2, 1e308)]
    _, summary = prepare(pd.DataFrame(rows), config)
    assert summary[summary.strategy.eq("1a")].speedup.iloc[0] == 1.0  # 1e308 / 1e308


def test_speedup_ignores_optional_bounds_without_warnings(config):
    frame = pair_fixture()
    frame["score_confidence_low"] = "bad"
    frame["score_confidence_high"] = "not-an-interval"
    frame["score_error"] = "unused"
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        _, summary = prepare(frame, config)
    assert summary[summary.strategy.eq("1a")].speedup.iloc[0] == 1.6666666666666667  # 250 / 150
    # Only the Fieller warning (two reruns, base 100 and 200); nothing about the ledger bounds.
    assert len(seen) == 1 and str(seen[0].message).startswith("No Fieller interval for ")


def test_line_coordinates_gaps_references_and_labels(config):
    config = replace(config, colors={"1a": "red"})
    rows = [observation(), observation("1a", threads=1, score=50),
            observation("1a", threads=4, score=200), observation("2a", threads=2, score=100),
            observation("2a", threads=6, score=300), observation("2a", threads=8, score=400)]
    _, summary = prepare(pd.DataFrame(rows), config)
    fig = render(summary, config, "Average")
    try:
        ax = fig.axes[0]
        lines = {line.get_label(): line for line in ax.lines}
        np.testing.assert_equal(lines["Ideal (y=N)"].get_xdata(), [1, 2, 4, 6, 8])
        np.testing.assert_equal(lines["Ideal (y=N)"].get_ydata(), [1, 2, 4, 6, 8])
        np.testing.assert_equal(lines["Base N=1"].get_ydata(), [1, 1])
        np.testing.assert_equal(lines["S1_A"].get_ydata(), [0.5, np.nan, 2, np.nan, np.nan])
        assert lines["S1_A"].get_color() == "red"
        assert list(lines) == ["Ideal (y=N)", "Base N=1", "Base", "S1_A", "S2_A"]
        assert ax.get_ylim()[0] == 0
        assert "dimensionless" in ax.get_ylabel()
        assert not ax.texts  # n counts sit under the tick labels, not on the lines.
        assert not [s for c in ax.collections if hasattr(c, "get_segments")
                    for s in c.get_segments() if np.isfinite(s).all()]  # One rerun per cell: no CI, no bars.
    finally:
        plt.close(fig)


def test_unknown_strategy_and_colors_stable_across_reruns(config):
    rows = [observation(), observation("new", score=100), observation(rerun="B"),
            observation("1a", "B", score=100), observation("new", "B", score=100)]
    with pytest.warns(UserWarning, match="Unknown strategies"):
        with pytest.warns(UserWarning, match="/thrpt missing"):
            observations, _ = prepare(pd.DataFrame(rows), config)
    from src.utils import strategy_order
    order = strategy_order(observations, config)
    figs = [render(group, config, "Rerun", strategies=order, threads=[1])
            for _, group in observations.groupby("rerun")]
    try:
        colors = [{line.get_label(): line.get_color() for line in fig.axes[0].lines} for fig in figs]
        assert colors[0]["new"] == colors[1]["new"]
    finally:
        for fig in figs:
            plt.close(fig)


@pytest.mark.parametrize("formats", [("png",), ("pdf", "svg")])
def test_exports_and_figure_lifecycle(config, formats):
    config = replace(config, formats=formats)
    pair_fixture().to_csv(config.ledger, index=False)
    before = plt.get_fignums()
    paths = run(config)
    assert len(paths) == 3 * (len(formats) + 1)
    assert all(path.is_file() and path.stat().st_size > 0 for path in paths)
    assert all(path.name.startswith("speedup_") for path in paths)
    summary = pd.read_csv(next(path for path in paths if path.name.endswith("_avg.csv")))
    exported = summary[summary.strategy.eq("1a")].iloc[0]
    assert exported.speedup == 1.6666666666666667  # 250 / 150
    assert exported.mean_score == 250.0  # (200 + 300) / 2
    assert exported.mean_baseline_score == 150.0  # (100 + 200) / 2
    individual = pd.read_csv(next(path for path in paths if path.stem.endswith("_r01") and path.suffix == ".csv"))
    assert individual[individual.strategy.eq("1a")].baseline_source_row.iloc[0] == 2
    assert plt.get_fignums() == before


def test_invalid_pairing_creates_no_output(config):
    pd.DataFrame([observation("1a")]).to_csv(config.ledger, index=False)
    with pytest.raises(ValueError, match="thrpt missing"):
        run(config)
    assert not config.output.exists()


@pytest.mark.skipif(not (Path(__file__).parents[1] / "docs" / "jmh_sample.csv").exists(),
                    reason="docs/jmh_sample.csv is gitignored and absent from this clone")
def test_supplied_synthetic_ledger_supports_both_features():
    from pathlib import Path
    from src.config import load_config
    from src.utils import load_ledger
    from src.thrpt import prepare as prepare_throughput

    config = load_config(Path(__file__).parents[1] / "config.yaml")
    frame = load_ledger(config.ledger, config.delimiter)
    individual, average = prepare(frame, config)
    throughput, throughput_average = prepare_throughput(frame, config)
    # 820 sample rows minus the 100 of 2null (2 benchmarks x 5 thread counts x 10 reruns), which config.yaml excludes.
    assert len(individual) == len(throughput) == 720
    assert len(average) == len(throughput_average) == 72
    assert "2null" not in set(individual.strategy)
    assert set(individual.benchmark_short) == {"mb1_rawNoFinalize", "mb2_rawFinalize"}
    assert average.n_reruns.eq(10).all() and average.total_reruns.eq(10).all()
    assert individual[individual.strategy.eq("base")].speedup.eq(1).all()
    # Ratio of means recomputed from docs/jmh_sample.csv with the csv module:
    # sum of 1a scores at 8 threads / sum of base scores at 1 thread (10 reruns each).
    result = average[average.strategy.eq("1a") & average.threads_blobshuffle.eq(8)]
    by_benchmark = dict(zip(result.benchmark_short, result.speedup))
    assert by_benchmark["mb1_rawNoFinalize"] == pytest.approx(3.094952301886, rel=1e-10)
    assert by_benchmark["mb2_rawFinalize"] == pytest.approx(3.094952301886363, rel=1e-10)
    assert individual.run_id.str.startswith("SYNTHETIC-").all()


def _aggregate_render(config, rows, threads):
    _, summary = prepare(pd.DataFrame(rows), config)
    return render(summary, config, "mb1 — speedup, mean of reruns", threads=threads)


def _two_reruns(strategy, threads, score):
    return [observation(strategy, rerun, threads=threads, score=score + int(rerun == "B") * 10)
            for rerun in ("A", "B")]


def test_aggregate_speedup_errorbars_are_dark_thin_capped_and_on_top(config):
    rows = _two_reruns("base", 1, 100) + _two_reruns("1a", 1, 100) + _two_reruns("1a", 2, 150)
    fig = _aggregate_render(config, rows, [1, 2])
    try:
        ax = fig.axes[0]
        bars = [c for c in ax.collections if hasattr(c, "get_segments") and len(c.get_segments())]
        assert bars and all(c.get_linewidth()[0] == 1.2 for c in bars)
        assert all(c.get_zorder() > max(line.get_zorder() for line in ax.lines
                                                       if line.get_marker() != "_") for c in bars)
        caps = [line for line in ax.lines if line.get_marker() == "_"]
        assert caps and all(line.get_markeredgewidth() == 1.2 for line in caps)
        assert "mean of reruns" in ax.get_title() and "average" not in ax.get_title().lower()
    finally:
        plt.close(fig)


def test_plot_has_no_n_labels_and_partial_coverage_still_warns(config):
    rows = (_two_reruns("base", 1, 100) + _two_reruns("1a", 1, 100) + _two_reruns("1a", 2, 150)
            + _two_reruns("2a", 2, 150)[:1])
    with pytest.warns(UserWarning, match="/thrpt missing"):
        fig = _aggregate_render(config, rows, [1, 2])
    try:
        labels = [t.get_text() for t in fig.axes[0].get_xticklabels()]
        assert labels == ["1", "2"]
        assert not fig.axes[0].texts
    finally:
        plt.close(fig)
