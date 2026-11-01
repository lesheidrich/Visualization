import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import yaml

from conftest import observation


def invoke(config_path, cwd):
    return subprocess.run([str(Path(sys.executable).with_name("thrpt")), "--config", str(config_path)],
                          cwd=cwd, capture_output=True, text=True, env={**os.environ, "MPLBACKEND": "Agg"})


def test_cli_ten_reruns_two_benchmarks_other_directory_and_repeat(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reruns = ["r/1", "r?2"] + [f"instance-{n}" for n in range(2, 10)]
    benchmarks = ["mb1_rawNoFinalize", "mb2_rawFinalize"]
    rows = [observation(rerun=rerun, benchmark_short=benchmark, score=(i + 1) * 100)
            for benchmark in benchmarks for i, rerun in enumerate(reruns)]
    pd.DataFrame(rows).rename(columns={"strategy": ""}).to_csv(project / "input.csv", index=False)
    config = project / "config.yaml"
    config.write_text(yaml.safe_dump({"input": {"path": "input.csv"},
                                     "output": {"directory": "out"}, "benchmarks": benchmarks,
                                     "plot": {"figsize": [4, 3], "dpi": 30}}))
    runs = []
    for _ in range(2):
        result = invoke(config, tmp_path)
        assert result.returncode == 0, result.stderr
        paths = [Path(line) for line in result.stdout.splitlines()]
        assert len(paths) == 44
        assert all(p.is_file() and p.stat().st_size > 0 for p in paths)
        assert len([p for p in paths if p.suffix == ".png"]) == 22
        for average in (p for p in paths if p.name.endswith("_avg.csv")):
            table = pd.read_csv(average)
            assert table["score"].iloc[0] == 550
            assert table["n_reruns"].iloc[0] == 10
            assert "score_confidence_low" not in table.columns
            assert table["ci_method"].iloc[0] == "95% percentile bootstrap of reruns"
            assert table["ci_low"].iloc[0] < 550 < table["ci_high"].iloc[0]
            assert json.loads(table["contributing_reruns"].iloc[0]) == reruns
        runs.append(set(paths))
    assert runs[0].isdisjoint(runs[1])
    assert len(list((project / "out").iterdir())) == 2


def test_cli_failure_is_actionable_without_traceback(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("input:\n  path: missing.csv\n")
    result = invoke(config, tmp_path)
    assert result.returncode == 1
    assert "missing.csv" in result.stderr
    assert "Traceback" not in result.stderr
