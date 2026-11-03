"""Coverage audit: error branches and small units of utils, config, cli, speedup and gc that
no other test reached. Every message fragment below is the one the source raises."""
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from conftest import gc_observation, observation
from src.config import load_config
from src.speedup import prepare as speedup_prepare
from src.gc_metrics import prepare as gc_prepare
from src.utils import (fieller_interval, has_spread, load_ledger, normalize_throughput, t_interval,
                       t_quantile, validate_comparability, warn_incomplete)


# ---- config ------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _close_figures():
    """Figures built by render() calls here must not leak into tests that count open figures."""
    yield
    import matplotlib.pyplot as plt
    plt.close("all")

def cfg(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


@pytest.mark.parametrize("text,match", [
    ("- a\n- b\n", "config must be a mapping"),                       # not a mapping
    ("input: [\n", "Invalid YAML"),                                    # YAML syntax error
    ("input: {path: l.csv}\nbenchmarks: [a, a]\n", "benchmarks contains duplicate entries"),
    ("input: {}\n", "input.path must be a nonempty path string"),     # path absent
    ("input: {path: ' '}\n", "input.path must be a nonempty path string"),
    ("input: {path: l.csv, delimiter: ';'}\n", "input.delimiter"),
    ("input: {path: l.csv}\nstrategies: {labels: {base: ''}}\n", "strategies.labels must map"),
    ("input: {path: l.csv}\nstrategies: {labels: {base: 3}}\n", "strategies.labels must map"),
    ("input: {path: l.csv}\ncomparison: {margin: 5}\n", "comparison.margin"),   # percent by mistake
    ("input: {path: l.csv}\ncomparison: {margin: 0}\n", "comparison.margin"),
    ("input: {path: l.csv}\ncomparison: {margin: true}\n", "comparison.margin"),
    ("input: {path: l.csv}\ncomparison: {size: 1}\n", "Unknown comparison setting"),
])
def test_config_errors(tmp_path, text, match):
    with pytest.raises(ValueError, match=match):
        load_config(cfg(tmp_path, text))


def test_config_valid_margin_and_tab_delimiter(tmp_path):
    config = load_config(cfg(tmp_path, "input: {path: l.csv, delimiter: \"\\t\"}\ncomparison: {margin: 0.05}\n"))
    assert config.comparison_margin == 0.05 and config.delimiter == "\t"
    assert load_config(cfg(tmp_path, "input: {path: l.csv}\n")).comparison_margin is None


# ---- ledger loading ----------------------------------------------------------------------

def test_excel_input_is_refused(tmp_path):
    with pytest.raises(ValueError, match="Excel input is not supported yet"):
        load_ledger(tmp_path / "ledger.xlsx")


def test_empty_file_is_a_readable_error(tmp_path):
    (tmp_path / "l.csv").write_text("")
    with pytest.raises(ValueError, match="Cannot read ledger"):
        load_ledger(tmp_path / "l.csv", ",")


def test_duplicate_header_names_after_trimming(tmp_path):
    (tmp_path / "l.csv").write_text("strategy,score, score\nbase,1,2\n")
    with pytest.raises(ValueError, match="duplicate column names after trimming"):
        load_ledger(tmp_path / "l.csv", ",")


# ---- throughput validation ---------------------------------------------------------------

def norm(rows, **kw):
    from src.config import Config
    config = Config("x", "y", benchmarks=("mb1_rawNoFinalize",))
    return normalize_throughput(pd.DataFrame(rows), config.benchmarks, config.labels, **kw)


def test_blank_strategy_names_the_ledger_row():
    with pytest.raises(ValueError, match=r"Missing strategy at ledger row\(s\) \[3\]"):
        norm([observation(), observation(strategy=" ", rerun="02")])


def test_blank_rerun_names_the_ledger_row():
    with pytest.raises(ValueError, match=r"Missing rerun at ledger row\(s\) \[2\]"):
        norm([observation(rerun="")])


def test_missing_score_column():
    row = observation()
    del row["score"]
    with pytest.raises(ValueError, match=r"Missing ledger column\(s\): \['score'\]"):
        norm([row])


def test_overflow_when_converting_units():
    # 1e306 ops/ms * 1000 = 1e309 > float max
    with pytest.raises(ValueError, match="Overflow normalizing score"):
        norm([observation(score="1e306", score_unit="ops/ms", score_confidence_low="1", score_confidence_high="1e306")])


def test_malformed_duration_in_settings_check():
    rows = [observation(measurement_time="soon"), observation(rerun="02", measurement_time="10 s")]
    with pytest.raises(ValueError, match="mb1_rawNoFinalize: Malformed comparison field measurement_time: soon"):
        norm(rows)


def test_jvm_args_json_that_is_not_a_list_compares_as_text():
    rows = pd.DataFrame([observation(jvm_args='{"a": 1}'), observation(rerun="02", jvm_args='{"a": 2}')])
    with pytest.raises(ValueError, match="Incompatible settings in mb1_rawNoFinalize: jvm_args"):
        norm(rows.to_dict("records"))
    same = pd.DataFrame([observation(jvm_args="7"), observation(rerun="02", jvm_args="7")])
    norm(same.to_dict("records"))  # equal text passes; a scalar JSON value has no flags to look for


# ---- numeric helpers (expected values from closed forms) -----------------------------------

def test_t_quantile_df2_closed_form_and_symmetry():
    # df = 2: t = (2p - 1) / sqrt(2 p (1 - p))
    p = 0.975
    assert t_quantile(p, 2) == pytest.approx((2 * p - 1) / math.sqrt(2 * p * (1 - p)), rel=1e-9)
    assert t_quantile(0.5, 7) == 0.0
    assert t_quantile(0.025, 2) == pytest.approx(-t_quantile(0.975, 2))
    assert t_quantile(0.975, 9) == pytest.approx(2.262157, abs=1e-6)  # textbook table value


@pytest.mark.parametrize("p", [0, 1, -0.1, 1.5])
def test_t_quantile_rejects_probabilities_outside_0_1(p):
    with pytest.raises(ValueError, match="probability must be between 0 and 1"):
        t_quantile(p, 5)


def test_t_interval_literal():
    # [10, 20, 30]: mean 20, sd 10, 4.302653 * 10 / sqrt(3) = 24.841377
    low, high = t_interval([10, 20, 30])
    assert (low, high) == pytest.approx((-4.841377, 44.841377), abs=1e-5)


def test_fieller_none_when_base_mean_indistinguishable_from_zero():
    assert fieller_interval([200, 300, 600], [100, 200, 300]) is None  # a = 200^2 - 18.5 * 10000 < 0


def test_fieller_degenerate_perfect_correlation_collapses_to_the_ratio():
    # s = 2 b exactly: the interval is the single ratio 2
    low, high = fieller_interval([2000, 2200, 2400], [1000, 1100, 1200])
    assert (low, high) == pytest.approx((2.0, 2.0), abs=1e-9)


@pytest.mark.parametrize("values,expected", [
    ([5], False), ([500, 500, 500], False), ([500, 500 + 1e-9 * 500], False),
    ([1, 2], True), ([100, 100.001], True),
])
def test_has_spread(values, expected):
    assert bool(has_spread(values)) is expected


def test_warn_incomplete_names_each_missing_rerun():
    group = pd.DataFrame({"rerun": ["01", "03"]})
    with pytest.warns(UserWarning) as record:
        warn_incomplete("mb1_rawNoFinalize", "2b", 4, group, ["01", "02", "03", "04"])
    assert [str(w.message) for w in record] == ["mb1_rawNoFinalize/2b_n04_r02/thrpt missing.",
                                                 "mb1_rawNoFinalize/2b_n04_r04/thrpt missing."]


# ---- speedup and gc branches -----------------------------------------------------------------

def test_speedup_group_with_nonfinite_mean_ratio_fails_naming_rows():
    from src.config import Config
    config = Config("x", "y", benchmarks=("mb1_rawNoFinalize",))
    tiny = 5e-324  # smallest positive float: half of it underflows to 0, so the group ratio is 0/0
    rows = [observation(strategy=s, rerun=r, score=v) for r in ("01", "02")
            for s, v in (("base", tiny), ("1a", 1e-323))]
    with pytest.raises(ValueError, match=r"Nonfinite speedup at ledger row\(s\) \[2, 4\]"):
        speedup_prepare(pd.DataFrame(rows), config)


def test_gc_malformed_measurement_time_is_stopped_by_the_settings_check():
    # The gc parser has its own "Malformed measurement_time" branch, but the shared settings check
    # on the same field always runs first, so this is the message a user can see.
    from src.config import Config
    config = Config("x", "y", benchmarks=("mb1_rawNoFinalize",))
    rows = [gc_observation(), gc_observation(rerun="02", measurement_time="soon")]
    with pytest.raises(ValueError, match="Malformed comparison field measurement_time: soon"):
        gc_prepare(pd.DataFrame(rows), config)


# ---- all four commands: lifecycle and failure exit (spec: Output lifecycle, Compact run folders) ----

from audit_support import run_main, write_run
from src.cli import compare_main, gc_main, main as thrpt_main, speedup_main

COMMANDS = [(thrpt_main, "thrpt"), (speedup_main, "speedup"), (gc_main, "gc_pauses"),
            (compare_main, "compare")]


def _all_feature_rows():
    return [gc_observation(strategy=s, rerun=r, score=100 + 10 * i + 5 * j, run_timestamp=f"2026-01-0{i + 1}")
            for i, r in enumerate(("01", "02")) for j, s in enumerate(("base", "2a", "2b"))]


@pytest.mark.parametrize("command,feature", COMMANDS)
def test_run_twice_gives_two_folders_named_by_feature(tmp_path, capsys, command, feature):
    cfg = write_run(tmp_path, _all_feature_rows(), margin=0.05)
    first = run_main(command, cfg, capsys)
    second = run_main(command, cfg, capsys)
    assert first[0] == second[0] == 0
    folders = sorted((tmp_path / "out").iterdir())
    assert len(folders) == 2 and all(f.name.endswith(f"_{feature}") for f in folders)
    one, two = {Path(p).parent for p in first[1]}, {Path(p).parent for p in second[1]}
    assert len(one) == len(two) == 1 and one != two     # each run wrote into a single folder of its own
    assert one | two == set(folders)
    assert not set(first[1]) & set(second[1])  # no path is shared between the two invocations


@pytest.mark.parametrize("command,feature", COMMANDS)
def test_missing_ledger_exits_1_with_prefixed_message_and_no_output(tmp_path, capsys, command, feature):
    cfg = write_run(tmp_path, _all_feature_rows())
    (tmp_path / "ledger.csv").unlink()
    code, printed, _, err = run_main(command, cfg, capsys)
    name = {"gc_pauses": "gc"}.get(feature, feature)
    assert code == 1 and printed == []
    assert err.startswith(f"{name}: ") and "ledger.csv" in err
    assert not (tmp_path / "out").exists()
