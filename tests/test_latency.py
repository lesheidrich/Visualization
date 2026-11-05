"""Change add-tail-latency: `uv run latency` tables and plots from sample rows.

Expected values are literal, derived by hand in the comments. t(2) = 4.302653. On 100, 100, 400 the
mean is 200, the SD 173.2051, so t half width = 4.302653 * 173.2051 / sqrt(3) = 430.2653. Resampled
means of three draws have P(all 100) = 8/27 > 2.5% and P(all 400) = 1/27 > 2.5%, so the percentile
bootstrap is exactly [100, 400]."""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from matplotlib.collections import PolyCollection
from matplotlib.colors import to_rgba

from audit_support import read_csv, run_main, write_run
from src import cli, latency
from src.ci import T_METHOD, CI_METHOD, bootstrap_ci, cell_seed
from src.config import load_config
from src.utils import PlainLogFormatter

MB1 = "mb1_rawNoFinalize"
MB2 = "mb2_rawFinalize"
ROOT = Path(__file__).parents[1]
RAW3 = ROOT / "docs" / "raw3.csv"
P = ("p50", "p90", "p95", "p99", "p99_9", "p99_99", "p100")
BASE_VALUES = (100, 110, 120, 130, 140, 150, 160)
TEN = [1, 2, 3.5, 5, 9, 11, 14, 17, 23, 31]  # Ten distinct reruns: the resampled CI depends on seed and order.


def row(strategy="1a", threads=1, rerun="01", values=BASE_VALUES, unit="ns/op", benchmark=MB1, mode="sample", **extra):
    return {"strategy": strategy, "rerun": rerun, "benchmark_short": benchmark, "mode": mode,
            "threads_blobshuffle": str(threads), "score": "1", "score_unit": unit,
            **{p: str(v) for p, v in zip(P, values)}, **extra}


def group(strategy, threads, scale_by_rerun, **kw):
    """One row per rerun; each percentile is its base value times that rerun's scale."""
    return [row(strategy, threads, f"{i + 1:02d}", tuple(v * s for v in BASE_VALUES), **kw)
            for i, s in enumerate(scale_by_rerun)]


def run(tmp_path, monkeypatch, capsys, rows, method=None, benchmarks=(MB1,), formats=("png",)):
    """Return (exit code, folder, warnings, {stem: figure}, stderr)."""
    figures = {}
    real = latency.save_figure

    def spy(fig, stem, config):
        figures[Path(stem).name] = fig
        return real(fig, stem, config)

    monkeypatch.setattr(latency, "save_figure", spy)
    place = tmp_path / (method or "default")
    place.mkdir()
    path = write_run(place, rows, benchmarks=benchmarks, ci_method=method, formats=formats)
    code, _, messages, stderr = run_main(cli.latency_main, path, capsys)
    out = place / "out"
    folders = list(out.iterdir()) if out.exists() else []
    return code, folders[0] if folders else None, messages, figures, stderr


def bands(ax):
    return [c for c in ax.collections if isinstance(c, PolyCollection)]


def band_ends(band):
    """(x, low, high) of a fill_between polygon."""
    vertices = band.get_paths()[0].vertices
    result = {}
    for x, y in vertices:
        result.setdefault(round(float(x), 6), []).append(float(y))
    return {x: (min(ys), max(ys)) for x, ys in result.items()}


# ------------------------------------------------------------------------ unit: reading rows


@pytest.mark.parametrize("unit, value, expected", [("ns/op", 174, 174), ("us/op", 0.174, 174),
                                                   ("µs/op", 0.174, 174), ("ms/op", 0.000174, 174),
                                                   ("s/op", 1.74e-7, 174)])
def test_spec_unit_conversion(config, unit, value, expected):
    frame = pd.DataFrame([row(values=(value,) * 7, unit=unit)])
    out = latency.normalize_sample(frame, config)
    assert out["p50"].iloc[0] == pytest.approx(expected)
    assert out["latency_unit"].iloc[0] == "ns/op" and out["original_score_unit"].iloc[0] == unit


def test_spec_unsupported_unit_names_the_row(config):
    with pytest.raises(ValueError, match=r"Unsupported score_unit at ledger row\(s\) \[2\]"):
        latency.normalize_sample(pd.DataFrame([row(unit="ops/s")]), config)


def test_spec_decreasing_percentiles_warn_and_keep_numbers(config):
    frame = pd.DataFrame([row(values=(100, 200, 150, 300, 400, 500, 600))])
    with pytest.warns(UserWarning, match=r"Percentiles decrease at ledger row\(s\) \[2\]"):
        out = latency.normalize_sample(frame, config)
    assert out["p95"].iloc[0] == 150


@pytest.mark.parametrize("bad", ["", "abc", "nan", "inf"])
def test_unit_bad_percentile_names_the_row(config, bad):
    rows = row()
    rows["p99"] = bad
    with pytest.raises(ValueError, match=r"Invalid p99 at ledger row\(s\) \[2\]"):
        latency.normalize_sample(pd.DataFrame([rows]), config)


def test_unit_negative_percentile_names_the_row(config):
    with pytest.raises(ValueError, match=r"p50 must be nonnegative at ledger row\(s\) \[2\]"):
        latency.normalize_sample(pd.DataFrame([row(values=(-1, 1, 1, 1, 1, 1, 1))]), config)


def test_unit_duplicates_name_the_rows(config):
    with pytest.raises(ValueError, match=r"Duplicate sample observations at ledger row\(s\) \[2, 3\]"):
        latency.normalize_sample(pd.DataFrame([row(), row()]), config)


def test_unit_missing_column(config):
    frame = pd.DataFrame([row()]).drop(columns="p99_99")
    with pytest.raises(ValueError, match=r"Missing ledger column\(s\): \['p99_99'\]"):
        latency.normalize_sample(frame, config)


def test_spec_throughput_rows_are_ignored(tmp_path, monkeypatch, capsys):
    rows = group("1a", 1, [1, 1, 4])
    bad = row("1a", 1, "01", mode="thrpt")
    bad["p50"] = "garbage"
    _, one, _, _, _ = run(tmp_path, monkeypatch, capsys, rows)
    _, two, _, _, _ = run(tmp_path, monkeypatch, capsys, rows + [bad], method="t")
    cols = [c for c in read_csv(one / f"latency_{MB1}_avg.csv") if c.startswith("p")]
    assert read_csv(one / f"latency_{MB1}_avg.csv")[cols].equals(read_csv(two / f"latency_{MB1}_avg.csv")[cols])


def test_spec_no_sample_rows(tmp_path, monkeypatch, capsys):
    code, folder, _, _, stderr = run(tmp_path, monkeypatch, capsys, [row(mode="thrpt")])
    assert code == 1 and folder is None and "No sample results for benchmark(s)" in stderr
    assert not (tmp_path / "default" / "out").exists()


def test_unit_excluded_strategy_is_gone_before_checks(tmp_path):
    from src.config import Config
    config = Config(tmp_path, tmp_path, benchmarks=(MB1,), exclude=("2null",))
    broken = row("2NULL ", 1)
    broken["p50"] = "garbage"
    out = latency.normalize_sample(pd.DataFrame([row("1a", 1), broken]), config)
    assert out["strategy"].tolist() == ["1a"]


# ------------------------------------------------------------------------ tables


def test_spec_literal_means_and_intervals(tmp_path, monkeypatch, capsys):
    rows = [row("1a", 1, f"{i + 1:02d}", (10, 20, 30, v, 50, 60, 70)) for i, v in enumerate((100, 100, 400))]
    _, folder, _, _, _ = run(tmp_path, monkeypatch, capsys, rows)
    table = read_csv(folder / f"latency_{MB1}_avg.csv")
    assert table.shape[0] == 1
    assert float(table.p99[0]) == 200
    assert (float(table.p99_ci_low[0]), float(table.p99_ci_high[0])) == (100, 400)
    assert (float(table.p99_t_low[0]), float(table.p99_t_high[0])) == pytest.approx((-230.2653, 630.2653), abs=1e-3)
    assert table.p50_t_low[0] == "" and table.p50_ci_high[0] == ""   # 10, 10, 10: no spread
    assert (int(table.n_reruns[0]), int(table.total_reruns[0])) == (3, 3)
    assert table.latency_unit[0] == "ns/op" and table.summary_kind[0] == "mean of reruns"
    assert table.contributing_reruns[0] == '["01", "02", "03"]'


def test_unit_table_columns_and_shape(tmp_path, monkeypatch, capsys):
    rows = group("1a", 1, [1, 2, 3]) + group("1a", 2, [1, 2, 3]) + group("base", 1, [1, 2, 3])
    _, folder, _, _, _ = run(tmp_path, monkeypatch, capsys, rows)
    table = read_csv(folder / f"latency_{MB1}_avg.csv")
    columns = table.columns.tolist()
    assert columns[:5] == ["benchmark_short", "strategy", "threads_blobshuffle", "n_reruns", "total_reruns"]
    assert columns[5:40] == [name for p in P for name in (p, f"{p}_t_low", f"{p}_t_high", f"{p}_ci_low", f"{p}_ci_high")]
    assert table[["strategy", "threads_blobshuffle"]].values.tolist() == [["base", "1"], ["1a", "1"], ["1a", "2"]]
    per_rerun = read_csv(folder / f"latency_{MB1}_r_all.csv")
    assert per_rerun.shape[0] == 9 and all(p in per_rerun for p in P)
    assert "threads_jmh" not in per_rerun and "p99_999" not in table and "p99_9999" not in table


def test_unit_seeds_are_per_percentile_and_row_order_free(config):
    rows = group("1a", 1, TEN)
    _, one = latency.prepare(pd.DataFrame(rows), config)
    _, two = latency.prepare(pd.DataFrame(rows[::-1]), config)
    expected = bootstrap_ci(np.array(TEN) * 130, seed=cell_seed(MB1, "1a", 1, "latency_p99"))
    assert (one.p99_ci_low[0], one.p99_ci_high[0]) == pytest.approx(expected)
    assert one.p99_ci_low[0] == two.p99_ci_low[0] and one.p99_ci_high[0] == two.p99_ci_high[0]
    assert one.p99_t_high[0] == two.p99_t_high[0]
    other = bootstrap_ci(np.array(TEN) * 130, seed=cell_seed(MB1, "1a", 1, "latency_p50"))
    assert other != expected    # the seed really matters here, so a wrong seed fails above


def test_spec_no_spread_blank_with_one_warning(tmp_path, monkeypatch, capsys):
    _, folder, messages, figures, _ = run(tmp_path, monkeypatch, capsys, group("1a", 1, [1, 1, 1]))
    table = read_csv(folder / f"latency_{MB1}_avg.csv")
    assert all(table[f"{p}_t_low"][0] == "" and table[f"{p}_ci_high"][0] == "" for p in P)
    found = [m for m in messages if m.startswith("No latency CI")]
    assert found == [f"No latency CI for {MB1}/1a/threads=1 at {', '.join(P)}: fewer than two reruns or no spread"]
    assert figures


def test_spec_missing_rerun(tmp_path, monkeypatch, capsys):
    rows = group("1a", 1, [1, 2, 3]) + group("1a", 2, [1, 2])
    _, folder, messages, _, _ = run(tmp_path, monkeypatch, capsys, rows)
    table = read_csv(folder / f"latency_{MB1}_avg.csv").set_index("threads_blobshuffle")
    assert (table.loc["2", "n_reruns"], table.loc["2", "total_reruns"]) == ("2", "3")
    assert f"{MB1}/1a_n02_r03/sample missing." in messages


def test_spec_tables_do_not_depend_on_the_method(tmp_path, monkeypatch, capsys):
    rows = group("1a", 1, [1, 2, 3])
    folders = [run(tmp_path, monkeypatch, capsys, rows, method=m)[1] for m in ("t", "resampled")]
    for name in (f"latency_{MB1}_avg.csv", f"latency_{MB1}_r_all.csv"):
        drop = lambda f: f.drop(columns=[c for c in f if "source_file" in c])
        pd.testing.assert_frame_equal(drop(read_csv(folders[0] / name)), drop(read_csv(folders[1] / name)))


# ------------------------------------------------------------------------ spectrum


def test_spec_files(tmp_path, monkeypatch, capsys):
    code, folder, _, _, _ = run(tmp_path, monkeypatch, capsys, group("1a", 1, [1, 2, 3]))
    assert code == 0 and folder.name.endswith("_latency")
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [f"latency_{MB1}_r_all.csv", f"latency_{MB1}_avg.csv", f"latency_{MB1}_spectrum.png", f"latency_{MB1}_percentiles.png"])


def test_spec_positions_and_values(tmp_path, monkeypatch, capsys):
    _, _, _, figures, _ = run(tmp_path, monkeypatch, capsys, group("1a", 1, [1, 2, 3]))
    ax = figures[f"latency_{MB1}_spectrum"].axes[0]
    line = ax.get_lines()[0]
    assert line.get_xdata() == pytest.approx((0.30103, 1, 1.30103, 2, 3, 4), abs=1e-4)
    assert line.get_ydata() == pytest.approx([2 * v for v in BASE_VALUES[:6]])     # mean of scales 1, 2, 3 is 2
    assert [t.get_text() for t in ax.get_xticklabels()] == ["50", "90", "95", "99", "99.9", "99.99"]
    assert ax.get_yscale() == "log" and ax.get_title() == "N = 1"


def test_spec_panels(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [1, 2, 3])
    for n in (1, 2, 4, 6, 8):
        rows += group("1a", n, [1, 2, 3])
    _, _, _, figures, _ = run(tmp_path, monkeypatch, capsys, rows)
    fig = figures[f"latency_{MB1}_spectrum"]
    axes = fig.axes
    assert [a.get_title() for a in axes] == ["N = 1", "N = 2", "N = 4", "N = 6", "N = 8"]
    assert [len(a.get_lines()) for a in axes] == [2, 1, 1, 1, 1]
    assert axes[0].get_shared_y_axes().joined(axes[0], axes[4])
    assert [t.get_text() for t in fig.legends[0].get_texts()] == ["Base", "S1_A"]


# ------------------------------------------------------------------------ percentile vs N


def test_spec_vs_n_values(tmp_path, monkeypatch, capsys):
    rows = group("1a", 1, [1, 2, 3]) + group("1a", 2, [2, 4, 6])
    _, _, _, figures, _ = run(tmp_path, monkeypatch, capsys, rows)
    axes = figures[f"latency_{MB1}_percentiles"].axes
    assert [a.get_title() for a in axes] == ["p50", "p95", "p99"]
    line = axes[1].get_lines()[0]
    assert list(line.get_xdata()) == [1, 2]
    assert line.get_ydata() == pytest.approx([240, 480])   # p95 base 120, means of scales are 2 and 4
    assert axes[0].get_lines()[0].get_ydata() == pytest.approx([200, 400])
    assert axes[2].get_lines()[0].get_ydata() == pytest.approx([260, 520])
    assert [t for t in axes[0].get_xticks()] == [1, 2]
    assert axes[0].get_yscale() == "log" and axes[0].get_shared_y_axes().joined(axes[0], axes[2])


def test_spec_base_is_marker_with_error_bar(tmp_path, monkeypatch, capsys):
    rows = [row("base", 1, f"{i + 1:02d}", (v,) * 7) for i, v in enumerate((100, 100, 400))]
    rows += group("1a", 1, [2, 2, 2.5]) + group("1a", 2, [2, 2, 2.5])
    # The lowest mean on the axis is Base's 200, so the bottom is 160 and a lower end of 100 is drawn there.
    for method, expected in (("t", (160, 630.2653)), ("resampled", (160, 400))):
        _, _, _, figures, _ = run(tmp_path, monkeypatch, capsys, rows, method=method)
        ax = figures[f"latency_{MB1}_percentiles"].axes[0]
        marker = next(c for c in ax.containers if len(c.lines[0].get_xdata()) == 1)
        assert list(marker.lines[0].get_xdata()) == [1] and marker.lines[0].get_ydata()[0] == 200
        assert to_rgba(marker.lines[0].get_color())[:3] == pytest.approx(to_rgba("#1f77b4")[:3])
        segment = marker.lines[2][0].get_segments()[0]
        assert float(segment[:, 1].max()) == pytest.approx(expected[1], abs=1e-3)
        assert float(segment[:, 1].min()) == pytest.approx(expected[0])
        assert len(bands(ax)) == 1       # 1a has a band; Base does not


# ------------------------------------------------------------------------ style


@pytest.mark.parametrize("stem", ["spectrum", "percentiles"])
def test_spec_line_alpha_one_band_alpha_low_in_strategy_colour(tmp_path, monkeypatch, capsys, stem):
    rows = group("1a", 1, [1, 2, 3]) + group("1a", 2, [1, 2, 3])
    _, _, _, figures, _ = run(tmp_path, monkeypatch, capsys, rows)
    ax = figures[f"latency_{MB1}_{stem}"].axes[0]
    line = ax.get_lines()[0]
    assert line.get_alpha() == 1
    band = bands(ax)[0]
    assert band.get_alpha() == 0.3
    assert tuple(band.get_facecolor()[0][:3]) == pytest.approx(to_rgba(line.get_color())[:3])


def test_spec_bands_follow_the_method(tmp_path, monkeypatch, capsys):
    rows = [row("1a", n, f"{i + 1:02d}", (v * n,) * 7) for n in (1, 2) for i, v in enumerate((500, 500, 800))]
    # n=1: mean 600, SD 173.2051: t half 430.2653; resampled [500, 800]. n=2 doubles all of these.
    for method, title, low, high in (("t", T_METHOD, 169.7347, 1030.2653), ("resampled", CI_METHOD, 500, 800)):
        _, _, _, figures, _ = run(tmp_path, monkeypatch, capsys, rows, method=method)
        fig = figures[f"latency_{MB1}_percentiles"]
        assert title in fig._suptitle.get_text()
        ends = band_ends(bands(fig.axes[0])[0])
        bottom = fig.axes[0].get_ylim()[0]
        assert ends[1.0] == pytest.approx((max(low, bottom), high), abs=1e-3)
        spectrum = figures[f"latency_{MB1}_spectrum"]
        assert title in spectrum._suptitle.get_text()
        assert band_ends(bands(spectrum.axes[0])[0])[4.0] == pytest.approx((max(low, spectrum.axes[0].get_ylim()[0]), high), abs=1e-3)


def test_spec_no_interval_has_no_band_and_no_method_in_title(tmp_path, monkeypatch, capsys):
    rows = group("1a", 1, [1, 1, 1]) + group("1a", 2, [1, 1, 1])
    _, _, _, figures, _ = run(tmp_path, monkeypatch, capsys, rows)
    for stem in ("spectrum", "percentiles"):
        fig = figures[f"latency_{MB1}_{stem}"]
        assert all(not bands(a) for a in fig.axes)
        assert "Bands" not in fig._suptitle.get_text() and "mean of reruns" in fig._suptitle.get_text()
        assert fig.axes[0].get_lines()


def test_unit_colours_follow_strategy_position(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [1, 2, 3]) + group("1a", 1, [1, 2, 3])
    _, _, _, figures, _ = run(tmp_path, monkeypatch, capsys, rows)
    lines = figures[f"latency_{MB1}_spectrum"].axes[0].get_lines()
    assert to_rgba(lines[0].get_color())[:3] == pytest.approx(to_rgba("#1f77b4")[:3])
    assert to_rgba(lines[1].get_color())[:3] == pytest.approx(to_rgba("#ff7f0e")[:3])


def test_unit_colours_follow_config(tmp_path):
    from src.config import Config
    config = Config(tmp_path, tmp_path, benchmarks=(MB1,), figsize=(6, 3), dpi=40, colors={"1a": "#123456", "base": "#654321"})
    rows = group("base", 1, [1, 2, 3]) + group("1a", 1, [1, 2, 3]) + group("1a", 2, [1, 2, 3])
    _, averages = latency.prepare(pd.DataFrame(rows), config)
    spectrum = latency.render_spectrum(averages, config, MB1, strategies=["base", "1a"], threads=[1, 2])
    lines = spectrum.axes[0].get_lines()
    assert lines[0].get_color() == "#654321" and lines[1].get_color() == "#123456"
    assert to_rgba("#123456")[:3] == pytest.approx(tuple(bands(spectrum.axes[1])[0].get_facecolor()[0][:3]))
    assert [to_rgba(h.get_color()) for h in spectrum.legends[0].get_lines()] == [to_rgba("#654321"), to_rgba("#123456")]
    both = latency.render_percentiles(averages, config, MB1, strategies=["base", "1a"], threads=[1, 2])
    marker = both.axes[0].containers[0]
    assert to_rgba(marker.lines[0].get_color()) == to_rgba("#654321")
    assert both.axes[0].get_lines()[-1].get_color() == "#123456"


def test_spec_lone_interval_draws_an_error_bar(tmp_path, monkeypatch, capsys):
    rows = [row("1a", n, f"{i + 1:02d}", (v,) * 7) for n, values in ((1, (5, 5, 5)), (2, (5, 5, 5)), (4, (2, 2, 5)), (6, (5, 5, 5)))
            for i, v in enumerate(values)]
    for method, high in (("t", 3 + 4.302653 * 3 ** 0.5 / 3 ** 0.5), ("resampled", 5)):
        _, _, _, figures, _ = run(tmp_path, monkeypatch, capsys, rows, method=method)
        fig = figures[f"latency_{MB1}_percentiles"]
        ax = fig.axes[0]
        assert not bands(ax)                       # no neighbour: no zero-width band
        bar = ax.containers[0]
        segment = bar.lines[2][0].get_segments()[0]
        assert float(segment[0, 0]) == 4 and float(segment[:, 1].max()) == pytest.approx(high, abs=1e-3)
        assert "Bands" in fig._suptitle.get_text()


def test_unit_two_neighbours_are_a_band_and_a_lone_one_a_bar():
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    nan = float("nan")
    drawn = latency._draw_interval(ax, [1, 2, 4, 6, 8], np.array([9.0, 9, 9, 9, 9]), np.array([8.0, 8, nan, 8, nan]),
                                   np.array([10.0, 10, nan, 10, nan]), "#123456")
    assert drawn
    assert sorted(band_ends(bands(ax)[0])) == [1.0, 2.0]
    assert [float(s[0, 0]) for s in ax.containers[0].lines[2][0].get_segments()] == [6.0]


def test_unit_nothing_known_draws_nothing():
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    nan = float("nan")
    assert not latency._draw_interval(ax, [1, 2], np.array([9.0, 9]), np.array([nan, nan]), np.array([nan, nan]), "k")
    assert not bands(ax) and not ax.containers


@pytest.mark.parametrize("stem", ["spectrum", "percentiles"])
def test_spec_y_label_and_plain_ticks(tmp_path, monkeypatch, capsys, stem):
    _, _, _, figures, _ = run(tmp_path, monkeypatch, capsys, group("1a", 1, [1, 2, 3]))
    ax = figures[f"latency_{MB1}_{stem}"].axes[0]
    assert ax.get_ylabel() == "Latency (ns per op, log scale)"
    formatter = ax.yaxis.get_major_formatter()
    assert isinstance(formatter, PlainLogFormatter) and formatter._num_to_string(1000.0, 1, 10000) == "1000"
    assert isinstance(ax.yaxis.get_minor_formatter(), PlainLogFormatter)


def test_spec_settings_must_agree_within_a_benchmark(config):
    rows = [row("1a", 1, "01", forks="5"), row("1a", 1, "02", forks="3")]
    with pytest.raises(ValueError, match="Incompatible settings in mb1_rawNoFinalize: forks"):
        latency.normalize_sample(pd.DataFrame(rows), config)


# ------------------------------------------------------------------------ log axis


def test_spec_limits(config):
    table = pd.DataFrame([{"p50": 100.0, "p50_t_low": 90.0, "p50_t_high": 1000.0, "p50_ci_low": 95.0, "p50_ci_high": 200.0}])
    assert latency.axis_limits(table, ("p50",), config) == pytest.approx((80, 1250))


def test_unit_limits_use_the_chosen_method(tmp_path):
    from src.config import Config
    config = Config(tmp_path, tmp_path, ci_method="resampled")
    table = pd.DataFrame([{"p50": 100.0, "p50_t_low": 90.0, "p50_t_high": 1000.0, "p50_ci_low": 95.0, "p50_ci_high": 200.0}])
    assert latency.axis_limits(table, ("p50",), config) == pytest.approx((80, 250))


def test_unit_limits_without_intervals(config):
    table = pd.DataFrame([{"p50": 100.0, "p50_t_low": np.nan, "p50_t_high": np.nan, "p50_ci_low": np.nan, "p50_ci_high": np.nan}])
    assert latency.axis_limits(table, ("p50",), config) == pytest.approx((80, 125))


def test_spec_lower_end_below_zero(tmp_path, monkeypatch, capsys):
    rows = [row("1a", n, f"{i + 1:02d}", (v,) * 7) for n in (1, 2) for i, v in enumerate((100, 100, 400))]
    _, folder, messages, figures, _ = run(tmp_path, monkeypatch, capsys, rows)
    ax = figures[f"latency_{MB1}_percentiles"].axes[0]
    assert ax.get_ylim() == pytest.approx((160, 630.2653 * 1.25), abs=1e-2)   # lowest mean 200 / 1.25
    assert band_ends(bands(ax)[0])[1.0] == pytest.approx((160, 630.2653), abs=1e-3)
    table = read_csv(folder / f"latency_{MB1}_avg.csv")
    assert float(table.p99_t_low[0]) == pytest.approx(-230.2653, abs=1e-3)
    tail = "drawn at the axis bottom, the table keeps the true value"
    assert messages.count(f"Latency band low end at or below zero for {MB1}/1a/threads=1 at p99: {tail}") == 1


def test_unit_resampled_has_no_low_end_warning(tmp_path, monkeypatch, capsys):
    rows = [row("1a", 1, f"{i + 1:02d}", (v,) * 7) for i, v in enumerate((100, 100, 400))]
    _, _, messages, _, _ = run(tmp_path, monkeypatch, capsys, rows, method="resampled")
    assert not [m for m in messages if m.startswith("Latency band")]


def test_spec_zero_mean_left_out_with_warning(tmp_path, monkeypatch, capsys):
    rows = [row("1a", 1, f"{i + 1:02d}", (0, 110, 120, 130, 140, 150, 160)) for i in range(3)]
    code, _, messages, figures, _ = run(tmp_path, monkeypatch, capsys, rows)
    assert code == 0
    assert f"Zero p50 mean left out of the log plot for {MB1}/1a/threads=1" in messages
    data = figures[f"latency_{MB1}_spectrum"].axes[0].get_lines()[0].get_ydata()
    assert np.isnan(data[0]) and data[1] == 110


def test_spec_p100_stays_out_of_the_plot(tmp_path, monkeypatch, capsys):
    rows = [row("1a", n, f"{i + 1:02d}", (100, 110, 120, 130, 140, 150, v)) for n in (1, 2)
            for i, v in enumerate((200, 200, 90000))]
    _, folder, messages, figures, _ = run(tmp_path, monkeypatch, capsys, rows)
    ax = figures[f"latency_{MB1}_spectrum"].axes[0]
    assert ax.get_ylim() == pytest.approx((80, 187.5))     # 100 / 1.25 and 150 * 1.25: no group's p100
    assert len(ax.get_lines()[0].get_ydata()) == 6 and not bands(ax)
    assert not [m for m in messages if "p100" in m and "band" in m]
    table = read_csv(folder / f"latency_{MB1}_avg.csv").iloc[0]
    # 200, 200, 90000: mean 30133.33, SD = 89800 / sqrt(3), half = 4.302653 * 89800 / 3 = 128792.74
    assert float(table.p100) == pytest.approx(30133.33, abs=0.01)
    assert float(table.p100_t_low) == pytest.approx(-98659.41, abs=0.01)
    assert (float(table.p100_ci_low), float(table.p100_ci_high)) == (200, 90000)


def test_unit_zero_p100_gives_no_plot_warning(config):
    averages = pd.DataFrame([{"strategy": "1a", "threads_blobshuffle": 1, **{p: 1.0 for p in P}, "p100": 0.0,
                              **{f"{p}_{k}": np.nan for p in P for k in ("t_low", "t_high", "ci_low", "ci_high")}}])
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        latency.warn_unplottable(averages, MB1, config)


def test_unit_all_zero_still_draws(tmp_path, monkeypatch, capsys):
    rows = [row("1a", 1, f"{i + 1:02d}", (0,) * 7) for i in range(3)]
    code, folder, _, _, _ = run(tmp_path, monkeypatch, capsys, rows)
    assert code == 0 and (folder / f"latency_{MB1}_spectrum.png").is_file()


# ------------------------------------------------------------------------ command, help, docs


def test_unit_command_is_registered():
    assert "latency" in cli.COMMANDS
    project = (ROOT / "pyproject.toml").read_text()
    assert 'latency = "src.cli:latency_main"' in project


def test_integration_help_lists_latency(capsys):
    cli.help_main([])
    assert "uv run latency" in capsys.readouterr().out


def test_unit_readme_describes_it():
    text = re.sub(r"\s+", " ", (ROOT / "README.md").read_text())
    for phrase in ("uv run latency", "latency_<benchmark>_spectrum", "latency_<benchmark>_percentiles", "alpha 0.3"):
        assert phrase in text


def test_unit_formats_follow_config(tmp_path, monkeypatch, capsys):
    _, folder, _, _, _ = run(tmp_path, monkeypatch, capsys, group("1a", 1, [1, 2, 3]), formats=("png", "pdf"))
    names = {p.name for p in folder.iterdir()}
    assert f"latency_{MB1}_spectrum.pdf" in names and f"latency_{MB1}_percentiles.png" in names


# ------------------------------------------------------------------------ feature, on the real format


@pytest.mark.skipif(not RAW3.exists(), reason="docs/raw3.csv is not in this clone")
def test_feature_raw3(tmp_path):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "input": {"path": str(RAW3)}, "output": {"directory": "out", "formats": ["png"]},
        "strategies": {"exclude": ["2null"]}, "plot": {"figsize": [6, 3], "dpi": 40}}))
    config = load_config(tmp_path / "config.yaml")
    with pytest.warns(UserWarning):
        paths = latency.run(config)
    assert len(paths) == 8
    table = pd.read_csv(next(p for p in paths if p.name == f"latency_{MB2}_avg.csv"), dtype={"strategy": str})
    # 8 strategies: base at N=1 and the 7 others at 5 thread counts.
    raw = pd.read_csv(RAW3, sep="\t", dtype=str, keep_default_na=False)
    assert table.shape[0] == 36 and set(table.n_reruns) == {raw["rerun"].nunique()} and "2null" not in set(table.strategy)
    source = raw[raw["mode"].eq("sample") & raw["benchmark_short"].eq(MB2) & raw["strategy"].eq("2a")
                 & raw["threads_blobshuffle"].eq("8")]
    mean = source["p99"].astype(float).mean() * 1e6
    cell = table[table.strategy.eq("2a") & table.threads_blobshuffle.eq(8)].p99.iloc[0]
    assert cell == pytest.approx(mean)
    assert (table.p50 <= table.p90).all() and (table.p99_99 <= table.p100).all()
