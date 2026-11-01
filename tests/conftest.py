import os
from pathlib import Path
import tempfile

import pandas as pd
import pytest

os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "thesis-matplotlib"))

from src.config import Config


def observation(strategy="base", rerun="01", threads=1, score=100, **extra):
    return {"strategy": strategy, "rerun": rerun, "benchmark_short": "mb1_rawNoFinalize",
            "mode": "thrpt", "threads_blobshuffle": str(threads), "score": str(score),
            "score_confidence_low": str(float(score) * 0.9),
            "score_confidence_high": str(float(score) * 1.1), "score_unit": "ops/s", **extra}


@pytest.fixture
def config(tmp_path):
    return Config(tmp_path / "ledger.csv", tmp_path / "out",
                  benchmarks=("mb1_rawNoFinalize",), figsize=(5, 3), dpi=40)


@pytest.fixture
def ledger():
    return pd.DataFrame([observation()])


# Valid GC defaults: gc_time_pct = 2500 / (5*5*10*1000) * 100 = 1.0 and
# batch_bytes = 1677.7216 / 0.0001 = 16777216, so batch_fill = 1.0.
GC_DEFAULTS = {
    "gc_alloc_rate_norm": "1000", "gc_count": "10", "gc_time": "2500",
    "pauses_max": "5", "pauses_p99": "2",
    "forks": "5", "measurement_iterations": "5", "measurement_time": "10 s",
    "aux_recordBytes": "1677.7216", "aux_recordBytes_unit": "bytes/ms",
    "aux_batches": "0.0001", "aux_batches_unit": "batches/ms",
    "cfg_batchDesiredBytes": "16777216",
}


def gc_observation(**overrides):
    """`observation(...)` plus valid GC, pause, aux and settings columns."""
    return observation(**{**GC_DEFAULTS, **overrides})
