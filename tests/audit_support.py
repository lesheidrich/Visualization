"""Helpers for the coverage-audit tests: write a ledger and config, run a CLI main, read outputs."""
import warnings

import pandas as pd
import yaml


def write_run(tmp_path, rows, benchmarks=("mb1_rawNoFinalize",), formats=("png",), margin=None,
              labels=None):
    """Write ledger.csv (rows in the given order) and config.yaml; return the config path."""
    pd.DataFrame(rows).to_csv(tmp_path / "ledger.csv", index=False)
    settings = {"input": {"path": "ledger.csv"}, "output": {"directory": "out", "formats": list(formats)},
                "benchmarks": list(benchmarks), "plot": {"figsize": [5, 3], "dpi": 40}}
    if margin is not None:
        settings["comparison"] = {"margin": margin}
    if labels:
        settings["strategies"] = {"labels": labels}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(settings))
    return path


def run_main(main, config_path, capsys):
    """Run a CLI main; return (exit code, printed paths, warning messages, stderr)."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        code = main(["--config", str(config_path)])
    out = capsys.readouterr()
    return code, out.out.split(), [str(w.message) for w in caught], out.err


def read_csv(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)
