"""Coverage audit, gc: file set, table shapes, literal cells and plot shapes through the CLI main.

Ledger (mb1_rawNoFinalize, reruns 03, 01, 02 written in that order; defaults from conftest:
forks 5, iterations 5, 10 s -> gc_time_pct = gc_time / 250000 * 100):
  base@1  rerun 01/02/03: score 100/200/300, alloc 1000/2000/3000, gc_count 10/20/30,
          gc_time 2500/5000/7500 (pct 1/2/3), pauses_max 5/10/20, p99 2/4/6,
          aux_recordBytes 1677.7216/838.8608/2516.5824 with 0.0001 batches -> fill 1/0.5/1.5
  1a@1    defaults in all three reruns (constant)
  1a@2    rerun 01: alloc 500, count 4, gc_time 2500, pauses_max 8, p99 3
          rerun 02: alloc 700, count 6, gc_time 5000, pauses_max 0, p99 5      (rerun 03 absent)
Ledger lines: rerun 03 -> base 2, 1a@1 3; rerun 01 -> 4, 5, 6 (1a@2); rerun 02 -> 7, 8, 9 (1a@2).
"""
import json
import warnings

import pytest

from audit_support import read_csv, run_main, write_run
from conftest import gc_observation
from src.cli import gc_main
from src.config import load_config
from src.gc_metrics import pause_medians, prepare, render_alloc, render_pauses
from src.utils import load_ledger


BENCH = "mb1_rawNoFinalize"
BASE = {  # rerun -> (score, alloc, count, gc_time, pauses_max, p99, recordBytes)
    "01": (100, 1000, 10, 2500, 5, 2, "1677.7216"),
    "02": (200, 2000, 20, 5000, 10, 4, "838.8608"),
    "03": (300, 3000, 30, 7500, 20, 6, "2516.5824"),
}
ONE_A_2 = {"01": (500, 4, 2500, 8, 3), "02": (700, 6, 5000, 0, 5)}


def ledger_rows():
    rows = []
    for rerun in ("03", "01", "02"):
        score, alloc, count, gc_time, pmax, p99, record = BASE[rerun]
        rows.append(gc_observation(strategy="base", rerun=rerun, score=score, gc_alloc_rate_norm=alloc,
                                   gc_count=count, gc_time=gc_time, pauses_max=pmax, pauses_p99=p99,
                                   aux_recordBytes=record))
        rows.append(gc_observation(strategy="1a", rerun=rerun))
        if rerun in ONE_A_2:
            alloc, count, gc_time, pmax, p99 = ONE_A_2[rerun]
            rows.append(gc_observation(strategy="1a", rerun=rerun, threads=2, gc_alloc_rate_norm=alloc,
                                       gc_count=count, gc_time=gc_time, pauses_max=pmax, pauses_p99=p99))
    return rows


@pytest.fixture
def run(tmp_path, capsys):
    cfg = write_run(tmp_path, ledger_rows())
    code, printed, warns, err = run_main(gc_main, cfg, capsys)
    assert code == 0, err
    (folder,) = (tmp_path / "out").iterdir()
    return {"dir": folder, "printed": printed, "warns": warns, "cfg": cfg}


METRICS = ["gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct", "pauses_max", "pauses_p99", "batch_fill"]


def test_shape_files_and_folder_name(run):
    expected = sorted([f"gc_{BENCH}_r_all.csv", f"gc_{BENCH}_avg.csv", f"gc_{BENCH}_pauses_max_median.csv",
                       f"gc_{BENCH}_pauses_max.png", f"gc_{BENCH}_gc_alloc_rate_norm.png"]
                      + [f"gc_{BENCH}_{m}_pivot.csv" for m in METRICS])
    assert sorted(p.name for p in run["dir"].iterdir()) == expected
    assert len(run["printed"]) == 12
    assert run["dir"].name.endswith("_gc_pauses")


def test_r_all_csv_columns_and_cells(run):
    table = read_csv(run["dir"] / f"gc_{BENCH}_r_all.csv")
    assert list(table.columns)[:29] == [
        "benchmark_short", "strategy", "strategy_original", "threads_blobshuffle", "rerun", "score",
        "score_unit", "gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct", "forks",
        "measurement_iterations", "measurement_time", "measurement_seconds", "pauses_max", "pauses_p99",
        "aux_recordBytes", "aux_recordBytes_unit", "aux_batches", "aux_batches_unit",
        "cfg_batchDesiredBytes", "batch_bytes", "batch_fill", "n_reruns", "total_reruns",
        "contributing_reruns", "source_row", "source_file"]
    assert not {"score_error", "score_confidence_low", "score_confidence_high"} & set(table.columns)
    # sorted by configured order, threads, rerun (ledger was 03, 01, 02)
    assert table[["strategy", "threads_blobshuffle", "rerun"]].values.tolist() == [
        ["base", "1", "01"], ["base", "1", "02"], ["base", "1", "03"],
        ["1a", "1", "01"], ["1a", "1", "02"], ["1a", "1", "03"],
        ["1a", "2", "01"], ["1a", "2", "02"]]
    base = table.iloc[:3]
    assert [float(x) for x in base["gc_time_pct"]] == pytest.approx([1.0, 2.0, 3.0])  # t/250000*100
    assert [float(x) for x in base["measurement_seconds"]] == [10.0] * 3
    assert [float(x) for x in base["batch_bytes"]] == pytest.approx([16777216, 8388608, 25165824])  # rb/0.0001
    assert [float(x) for x in base["batch_fill"]] == pytest.approx([1.0, 0.5, 1.5])  # bytes / 16777216
    assert base["source_row"].tolist() == ["4", "7", "2"]
    assert table["source_row"].tolist() == ["4", "7", "2", "5", "8", "3", "6", "9"]


AVG_COLUMNS = (["benchmark_short", "strategy", "threads_blobshuffle", "score", "gc_alloc_rate_norm",
                "gc_count", "gc_time", "gc_time_pct", "pauses_max", "pauses_p99", "batch_bytes",
                "batch_fill", "n_reruns", "total_reruns", "contributing_reruns", "source_rows",
                "source_files", "original_strategies", "summary_kind", "gc_alloc_rate_norm_ci_low",
                "gc_alloc_rate_norm_ci_high"]
               + [f"{m}_{s}" for m in ("score", "gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct",
                                       "pauses_max", "pauses_p99", "batch_bytes", "batch_fill")
                  for s in ("t_low", "t_high")])


def test_avg_csv_columns_and_cells(run):
    table = read_csv(run["dir"] / f"gc_{BENCH}_avg.csv")
    assert list(table.columns) == AVG_COLUMNS
    assert table[["strategy", "threads_blobshuffle"]].values.tolist() == [["base", "1"], ["1a", "1"], ["1a", "2"]]
    base, flat, two = (table.iloc[i] for i in range(3))
    f = lambda row, col: float(row[col])
    assert [f(base, c) for c in ("score", "gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct")] == [
        200.0, 2000.0, 20.0, 5000.0, 2.0]
    assert f(base, "pauses_max") == pytest.approx(35 / 3)       # mean of per-rerun maxima, not a maximum
    assert f(base, "pauses_p99") == 4.0
    assert f(base, "batch_fill") == 1.0 and f(base, "batch_bytes") == pytest.approx(16777216)
    # t interval, t(2) = 4.302653: alloc 2000 -/+ 4.302653 * 1000 / sqrt(3)
    assert (f(base, "gc_alloc_rate_norm_t_low"), f(base, "gc_alloc_rate_norm_t_high")) == pytest.approx(
        (-484.137712, 4484.137712), abs=1e-5)
    assert (f(base, "gc_count_t_low"), f(base, "gc_count_t_high")) == pytest.approx((-4.841377, 44.841377), abs=1e-5)
    assert (f(base, "gc_time_pct_t_low"), f(base, "gc_time_pct_t_high")) == pytest.approx((-0.484138, 4.484138), abs=1e-5)
    assert (f(base, "pauses_max_t_low"), f(base, "pauses_max_t_high")) == pytest.approx((-7.306249, 30.639582), abs=1e-5)
    assert (f(base, "pauses_p99_t_low"), f(base, "pauses_p99_t_high")) == pytest.approx((-0.968275, 8.968275), abs=1e-5)
    assert (f(base, "batch_fill_t_low"), f(base, "batch_fill_t_high")) == pytest.approx((-0.242069, 2.242069), abs=1e-5)
    assert (f(base, "score_t_low"), f(base, "score_t_high")) == pytest.approx((-48.413771, 448.413771), abs=1e-5)
    # resampled CI of alloc [1000, 2000, 3000], seed sha256("42|mb1_rawNoFinalize|base|1|gc_alloc_rate_norm")
    assert (f(base, "gc_alloc_rate_norm_ci_low"), f(base, "gc_alloc_rate_norm_ci_high")) == (1000.0, 3000.0)
    assert json.loads(base["contributing_reruns"]) == ["03", "01", "02"]  # ledger order
    assert json.loads(base["source_rows"]) == [2, 4, 7]
    assert (base["n_reruns"], base["total_reruns"], base["summary_kind"]) == ("3", "3", "mean of reruns")
    # constant group: defaults 1000 / 10 / 2500 (pct 1.0), no spread -> every interval blank
    assert [f(flat, c) for c in ("gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct", "pauses_max",
                                 "pauses_p99", "batch_fill")] == [1000.0, 10.0, 2500.0, 1.0, 5.0, 2.0, 1.0]
    assert all(flat[c] == "" for c in AVG_COLUMNS[-18:] + ["gc_alloc_rate_norm_ci_low"])
    # 1a@2 over reruns 01, 02: alloc 600, count 5, gc_time 3750, pct (1+2)/2 = 1.5, pauses_max (8+0)/2 = 4 (zero kept in mean)
    assert [f(two, c) for c in ("gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct", "pauses_max",
                                "pauses_p99")] == [600.0, 5.0, 3750.0, 1.5, 4.0, 4.0]
    assert (two["n_reruns"], two["total_reruns"]) == ("2", "3")
    # t(1) = 12.706205: alloc 600 -/+ 12.706205 * 141.42136 / sqrt(2)
    assert (f(two, "gc_alloc_rate_norm_t_low"), f(two, "gc_alloc_rate_norm_t_high")) == pytest.approx(
        (-670.620474, 1870.620474), abs=1e-4)
    assert (f(two, "gc_alloc_rate_norm_ci_low"), f(two, "gc_alloc_rate_norm_ci_high")) == (500.0, 700.0)
    assert two["batch_fill_t_low"] == ""  # fill 1 and 1: no spread


def test_pivot_csvs_literal_text(run):
    text = lambda m: (run["dir"] / f"gc_{BENCH}_{m}_pivot.csv").read_text()
    # strategy rows in configured order, thread columns ascending, missing combination left blank
    assert text("gc_time") == "strategy,1,2\nbase,5000.0,\n1a,2500.0,3750.0\n"
    assert text("gc_time_pct") == "strategy,1,2\nbase,2.0,\n1a,1.0,1.5\n"
    assert text("gc_alloc_rate_norm") == "strategy,1,2\nbase,2000.0,\n1a,1000.0,600.0\n"
    assert text("gc_count") == "strategy,1,2\nbase,20.0,\n1a,10.0,5.0\n"
    assert text("pauses_p99") == "strategy,1,2\nbase,4.0,\n1a,2.0,4.0\n"
    assert text("batch_fill").splitlines()[0] == "strategy,1,2"
    assert text("batch_fill").splitlines()[2] == "1a,1.0,1.0"


def test_median_csv_and_warnings(run):
    table = read_csv(run["dir"] / f"gc_{BENCH}_pauses_max_median.csv")
    assert list(table.columns) == ["benchmark_short", "strategy", "threads_blobshuffle",
                                   "pauses_max_median", "n_reruns", "total_reruns", "contributing_reruns"]
    assert table[["strategy", "threads_blobshuffle", "pauses_max_median", "n_reruns", "total_reruns"]
                 ].values.tolist() == [["base", "1", "10.0", "3", "3"], ["1a", "1", "5.0", "3", "3"],
                                       ["1a", "2", "8.0", "1", "3"]]  # odd count, constant, zero omitted
    assert json.loads(table["contributing_reruns"].iloc[2]) == ["01"]
    assert any("Non-positive pauses_max omitted" in w and "[9]" in w for w in run["warns"])
    assert f"{BENCH}/1a_n02_r03/thrpt missing." in run["warns"]


def _prepared(run):
    config = load_config(run["cfg"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        observations, averages = prepare(load_ledger(config.ledger), config)
        return config, observations, averages, pause_medians(observations)


def test_pauses_plot_shape(run):
    config, obs, _, medians = _prepared(run)
    ax = render_pauses(obs, medians, config, "t", strategies=["base", "1a"], threads=[1, 2]).axes[0]
    scatters = [c for c in ax.collections if type(c).__name__ == "PathCollection"]
    dots = {name: sorted((round(x, 6), y) for x, y in c.get_offsets()) for c, name in zip(scatters, ("base", "1a"))}
    # thread 1 has two strategies (slots -0.2, +0.2); thread 2 only 1a, centred at 1.0; zero omitted
    assert dots["base"] == [(-0.2, 5.0), (-0.2, 10.0), (-0.2, 20.0)]
    assert dots["1a"] == [(0.2, 5.0), (0.2, 5.0), (0.2, 5.0), (1.0, 8.0)]
    segs = sorted((round(s[0][0], 6), round(s[1][0], 6), s[0][1]) for c in ax.collections
                  if type(c).__name__ == "LineCollection" for s in c.get_segments())
    assert segs == [(-0.4, 0.0, 10.0), (0.0, 0.4, 5.0), (0.8, 1.2, 8.0)]  # full slot = width 0.8/2 = 0.4 centred on the dot column
    assert ax.get_yscale() == "log"
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["Base", "S1_A", "median (per strategy and threads)"]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1", "2"]
    assert ax.get_ylabel() == "pauses_max (ms, log scale)"
    assert "e" not in "".join(t.get_text() for t in ax.get_yticklabels() if t.get_text())  # plain numbers


def test_alloc_plot_shape(run):
    config, _, averages, _ = _prepared(run)
    ax = render_alloc(averages, config, BENCH, strategies=["base", "1a"], threads=[1, 2]).axes[0]
    bars = ax.patches
    assert [round(b.get_x() + b.get_width() / 2, 6) for b in bars] == [-0.2, 0.2, 1.0]  # 1a@2 centred alone
    assert [b.get_height() for b in bars] == [2000.0, 1000.0, 600.0]
    spans = sorted((round(s[0][0], 6), round(s[0][1], 3), round(s[1][1], 3))
                   for c in ax.collections for s in c.get_segments() if s[0][0] == s[1][0] and s[0][1] != s[1][1])
    assert spans == [(-0.2, -484.138, 4484.138), (1.0, -670.62, 1870.62)]  # none for the constant group
    assert ax.get_yscale() == "linear" and ax.get_ylim()[0] == 0
    assert ax.get_title() == f"{BENCH} — gc_alloc_rate_norm, mean of reruns\nError bars: 95% t interval of reruns"
    assert ax.get_ylabel() == "gc_alloc_rate_norm (B/op)"
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["Base", "S1_A"]
