"""Coverage audit, compare: whole-table shape and literal cells through the CLI main.

Ledger (mb1_rawNoFinalize, reruns 01..03, F = 2 thread counts so alpha = 0.025: levels 0.975 / 0.95):
  threads 1: 2a 110, 210, 330   2b 100, 200, 300   ratios 1.1, 1.05, 1.1
  threads 2: 2a 110, 220, 330   2b 100, 200, 300   ratios all 1.1 (no spread)
Rows are written rerun by rerun, per thread count 2a then 2b: rerun 01 -> lines 2..5, 02 -> 6..9, 03 -> 10..13.
"""
import json

import pytest

from audit_support import read_csv, run_main, write_run
from conftest import observation
from src.cli import compare_main


BENCH = "mb1_rawNoFinalize"
A = {1: [110, 210, 330], 2: [110, 220, 330]}
B = [100, 200, 300]
STAMPS = {  # (rerun, strategy) -> run_timestamp for threads 1: 2a first, 2b first, 2a first
    ("01", "2a"): "2026-01-01T10:00:00", ("01", "2b"): "2026-01-01T10:05:00",
    ("02", "2a"): "2026-01-01T10:10:00", ("02", "2b"): "2026-01-01T10:05:00",
    ("03", "2a"): "2026-01-01T10:00:00", ("03", "2b"): "2026-01-01T10:05:00",
}


def ledger_rows(drop=None):
    rows = []
    for i, rerun in enumerate(("01", "02", "03")):
        for threads in (1, 2):
            for strategy, scores in (("2a", A[threads]), ("2b", [None] + B)):
                if (strategy, threads, rerun) == drop:
                    continue
                score = A[threads][i] if strategy == "2a" else B[i]
                stamp = STAMPS[(rerun, strategy)] if threads == 1 else "2026-01-01T10:00:00"
                dirty = "" if (threads == 2 and rerun == "02" and strategy == "2a") else "false"
                host = "hostB" if (threads == 2 and rerun == "01" and strategy == "2b") else "hostA"
                rows.append(observation(strategy=strategy, rerun=rerun, threads=threads, score=score,
                                        run_timestamp=stamp, hostname=host, git_sha="abc", git_dirty=dirty,
                                        aux_recordBytes=5 * score, run_tag=f"{strategy}_n{threads:02d}_r{rerun}"))
    return rows


COLUMNS = ["benchmark_short", "threads_blobshuffle", "n_reruns", "mean_2a", "mean_2b", "ratio_of_means",
           "logt_ratio", "diff_level", "diff_low", "diff_high", "equiv_level", "equiv_low", "equiv_high",
           "smallest_provable_margin", "sd_log_ratio_pct", "reruns_2a_gt_2b", "margin", "verdict", "note",
           "flags", "boot95_low", "boot95_high", "ci_seed", "ci_resamples", "order_2a_first",
           "order_adjusted_ratio_geo", "contributing_reruns", "source_rows"]


@pytest.fixture
def run(tmp_path, capsys):
    cfg = write_run(tmp_path, ledger_rows(), margin=0.05)
    code, printed, warns, err = run_main(compare_main, cfg, capsys)
    assert code == 0, err
    (folder,) = (tmp_path / "out").iterdir()
    return {"dir": folder, "printed": printed, "warns": warns}


def test_shape_one_csv_no_plot(run):
    assert [p.name for p in run["dir"].iterdir()] == [f"compare_{BENCH}_avg.csv"]
    assert run["dir"].name.endswith("_compare")
    assert [p.split("/")[-1] for p in run["printed"]] == [f"compare_{BENCH}_avg.csv"]


def test_columns_rows_and_literal_cells_with_spread(run):
    table = read_csv(run["dir"] / f"compare_{BENCH}_avg.csv")
    assert list(table.columns) == COLUMNS
    assert table["threads_blobshuffle"].tolist() == ["1", "2"]  # ascending
    r = table.iloc[0]
    f = lambda c: float(r[c])
    assert (r["n_reruns"], f("mean_2b")) == ("3", 200.0)
    assert f("mean_2a") == pytest.approx(650 / 3)         # (110+210+330)/3
    assert f("ratio_of_means") == pytest.approx(1.0833333333, abs=1e-9)  # 650/3 / 200
    # y = ln(1.1), ln(1.05), ln(1.1): mean 0.0798035, sd 0.0268583; exp(mean) = 1.0830742
    assert f("logt_ratio") == pytest.approx(1.0830742, abs=1e-7)
    assert (f("diff_level"), f("equiv_level")) == (0.975, 0.95)  # 1 - 0.05/2 and 1 - 2 * 0.05/2
    # t(0.9875, 2) = 6.205347 and t(0.975, 2) = 4.302653 (exact df-2 form), se = 0.0268583 / sqrt(3)
    assert (f("diff_low"), f("diff_high")) == pytest.approx((0.9837133, 1.1924712), abs=1e-6)
    assert (f("equiv_low"), f("equiv_high")) == pytest.approx((1.0131696, 1.1578019), abs=1e-6)
    assert f("smallest_provable_margin") == pytest.approx(0.1578019, abs=1e-6)  # exp(|0.0798035| + 4.302653 se) - 1
    assert f("sd_log_ratio_pct") == pytest.approx(2.6858344, abs=1e-6)
    assert r["reruns_2a_gt_2b"] == "3"
    assert f("margin") == 0.05
    assert (r["verdict"], r["note"]) == ("inconclusive", "")  # diff low 0.9837 < 1, equiv high 1.158 > 1.05
    assert r["flags"] == ""
    # bootstrap of sum(2a)/sum(2b) over reruns; resampled ratios only reach 1.05 to 1.1 here (computed apart)
    assert (f("boot95_low"), f("boot95_high")) == (1.05, 1.1)
    assert r["ci_seed"] == "3101053873585241809" and r["ci_resamples"] == "10000"
    assert r["order_2a_first"] == "2"
    assert f("order_adjusted_ratio_geo") == pytest.approx(1.0747093, abs=1e-6)
    assert json.loads(r["contributing_reruns"]) == ["01", "02", "03"]
    assert json.loads(r["source_rows"]) == [2, 3, 6, 7, 10, 11]


def test_no_spread_row_cells_flags_and_warnings(run):
    r = read_csv(run["dir"] / f"compare_{BENCH}_avg.csv").iloc[1]
    assert (r["verdict"], r["note"]) == ("no spread observed", "")
    assert (float(r["mean_2a"]), float(r["mean_2b"]), float(r["ratio_of_means"])) == (
        pytest.approx(220.0), 200.0, pytest.approx(1.1))
    assert float(r["logt_ratio"]) == pytest.approx(1.1)
    assert [r[c] for c in ("diff_low", "diff_high", "equiv_low", "equiv_high", "smallest_provable_margin",
                           "boot95_low", "boot95_high", "ci_seed", "ci_resamples")] == [""] * 9
    assert float(r["margin"]) == 0.05  # still holds the configured setting
    assert r["reruns_2a_gt_2b"] == "3"
    assert r["flags"] == "host_differs;dirty_build;order_unknown"  # blank git_dirty counts; tied timestamps
    assert r["order_2a_first"] == "0" and r["order_adjusted_ratio_geo"] == ""
    assert json.loads(r["source_rows"]) == [4, 5, 8, 9, 12, 13]
    assert f"No spread observed for {BENCH}/threads=2: no intervals" in run["warns"]
    assert f"{BENCH}: 3 reruns instead of 10" in run["warns"]


def test_missing_partner_fails_with_readable_error_and_writes_nothing(tmp_path, capsys):
    cfg = write_run(tmp_path, ledger_rows(drop=("2b", 2, "03")), margin=0.05)
    code, printed, _, err = run_main(compare_main, cfg, capsys)
    assert code == 1 and printed == []
    # 2a of rerun 03 at 2 threads is on ledger line 12 and has run_tag 2a_n02_r03 (rerun is spelled 03)
    assert err.strip() == f"compare: {BENCH}/2a_n02_r03/thrpt missing 2b partner (ledger row 12)."
    assert not (tmp_path / "out").exists()


def test_no_margin_warns_and_never_says_equivalent(tmp_path, capsys):
    cfg = write_run(tmp_path, ledger_rows())
    code, _, warns, err = run_main(compare_main, cfg, capsys)
    assert code == 0, err
    (folder,) = (tmp_path / "out").iterdir()
    table = read_csv(folder / f"compare_{BENCH}_avg.csv")
    assert "No comparison margin set: equivalence cannot be claimed" in warns
    assert table["margin"].tolist() == ["", ""]  # blank = none configured
    assert table["verdict"].tolist() == ["inconclusive", "no spread observed"]
