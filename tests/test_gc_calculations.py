"""Hand-calculated GC regressions. Every expected value is a literal with its arithmetic.

Ledger row numbers: a programmatic frame has its first row at ledger row 2, so row i
(0-based) is reported as ledger row i + 2.
"""
from dataclasses import replace
import json
import re
import warnings

import pandas as pd
import pytest

from conftest import gc_observation
from src.gc_metrics import AVERAGED, PIVOT_METRICS, pivots, prepare
from src.utils import _setting, duration_seconds

BENCH = "mb1_rawNoFinalize"
APPROX = dict(rel=1e-9, abs=1e-12)


def quiet_prepare(rows, config):
    """Run prepare and fail on any UserWarning (the input is meant to be clean)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        # The new alloc-CI warning is intended for these one-rerun inputs; every other warning still fails.
        warnings.filterwarnings("ignore", message=r"No gc_alloc_rate_norm CI for ", category=UserWarning)
        return prepare(pd.DataFrame(rows), config)


def one(config, **overrides):
    observations, averages = quiet_prepare([gc_observation(**overrides)], config)
    assert len(observations) == 1 and len(averages) == 1
    return observations.iloc[0], averages.iloc[0]


def warned(record):
    return " || ".join(str(w.message) for w in record)


# ---------------------------------------------------------------- duration to seconds

@pytest.mark.parametrize("text, seconds", [
    ("10 s", 10.0),        # 10 * 1
    ("10s", 10.0),         # 10 * 1, no space
    ("500 ms", 0.5),       # 500 * 1e-3
    ("1 m", 60.0),         # 1 * 60
    ("1.5 s", 1.5),        # 1.5 * 1
    ("2000 us", 0.002),    # 2000 * 1e-6
    ("2000 µs", 0.002),    # micro sign U+00B5
    ("2000 μs", 0.002),    # Greek mu U+03BC
    ("250 ns", 2.5e-7),    # 250 * 1e-9
    ("2 h", 7200.0),       # 2 * 3600
    ("7", 7.0),            # bare number = seconds
    ("  3 ms ", 0.003),    # 3 * 1e-3, surrounding whitespace
])
def test_duration_seconds(text, seconds):
    assert duration_seconds(text) == pytest.approx(seconds, rel=1e-12)


@pytest.mark.parametrize("text", ["abc", "10 parsecs", "s", "1 x 2", "ten seconds", "1,5 s", "--3 s"])
def test_duration_seconds_malformed_names_the_text(text):
    with pytest.raises(ValueError, match=re.escape(text)):
        duration_seconds(text)


@pytest.mark.parametrize("text", ["", "   "])
def test_duration_seconds_blank_fails(text):
    with pytest.raises(ValueError):
        duration_seconds(text)


@pytest.mark.parametrize("text, seconds", [("10 s", 10), ("500 ms", 0.5), ("1 m", 60)])
def test_setting_check_keeps_its_behaviour(text, seconds):
    assert float(_setting(text, "measurement_time")) == pytest.approx(seconds)
    assert _setting("  ", "measurement_time") == ""
    with pytest.raises(ValueError, match="Malformed comparison field measurement_time: 10 parsecs"):
        _setting("10 parsecs", "measurement_time")


# ---------------------------------------------------------------- metric pass-through

@pytest.mark.parametrize("column", ["gc_alloc_rate_norm", "gc_count", "gc_time", "pauses_max", "pauses_p99"])
@pytest.mark.parametrize("text, value", [
    ("1234", 1234.0),
    ("12.5", 12.5),
    ("1.5e3", 1500.0),    # 1.5 * 10^3
    ("2E-2", 0.02),       # 2 * 10^-2
    ("0", 0.0),
    ("  7.25  ", 7.25),   # whitespace around a number
])
def test_ledger_metrics_pass_through_unscaled(config, column, text, value):
    # gc_time is also an input of gc_time_pct; with a huge-enough window nothing warns.
    observation, average = one(config, **{column: text})
    assert observation[column] == pytest.approx(value, **APPROX)
    assert average[column] == pytest.approx(value, **APPROX)


def test_other_metrics_stay_untouched_when_one_changes(config):
    observation, _ = one(config, gc_alloc_rate_norm="4096", gc_count="3", gc_time="100",
                         pauses_max="0.75", pauses_p99="0.5")
    assert observation["gc_alloc_rate_norm"] == 4096
    assert observation["gc_count"] == 3
    assert observation["gc_time"] == 100
    assert observation["pauses_max"] == 0.75
    assert observation["pauses_p99"] == 0.5


# ---------------------------------------------------------------- score normalization carried along

@pytest.mark.parametrize("unit, score, ops_per_second", [
    ("ops/us", "3", 3_000_000.0),     # 3 * 1e6
    ("ops/µs", "1", 1_000_000.0),     # 1 * 1e6
    ("ops/ms", "2.5", 2500.0),        # 2.5 * 1e3
    ("ops/s", "7", 7.0),              # 7 * 1
])
def test_score_is_normalized_to_ops_per_second(config, unit, score, ops_per_second):
    observation, average = one(config, score=score, score_unit=unit)
    assert observation["score"] == pytest.approx(ops_per_second)
    assert observation["score_unit"] == "ops/s"
    assert average["score"] == pytest.approx(ops_per_second)


# ---------------------------------------------------------------- gc_time_pct

@pytest.mark.parametrize("gc_time, forks, iterations, measurement_time, seconds, pct", [
    ("2500", "5", "5", "10 s", 10.0, 1.0),    # 2500 / (5*5*10*1000) * 100
    ("50", "1", "1", "500 ms", 0.5, 10.0),    # 50 / (1*1*0.5*1000) * 100
    ("0", "5", "5", "10 s", 10.0, 0.0),       # 0 / 250000 * 100
    ("3600", "2", "3", "1 m", 60.0, 1.0),     # 3600 / (2*3*60*1000) * 100
    ("125", "2", "1", "250 ms", 0.25, 25.0),  # 125 / (2*1*0.25*1000) * 100
    ("500", "1", "2", "10s", 10.0, 2.5),      # 500 / (1*2*10*1000) * 100
    ("1000", "1", "1", "1 s", 1.0, 100.0),    # 1000 / (1*1*1*1000) * 100, exactly 100 does not warn
    ("10", "4", "1", "5", 5.0, 0.05),         # 10 / (4*1*5*1000) * 100, bare seconds
])
def test_gc_time_pct(config, gc_time, forks, iterations, measurement_time, seconds, pct):
    observation, average = one(config, gc_time=gc_time, forks=forks,
                               measurement_iterations=iterations, measurement_time=measurement_time)
    assert observation["gc_time_pct"] == pytest.approx(pct, rel=1e-9, abs=1e-12)
    assert average["gc_time_pct"] == pytest.approx(pct, rel=1e-9, abs=1e-12)
    assert observation["measurement_seconds"] == pytest.approx(seconds, rel=1e-12)
    # Inputs stay as columns, and the time text is not rewritten.
    assert observation["measurement_time"] == measurement_time
    assert observation["gc_time"] == float(gc_time)
    assert observation["forks"] == float(forks)
    assert observation["measurement_iterations"] == float(iterations)


@pytest.mark.parametrize("gc_time, forks, iterations, measurement_time, pct", [
    ("1500", "1", "1", "1 s", 150.0),         # 1500 / (1*1*1*1000) * 100
    ("30000", "5", "5", "10 s", 12.0),        # 30000 / (5*5*10*1000) * 100, below 100: no warning
])
def test_gc_time_pct_above_100_warns_only_above(config, gc_time, forks, iterations, measurement_time, pct):
    rows = [gc_observation(gc_time=gc_time, forks=forks, measurement_iterations=iterations,
                           measurement_time=measurement_time)]
    if pct > 100:
        with pytest.warns(UserWarning, match=re.escape("gc_time_pct above 100 % at ledger row(s) [2]")):
            observations, _ = prepare(pd.DataFrame(rows), config)
        assert observations["gc_time_pct"].iloc[0] == pytest.approx(pct)
    else:
        observations, _ = quiet_prepare(rows, config)
        assert observations["gc_time_pct"].iloc[0] == pytest.approx(pct)


def test_gc_time_pct_warning_lists_every_offending_row(config):
    rows = [gc_observation(rerun="a", gc_time="2500"), gc_observation(rerun="b", gc_time="26000"),
            gc_observation(rerun="c", gc_time="2500"), gc_observation(rerun="d", gc_time="260000")]
    # 26000 / 250000 * 100 = 10.4 (fine); 260000 / 250000 * 100 = 104.0 (above 100)
    with pytest.warns(UserWarning, match=re.escape("gc_time_pct above 100 % at ledger row(s) [5]")):
        observations, _ = prepare(pd.DataFrame(rows), config)
    assert observations["gc_time_pct"].tolist() == pytest.approx([1.0, 10.4, 1.0, 104.0])


# ---------------------------------------------------------------- batch_bytes

@pytest.mark.parametrize("record, record_unit, batches, batches_unit, expected", [
    ("1677.7216", "bytes/ms", "0.0001", "batches/ms", 16777216.0),   # 1677.7216 / 0.0001
    ("838.8608", "bytes/ms", "0.0001", "batches/ms", 8388608.0),     # 838.8608 / 0.0001
    ("1000", "bytes/ms", "0.5", "batches/ms", 2000.0),               # 1000 / 0.5
    ("1677.7216", "bytes/us", "0.0001", "batches/us", 16777216.0),   # same rates per us
    ("1000", "bytes/s", "0.5", "batches/s", 2000.0),                 # same rates per s
    ("1000", "bytes/ns", "0.5", "batches/ns", 2000.0),               # same rates per ns
    ("3", "bytes/ms", "4", "batches/ms", 0.75),                      # 3 / 4
    ("0", "bytes/ms", "2", "batches/ms", 0.0),                       # 0 / 2
])
def test_batch_bytes(config, record, record_unit, batches, batches_unit, expected):
    observation, average = one(config, aux_recordBytes=record, aux_recordBytes_unit=record_unit,
                               aux_batches=batches, aux_batches_unit=batches_unit)
    assert observation["batch_bytes"] == pytest.approx(expected, **APPROX)
    assert average["batch_bytes"] == pytest.approx(expected, **APPROX)
    assert observation["aux_recordBytes"] == float(record)
    assert observation["aux_batches"] == float(batches)


@pytest.mark.parametrize("record", ["1000", "0"])
def test_zero_batches_on_no_finalize_leave_both_missing_without_a_warning(config, record):
    rows = [gc_observation(aux_recordBytes=record, aux_batches="0")]
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        observations, averages = prepare(pd.DataFrame(rows), config)
    assert not any("batch" in str(w.message) for w in seen)  # NoFinalize closes no batch by design.
    assert pd.isna(observations["batch_bytes"].iloc[0]) and pd.isna(observations["batch_fill"].iloc[0])
    assert pd.isna(averages["batch_bytes"].iloc[0]) and pd.isna(averages["batch_fill"].iloc[0])
    # The rest of the row is kept.
    assert observations["gc_time_pct"].iloc[0] == pytest.approx(1.0)  # 2500 / 250000 * 100
    assert observations["gc_count"].iloc[0] == 10


def test_zero_batches_warning_lists_every_row(config):
    rows = [gc_observation(rerun="a"), gc_observation(rerun="b", aux_batches="0"),
            gc_observation(rerun="c"), gc_observation(rerun="d", aux_batches="0")]
    with pytest.warns(UserWarning, match=re.escape("Zero aux_batches at ledger row(s) [3, 5]; batch_bytes and batch_fill missing")):
        observations, _ = prepare(pd.DataFrame(rows), config)
    assert observations["batch_fill"].isna().tolist() == [False, True, False, True]


# ---------------------------------------------------------------- batch_fill

@pytest.mark.parametrize("record, batches, desired, fill", [
    ("1677.7216", "0.0001", "16777216", 1.0),   # (1677.7216/0.0001) / 16777216 = 16777216 / 16777216
    ("838.8608", "0.0001", "16777216", 0.5),    # 8388608 / 16777216
    ("419.4304", "0.0001", "16777216", 0.25),   # 4194304 / 16777216
    ("2516.5824", "0.0001", "16777216", 1.5),   # 25165824 / 16777216, not clamped to 1
    ("786.432", "0.001", "1048576", 0.75),      # 786432 / 1048576
    ("3000", "1", "2000", 1.5),                 # 3000 / 2000
    ("1000", "0.5", "4000", 0.5),               # (1000/0.5) / 4000 = 2000 / 4000
    ("0", "1", "4000", 0.0),                    # 0 / 4000
    ("100000", "1", "1000", 100.0),             # 100000 / 1000, far above 1, not clamped
])
def test_batch_fill(config, record, batches, desired, fill):
    observation, average = one(config, aux_recordBytes=record, aux_batches=batches,
                               cfg_batchDesiredBytes=desired)
    assert observation["batch_fill"] == pytest.approx(fill, **APPROX)
    assert average["batch_fill"] == pytest.approx(fill, **APPROX)
    assert observation["cfg_batchDesiredBytes"] == float(desired)


# ---------------------------------------------------------------- aux unit check

@pytest.mark.parametrize("record_unit, batches_unit", [
    ("bytes/ms", "batches/ms"),
    ("bytes/us", "batches/us"),
    ("bytes/µs", "batches/µs"),     # micro sign both
    ("bytes/μs", "batches/μs"),     # Greek mu both
    ("bytes/µs", "batches/us"),     # micro sign vs ASCII: same unit after normalizing
    ("bytes/us", "batches/μs"),     # ASCII vs Greek mu
    ("bytes/μs", "batches/µs"),     # Greek mu vs micro sign
    ("bytes/s", "batches/s"),
    ("bytes/ns", "batches/ns"),
    (" bytes/ms ", "\tbatches/ms "),  # whitespace is stripped
])
def test_compatible_aux_units_pass(config, record_unit, batches_unit):
    observation, _ = one(config, aux_recordBytes_unit=record_unit, aux_batches_unit=batches_unit)
    assert observation["batch_fill"] == pytest.approx(1.0)  # 1677.7216 / 0.0001 / 16777216


@pytest.mark.parametrize("record_unit, batches_unit", [
    ("bytes/ms", "batches/us"),   # mixed time units
    ("bytes/us", "batches/ms"),
    ("bytes/ms", "batches/ns"),
    ("bytes/s", "batches/ms"),
    ("ops/ms", "batches/ms"),     # wrong quantity for the byte rate
    ("bytes/ms", "ops/ms"),       # wrong quantity for the batch rate
    ("batches/ms", "bytes/ms"),   # swapped
    ("bytes/h", "batches/h"),     # time unit not in {ns, us, ms, s}
    ("bytes", "batches"),         # no time unit
    ("bytes/ms", ""),             # blank unit beside a nonblank value
    ("", "batches/ms"),
])
def test_incompatible_aux_units_fail(config, record_unit, batches_unit):
    rows = [gc_observation(aux_recordBytes_unit=record_unit, aux_batches_unit=batches_unit)]
    with pytest.raises(ValueError, match=re.escape("Incompatible aux units at ledger row(s) [2]")) as error:
        prepare(pd.DataFrame(rows), config)
    assert record_unit in str(error.value) and batches_unit in str(error.value)


def test_incompatible_aux_units_fail_even_when_batches_are_zero(config):
    rows = [gc_observation(aux_batches="0", aux_batches_unit="batches/us")]
    with pytest.raises(ValueError, match="Incompatible aux units"):
        prepare(pd.DataFrame(rows), config)


def test_incompatible_aux_units_list_every_bad_row(config):
    rows = [gc_observation(rerun="a"), gc_observation(rerun="b", aux_batches_unit="batches/us"),
            gc_observation(rerun="c", aux_recordBytes_unit="ops/ms")]
    with pytest.raises(ValueError, match=re.escape("Incompatible aux units at ledger row(s) [3, 4]")):
        prepare(pd.DataFrame(rows), config)


@pytest.mark.parametrize("blank_column, bad_unit_column", [
    ("aux_batches", "aux_batches_unit"),
    ("aux_recordBytes", "aux_recordBytes_unit"),
    ("aux_batches", "aux_recordBytes_unit"),
])
def test_units_are_only_checked_when_both_values_are_present(config, blank_column, bad_unit_column):
    rows = [gc_observation(**{blank_column: "", bad_unit_column: "garbage"})]
    with pytest.warns(UserWarning, match="Blank " + blank_column):
        observations, _ = prepare(pd.DataFrame(rows), config)
    assert pd.isna(observations["batch_bytes"].iloc[0])


# ---------------------------------------------------------------- averages

# Each case: metric, per-rerun overrides, hand-calculated mean.
AVERAGE_CASES = [
    ("score", [{"score": "100"}, {"score": "200"}, {"score": "600"}], 300.0),                  # (100+200+600)/3
    ("gc_alloc_rate_norm", [{"gc_alloc_rate_norm": v} for v in ("1000", "1200", "1400")], 1200.0),  # 3600/3
    ("gc_count", [{"gc_count": v} for v in ("10", "20", "30")], 20.0),                          # 60/3
    ("gc_time", [{"gc_time": v} for v in ("2500", "5000", "7500")], 5000.0),                    # 15000/3
    ("gc_time_pct", [{"gc_time": v} for v in ("2500", "5000", "7500")], 2.0),                   # (1+2+3)/3 with 250000 ms windows
    ("pauses_max", [{"pauses_max": v} for v in ("2.26", "4.1", "3.0")], 3.12),                  # 9.36/3
    ("pauses_max", [{"pauses_max": v} for v in ("1", "9")], 5.0),                               # mean of maxima, not the max: (1+9)/2
    ("pauses_p99", [{"pauses_p99": v} for v in ("1", "2", "6")], 3.0),                          # 9/3
    ("batch_bytes", [{"aux_recordBytes": v} for v in ("838.8608", "1677.7216", "2516.5824")], 16777216.0),  # (8388608+16777216+25165824)/3
    ("batch_fill", [{"aux_recordBytes": v} for v in ("838.8608", "1677.7216")], 0.75),          # (0.5+1.0)/2
    ("batch_fill", [{"aux_recordBytes": v} for v in ("838.8608", "1677.7216", "2516.5824")], 1.0),  # (0.5+1.0+1.5)/3
    ("batch_fill", [{"aux_recordBytes": v} for v in ("419.4304", "1677.7216")], 0.625),         # (0.25+1.0)/2
]


@pytest.mark.parametrize("metric, per_rerun, expected", AVERAGE_CASES)
def test_average_of_each_metric(config, metric, per_rerun, expected):
    rows = [gc_observation(rerun=f"r{i}", **overrides) for i, overrides in enumerate(per_rerun)]
    _, averages = quiet_prepare(rows, config)
    assert len(averages) == 1
    assert averages[metric].iloc[0] == pytest.approx(expected, **APPROX)
    assert averages["n_reruns"].iloc[0] == len(per_rerun)


def test_averaged_constant_lists_every_metric():
    assert AVERAGED == ("score", "gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct",
                        "pauses_max", "pauses_p99", "batch_bytes", "batch_fill")
    assert PIVOT_METRICS == ("gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct",
                             "pauses_max", "pauses_p99", "batch_fill")


def test_single_rerun_average_equals_itself(config):
    _, average = one(config, score="50", gc_alloc_rate_norm="777", gc_count="9", gc_time="500",
                     pauses_max="1.25", pauses_p99="0.5", aux_recordBytes="838.8608")
    assert average["score"] == 50
    assert average["gc_alloc_rate_norm"] == 777
    assert average["gc_count"] == 9
    assert average["gc_time"] == 500
    assert average["gc_time_pct"] == pytest.approx(0.2)    # 500 / 250000 * 100
    assert average["pauses_max"] == 1.25
    assert average["pauses_p99"] == 0.5
    assert average["batch_bytes"] == pytest.approx(8388608)  # 838.8608 / 0.0001
    assert average["batch_fill"] == pytest.approx(0.5)       # 8388608 / 16777216
    assert average["n_reruns"] == 1 and average["total_reruns"] == 1


def three_reruns():
    return [gc_observation(rerun="a", gc_alloc_rate_norm="1000", gc_count="10", gc_time="2500",
                           pauses_max="1", pauses_p99="1", aux_recordBytes="838.8608"),
            gc_observation(rerun="b", gc_alloc_rate_norm="1200", gc_count="20", gc_time="5000",
                           pauses_max="2", pauses_p99="2", aux_recordBytes="1677.7216"),
            gc_observation(rerun="c", gc_alloc_rate_norm="1400", gc_count="30", gc_time="7500",
                           pauses_max="3", pauses_p99="3", aux_recordBytes="2516.5824")]


# Averages that survive when the given column is blank in rerun "b" (ledger row 3).
SURVIVING = {
    "gc_alloc_rate_norm": {"gc_count": 20, "gc_time": 5000, "gc_time_pct": 2.0, "pauses_max": 2.0,
                           "pauses_p99": 2.0, "batch_bytes": 16777216, "batch_fill": 1.0},
    "gc_count": {"gc_alloc_rate_norm": 1200, "gc_time": 5000, "gc_time_pct": 2.0, "pauses_max": 2.0,
                 "pauses_p99": 2.0, "batch_bytes": 16777216, "batch_fill": 1.0},
    "gc_time": {"gc_alloc_rate_norm": 1200, "gc_count": 20, "pauses_max": 2.0, "pauses_p99": 2.0,
                "batch_bytes": 16777216, "batch_fill": 1.0},
    "pauses_max": {"gc_alloc_rate_norm": 1200, "gc_count": 20, "gc_time": 5000, "gc_time_pct": 2.0,
                   "pauses_p99": 2.0, "batch_bytes": 16777216, "batch_fill": 1.0},
    "pauses_p99": {"gc_alloc_rate_norm": 1200, "gc_count": 20, "gc_time": 5000, "gc_time_pct": 2.0,
                   "pauses_max": 2.0, "batch_bytes": 16777216, "batch_fill": 1.0},
    "aux_recordBytes": {"gc_alloc_rate_norm": 1200, "gc_count": 20, "gc_time": 5000, "gc_time_pct": 2.0,
                        "pauses_max": 2.0, "pauses_p99": 2.0},
    "aux_batches": {"gc_alloc_rate_norm": 1200, "gc_count": 20, "gc_time": 5000, "gc_time_pct": 2.0,
                    "pauses_max": 2.0, "pauses_p99": 2.0},
}
MISSING = {
    "gc_alloc_rate_norm": ["gc_alloc_rate_norm"], "gc_count": ["gc_count"],
    "gc_time": ["gc_time", "gc_time_pct"], "pauses_max": ["pauses_max"], "pauses_p99": ["pauses_p99"],
    "aux_recordBytes": ["batch_bytes", "batch_fill"], "aux_batches": ["batch_bytes", "batch_fill"],
}
TAILS = {"gc_time": "; gc_time_pct missing", "aux_recordBytes": "; batch_bytes and batch_fill missing",
         "aux_batches": "; batch_bytes and batch_fill missing"}


@pytest.mark.parametrize("blank", list(SURVIVING))
def test_missing_contributor_blanks_only_its_metric_average(config, blank):
    rows = three_reruns()
    rows[1][blank] = ""
    with pytest.warns(UserWarning) as seen:
        observations, averages = prepare(pd.DataFrame(rows), config)
    text = warned(seen)
    assert f"Blank {blank} at ledger row(s) [3]{TAILS.get(blank, '')}" in text
    average = averages.iloc[0]
    for metric in MISSING[blank]:
        assert pd.isna(average[metric]), metric
        assert f"Mean of reruns of {metric} missing for {BENCH}/base/threads=1: a contributor lacks {metric}" in text
    for metric, expected in SURVIVING[blank].items():
        assert average[metric] == pytest.approx(expected, **APPROX), metric
    # Per-rerun values of the other contributors stay present.
    assert observations[MISSING[blank]].iloc[[0, 2]].notna().all().all()
    assert observations[MISSING[blank]].iloc[1].isna().all()
    assert average["n_reruns"] == 3


# ---------------------------------------------------------------- coverage fields

OPAQUE = ["01", "10", "instance-A"]


def coverage_rows(reruns, partial_threads=None):
    rows = [gc_observation(rerun=r) for r in reruns]
    if partial_threads:
        rows += [gc_observation(strategy="1a", rerun=r, threads=partial_threads[0])
                 for r in partial_threads[1]]
    return rows


@pytest.mark.parametrize("reruns, present", [
    (OPAQUE, OPAQUE),                                 # 3/3
    (OPAQUE, ["01", "instance-A"]),                   # 2/3
    (["01", "10", "instance-A"] + [f"x{i}" for i in range(7)], ["instance-A"]),  # 1/10
])
def test_coverage_fields(config, reruns, present):
    rows = coverage_rows(reruns, (4, present))
    expected_count = len(present)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        observations, averages = prepare(pd.DataFrame(rows), config)
    text = " || ".join(str(w.message) for w in seen)
    group = averages[averages["strategy"].eq("1a")].iloc[0]
    assert group["threads_blobshuffle"] == 4
    assert group["n_reruns"] == expected_count
    assert group["total_reruns"] == len(reruns)
    assert sorted(json.loads(group["contributing_reruns"])) == sorted(present)
    assert all(isinstance(r, str) for r in json.loads(group["contributing_reruns"]))
    if expected_count < len(reruns):
        assert all(f"{BENCH}/1a_n04_r{r}/thrpt missing." in text for r in set(reruns) - set(present))
    else:
        assert "/thrpt missing" not in text
    full = averages[averages["strategy"].eq("base")].iloc[0]
    assert full["n_reruns"] == full["total_reruns"] == len(reruns)
    assert sorted(json.loads(full["contributing_reruns"])) == sorted(reruns)
    # observations: one row per ledger row, one contributing rerun each, same total_reruns.
    assert (observations["n_reruns"] == 1).all()
    assert (observations["total_reruns"] == len(reruns)).all()
    for _, row in observations.iterrows():
        assert json.loads(row["contributing_reruns"]) == [row["rerun"]]
    assert set(observations["rerun"]) == set(reruns)


def test_average_provenance_lists(config):
    rows = [gc_observation(rerun="01", strategy="1A"), gc_observation(rerun="10", strategy="1a ")]
    frame = pd.DataFrame(rows)
    _, averages = prepare(frame, config)
    average = averages.iloc[0]
    assert average["strategy"] == "1a"
    assert json.loads(average["source_rows"]) == [2, 3]
    assert json.loads(average["source_files"]) == []                        # no source_file column
    assert sorted(json.loads(average["original_strategies"])) == ["1A", "1a"]  # stripped originals
    assert average["summary_kind"] == "mean of reruns"


def test_source_file_is_listed_when_present(config):
    frame = pd.DataFrame([gc_observation(rerun="01"), gc_observation(rerun="02")])
    frame["source_row"] = [11, 12]
    frame["source_file"] = "/data/ledger.csv"
    observations, averages = prepare(frame, config)
    assert observations["source_row"].tolist() == [11, 12]
    assert observations["source_file"].tolist() == ["/data/ledger.csv"] * 2
    assert json.loads(averages["source_rows"].iloc[0]) == [11, 12]
    assert set(json.loads(averages["source_files"].iloc[0])) == {"/data/ledger.csv"}


def test_observation_columns_and_order(config):
    frame = pd.DataFrame([gc_observation()])
    frame["source_file"] = "x.csv"
    observations, averages = prepare(frame, config)
    assert observations.columns.tolist()[:29] == [
        "benchmark_short", "strategy", "strategy_original", "threads_blobshuffle", "rerun",
        "score", "score_unit", "gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct",
        "forks", "measurement_iterations", "measurement_time", "measurement_seconds",
        "pauses_max", "pauses_p99", "aux_recordBytes", "aux_recordBytes_unit", "aux_batches",
        "aux_batches_unit", "cfg_batchDesiredBytes", "batch_bytes", "batch_fill",
        "n_reruns", "total_reruns", "contributing_reruns", "source_row", "source_file"]
    assert averages.columns.tolist() == [
        "benchmark_short", "strategy", "threads_blobshuffle", *AVERAGED, "n_reruns", "total_reruns",
        "contributing_reruns", "source_rows", "source_files", "original_strategies", "summary_kind",
        "gc_alloc_rate_norm_ci_low", "gc_alloc_rate_norm_ci_high",
        *[f"{m}_t_{e}" for m in AVERAGED for e in ("low", "high")],
        *[f"{m}_ci_{e}" for m in AVERAGED if m != "gc_alloc_rate_norm" for e in ("low", "high")]]


# ---------------------------------------------------------------- pivots

# (strategy, threads) -> two reruns of (alloc, count, gc_time, pauses_max, pauses_p99, record_bytes)
CELLS = {
    ("base", 1): [("1000", "10", "2500", "1", "0.5", "838.8608"), ("1200", "20", "5000", "3", "1.5", "1677.7216")],
    ("base", 2): [("2000", "30", "5000", "2", "1", "1677.7216"), ("2400", "50", "10000", "4", "3", "2516.5824")],
    ("1a", 1): [("500", "4", "250", "0.5", "0.25", "419.4304"), ("700", "8", "750", "1.5", "0.75", "838.8608")],
    ("1a", 2): [("800", "6", "1250", "4", "2", "838.8608"), ("1000", "10", "3750", "6", "4", "2516.5824")],
}
# Hand-calculated averages per metric, (base,1) (base,2) (1a,1) (1a,2).
PIVOT_EXPECTED = {
    "gc_alloc_rate_norm": {("base", 1): 1100, ("base", 2): 2200, ("1a", 1): 600, ("1a", 2): 900},   # (1000+1200)/2, (2000+2400)/2, (500+700)/2, (800+1000)/2
    "gc_count": {("base", 1): 15, ("base", 2): 40, ("1a", 1): 6, ("1a", 2): 8},                      # (10+20)/2, (30+50)/2, (4+8)/2, (6+10)/2
    "gc_time": {("base", 1): 3750, ("base", 2): 7500, ("1a", 1): 500, ("1a", 2): 2500},              # (2500+5000)/2, (5000+10000)/2, (250+750)/2, (1250+3750)/2
    "gc_time_pct": {("base", 1): 1.5, ("base", 2): 3.0, ("1a", 1): 0.2, ("1a", 2): 1.0},             # (1+2)/2, (2+4)/2, (0.1+0.3)/2, (0.5+1.5)/2 (window 250000 ms)
    "pauses_max": {("base", 1): 2.0, ("base", 2): 3.0, ("1a", 1): 1.0, ("1a", 2): 5.0},              # (1+3)/2, (2+4)/2, (0.5+1.5)/2, (4+6)/2
    "pauses_p99": {("base", 1): 1.0, ("base", 2): 2.0, ("1a", 1): 0.5, ("1a", 2): 3.0},              # (0.5+1.5)/2, (1+3)/2, (0.25+0.75)/2, (2+4)/2
    "batch_fill": {("base", 1): 0.75, ("base", 2): 1.25, ("1a", 1): 0.375, ("1a", 2): 1.0},          # (0.5+1)/2, (1+1.5)/2, (0.25+0.5)/2, (0.5+1.5)/2
}


def cell_rows(skip=()):
    rows = []
    for (strategy, threads), reruns in CELLS.items():
        if (strategy, threads) in skip:
            continue
        for i, (alloc, count, gc_time, pmax, p99, record) in enumerate(reruns):
            rows.append(gc_observation(strategy=strategy, threads=threads, rerun=f"0{i + 1}",
                                       gc_alloc_rate_norm=alloc, gc_count=count, gc_time=gc_time,
                                       pauses_max=pmax, pauses_p99=p99, aux_recordBytes=record))
    return rows


def built_pivots(config, rows):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        _, averages = prepare(pd.DataFrame(rows), config)
        return pivots(averages, config)


def test_pivot_keys(config):
    result = built_pivots(config, cell_rows())
    assert list(result) == [BENCH]
    assert set(result[BENCH]) == set(PIVOT_METRICS)


@pytest.mark.parametrize("metric", PIVOT_METRICS)
def test_pivot_cells_layout_and_order(config, metric):
    table = built_pivots(config, cell_rows())[BENCH][metric]
    assert table.index.name == "strategy"
    assert table.index.tolist() == ["base", "1a"]        # configured order
    assert table.columns.tolist() == [1, 2]              # sorted int thread counts
    for (strategy, threads), expected in PIVOT_EXPECTED[metric].items():
        assert table.loc[strategy, threads] == pytest.approx(expected, **APPROX), (strategy, threads)
    assert all(pd.api.types.is_float_dtype(dtype) for dtype in table.dtypes)


@pytest.mark.parametrize("metric", PIVOT_METRICS)
def test_pivot_empty_cell_for_absent_combination(config, metric):
    table = built_pivots(config, cell_rows(skip={("1a", 2)}))[BENCH][metric]
    assert table.columns.tolist() == [1, 2]
    assert pd.isna(table.loc["1a", 2])
    assert table.loc["1a", 1] == pytest.approx(PIVOT_EXPECTED[metric][("1a", 1)], **APPROX)
    assert table.loc["base", 2] == pytest.approx(PIVOT_EXPECTED[metric][("base", 2)], **APPROX)


def test_pivot_order_follows_config_with_unknown_strategies_last(config):
    rows = cell_rows() + [gc_observation(strategy="zzz", threads=1, rerun="01"),
                          gc_observation(strategy="aaa", threads=1, rerun="01")]
    rows += [gc_observation(strategy=s, threads=1, rerun="02") for s in ("zzz", "aaa")]
    swapped = replace(config, order=("1a", "base"))
    assert built_pivots(swapped, rows)[BENCH]["gc_count"].index.tolist() == ["1a", "base", "aaa", "zzz"]
    assert built_pivots(config, rows)[BENCH]["gc_count"].index.tolist() == ["base", "1a", "aaa", "zzz"]


def test_pivot_thread_columns_sort_numerically(config):
    rows = [gc_observation(threads=t, gc_count=str(c)) for t, c in ((10, 100), (2, 20), (4, 40))]
    table = built_pivots(config, rows)[BENCH]["gc_count"]
    assert table.columns.tolist() == [2, 4, 10]           # not the text order 10, 2, 4
    assert table.loc["base"].tolist() == [20, 40, 100]


# ---------------------------------------------------------------- warnings and errors

BLANK_TAILS = {"gc_time": "; gc_time_pct missing", "aux_recordBytes": "; batch_bytes and batch_fill missing",
               "aux_batches": "; batch_bytes and batch_fill missing"}
METRIC_COLUMNS = ["gc_alloc_rate_norm", "gc_count", "gc_time", "pauses_max", "pauses_p99",
                  "aux_recordBytes", "aux_batches"]


@pytest.mark.parametrize("column", METRIC_COLUMNS)
def test_blank_metric_warns_with_rows_and_consequence(config, column):
    rows = [gc_observation(rerun="a"), gc_observation(rerun="b", **{column: ""}),
            gc_observation(rerun="c", **{column: "  "})]
    with pytest.warns(UserWarning, match=re.escape(f"Blank {column} at ledger row(s) [3, 4]{BLANK_TAILS.get(column, '')}")):
        observations, _ = prepare(pd.DataFrame(rows), config)
    base = {"aux_recordBytes": "batch_bytes", "aux_batches": "batch_bytes", "gc_time": "gc_time_pct"}.get(column, column)
    assert observations[base].isna().tolist() == [False, True, True]
    assert observations["gc_count"].notna().sum() == (3 if column != "gc_count" else 1)


@pytest.mark.parametrize("column", METRIC_COLUMNS)
@pytest.mark.parametrize("bad", ["-1", "-0.5"])
def test_negative_metric_fails(config, column, bad):
    rows = [gc_observation(), gc_observation(rerun="b", **{column: bad})]
    with pytest.raises(ValueError, match=re.escape(f"{column} must be nonnegative")) as error:
        prepare(pd.DataFrame(rows), config)
    assert "[3]" in str(error.value)


@pytest.mark.parametrize("column", METRIC_COLUMNS)
@pytest.mark.parametrize("bad", ["abc", "nan", "inf", "1,5"])
def test_malformed_metric_fails_with_column_and_row(config, column, bad):
    rows = [gc_observation(), gc_observation(rerun="b", **{column: bad})]
    with pytest.raises(ValueError, match=re.escape(f"Invalid {column} at ledger row(s) [3]")):
        prepare(pd.DataFrame(rows), config)


@pytest.mark.parametrize("column", ["forks", "measurement_iterations", "measurement_time", "cfg_batchDesiredBytes"])
def test_blank_setting_fails(config, column):
    rows = [gc_observation(rerun="a", **{column: ""}), gc_observation(rerun="b", **{column: " "})]
    with pytest.raises(ValueError, match=re.escape(f"Blank {column} at ledger row(s) [2, 3]")):
        prepare(pd.DataFrame(rows), config)


@pytest.mark.parametrize("column, bad", [
    ("forks", "0"), ("forks", "-1"),
    ("measurement_iterations", "0"), ("measurement_iterations", "-5"),
    ("cfg_batchDesiredBytes", "0"), ("cfg_batchDesiredBytes", "-16777216"),
    ("measurement_time", "0 s"), ("measurement_time", "0"), ("measurement_time", "0 ms"),
])
def test_nonpositive_setting_fails(config, column, bad):
    rows = [gc_observation(**{column: bad})]
    with pytest.raises(ValueError, match=re.escape(f"{column} must be positive")) as error:
        prepare(pd.DataFrame(rows), config)
    assert "[2]" in str(error.value)


def test_malformed_measurement_time_fails_naming_the_text(config):
    with pytest.raises(ValueError, match="ten seconds"):
        prepare(pd.DataFrame([gc_observation(measurement_time="ten seconds")]), config)


REQUIRED = ["gc_alloc_rate_norm", "gc_count", "gc_time", "pauses_max", "pauses_p99", "forks",
            "measurement_iterations", "measurement_time", "aux_recordBytes", "aux_recordBytes_unit",
            "aux_batches", "aux_batches_unit", "cfg_batchDesiredBytes"]


@pytest.mark.parametrize("column", REQUIRED)
def test_missing_required_column_fails(config, column):
    frame = pd.DataFrame([gc_observation()]).drop(columns=[column])
    with pytest.raises(ValueError, match=re.escape(f"Missing ledger column(s): ['{column}']")):
        prepare(frame, config)


def test_missing_columns_are_listed_sorted(config):
    frame = pd.DataFrame([gc_observation()]).drop(columns=["gc_time", "forks", "aux_batches"])
    with pytest.raises(ValueError, match=re.escape("Missing ledger column(s): ['aux_batches', 'forks', 'gc_time']")):
        prepare(frame, config)


def test_blank_inputs_warn_for_every_missing_derived_value(config):
    rows = [gc_observation(rerun="a", gc_time=""), gc_observation(rerun="b", aux_recordBytes=""),
            gc_observation(rerun="c", aux_batches=""), gc_observation(rerun="d")]
    with pytest.warns(UserWarning) as seen:
        observations, _ = prepare(pd.DataFrame(rows), config)
    text = warned(seen)
    assert "Blank gc_time at ledger row(s) [2]; gc_time_pct missing" in text
    assert "Blank aux_recordBytes at ledger row(s) [3]; batch_bytes and batch_fill missing" in text
    assert "Blank aux_batches at ledger row(s) [4]; batch_bytes and batch_fill missing" in text
    assert observations["gc_time_pct"].isna().tolist() == [True, False, False, False]
    assert observations["batch_fill"].isna().tolist() == [False, True, True, False]
