"""Coverage audit, speedup: whole-table shapes and literal cells through the CLI main.

Ledger (mb1_rawNoFinalize, reruns 01..10, rows written in reverse rerun order):
  base@1 100,104,96,103,98,101,99,103,97,99 (rerun 01..10)   mean 100
  1a@1   200,210,190,205,195,202,198,208,192,200             mean 200 -> speedup 2.0 (README Fieller case)
  1a@2   rerun 01: 150, rerun 02: 300
Ledger lines (header 1): rerun 10 -> base 2, 1a@1 3; ... rerun 3 -> 16, 17; rerun 2 -> 18, 19, 20;
rerun 1 -> 21, 22, 23.
"""
import json
import warnings

import pytest

from audit_support import read_csv, run_main, write_run
from conftest import observation
from src.cli import speedup_main
from src.config import load_config
from src.speedup import prepare, render
from src.utils import load_ledger


BENCH = "mb1_rawNoFinalize"
BASE = [100, 104, 96, 103, 98, 101, 99, 103, 97, 99]
ONE_A = [200, 210, 190, 205, 195, 202, 198, 208, 192, 200]


def ledger_rows():
    rows = []
    for rerun in range(10, 0, -1):
        name = f"{rerun:02d}"
        rows.append(observation(strategy="base", rerun=name, score=BASE[rerun - 1]))
        rows.append(observation(strategy="1a", rerun=name, score=ONE_A[rerun - 1]))
        if rerun == 2:
            rows.append(observation(strategy="1a", rerun=name, threads=2, score=300))
        if rerun == 1:
            rows.append(observation(strategy="1a", rerun=name, threads=2, score=150))
    return rows


@pytest.fixture
def run(tmp_path, capsys):
    cfg = write_run(tmp_path, ledger_rows())
    code, printed, warns, err = run_main(speedup_main, cfg, capsys)
    assert code == 0, err
    (folder,) = (tmp_path / "out").iterdir()
    return {"dir": folder, "printed": printed, "warns": warns, "cfg": cfg}


def test_shape_files(run):
    expected = sorted(f"speedup_{BENCH}_{part}.{ext}" for part in
                      [f"r{n:02d}" for n in range(1, 11)] + ["avg"] for ext in ("csv", "png"))
    assert sorted(p.name for p in run["dir"].iterdir()) == expected
    assert run["dir"].name.endswith("_speedup")


AVG_COLUMNS = ["benchmark_short", "strategy", "threads_blobshuffle", "mean_score", "mean_baseline_score",
               "speedup", "ci_low", "ci_high", "t_low", "t_high", "fieller_low", "fieller_high",
               "ci_method", "ci_resamples", "ci_seed", "t_basis", "baseline_strategy", "baseline_threads",
               "summary_kind", "n_reruns", "total_reruns", "contributing_reruns", "source_rows",
               "baseline_source_rows", "source_files", "baseline_source_files", "original_strategies"]


def test_avg_csv_shape_and_literal_cells(run):
    table = read_csv(run["dir"] / f"speedup_{BENCH}_avg.csv")
    assert list(table.columns) == AVG_COLUMNS
    assert table[["strategy", "threads_blobshuffle"]].values.tolist() == [["base", "1"], ["1a", "1"], ["1a", "2"]]
    base, one, two = (table.iloc[i] for i in range(3))
    # base at 1: ratio 1, no interval, no warning for it
    assert (float(base["speedup"]), float(base["mean_score"]), float(base["mean_baseline_score"])) == (1.0, 100.0, 100.0)
    assert [base[c] for c in ("ci_low", "ci_high", "t_low", "t_high", "fieller_low", "fieller_high",
                              "ci_method", "ci_seed")] == [""] * 8
    assert not any("base/threads=1" in w for w in run["warns"])
    # 1a@1: 2000 / 1000 reruns -> mean 200 / mean 100 = 2.0
    assert (float(one["mean_score"]), float(one["mean_baseline_score"]), float(one["speedup"])) == (200.0, 100.0, 2.0)
    # Fieller: roots of (200 - th*100)^2 = t^2/10 (vss - 2 th vsb + th^2 vbb), t = 2.262157 (README)
    assert float(one["fieller_low"]) == pytest.approx(1.988670, abs=1e-6)
    assert float(one["fieller_high"]) == pytest.approx(2.011045, abs=1e-6)
    # t interval on the ten per-rerun ratios (mean 1.99984...), computed apart
    assert (float(one["t_low"]), float(one["t_high"])) == pytest.approx((1.988534, 2.010963), abs=1e-6)
    # bootstrap of ratio-of-sums, seed sha256("42|mb1_rawNoFinalize|1a|1|speedup"), computed apart
    assert (float(one["ci_low"]), float(one["ci_high"])) == pytest.approx((1.9908998988877655, 2.009), abs=1e-12)
    assert one["ci_seed"] == "8338262694947842388" and one["ci_resamples"] == "10000"
    assert one["t_basis"] == "per-rerun ratios"
    assert one["summary_kind"] == "ratio of mean scores across reruns"
    assert (one["baseline_strategy"], one["baseline_threads"]) == ("base", "1")
    assert json.loads(one["source_rows"]) == [3, 5, 7, 9, 11, 13, 15, 17, 19, 22]
    assert json.loads(one["baseline_source_rows"]) == [2, 4, 6, 8, 10, 12, 14, 16, 18, 21]
    # 1a@2: reruns 01 (150) and 02 (300) only; base mean of just those two reruns = (100+104)/2 = 102
    assert (float(two["mean_score"]), float(two["mean_baseline_score"])) == (225.0, 102.0)
    assert float(two["speedup"]) == pytest.approx(225 / 102, abs=1e-12)  # 2.2058823...
    assert (two["n_reruns"], two["total_reruns"]) == ("2", "10")
    assert json.loads(two["contributing_reruns"]) == ["02", "01"]
    assert json.loads(two["source_rows"]) == [20, 23]
    assert json.loads(two["baseline_source_rows"]) == [18, 21]
    # Fieller at n=2 (t = 12.7062): [-9.505004, 9.245295]; the true negative bound is kept in the CSV
    assert float(two["fieller_low"]) == pytest.approx(-9.505004, abs=1e-5)
    assert float(two["fieller_high"]) == pytest.approx(9.245295, abs=1e-5)
    assert (float(two["ci_low"]), float(two["ci_high"])) == pytest.approx((1.5, 2.8846153846), abs=1e-9)
    assert (float(two["t_low"]), float(two["t_high"])) == pytest.approx((-6.604296, 10.988911), abs=1e-5)


def test_per_rerun_csv_cells_and_no_intervals(run):
    table = read_csv(run["dir"] / f"speedup_{BENCH}_r01.csv")
    assert table[["strategy", "threads_blobshuffle"]].values.tolist() == [["base", "1"], ["1a", "1"], ["1a", "2"]]
    # rerun 01: base 100; 1a@1 200/100 = 2; 1a@2 150/100 = 1.5
    assert [float(x) for x in table["speedup"]] == [1.0, 2.0, 1.5]
    assert [float(x) for x in table["baseline_score"]] == [100.0] * 3
    assert table["baseline_source_row"].tolist() == ["21"] * 3
    assert table["source_row"].tolist() == ["21", "22", "23"]
    assert table["summary_kind"].tolist() == ["individual paired ratio"] * 3
    assert not [c for c in table.columns if "ci_" in c or "fieller" in c or c.startswith("t_")]
    assert "score_confidence_low" not in table.columns  # ledger bounds never contribute


def _fig(run, which):
    config = load_config(run["cfg"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        observations, averages = prepare(load_ledger(config.ledger), config)
    table = averages if which == "avg" else observations[observations["rerun"].eq(which)]
    return render(table, config, f"{BENCH} — speedup, {which}", strategies=["base", "1a"], threads=[1, 2])


def test_avg_plot_shape(run):
    ax = _fig(run, "avg").axes[0]
    lines = ax.get_lines()
    # first the dashed ideal y=N, then the horizontal y=1, then one line per strategy
    assert [list(l.get_ydata()) for l in lines[:2]] == [[1, 2], [1, 1]]
    assert lines[0].get_linestyle() == "--"
    ys = [list(l.get_ydata()) for l in lines[2:4]]
    assert ys[0][0] == 1.0 and ys[0][1] != ys[0][1]  # base: gap (NaN) at 2 threads
    assert ys[1] == pytest.approx([2.0, 225 / 102])
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["Ideal (y=N)", "Base N=1", "Base", "S1_A"]
    spans = sorted((round(s[0][0], 6), round(s[0][1], 4), round(s[1][1], 4))
                   for c in ax.collections for s in c.get_segments() if s[0][0] == s[1][0] and s[0][1] != s[1][1])
    assert spans == [(1.0, 1.9887, 2.011), (2.0, -9.505, 9.2453)]  # Fieller extent; none for base
    assert ax.get_ylim()[0] == 0
    assert [t.get_text() for t in ax.get_xticklabels()] == ["1", "2"]
    assert ax.get_ylabel() == "Speedup vs. Base N=1 (dimensionless)"
    assert ax.get_title() == f"{BENCH} — speedup, avg\nError bars: 95% paired Fieller interval of reruns"


def test_per_rerun_plot_has_no_error_bars_or_second_title_line(run):
    ax = _fig(run, "01").axes[0]
    assert ax.get_title() == f"{BENCH} — speedup, 01"
    assert not [c for c in ax.collections if len(c.get_segments())]
    assert [list(l.get_ydata()) for l in ax.get_lines()[2:]] == [[1.0, pytest.approx(float("nan"), nan_ok=True)], [2.0, 1.5]]


def test_unbounded_fieller_blank_columns_warning_no_title_claim(tmp_path, capsys):
    # base 100,200,300 and 1a 200,300,600: base mean 200 is within t of zero (complex roots), so no Fieller.
    rows = []
    for rerun, (b, s) in enumerate(zip([100, 200, 300], [200, 300, 600]), 1):
        rows += [observation(strategy="base", rerun=f"0{rerun}", score=b),
                 observation(strategy="1a", rerun=f"0{rerun}", score=s)]
    cfg = write_run(tmp_path, rows)
    code, _, warns, err = run_main(speedup_main, cfg, capsys)
    assert code == 0, err
    (folder,) = (tmp_path / "out").iterdir()
    row = read_csv(folder / f"speedup_{BENCH}_avg.csv").iloc[1]
    assert row["strategy"] == "1a"
    assert float(row["speedup"]) == pytest.approx(1100 / 600)  # mean 366.67 / mean 200
    assert row["fieller_low"] == "" and row["fieller_high"] == ""
    assert (float(row["ci_low"]), float(row["ci_high"])) == (1.5, 2.0)
    assert (float(row["t_low"]), float(row["t_high"])) == pytest.approx((1.116225, 2.550442), abs=1e-5)
    assert any(w.startswith(f"No Fieller interval for {BENCH}/1a/threads=1") for w in warns)
    config = load_config(cfg)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _, averages = prepare(load_ledger(config.ledger), config)
    ax = render(averages, config, "t", strategies=["base", "1a"], threads=[1]).axes[0]
    assert "Error bars" not in ax.get_title()
    assert not [c for c in ax.collections if len(c.get_segments())]
