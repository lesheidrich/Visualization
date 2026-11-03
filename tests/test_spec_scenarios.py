"""One test per spec scenario that no other test checks in full.

Each test is named test_<spec>__<scenario>; the docstring quotes the WHEN and THEN it checks.
Expected values are literals with their arithmetic.
"""
from dataclasses import replace
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from audit_support import read_csv, run_main, write_run
from conftest import gc_observation, observation
from src import compare, gc_metrics, speedup, thrpt, utils
from src.cli import gc_main, main as thrpt_main, speedup_main
from src.config import load_config
from src.utils import load_ledger


BENCH = "mb1_rawNoFinalize"


def collect(function, *args, **kwargs):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = function(*args, **kwargs)
    return result, [str(w.message) for w in caught]


def frame(rows):
    return pd.DataFrame(rows)


def bar_spans(ax):
    return sorted((round(s[0][0], 4), round(s[0][1], 3), round(s[1][1], 3))
                  for c in ax.collections for s in c.get_segments()
                  if len(s) == 2 and s[0][0] == s[1][0] and s[0][1] != s[1][1])


# ============================================================ throughput-visualization

def test_thr__opaque_identifiers_each_get_their_own_output(config, tmp_path):
    """WHEN reruns are nonconsecutive numbers or strings THEN every identifier gets an output, kept distinct."""
    rows = [observation(rerun=r, score=100 + i) for i, r in enumerate(["7", "a", "x-3"])]
    pd.DataFrame(rows).to_csv(config.ledger, index=False)
    names = sorted(p.name for p in thrpt.run(config) if p.suffix == ".csv")
    assert names == [f"thrpt_{BENCH}_avg.csv", f"thrpt_{BENCH}_r7.csv", f"thrpt_{BENCH}_ra.csv",
                     f"thrpt_{BENCH}_rx-3.csv"]


def test_thr__all_observations_contribute_equally(config):
    """WHEN scores are 100, 200, 600 THEN aggregate 300 (1000/3 is 333.3 only if wrongly weighted) and 3 contributors."""
    (_, summary), _ = collect(thrpt.prepare, frame([observation(rerun=f"0{i}", score=s)
                                                    for i, s in enumerate([100, 200, 600], 1)]), config)
    row = summary.iloc[0]
    assert row.score == 300.0  # (100 + 200 + 600) / 3 = 300
    assert json.loads(row.contributing_reruns) == ["01", "02", "03"] and row.n_reruns == 3


def test_thr__duplicate_observations_fail(config):
    """WHEN two rows share benchmark, strategy, threads and rerun THEN the command fails, naming the rows."""
    with pytest.raises(ValueError, match=r"Duplicate throughput observations at ledger row\(s\) \[2, 3\]"):
        thrpt.prepare(frame([observation(), observation(score=101)]), config)


def test_thr__different_workload_settings_fail_naming_the_field(config):
    """WHEN record sizes differ within a benchmark THEN it fails with the differing field."""
    rows = [observation(cfg_valueBytes="100"), observation(rerun="02", cfg_valueBytes="200")]
    with pytest.raises(ValueError, match=f"Incompatible settings in {BENCH}: cfg_valueBytes"):
        thrpt.prepare(frame(rows), config)


def test_thr__varying_threads_jmh_does_not_fail(config):
    """WHEN rows differ only in the ledger column named threads_jmh THEN nothing fails."""
    rows = [observation(**{"threads_jmh": "1"}), observation(rerun="02", **{"threads_jmh": "4"})]
    (_, summary), _ = collect(thrpt.prepare, frame(rows), config)
    assert summary.n_reruns.tolist() == [2]


def test_thr__one_rerun_gives_count_one_and_no_aggregate_error_bars(config):
    """WHEN a benchmark has one rerun THEN individual and aggregate outputs exist, count 1, no aggregate error bars."""
    pd.DataFrame([observation(score=100), observation("1a", score=150)]).to_csv(config.ledger, index=False)
    paths, _ = collect(thrpt.run, config)
    assert sorted(p.name for p in paths if p.suffix == ".csv") == [f"thrpt_{BENCH}_avg.csv", f"thrpt_{BENCH}_r01.csv"]
    avg = read_csv(next(p for p in paths if p.name.endswith("_avg.csv")))
    assert avg.n_reruns.tolist() == ["1", "1"] and avg.total_reruns.tolist() == ["1", "1"]
    assert avg.t_low.tolist() == ["", ""] and avg.interval_kind.tolist() == ["none", "none"]
    (_, summary), _ = collect(thrpt.prepare, frame([observation(score=100), observation("1a", score=150)]), config)
    fig = thrpt.render(summary[summary.benchmark_short.eq(BENCH)], config, "t", strategies=["base", "1a"], threads=[1])
    assert bar_spans(fig.axes[0]) == []


def test_thr__missing_bounds_keep_the_bar_and_warn(config):
    """WHEN a valid score lacks a bound THEN its bar stays (no error bar) and a warning names the row."""
    rows = [observation(), observation("1a", score_confidence_high="")]
    (obs, _), messages = collect(thrpt.prepare, frame(rows), config)
    assert obs.score.tolist() == [100.0, 100.0]
    assert obs.interval_kind.tolist() == ["ledger confidence bounds", "none"]
    assert any("Missing ledger bounds at row(s) [3]" in m for m in messages)
    fig = thrpt.render(obs, config, "t", strategies=["base", "1a"], threads=[1])
    ax = fig.axes[0]
    assert len(ax.patches) == 2 and bar_spans(ax) == [(-0.2, 90.0, 110.0)]


@pytest.mark.parametrize("low,high", [("101", "110"), ("90", "99")])
def test_thr__invalid_bounds_fail_naming_the_row(config, low, high):
    """WHEN finite bounds violate lower <= score <= upper THEN validation fails with the row."""
    with pytest.raises(ValueError, match=r"Invalid confidence bounds at ledger row\(s\) \[2\]"):
        thrpt.prepare(frame([observation(score_confidence_low=low, score_confidence_high=high)]), config)


def test_thr__t_interval_of_100_to_190(config):
    """WHEN scores are 100, 110, ..., 190 THEN t interval is 145 -/+ 2.262157 * 30.276504 / sqrt(10) = [123.34, 166.66]."""
    (_, summary), _ = collect(thrpt.prepare, frame([observation(rerun=f"{i:02d}", score=100 + 10 * i)
                                                    for i in range(10)]), config)
    assert (summary.t_low[0], summary.t_high[0]) == pytest.approx((123.3415, 166.6585), abs=1e-4)


def test_thr__reproducible(config):
    """WHEN the same ledger is processed twice THEN CI values and seed are identical."""
    rows = frame([observation(rerun=f"{i:02d}", score=100 + 7 * i * i) for i in range(10)])
    first = collect(thrpt.prepare, rows, config)[0][1]
    second = collect(thrpt.prepare, rows, config)[0][1]
    pd.testing.assert_frame_equal(first, second)


def test_thr__identical_reruns_have_no_ci_no_bar_and_a_warning(config):
    """WHEN all scores are 500 THEN CI fields empty, no error bar, a warning names the group."""
    (_, summary), messages = collect(thrpt.prepare, frame([observation(rerun=f"0{i}", score=500) for i in (1, 2, 3)]), config)
    row = summary.iloc[0]
    assert all(pd.isna(row[c]) for c in ("ci_low", "ci_high", "t_low", "t_high", "ci_method")) and row.score == 500
    assert messages == [f"No CI for {BENCH}/base/threads=1: fewer than two reruns or no spread"]
    ax = thrpt.render(summary, config, "t", strategies=["base"], threads=[1]).axes[0]
    assert len(ax.patches) == 1 and bar_spans(ax) == []


def test_thr__single_rerun_interval_kind_none_and_title_claims_nothing(config):
    """WHEN a group has one rerun THEN interval_kind is none and the title has no Error bars line."""
    (_, summary), _ = collect(thrpt.prepare, frame([observation()]), config)
    assert summary.interval_kind.tolist() == ["none"]
    assert thrpt.render(summary, config, "T", strategies=["base"], threads=[1]).axes[0].get_title() == "T"


def test_thr__single_rerun_title_names_error_bars_only_if_another_row_has_a_ci(config):
    """WHEN one group has one rerun but another has a CI THEN the title names the t interval (another row has a CI)."""
    rows = [observation(rerun=f"0{i}", score=100 + 10 * i) for i in (1, 2, 3)] + [observation("1a", rerun="01")]
    (_, summary), _ = collect(thrpt.prepare, frame(rows), config)
    assert summary.interval_kind.tolist() == ["95% t interval of reruns", "none"]
    ax = thrpt.render(summary, config, "T", strategies=["base", "1a"], threads=[1]).axes[0]
    assert ax.get_title() == "T\nError bars: 95% t interval of reruns"


def test_thr__all_bounds_blank_title_has_no_error_bars_line(config):
    """WHEN every row of a rerun lacks a bound THEN that rerun's title has no Error bars line."""
    rows = [observation(score_confidence_low="", score_confidence_high=""),
            observation("1a", score_confidence_low="", score_confidence_high="")]
    (obs, _), _ = collect(thrpt.prepare, frame(rows), config)
    assert obs.interval_kind.tolist() == ["none", "none"]
    assert thrpt.render(obs, config, "T", strategies=["base", "1a"], threads=[1]).axes[0].get_title() == "T"


def test_thr__some_bounds_blank_title_names_the_ledger_bounds(config):
    """WHEN some rows have both bounds THEN the title names the (99.9%) ledger bounds."""
    rows = [observation(), observation("1a", score_confidence_low="")]
    (obs, _), _ = collect(thrpt.prepare, frame(rows), config)
    title = thrpt.render(obs, config, "T", strategies=["base", "1a"], threads=[1]).axes[0].get_title()
    assert title == "T\nError bars: 99.9% ledger confidence bounds"


# ============================================================ speedup-visualization

def sp_rows(base, strategy, threads=2):
    return [r for i, (b, s) in enumerate(zip(base, strategy), 1)
            for r in (observation(rerun=f"{i:02d}", score=b), observation("1a", f"{i:02d}", threads, s))]


def test_sp__shared_configuration_exit_zero_and_prints_paths(tmp_path, capsys):
    """WHEN speedup runs with a configuration THEN it exits zero and prints the paths it wrote."""
    cfg = write_run(tmp_path, sp_rows([100, 200], [200, 300]))
    code, printed, _, err = run_main(speedup_main, cfg, capsys)
    assert code == 0 and err == ""
    assert sorted(Path(p).name for p in printed) == sorted(
        f"speedup_{BENCH}_{part}.{ext}" for part in ("r01", "r02", "avg") for ext in ("csv", "png"))
    assert all(Path(p).is_file() for p in printed)


def test_sp__invalid_invocation_input_exits_nonzero_before_output(tmp_path, capsys):
    """WHEN the configuration has an unknown key THEN exit nonzero, message names the setting, no output dir."""
    cfg = write_run(tmp_path, sp_rows([100, 200], [200, 300]))
    cfg.write_text(cfg.read_text() + "aggregate: median\n")
    code, printed, _, err = run_main(speedup_main, cfg, capsys)
    assert code == 1 and printed == [] and err.startswith("speedup: Unknown config setting") and "aggregate" in err
    assert not (tmp_path / "out").exists()


def test_sp__baselines_vary_across_reruns(config):
    """WHEN base is 100 in rerun 01 and 200 in rerun 02 THEN each rerun is divided by its own base."""
    (obs, _), _ = collect(speedup.prepare, frame(sp_rows([100, 200], [200, 300])), config)
    s = obs[obs.strategy.eq("1a")].sort_values("rerun")
    assert s.speedup.tolist() == [2.0, 1.5]  # 200/100 and 300/200
    assert s.baseline_score.tolist() == [100.0, 200.0]


def test_sp__benchmark_isolation_and_units(config):
    """WHEN two benchmarks and ops/ms exist THEN each benchmark uses its own base, in ops/s."""
    other = "mb2_rawFinalize"
    rows = [observation(score=100), observation("1a", threads=2, score=0.4, score_unit="ops/ms"),   # 400 ops/s
            observation(score=50, benchmark_short=other), observation("1a", threads=2, score=200, benchmark_short=other)]
    cfg2 = replace(config, benchmarks=(BENCH, other))
    (obs, _), _ = collect(speedup.prepare, frame(rows), cfg2)
    got = {(r.benchmark_short, r.strategy): r.speedup for r in obs.itertuples()}
    assert got[(BENCH, "1a")] == 4.0       # 400 / 100
    assert got[(other, "1a")] == 4.0       # 200 / 50


@pytest.mark.parametrize("base_rows,match", [
    ([], "Speedup needs base at one thread in every rerun"),                       # missing
    ([observation(score=100), observation(score=100)], "Duplicate"),               # duplicate
    ([observation(score=0)], "Baseline score must be positive"),                   # zero
])
def test_sp__unusable_baseline_fails_before_output(config, base_rows, match):
    """WHEN base is missing, duplicated or zero in a rerun THEN speedup fails and writes nothing."""
    rows = base_rows + [observation("1a", threads=2, score=200)]
    pd.DataFrame(rows).to_csv(config.ledger, index=False)
    with pytest.raises(ValueError, match=match) as failure:
        speedup.run(config)
    assert BENCH in str(failure.value)  # names the affected benchmark
    assert not config.output.exists()


def test_sp__negative_baseline_fails(config):
    """WHEN base is negative THEN it fails (negative scores are refused with the row)."""
    with pytest.raises(ValueError, match=r"score must be nonnegative at ledger row\(s\) \[2\]"):
        speedup.prepare(frame([observation(score=-5), observation("1a", threads=2, score=200)]), config)


def test_sp__zero_numerator_is_a_zero_speedup(config):
    """WHEN a strategy score is 0 THEN its speedup is 0 and it stays present."""
    (obs, summary), _ = collect(speedup.prepare, frame(sp_rows([100, 100], [0, 0])), config)
    assert obs[obs.strategy.eq("1a")].speedup.tolist() == [0.0, 0.0]
    assert summary[summary.strategy.eq("1a")].speedup.tolist() == [0.0]


def test_sp__missing_strategy_observation_warns_and_leaves_no_zero(config):
    """WHEN 1a lacks rerun 02 THEN a coverage warning, n 1/2, base mean uses only rerun 01 (100, not 150)."""
    rows = [observation(rerun="01", score=100), observation(rerun="02", score=200), observation("1a", "01", 2, 300)]
    (_, summary), messages = collect(speedup.prepare, frame(rows), config)
    row = summary[summary.strategy.eq("1a")].iloc[0]
    assert (row.n_reruns, row.total_reruns, row.speedup) == (1, 2, 3.0)  # 300 / 100
    assert f"{BENCH}/1a_n02_r02/thrpt missing." in messages


def test_sp__missing_point_leaves_a_gap_and_unknown_strategy_is_kept(config):
    """WHEN 1a has no point at 4 threads and strategy 'future' is unknown THEN a gap (NaN) and a visible warning, id as label."""
    rows = [observation(rerun="01", score=100), observation("1a", "01", 2, 150), observation("1b", "01", 4, 150),
            observation("future", "01", 2, 120)]
    ((obs, summary), messages) = collect(speedup.prepare, frame(rows), config)
    assert any("Unknown strategies retained: ['future']" in m for m in messages)
    ax = speedup.render(summary, config, "t", strategies=["base", "1a", "1b", "future"], threads=[1, 2, 4]).axes[0]
    ys = {l.get_label(): list(l.get_ydata()) for l in ax.get_lines()}
    assert np.isnan(ys["S1_A"][2]) and ys["S1_A"][1] == 1.5
    assert "future" in ys and ys["future"][1] == 1.2


def test_sp__trace_a_paired_value(config):
    """WHEN a user reads a per-rerun row THEN numerator, baseline, rows, files and originals are present."""
    (obs, _), _ = collect(speedup.prepare, frame(sp_rows([100], [250])), config)
    r = obs[obs.strategy.eq("1a")].iloc[0]
    assert (r.score, r.baseline_score, r.speedup) == (250.0, 100.0, 2.5)
    assert (r.source_row, r.baseline_source_row) == (3, 2)
    assert (r.original_score, r.baseline_original_score) == ("250", "100")
    assert (r.baseline_strategy, r.baseline_threads) == ("base", 1)


def test_sp__ratio_of_means_not_mean_of_ratios(config):
    """WHEN base is [100, 200] and strategy [200, 300] THEN speedup 250/150 = 1.667, not (2 + 1.5)/2 = 1.75."""
    (_, summary), _ = collect(speedup.prepare, frame(sp_rows([100, 200], [200, 300])), config)
    row = summary[summary.strategy.eq("1a")].iloc[0]
    assert row.speedup == pytest.approx(5 / 3) and (row.mean_score, row.mean_baseline_score) == (250.0, 150.0)


def test_sp__variable_rerun_counts_use_the_contributing_base(config):
    """WHEN 1a has reruns 01,03 of 3 THEN base mean uses base 01,03 only: (100+300)/2 = 200, speedup 2."""
    rows = [observation(rerun=f"0{i}", score=b) for i, b in ((1, 100), (2, 200), (3, 300))] + \
           [observation("1a", "01", 2, 300), observation("1a", "03", 2, 500)]
    (_, summary), _ = collect(speedup.prepare, frame(rows), config)
    row = summary[summary.strategy.eq("1a")].iloc[0]
    assert (row.mean_baseline_score, row.mean_score, row.speedup) == (200.0, 400.0, 2.0)


@pytest.mark.parametrize("count", [1, 3, 10])
def test_sp__variable_rerun_counts_give_each_rerun_outputs(tmp_path, capsys, count):
    """WHEN a benchmark has one, three or ten reruns THEN each rerun gets its outputs and every observation contributes."""
    base = [100 + 5 * i for i in range(count)]
    cfg = write_run(tmp_path, sp_rows(base, [2 * b for b in base]))
    code, printed, _, err = run_main(speedup_main, cfg, capsys)
    assert code == 0, err
    assert sorted(Path(p).name for p in printed if p.endswith(".csv")) == sorted(
        [f"speedup_{BENCH}_r{i:02d}.csv" for i in range(1, count + 1)] + [f"speedup_{BENCH}_avg.csv"])
    avg = read_csv(next(Path(p) for p in printed if p.endswith("_avg.csv")))
    assert avg[avg.strategy == "1a"].n_reruns.tolist() == [str(count)]


def test_sp__pairing_is_kept_in_the_bootstrap(config):
    """WHEN base [100, 200], strategy [200, 300] THEN bootstrap [1.5, 2]: resampled ratios 2, 500/300 or 1.5 only."""
    (_, summary), _ = collect(speedup.prepare, frame(sp_rows([100, 200], [200, 300])), config)
    row = summary[summary.strategy.eq("1a")].iloc[0]
    assert (row.ci_low, row.ci_high) == (1.5, 2.0)


def test_sp__row_order_leaves_ci_values_and_pairing_unchanged(config):
    """WHEN ten-rerun rows are shuffled THEN CI/Fieller/t values are identical and each score keeps its base."""
    base = [100, 104, 96, 103, 98, 101, 99, 103, 97, 99]
    strategy = [200, 210, 190, 205, 195, 202, 198, 208, 192, 200]
    rows = sp_rows(base, strategy)
    ordered = collect(speedup.prepare, frame(rows), config)[0]
    shuffled = collect(speedup.prepare, frame(rows).sample(frac=1, random_state=5), config)[0]
    cols = ["ci_low", "ci_high", "t_low", "t_high", "fieller_low", "fieller_high", "speedup"]
    key = lambda t: t.sort_values(["strategy", "threads_blobshuffle"]).reset_index(drop=True)[cols]
    pd.testing.assert_frame_equal(key(ordered[1]), key(shuffled[1]))
    paired = shuffled[0][shuffled[0].strategy.eq("1a")].sort_values("rerun")
    assert paired.speedup.round(6).tolist() == [round(s / b, 6) for s, b in zip(strategy, base)]


def test_sp__unusable_ledger_bounds_are_ignored(config):
    """WHEN bound/error columns are malformed THEN speedup still runs, silently, with the same numbers."""
    rows = sp_rows([100, 200], [200, 300])
    for r in rows:
        r.update(score_confidence_low="oops", score_confidence_high="", score_error="NaN?")
    (_, summary), messages = collect(speedup.prepare, frame(rows), config)
    (_, clean), _ = collect(speedup.prepare, frame(sp_rows([100, 200], [200, 300])), config)
    got, want = summary[summary.strategy.eq("1a")].iloc[0], clean[clean.strategy.eq("1a")].iloc[0]
    assert got.speedup == pytest.approx(5 / 3)
    assert (got.ci_low, got.ci_high) == (want.ci_low, want.ci_high)
    assert not [m for m in messages if "ledger" in m.lower() or "confidence" in m.lower()]


def test_sp__missing_base_error_names_tag_and_a_row_of_that_rerun(config):
    """WHEN rerun 02 has no base THEN the error names mb1/base_n01_r02/thrpt and a ledger row of that rerun."""
    rows = [observation(rerun="01", score=100), observation("1a", "02", 2, 150)]
    with pytest.raises(ValueError, match=rf"{BENCH}/base_n01_r02/thrpt missing \(ledger row 3 is in that rerun\)"):
        speedup.prepare(frame(rows), config)


def test_sp__missing_rerun_in_a_group_warning_names_the_tag(config):
    """WHEN 1a@2 lacks rerun 02 THEN the warning reads <bench>/1a_n02_r02/thrpt missing."""
    rows = sp_rows([100, 200], [150, 300])[:-1]
    _, messages = collect(speedup.prepare, frame(rows), config)
    assert f"{BENCH}/1a_n02_r02/thrpt missing." in messages


# ============================================================ gc-metrics

def gc_prepare(rows, config):
    return collect(gc_metrics.prepare, frame(rows), config)


def test_gc__sample_rows_do_not_contribute(config):
    """WHEN sample and thrpt rows share a configuration THEN only thrpt contributes."""
    rows = [gc_observation(), gc_observation(mode="sample", gc_count="999")]
    (obs, summary), _ = gc_prepare(rows, config)
    assert len(obs) == 1 and obs.gc_count.tolist() == [10.0] and summary.n_reruns.tolist() == [1]


def test_gc__duplicate_observation_names_the_rows(config):
    """WHEN two thrpt rows share benchmark, strategy, threads, rerun THEN gc fails naming both rows."""
    with pytest.raises(ValueError, match=r"Duplicate throughput observations at ledger row\(s\) \[2, 3\]"):
        gc_prepare([gc_observation(), gc_observation()], config)


def test_gc__missing_column_is_named(config):
    """WHEN there is no pauses_max column THEN gc fails naming it."""
    row = gc_observation()
    del row["pauses_max"]
    with pytest.raises(ValueError, match=r"Missing ledger column\(s\): \['pauses_max'\]"):
        gc_prepare([row], config)


def test_gc__blank_value_stays_missing_with_a_warning_naming_the_row(config):
    """WHEN gc_count is blank THEN it is missing, other metrics kept, warning names the row."""
    (obs, _), messages = gc_prepare([gc_observation(), gc_observation(rerun="02", gc_count="")], config)
    row = obs.iloc[1]
    assert pd.isna(row.gc_count) and (row.gc_time, row.pauses_max, row.gc_alloc_rate_norm) == (2500.0, 5.0, 1000.0)
    assert "Blank gc_count at ledger row(s) [3]" in messages


def test_gc__blank_setting_fails_naming_row_and_column(config):
    """WHEN forks is blank THEN gc fails naming the row and forks."""
    with pytest.raises(ValueError, match=r"Blank forks at ledger row\(s\) \[2\]"):
        gc_prepare([gc_observation(forks="")], config)


def test_gc__blank_gc_time_leaves_pct_missing_and_warns_with_all_three_names(config):
    """WHEN gc_time is blank THEN gc_time_pct is missing; warning names row, gc_time, gc_time_pct."""
    (obs, _), messages = gc_prepare([gc_observation(gc_time="")], config)
    assert pd.isna(obs.gc_time_pct[0])
    assert "Blank gc_time at ledger row(s) [2]; gc_time_pct missing" in messages


def test_gc__current_runner_settings_pct_1(config):
    """WHEN gc_time 2500, 5 forks, 5 iterations, 10 s THEN pct = 2500 / (5*5*10*1000) * 100 = 1.0."""
    (obs, _), _ = gc_prepare([gc_observation()], config)
    assert obs.gc_time_pct[0] == pytest.approx(1.0)


def test_gc__millisecond_duration_pct_10(config):
    """WHEN 500 ms, 1 fork, 1 iteration, gc_time 50 THEN pct = 50 / (1*1*500) * 100 = 10.0."""
    (obs, _), _ = gc_prepare([gc_observation(gc_time="50", forks="1", measurement_iterations="1",
                                              measurement_time="500 ms")], config)
    assert obs.gc_time_pct[0] == pytest.approx(10.0)


def test_gc__mismatched_time_units_fail_naming_row_and_units(config):
    """WHEN bytes/ms meets batches/us THEN gc fails, naming the row and both units."""
    with pytest.raises(ValueError, match=r"Incompatible aux units at ledger row\(s\) \[2\]: \[\('bytes/ms', 'batches/us'\)\]"):
        gc_prepare([gc_observation(aux_batches_unit="batches/us")], config)


def test_gc__no_closed_batch_on_some_rows_names_the_rows(config):
    """WHEN aux_batches is 0 on some mb2 rows THEN both values are missing there and a warning names the rows."""
    mb2 = "mb2_rawFinalize"
    rows = [gc_observation(benchmark_short=mb2), gc_observation(benchmark_short=mb2, rerun="02", aux_batches="0")]
    (obs, _), messages = gc_prepare(rows, replace(config, benchmarks=(mb2,)))
    assert pd.isna(obs.batch_bytes[1]) and pd.isna(obs.batch_fill[1]) and obs.batch_fill[0] == pytest.approx(1.0)
    assert "Zero aux_batches at ledger row(s) [3]; batch_bytes and batch_fill missing" in messages


def test_gc__benchmark_closing_no_batch_gives_exactly_one_warning(config):
    """WHEN aux_batches is 0 on every mb2 row THEN both missing and exactly one warning naming the benchmark."""
    mb2 = "mb2_rawFinalize"
    rows = [gc_observation(benchmark_short=mb2, rerun=f"0{i}", aux_batches="0") for i in (1, 2, 3)]
    (obs, _), messages = gc_prepare(rows, replace(config, benchmarks=(mb2,)))
    assert obs.batch_bytes.isna().all() and obs.batch_fill.isna().all()
    assert [m for m in messages if "batch" in m] == [f"{mb2}: aux_batches is 0 on every row; batch_bytes and batch_fill are blank"]


def test_gc__no_finalization_gives_no_batch_warning(config):
    """WHEN aux_batches is 0 on every mb1_rawNoFinalize row THEN both missing and no batch warning."""
    rows = [gc_observation(rerun=f"0{i}", aux_batches="0") for i in (1, 2)]
    (obs, _), messages = gc_prepare(rows, config)
    assert obs.batch_fill.isna().all() and not [m for m in messages if "batch" in m]


def test_gc__pivot_layout_nine_strategies_five_thread_counts(config):
    """WHEN nine strategies and threads 1, 2, 4, 6, 8 THEN each pivot has 9 rows in configured order, 5 columns, blanks where absent."""
    order = ["base", "1a", "1b", "1c", "2null", "2a", "2b", "3", "4"]
    rows = [gc_observation(strategy=s, threads=n, gc_count=str(10 * (i + 1) + n))
            for i, s in enumerate(order) for n in (1, 2, 4, 6, 8) if not (s == "base" and n != 1)]
    (_, averages), _ = gc_prepare(rows, config)
    table = gc_metrics.pivots(averages, config)[BENCH]["gc_count"]
    assert table.index.tolist() == order and table.columns.tolist() == [1, 2, 4, 6, 8]
    assert table.loc["base"].tolist()[0] == 11.0 and table.loc["base"].isna().tolist() == [False, True, True, True, True]
    assert table.loc["4", 8] == 98.0  # 10 * 9 + 8
    assert table.shape == (9, 5)


def test_gc__trace_a_value_has_every_input(config):
    """WHEN a user reads a per-rerun row THEN identity, source and all gc_time_pct / batch_fill inputs are present."""
    (obs, _), _ = gc_prepare([gc_observation()], config)
    for column in ("benchmark_short", "strategy", "threads_blobshuffle", "rerun", "source_row",
                   "gc_time", "forks", "measurement_iterations", "measurement_time", "measurement_seconds",
                   "aux_recordBytes", "aux_batches", "aux_recordBytes_unit", "aux_batches_unit",
                   "cfg_batchDesiredBytes"):
        assert column in obs.columns and pd.notna(obs[column][0])


def _pause_fig(config, rows, strategies, threads):
    (obs, _), _ = gc_prepare(rows, config)
    medians = gc_metrics.pause_medians(obs)
    return gc_metrics.render_pauses(obs, medians, config, "t", strategies=strategies, threads=threads), medians


def test_gc__dots_per_rerun_at_heights_1_2_8(config):
    """WHEN a strategy at two threads has pauses_max 1, 2, 8 THEN three dots at those heights in the two-thread group."""
    rows = [gc_observation(strategy="base", rerun=f"0{i}", threads=2, pauses_max=str(v)) for i, v in enumerate([1, 2, 8], 1)]
    fig, _ = _pause_fig(config, rows, ["base"], [1, 2])
    (dots,) = [c for c in fig.axes[0].collections if type(c).__name__ == "PathCollection"]
    assert sorted((round(x, 6), y) for x, y in dots.get_offsets()) == [(1.0, 1.0), (1.0, 2.0), (1.0, 8.0)]


def test_gc__absent_combination_base_only_at_one_thread(config):
    """WHEN base exists only at 1 thread and eight others everywhere THEN no base dots/median elsewhere, others centred."""
    others = ["1a", "1b", "1c", "2null", "2a", "2b", "3", "4"]
    rows = [gc_observation(strategy="base", threads=1)] + [gc_observation(strategy=s, threads=n) for s in others for n in (1, 2)]
    fig, medians = _pause_fig(config, rows, ["base"] + others, [1, 2])
    ax = fig.axes[0]
    xs_by_label = {c.get_label(): sorted({round(x, 4) for x, _ in c.get_offsets()}) for c in ax.collections
                   if type(c).__name__ == "PathCollection"}
    width = 0.8 / 9
    assert len(xs_by_label["Base"]) == 1 and xs_by_label["Base"][0] == pytest.approx(0 + (0 - 4) * width, abs=1e-4)
    # at 2 threads eight strategies share slots centred on 1.0: first at 1 - 3.5 width, last at 1 + 3.5 width
    assert xs_by_label["S1_A"][1] == pytest.approx(1 - 3.5 * width, abs=1e-4)
    assert xs_by_label["S4"][1] == pytest.approx(1 + 3.5 * width, abs=1e-4)
    assert not ((medians.strategy == "base") & (medians.threads_blobshuffle == 2)).any()


def test_gc__readable_log_axis_2_to_4(config):
    """WHEN values range 2 to 4 ms THEN y tick labels are plain numbers such as 2, 3, 4."""
    rows = [gc_observation(rerun=f"0{i}", pauses_max=str(v)) for i, v in enumerate([2, 3, 4], 1)]
    fig, _ = _pause_fig(config, rows, ["base"], [1])
    fig.canvas.draw()
    labels = {t.get_text() for t in fig.axes[0].get_yticklabels(minor=True) + fig.axes[0].get_yticklabels() if t.get_text()}
    assert {"2", "3", "4"} <= labels and not any("e" in t or "^" in t or "$" in t for t in labels)


@pytest.mark.parametrize("values,median", [([1, 2, 8], 2.0), ([1, 2, 4, 8], 3.0)])  # (2+4)/2 = 3 for even
def test_gc__odd_and_even_count_medians(config, values, median):
    """WHEN a group's values are 1, 2, 8 (or 1, 2, 4, 8) THEN its median is 2 (or 3)."""
    rows = [gc_observation(rerun=f"0{i}", pauses_max=str(v)) for i, v in enumerate(values, 1)]
    (obs, _), _ = gc_prepare(rows, config)
    assert gc_metrics.pause_medians(obs).pauses_max_median.tolist() == [median]


def test_gc__median_export_equals_the_drawn_line_height(config):
    """WHEN the median CSV is read THEN each median equals the height of the drawn black line."""
    rows = [gc_observation(strategy=s, rerun=f"0{i}", pauses_max=str(v)) for s, base in (("base", 1), ("1a", 5))
            for i, v in enumerate([base, base * 2, base * 8], 1)]
    fig, medians = _pause_fig(config, rows, ["base", "1a"], [1])
    heights = sorted(s[0][1] for c in fig.axes[0].collections if type(c).__name__ == "LineCollection" for s in c.get_segments())
    assert heights == sorted(medians.pauses_max_median.tolist()) == [2.0, 10.0]


def test_gc__zero_pause_leaves_two_dots_and_median_of_those(config):
    """WHEN one of three reruns is 0 THEN two dots, median of those two, n_reruns 2, warning names the row."""
    rows = [gc_observation(rerun=f"0{i}", pauses_max=str(v)) for i, v in enumerate([0, 4, 8], 1)]
    (obs, _), _ = gc_prepare(rows, config)
    medians, messages = collect(gc_metrics.pause_medians, obs)
    assert medians.pauses_max_median.tolist() == [6.0] and medians.n_reruns.tolist() == [2]  # (4 + 8) / 2
    assert any("Non-positive pauses_max omitted from log plot at ledger row(s) [2]" in m for m in messages)
    fig = gc_metrics.render_pauses(obs, medians, config, "t", strategies=["base"], threads=[1])
    (dots,) = [c for c in fig.axes[0].collections if type(c).__name__ == "PathCollection"]
    assert sorted(y for _, y in dots.get_offsets()) == [4.0, 8.0]


def test_gc__configured_colour_dots_red_median_black_label_configured(config):
    """WHEN 2a's colour is red THEN its dots are red, median black, legend uses its label."""
    cfg = replace(config, colors={"2a": "red"})
    rows = [gc_observation(strategy="2a", rerun=f"0{i}", pauses_max=str(i)) for i in (1, 2)]
    fig, _ = _pause_fig(cfg, rows, ["2a"], [1])
    ax = fig.axes[0]
    (dots,) = [c for c in ax.collections if type(c).__name__ == "PathCollection"]
    assert tuple(dots.get_facecolor()[0][:3]) == (1.0, 0.0, 0.0)
    (lines,) = [c for c in ax.collections if type(c).__name__ == "LineCollection"]
    assert tuple(lines.get_color()[0][:3]) == (0.0, 0.0, 0.0)
    assert [t.get_text() for t in ax.get_legend().get_texts()][0] == "S2_A"


def test_gc__alloc_all_blank_plot_has_no_bars_no_legend_no_warning(config):
    """WHEN every gc_alloc_rate_norm is blank THEN the plot has no bars, no legend and no matplotlib warning."""
    rows = [gc_observation(rerun=f"0{i}", gc_alloc_rate_norm="") for i in (1, 2)]
    (_, averages), _ = gc_prepare(rows, config)
    fig, messages = collect(gc_metrics.render_alloc, averages, config, BENCH, strategies=["base"], threads=[1])
    assert len(fig.axes[0].patches) == 0 and fig.axes[0].get_legend() is None and messages == []


def test_gc__alloc_ci_row_order(config):
    """WHEN a ten-rerun group's rows are shuffled THEN ci_low/ci_high are identical."""
    rows = [gc_observation(rerun=f"{i:02d}", gc_alloc_rate_norm=str(1000 + 100 * i)) for i in range(10)]
    a = gc_prepare(rows, config)[0][1].iloc[0]
    b = collect(gc_metrics.prepare, frame(rows).sample(frac=1, random_state=2), config)[0][1].iloc[0]
    assert (a.gc_alloc_rate_norm_ci_low, a.gc_alloc_rate_norm_ci_high) == (b.gc_alloc_rate_norm_ci_low, b.gc_alloc_rate_norm_ci_high)


def test_gc__t_columns_for_gc_count_100_to_190(config):
    """WHEN gc_count is 100, 110, ..., 190 THEN gc_count_t_low/high are 145 -/+ 21.66 = 123.34 and 166.66."""
    rows = [gc_observation(rerun=f"{i:02d}", gc_count=str(100 + 10 * i)) for i in range(10)]
    row = gc_prepare(rows, config)[0][1].iloc[0]
    assert (row.gc_count_t_low, row.gc_count_t_high) == pytest.approx((123.3415, 166.6585), abs=1e-4)
    assert row.gc_count == 145.0


# ============================================================ merge-order-comparison

A = [101, 103, 98, 102, 100, 103, 98, 101, 102, 100]
B = [100, 102, 98, 101, 99, 103, 97, 100, 102, 98]


def cmp_config(config, margin=None):
    return replace(config, comparison_margin=margin)


def cmp_rows(a, b, threads=(1,), benchmark=BENCH):
    return [observation(strategy=s, rerun=f"{i:02d}", threads=n, score=v, benchmark_short=benchmark)
            for n in threads for s, vals in (("2a", a), ("2b", b)) for i, v in enumerate(vals)]


def test_cmp__rerun_missing_from_both_names_benchmark_threads_rerun(config):
    """WHEN rerun 03 exists at 1 thread but neither strategy has it at 2 THEN it fails naming benchmark, threads, rerun."""
    rows = cmp_rows(A[:3], B[:3], threads=(1, 2))
    rows = [r for r in rows if not (r["threads_blobshuffle"] == "2" and r["rerun"] == "02")]
    with pytest.raises(ValueError, match=rf"{BENCH}/2a_n02_r02/thrpt and 2b_n02_r02 are both missing"):
        compare.prepare(frame(rows), cmp_config(config, 0.05))


def test_cmp__five_thread_counts_levels_99_and_98_whatever_the_other_benchmark(config):
    """WHEN a benchmark has five thread counts THEN levels 0.99 / 0.98 although the other benchmark has two."""
    other = "mb2_rawFinalize"
    rows = cmp_rows(A, B, threads=(1, 2, 4, 6, 8)) + cmp_rows(A, B, threads=(1, 2), benchmark=other)
    tables, _ = collect(compare.prepare, frame(rows), replace(cmp_config(config, 0.05), benchmarks=(BENCH, other)))
    assert tables[BENCH].diff_level.tolist() == [pytest.approx(0.99)] * 5
    assert tables[BENCH].equiv_level.tolist() == [pytest.approx(0.98)] * 5
    assert tables[other].diff_level.tolist() == [pytest.approx(0.975)] * 2   # 1 - 0.05 / 2


def table_row(config, a=A, b=B, margin=None, threads=(1, 2, 4, 6, 8)):
    tables, messages = collect(compare.prepare, frame(cmp_rows(a, b, threads)), cmp_config(config, margin))
    return tables[BENCH].iloc[0], messages


def test_cmp__worked_example_cells(config):
    """WHEN the worked example runs with F = 5 THEN 1.008, 1.008034, [1.001464, 1.014647], [1.002328, 1.013773]."""
    r, _ = table_row(config)
    assert r.ratio_of_means == pytest.approx(1.008, abs=5e-7) and r.logt_ratio == pytest.approx(1.008034, abs=5e-7)
    assert (r.diff_low, r.diff_high) == pytest.approx((1.001464, 1.014647), abs=5e-7)
    assert (r.equiv_low, r.equiv_high) == pytest.approx((1.002328, 1.013773), abs=5e-7)


def test_cmp__equivalent_with_five_percent_margin_and_note(config):
    """WHEN margin 5% THEN verdict equivalent, note '2A 0.8% faster, below margin'."""
    r, _ = table_row(config, margin=0.05)
    assert (r.verdict, r.note) == ("equivalent", "2A 0.8% faster, below margin")


def test_cmp__faster_at_the_edge(config):
    """WHEN no margin and the edge data THEN 99% low is 1.000039 and verdict 2A faster."""
    r, _ = table_row(config, [105, 97, 103, 98, 97, 101, 98, 100, 97, 103], [104, 96, 102, 97, 96, 100, 98, 98, 96, 104])
    assert r.diff_low == pytest.approx(1.000039, abs=5e-7) and r.verdict == "2A faster"


def test_cmp__not_within_margin(config):
    """WHEN margin 2% and the far data THEN 98% high is 1.020070 and verdict is not equivalent."""
    r, _ = table_row(config, [99, 100, 104, 104, 100, 99, 99, 104, 100, 99], [100, 98, 104, 103, 101, 98, 96, 103, 98, 100], 0.02)
    assert r.equiv_high == pytest.approx(1.020070, abs=5e-7) and r.verdict != "equivalent"


def test_cmp__smallest_provable_margin_1_3773_percent(config):
    """WHEN the worked example is evaluated THEN the smallest provable margin is 1.3773% (equiv_high - 1)."""
    r, _ = table_row(config)
    assert r.smallest_provable_margin * 100 == pytest.approx(1.3773, abs=5e-5)


def test_cmp__reference_columns_sd_and_reruns_above(config):
    """WHEN the worked example is evaluated THEN SD of log ratios 0.636% and 7 reruns with 2a > 2b."""
    r, _ = table_row(config, margin=0.05)
    assert r.sd_log_ratio_pct == pytest.approx(0.636, abs=5e-4) and r.reruns_2a_gt_2b == 7


@pytest.mark.parametrize("dirty", ["", "true"])
def test_cmp__dirty_build_flag_leaves_numbers_unchanged(config, dirty):
    """WHEN a contributing row's git_dirty is blank or true THEN it carries dirty_build and numbers are unchanged."""
    clean = [dict(r, git_dirty="false") for r in cmp_rows(A, B, (1, 2))]
    dirty_rows = [dict(r) for r in clean]
    dirty_rows[0]["git_dirty"] = dirty
    base = collect(compare.prepare, frame(clean), cmp_config(config, 0.05))[0][BENCH].iloc[0]
    got = collect(compare.prepare, frame(dirty_rows), cmp_config(config, 0.05))[0][BENCH].iloc[0]
    assert "dirty_build" in got["flags"].split(";") and "dirty_build" not in base["flags"].split(";")
    numeric = ["mean_2a", "mean_2b", "logt_ratio", "diff_low", "diff_high", "equiv_low", "equiv_high"]
    assert got[numeric].tolist() == base[numeric].tolist()


def test_cmp__missing_pair_four_threads_names_benchmark_threads_rerun(config):
    """WHEN a rerun has 2a but not 2b at four threads THEN it fails naming benchmark, threads, rerun, before output."""
    rows = [r for r in cmp_rows(A[:3], B[:3], threads=(1, 4)) if not (r["strategy"] == "2b" and r["threads_blobshuffle"] == "4" and r["rerun"] == "02")]
    with pytest.raises(ValueError, match=rf"{BENCH}/2a_n04_r02/thrpt missing 2b partner"):
        compare.prepare(frame(rows), cmp_config(config, 0.05))


def test_cmp__partner_exists_names_row_and_tag(config):
    """WHEN 2a is missing at 4 threads in rerun 3 and 2b is on row N with run_tag 2b_n04_r3 THEN the error names row N and the tag."""
    rows = [observation(strategy=s, rerun=r, threads=4, score=100 + int(r), run_tag=f"{s}_n04_r{r}")
            for r in ("1", "2", "3") for s in ("2a", "2b") if not (s == "2a" and r == "3")]
    # ledger lines: r1 -> 2, 3; r2 -> 4, 5; r3 (2b only) -> 6
    with pytest.raises(ValueError, match=rf"{BENCH}/2b_n04_r3/thrpt missing 2a partner \(ledger row 6\)\."):
        compare.prepare(frame(rows), cmp_config(config, 0.05))


# ============================================================ visualization-cli

import os
import subprocess
import sys

from matplotlib.collections import LineCollection


def test_cli__run_from_another_directory_resolves_paths_against_the_config(tmp_path):
    """WHEN the installed command runs from another cwd with an explicit config THEN input/output resolve beside the config."""
    project, elsewhere = tmp_path / "project", tmp_path / "elsewhere"
    project.mkdir(), elsewhere.mkdir()
    cfg = write_run(project, [observation(rerun=f"0{i}", score=100 + 10 * i) for i in (1, 2, 3)])
    result = subprocess.run([str(Path(sys.executable).with_name("thrpt")), "--config", str(cfg)], cwd=elsewhere,
                            capture_output=True, text=True, env={**os.environ, "MPLBACKEND": "Agg"})
    assert result.returncode == 0, result.stderr
    (folder,) = (project / "out").iterdir()
    assert sorted(result.stdout.split()) == sorted(str(p) for p in folder.iterdir())
    assert list(elsewhere.iterdir()) == []


def test_cli__identifiers_are_text_and_strategy_comes_from_the_unnamed_column(tmp_path):
    """WHEN rerun ids are 01, 10, instance-A and strategy is the unnamed first column THEN they are read verbatim."""
    rows = [observation(rerun=r) for r in ("01", "10", "instance-A")]
    table = pd.DataFrame(rows).rename(columns={"strategy": ""})
    table.to_csv(tmp_path / "l.csv", index=False)
    loaded = load_ledger(tmp_path / "l.csv")
    assert loaded.rerun.tolist() == ["01", "10", "instance-A"] and loaded.strategy.tolist() == ["base"] * 3


def test_cli__throughput_works_when_gc_fields_are_empty(config):
    """WHEN throughput data is valid but GC fields are empty THEN throughput still runs."""
    rows = [observation(rerun=f"0{i}", score=100 + i, gc_count="", gc_time="", pauses_max="", forks="") for i in (1, 2)]
    (_, summary), _ = collect(thrpt.prepare, frame(rows), config)
    assert summary.score.tolist() == [101.5]


def test_cli__mixed_compatible_units_convert_before_the_mean(config):
    """WHEN rows use ops/ms and ops/s THEN mean = (2 ops/ms * 1000 + 1000 ops/s) / 2 = 1500 ops/s."""
    rows = [observation(rerun="01", score=2, score_unit="ops/ms"), observation(rerun="02", score=1000)]
    (_, summary), _ = collect(thrpt.prepare, frame(rows), config)
    assert summary.score.tolist() == [1500.0] and summary.score_unit.tolist() == ["ops/s"]


def test_cli__unsupported_unit_fails_without_assuming_ops_per_second(config):
    """WHEN a row has unit ops/h THEN it fails, naming the row and unit."""
    with pytest.raises(ValueError, match=r"Unsupported score_unit at ledger row\(s\) \[2\]: \['ops/h'\]"):
        thrpt.prepare(frame([observation(score_unit="ops/h")]), config)


class FrozenClock(datetime):
    """datetime whose now() walks through the given instants (last one repeats)."""
    instants = []

    @classmethod
    def now(cls, tz=None):
        value = cls.instants.pop(0) if len(cls.instants) > 1 else cls.instants[0]
        return value


def test_cli__same_second_creates_the_next_second_and_leaves_the_existing_folder(tmp_path, monkeypatch):
    """WHEN a folder for this second and feature exists THEN the next free second is used; the old folder is untouched."""
    t = lambda s: datetime(2026, 11, 1, 15, 1, s, tzinfo=timezone.utc)
    existing = tmp_path / "2026-11-01_15-01-27_thrpt"
    existing.mkdir()
    (existing / "keep.txt").write_text("x")
    FrozenClock.instants = [t(27), t(27), t(28)]
    monkeypatch.setattr(utils, "datetime", FrozenClock)
    monkeypatch.setattr(utils.time, "sleep", lambda _: None)
    made = utils.output_directory(tmp_path, "thrpt")
    assert made.name == "2026-11-01_15-01-28_thrpt"
    assert (existing / "keep.txt").read_text() == "x" and list(existing.iterdir()) == [existing / "keep.txt"]


def test_cli__gc_files_live_in_the_gc_pauses_folder_of_that_second(tmp_path, capsys, monkeypatch):
    """WHEN uv run gc runs at 2026-11-01 15:01:27 UTC THEN all its files are in 2026-11-01_15-01-27_gc_pauses/."""
    FrozenClock.instants = [datetime(2026, 11, 1, 15, 1, 27, tzinfo=timezone.utc)]
    monkeypatch.setattr(utils, "datetime", FrozenClock)
    cfg = write_run(tmp_path, [gc_observation(rerun=f"0{i}") for i in (1, 2)])
    code, printed, _, err = run_main(gc_main, cfg, capsys)
    assert code == 0, err
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["2026-11-01_15-01-27_gc_pauses"]
    assert {Path(p).parent.name for p in printed} == {"2026-11-01_15-01-27_gc_pauses"} and len(printed) == 12


def test_cli__benchmarks_that_share_a_file_name_stop_before_output(tmp_path, capsys):
    """WHEN two benchmarks reduce to the same safe name (ignoring case) THEN exit nonzero, both named, no output dir."""
    rows = [observation(benchmark_short=b) for b in ("mb 1", "MB_1")]
    cfg = write_run(tmp_path, rows, benchmarks=("mb 1", "MB_1"))
    code, printed, _, err = run_main(thrpt_main, cfg, capsys)
    assert code == 1 and printed == [] and "'mb 1'" in err and "'MB_1'" in err and "mb_1" in err.lower()
    assert not (tmp_path / "out").exists()


def _speedup_titles(tmp_path, monkeypatch, rows):
    titles = {}
    original = speedup.save_figure

    def spy(fig, stem, config):  # records the title, then does the real save
        titles[Path(stem).name] = fig.axes[0].get_title()
        return original(fig, stem, config)

    monkeypatch.setattr(speedup, "save_figure", spy)
    cfg = load_config(write_run(tmp_path, rows))
    collect(speedup.run, cfg)
    return titles


def test_cli__speedup_title_says_mean_of_reruns_never_average(tmp_path, monkeypatch):
    """WHEN speedup draws the across-reruns plot THEN line one is '<bench> — speedup, mean of reruns'."""
    titles = _speedup_titles(tmp_path, monkeypatch, sp_rows([100, 104, 96], [200, 210, 190]))
    first = titles[f"speedup_{BENCH}_avg"].split("\n")
    assert first[0] == f"{BENCH} — speedup, mean of reruns" and "average" not in " ".join(titles.values()).lower()
    assert first[1] == "Error bars: 95% paired Fieller interval of reruns"


def test_cli__no_ci_at_all_means_no_error_bars_line(tmp_path, monkeypatch):
    """WHEN no plotted row has a CI (one rerun) THEN the title has no Error bars line."""
    titles = _speedup_titles(tmp_path, monkeypatch, sp_rows([100], [200]))
    assert titles[f"speedup_{BENCH}_avg"] == f"{BENCH} — speedup, mean of reruns"


def test_cli__small_interval_bar_is_dark_thin_capped_and_on_top(config):
    """WHEN a speedup interval is narrow THEN its bar is dark, <= 1.5 pt, capped, above the line and dots."""
    rows = sp_rows([100, 104, 96, 103], [200, 210, 190, 205])
    (_, summary), _ = collect(speedup.prepare, frame(rows), config)
    ax = speedup.render(summary, config, "t", strategies=["base", "1a"], threads=[1, 2]).axes[0]
    (bars,) = [c for c in ax.collections if isinstance(c, LineCollection)]
    assert bars.get_zorder() > max(l.get_zorder() for l in ax.get_lines() if l.get_marker() == "o")  # above lines and dots
    assert bars.get_linewidth()[0] <= 1.5 and bars.get_zorder() >= 6
    assert max(bars.get_color()[0][:3]) <= 0.2          # #222 is (0.133, 0.133, 0.133)
    caps = [l for l in ax.get_lines() if l.get_marker() == "_" and l.get_zorder() > 3]
    assert caps and all(l.get_markersize() > 0 for l in caps)


def test_cli__partial_coverage_no_count_on_plot_warning_with_tag_and_csv_counts(config):
    """WHEN a strategy at 2 threads has 1 of 2 reruns THEN no count on the plot, warning with the tag, n 1 / total 2."""
    rows = [observation(rerun="01"), observation(rerun="02", score=110),
            observation("1a", "01", 2, 150), observation("1a", "02", 1, 160)]
    pd.DataFrame(rows).to_csv(config.ledger, index=False)
    paths, messages = collect(thrpt.run, config)
    assert f"{BENCH}/1a_n02_r02/thrpt missing." in messages
    avg = read_csv(next(p for p in paths if p.name.endswith("_avg.csv")))
    row = avg[(avg.strategy == "1a") & (avg.threads_blobshuffle == "2")].iloc[0]
    assert (row.n_reruns, row.total_reruns) == ("1", "2")
    (obs, summary), _ = collect(thrpt.prepare, frame(rows), config)
    ax = thrpt.render(summary, config, "t", strategies=["base", "1a"], threads=[1, 2]).axes[0]
    assert len(ax.texts) == 0 and not any("n=" in t.get_text() for t in ax.get_children() if hasattr(t, "get_text"))


def test_cli__blank_line_does_not_shift_the_next_rows_line_number(tmp_path):
    """WHEN a blank line sits between two data rows THEN the row after it reports its true line (4)."""
    (tmp_path / "l.csv").write_text("strategy,rerun,score\nbase,01,1\n\nbase,02,2\n")
    assert load_ledger(tmp_path / "l.csv").source_row.tolist() == [2, 4]


def test_cli__trailing_delimiter_fails_naming_the_line(tmp_path):
    """WHEN a data row ends in an extra delimiter THEN loading fails naming that line."""
    (tmp_path / "l.csv").write_text("strategy,rerun,score\nbase,01,1\nbase,02,2,\n")
    with pytest.raises(ValueError, match="Ledger line 3 has 4 fields but the header has 3"):
        load_ledger(tmp_path / "l.csv", ",")


def _jvm(threads, buffer=None, extra="-Xmx4g"):
    flags = [extra, f"-Dbench.threads={threads}"] + ([f"-Dbench.partitionBufferBytes={buffer}"] if buffer else [])
    return json.dumps(flags)


def test_cli__rows_differing_only_in_thread_and_buffer_flags_run(config):
    """WHEN rows differ only in -Dbench.threads and -Dbench.partitionBufferBytes THEN the command runs."""
    rows = [observation(threads=1, jvm_args=_jvm(1)), observation("1c", threads=2, jvm_args=_jvm(2, 4096))]
    (obs, _), _ = collect(thrpt.prepare, frame(rows), config)
    assert len(obs) == 2


def test_cli__other_jvm_flag_differing_fails_naming_jvm_args(config):
    """WHEN rows differ in -Xmx THEN the command fails naming jvm_args."""
    rows = [observation(jvm_args=_jvm(1, extra="-Xmx4g")), observation(rerun="02", jvm_args=_jvm(1, extra="-Xmx8g"))]
    with pytest.raises(ValueError, match=f"Incompatible settings in {BENCH}: jvm_args"):
        thrpt.prepare(frame(rows), config)


def test_cli__thread_flag_contradicting_its_row_fails_naming_the_row(config):
    """WHEN a row's -Dbench.threads differs from threads_blobshuffle THEN it fails naming the row."""
    rows = [observation(threads=2, jvm_args=_jvm(4))]
    with pytest.raises(ValueError, match=r"-Dbench.threads differs from threads_blobshuffle at ledger row\(s\) \[2\]"):
        thrpt.prepare(frame(rows), config)


def test_cli__large_scores_print_in_full_without_offset(config):
    """WHEN throughput reaches 14 million ops/s THEN ticks read 14,000,000, no offset text, label stays."""
    (obs, _), _ = collect(thrpt.prepare, frame([observation(score=14_000_000)]), config)
    fig = thrpt.render(obs, config, "t", strategies=["base"], threads=[1])
    ax = fig.axes[0]
    fig.canvas.draw()
    labels = [t.get_text() for t in ax.get_yticklabels()]
    assert ax.yaxis.get_major_formatter()(14_000_000, 0) == "14,000,000"
    assert "15,000,000" in labels and ax.yaxis.get_offset_text().get_text() == ""
    assert ax.get_ylabel() == "Throughput (ops/s)"


def test_cli__gc_table_in_ops_per_ms_has_score_in_ops_per_s_and_no_ledger_unit_columns(config):
    """WHEN the ledger reports ops/ms THEN the GC per-rerun table has score in ops/s and none of the three columns."""
    row = gc_observation(score=2, score_unit="ops/ms", score_error="0.1")
    (obs, _), _ = gc_prepare([row], config)
    assert obs.score.tolist() == [2000.0] and obs.score_unit.tolist() == ["ops/s"]
    assert not {"score_error", "score_confidence_low", "score_confidence_high"} & set(obs.columns)


def test_cli__shuffled_ledger_csvs_sorted_with_unchanged_values(tmp_path):
    """WHEN ledger rows are shuffled THEN CSV rows are in configured order, threads, rerun, values equal an unsorted run."""
    rows = [observation(strategy=s, rerun=f"0{r}", threads=n, score=100 * r + 10 * n + (0 if s == "base" else 5))
            for s in ("base", "1a") for n in ((1,) if s == "base" else (1, 2)) for r in (1, 2, 3)]
    rng = np.random.default_rng(4)
    shuffled = [rows[i] for i in rng.permutation(len(rows))]
    out, averages = {}, {}
    for name, order in (("sorted", rows), ("shuffled", shuffled)):
        sub = tmp_path / name
        sub.mkdir()
        cfg = write_run(sub, order)
        collect(thrpt.run, load_config(cfg))
        (folder,) = (sub / "out").iterdir()
        out[name] = read_csv(folder / f"thrpt_{BENCH}_r02.csv")
        averages[name] = read_csv(folder / f"thrpt_{BENCH}_avg.csv")
    got = out["shuffled"]
    assert got[["strategy", "threads_blobshuffle"]].values.tolist() == [["base", "1"], ["1a", "1"], ["1a", "2"]]
    assert got.score.tolist() == out["sorted"].score.tolist() == ["210", "215", "225"]
    assert got.rerun.tolist() == ["02"] * 3
    # Whole tables match, apart from where the rows sat in the ledger and which file held them.
    trace = ["source_row", "source_rows", "source_file", "source_files", "contributing_reruns"]
    for table in (out, averages):
        a, b = (table[name].drop(columns=trace, errors="ignore") for name in ("sorted", "shuffled"))
        pd.testing.assert_frame_equal(a, b)
    assert averages["shuffled"][["strategy", "threads_blobshuffle"]].values.tolist() == [["base", "1"], ["1a", "1"], ["1a", "2"]]
