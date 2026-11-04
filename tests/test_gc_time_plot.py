"""Change add-gc-time-plot: `uv run gc` saves a gc_time_pct bar plot per benchmark, with a y axis top
rounded up to 1, 2 or 5 times a power of ten. Expected values are literal, derived by hand in the
comments (t quantile with 2 degrees of freedom is 4.302653, printed table value).

gc_time_pct = gc_time / (forks * iterations * seconds * 1000) * 100 = gc_time / 2500 with the test
defaults (5 forks, 5 iterations, 10 s), so gc_time = 2500 * pct."""
import re
import shutil
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import pytest
from matplotlib.collections import LineCollection
from matplotlib.colors import to_rgb

from audit_support import run_main, write_run
from conftest import gc_observation
from src import cli, gc_metrics

MB1 = "mb1_rawNoFinalize"
MB2 = "mb2_rawFinalize"
RAW3 = Path(__file__).parents[1] / "docs" / "raw3.csv"
STEM1 = f"gc_{MB1}_gc_time_pct"
STEM2 = f"gc_{MB2}_gc_time_pct"
HALF = 4.302653 / 3 ** 0.5          # t(2) * SD 1 / sqrt(3) = 2.48414


def row(strategy, threads, rerun, pct, benchmark=MB1, mode="thrpt"):
    return gc_observation(benchmark_short=benchmark, strategy=strategy, threads=threads, rerun=rerun,
                          mode=mode, gc_time=str(round(pct * 2500, 6)))


def group(strategy, threads, pcts, **kw):
    return [row(strategy, threads, f"{i + 1:02d}", pct, **kw) for i, pct in enumerate(pcts)]


def run_gc(tmp_path, monkeypatch, capsys, rows, benchmarks=(MB1,), formats=("png",)):
    """Run the gc command; return (exit code, file names, warnings, {file stem: figure}, folder)."""
    figures = {}
    real = gc_metrics.save_figure

    def spy(fig, stem, config):
        figures[Path(stem).name] = fig
        return real(fig, stem, config)

    monkeypatch.setattr(gc_metrics, "save_figure", spy)
    config = write_run(tmp_path, rows, benchmarks=benchmarks, formats=formats)
    code, printed, messages, _ = run_main(cli.gc_main, config, capsys)
    folders = list((tmp_path / "out").iterdir())
    names = sorted(p.name for f in folders for p in f.iterdir())
    return code, names, messages, figures, (folders[0] if folders else None)


def bars(fig):
    return [(round(p.get_x() + p.get_width() / 2, 6), p.get_height()) for p in fig.axes[0].patches]


def segments(fig):
    return [(round(float(s[0][0]), 6), float(min(s[:, 1])), float(max(s[:, 1])))
            for c in fig.axes[0].collections if isinstance(c, LineCollection) for s in c.get_segments()]


def time_messages(messages):
    return [m for m in messages if m.startswith("No gc_time_pct t interval")]


def time_files(names):
    return [n for n in names if "gc_time_pct." in n]


def message(strategy, threads, benchmark=MB1):
    return (f"No gc_time_pct t interval for {benchmark}/{strategy}/threads={threads}: "
            "fewer than two reruns or no spread")


# ------------------------------------------------------------------ unit: rounded_top

@pytest.mark.parametrize("highest, top", [
    (0, 1.0), (0.0, 1.0),
    (6, 10.0),                                  # owner's example: 6.6 -> 10
    (1, 2.0), (0.5, 1.0), (10, 20.0), (100, 200.0), (1000, 2000.0),
    (0.03, 0.05), (0.1, 0.2), (0.001, 0.002), (0.0004, 0.0005), (0.00001, 0.000020),
    (4.48414, 5.0),                             # 4.93255 -> 5
    (4.6, 10.0),                                # 5.06 -> 10
    (45, 50.0), (46, 100.0), (4500, 5000.0), (4600, 10000.0), (0.045, 0.05), (0.046, 0.1),
    (1.8181818181818181, 2.0),                  # just below 20/11: 1.1 times it is just below 2
    (1.8181818181818183, 5.0),                  # just above 20/11: 1.1 times it is just above 2
    (4.545454545454545, 5.0),                   # just below 50/11
    (4.545454545454546, 10.0),                  # just above 50/11
    (0.9090909090909091, 2.0), (0.9090909090909090, 1.0),   # around 10/11
    (9.09090909090909, 10.0), (9.090909090909092, 20.0),    # around 100/11
    (0.45454545454545453, 0.5),
    (1e-7, 2e-7), (3e7, 5e7),
])
def test_rounded_top_values(highest, top):
    assert gc_metrics.rounded_top(highest) == top          # exact, no 2.0000000000000004


@pytest.mark.parametrize("highest", [0.03, 1.8181818181818183, 0.0004, 6, 123.456, 0.7, 3e-9, 9.5e12])
def test_rounded_top_is_a_clean_1_2_5_value(highest):
    top = gc_metrics.rounded_top(highest)
    assert f"{top:.0e}"[0] in "125"
    assert float(f"{top:.0e}") == top                      # one significant digit, nothing like ...04
    assert top >= 1.1 * highest - 1e-12 * highest
    assert top <= 2.5 * 1.1 * highest * (1 + 1e-9)        # the smallest such value, not a larger one


def test_rounded_top_returns_float_one_for_zero():
    assert gc_metrics.rounded_top(0) == 1 and isinstance(gc_metrics.rounded_top(0), float)


# ------------------------------------------------------------------ Requirement: GC time bar plot

def test_scenario_mean_and_error_bar(tmp_path, monkeypatch, capsys):
    _, _, _, figures, folder = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [1, 2, 3]))
    fig = figures[STEM1]
    ax = fig.axes[0]
    assert bars(fig) == [(0, pytest.approx(2.0))]
    (x, low, high), = segments(fig)
    assert x == 0 and (low, high) == pytest.approx((2 - HALF, 2 + HALF), abs=1e-5)   # -0.48414, 4.48414
    assert (low, high) == pytest.approx((-0.48, 4.48), abs=5e-3)
    assert ax.get_ylim() == (0, 5.0)
    avg = pd.read_csv(folder / f"gc_{MB1}_avg.csv")
    assert (avg.gc_time_pct[0], avg.gc_time_pct_t_low[0], avg.gc_time_pct_t_high[0]) == pytest.approx((2, low, high), abs=1e-9)


def test_scenario_no_spread(tmp_path, monkeypatch, capsys):
    _, _, messages, figures, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [2, 2, 2]))
    assert bars(figures[STEM1]) == [(0, pytest.approx(2.0))]
    assert segments(figures[STEM1]) == []
    assert figures[STEM1].axes[0].get_title() == f"{MB1} — gc_time_pct, mean of reruns"
    assert time_messages(messages) == [message("base", 1)]


def test_scenario_other_outputs_unchanged(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [1, 2, 3]) + group("base", 1, [0.9, 0.95, 0.97], benchmark=MB2)
    kw = dict(benchmarks=(MB1, MB2), formats=("png", "svg", "pdf"))
    _, with_names, with_messages, _, with_folder = run_gc(tmp_path, monkeypatch, capsys, rows, **kw)
    assert time_files(with_names) == [f"{stem}.{e}" for stem in (STEM1, STEM2) for e in ("pdf", "png", "svg")]
    shutil.move(str(with_folder), str(tmp_path / "with_plot"))
    real = gc_metrics.save_figure          # the same run without the plot: not drawn and not written
    monkeypatch.setattr(gc_metrics, "render_gc_time", lambda *args, **kwargs: plt.figure())
    monkeypatch.setattr(gc_metrics, "save_figure",
                        lambda fig, stem, c: [] if Path(stem).name.endswith("_gc_time_pct") else real(fig, stem, c))
    _, without_names, without_messages, _, without_folder = run_gc(tmp_path, monkeypatch, capsys, rows, **kw)
    assert time_files(without_names) == []
    plot_files = sorted(f"{stem}.{e}" for stem in (STEM1, STEM2) for e in ("pdf", "png", "svg"))
    assert with_names == sorted(without_names + plot_files)
    assert [m for m in with_messages if m not in time_messages(with_messages)] == without_messages
    for name in without_names:
        if name.endswith((".csv", ".png")):
            assert (tmp_path / "with_plot" / name).read_bytes() == (without_folder / name).read_bytes(), name
    # 10 CSVs and the other plots (pauses_max, alloc, batch_fill for mb2 only) in 3 formats: mb1 10 + 6, mb2 10 + 9
    assert len(without_names) == (10 + 3 * 2) + (10 + 3 * 3)


# ------------------------------------------------------------------ Requirement: axis top

def test_scenario_highest_value_has_no_error_bar(tmp_path, monkeypatch, capsys):
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [6, 6, 6]))
    ax = figures[STEM1].axes[0]
    assert bars(figures[STEM1]) == [(0, pytest.approx(6.0))] and segments(figures[STEM1]) == []
    assert ax.get_ylim() == (0, 10.0)                    # 6.6 -> 10


def test_scenario_error_bar_sets_the_top(tmp_path, monkeypatch, capsys):
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [1, 2, 3]))
    ax = figures[STEM1].axes[0]
    (x, low, high), = segments(figures[STEM1])
    assert bars(figures[STEM1]) == [(0, pytest.approx(2.0))]
    assert high == pytest.approx(4.48414, abs=1e-5)
    assert ax.get_ylim() == (0, 5.0)                     # 1.1 * 4.48414 = 4.93255 -> 5, not 5 from the bar (2.2 -> 5 too)
    assert ax.get_ylim()[1] > high                       # the whole error bar is visible


def test_error_bar_not_the_bar_decides_the_top(tmp_path, monkeypatch, capsys):
    # bar 3 would give 3.3 -> 5; mean 3 with values 2, 3, 4: error bar up to 3 + 2.48414 = 5.48414 -> 6.03 -> 10
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [2, 3, 4]))
    assert figures[STEM1].axes[0].get_ylim() == (0, 10.0)


def test_scenario_small_values(tmp_path, monkeypatch, capsys):
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [0.03, 0.03, 0.03]))
    assert bars(figures[STEM1]) == [(0, pytest.approx(0.03))]
    assert figures[STEM1].axes[0].get_ylim() == (0, 0.05)       # 0.033 -> 0.05


def test_scenario_all_values_zero(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [0, 0, 0]) + group("1a", 1, [0, 0, 0])
    code, names, messages, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    assert code == 0 and time_files(names) == [f"{STEM1}.png"]
    assert bars(figures[STEM1]) == [(-0.2, 0.0), (0.2, 0.0)]
    assert figures[STEM1].axes[0].get_ylim() == (0, 1.0)
    assert time_messages(messages) == [message("base", 1), message("1a", 1)]


def test_highest_across_groups_decides(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [0.1, 0.1, 0.1]) + group("1a", 1, [4.6, 4.6, 4.6]) + group("base", 2, [1, 1, 1])
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    assert figures[STEM1].axes[0].get_ylim() == (0, 10.0)        # 4.6 * 1.1 = 5.06 -> 10


def test_other_plots_keep_their_axis(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [1, 2, 3])
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    alloc = figures[f"gc_{MB1}_gc_alloc_rate_norm"].axes[0]
    top = alloc.get_ylim()[1]
    assert top == pytest.approx(1000 * 1.05) and alloc.get_ylim()[0] == 0     # all reruns 1000, plain margin, no rounding


# ------------------------------------------------------------------ shape and labels

def test_shape_labels_ticks_legend_title(tmp_path, monkeypatch, capsys):
    rows = (group("base", 1, [1, 2, 3]) + group("1a", 1, [5, 5, 5])
            + group("base", 2, [1, 1, 1]))
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    fig = figures[STEM1]
    ax = fig.axes[0]
    fig.canvas.draw()
    assert len(fig.axes) == 1
    assert len(bars(fig)) == 3
    # threads 1: two strategies, width 0.4 -> -0.2 and 0.2; threads 2: base alone, centred at 1
    assert bars(fig) == [(-0.2, pytest.approx(2.0)), (1, pytest.approx(1.0)), (0.2, pytest.approx(5.0))]
    assert [s[0] for s in segments(fig)] == [-0.2]
    assert ax.get_xlabel() == "BlobShuffle threads"
    assert ax.get_ylabel() == "gc_time_pct (%)"
    assert ax.get_title() == f"{MB1} — gc_time_pct, mean of reruns\nError bars: 95% t interval of reruns"
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1", "2"]
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["Base", "S1_A"]
    assert ax.get_yscale() == "linear" and ax.get_ylim() == (0, 10.0)      # highest 5 (1a bar), 5.5 -> 10
    assert [t.get_text() for t in ax.get_yticklabels() if t.get_text()][:2] == ["0", "2"]
    assert len(ax.texts) == 0 and len(fig.texts) == 0
    assert "average" not in ax.get_title().lower()


def test_title_without_error_bars_has_no_second_line(tmp_path, monkeypatch, capsys):
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [3, 3, 3]))
    assert figures[STEM1].axes[0].get_title() == f"{MB1} — gc_time_pct, mean of reruns"


def test_colours_and_order_follow_configuration(tmp_path, monkeypatch, capsys):
    rows = group("1a", 1, [2, 2, 2]) + group("base", 1, [1, 1, 1])
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    ax = figures[STEM1].axes[0]
    assert bars(figures[STEM1]) == [(-0.2, pytest.approx(1.0)), (0.2, pytest.approx(2.0))]
    assert [tuple(round(c, 6) for c in p.get_facecolor()[:3]) for p in ax.patches] == [
        tuple(round(c, 6) for c in to_rgb(k)) for k in ("C0", "C1")]


def test_strategy_without_a_thread_count_has_no_empty_slot(tmp_path, monkeypatch, capsys):
    rows = (group("base", 1, [1, 1, 1]) + group("base", 2, [2, 2, 2]) + group("1a", 2, [3, 3, 3]))
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    assert bars(figures[STEM1]) == [(0, pytest.approx(1.0)), (0.8, pytest.approx(2.0)),
                                    (1.2, pytest.approx(3.0))]


def test_lower_bound_below_zero_keeps_axis_at_zero(tmp_path, monkeypatch, capsys):
    # 0.01, 0.02, 0.6 -> gc_time_pct with the same shape: low is below zero, the axis still starts at 0
    _, _, _, figures, folder = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [0.01, 0.02, 0.6]))
    (x, low, high), = segments(figures[STEM1])
    assert low == pytest.approx(-0.62911, abs=1e-4) and high == pytest.approx(1.04911, abs=1e-4)
    assert figures[STEM1].axes[0].get_ylim() == (0, 2.0)           # 1.04911 * 1.1 = 1.154 -> 2


def test_gc_time_pct_comes_from_thrpt_rows_only(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [1, 2, 3]) + group("base", 1, [50, 60, 70], mode="sample")
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    assert bars(figures[STEM1]) == [(0, pytest.approx(2.0))]


# ------------------------------------------------------------------ files, benchmarks, formats

def test_one_plot_per_requested_benchmark_in_every_format(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [1, 2, 3]) + group("base", 1, [4, 4, 4], benchmark=MB2)
    code, names, _, figures, folder = run_gc(tmp_path, monkeypatch, capsys, rows, benchmarks=(MB1, MB2),
                                             formats=("png", "svg"))
    assert code == 0
    assert time_files(names) == [f"{STEM1}.png", f"{STEM1}.svg", f"{STEM2}.png", f"{STEM2}.svg"]
    assert bars(figures[STEM2]) == [(0, pytest.approx(4.0))]
    assert figures[STEM2].axes[0].get_ylim() == (0, 5.0)           # 4.4 -> 5
    assert figures[STEM2].axes[0].get_title() == f"{MB2} — gc_time_pct, mean of reruns"
    assert folder.name.endswith("_gc_pauses")


def test_benchmark_not_requested_gets_no_plot(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [1, 2, 3]) + group("base", 1, [4, 4, 4], benchmark=MB2)
    _, names, _, _, _ = run_gc(tmp_path, monkeypatch, capsys, rows, benchmarks=(MB2,))
    assert time_files(names) == [f"{STEM2}.png"]


def test_no_gc_time_pct_mean_gives_no_plot_and_no_t_warning(tmp_path, monkeypatch, capsys):
    rows = [gc_observation(strategy="base", threads=1, rerun=f"0{i}", gc_time="") for i in (1, 2, 3)]
    code, names, messages, _, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    assert code == 0 and time_files(names) == []
    assert time_messages(messages) == []
    assert f"gc_{MB1}_avg.csv" in names


def test_group_without_a_mean_has_no_bar_and_no_t_warning(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [1, 2, 3]) + [gc_observation(strategy="1a", threads=1, rerun=f"0{i}", gc_time="")
                                          for i in (1, 2, 3)]
    _, _, messages, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    assert bars(figures[STEM1]) == [(0, pytest.approx(2.0))]
    assert time_messages(messages) == []


# ------------------------------------------------------------------ warnings

def test_one_warning_per_group_even_with_several_formats(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [2, 2, 2]) + group("base", 2, [3, 3, 3]) + group("1a", 1, [1, 2, 3])
    _, _, messages, _, _ = run_gc(tmp_path, monkeypatch, capsys, rows, formats=("png", "svg", "pdf"))
    assert time_messages(messages) == [message("base", 1), message("base", 2)]


def test_one_rerun_group_warns(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [1, 2, 3]) + group("1a", 1, [4])
    _, _, messages, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    assert len(bars(figures[STEM1])) == 2 and len(segments(figures[STEM1])) == 1
    assert time_messages(messages) == [message("1a", 1)]


def test_other_warnings_do_not_change_or_double(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [2, 2, 2]) + [row("1a", 1, "01", 1), row("1a", 1, "02", 2)] \
        + group("1a", 2, [1, 2, 3])
    rows[3]["gc_time"] = ""                                        # one blank gc_time
    _, _, messages, _, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    assert sum("Blank gc_time" in m for m in messages) == 1
    assert sum("Mean of reruns of gc_time_pct missing" in m for m in messages) == 1
    assert time_messages(messages) == [message("base", 1)]


def test_table_step_warns_nothing_about_gc_time_t(tmp_path):
    import warnings
    from src.config import Config
    config = Config(tmp_path / "l.csv", tmp_path / "o", benchmarks=(MB1,), figsize=(5, 3), dpi=40)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _, averages = gc_metrics.prepare(pd.DataFrame(group("base", 1, [2, 2, 2])), config)
    assert [str(w.message) for w in caught if "t interval" in str(w.message)] == []
    assert len(averages) == 1
    assert averages[["gc_time_pct_t_low", "gc_time_pct_t_high"]].isna().all(axis=None)


# ------------------------------------------------------------------ help, README, constants

def test_help_names_the_gc_time_plot():
    assert "gc_alloc_rate_norm and gc_time_pct as mean of reruns" in cli.COMMANDS["gc"]


def test_readme_documents_the_gc_time_plot():
    text = re.sub(r"\s+", " ", (Path(__file__).parents[1] / "README.md").read_text())
    assert "gc_mb1_rawNoFinalize_gc_time_pct.png" in text
    section = text.split("### gc_time_pct bar plot")[1].split(" ### ")[0]
    for part in ("`gc_<benchmark>_gc_time_pct.<fmt>`", "mean of reruns", "linear y-axis from zero",
                 "`gc_time_pct (%)`", "95% t interval", "`gc_time_pct_t_low`", "`gc_time_pct_t_high`",
                 "1.1 times the highest drawn value", "next 1, 2 or 5 times a power of ten",
                 "highest 6 gives 10", "highest value is 0, the top is 1", "every other plot keeps its axis",
                 "no error bar and one warning", "`gc_count` is not plotted", "no notes"):
        assert part in section, part
    assert "so does the `gc_time_pct` plot" in text
    assert "average" not in section.lower()


def test_strategy_docs_say_the_plot_exists():
    root = Path(__file__).parents[1] / "strategies"
    table = (root / "plots_and_tables.md").read_text()
    assert re.search(r"\| `gc_time_pct` \| `thrpt` \| ✓ \| ✓ \| Yes: `add-gc-time-plot`", table)
    assert re.search(r"\| `gc_count` \| `thrpt` \| ✓ \| X \|", table)
    todo = (root / "todo.txt").read_text()
    assert "[x] gc_time plot" in todo and "gc_count remains table only" in todo


def test_constants():
    assert gc_metrics.TIME_PCT == "gc_time_pct"


# ------------------------------------------------------------------ raw3

@pytest.mark.skipif(not RAW3.is_file(), reason="docs/raw3.csv is absent")
def test_raw3_gives_one_gc_time_plot_per_benchmark(tmp_path, monkeypatch, capsys):
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"input": {"path": str(RAW3)}, "output": {"directory": "out"},
                                      "benchmarks": [MB1, MB2], "plot": {"figsize": [5, 3], "dpi": 40}}))
    figures = {}
    real = gc_metrics.save_figure
    monkeypatch.setattr(gc_metrics, "save_figure",
                        lambda fig, stem, c: figures.setdefault(Path(stem).name, fig) and real(fig, stem, c))
    code, printed, messages, _ = run_main(cli.gc_main, config, capsys)
    assert code == 0
    assert [Path(p).name for p in printed if "gc_time_pct." in p] == [f"{STEM1}.png", f"{STEM2}.png"]
    for benchmark, stem in ((MB1, STEM1), (MB2, STEM2)):
        fig = figures[stem]
        ax = fig.axes[0]
        avg = pd.read_csv(next(Path(p) for p in printed if p.endswith(f"gc_{benchmark}_avg.csv")))
        assert sorted(h for _, h in bars(fig)) == pytest.approx(sorted(avg["gc_time_pct"]))
        drawn = sorted(s[1:] for s in segments(fig))
        wanted = sorted(zip(avg["gc_time_pct_t_low"].dropna(), avg["gc_time_pct_t_high"].dropna()))
        assert [v for pair in drawn for v in pair] == pytest.approx([v for pair in wanted for v in pair])
        highest = max(list(avg["gc_time_pct"]) + list(avg["gc_time_pct_t_high"].dropna()))
        assert ax.get_ylim() == (0, gc_metrics.rounded_top(highest))
        assert ax.get_ylim()[1] >= 1.1 * highest
        assert len(ax.texts) == 0 and len(fig.texts) == 0
