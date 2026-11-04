"""gc_alloc_rate_norm: resampled CI columns, bar plot and its spec scenarios.

Layers: unit (CI, seed, slots), integration (prepare -> CSV), feature (CLI entry point in a temp
dir) and spec (one test per scenario of changes/add-gc-alloc-plot/specs/gc-metrics/spec.md).
Expected numbers are literals or are computed in this file by a separate resampling.
"""
from pathlib import Path
import io
import json
from contextlib import redirect_stdout
import warnings

import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import to_rgba
import numpy as np
import pandas as pd
import pytest
import yaml

from conftest import gc_observation
from src import cli, gc_metrics
from src.gc_metrics import ALLOC_CI, _compact_slots, prepare
from src.ci import T_METHOD, cell_seed

BENCH = "mb1_rawNoFinalize"
BENCH2 = "mb2_rawFinalize"
ROOT = Path(__file__).parents[1]
SAMPLE = ROOT / "docs" / "jmh_sample.csv"
CI_LOW, CI_HIGH = ALLOC_CI
T_LOW, T_HIGH = gc_metrics.ALLOC_T


@pytest.fixture(autouse=True)
def close_figures():
    yield
    plt.close("all")


def rows_of(specs, benchmark=BENCH):
    """specs: (strategy, threads, rerun, gc_alloc_rate_norm); "" is a blank value."""
    return [gc_observation(benchmark_short=benchmark, strategy=s, threads=t, rerun=r,
                           gc_alloc_rate_norm=str(v)) for s, t, r, v in specs]


def prepared(rows, config):
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        result = prepare(pd.DataFrame(rows), config)
    return result[1], [str(w.message) for w in record]


def written_out_ci(values, seed):
    """Separate 95% percentile bootstrap, 10,000 resamples of the reruns, written out here."""
    values = np.asarray(values, dtype=float)
    draws = np.random.default_rng(seed).integers(0, len(values), size=(len(values), 10000))
    means = np.empty(10000)
    for k in range(10000):
        means[k] = values[draws[:, k]].mean()
    return np.percentile(means, 2.5), np.percentile(means, 97.5)


# ================================================================ unit

def test_unit_ci_equals_separate_resampling_on_literal_fixture(config):
    values = [1000, 1200, 1400, 900, 1700]
    averages, _ = prepared(rows_of([("base", 1, f"r{i}", v) for i, v in enumerate(values)]), config)
    seed = cell_seed(BENCH, "base", 1, "gc_alloc_rate_norm")
    low, high = written_out_ci(values, seed)
    row = averages.iloc[0]
    assert row[CI_LOW] == pytest.approx(low, rel=1e-12)
    assert row[CI_HIGH] == pytest.approx(high, rel=1e-12)
    assert row["gc_alloc_rate_norm"] == pytest.approx(1240)       # (1000+1200+1400+900+1700)/5


def test_unit_ci_of_three_values_has_known_exact_bounds(config):
    # Resampling 1000, 1200, 1400 three times: P(all 1000) = P(all 1400) = 1/27 = 3.7% > 2.5%,
    # so the 2.5% and 97.5% quantiles of the resampled means are exactly 1000 and 1400.
    averages, _ = prepared(rows_of([("base", 1, "r1", 1000), ("base", 1, "r2", 1200),
                                    ("base", 1, "r3", 1400)]), config)
    assert (averages[CI_LOW].iloc[0], averages[CI_HIGH].iloc[0]) == (1000, 1400)


def test_unit_seed_determinism(config):
    rows = rows_of([("base", 1, f"r{i}", v) for i, v in enumerate([5, 9, 4, 12, 7])])
    first, _ = prepared(rows, config)
    second, _ = prepared(rows, config)
    assert first[[CI_LOW, CI_HIGH]].equals(second[[CI_LOW, CI_HIGH]])
    # The seed is the one of the group, so another group name gives another resampling.
    other, _ = prepared(rows_of([("1a", 1, f"r{i}", v) for i, v in enumerate([5, 9, 4, 12, 7])]), config)
    assert written_out_ci([5, 9, 4, 12, 7], cell_seed(BENCH, "1a", 1, "gc_alloc_rate_norm"))[0] \
        == pytest.approx(other[CI_LOW].iloc[0], rel=1e-12)
    assert cell_seed(BENCH, "1a", 1, "gc_alloc_rate_norm") != cell_seed(BENCH, "base", 1, "gc_alloc_rate_norm")


def test_unit_no_spread_gives_blank_ci_and_names_the_group(config):
    averages, messages = prepared(rows_of([("base", 2, f"r{i}", 700) for i in range(3)]), config)
    assert np.isnan(averages[[CI_LOW, CI_HIGH]].iloc[0]).all()
    assert averages["gc_alloc_rate_norm"].iloc[0] == 700
    assert any(BENCH in m and "base" in m and "threads=2" in m and "no spread" in m for m in messages)


def test_unit_one_rerun_gives_blank_ci_and_names_the_group(config):
    averages, messages = prepared(rows_of([("1a", 4, "r1", 700)]), config)
    assert np.isnan(averages[[CI_LOW, CI_HIGH]].iloc[0]).all()
    assert any(BENCH in m and "1a" in m and "threads=4" in m and "fewer than two reruns" in m
               for m in messages)


def test_unit_blank_contributor_gives_missing_average_no_ci_and_no_crash(config):
    averages, messages = prepared(rows_of([("base", 1, "r1", 1000), ("base", 1, "r2", ""),
                                           ("base", 1, "r3", 1400)]), config)
    row = averages.iloc[0]
    assert np.isnan(row["gc_alloc_rate_norm"])
    assert np.isnan(row[CI_LOW]) and np.isnan(row[CI_HIGH])
    assert any("Mean of reruns of gc_alloc_rate_norm missing" in m for m in messages)
    assert any("No gc_alloc_rate_norm CI" in m and "a contributor lacks" in m for m in messages)


def test_unit_slot_centring():
    w = 0.8 / 3
    slots = _compact_slots(["a", "b", "c"], [1, 2], lambda s, n: not (s == "a" and n == 2), w)
    assert slots[("a", 1)] == pytest.approx(-w) and slots[("b", 1)] == pytest.approx(0)
    assert slots[("c", 1)] == pytest.approx(w)
    assert ("a", 2) not in slots
    assert slots[("b", 2)] == pytest.approx(1 - w / 2) and slots[("c", 2)] == pytest.approx(1 + w / 2)
    assert (slots[("b", 2)] + slots[("c", 2)]) / 2 == pytest.approx(1)      # centred on the group


# ================================================================ integration

def literal_rows():
    return rows_of([
        ("base", 1, "r1", 1000), ("base", 1, "r2", 1200), ("base", 1, "r3", 1400),
        ("1a", 1, "r1", 500), ("1a", 1, "r2", 500), ("1a", 1, "r3", 500),       # no spread
        ("1a", 2, "r1", 800), ("1a", 2, "r2", 1000), ("1a", 2, "r3", 1200),     # base missing at 2
        ("2a", 2, "r1", 50), ("2a", 2, "r2", 60), ("2a", 2, "r3", 70),
    ])


def test_integration_average_gains_exactly_the_expected_columns_and_nothing_else_changes(config):
    averages, _ = prepared(literal_rows(), config)
    assert averages.columns.tolist() == [
        "benchmark_short", "strategy", "threads_blobshuffle", *gc_metrics.AVERAGED,
        "n_reruns", "total_reruns", "contributing_reruns", "source_rows", "source_files",
        "original_strategies", "summary_kind", "gc_alloc_rate_norm_ci_low", "gc_alloc_rate_norm_ci_high",
        *[f"{m}_t_{e}" for m in gc_metrics.AVERAGED for e in ("low", "high")],
        *[f"{m}_ci_{e}" for m in gc_metrics.AVERAGED if m != "gc_alloc_rate_norm" for e in ("low", "high")]]
    base = averages[averages["strategy"].eq("base")].iloc[0]
    # Literal values of the unchanged columns (defaults: gc_count 10, gc_time 2500, pauses 5/2, fill 1).
    assert (base["gc_alloc_rate_norm"], base["gc_count"], base["gc_time"], base["gc_time_pct"],
            base["pauses_max"], base["pauses_p99"], base["batch_fill"], base["score"]) == \
        (1200, 10, 2500, 1.0, 5, 2, 1.0, 100)
    assert (base["n_reruns"], base["total_reruns"]) == (3, 3)
    assert base["contributing_reruns"] == json.dumps(["r1", "r2", "r3"])
    assert base["summary_kind"] == "mean of reruns"
    assert len(averages) == 4


# ================================================================ feature (CLI entry point)

def make_project(tmp_path, ledger_rows, colors=None, formats=("png",), benchmarks=(BENCH,)):
    pd.DataFrame(ledger_rows).to_csv(tmp_path / "ledger.csv", index=False)
    settings = {"input": {"path": "ledger.csv"}, "output": {"directory": "out", "formats": list(formats)},
                "benchmarks": list(benchmarks), "plot": {"figsize": [6, 4], "dpi": 40}}
    if colors:
        settings["strategies"] = {"colors": colors}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(settings))
    return tmp_path / "config.yaml"


def run_cli(config_path, monkeypatch, capture=True):
    """Run `gc` through the entry point; return (exit code, written paths, {file name: figure})."""
    figures = {}
    real = gc_metrics.save_figure

    def spy(fig, stem, config):
        figures[Path(stem).name] = fig
        return real(fig, stem, config)

    monkeypatch.setattr(gc_metrics, "save_figure", spy)
    out = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with redirect_stdout(out):
            code = cli.gc_main(["--config", str(config_path)])
    return code, [Path(line) for line in out.getvalue().splitlines()], figures


def bars_of(fig):
    """[(x centre, height, face colour)] of the bars, in drawing order."""
    ax = fig.axes[0]
    return [(p.get_x() + p.get_width() / 2, p.get_height(), p.get_facecolor()) for p in ax.patches]


def error_segments(fig):
    ax = fig.axes[0]
    return [seg for c in ax.collections if isinstance(c, LineCollection) for seg in c.get_segments()]


def test_feature_files_exist_per_benchmark_and_format(tmp_path, monkeypatch):
    rows = literal_rows() + rows_of([("base", 1, "r1", 10), ("base", 1, "r2", 30)], benchmark=BENCH2)
    config = make_project(tmp_path, rows, formats=("png", "svg"), benchmarks=(BENCH, BENCH2))
    code, paths, figures = run_cli(config, monkeypatch)
    assert code == 0
    names = {p.name for p in paths}
    for benchmark in (BENCH, BENCH2):
        for extension in ("png", "svg"):
            assert f"gc_{benchmark}_gc_alloc_rate_norm.{extension}" in names
    assert all(p.is_file() for p in paths) and len({p.parent for p in paths}) == 1
    assert sum("gc_alloc_rate_norm." in n for n in names) == 4          # no per-rerun alloc plots
    assert sorted(figures) == sorted([f"gc_{b}_{part}" for b in (BENCH, BENCH2)
                                      for part in ("pauses_max", "gc_alloc_rate_norm", "gc_time_pct")]
                                     + [f"gc_{BENCH2}_batch_fill"])      # batch fill: mb2 only


def test_feature_bars_error_bars_axes_order_and_colours(tmp_path, monkeypatch):
    colors = {"base": "#ff0000", "1a": "#00ff00", "2a": "#0000ff"}
    config = make_project(tmp_path, literal_rows(), colors=colors)
    code, paths, figures = run_cli(config, monkeypatch)
    assert code == 0
    fig = figures[f"gc_{BENCH}_gc_alloc_rate_norm"]
    ax = fig.axes[0]
    csv = pd.read_csv(next(p for p in paths if p.name == f"gc_{BENCH}_avg.csv"), dtype={"strategy": str})
    assert ax.get_yscale() == "linear" and ax.get_ylim()[0] == 0
    assert ax.get_ylabel() == "gc_alloc_rate_norm (B/op)"
    assert ax.get_xlabel() == "BlobShuffle threads"
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1", "2"]
    assert ax.get_title().split("\n") == [f"{BENCH} — gc_alloc_rate_norm, mean of reruns",
                                          f"Error bars: {T_METHOD}"]
    assert not ax.texts and len(fig.texts) == 0                        # no explanatory notes
    # Legend: configured order and labels (Base, S1_A, S2_A).
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["Base", "S1_A", "S2_A"]
    # Bars: heights equal the CSV means; x positions and colours follow slots and strategy colours.
    w = 0.8 / 3
    # Group 1 holds base and 1a, group 2 holds 1a and 2a; each pair is centred on its tick.
    expected = {("base", 1): (-w / 2, "#ff0000"), ("1a", 1): (w / 2, "#00ff00"),
                ("1a", 2): (1 - w / 2, "#00ff00"), ("2a", 2): (1 + w / 2, "#0000ff")}
    bars = bars_of(fig)
    assert len(bars) == 4                                              # base at 2 has no bar
    by_key = {}
    for (strategy, threads), (x, colour) in expected.items():
        mean = csv[csv["strategy"].eq(strategy) & csv["threads_blobshuffle"].eq(threads)]["gc_alloc_rate_norm"].iloc[0]
        match = [b for b in bars if abs(b[0] - x) < 1e-9]
        assert len(match) == 1 and match[0][1] == pytest.approx(mean)
        assert match[0][2] == to_rgba(colour)
        by_key[(strategy, threads)] = mean
    assert by_key == pytest.approx({("base", 1): 1200, ("1a", 1): 500, ("1a", 2): 1000, ("2a", 2): 60})
    assert all(h > 0 for _, h, _ in bars)                              # bars are visible
    # Error bars: equal the CSV t interval where drawn; none for the no-spread group (1a at 1 thread).
    segments = {round(seg[0][0], 9): (seg[0][1], seg[1][1]) for seg in error_segments(fig)}
    assert len(segments) == 3
    for (strategy, threads), (x, _) in expected.items():
        row = csv[csv["strategy"].eq(strategy) & csv["threads_blobshuffle"].eq(threads)].iloc[0]
        if pd.isna(row[T_LOW]):
            assert round(x, 9) not in segments
        else:
            assert segments[round(x, 9)] == pytest.approx((row[T_LOW], row[T_HIGH]))
    errorbars = [c for c in ax.containers if c.__class__.__name__ == "ErrorbarContainer"]
    assert errorbars and all(c.lines[2][0].get_linewidth()[0] <= 2 for c in errorbars)   # thin
    assert all(len(c.lines[1]) == 2 for c in errorbars)                                  # with caps


def test_feature_missing_combination_has_no_bar_and_present_strategies_are_centred(tmp_path, monkeypatch):
    config = make_project(tmp_path, literal_rows())
    _, _, figures = run_cli(config, monkeypatch)
    xs = sorted(round(b[0], 9) for b in bars_of(figures[f"gc_{BENCH}_gc_alloc_rate_norm"]))
    w = 0.8 / 3
    # Group 1 (base, S1_A) and group 2 (S1_A, S2_A) are each centred on their tick.
    assert xs == pytest.approx([-w / 2, w / 2, 1 - w / 2, 1 + w / 2])


def test_feature_no_error_bar_title_without_any_bar(tmp_path, monkeypatch):
    rows = rows_of([("base", 1, f"r{i}", 500) for i in range(3)])
    _, _, figures = run_cli(make_project(tmp_path, rows), monkeypatch)
    fig = figures[f"gc_{BENCH}_gc_alloc_rate_norm"]
    assert fig.axes[0].get_title() == f"{BENCH} — gc_alloc_rate_norm, mean of reruns"
    assert error_segments(fig) == []


def test_feature_blank_contributor_has_no_bar_and_no_error_bar(tmp_path, monkeypatch):
    rows = rows_of([("base", 1, "r1", 1000), ("base", 1, "r2", ""), ("base", 1, "r3", 1400),
                    ("1a", 1, "r1", 10), ("1a", 1, "r2", 20), ("1a", 1, "r3", 30)])
    code, paths, figures = run_cli(make_project(tmp_path, rows), monkeypatch)
    assert code == 0
    fig = figures[f"gc_{BENCH}_gc_alloc_rate_norm"]
    assert [round(b[1], 9) for b in bars_of(fig)] == [20]
    assert len(error_segments(fig)) == 1
    avg = pd.read_csv(next(p for p in paths if p.name.endswith("_avg.csv")), dtype={"strategy": str})
    base = avg[avg["strategy"].eq("base")].iloc[0]
    assert pd.isna(base["gc_alloc_rate_norm"]) and pd.isna(base[CI_LOW]) and pd.isna(base[CI_HIGH])


def old_run(monkeypatch):
    """The run without the feature: no allocation plot (an empty figure) and blank CI values."""
    monkeypatch.setattr(gc_metrics, "render_alloc", lambda *a, **k: plt.figure())
    monkeypatch.setattr(gc_metrics, "_alloc_ci", lambda *a, **k: (np.nan, np.nan))


def compare_with_old(config_path, monkeypatch, tmp_path):
    _, new_paths, _ = run_cli(config_path, monkeypatch)
    new_dir = new_paths[0].parent
    monkeypatch.undo()
    old_run(monkeypatch)
    code, old_paths, _ = run_cli(config_path, monkeypatch)
    old_dir = old_paths[0].parent
    assert new_dir != old_dir
    for old in old_paths:
        if "gc_alloc_rate_norm." in old.name:
            continue                                                   # the new plot is the feature
        new = new_dir / old.name
        if old.suffix == ".svg":
            assert new.is_file()                                       # SVG ids and date differ per run
            continue
        if old.name.endswith("_avg.csv"):
            new_frame, old_frame = pd.read_csv(new), pd.read_csv(old)
            assert old_frame[[CI_LOW, CI_HIGH]].isna().all().all()    # the old run has no CI values
            t_columns = [f"{m}_t_{e}" for m in gc_metrics.AVERAGED for e in ("low", "high")]
            later = [f"{m}_ci_{e}" for m in gc_metrics.AVERAGED if m != "gc_alloc_rate_norm" for e in ("low", "high")]
            assert new_frame.columns.tolist()[-len(t_columns) - len(later) - 2:] == [CI_LOW, CI_HIGH, *t_columns, *later]
            pd.testing.assert_frame_equal(new_frame.drop(columns=[CI_LOW, CI_HIGH]),
                                          old_frame.drop(columns=[CI_LOW, CI_HIGH]))
        else:
            assert new.read_bytes() == old.read_bytes(), old.name
    return new_paths


def test_feature_existing_outputs_are_unchanged_on_literal_ledger(tmp_path, monkeypatch):
    paths = compare_with_old(make_project(tmp_path, literal_rows()), monkeypatch, tmp_path)
    assert any(p.name.endswith("pauses_max.png") for p in paths)


@pytest.mark.skipif(not SAMPLE.exists(), reason="docs/jmh_sample.csv is not in this clone")
def test_feature_sample_ledger(tmp_path, monkeypatch):
    settings = yaml.safe_load((ROOT / "config.yaml").read_text())
    settings["input"]["path"] = str(SAMPLE)
    settings["output"]["directory"] = str(tmp_path / "out")
    settings["output"]["formats"] = ["png", "svg"]
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(settings))
    paths = compare_with_old(config_path, monkeypatch, tmp_path)
    names = {p.name for p in paths}
    for benchmark in settings["benchmarks"]:
        for extension in ("png", "svg"):
            assert f"gc_{benchmark}_gc_alloc_rate_norm.{extension}" in names
    # Bar heights and error bars equal the CSV on the real sample.
    monkeypatch.undo()
    _, paths, figures = run_cli(config_path, monkeypatch)
    for benchmark in settings["benchmarks"]:
        avg = pd.read_csv(next(p for p in paths if p.name == f"gc_{benchmark}_avg.csv"),
                          dtype={"strategy": str})
        fig = figures[f"gc_{benchmark}_gc_alloc_rate_norm"]
        ax = fig.axes[0]
        assert ax.get_yscale() == "linear" and ax.get_ylim()[0] == 0
        known = avg[avg["gc_alloc_rate_norm"].notna()]
        assert sorted(round(b[1], 6) for b in bars_of(fig)) == \
            sorted(round(v, 6) for v in known["gc_alloc_rate_norm"])
        drawn = sorted((round(s[0][1], 6), round(s[1][1], 6)) for s in error_segments(fig))
        csv = sorted((round(a, 6), round(b, 6)) for a, b in
                     zip(known["gc_alloc_rate_norm_t_low"].dropna(),
                         known["gc_alloc_rate_norm_t_high"].dropna()))
        assert drawn == csv


# ================================================================ spec scenarios

def test_spec_allocation_bar_plot_linear_axis_from_zero(tmp_path, monkeypatch):
    _, paths, figures = run_cli(make_project(tmp_path, literal_rows()), monkeypatch)
    fig = figures[f"gc_{BENCH}_gc_alloc_rate_norm"]
    ax = fig.axes[0]
    assert ax.get_yscale() == "linear" and ax.get_ylim()[0] == 0
    assert sorted(b[1] for b in bars_of(fig)) == pytest.approx([60, 500, 1000, 1200])  # group means


def test_spec_allocation_bar_plot_missing_combination(tmp_path, monkeypatch):
    test_feature_missing_combination_has_no_bar_and_present_strategies_are_centred(tmp_path, monkeypatch)


def test_spec_allocation_error_bars_no_spread(tmp_path, monkeypatch):
    rows = rows_of([("base", 1, f"r{i}", 700) for i in range(3)]
                   + [("1a", 1, "r1", 100), ("1a", 1, "r2", 300)])
    config = make_project(tmp_path, rows)
    pd.DataFrame(rows).to_csv(tmp_path / "ledger.csv", index=False)
    figures = {}
    real = gc_metrics.save_figure
    monkeypatch.setattr(gc_metrics, "save_figure",
                        lambda fig, stem, c: figures.setdefault(Path(stem).name, fig) and real(fig, stem, c))
    with pytest.warns(UserWarning, match=rf"{BENCH}/base/threads=1.*no spread"):
        assert cli.gc_main(["--config", str(config)]) == 0
    fig = figures[f"gc_{BENCH}_gc_alloc_rate_norm"]
    assert sorted(b[1] for b in bars_of(fig)) == [200, 700]            # the no-spread bar is drawn
    assert len(error_segments(fig)) == 1                               # only 1a has an error bar


def test_spec_all_rerun_averages_mean_over_reruns(config):
    averages, _ = prepared(rows_of([("base", 1, "r1", 1000), ("base", 1, "r2", 1200),
                                    ("base", 1, "r3", 1400)]), config)
    assert averages["gc_alloc_rate_norm"].iloc[0] == 1200 and averages["n_reruns"].iloc[0] == 3


def test_spec_all_rerun_averages_incomplete_coverage(config):
    rows = rows_of([("base", 4, "r1", 1000), ("base", 4, "r2", 1400), ("1a", 4, "r1", 1),
                    ("1a", 4, "r2", 2), ("1a", 4, "r3", 3)])
    averages, messages = prepared(rows, config)
    row = averages[averages["strategy"].eq("base")].iloc[0]
    assert (row["gc_alloc_rate_norm"], row["n_reruns"], row["total_reruns"]) == (1200, 2, 3)
    assert any("base_n04_r" in m and "/thrpt missing." in m for m in messages) or any("/thrpt missing." in m for m in messages)


def test_spec_all_rerun_averages_contributor_lacks_a_metric(config):
    rows = [gc_observation(strategy="base", rerun=r, pauses_max=p, gc_alloc_rate_norm=a)
            for r, p, a in (("r1", "1", "100"), ("r2", "", "200"), ("r3", "3", "300"))]
    averages, _ = prepared(rows, config)
    assert np.isnan(averages["pauses_max"].iloc[0])
    assert averages["gc_alloc_rate_norm"].iloc[0] == 200 and averages["gc_time"].iloc[0] == 2500


# ================================================================ help text

def test_help_describes_what_gc_now_does():
    text = cli.COMMANDS["gc"]
    for part in ("Tabulate", "pauses_max", "gc_alloc_rate_norm", "mean of reruns",
                 "batch_fill as mean of reruns for mb2_rawFinalize"):
        assert part in text
