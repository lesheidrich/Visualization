"""Run folder and file names, checked against literal expected names."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import re

import pandas as pd
import pytest

from src import gc_metrics, speedup, thrpt, utils
from src.config import load_config
from src.utils import file_names, output_directory, output_stem, rerun_part, safe_name

ROOT = Path(__file__).parents[1]
RERUNS = [f"{n:02d}" for n in range(1, 11)]
BENCHMARKS = ("mb1_rawNoFinalize", "mb2_rawFinalize")
GC_PIVOTS = ("gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct",
             "pauses_max", "pauses_p99", "batch_fill")


class FrozenClock:
    """Stands in for datetime in utils, returning the given UTC times in order."""

    def __init__(self, *moments):
        self.moments = list(moments)

    def now(self, tz):
        assert tz is timezone.utc
        return self.moments.pop(0)


def at(second):
    return datetime(2026, 11, 1, 14, 50, second, 123456, tzinfo=timezone.utc)


@pytest.mark.parametrize("identifier, expected", [
    ("mb1_rawNoFinalize", "mb1_rawNoFinalize"),
    ("01", "01"),
    (5, "5"),
    ("instance-3", "instance-3"),
    ("r/1", "r_1"),
    ("r?1", "r_1"),
    ("../unsafe", "unsafe"),
    ("a b.c", "a_b_c"),
    ("///", "value"),
    ("", "value"),
    ("x" * 80, "x" * 80),
])
def test_safe_name_literals(identifier, expected):
    assert safe_name(identifier) == expected


@pytest.mark.parametrize("feature, benchmark, part, expected", [
    ("thrpt", "mb1_rawNoFinalize", "avg", "thrpt_mb1_rawNoFinalize_avg"),
    ("speedup", "mb2_rawFinalize", "r05", "speedup_mb2_rawFinalize_r05"),
    ("gc", "mb1_rawNoFinalize", "r_all", "gc_mb1_rawNoFinalize_r_all"),
])
def test_output_stem_literals(feature, benchmark, part, expected):
    assert output_stem(feature, benchmark, part) == expected


def test_rerun_part_keeps_the_ledger_zero_padding():
    assert rerun_part("05") == "r05"
    assert rerun_part("10") == "r10"


def test_file_names_maps_each_identifier():
    assert file_names(["01", "r/1"], "rerun") == {"01": "01", "r/1": "r_1"}


def test_file_names_accepts_a_repeated_identifier():
    assert file_names(["01", "01"], "rerun") == {"01": "01"}


@pytest.mark.parametrize("first, second", [("r/1", "r?1"), ("R1", "r1"), ("a.b", "a_b")])
def test_file_names_refuses_a_shared_name(first, second):
    with pytest.raises(ValueError) as caught:
        file_names([first, second], "rerun")
    assert repr(first) in str(caught.value) and repr(second) in str(caught.value)


def test_folder_is_named_to_the_second(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "datetime", FrozenClock(at(47)))
    directory = output_directory(tmp_path)
    assert directory == tmp_path / "2026-11-01_14-50-47"
    assert directory.is_dir()


def test_same_second_waits_and_leaves_the_existing_folder_alone(tmp_path, monkeypatch):
    existing = tmp_path / "2026-11-01_14-50-47"
    existing.mkdir()
    (existing / "keep.csv").write_text("kept")
    monkeypatch.setattr(utils, "datetime", FrozenClock(at(47), at(47), at(48)))
    sleeps = []
    monkeypatch.setattr(utils.time, "sleep", sleeps.append)
    directory = output_directory(tmp_path)
    assert directory == tmp_path / "2026-11-01_14-50-48"
    assert sleeps == [0.05, 0.05]
    assert (existing / "keep.csv").read_text() == "kept"
    assert list(directory.iterdir()) == []


def test_folder_parents_are_created(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "datetime", FrozenClock(at(1)))
    assert output_directory(tmp_path / "a" / "b") == tmp_path / "a" / "b" / "2026-11-01_14-50-01"


@pytest.fixture(scope="module")
def sample_outputs(tmp_path_factory):
    config = load_config(ROOT / "config.yaml")
    output = tmp_path_factory.mktemp("sample")
    config = replace(config, output=output)
    return {feature.__name__.split(".")[-1]: feature.run(config)
            for feature in (thrpt, speedup, gc_metrics)}


def per_rerun_names(feature):
    return {f"{feature}_{benchmark}_{part}.{extension}" for benchmark in BENCHMARKS
            for part in [f"r{rerun}" for rerun in RERUNS] + ["avg"]
            for extension in ("csv", "png")}


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_throughput_names_on_the_real_sample(sample_outputs):
    assert {p.name for p in sample_outputs["thrpt"]} == per_rerun_names("thrpt")


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_speedup_names_on_the_real_sample(sample_outputs):
    assert {p.name for p in sample_outputs["speedup"]} == per_rerun_names("speedup")


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_gc_names_on_the_real_sample(sample_outputs):
    expected = set()
    for benchmark in BENCHMARKS:
        expected |= {f"gc_{benchmark}_{part}.csv" for part in
                     ["r_all", "avg", "pauses_max_median"] + [f"{m}_pivot" for m in GC_PIVOTS]}
        expected.add(f"gc_{benchmark}_pauses_max.png")
    assert {p.name for p in sample_outputs["gc_metrics"]} == expected


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_every_feature_uses_a_short_folder_and_no_digest(sample_outputs):
    for paths in sample_outputs.values():
        assert len({p.parent for p in paths}) == 1
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", paths[0].parent.name)
        assert all(p.is_file() and p.stat().st_size > 0 for p in paths)
        assert not any(re.search(r"[0-9a-f]{16}", p.name) for p in paths)
        assert not any("average" in p.name or "rerun" in p.name for p in paths)


@pytest.mark.parametrize("feature", [thrpt, speedup, gc_metrics])
def test_colliding_benchmarks_stop_before_any_output(feature, tmp_path):
    config = load_config(ROOT / "config.yaml")
    config = replace(config, output=tmp_path / "out", benchmarks=("mb1_rawNoFinalize", "mb1.rawNoFinalize"))
    frame = feature.load_ledger(config.ledger, config.delimiter)
    twin = frame[frame["benchmark_short"].eq("mb1_rawNoFinalize")].assign(benchmark_short="mb1.rawNoFinalize")
    ledger = tmp_path / "ledger.csv"
    pd.concat([frame, twin]).rename(columns={"strategy": ""}).to_csv(ledger, index=False)
    with pytest.raises(ValueError, match="would share the file name"):
        feature.run(replace(config, ledger=ledger))
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("feature", [thrpt, speedup])
def test_colliding_reruns_stop_before_any_output(feature, tmp_path):
    config = load_config(ROOT / "config.yaml")
    config = replace(config, output=tmp_path / "out")
    frame = feature.load_ledger(config.ledger, config.delimiter)
    frame["rerun"] = frame["rerun"].replace({"01": "a/1", "02": "a?1"})
    ledger = tmp_path / "ledger.csv"
    frame.rename(columns={"strategy": ""}).to_csv(ledger, index=False)
    with pytest.raises(ValueError, match="'a/1' and 'a\\?1'"):
        feature.run(replace(config, ledger=ledger))
    assert not (tmp_path / "out").exists()
