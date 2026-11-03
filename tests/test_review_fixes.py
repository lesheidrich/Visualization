"""Regressions for the review findings (change fix-review-findings) and test gaps found by mutation.

Expected values are literals, or are computed here by a separate resampling written out in the test.
"""
import dataclasses
import json
from pathlib import Path
import random
import re
import warnings

import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import numpy as np
import pandas as pd
import pytest

from conftest import gc_observation, observation
from src import cli, compare, gc_metrics, speedup, thrpt
from src.compare import prepare as compare_prepare
from src.config import Config
from src.utils import FIELLER_METHOD, T_METHOD, cell_seed, load_ledger

BENCH = "mb1_rawNoFinalize"
RERUNS = [f"{i:02d}" for i in range(1, 11)]
# Ten non-lattice values: no resampled mean falls on a coarse grid, so a wrong seed or order changes the CI.
SCORES = [103.7, 98.2, 121.9, 87.4, 110.3, 95.8, 130.1, 101.6, 92.7, 116.4]
BASE_SCORES = [50.3, 49.1, 55.7, 47.2, 52.9, 48.8, 58.4, 51.3, 46.9, 54.6]


@pytest.fixture(autouse=True)
def close_figures():
    yield
    plt.close("all")


def separate_bootstrap(numerators, denominators, seed):
    """95% percentile bootstrap over whole reruns, written out here (denominators None: mean)."""
    n = len(numerators)
    draws = np.random.default_rng(seed).integers(0, n, size=(n, 10000))
    numerators = np.asarray(numerators, dtype=float)
    if denominators is None:
        statistic = numerators[draws].sum(axis=0) / n
    else:
        statistic = numerators[draws].sum(axis=0) / np.asarray(denominators, dtype=float)[draws].sum(axis=0)
    return tuple(np.quantile(statistic, [0.025, 0.975]))


def shuffled(rows):
    result = list(rows)
    random.Random(7).shuffle(result)
    assert result != rows
    return result


def quiet(function, *args):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return function(*args)


def finite_segments(ax):
    found = [np.asarray(s) for c in ax.collections if isinstance(c, LineCollection) for s in c.get_segments()]
    return [s for s in found if not np.isnan(s).all()]


# ================================================================ 1. CI independent of ledger row order

def test_throughput_ci_does_not_depend_on_row_order_ten_reruns(config):
    rows = [observation("base", r, 1, s) for r, s in zip(RERUNS, SCORES)]
    low, high = separate_bootstrap(SCORES, None, cell_seed(BENCH, "base", 1, "score"))
    for ordering in (rows, shuffled(rows)):
        _, result = quiet(thrpt.prepare, pd.DataFrame(ordering), config)
        assert result.ci_low.iloc[0] == pytest.approx(low, rel=1e-12)
        assert result.ci_high.iloc[0] == pytest.approx(high, rel=1e-12)
    _, result = quiet(thrpt.prepare, pd.DataFrame(shuffled(rows)), config)
    # Everything else keeps the ledger order.
    assert json.loads(result.contributing_reruns.iloc[0]) == [r["rerun"] for r in shuffled(rows)]
    assert json.loads(result.source_rows.iloc[0]) == list(range(2, 12))        # ledger line order, unsorted


def test_speedup_ci_does_not_depend_on_row_order_and_stays_paired(config):
    rows = [row for r, b, s in zip(RERUNS, BASE_SCORES, SCORES)
            for row in (observation("base", r, 1, b), observation("1a", r, 2, s))]
    low, high = separate_bootstrap(SCORES, BASE_SCORES, cell_seed(BENCH, "1a", 2, "speedup"))
    for ordering in (rows, shuffled(rows)):
        _, result = quiet(speedup.prepare, pd.DataFrame(ordering), config)
        row = result[result.strategy.eq("1a")].iloc[0]
        assert row.ci_low == pytest.approx(low, rel=1e-12)
        assert row.ci_high == pytest.approx(high, rel=1e-12)
    _, result = quiet(speedup.prepare, pd.DataFrame(shuffled(rows)), config)
    row = result[result.strategy.eq("1a")].iloc[0]
    assert row.speedup == pytest.approx(sum(SCORES) / sum(BASE_SCORES), rel=1e-12)
    assert json.loads(row.contributing_reruns) == [r["rerun"] for r in shuffled(rows) if r["strategy"] == "1a"]


def test_alloc_ci_does_not_depend_on_row_order(config):
    rows = [gc_observation(strategy="base", rerun=r, gc_alloc_rate_norm=str(v)) for r, v in zip(RERUNS, SCORES)]
    low, high = separate_bootstrap(SCORES, None, cell_seed(BENCH, "base", 1, "gc_alloc_rate_norm"))
    for ordering in (rows, shuffled(rows)):
        _, averages = quiet(gc_metrics.prepare, pd.DataFrame(ordering), config)
        assert averages["gc_alloc_rate_norm_ci_low"].iloc[0] == pytest.approx(low, rel=1e-12)
        assert averages["gc_alloc_rate_norm_ci_high"].iloc[0] == pytest.approx(high, rel=1e-12)
    # With non-lattice values a wrong seed would give a different CI, so the check above has teeth.
    assert separate_bootstrap(SCORES, None, cell_seed(BENCH, "base", 1, "gc_alloc_rate_norm") + 1)[0] != low


# ================================================================ 2. interval_kind and titles through run()

def capture_titles(module, monkeypatch):
    titles = {}
    real = module.save_figure

    def spy(fig, stem, config):
        titles[Path(stem).name] = fig.axes[0].get_title()
        titles[Path(stem).name + ":figure"] = fig
        return real(fig, stem, config)

    monkeypatch.setattr(module, "save_figure", spy)
    return titles


def write_ledger(config, rows):
    pd.DataFrame(rows).rename(columns={"strategy": ""}).to_csv(config.ledger, index=False)


def bound_rows(blank_in=()):
    """base at 1 and 1a at 2 in three reruns; `blank_in` lists (rerun, strategy) that lack a bound."""
    rows = []
    for k, rerun in enumerate(("01", "02", "03")):
        for strategy, threads, score in (("base", 1, 100 + 7 * k), ("1a", 2, 150 + 11 * k)):
            extra = {"score_confidence_low": ""} if (rerun, strategy) in blank_in else {}
            rows.append(observation(strategy, rerun, threads, score, **extra))
    return rows


def test_thrpt_titles_through_run_name_only_drawn_error_bars(config, monkeypatch):
    blank = [("01", "base"), ("01", "1a"), ("02", "1a")]
    write_ledger(config, bound_rows(blank))
    titles = capture_titles(thrpt, monkeypatch)
    quiet(thrpt.run, config)
    stem = f"thrpt_{BENCH}_"
    assert titles[stem + "r01"] == f"{BENCH} — throughput, rerun 01"                      # every bound blank
    assert titles[stem + "r02"] == f"{BENCH} — throughput, rerun 02\nError bars: 99.9% ledger confidence bounds"
    assert titles[stem + "r03"] == f"{BENCH} — throughput, rerun 03\nError bars: 99.9% ledger confidence bounds"
    assert titles[stem + "avg"] == f"{BENCH} — throughput, mean of reruns\nError bars: {T_METHOD}"
    assert finite_segments(titles[stem + "r01:figure"].axes[0]) == []
    assert len(finite_segments(titles[stem + "r02:figure"].axes[0])) == 1       # only base is drawn


def test_thrpt_interval_kind_is_none_on_rows_without_a_bound(config):
    observations, _ = quiet(thrpt.prepare, pd.DataFrame(bound_rows([("01", "1a")])), config)
    kinds = {(r.rerun, r.strategy): r.interval_kind for r in observations.itertuples()}
    assert kinds[("01", "1a")] == "none"
    assert kinds[("01", "base")] == kinds[("02", "1a")] == "ledger confidence bounds"


def test_speedup_titles_through_run(config, monkeypatch):
    write_ledger(config, [observation(s, r, t, v) for r, (b, v) in zip(
        ("01", "02", "03"), ((100, 150), (110, 170), (105, 190)))
        for s, t, v in (("base", 1, b), ("1a", 2, v))])
    titles = capture_titles(speedup, monkeypatch)
    quiet(speedup.run, config)
    stem = f"speedup_{BENCH}_"
    assert titles[stem + "r01"] == f"{BENCH} — speedup, rerun 01"
    assert titles[stem + "avg"] == f"{BENCH} — speedup, mean of reruns\nError bars: {FIELLER_METHOD}"


def test_thrpt_one_rerun_writes_files_and_draws_no_error_bar_on_the_aggregate(config, monkeypatch):
    write_ledger(config, [observation("base", "01", 1, 100), observation("1a", "01", 2, 150)])
    titles = capture_titles(thrpt, monkeypatch)
    with pytest.warns(UserWarning, match="No CI"):
        paths = thrpt.run(config)
    assert sorted(p.name for p in paths) == [f"thrpt_{BENCH}_avg.csv", f"thrpt_{BENCH}_avg.png",
                                              f"thrpt_{BENCH}_r01.csv", f"thrpt_{BENCH}_r01.png"]
    aggregate = f"thrpt_{BENCH}_avg"
    assert titles[aggregate] == f"{BENCH} — throughput, mean of reruns"
    assert finite_segments(titles[aggregate + ":figure"].axes[0]) == []
    assert len(finite_segments(titles[f"thrpt_{BENCH}_r01:figure"].axes[0])) == 2   # ledger bounds
    avg = pd.read_csv(next(p for p in paths if p.name.endswith("_avg.csv")))
    assert avg.ci_low.isna().all() and (avg.interval_kind == "none").all()


# ================================================================ 3 and 4. ledger lines and field counts

def test_source_row_is_the_true_line_after_blank_lines(tmp_path):
    path = tmp_path / "ledger.csv"
    path.write_text(",rerun,score\nbase,01,1\n\nbase,02,2\n\n\nbase,03,3\n")
    loaded = load_ledger(path)
    assert loaded["rerun"].tolist() == ["01", "02", "03"]
    assert loaded["source_row"].tolist() == [2, 4, 7]               # header is line 1; blank lines count


def test_source_row_without_blank_lines_keeps_header_plus_one_numbering(tmp_path):
    path = tmp_path / "ledger.csv"
    path.write_text(",rerun,score\nbase,01,1\nbase,02,2\n")
    assert load_ledger(path)["source_row"].tolist() == [2, 3]


def test_blank_line_does_not_change_other_values(tmp_path):
    plain, blank = tmp_path / "a.csv", tmp_path / "b.csv"
    plain.write_text(",rerun,score\nbase,01,1\nbase,02,2\n")
    blank.write_text(",rerun,score\nbase,01,1\n\nbase,02,2\n")
    pd.testing.assert_frame_equal(load_ledger(plain).drop(columns=["source_row", "source_file"]),
                                  load_ledger(blank).drop(columns=["source_row", "source_file"]))


def test_trailing_comma_on_data_rows_fails_with_a_clear_message(tmp_path):
    path = tmp_path / "ledger.csv"
    path.write_text(",rerun,score\nbase,01,1,\nbase,02,2,\n")
    with pytest.raises(ValueError, match=r"line 2 has 4 fields but the header has 3.*trailing delimiter"):
        load_ledger(path, ",")


def test_trailing_tab_fails_the_same_way(tmp_path):
    path = tmp_path / "ledger.tsv"
    path.write_text("\trerun\tscore\nbase\t01\t1\nbase\t02\t2\t\n")
    with pytest.raises(ValueError, match=r"line 3 has 4 fields"):
        load_ledger(path, "\t")


# ================================================================ 5. allocation plot with no values

def test_alloc_plot_without_any_mean_has_no_legend_and_no_warning(config):
    rows = [gc_observation(strategy="base", rerun=r, gc_alloc_rate_norm="") for r in ("01", "02")]
    _, averages = quiet(gc_metrics.prepare, pd.DataFrame(rows), config)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        fig = gc_metrics.render_alloc(averages, config, BENCH, strategies=["base"], threads=[1])
    assert [str(w.message) for w in seen if "artists" in str(w.message) or "legend" in str(w.message)] == []
    assert fig.axes[0].get_legend() is None and len(fig.axes[0].patches) == 0


def test_alloc_plot_without_any_mean_is_still_written_by_run(config):
    write_ledger(config, [gc_observation(strategy="base", rerun=r, gc_alloc_rate_norm="") for r in ("01", "02")])
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        paths = gc_metrics.run(config)
    assert any(p.name == f"gc_{BENCH}_gc_alloc_rate_norm.png" for p in paths)
    assert not [w for w in seen if "No artists" in str(w.message)]


# ================================================================ 6. gc warning wording

def test_gc_missing_mean_warning_says_mean_of_reruns(config):
    rows = [gc_observation(strategy="base", rerun="01", pauses_max=""), gc_observation(strategy="base", rerun="02")]
    with pytest.warns(UserWarning, match=r"Mean of reruns of pauses_max missing for .*base/threads=1"):
        gc_metrics.prepare(pd.DataFrame(rows), config)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        gc_metrics.prepare(pd.DataFrame(rows), config)
    assert not [w for w in seen if re.search(r"\bAverage\b", str(w.message))]


# ================================================================ compare gaps

A = [101, 103, 98, 102, 100, 103, 98, 101, 102, 100]
B = [100, 102, 98, 101, 99, 103, 97, 100, 102, 98]
CLEAN = dict(hostname="h", git_sha="s", git_dirty="FALSE", run_timestamp="2026-01-01T00:00:00")


def pair_rows(a, b, threads, work_b=1.0):
    out = []
    for i, (x, y) in enumerate(zip(a, b), 1):
        for strategy, score, factor in (("2a", x, 1.0), ("2b", y, work_b)):
            out.append(observation(strategy, f"{i:02d}", threads, score, aux_recordBytes=str(score * 500 * factor),
                                   **CLEAN))
    return out


def compare_table(rows, tmp_path):
    config = Config(tmp_path / "l.csv", tmp_path / "out", benchmarks=(BENCH,), comparison_margin=0.05)
    return compare_prepare(pd.DataFrame(rows), config)[BENCH]


def test_compare_warns_exactly_once_per_no_spread_row(tmp_path):
    constant = [2 * x for x in B]                                   # ratio 2 in every rerun: no spread
    rows = pair_rows(constant, B, 1) + pair_rows(constant, B, 2) + pair_rows(A, B, 4)
    with pytest.warns(UserWarning) as seen:
        table = compare_table(rows, tmp_path)
    no_spread = [str(w.message) for w in seen if "No spread observed" in str(w.message)]
    assert sorted(no_spread) == sorted([f"No spread observed for {BENCH}/threads=1: no intervals",
                                        f"No spread observed for {BENCH}/threads=2: no intervals"])
    assert table.verdict.tolist() == ["no spread observed", "no spread observed", "equivalent"]


def test_compare_bootstrap_interval_equals_a_separate_paired_resampling(tmp_path):
    table = compare_table(pair_rows(A, B, 1), tmp_path)
    seed = cell_seed(BENCH, "2a_vs_2b", 1, "ratio")
    low, high = separate_bootstrap(A, B, seed)
    assert table.boot95_low.iloc[0] == pytest.approx(low, rel=1e-12)
    assert table.boot95_high.iloc[0] == pytest.approx(high, rel=1e-12)
    assert table.ci_seed.iloc[0] == seed and table.ci_resamples.iloc[0] == 10000


def test_compare_boot_interval_is_independent_of_row_order(tmp_path):
    rows = pair_rows(A, B, 1)
    a = compare_table(rows, tmp_path)
    b = compare_table(shuffled(rows), tmp_path)
    assert (a.boot95_low.iloc[0], a.boot95_high.iloc[0]) == (b.boot95_low.iloc[0], b.boot95_high.iloc[0])


@pytest.mark.parametrize("work_b, flagged", [
    (1.00099, False),    # work differs by 0.0990% of 500: below WORK_TOLERANCE (0.1% of the mean)
    (1.00102, True),     # 0.102%: above it
])
def test_work_differs_threshold_either_side(tmp_path, work_b, flagged):
    flags = compare_table(pair_rows(A, B, 1, work_b=work_b), tmp_path)["flags"].iloc[0].split(";")
    assert ("work_differs" in flags) is flagged


@pytest.mark.parametrize("order, unbalanced", [
    ([1] * 5 + [-1] * 4, False),     # 5 against 4: difference 1
    ([1] * 6 + [-1] * 4, False),     # difference 2 equals ORDER_TOLERANCE
    ([1] * 6 + [-1] * 3, True),      # difference 3 exceeds it
    ([1] * 3 + [-1] * 6, True),      # difference -3, either direction
])
def test_order_tolerance_either_side(order, unbalanced):
    y = np.log(np.array(A[:len(order)]) / np.array(B[:len(order)]))
    assert compare.order_columns(y, order)[3] is unbalanced


# ================================================================ gc CLI failure path

def test_gc_cli_failure_exits_nonzero_with_prefix_and_creates_no_folder(tmp_path, capsys):
    ledger = tmp_path / "ledger.csv"
    pd.DataFrame([gc_observation(pauses_max="-1")]).rename(columns={"strategy": ""}).to_csv(ledger, index=False)
    (tmp_path / "config.yaml").write_text(
        f"input: {{path: {ledger}}}\noutput: {{directory: {tmp_path / 'out'}}}\nbenchmarks: [{BENCH}]\n")
    code = cli.gc_main(["--config", str(tmp_path / "config.yaml")])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.startswith("gc: ") and "pauses_max" in captured.err
    assert not (tmp_path / "out").exists()


# ================================================================ help

def help_text(capsys):
    cli.help_main([])
    return capsys.readouterr().out


def test_help_never_says_bare_average(capsys):
    assert not re.search(r"\baverage\b", help_text(capsys), re.IGNORECASE)
    assert not [name for name, text in cli.COMMANDS.items() if re.search(r"\baverage\b", text, re.IGNORECASE)]


def test_help_describes_every_command_in_its_own_words(capsys):
    text = help_text(capsys)
    for name, description in cli.COMMANDS.items():
        assert f"uv run {name}" in text and description in text
    assert "mean of reruns" in cli.COMMANDS["thrpt"]


# ---------------------------------------------------------------- ledger reader, second pass


def _ledger(tmp_path, text):
    path = tmp_path / "ledger.csv"
    path.write_text(text, encoding="utf-8")
    return load_ledger(path, ",")


def test_whitespace_only_line_counts_as_a_blank_line(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        frame = _ledger(tmp_path, "a,b\n1,2\n   \n3,4\n")
    assert frame.source_row.tolist() == [2, 4]


def test_multi_line_record_reports_its_first_line(tmp_path):
    frame = _ledger(tmp_path, 'a,b\n1,"x\ny\nz"\n3,4\n')
    assert frame.source_row.tolist() == [2, 5]


def test_long_field_still_loads_and_restores_the_csv_limit(tmp_path):
    import csv
    original = csv.field_size_limit(131072)
    try:
        frame = _ledger(tmp_path, f"a,b\n1,{'x' * 200000}\n")
        assert len(frame.b.iloc[0]) == 200000
        assert csv.field_size_limit() == 131072
    finally:
        csv.field_size_limit(original)


def test_short_rows_stay_allowed(tmp_path):
    frame = _ledger(tmp_path, "a,b,c\n1,2\n3,4,5\n")
    assert frame.c.tolist() == ["", "5"]
    assert frame.source_row.tolist() == [2, 3]


def test_extra_field_message_names_all_likely_causes(tmp_path):
    with pytest.raises(ValueError, match="trailing delimiter, wrong delimiter or broken quote"):
        _ledger(tmp_path, "a,b\n1,2,3\n")


def test_unmatched_ledger_lines_warn_instead_of_staying_silent(tmp_path, monkeypatch):
    from src import utils
    monkeypatch.setattr(utils, "_ledger_lines", lambda reader: (next(reader), []))
    with pytest.warns(UserWarning, match="Cannot match ledger lines"):
        frame = _ledger(tmp_path, "a,b\n1,2\n")
    assert frame.source_row.tolist() == [2]


def test_gc_tables_have_no_bounds_in_the_ledger_unit(config):
    rows = [gc_observation(score_unit="ops/ms", score="2", score_error="1",
                           score_confidence_low="1", score_confidence_high="3")]
    observations = gc_metrics.prepare(pd.DataFrame(rows), config)[0]
    assert observations.score.iloc[0] == 2000
    assert not {"score_error", "score_confidence_low", "score_confidence_high"} & set(observations)


def test_throughput_ticks_are_plain_numbers_in_ops_per_second(config):
    averages = thrpt.prepare(pd.DataFrame([observation(score="14000000", score_unit="ops/s")]), config)[1]
    fig = thrpt.render(averages, config, "t", strategies=["base"], threads=[1])
    fig.canvas.draw()
    ax = fig.axes[0]
    assert ax.yaxis.get_offset_text().get_text() == ""
    assert any("," in tick.get_text() for tick in ax.get_yticklabels())
    assert ax.get_ylabel() == "Throughput (ops/s)"


# ---------------------------------------------------------------- finding missing data


def test_missing_pair_reads_like_the_ledger_tag(tmp_path):
    rows = [observation("2a", r, 1, 100 + i, run_tag=f"2a_n01_r{r}") for i, r in enumerate("12")]
    rows += [observation("2b", "1", 1, 100, run_tag="2b_n01_r1"), observation("2b", "2", 1, 100, run_tag="2b_n01_r2")]
    rows += [observation("2a", "2", 4, 100, run_tag="2a_n04_r2"),
             observation("2a", "1", 4, 100, run_tag="2a_n04_r1"), observation("2b", "1", 4, 100, run_tag="2b_n04_r1")]
    config = Config(tmp_path / "ledger.csv", tmp_path / "out", benchmarks=(BENCH,))
    with pytest.raises(ValueError) as caught:
        compare_prepare(pd.DataFrame(rows), config)
    assert str(caught.value) == f"{BENCH}/2a_n04_r2/thrpt missing 2b partner (ledger row 6)."


def test_missing_base_names_its_tag_and_a_row_of_that_rerun(config):
    rows = [observation("base", "1", 1, 100), observation("1a", "1", 1, 90), observation("1a", "2", 1, 90)]
    with pytest.raises(ValueError) as caught:
        speedup.prepare(pd.DataFrame(rows), config)
    assert f"{BENCH}/base_n01_r2/thrpt missing (ledger row 4 is in that rerun)" in str(caught.value)


@pytest.mark.parametrize("module", [thrpt, speedup])
def test_coverage_warning_is_the_missing_tag(config, module):
    rows = [observation("base", r, 1, 100) for r in "123"] + [observation("1a", r, 4, 100) for r in "13"]
    with pytest.warns(UserWarning) as seen:
        module.prepare(pd.DataFrame(rows), config)
    assert f"{BENCH}/1a_n04_r2/thrpt missing." in [str(w.message) for w in seen]


MB2 = "mb2_rawFinalize"


def test_no_batch_warning_for_no_finalize_benchmarks_but_one_for_mb2(config):
    rows = [gc_observation(strategy="base", rerun=r, aux_batches="0") for r in "12"]
    rows += [gc_observation(strategy="base", rerun=r, benchmark_short=MB2, aux_batches="0") for r in "12"]
    config = dataclasses.replace(config, benchmarks=(BENCH, MB2))
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        _, averages = gc_metrics.prepare(pd.DataFrame(rows), config)
    text = [str(w.message) for w in seen]
    assert not any(BENCH in m and "batch" in m for m in text)
    assert sum(f"{MB2}: aux_batches is 0 on every row" in m for m in text) == 1
    assert averages.batch_fill.isna().all()


def test_partial_zero_batches_on_mb2_name_the_rows(config):
    rows = [gc_observation(strategy="base", rerun="1", benchmark_short=MB2, aux_batches="0"),
            gc_observation(strategy="base", rerun="2", benchmark_short=MB2)]
    config = dataclasses.replace(config, benchmarks=(MB2,))
    with pytest.warns(UserWarning, match=re.escape("Zero aux_batches at ledger row(s) [2]")):
        gc_metrics.prepare(pd.DataFrame(rows), config)


# ---------------------------------------------------------------- sorted CSV rows


def shuffled_rows():
    rows = [observation(s, r, t, 100 + i) for i, (s, t, r) in enumerate(
        [("1a", 2, "10"), ("base", 1, "2"), ("1a", 1, "2"), ("1a", 2, "2"), ("base", 1, "10"), ("1a", 1, "10")])]
    return rows


def test_sorted_for_csv_orders_rows_and_keeps_every_value(config):
    from src.utils import sorted_for_csv
    frame = pd.DataFrame(shuffled_rows())
    result = sorted_for_csv(frame, config)
    assert list(zip(result.strategy, result.threads_blobshuffle, result.rerun)) == [
        ("base", "1", "2"), ("base", "1", "10"), ("1a", "1", "2"), ("1a", "1", "10"), ("1a", "2", "2"), ("1a", "2", "10")]
    pd.testing.assert_frame_equal(result.sort_index(), frame)


@pytest.mark.parametrize("module,stem", [(thrpt, "thrpt"), (speedup, "speedup")])
def test_csvs_are_sorted_and_plots_do_not_change(config, module, stem, tmp_path):
    write_ledger(config, shuffled_rows())
    paths = quiet(module.run, config)
    table = pd.read_csv(next(p for p in paths if p.name.endswith("_avg.csv")), dtype=str)
    assert table.strategy.tolist() == ["base", "1a", "1a"]
    assert table.threads_blobshuffle.tolist() == ["1", "1", "2"]
    first = {p.name: p.read_bytes() for p in paths if p.suffix == ".png"}
    ordered = sorted(shuffled_rows(), key=lambda r: (r["strategy"] != "base", r["strategy"], int(r["threads_blobshuffle"]), int(r["rerun"])))
    write_ledger(config, ordered)
    second = quiet(module.run, config)
    assert {p.name: p.read_bytes() for p in second if p.suffix == ".png"} == first


def test_gc_csvs_are_sorted_as_the_last_step(config):
    rows = [gc_observation(strategy=s, rerun=r, threads=t, score=100 + i) for i, (s, t, r) in enumerate(
        [("1a", 2, "10"), ("base", 1, "2"), ("1a", 1, "2"), ("1a", 2, "2"), ("base", 1, "10"), ("1a", 1, "10")])]
    write_ledger(config, rows)
    paths = quiet(gc_metrics.run, config)
    tables = {p.name: pd.read_csv(p, dtype=str) for p in paths if p.suffix == ".csv"}
    for name, expected in ((f"gc_{BENCH}_avg.csv", ["base", "1a", "1a"]),
                           (f"gc_{BENCH}_pauses_max_median.csv", ["base", "1a", "1a"]),
                           (f"gc_{BENCH}_r_all.csv", ["base", "base", "1a", "1a", "1a", "1a"])):
        assert tables[name].strategy.tolist() == expected, name
    assert tables[f"gc_{BENCH}_r_all.csv"].rerun.tolist() == ["2", "10", "2", "10", "2", "10"]


def test_sorted_for_csv_keeps_rows_on_a_duplicate_index_and_sorts_threads_as_numbers(config):
    from src.utils import sorted_for_csv
    frame = pd.DataFrame(
        {"strategy": ["base", "base", "base"], "threads_blobshuffle": ["10", "2", "4"]}, index=[5, 5, 7])
    assert sorted_for_csv(frame, config).threads_blobshuffle.tolist() == ["2", "4", "10"]


@pytest.mark.parametrize("tag,expected", [
    ("raw3_2a_n04_r3", "raw3_2a_n04_r3"),   # prefix kept
    ("2a_n01_r1", "2a_n04_r3"),             # disagrees with the row
    ("sweep", "2a_n04_r3"),                 # says nothing
    ("", "2a_n04_r3")])
def test_row_tag_never_misstates_the_row(tag, expected):
    from src.utils import row_tag
    assert row_tag({"strategy": "2a", "threads_blobshuffle": 4, "rerun": "3", "run_tag": tag}) == expected


def test_one_blank_among_mb1_zeros_warns_only_about_the_blank(config):
    rows = [gc_observation(strategy="base", rerun=r, threads=t, aux_batches=b)
            for r, t, b in (("1", 1, "0"), ("2", 1, "0"), ("1", 2, "0"), ("2", 2, ""))]
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        gc_metrics.prepare(pd.DataFrame(rows), config)
    text = [str(w.message) for w in seen if "batch" in str(w.message).lower()]
    assert any("Blank aux_batches at ledger row(s) [5]" in m for m in text)
    assert not any("Zero aux_batches" in m or "is 0 on every row" in m for m in text)
    assert not any("threads=1" in m for m in text)


def test_blank_other_metric_on_a_no_batch_benchmark_still_warns(config):
    rows = [gc_observation(strategy="base", rerun="1", aux_batches="0", pauses_max=""),
            gc_observation(strategy="base", rerun="2", aux_batches="0")]
    with pytest.warns(UserWarning, match="Mean of reruns of pauses_max missing"):
        gc_metrics.prepare(pd.DataFrame(rows), config)
