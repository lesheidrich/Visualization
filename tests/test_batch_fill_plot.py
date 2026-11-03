"""Change add-batch-fill-plot: `uv run gc` saves a batch_fill bar plot for mb2_rawFinalize `thrpt` rows
only. Expected values are literal, derived by hand in the comments (t quantile with 2 degrees of
freedom is 4.302653, printed table value)."""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from matplotlib.collections import LineCollection

from audit_support import read_csv, run_main, write_run
from conftest import gc_observation
from src import cli, gc_metrics

MB1 = "mb1_rawNoFinalize"
MB2 = "mb2_rawFinalize"
RAW3 = Path(__file__).parents[1] / "docs" / "raw3.csv"
DESIRED = 16777216
YLABEL = "batch_fill (batch_bytes / cfg_batchDesiredBytes)"
STEM = "gc_mb2_rawFinalize_batch_fill"


def row(strategy, threads, rerun, fill, benchmark=MB2, mode="thrpt", batches="1"):
    """One ledger row whose batch_fill is `fill`: 1 batch of fill * cfg_batchDesiredBytes bytes."""
    record_bytes = str(float(fill) * DESIRED) if batches != "0" else "0"
    return gc_observation(benchmark_short=benchmark, strategy=strategy, threads=threads, rerun=rerun,
                          mode=mode, aux_recordBytes=record_bytes, aux_batches=batches)


def group(strategy, threads, fills, **kw):
    return [row(strategy, threads, f"{i + 1:02d}", fill, **kw) for i, fill in enumerate(fills)]


def run_gc(tmp_path, monkeypatch, capsys, rows, benchmarks=(MB2,), formats=("png",)):
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
    ax = fig.axes[0]
    return [(round(p.get_x() + p.get_width() / 2, 6), p.get_height()) for p in ax.patches]


def segments(fig):
    ax = fig.axes[0]
    return [(round(float(s[0][0]), 6), float(min(s[:, 1])), float(max(s[:, 1])))
            for c in ax.collections if isinstance(c, LineCollection) for s in c.get_segments()]


def fill_messages(messages):
    return [m for m in messages if m.startswith("No batch_fill t interval")]


def batch_files(names):
    return [n for n in names if "batch_fill." in n]


# ------------------------------------------------------------------ Requirement: Batch fill bar plot

def test_scenario_thrpt_rows_only(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [0.96, 0.97, 0.98]) + group("base", 1, [0.1, 0.2, 0.3], mode="sample")
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    assert bars(figures[STEM]) == [(0, pytest.approx(0.97))]      # (0.96+0.97+0.98)/3, not the sample 0.2


def test_scenario_mean_of_reruns(tmp_path, monkeypatch, capsys):
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [0.96, 0.97, 0.98]))
    ax = figures[STEM].axes[0]
    assert bars(figures[STEM]) == [(0, pytest.approx(0.97))]
    assert ax.get_yscale() == "linear"
    low, high = ax.get_ylim()
    assert low == 0 and high >= 1                                   # includes 0 and 1


def test_scenario_low_fill(tmp_path, monkeypatch, capsys):
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [0.5, 0.6, 0.7]))
    ax = figures[STEM].axes[0]
    assert ax.get_ylim() == (0, 1)
    assert bars(figures[STEM]) == [(0, pytest.approx(0.6))]
    (x, low, high), = segments(figures[STEM])
    # SD 0.1, half width 4.302653 * 0.1 / sqrt(3) = 0.24841
    assert (low, high) == pytest.approx((0.35159, 0.84841), abs=1e-4)


def test_scenario_fill_above_one(tmp_path, monkeypatch, capsys):
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [1.1, 1.2, 1.3]))
    ax = figures[STEM].axes[0]
    assert bars(figures[STEM]) == [(0, pytest.approx(1.2))]
    assert ax.get_ylim()[1] >= 1.2 + 0.24841 - 1e-4                  # bar and error bar fully visible
    (x, low, high), = segments(figures[STEM])
    assert high == pytest.approx(1.44841, abs=1e-4)


def test_scenario_fill_above_one_without_error_bar(tmp_path, monkeypatch, capsys):
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [1.2, 1.2, 1.2]))
    assert bars(figures[STEM]) == [(0, pytest.approx(1.2))]
    assert figures[STEM].axes[0].get_ylim()[1] > 1.2


def test_scenario_strategy_without_a_thread_count(tmp_path, monkeypatch, capsys):
    rows = (group("base", 1, [0.9, 0.9, 0.9]) + group("base", 2, [0.8, 0.8, 0.8])
            + group("1a", 2, [0.7, 0.7, 0.7]))
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    ax = figures[STEM].axes[0]
    # Two strategies, width 0.8 / 2 = 0.4. Thread 1: base alone, centred at 0.
    # Thread 2: base at 1 - 0.2 and 1a at 1 + 0.2.
    assert bars(figures[STEM]) == [(0, pytest.approx(0.9)), (0.8, pytest.approx(0.8)),
                                   (1.2, pytest.approx(0.7))]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1", "2"]


# ------------------------------------------------------------------ Requirement: labels

def test_scenario_labels(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [0.96, 0.97, 0.98]) + group("1a", 1, [0.5, 0.6, 0.7])
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    fig = figures[STEM]
    ax = fig.axes[0]
    assert len(fig.axes) == 1
    assert ax.get_xlabel() == "BlobShuffle threads"
    assert ax.get_ylabel() == YLABEL
    assert ax.get_title() == f"{MB2} — batch_fill, mean of reruns\nError bars: 95% t interval of reruns"
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1"]
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["Base", "S1_A"]
    assert len(ax.texts) == 0 and len(fig.texts) == 0
    assert [t.get_text() for t in ax.get_yticklabels() if t.get_text()] != []
    assert "average" not in ax.get_title().lower()


def test_title_has_no_error_bar_line_without_error_bars(tmp_path, monkeypatch, capsys):
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [0.9, 0.9, 0.9]))
    assert figures[STEM].axes[0].get_title() == f"{MB2} — batch_fill, mean of reruns"


# ------------------------------------------------------------------ Requirement: error bars

def test_scenario_error_bar_from_t_columns(tmp_path, monkeypatch, capsys):
    _, names, _, figures, folder = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [0.96, 0.97, 0.98]))
    (x, low, high), = segments(figures[STEM])
    # SD 0.01, half width 4.302653 * 0.01 / sqrt(3) = 0.024841
    assert x == 0 and (low, high) == pytest.approx((0.94516, 0.99484), abs=1e-5)
    avg = pd.read_csv(folder / f"gc_{MB2}_avg.csv")
    assert (avg.batch_fill_t_low[0], avg.batch_fill_t_high[0]) == pytest.approx((low, high), abs=1e-9)


def test_error_bar_is_not_the_bootstrap(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [0.5, 0.6, 0.7])
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    (x, low, high), = segments(figures[STEM])
    assert low < 0.5 and high > 0.7             # a resampled interval of three values stays within 0.5 to 0.7


def test_scenario_upper_bound_above_one(tmp_path, monkeypatch, capsys):
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [0.9, 1.0, 1.1]))
    (x, low, high), = segments(figures[STEM])
    assert high == pytest.approx(1.0 + 4.302653 * 0.1 / 3 ** 0.5, abs=1e-5)      # 1.24841, not clamped to 1
    assert figures[STEM].axes[0].get_ylim()[1] >= high


def test_scenario_lower_bound_below_zero(tmp_path, monkeypatch, capsys):
    _, _, _, figures, folder = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [0.01, 0.02, 0.6]))
    # mean 0.21, squares 0.04 + 0.0361 + 0.1521 = 0.2282, SD sqrt(0.1141) = 0.337787,
    # half width 4.302653 * 0.337787 / sqrt(3) = 0.83911, low = -0.62911
    avg = pd.read_csv(folder / f"gc_{MB2}_avg.csv")
    assert avg.batch_fill_t_low[0] == pytest.approx(-0.62911, abs=1e-4)
    (x, low, high), = segments(figures[STEM])
    assert low == pytest.approx(-0.62911, abs=1e-4)
    assert figures[STEM].axes[0].get_ylim()[0] == 0


# ------------------------------------------------------------------ Requirement: warnings

def message(strategy, threads):
    return (f"No batch_fill t interval for {MB2}/{strategy}/threads={threads}: "
            "fewer than two reruns or no spread")


def test_scenario_no_spread(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [0.9, 0.9, 0.9]) + group("1a", 1, [0.5, 0.6, 0.7])
    _, _, messages, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    assert bars(figures[STEM]) == [(-0.2, pytest.approx(0.9)), (0.2, pytest.approx(0.6))]
    assert len(segments(figures[STEM])) == 1 and segments(figures[STEM])[0][0] == 0.2
    assert fill_messages(messages) == [message("base", 1)]


def test_scenario_one_rerun(tmp_path, monkeypatch, capsys):
    rows = (group("base", 1, [0.9, 0.95, 0.97]) + group("1a", 1, [0.8, 0.8, 0.8][:1])
            + group("1a", 2, [0.5, 0.6, 0.7]) + group("base", 2, [0.5, 0.6, 0.7]))
    _, _, messages, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    assert len(bars(figures[STEM])) == 4
    assert len(segments(figures[STEM])) == 3
    assert fill_messages(messages) == [message("1a", 1)]
    assert f"{MB2}/1a_n01_r02/thrpt missing." in messages
    assert f"{MB2}/1a_n01_r03/thrpt missing." in messages


def test_scenario_one_rerun_with_zero_batches(tmp_path, monkeypatch, capsys):
    rows = (group("base", 1, [0.9, 0.95, 0.97]) + group("1a", 1, [0.5, 0.6])
            + [row("1a", 1, "03", 0.7, batches="0")])
    _, _, messages, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    assert bars(figures[STEM]) == [(0, pytest.approx(np.mean([0.9, 0.95, 0.97])))]   # 1a has no bar
    assert len(segments(figures[STEM])) == 1
    assert fill_messages(messages) == []
    assert any("Zero aux_batches" in m for m in messages)
    assert any("Mean of reruns of batch_fill missing for mb2_rawFinalize/1a/threads=1" in m for m in messages)


# ------------------------------------------------------------------ Requirement: only mb2

def test_scenario_mb1(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [0.9, 0.95, 0.97], benchmark=MB1) + group("base", 1, [0.9, 0.95, 0.97])
    code, names, messages, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows, benchmarks=(MB1, MB2))
    assert code == 0
    assert batch_files(names) == [f"{STEM}.png"]
    assert f"gc_{MB1}_avg.csv" in names and f"gc_{MB1}_gc_alloc_rate_norm.png" in names
    assert f"gc_{MB1}_pauses_max.png" in names
    assert [m for m in messages if "batch_fill" in m] == []


@pytest.mark.parametrize("fills", [[0.9, 0.95, 0.97], [0.9, 0.9, 0.9]])
def test_scenario_mb1_with_values(tmp_path, monkeypatch, capsys, fills):
    code, names, messages, _, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, fills, benchmark=MB1),
                                         benchmarks=(MB1,))
    assert code == 0 and batch_files(names) == []
    assert not [m for m in messages if "No batch_fill t interval" in m]
    assert not [m for m in messages if MB1 in m and "batch_fill" in m]


def test_scenario_mb2_not_requested(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [0.9, 0.95, 0.97], benchmark=MB1) + group("base", 1, [0.9, 0.95, 0.97])
    code, names, messages, _, _ = run_gc(tmp_path, monkeypatch, capsys, rows, benchmarks=(MB1,))
    assert code == 0 and batch_files(names) == [] and fill_messages(messages) == []


def test_scenario_mb2_without_values(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [0.9, 0.9, 0.9], batches="0") + group("1a", 1, [0.9, 0.9, 0.9], batches="0")
    code, names, messages, _, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    assert code == 0 and batch_files(names) == []
    assert fill_messages(messages) == []
    assert [m for m in messages if "aux_batches" in m] == [
        f"{MB2}: aux_batches is 0 on every row; batch_bytes and batch_fill are blank"]
    assert f"gc_{MB2}_avg.csv" in names and f"gc_{MB2}_pauses_max.png" in names   # other outputs still written


def test_scenario_other_outputs_unchanged(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [0.96, 0.97, 0.98]) + group("base", 1, [0.9, 0.95, 0.97], benchmark=MB1)
    kw = dict(benchmarks=(MB1, MB2), formats=("png", "svg", "pdf"))
    _, with_names, with_messages, _, with_folder = run_gc(tmp_path, monkeypatch, capsys, rows, **kw)
    assert batch_files(with_names) == [f"{STEM}.pdf", f"{STEM}.png", f"{STEM}.svg"]
    import shutil
    shutil.move(str(with_folder), str(tmp_path / "with_plot"))
    monkeypatch.setattr(gc_metrics, "BATCH_FILL_BENCHMARK", "none")        # the same run without the plot
    _, without_names, without_messages, _, without_folder = run_gc(tmp_path, monkeypatch, capsys, rows, **kw)
    assert batch_files(without_names) == []
    assert with_names == sorted(without_names + [f"{STEM}.{e}" for e in ("pdf", "png", "svg")])
    assert with_messages == without_messages
    for name in without_names:
        if name.endswith((".csv", ".png")):
            assert (tmp_path / "with_plot" / name).read_bytes() == (without_folder / name).read_bytes(), name
    assert len(without_names) == 2 * (10 + 3 * 2)       # 10 CSVs, pauses_max and alloc in 3 formats, twice


def test_help_names_the_batch_fill_plot():
    assert "batch_fill as mean of reruns for mb2_rawFinalize" in cli.COMMANDS["gc"]


# ------------------------------------------------------------------ extra checks

def test_constant_names_mb2():
    assert gc_metrics.BATCH_FILL_BENCHMARK == MB2


def test_render_alloc_has_no_top_rule(tmp_path, monkeypatch, capsys):
    rows = [gc_observation(benchmark_short=MB1, strategy="base", threads=1, rerun=f"0{i}",
                           gc_alloc_rate_norm=str(v)) for i, v in ((1, 0.4), (2, 0.5), (3, 0.6))]
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows, benchmarks=(MB1,))
    ax = figures[f"gc_{MB1}_gc_alloc_rate_norm"].axes[0]
    assert ax.get_ylim()[1] < 1 and ax.get_ylabel() == "gc_alloc_rate_norm (B/op)"
    assert ax.get_title().startswith(f"{MB1} \u2014 gc_alloc_rate_norm, mean of reruns")


def test_formats_follow_configuration(tmp_path, monkeypatch, capsys):
    _, names, _, _, _ = run_gc(tmp_path, monkeypatch, capsys, group("base", 1, [0.9, 0.95, 0.97]),
                               formats=("svg", "pdf"))
    assert batch_files(names) == [f"{STEM}.pdf", f"{STEM}.svg"]


def test_colours_and_order_follow_configuration(tmp_path, monkeypatch, capsys):
    rows = group("1a", 1, [0.9] * 3) + group("base", 1, [0.8] * 3)
    _, _, _, figures, _ = run_gc(tmp_path, monkeypatch, capsys, rows)
    ax = figures[STEM].axes[0]
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["Base", "S1_A"]     # configured order
    assert bars(figures[STEM]) == [(-0.2, pytest.approx(0.8)), (0.2, pytest.approx(0.9))]   # Base left, S1_A right


def test_one_warning_per_group_even_with_several_formats(tmp_path, monkeypatch, capsys):
    rows = group("base", 1, [0.9, 0.9, 0.9]) + group("base", 2, [0.9, 0.9, 0.9])
    _, _, messages, _, _ = run_gc(tmp_path, monkeypatch, capsys, rows, formats=("png", "svg"))
    assert fill_messages(messages) == [message("base", 1), message("base", 2)]


def test_table_step_warns_nothing_about_batch_fill_t(tmp_path):
    import warnings
    from src.config import Config
    config = Config(tmp_path / "l.csv", tmp_path / "o", benchmarks=(MB2,), figsize=(5, 3), dpi=40)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        gc_metrics.prepare(pd.DataFrame(group("base", 1, [0.9, 0.9, 0.9])), config)
    assert [str(w.message) for w in caught if "t interval" in str(w.message)] == []


def test_readme_documents_the_batch_fill_plot():
    text = (Path(__file__).parents[1] / "README.md").read_text()
    assert "gc_mb2_rawFinalize_batch_fill.png" in text
    section = text.split("### batch_fill bar plot")[1].split("\n## ")[0]
    for part in ("`mb2_rawFinalize` only", "`thrpt` rows", "mean of reruns", "linear y-axis from zero",
                 "larger of 1 and the automatic top", "batch_fill (batch_bytes / cfg_batchDesiredBytes)",
                 "95% t interval", "not\nclamped", "mb1_rawNoFinalize", "no t interval"):
        assert part in section, part
    assert "one figure per benchmark" not in text
    assert "warns for a group that has a\nmean but no t interval" in text


@pytest.mark.skipif(not RAW3.is_file(), reason="docs/raw3.csv is absent")
def test_raw3_gives_one_mb2_batch_fill_plot(tmp_path, monkeypatch, capsys):
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
    assert [Path(p).name for p in printed if "batch_fill." in p] == [f"{STEM}.png"]
    ax = figures[STEM].axes[0]
    assert ax.get_ylim()[0] == 0 and ax.get_ylim()[1] >= 1 and len(ax.texts) == 0 and len(figures[STEM].texts) == 0
    assert not [m for m in messages if "No batch_fill t interval" in m and MB1 in m]
