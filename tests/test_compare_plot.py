"""2A vs 2B ratio plot: unit, integration, feature, spec scenarios and README behaviour.

Worked example (strategies/theory/comparison_2a_2b.md section 4, ten pairs): with five thread counts
the dot is 1.008034, the thick bar 1.002328 to 1.013773 and the thin bar 1.001464 to 1.014647.
Multiplying every 2A score by k multiplies each of those numbers by exactly k, which gives hand-derived
values for other rows (k = 2: 2.016068, 2.004656 to 2.027546, 2.002928 to 2.029294).
With four thread counts alpha = 0.05 / 4 = 0.0125: levels 0.9875 (thin bar) and 0.975 (thick bar).
"""
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.text import Text
import pandas as pd
import pytest

from audit_support import read_csv, run_main, write_run
from conftest import observation
from src import cli, compare
from src.compare import prepare, render_ratio
from src.config import Config

BENCH = "mb1_rawNoFinalize"
BENCH2 = "mb2_rawFinalize"
ROOT = Path(__file__).parents[1]
A = [101, 103, 98, 102, 100, 103, 98, 101, 102, 100]
B = [100, 102, 98, 101, 99, 103, 97, 100, 102, 98]
DOT, THICK, THIN = 1.008034, (1.002328, 1.013773), (1.001464, 1.014647)
TOL = dict(abs=2e-5)
NO_MARGIN_WARNING = "No comparison margin set: equivalence cannot be claimed"


def scaled(k, a=A):
    return [v * k for v in a]


def ledger(per_thread, benchmark=BENCH, b=B):
    """Rows for {threads: 2A scores}; 2B scores are `b` at every thread count."""
    return [observation(strategy, f"{i:02d}", n, score, benchmark_short=benchmark)
            for n, a in per_thread.items() for strategy, scores in (("2a", a), ("2b", b))
            for i, score in enumerate(scores, 1)]


def make_config(tmp_path, margin=0.05, benchmarks=(BENCH,)):
    return Config(tmp_path / "l.csv", tmp_path / "out", benchmarks=tuple(benchmarks),
                  figsize=(6, 4), dpi=40, comparison_margin=margin)


def draw(rows, tmp_path, margin=0.05, benchmark=BENCH):
    cfg = make_config(tmp_path, margin, (benchmark,))
    table = prepare(pd.DataFrame(rows), cfg)[benchmark]
    return render_ratio(table, cfg, benchmark), table


def parts(fig):
    """(dots, thick, thin, reference lines) read back from the axes."""
    ax = fig.axes[0]
    dots = [(list(map(float, l.get_xdata())), list(map(float, l.get_ydata())))
            for l in ax.lines if l.get_marker() == "o"]
    bars = {}
    for c in ax.collections:
        if isinstance(c, LineCollection):
            bars[round(float(c.get_linewidth()[0]), 6)] = sorted(
                (float(s[0][0]), float(s[0][1]), float(s[1][1])) for s in c.get_segments())
    lines = sorted((float(l.get_ydata()[0]), l.get_linestyle()) for l in ax.lines if l.get_marker() != "o")
    return dots, bars, lines


def legend_labels(fig):
    return [t.get_text() for t in fig.axes[0].get_legend().get_texts()]


def five(per=None):
    return per or {n: A for n in (1, 2, 4, 8, 16)}


# ---- unit: values and shape -----------------------------------------------------------------

def test_unit_worked_example_dot_and_bars(tmp_path):
    fig, _ = draw(ledger(five()), tmp_path)
    dots, bars, _ = parts(fig)
    assert dots[0][0] == [0, 1, 2, 3, 4]
    assert dots[0][1] == pytest.approx([DOT] * 5, **TOL)
    assert len(bars) == 2 and sorted(bars) == [1.5, 6.0]
    for x, (low, high) in enumerate([THICK] * 5):
        assert bars[6.0][x][0] == x
        assert (bars[6.0][x][1], bars[6.0][x][2]) == pytest.approx((low, high), **TOL)
    for x in range(5):
        assert bars[1.5][x][0] == x
        assert (bars[1.5][x][1], bars[1.5][x][2]) == pytest.approx(THIN, **TOL)


def test_unit_scaled_rows_have_hand_derived_heights(tmp_path):
    fig, _ = draw(ledger({1: scaled(1), 2: scaled(2), 4: scaled(0.5), 8: scaled(1), 16: scaled(1)}), tmp_path)
    dots, bars, _ = parts(fig)   # five thread counts, so the worked-example levels apply to every row
    assert dots[0][1][:3] == pytest.approx([DOT, DOT * 2, DOT * 0.5], **TOL)
    flat = lambda key: [v for b in bars[key][:3] for v in b[1:]]
    assert flat(6.0) == pytest.approx([*THICK, 2.004656, 2.027546, 0.501164, 0.5068865], **TOL)
    assert flat(1.5) == pytest.approx([*THIN, 2.002928, 2.029294, 0.500732, 0.5073235], **TOL)


def test_unit_thread_counts_are_sorted_with_even_positions_and_tick_labels(tmp_path):
    fig, table = draw(ledger({1: scaled(1), 2: scaled(2), 4: scaled(0.5), 16: scaled(3)}), tmp_path)
    shuffled = table.iloc[[3, 0, 2, 1]]
    fig = render_ratio(shuffled, make_config(tmp_path), BENCH)
    dots, bars, _ = parts(fig)
    ax = fig.axes[0]
    assert dots[0][0] == [0, 1, 2, 3]
    assert dots[0][1] == pytest.approx([DOT, DOT * 2, DOT * 0.5, DOT * 3], **TOL)
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1", "2", "4", "16"]
    assert list(ax.get_xticks()) == [0, 1, 2, 3]
    assert [b[0] for b in bars[6.0]] == [0, 1, 2, 3]


def test_unit_labels_title_scale_and_no_extra_text(tmp_path):
    fig, _ = draw(ledger(five()), tmp_path)
    ax = fig.axes[0]
    assert ax.get_xlabel() == "BlobShuffle threads"
    assert ax.get_ylabel() == "2A / 2B throughput ratio"
    assert ax.get_title() == f"{BENCH} — 2A / 2B, geometric mean of per-rerun ratios"
    assert ax.get_yscale() == "linear" and ax.get_xscale() == "linear"
    assert list(ax.texts) == [] and list(fig.texts) == [] and len(fig.axes) == 1
    shown = {t.get_text() for t in fig.findobj(Text) if t.get_text() and t.get_visible()}
    ticks = {t.get_text() for t in ax.get_xticklabels() + ax.get_yticklabels()}
    assert {t.get_text() for t in ax.get_xticklabels()} == {"1", "2", "4", "8", "16"}
    assert shown - ticks == {ax.get_title(), "BlobShuffle threads", "2A / 2B throughput ratio",
                             *legend_labels(fig)}


def test_unit_axis_includes_one_and_both_margin_lines(tmp_path):
    fig, _ = draw(ledger({n: scaled(3) for n in (1, 2)}), tmp_path, margin=0.05)
    low, high = fig.axes[0].get_ylim()
    assert low < 0.95238 and high > 3.02   # one and the margin lines lie inside although every dot is near 3
    fig, _ = draw(ledger(five()), tmp_path, margin=None)
    low, high = fig.axes[0].get_ylim()
    assert low < 1.0 < 1.001464 and high > 1.014647


# ---- spec scenarios -----------------------------------------------------------------------------

def test_spec_worked_example(tmp_path):
    """WHEN the ten pairs run with five thread counts THEN dot ~1.008034, thick 1.002328-1.013773, thin 1.001464-1.014647."""
    fig, _ = draw(ledger(five()), tmp_path)
    dots, bars, _ = parts(fig)
    assert dots[0][1][0] == pytest.approx(1.008034, abs=5e-6)
    assert bars[6.0][0][1:] == pytest.approx((1.002328, 1.013773), abs=5e-6)
    assert bars[1.5][0][1:] == pytest.approx((1.001464, 1.014647), abs=5e-6)


def run_command(tmp_path, capsys, rows, **kwargs):
    cfg = write_run(tmp_path, rows, **kwargs)
    code, printed, warns, err = run_main(cli.compare_main, cfg, capsys)
    assert code == 0, err
    return printed, warns, sorted((tmp_path / "out").iterdir())[0]


def test_spec_other_outputs_unchanged(tmp_path, capsys):
    """WHEN compare runs with this plot THEN the CSV is byte-identical to the table written by prepare alone."""
    rows = ledger(five())
    printed, _, folder = run_command(tmp_path, capsys, rows, margin=0.05)
    csv = (folder / f"compare_{BENCH}_avg.csv").read_bytes()
    expected = prepare(pd.DataFrame(rows), make_config(tmp_path))[BENCH].to_csv(index=False).encode()
    assert csv == expected
    table = read_csv(folder / f"compare_{BENCH}_avg.csv")
    assert table["logt_ratio"][0].startswith("1.00803") and table["equiv_low"][0].startswith("1.00232")
    assert sorted(p.name for p in folder.iterdir()) == [f"compare_{BENCH}_avg.csv", f"compare_{BENCH}_ratio.png"]


def test_spec_benchmarks_stay_separate(tmp_path, capsys, monkeypatch):
    """WHEN both benchmarks are requested THEN each plot shows only its own rows."""
    rows = ledger({1: scaled(1), 2: scaled(1)}) + ledger({1: scaled(2), 2: scaled(2), 4: scaled(2)}, BENCH2)
    seen = {}
    original = compare.render_ratio
    monkeypatch.setattr(compare, "render_ratio", lambda t, c, b: seen.setdefault(b, original(t, c, b)))
    printed, _, folder = run_command(tmp_path, capsys, rows, benchmarks=(BENCH, BENCH2), margin=0.05)
    assert sorted(p.name for p in folder.iterdir()) == [
        f"compare_{BENCH}_avg.csv", f"compare_{BENCH}_ratio.png",
        f"compare_{BENCH2}_avg.csv", f"compare_{BENCH2}_ratio.png"]
    first, second = (parts(seen[b])[0][0] for b in (BENCH, BENCH2))
    assert first[0] == [0, 1] and first[1] == pytest.approx([DOT] * 2, **TOL)
    assert second[0] == [0, 1, 2] and second[1] == pytest.approx([DOT * 2] * 3, **TOL)
    assert seen[BENCH].axes[0].get_title().startswith(BENCH)
    assert seen[BENCH2].axes[0].get_title().startswith(BENCH2)


def test_spec_four_thread_counts_legend_levels(tmp_path):
    """WHEN a benchmark has four thread counts THEN the legend says 97.5% (thick) and 98.75% (thin)."""
    fig, _ = draw(ledger({n: A for n in (1, 2, 4, 8)}), tmp_path)
    labels = legend_labels(fig)
    assert "97.5% equivalence interval" in labels and "98.75% difference interval" in labels
    assert labels[1:3] == ["97.5% equivalence interval", "98.75% difference interval"]


def test_spec_five_thread_counts_legend_levels(tmp_path):
    fig, _ = draw(ledger(five()), tmp_path)
    assert legend_labels(fig) == ["geometric mean ratio", "98% equivalence interval",
                                  "99% difference interval", "margin 5%", "no difference"]


def test_spec_no_spread_row_has_dot_and_no_bars_and_adds_no_warning(tmp_path, recwarn):
    """WHEN a row has `no spread observed` THEN its dot has no bars and the plot adds no warning."""
    rows = ledger({1: A, 2: B})   # at 2 threads 2A = 2B in every rerun: ratios all exactly 1
    cfg = make_config(tmp_path)
    with pytest.warns(UserWarning, match="No spread observed for mb1_rawNoFinalize/threads=2"):
        table = prepare(pd.DataFrame(rows), cfg)[BENCH]
    assert table["verdict"].tolist() == ["equivalent", "no spread observed"]
    recwarn.clear()
    fig = render_ratio(table, cfg, BENCH)
    assert len(recwarn) == 0
    dots, bars, _ = parts(fig)
    assert dots[0][0] == [0, 1] and dots[0][1][1] == 1.0
    # F = 2: alpha = 0.025; row one keeps both bars, row two has neither
    assert [b[0] for b in bars[6.0]] == [0] and [b[0] for b in bars[1.5]] == [0]


def test_spec_margin_lines(tmp_path):
    """WHEN the margin is 0.05 THEN lines mark 1.05, 0.95238 and 1; the legend names the margin and no difference."""
    fig, _ = draw(ledger(five()), tmp_path, margin=0.05)
    _, _, lines = parts(fig)
    assert [y for y, _ in lines] == pytest.approx([1 / 1.05, 1.0, 1.05], abs=1e-9)
    assert [y for y, _ in lines][0] == pytest.approx(0.95238, abs=5e-6)
    assert [s for _, s in lines] == ["--", "-", "--"]
    assert legend_labels(fig)[-2:] == ["margin 5%", "no difference"]


def test_spec_no_margin(tmp_path, capsys):
    """WHEN no margin is configured THEN only the line at 1 is drawn, and the plot adds no warning."""
    fig, _ = draw(ledger(five()), tmp_path, margin=None)
    _, _, lines = parts(fig)
    assert lines == [(1.0, "-")]
    assert legend_labels(fig) == ["geometric mean ratio", "98% equivalence interval",
                                  "99% difference interval", "no difference"]
    # through the command: the only warning is the existing no-margin one, once
    _, warns, folder = run_command(tmp_path, capsys, ledger(five()))
    assert warns == [NO_MARGIN_WARNING]
    assert (folder / f"compare_{BENCH}_ratio.png").exists()


def test_spec_verdicts_stay_in_the_table(tmp_path):
    """WHEN the plot is drawn THEN no verdict, note, flag or other text is on the axes or figure."""
    fig, table = draw(ledger(five()), tmp_path)
    assert table["verdict"].iloc[0] == "equivalent" and table["note"].iloc[0] != ""
    assert list(fig.axes[0].texts) == [] and list(fig.texts) == []
    everything = " ".join(t.get_text() for t in fig.findobj(Text))
    for word in ("equivalent ", "faster", "inconclusive", "below margin", "dirty_build", "work_differs"):
        assert word not in everything.replace("equivalence interval", "")


# ---- integration / feature --------------------------------------------------------------------------

def test_feature_formats_and_printed_paths(tmp_path, capsys):
    printed, _, folder = run_command(tmp_path, capsys, ledger(five()), formats=("png", "svg"), margin=0.05)
    assert sorted(p.name for p in folder.iterdir()) == [
        f"compare_{BENCH}_avg.csv", f"compare_{BENCH}_ratio.png", f"compare_{BENCH}_ratio.svg"]
    assert [p.split("/")[-1] for p in printed] == [
        f"compare_{BENCH}_avg.csv", f"compare_{BENCH}_ratio.png", f"compare_{BENCH}_ratio.svg"]


def test_feature_existing_warnings_neither_change_nor_double(tmp_path, capsys):
    rows = ledger({1: A, 2: B})
    _, warns, _ = run_command(tmp_path, capsys, rows, margin=0.05)
    assert warns == ["No spread observed for mb1_rawNoFinalize/threads=2: no intervals"]


def test_feature_failure_leaves_no_output_folder(tmp_path, capsys):
    rows = [r for r in ledger(five()) if not (r["strategy"] == "2b" and r["rerun"] == "04")]
    cfg = write_run(tmp_path, rows, margin=0.05)
    code, printed, _, err = run_main(cli.compare_main, cfg, capsys)
    assert code == 1 and "missing" in err and printed == []
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())


def test_feature_figures_are_closed_after_the_run(tmp_path, capsys):
    run_command(tmp_path, capsys, ledger(five()), margin=0.05)
    assert plt.get_fignums() == []


def test_feature_show_is_called_once_per_benchmark(tmp_path, capsys, monkeypatch):
    shown = []
    monkeypatch.setattr(plt, "show", lambda *a, **k: shown.append(1))
    cfg = write_run(tmp_path, ledger(five()), margin=0.05)
    import yaml
    data = yaml.safe_load(cfg.read_text())
    data["plot"]["show"] = True
    cfg.write_text(yaml.safe_dump(data))
    code, *_ = run_main(cli.compare_main, cfg, capsys)
    assert code == 0 and shown == [1]


@pytest.mark.skipif(not (ROOT / "docs" / "raw3.csv").exists(), reason="docs/raw3.csv is absent from this clone")
def test_feature_raw3_ledger_plots_both_benchmarks(tmp_path, capsys):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"input: {{path: {ROOT / 'docs' / 'raw3.csv'}}}\noutput: {{directory: {tmp_path / 'out'}, formats: [png]}}\n"
                   "benchmarks: [mb1_rawNoFinalize, mb2_rawFinalize]\ncomparison: {margin: 0.05}\n")
    code, printed, _, err = run_main(cli.compare_main, cfg, capsys)
    assert code == 0, err
    assert [p.split("/")[-1] for p in printed] == [
        "compare_mb1_rawNoFinalize_avg.csv", "compare_mb1_rawNoFinalize_ratio.png",
        "compare_mb2_rawFinalize_avg.csv", "compare_mb2_rawFinalize_ratio.png"]


# ---- README and help text -----------------------------------------------------------------------------

def test_readme_and_help_describe_the_plot():
    readme = (ROOT / "README.md").read_text()
    assert "| `compare_mb1_rawNoFinalize_ratio.png` |" in readme
    assert "It draws no plot" not in readme
    assert "`compare_<benchmark>_ratio.<fmt>`" in readme
    for text in ("thick bar is the equivalence interval", "thin bar is the\ndifference interval",
                 "`2A / 2B throughput ratio`", "`geometric mean of per-rerun ratios`",
                 "1 + margin and 1 / (1 + margin)"):
        assert text in readme
    assert cli.COMMANDS["compare"] == ("Tabulate the paired 2A vs 2B comparison with an equivalence verdict "
                                       "per thread count; also plot the ratio per thread count.")
