from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.thrpt import prepare, render, run
from src.utils import load_ledger
from conftest import observation


def test_numerical_regression(config):
    frame = load_ledger(Path(__file__).parent / "fixtures/throughput.csv")
    _, result = prepare(frame, config)
    assert result["strategy"].tolist() == ["base", "1a"]
    assert result["threads_blobshuffle"].tolist() == [1, 2]
    assert result["score"].tolist() == [150, 500]
    assert "score_confidence_low" not in result.columns
    assert "score_confidence_high" not in result.columns
    assert result["ci_low"].tolist() == [100, 400]  # two reruns: resampled means 100/150/200 and 400/500/600
    assert result["ci_high"].tolist() == [200, 600]
    assert result["n_reruns"].tolist() == [2, 2]
    assert json.loads(result["contributing_reruns"].iloc[0]) == ["01", "02"]
    assert result["interval_kind"].unique().tolist() == ["95% percentile bootstrap of reruns"]


@pytest.mark.parametrize("count", [1, 3, 10])
def test_dynamic_rerun_count_and_thread_groups(config, count):
    frame = pd.DataFrame([observation(rerun=f"instance-{i}", threads=n, score=(i + 1) * n * 100)
                          for i in range(count) for n in [1, 2]])
    observations, result = prepare(frame, config)
    assert observations["rerun"].nunique() == count
    assert result["n_reruns"].tolist() == [count, count]
    assert result["score"].tolist() == [(count + 1) * 50, (count + 1) * 100]


def test_all_three_scores_are_used(config):
    frame = pd.DataFrame([observation(rerun=str(i), score=n) for i, n in enumerate([100, 200, 600])])
    assert prepare(frame, config)[1]["score"].iloc[0] == 300


def test_missing_bounds_keep_all_scores(config):
    frame = pd.DataFrame([observation(), observation(rerun="B", score=200, score_confidence_low="")])
    with pytest.warns(UserWarning, match="bound") as messages:
        observations, result = prepare(frame, config)
    assert not any("Aggregate bounds omitted" in str(m.message) for m in messages)
    assert observations["score"].tolist() == [100, 200]
    assert result["score"].iloc[0] == 150
    # Missing ledger bounds no longer matter for the across-reruns CI (scores 100 and 200).
    assert result["ci_low"].iloc[0] == 100 and result["ci_high"].iloc[0] == 200


def test_missing_bound_columns(config, ledger):
    with pytest.warns(UserWarning):
        observations, result = prepare(ledger.drop(columns=["score_confidence_low", "score_confidence_high"]), config)
    assert observations["score"].iloc[0] == result["score"].iloc[0] == 100


def test_incomplete_coverage(config):
    frame = pd.DataFrame([observation(), observation(rerun="B"), observation(strategy="1a", score=200)])
    with pytest.warns(UserWarning, match="Incomplete coverage"):
        _, result = prepare(frame, config)
    missing = result[result["strategy"].eq("1a")].iloc[0]
    assert missing["n_reruns"] == 1 and missing["total_reruns"] == 2
    assert missing["score"] == 200


def test_requested_empty_benchmark(config, ledger):
    with pytest.raises(ValueError, match="No throughput results.*mb2"):
        prepare(ledger, replace(config, benchmarks=("mb2_rawFinalize",)))


def test_plot_uses_prepared_values_bounds_labels_and_missing_positions(config):
    import matplotlib.pyplot as plt
    from matplotlib.colors import to_rgba

    frame = load_ledger(Path(__file__).parent / "fixtures/throughput.csv")
    _, averages = prepare(frame, config)
    config = replace(config, colors={"base": "red", "1a": "blue"})
    fig = render(averages, config, "mb1 — mean of reruns", strategies=["base", "1a"], threads=[1, 2])
    try:
        ax = fig.axes[0]
        assert [bar.get_height() for bar in ax.patches] == [150, 500]
        assert [bar.get_facecolor() for bar in ax.patches] == [to_rgba("red"), to_rgba("blue")]
        assert [text.get_text() for text in ax.get_xticklabels()] == ["1", "2"]
        assert ax.get_xlabel() == "BlobShuffle threads"
        assert ax.get_ylabel() == "Throughput (ops/s)"
        assert "Error bars: 95% percentile bootstrap of reruns" in ax.get_title()
        assert "mean of reruns" in ax.get_title() and "average" not in ax.get_title().lower()
        assert not ax.texts  # No n labels on the plot.
        assert ax.get_ylim()[0] == 0
        assert [text.get_text() for text in ax.get_legend().get_texts()] == ["Base", "S1_A"]
        segments = [segment for collection in ax.collections for segment in collection.get_segments()]
        np.testing.assert_allclose([segment[:, 1] for segment in segments], [[100, 200], [400, 600]])  # ci_low, ci_high
        np.testing.assert_allclose([bar.get_x() + bar.get_width() / 2 for bar in ax.patches], [-0.2, 1.2])
        assert len(ax.patches) == 2  # No zero-filled bars for the absent combinations.
    finally:
        plt.close(fig)


def test_exports_match_plotted_tables_and_close_figures(config):
    import matplotlib.pyplot as plt

    pd.DataFrame([observation(rerun="r/1"), observation(rerun="r?2", score=200)]).to_csv(config.ledger, index=False)
    config = replace(config, formats=("png", "pdf", "svg"))
    paths = run(config)
    assert len(paths) == 12
    assert len(set(paths)) == 12
    assert all(path.stat().st_size > 0 for path in paths)
    average = next(path for path in paths if path.name.endswith("_avg.csv"))
    result = pd.read_csv(average)
    assert result["score"].tolist() == [150]
    assert json.loads(result["contributing_reruns"].iloc[0]) == ["r/1", "r?2"]
    individual = pd.read_csv(next(p for p in paths if p.suffix == ".csv" and "_rr_" in p.name), dtype=str)
    assert individual["original_score_unit"].iloc[0] == "ops/s"
    assert individual["source_file"].iloc[0] == str(config.ledger)
    assert plt.get_fignums() == []
