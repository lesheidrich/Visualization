"""Installed-command integration with traceable paired inputs."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest
import yaml

from conftest import observation


def invoke(name, config_path, cwd):
    return subprocess.run([str(Path(sys.executable).with_name(name)), "--config", str(config_path)],
                          cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, "MPLBACKEND": "Agg"})


def test_two_benchmarks_ten_reruns_other_cwd_repeat_and_pairing(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reruns = ["r/1", "r?2"] + [f"instance-{n}" for n in range(2, 10)]
    benchmarks = ["mb1_rawNoFinalize", "mb2_rawFinalize"]
    rows = [row for benchmark in benchmarks for i, rerun in enumerate(reruns)
            for row in [observation(rerun=rerun, benchmark_short=benchmark, score=100 * (i + 1)),
                        observation("1a", rerun, 2, 100 * (i + 1) * (i + 2), benchmark_short=benchmark)]]
    pd.DataFrame(rows).rename(columns={"strategy": ""}).to_csv(project / "input.csv", index=False)
    config = project / "config.yaml"
    config.write_text(yaml.safe_dump({"input": {"path": "input.csv"},
                                     "output": {"directory": "out"}, "benchmarks": benchmarks,
                                     "plot": {"figsize": [4, 3], "dpi": 30}}))
    runs = []
    for _ in range(2):
        result = invoke("speedup", config, tmp_path)
        assert result.returncode == 0, result.stderr
        paths = [Path(line) for line in result.stdout.splitlines()]
        assert len(paths) == 44
        assert all(path.is_file() and path.stat().st_size > 0 for path in paths)
        assert sum(path.suffix == ".png" for path in paths) == 22
        assert sum(path.suffix == ".csv" for path in paths) == 22
        for path in (p for p in paths if p.suffix == ".csv"):
            table = pd.read_csv(path, dtype={"rerun": str})
            strategy = table[table.strategy.eq("1a")].iloc[0]
            if path.name.endswith("_avg.csv"):
                assert strategy.speedup == pytest.approx(8.0, rel=1e-12)  # 44000 / 5500
                assert strategy.mean_score == pytest.approx(4400.0)  # 44000 / 10
                assert strategy.mean_baseline_score == pytest.approx(550.0)  # 5500 / 10
                assert strategy.n_reruns == strategy.total_reruns == 10
                assert json.loads(strategy.contributing_reruns) == reruns
                assert len(json.loads(strategy.baseline_source_rows)) == 10
            else:
                index = reruns.index(strategy.rerun)
                assert strategy.speedup == index + 2
                assert strategy.score == 100 * (index + 1) * (index + 2)
                assert strategy.baseline_score == 100 * (index + 1)
                assert strategy.source_row == strategy.baseline_source_row + 1
                assert Path(strategy.baseline_source_file) == project / "input.csv"
        runs.append(set(paths))
    assert runs[0].isdisjoint(runs[1])
    assert len(list((project / "out").iterdir())) == 2


@pytest.mark.parametrize("name", ["thrpt", "speedup"])
def test_harness_invalid_config_and_help(tmp_path, name):
    config = tmp_path / "invalid.yaml"
    config.write_text("input:\n  path: missing.csv\n")
    result = invoke(name, config, tmp_path)
    assert result.returncode == 1
    assert result.stderr.startswith(f"{name}:")
    assert "missing.csv" in result.stderr and "Traceback" not in result.stderr
    result = subprocess.run([str(Path(sys.executable).with_name(name)), "--help"],
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert f"usage: {name}" in result.stdout and "--config" in result.stdout


def test_cli_missing_baseline_creates_no_outputs(tmp_path):
    pd.DataFrame([observation("1a", rerun="unpaired")]).to_csv(tmp_path / "input.csv", index=False)
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"input": {"path": "input.csv"},
                                     "output": {"directory": "out"},
                                     "benchmarks": ["mb1_rawNoFinalize"]}))
    result = invoke("speedup", config, tmp_path)
    assert result.returncode == 1
    assert "speedup: Missing base" in result.stderr and "unpaired" in result.stderr
    assert not (tmp_path / "out").exists()
