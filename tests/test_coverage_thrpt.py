"""Coverage audit, thrpt: whole-table shapes and literal cells through the CLI main.

Ledger (benchmark mb1_rawNoFinalize, reruns 01..10, rows written in reverse rerun order):
  BASE@1   100, 110, ..., 190  (rerun 01..10)         -> mean 145, t [123.34, 166.66] (README)
  1a@1     150 in every rerun                         -> no spread, no CI
  1a@2     rerun 03: 200 ops/s; rerun 01: 0.1 ops/ms  -> 100 ops/s; scores 100, 200
Ledger lines (header = 1): rerun 10 -> BASE 2, 1a@1 3; rerun 9 -> 4, 5; ... rerun 4 -> 14, 15;
rerun 3 -> 16, 17, 18; rerun 2 -> 19, 20; rerun 1 -> 21, 22, 23.
"""
import json

import numpy as np
import pytest

from audit_support import read_csv, run_main, write_run
from conftest import observation
from src.cli import main
from src.config import load_config
from src.thrpt import prepare, render
from src.utils import load_ledger


BENCH = "mb1_rawNoFinalize"


def ledger_rows():
    rows = []
    for rerun in range(10, 0, -1):
        name = f"{rerun:02d}"
        rows.append(observation(strategy="BASE", rerun=name, score=100 + 10 * (rerun - 1)))
        rows.append(observation(strategy="1a", rerun=name, score=150))
        if rerun == 3:
            rows.append(observation(strategy="1a", rerun=name, threads=2, score=200))
        if rerun == 1:
            rows.append(observation(strategy="1a", rerun=name, threads=2, score=0.1, score_unit="ops/ms"))
    return rows


@pytest.fixture
def run(tmp_path, capsys):
    cfg = write_run(tmp_path, ledger_rows())
    code, printed, warns, err = run_main(main, cfg, capsys)
    assert code == 0, err
    folder = sorted((tmp_path / "out").iterdir())
    assert len(folder) == 1
    return {"dir": folder[0], "printed": printed, "warns": warns, "cfg": cfg, "tmp": tmp_path}


# ---- shapes: which files exist ---------------------------------------------------------

def test_shape_files_written_and_printed(run):
    names = sorted(p.name for p in run["dir"].iterdir())
    expected = sorted([f"thrpt_{BENCH}_{part}.{ext}" for part in
                       [f"r{n:02d}" for n in range(1, 11)] + ["avg"] for ext in ("csv", "png")])
    assert names == expected  # 10 reruns + avg, csv and png each; nothing else (22 files)
    assert sorted(p.split("/")[-1] for p in run["printed"]) == expected
    assert run["dir"].name.endswith("_thrpt")


# ---- cells and shapes: the mean-of-reruns CSV ------------------------------------------

AVG_COLUMNS = ["benchmark_short", "strategy", "threads_blobshuffle", "score", "score_unit",
               "ci_low", "ci_high", "t_low", "t_high", "ci_method", "ci_resamples", "ci_seed",
               "interval_kind", "n_reruns", "total_reruns", "contributing_reruns", "source_rows",
               "source_files", "original_strategies"]


def test_avg_csv_shape_and_literal_cells(run):
    table = read_csv(run["dir"] / f"thrpt_{BENCH}_avg.csv")
    assert list(table.columns) == AVG_COLUMNS
    # configured order base, 1a; then threads ascending
    assert table[["strategy", "threads_blobshuffle"]].values.tolist() == [["base", "1"], ["1a", "1"], ["1a", "2"]]
    base, flat, two = (table.iloc[i] for i in range(3))
    # BASE: mean (100+...+190)/10 = 145; t: 145 -/+ 2.262157 * 30.276504 / sqrt(10)
    assert float(base["score"]) == 145.0
    assert float(base["t_low"]) == pytest.approx(123.341494, abs=1e-5)
    assert float(base["t_high"]) == pytest.approx(166.658506, abs=1e-5)
    # bootstrap of the ten scores, seed sha256("42|mb1_rawNoFinalize|base|1|score"), computed apart
    assert (float(base["ci_low"]), float(base["ci_high"])) == (127.0, 163.0)
    assert base["ci_seed"] == "5721083545952246177" and base["ci_resamples"] == "10000"
    assert base["ci_method"] == "95% percentile bootstrap of reruns"
    assert base["interval_kind"] == "95% t interval of reruns"
    assert (base["n_reruns"], base["total_reruns"]) == ("10", "10")
    assert json.loads(base["contributing_reruns"]) == [f"{n:02d}" for n in range(10, 0, -1)]
    assert json.loads(base["source_rows"]) == [2, 4, 6, 8, 10, 12, 14, 16, 19, 21]
    assert json.loads(base["original_strategies"]) == ["BASE"]
    assert base["score_unit"] == "ops/s"
    # no spread: mean 150, every interval field blank, kind none, a warning names it
    assert float(flat["score"]) == 150.0
    assert [flat[c] for c in ("ci_low", "ci_high", "t_low", "t_high", "ci_method", "ci_resamples",
                              "ci_seed")] == [""] * 7
    assert flat["interval_kind"] == "none"
    assert any(f"No CI for {BENCH}/1a/threads=1" in w for w in run["warns"])
    # two reruns 100 (0.1 ops/ms) and 200: mean 150; bootstrap [100, 200] (spec);
    # t: 150 -/+ 12.706205 * 70.710678 / sqrt(2) = 150 -/+ 635.310237
    assert float(two["score"]) == 150.0
    assert (float(two["ci_low"]), float(two["ci_high"])) == (100.0, 200.0)
    assert float(two["t_low"]) == pytest.approx(-485.310237, abs=1e-4)  # true value kept below zero
    assert float(two["t_high"]) == pytest.approx(785.310237, abs=1e-4)
    assert (two["n_reruns"], two["total_reruns"]) == ("2", "10")
    assert json.loads(two["contributing_reruns"]) == ["03", "01"]  # ledger order, not sorted
    assert json.loads(two["source_rows"]) == [18, 23]
    assert two["ci_seed"] == "4948964953852310413"
    # missing reruns are warned with the run tag, in the documented form
    assert f"{BENCH}/1a_n02_r02/thrpt missing." in run["warns"]
    assert sum("1a_n02_r" in w and "missing" in w for w in run["warns"]) == 8


def test_per_rerun_csv_shape_and_cells(run):
    table = read_csv(run["dir"] / f"thrpt_{BENCH}_r01.csv")
    assert table["strategy"].tolist() == ["base", "1a", "1a"]
    assert table["threads_blobshuffle"].tolist() == ["1", "1", "2"]
    assert [float(x) for x in table["score"]] == [100.0, 150.0, 100.0]
    # ledger bounds are 0.9 and 1.1 of the score; 0.1 ops/ms bounds 0.09, 0.11 -> 90, 110 ops/s
    assert [round(float(x), 6) for x in table["score_confidence_low"]] == [90.0, 135.0, 90.0]
    assert [round(float(x), 6) for x in table["score_confidence_high"]] == [110.0, 165.0, 110.0]
    assert table["original_score"].tolist() == ["100", "150", "0.1"]
    assert table["original_score_unit"].tolist() == ["ops/s", "ops/s", "ops/ms"]
    assert set(table["score_unit"]) == {"ops/s"}
    assert table["interval_kind"].tolist() == ["ledger confidence bounds"] * 3
    assert table["n_reruns"].tolist() == ["1", "1", "1"] and set(table["total_reruns"]) == {"10"}
    assert table["source_row"].tolist() == ["21", "22", "23"]
    assert json.loads(table["contributing_reruns"].iloc[0]) == ["01"]
    # input columns first; then provenance, originals (score_error is added blank), then counts
    assert list(table.columns) == [
        "strategy", "rerun", "benchmark_short", "mode", "threads_blobshuffle", "score",
        "score_confidence_low", "score_confidence_high", "score_unit", "source_row", "source_file",
        "strategy_original", "original_score", "original_score_confidence_low",
        "original_score_confidence_high", "score_error", "original_score_error",
        "original_score_unit", "interval_kind", "n_reruns",
        "total_reruns", "contributing_reruns"]


# ---- shapes: the plots -------------------------------------------------------------------

def _figure(run, which):
    config = load_config(run["cfg"])
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        observations, averages = prepare(load_ledger(config.ledger), config)
    table = averages if which == "avg" else observations[observations["rerun"].eq(which)]
    return render(table, config, f"{BENCH} — throughput, {which}", strategies=["base", "1a"], threads=[1, 2])


def test_avg_plot_shape(run):
    fig = _figure(run, "avg")
    ax = fig.axes[0]
    bars = ax.patches
    assert len(bars) == 3
    width = 0.8 / 2
    # base is slot 0 of 2 (offset -0.2), 1a slot 1 (+0.2); thread positions 0 and 1; no bar for base@2
    assert [round(b.get_x() + b.get_width() / 2, 6) for b in bars] == [-0.2, 0.2, 1.2]
    assert [b.get_width() for b in bars] == [pytest.approx(width)] * 3
    assert [b.get_height() for b in bars] == [145.0, 150.0, 150.0]
    segments = [seg for c in ax.collections for seg in c.get_segments() if len(seg) == 2 and seg[0][0] == seg[1][0]]
    spans = sorted((round(s[0][0], 6), round(s[0][1], 3), round(s[1][1], 3)) for s in segments
                   if s[0][1] != s[1][1])
    # only the t-interval bars: base@1 and 1a@2 (1a@1 has none); y extent from t_low to t_high
    assert spans == [(-0.2, 123.341, 166.659), (1.2, -485.31, 785.31)]
    assert ax.get_ylim()[0] == 0  # axis starts at 0 although t_low < 0
    assert ax.get_yscale() == "linear"
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1", "2"]
    assert ax.get_xlabel() == "BlobShuffle threads" and ax.get_ylabel() == "Throughput (ops/s)"
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["Base", "S1_A"]
    assert ax.get_title() == f"{BENCH} — throughput, avg\nError bars: 95% t interval of reruns"
    assert ax.yaxis.get_major_formatter()(14_000_000, 0) == "14,000,000"
    assert len(fig.axes) == 1


def test_per_rerun_plot_shape_uses_ledger_bounds(run):
    fig = _figure(run, "01")
    ax = fig.axes[0]
    assert [b.get_height() for b in ax.patches] == [100.0, 150.0, 100.0]
    spans = sorted((round(s[0][0], 6), round(s[0][1], 3), round(s[1][1], 3))
                   for c in ax.collections for s in c.get_segments()
                   if len(s) == 2 and s[0][0] == s[1][0] and s[0][1] != s[1][1])
    assert spans == [(-0.2, 90.0, 110.0), (0.2, 135.0, 165.0), (1.2, 90.0, 110.0)]
    assert ax.get_title() == f"{BENCH} — throughput, 01\nError bars: 99.9% ledger confidence bounds"


def test_mean_of_reruns_values_from_prepare_alone(tmp_path):
    """Value check that does not depend on rendering: the mean of reruns, computed by hand.

    BASE 100..190 -> (100+110+...+190)/10 = 1450/10 = 145; 1a@1 150 x10 -> 150;
    1a@2 100 and 200 -> 150.  A max or median would give 190 / 150 / 200 for the first and last.
    """
    import warnings
    cfg = write_run(tmp_path, ledger_rows())
    config = load_config(cfg)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _, averages = prepare(load_ledger(config.ledger), config)
    got = {(r.strategy, r.threads_blobshuffle): r.score for r in averages.itertuples()}
    assert got == {("base", 1): 145.0, ("1a", 1): 150.0, ("1a", 2): 150.0}
    assert averages.sort_values(["strategy", "threads_blobshuffle"]).n_reruns.tolist() == [10, 2, 10]
