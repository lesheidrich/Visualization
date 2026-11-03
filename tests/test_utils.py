import json

import pandas as pd
import pytest

from src.thrpt import prepare
from src.utils import load_ledger, output_directory, strategy_key
from conftest import observation


@pytest.mark.parametrize("sep", [",", "\t"])
def test_text_loading_preserves_opaque_identifiers(tmp_path, sep):
    frame = pd.DataFrame([observation(strategy=" 3.0 ", rerun="NA"),
                          observation(strategy="2a", rerun="01")]).rename(columns={"strategy": ""})
    path = tmp_path / "ledger.csv"
    frame.to_csv(path, sep=sep, index=False)
    loaded = load_ledger(path)
    assert loaded["rerun"].tolist() == ["NA", "01"]
    assert loaded["source_row"].tolist() == [2, 3]
    assert strategy_key(loaded["strategy"].iloc[0]) == "3"
    assert strategy_key(strategy_key(" 3.0 ")) == "3"


def test_ambiguous_strategy(tmp_path):
    path = tmp_path / "ledger.csv"
    path.write_text(",strategy,rerun\nbase,base,1\n")
    with pytest.raises(ValueError, match="Ambiguous strategy"):
        load_ledger(path)


def test_duplicate_column_names_are_not_silently_mangled(tmp_path):
    path = tmp_path / "ledger.csv"
    path.write_text("strategy,score,score\nbase,100,200\n")
    with pytest.raises(ValueError, match="duplicate column"):
        load_ledger(path, delimiter=",")


@pytest.mark.parametrize("unit,factor", [("ops/s", 1), ("ops/ms", 1000), ("ops/us", 1e6),
                                          ("ops/µs", 1e6), ("ops/μs", 1e6)])
def test_unit_conversion_before_averaging(config, unit, factor):
    frame = pd.DataFrame([observation(score=2, score_unit=unit),
                          observation(rerun="B", score=10)])
    observations, averages = prepare(frame, config)
    assert observations["score"].iloc[0] == 2 * factor
    assert averages["score"].iloc[0] == pytest.approx((2 * factor + 10) / 2)
    assert observations["score_confidence_low"].iloc[0] == pytest.approx(1.8 * factor)


@pytest.mark.parametrize("field,value,match", [
    ("score", "bad", "score.*row"), ("score", "", "score.*row"),
    ("score", "inf", "score.*row"), ("score", "NaN", "score.*row"),
    ("score", "-1", "nonnegative"),
    ("threads_blobshuffle", "1.5", "positive integers"),
    ("threads_blobshuffle", "0", "positive integers"),
    ("score_unit", "records/s", "score_unit.*row"),
    ("score_confidence_low", "101", "confidence bounds.*row"),
    ("score_confidence_high", "99", "confidence bounds.*row"),
    ("score_confidence_high", "garbage", "score_confidence_high.*row"),
])
def test_validation(config, ledger, field, value, match):
    ledger.loc[0, field] = value
    with pytest.raises(ValueError, match=match):
        prepare(ledger, config)


def test_no_gc_required_and_integral_thread_spelling(config):
    observations, _ = prepare(pd.DataFrame([observation(threads="2.0")]), config)
    assert observations["threads_blobshuffle"].tolist() == [2]


def test_unknown_strategy_retained(config):
    with pytest.warns(UserWarning, match="Unknown strategies retained"):
        observations, _ = prepare(pd.DataFrame([observation(strategy="future")]), config)
    assert observations["strategy"].tolist() == ["future"]


def test_duplicates_fail(config, ledger):
    with pytest.raises(ValueError, match="Duplicate"):
        prepare(pd.concat([ledger, ledger], ignore_index=True), config)


def test_comparison_settings_and_provenance(config):
    rows = [observation(cfg_valueBytes="1000", measurement_time="10 s", hostname="a", git_sha="1"),
            observation(rerun="B", cfg_valueBytes="1e3", measurement_time="10000 ms", hostname="b", git_sha="2")]
    assert len(prepare(pd.DataFrame(rows), config)[1]) == 1
    rows[1]["cfg_valueBytes"] = "2000"
    with pytest.raises(ValueError, match="Incompatible.*cfg_valueBytes"):
        prepare(pd.DataFrame(rows), config)
    rows[1]["cfg_valueBytes"] = "bad"
    with pytest.raises(ValueError, match="Malformed comparison field cfg_valueBytes"):
        prepare(pd.DataFrame(rows), config)


def _jvm(*flags):
    return json.dumps(["-Xms4g", "-Dbench.partitions=64", *flags])


@pytest.mark.parametrize("spacing", [",", ", "])
def test_jvm_args_flags_that_vary_by_design_do_not_stop_the_command(config, spacing):
    rows = [observation(strategy="2a", threads_blobshuffle="1", jvm_args=_jvm("-Dbench.threads=1")),
            observation(strategy="2a", threads_blobshuffle="8", jvm_args=_jvm("-Dbench.threads=8")),
            observation(strategy="1c", threads_blobshuffle="8",
                        jvm_args=_jvm("-Dbench.threads=8", "-Dbench.partitionBufferBytes=2097152")
                        .replace(", ", spacing))]
    assert len(prepare(pd.DataFrame(rows), config)[1]) == 3


@pytest.mark.parametrize("other", ["-Xmx8g", "-Dbench.partitionsX=1", "-Dbench.threadsX=1"])
def test_jvm_args_other_differences_still_stop_the_command(config, other):
    rows = [observation(strategy="2a", jvm_args=_jvm("-Dbench.threads=1")),
            observation(strategy="2b", jvm_args=_jvm("-Dbench.threads=1", other))]
    with pytest.raises(ValueError, match="Incompatible settings.*jvm_args"):
        prepare(pd.DataFrame(rows), config)


def test_thread_flag_must_match_threads_blobshuffle(config):
    rows = [observation(threads_blobshuffle="1", jvm_args=_jvm("-Dbench.threads=2"))]
    with pytest.raises(ValueError, match="-Dbench.threads differs from threads_blobshuffle"):
        prepare(pd.DataFrame(rows), config)


def test_partition_buffer_must_be_constant_within_a_strategy(config):
    rows = [observation(strategy="1c", rerun="A", jvm_args=_jvm("-Dbench.partitionBufferBytes=1")),
            observation(strategy="1c", rerun="B", jvm_args=_jvm("-Dbench.partitionBufferBytes=2"))]
    with pytest.raises(ValueError, match=r"partitionBufferBytes differs within strategy \['1c'\]"):
        prepare(pd.DataFrame(rows), config)


def test_partition_buffer_on_only_some_reruns_of_a_strategy_stops_the_command(config):
    rows = [observation(strategy="1c", rerun="A", jvm_args=_jvm("-Dbench.partitionBufferBytes=1")),
            observation(strategy="1c", rerun="B", jvm_args=_jvm())]
    with pytest.raises(ValueError, match="Incompatible settings"):
        prepare(pd.DataFrame(rows), config)


def test_jvm_args_that_are_not_a_list_compare_as_text(config):
    rows = [observation(strategy="2a", jvm_args="-Xmx4g"), observation(strategy="2b", jvm_args="-Xmx4g")]
    assert len(prepare(pd.DataFrame(rows), config)[1]) == 2
    rows[1]["jvm_args"] = "-Xmx8g"
    with pytest.raises(ValueError, match="Incompatible settings.*jvm_args"):
        prepare(pd.DataFrame(rows), config)


def test_missing_column(config, ledger):
    with pytest.raises(ValueError, match="Missing ledger column.*score_unit"):
        prepare(ledger.drop(columns="score_unit"), config)


def test_unique_output_names(tmp_path):
    assert output_directory(tmp_path, "thrpt") != output_directory(tmp_path, "thrpt")


def test_threads_jmh_is_not_checked_for_comparability(config):
    rows = [observation(threads_jmh="1"), observation(rerun="B", threads_jmh="8")]
    assert len(prepare(pd.DataFrame(rows), config)[1]) == 1
    rows[1]["threads_jmh"] = "not a number"
    assert len(prepare(pd.DataFrame(rows), config)[1]) == 1
