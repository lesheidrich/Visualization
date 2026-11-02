"""Nonsense-catching invariants for the GC tables (tasks 4.1-4.9).

The oracle below reads the raw ledger CSV with the standard library and recomputes
everything with fractions; nothing from src is used for the calculation itself.
"""
import csv
from dataclasses import replace
from fractions import Fraction
import json
import math
from pathlib import Path
import random
import re
import warnings

import pandas as pd
import pytest

from conftest import gc_observation
from src.config import Config
from src.gc_metrics import AVERAGED, PIVOT_METRICS, pivots, prepare, run

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

MB1, MB2 = "mb1_rawNoFinalize", "mb2_rawFinalize"
RERUNS = ["01", "02", "instance-A", "10"]
SCORE_UNITS = ["ops/s", "ops/ms", "ops/us", "ops/µs"]
TIME_UNITS = ["ms", "us", "s", "µs"]
KEY = ["benchmark_short", "strategy", "threads_blobshuffle"]
RTOL = 1e-9


# ------------------------------------------------------------------ fixture ledger

def make_rows(seed=7):
    """Random but exact-decimal ledger rows for two benchmarks (thrpt only)."""
    rng = random.Random(seed)
    plan = {
        MB1: dict(strategies=["base", "1a", "2a"], threads=[1, 2, 4], gc_max=20000,
                  settings=dict(forks="5", measurement_iterations="5", measurement_time="10 s",
                                cfg_batchDesiredBytes="16777216")),
        MB2: dict(strategies=["base", "1a"], threads=[1, 2], gc_max=5000,
                  settings=dict(forks="3", measurement_iterations="4", measurement_time="500 ms",
                                cfg_batchDesiredBytes="1048576")),
    }
    rows = []
    for bench, spec in plan.items():
        for strategy in spec["strategies"]:
            for threads in spec["threads"]:
                for rerun in RERUNS:
                    if (bench, strategy, threads) == (MB1, "2a", 4):
                        continue                      # a combination that never ran
                    if (bench, strategy, threads, rerun) == (MB1, "1a", 2, "10"):
                        continue                      # incomplete coverage: 3/4
                    unit = rng.choice(TIME_UNITS)
                    rows.append(gc_observation(
                        benchmark_short=bench, strategy=strategy, rerun=rerun, threads=threads,
                        score=str(rng.randint(1, 10**6)), score_unit=rng.choice(SCORE_UNITS),
                        gc_alloc_rate_norm=str(rng.randint(100, 9000)),
                        gc_count=str(rng.randint(1, 300)),
                        gc_time=str(rng.randint(0, spec["gc_max"])),
                        pauses_max=str(rng.randint(1, 2000) / 100),
                        pauses_p99=str(rng.randint(1, 2000) / 100),
                        aux_recordBytes=str(rng.randint(100, 400000) / 100),
                        aux_batches=str(rng.randint(1, 500) / 10000),
                        aux_recordBytes_unit=f"bytes/{unit}", aux_batches_unit=f"batches/{unit}",
                        **spec["settings"]))
    return rows


def distractors():
    """Rows that must never influence the requested thrpt groups (valid, so they parse)."""
    extra = []
    for strategy, threads in (("base", 1), ("1a", 2), ("2a", 1)):
        for rerun in RERUNS:
            extra.append(gc_observation(      # sample row with the same identity, extreme values
                benchmark_short=MB1, strategy=strategy, rerun=rerun, threads=threads,
                mode="sample", gc_time="99999999", pauses_max="1e12", gc_count="1e9",
                aux_batches="0", aux_recordBytes_unit="garbage", forks="1", measurement_time="1 h"))
    for rerun in RERUNS:
        extra.append(gc_observation(          # thrpt of a benchmark nobody requested, other settings
            benchmark_short="mb3_other", rerun=rerun, gc_time="1e15", pauses_max="1e15",
            forks="77", measurement_time="3 h", cfg_batchDesiredBytes="3"))
    return extra


def write_ledger(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def make_config(tmp_path, ledger, **changes):
    base = Config(ledger, tmp_path / "out", benchmarks=(MB1, MB2), figsize=(5, 3), dpi=40)
    return replace(base, **changes)


def execute(config):
    """Run the command and load every written CSV: bench -> reruns / average / pivots."""
    paths = run(config)
    assert len(paths) == (10 + 2 * len(config.formats)) * len(config.benchmarks)  # 10 CSVs + two figures per format
    tables = {}
    for bench in config.benchmarks:
        stem = f"gc_{bench}_"
        mine = {p.name[len(stem):]: p for p in paths if p.name.startswith(stem)}
        assert len(mine) == 10 + 2 * len(config.formats), sorted(mine)  # 10 CSVs + two figures per format
        tables[bench] = {
            "reruns": pd.read_csv(mine["r_all.csv"], dtype={"rerun": str, "strategy": str}),
            "average": pd.read_csv(mine["avg.csv"], dtype={"strategy": str}),
            "pivots": {m: pd.read_csv(mine[f"{m}_pivot.csv"], index_col="strategy",
                                      dtype={"strategy": str}) for m in PIVOT_METRICS},
        }
    return tables


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_world")
    rows = make_rows() + distractors()
    ledger = write_ledger(tmp / "ledger.csv", rows)
    config = make_config(tmp, ledger)
    return {"ledger": ledger, "config": config, "tables": execute(config), "tmp": tmp}


# ------------------------------------------------------------------ plain-python oracle

DURATION_FACTORS = {"ns": Fraction(1, 10**9), "us": Fraction(1, 10**6), "µs": Fraction(1, 10**6),
                    "μs": Fraction(1, 10**6), "ms": Fraction(1, 1000), "s": Fraction(1),
                    "m": Fraction(60), "h": Fraction(3600), None: Fraction(1)}
SCORE_FACTORS = {"ops/s": 1, "ops/ms": 1000, "ops/us": 10**6, "ops/µs": 10**6}


def raw_ledger(path):
    with open(path, encoding="utf-8", newline="") as stream:
        return [{**row, "row": index + 2} for index, row in enumerate(csv.DictReader(stream))]


def seconds(text):
    match = re.fullmatch(r"\s*([0-9.]+)\s*(ns|µs|μs|us|ms|s|m|h)?\s*", text)
    return Fraction(match[1]) * DURATION_FACTORS[match[2]]


def oracle(path, benchmarks):
    """(per_rerun, averages, totals) computed from the raw rows with Fractions only."""
    per_rerun = {}
    for row in raw_ledger(path):
        if row["mode"] != "thrpt" or row["benchmark_short"] not in benchmarks:
            continue
        window_ms = Fraction(row["forks"]) * Fraction(row["measurement_iterations"]) \
            * seconds(row["measurement_time"]) * 1000
        batch_bytes = Fraction(row["aux_recordBytes"]) / Fraction(row["aux_batches"])
        values = {
            "score": Fraction(row["score"]) * SCORE_FACTORS[row["score_unit"]],
            "gc_alloc_rate_norm": Fraction(row["gc_alloc_rate_norm"]),
            "gc_count": Fraction(row["gc_count"]),
            "gc_time": Fraction(row["gc_time"]),
            "gc_time_pct": Fraction(row["gc_time"]) / window_ms * 100,
            "pauses_max": Fraction(row["pauses_max"]),
            "pauses_p99": Fraction(row["pauses_p99"]),
            "batch_bytes": batch_bytes,
            "batch_fill": batch_bytes / Fraction(row["cfg_batchDesiredBytes"]),
        }
        key = (row["benchmark_short"], row["strategy"].strip().lower(),
               int(row["threads_blobshuffle"]), row["rerun"])
        assert key not in per_rerun
        per_rerun[key] = {**values, "row": row["row"]}
    groups = {}
    for (bench, strategy, threads, rerun), values in per_rerun.items():
        groups.setdefault((bench, strategy, threads), {})[rerun] = values
    averages = {
        group: {metric: sum((v[metric] for v in members.values()), Fraction(0)) / len(members)
                for metric in AVERAGED}
        | {"reruns": set(members), "rows": {v["row"] for v in members.values()}}
        for group, members in groups.items()}
    totals = {bench: len({k[3] for k in per_rerun if k[0] == bench}) for bench in benchmarks}
    return per_rerun, averages, totals


# ------------------------------------------------------------------ 4.1 oracle

def test_oracle_agrees_with_every_per_rerun_and_average_value(world):
    config = world["config"]
    per_rerun, averages, totals = oracle(world["ledger"], config.benchmarks)
    seen_rows = seen_averages = 0
    for bench, tables in world["tables"].items():
        for _, row in tables["reruns"].iterrows():
            expected = per_rerun[(bench, row["strategy"], int(row["threads_blobshuffle"]), row["rerun"])]
            seen_rows += 1
            for metric in AVERAGED:
                assert row[metric] == pytest.approx(float(expected[metric]), rel=RTOL, abs=1e-12), \
                    (bench, row["strategy"], row["threads_blobshuffle"], row["rerun"], metric)
            assert row["source_row"] == expected["row"]
            assert row["total_reruns"] == totals[bench] and row["n_reruns"] == 1
        for _, row in tables["average"].iterrows():
            group = (bench, row["strategy"], int(row["threads_blobshuffle"]))
            expected = averages[group]
            seen_averages += 1
            for metric in AVERAGED:
                assert row[metric] == pytest.approx(float(expected[metric]), rel=RTOL, abs=1e-12), \
                    (group, metric)
            assert row["n_reruns"] == len(expected["reruns"])
            assert row["total_reruns"] == totals[bench]
            assert set(json.loads(row["contributing_reruns"])) == expected["reruns"]
            assert set(json.loads(row["source_rows"])) == expected["rows"]
    assert seen_rows == len(per_rerun) and seen_averages == len(averages)


def test_oracle_matches_math_fsum_of_the_per_rerun_csv(world):
    """The averages are plain means of the rows printed in the per-rerun CSV."""
    for tables in world["tables"].values():
        for _, row in tables["average"].iterrows():
            group = tables["reruns"][(tables["reruns"]["strategy"] == row["strategy"])
                                     & (tables["reruns"]["threads_blobshuffle"] == row["threads_blobshuffle"])]
            for metric in AVERAGED:
                mean = math.fsum(group[metric]) / len(group)
                assert row[metric] == pytest.approx(mean, rel=RTOL, abs=1e-12)


# ------------------------------------------------------------------ 4.2 no lost or doubled rows

def test_one_row_per_thrpt_ledger_row_and_each_source_row_once(world):
    config = world["config"]
    raw = raw_ledger(world["ledger"])
    selected = [r for r in raw if r["mode"] == "thrpt" and r["benchmark_short"] in config.benchmarks]
    assert len(selected) == 47 and len(raw) > len(selected)       # fixture sanity: distractors exist
    for bench in config.benchmarks:
        expected_rows = sorted(r["row"] for r in selected if r["benchmark_short"] == bench)
        table = world["tables"][bench]["reruns"]
        assert sorted(table["source_row"].tolist()) == expected_rows
        assert table["source_row"].is_unique
        by_row = {r["row"]: r for r in raw}
        for _, out in table.iterrows():      # and every source_row points at its own ledger row
            source = by_row[out["source_row"]]
            assert source["benchmark_short"] == bench and source["mode"] == "thrpt"
            assert source["strategy"] == out["strategy"] and source["rerun"] == out["rerun"]
            assert int(source["threads_blobshuffle"]) == out["threads_blobshuffle"]


# ------------------------------------------------------------------ 4.3 average sanity

def test_average_between_min_and_max_and_counts_match(world):
    for tables in world["tables"].values():
        reruns, average = tables["reruns"], tables["average"]
        assert average[KEY].duplicated().sum() == 0
        for _, row in average.iterrows():
            group = reruns[(reruns["strategy"] == row["strategy"])
                           & (reruns["threads_blobshuffle"] == row["threads_blobshuffle"])]
            for metric in AVERAGED:
                low, high = group[metric].min(), group[metric].max()
                tolerance = 1e-9 * max(abs(low), abs(high), 1)
                assert low - tolerance <= row[metric] <= high + tolerance, (row["strategy"], metric)
            listed = json.loads(row["contributing_reruns"])
            assert row["n_reruns"] == len(listed) == len(set(listed)) == len(group)
            assert len(json.loads(row["source_rows"])) == row["n_reruns"]
            assert row["n_reruns"] <= row["total_reruns"]


# ------------------------------------------------------------------ 4.4 pivot = average

@pytest.mark.parametrize("metric", PIVOT_METRICS)
def test_every_pivot_cell_equals_the_average_table(world, metric):
    for bench, tables in world["tables"].items():
        pivot, average = tables["pivots"][metric], tables["average"]
        assert pivot.index.name == "strategy"
        assert set(pivot.index) == set(average["strategy"])
        assert [int(c) for c in pivot.columns] == sorted(set(average["threads_blobshuffle"]))
        present = set()
        for _, row in average.iterrows():
            present.add((row["strategy"], str(row["threads_blobshuffle"])))
            assert pivot.loc[row["strategy"], str(row["threads_blobshuffle"])] == \
                pytest.approx(row[metric], rel=RTOL, abs=1e-12)
        for strategy in pivot.index:                 # absent combinations are empty, never filled
            for column in pivot.columns:
                if (strategy, column) not in present:
                    assert pd.isna(pivot.loc[strategy, column])


def test_pivot_dimensions_differ_per_benchmark(world):
    mb1, mb2 = (world["tables"][b]["pivots"]["gc_count"] for b in (MB1, MB2))
    assert set(mb1.index) == {"base", "1a", "2a"} and list(mb1.columns) == ["1", "2", "4"]
    assert set(mb2.index) == {"base", "1a"} and list(mb2.columns) == ["1", "2"]
    assert pd.isna(mb1.loc["2a", "4"])


# ------------------------------------------------------------------ 4.5 row order does not matter

def normalized(tables):
    reruns = tables["reruns"].drop(columns=["source_row", "source_file"]) \
        .sort_values(KEY + ["rerun"]).reset_index(drop=True)
    average = tables["average"].drop(columns=["source_rows", "source_files"]) \
        .sort_values(KEY).reset_index(drop=True)
    for column in ("contributing_reruns", "original_strategies"):
        average[column] = average[column].map(lambda text: sorted(json.loads(text)))
    return reruns, average


def assert_same(left, right):
    for bench in left:
        reruns_l, average_l = normalized(left[bench])
        reruns_r, average_r = normalized(right[bench])
        pd.testing.assert_frame_equal(reruns_l, reruns_r, check_exact=False, rtol=1e-12)
        pd.testing.assert_frame_equal(average_l, average_r, check_exact=False, rtol=1e-12)
        for metric in PIVOT_METRICS:
            pd.testing.assert_frame_equal(left[bench]["pivots"][metric], right[bench]["pivots"][metric],
                                          check_exact=False, rtol=1e-12)


def test_shuffled_ledger_gives_the_same_values(world, tmp_path):
    rows = make_rows() + distractors()
    random.Random(99).shuffle(rows)
    assert rows != make_rows() + distractors()
    shuffled = write_ledger(tmp_path / "shuffled.csv", rows)
    other = execute(make_config(tmp_path, shuffled))
    assert_same(world["tables"], other)
    for bench in other:                                # source_row still tracks the shuffled file
        by_row = {r["row"]: r for r in raw_ledger(shuffled)}
        for _, out in other[bench]["reruns"].iterrows():
            assert by_row[out["source_row"]]["rerun"] == out["rerun"]
            assert by_row[out["source_row"]]["strategy"] == out["strategy"]


# ------------------------------------------------------------------ 4.6 no leaking between groups

def test_other_benchmarks_threads_and_sample_rows_do_not_change_the_target(world, tmp_path):
    rows = make_rows() + distractors()
    extremes = []
    for strategy in ("base", "1a", "2a"):          # another thread count of the target benchmark
        for rerun in RERUNS:
            extremes.append(gc_observation(
                benchmark_short=MB1, strategy=strategy, rerun=rerun, threads=8,
                gc_alloc_rate_norm="1e12", gc_count="1e9", gc_time="1e9", pauses_max="1e9",
                pauses_p99="1e9", aux_recordBytes="1e12", aux_batches="1e-3", score="1e12",
                forks="5", measurement_iterations="5", measurement_time="10 s",
                cfg_batchDesiredBytes="16777216"))
    for rerun in RERUNS:                           # garbage in rows that are not selected
        garbage = gc_observation(
            benchmark_short=MB2, strategy="base", rerun=rerun, threads=1, mode="sample",
            gc_time="garbage", gc_count="-5", pauses_max="", forks="", measurement_time="??",
            aux_batches="x")
        garbage["score"] = "bad"
        extremes.append(garbage)
        extremes.append(gc_observation(benchmark_short="mb3_other", rerun=rerun, mode="sample",
                                       gc_time="garbage", forks="-1"))
    perturbed_path = write_ledger(tmp_path / "perturbed.csv", rows + extremes)
    perturbed = execute(make_config(tmp_path, perturbed_path))

    assert 8 in set(perturbed[MB1]["average"]["threads_blobshuffle"])        # the new group exists
    for bench in (MB1, MB2):
        before, after = world["tables"][bench], perturbed[bench]
        kept = lambda frame: frame[frame["threads_blobshuffle"] != 8]
        pd.testing.assert_frame_equal(
            before["reruns"].drop(columns="source_file"),
            kept(after["reruns"]).drop(columns="source_file"), check_exact=False, check_dtype=False, rtol=1e-12)
        pd.testing.assert_frame_equal(
            before["average"].drop(columns="source_files"),
            kept(after["average"]).drop(columns="source_files"), check_exact=False, check_dtype=False, rtol=1e-12)
        for metric in PIVOT_METRICS:
            table = after["pivots"][metric]
            pd.testing.assert_frame_equal(
                before["pivots"][metric],
                table.drop(columns=[c for c in table.columns if c == "8"]),
                check_exact=False, check_dtype=False, rtol=1e-12)
    assert "8" not in perturbed[MB2]["pivots"]["gc_count"].columns


# ------------------------------------------------------------------ 4.7 units cancel

# (record bytes, record unit, batches, batch unit): every row is 838.8608 bytes per 0.0001 batches
# scaled by the unit, i.e. 8388608 bytes per batch, which is exactly half of 16777216.
UNIT_SETS = [
    ("838.8608", "bytes/ms", "0.0001", "batches/ms"),
    ("0.8388608", "bytes/us", "0.0000001", "batches/us"),     # the ms rates divided by 1000
    ("838860.8", "bytes/s", "0.1", "batches/s"),              # the ms rates multiplied by 1000
    ("0.0008388608", "bytes/ns", "0.0000000001", "batches/ns"),
    ("0.8388608", "bytes/µs", "0.0000001", "batches/μs"),     # mixed mu spellings
]


@pytest.mark.parametrize("record, record_unit, batches, batches_unit", UNIT_SETS)
def test_equal_rates_in_any_time_unit_give_the_same_fill(config, record, record_unit, batches, batches_unit):
    frame = pd.DataFrame([gc_observation(aux_recordBytes=record, aux_recordBytes_unit=record_unit,
                                         aux_batches=batches, aux_batches_unit=batches_unit)])
    observations, averages = prepare(frame, config)
    assert observations["batch_bytes"].iloc[0] == pytest.approx(8388608, rel=1e-9)   # bytes/batch
    assert observations["batch_fill"].iloc[0] == pytest.approx(0.5, rel=1e-9)        # 8388608 / 16777216
    assert pivots(averages, config)[MB1]["batch_fill"].loc["base", 1] == pytest.approx(0.5, rel=1e-9)


# ------------------------------------------------------------------ 4.8 labels are only labels

def test_labels_and_order_change_layout_but_not_numbers(world, tmp_path):
    relabel = make_config(tmp_path, world["ledger"],
                          labels={"base": "Zeta", "1a": "Alpha", "2a": "Middle"},
                          order=("2a", "1a", "base"),
                          colors={"base": "red", "1a": "blue", "2a": "green"})
    other = execute(relabel)
    for bench in (MB1, MB2):
        before, after = world["tables"][bench], other[bench]
        pd.testing.assert_frame_equal(
            before["reruns"].drop(columns="source_file").sort_values(KEY + ["rerun"]).reset_index(drop=True),
            after["reruns"].drop(columns="source_file").sort_values(KEY + ["rerun"]).reset_index(drop=True),
            check_exact=False, rtol=1e-12)
        pd.testing.assert_frame_equal(
            before["average"].drop(columns="source_files").sort_values(KEY).reset_index(drop=True),
            after["average"].drop(columns="source_files").sort_values(KEY).reset_index(drop=True),
            check_exact=False, rtol=1e-12)
        assert set(after["average"]["strategy"]) <= {"base", "1a", "2a"}          # identifiers, not labels
        for metric in PIVOT_METRICS:
            old, new = before["pivots"][metric], after["pivots"][metric]
            assert new.index.tolist() == [s for s in ("2a", "1a", "base") if s in old.index]
            pd.testing.assert_frame_equal(old.reindex(new.index), new, check_exact=False, rtol=1e-12)


# ------------------------------------------------------------------ 4.9 no silent gaps

WARNING_ROWS = re.compile(r"(?:Blank|Zero) (\w+) at ledger row\(s\) \[([\d, ]*)\]")
SOURCES = {"gc_alloc_rate_norm": ("gc_alloc_rate_norm",), "gc_count": ("gc_count",),
           "gc_time": ("gc_time",), "pauses_max": ("pauses_max",), "pauses_p99": ("pauses_p99",),
           "aux_recordBytes": ("aux_recordBytes",), "aux_batches": ("aux_batches",),
           "gc_time_pct": ("gc_time",), "batch_bytes": ("aux_recordBytes", "aux_batches"),
           "batch_fill": ("aux_recordBytes", "aux_batches")}


def gap_fixture():
    rows = []
    for rerun in ("a", "b", "c"):
        rows.append(gc_observation(rerun=rerun, strategy="base"))
        rows.append(gc_observation(rerun=rerun, strategy="1a"))
    rows[0]["gc_alloc_rate_norm"] = ""                  # base/a
    rows[2]["gc_time"] = ""                             # base/b
    rows[4]["aux_recordBytes"] = ""                     # base/c
    rows[5]["aux_batches"] = ""                         # 1a/c
    rows[1]["aux_batches"] = "0"                        # 1a/a: zero batches
    rows[3]["pauses_max"] = ""                          # 1a/b
    rows[3]["pauses_p99"] = ""
    for index in (1, 3, 5):                             # 1a/*: gc_count blank everywhere
        rows[index]["gc_count"] = ""
    return rows


def test_every_missing_cell_has_a_warning_naming_it():
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        observations, averages = prepare(pd.DataFrame(gap_fixture()), config_for_gaps())
    messages = [str(w.message) for w in seen if issubclass(w.category, UserWarning)]
    flagged = {(column, int(row)) for message in messages
               for column, rows in WARNING_ROWS.findall(message) for row in rows.split(", ") if row}
    assert flagged                                          # the fixture really has gaps
    missing_cells = 0
    for _, row in observations.iterrows():
        for column, sources in SOURCES.items():
            if pd.isna(row[column]):
                missing_cells += 1
                assert any((source, row["source_row"]) in flagged for source in sources), \
                    f"silent gap: {column} at ledger row {row['source_row']}"
    assert missing_cells >= 9
    for _, row in averages.iterrows():                      # and the converse: no phantom warnings
        for metric in AVERAGED:
            text = f"Mean of reruns of {metric} missing for {row['benchmark_short']}/{row['strategy']}/threads={row['threads_blobshuffle']}"
            assert pd.isna(row[metric]) == any(text in message for message in messages), text
    for column, row_number in flagged:                      # every warned cell is really missing
        match = observations[observations["source_row"] == row_number]
        assert not match.empty and (match[column].isna().all() or
                                    match[["batch_bytes", "batch_fill"]].isna().all(axis=None))


def config_for_gaps():
    return Config(Path("unused.csv"), Path("unused-out"), benchmarks=(MB1,), figsize=(5, 3), dpi=40)
