"""Installed `gc` command: two benchmarks, ten opaque reruns, another cwd, run twice."""
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import yaml

from conftest import gc_observation

METRICS = ["gc_alloc_rate_norm", "gc_count", "gc_time", "gc_time_pct", "pauses_max", "pauses_p99",
           "batch_fill"]
BENCHMARKS = ["mb1_rawNoFinalize", "mb2_rawFinalize"]
RERUNS = ["r/1", "r?2"] + [f"instance-{n}" for n in range(2, 10)]


def invoke(name, config_path, cwd):
    return subprocess.run([str(Path(sys.executable).with_name(name)), "--config", str(config_path)],
                          cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, "MPLBACKEND": "Agg"})


def test_two_benchmarks_ten_reruns_other_cwd_run_twice(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    rows = []
    for benchmark in BENCHMARKS:
        for i, rerun in enumerate(RERUNS):
            n = i + 1                                   # 1..10
            for strategy, threads, factor in (("base", 1, 1), ("1a", 2, 2)):
                rows.append(gc_observation(
                    benchmark_short=benchmark, strategy=strategy, rerun=rerun, threads=threads,
                    score=100 * n * factor,
                    gc_alloc_rate_norm=str(1000 * n * factor),
                    gc_count=str(n * factor),
                    gc_time=str(250 * n * factor),     # pct = 250*n*f / 250000 * 100 = 0.1*n*f
                    pauses_max=str(n * factor),
                    pauses_p99=str(n * factor),
                    aux_recordBytes=str(Decimal("167.77216") * n * factor)))  # fill = 0.1*n*f
    pd.DataFrame(rows).to_csv(project / "input.csv", index=False)
    config = project / "config.yaml"
    config.write_text(yaml.safe_dump({"input": {"path": "input.csv"}, "output": {"directory": "out"},
                                     "benchmarks": BENCHMARKS, "plot": {"figsize": [4, 3], "dpi": 30}}))
    other = tmp_path / "elsewhere"
    other.mkdir()

    runs = []
    for _ in range(2):
        result = invoke("gc", config, other)
        assert result.returncode == 0, result.stderr
        paths = [Path(line) for line in result.stdout.splitlines()]
        assert len(paths) == 24                                         # (10 CSVs + 2 PNGs) x 2 benchmarks
        assert len(set(paths)) == 24
        assert sum(p.suffix == ".csv" for p in paths) == 20
        assert sum(p.suffix == ".png" for p in paths) == 4
        assert all(path.is_file() and path.stat().st_size > 0 for path in paths)
        assert len({path.parent for path in paths}) == 1               # one new directory per call
        assert project / "out" in paths[0].parents
        for benchmark in BENCHMARKS:
            mine = [p for p in paths if p.name.startswith("gc_")
                    and p.name.split("_")[1:3] == benchmark.split("_")[:2]]
            assert len(mine) == 12
            assert sum(p.name.endswith("_pauses_max_median.csv") for p in mine) == 1
            png = [p for p in mine if p.suffix == ".png"]
            assert sorted(p.name for p in png) == sorted(
                [f"gc_{benchmark}_gc_alloc_rate_norm.png", f"gc_{benchmark}_pauses_max.png"])
            assert all(p.read_bytes()[:4] == b"\x89PNG" for p in png)
            assert sum(p.name.endswith("_r_all.csv") for p in mine) == 1
            assert sum(p.name.endswith("_avg.csv") for p in mine) == 1
            assert sum(p.name.endswith("_pivot.csv") for p in mine) == 7
            for metric in METRICS:
                assert sum(p.name.endswith(f"_{metric}_pivot.csv") for p in mine) == 1
            reruns = pd.read_csv(next(p for p in mine if p.name.endswith("_r_all.csv")),
                                 dtype={"rerun": str})
            average = pd.read_csv(next(p for p in mine if p.name.endswith("_avg.csv")))
            assert len(reruns) == 20
            assert set(reruns["rerun"]) == set(RERUNS)                  # every rerun contributes
            for strategy, factor in (("base", 1), ("1a", 2)):
                row = average[average["strategy"].eq(strategy)].iloc[0]
                assert row["n_reruns"] == row["total_reruns"] == 10
                assert sorted(json.loads(row["contributing_reruns"])) == sorted(RERUNS)
                assert len(json.loads(row["source_rows"])) == 10
                # Means of n = 1..10 are 5.5.
                assert row["score"] == 550 * factor                     # 100*5.5*f
                assert abs(row["gc_alloc_rate_norm"] - 5500 * factor) < 1e-6  # 1000*5.5*f
                assert abs(row["gc_count"] - 5.5 * factor) < 1e-9       # 5.5*f
                assert abs(row["gc_time"] - 1375 * factor) < 1e-6       # 250*5.5*f
                assert abs(row["gc_time_pct"] - 0.55 * factor) < 1e-9  # 0.1*5.5*f
                assert abs(row["pauses_max"] - 5.5 * factor) < 1e-9
                assert abs(row["pauses_p99"] - 5.5 * factor) < 1e-9
                assert abs(row["batch_fill"] - 0.55 * factor) < 1e-6   # 5.5*f/10
            median = pd.read_csv(next(p for p in mine if p.name.endswith("_pauses_max_median.csv")))
            assert median["strategy"].tolist() == ["base", "1a"]
            assert median["threads_blobshuffle"].tolist() == [1, 2]
            assert median["pauses_max_median"].tolist() == [5.5, 11.0]  # (5+6)/2 of 1..10; 2*5.5
            assert median["n_reruns"].tolist() == median["total_reruns"].tolist() == [10, 10]
            for rerun in RERUNS:                                        # each rerun contributes its own value
                n = RERUNS.index(rerun) + 1
                got = reruns[reruns["rerun"].eq(rerun) & reruns["strategy"].eq("base")].iloc[0]
                assert got["gc_alloc_rate_norm"] == 1000 * n
        runs.append(set(paths))
    assert runs[0].isdisjoint(runs[1])                                  # second call overwrote nothing
    assert len(list((project / "out").iterdir())) == 2
