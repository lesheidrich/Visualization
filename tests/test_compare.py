"""Paired 2A vs 2B comparison: calculation, verdicts, flags, config and command."""
import json
import os
from pathlib import Path
import subprocess
import sys
import warnings

import numpy as np
import pandas as pd
import pytest
import yaml

from conftest import observation
from src.compare import (COLUMNS, decide, order_columns, paired_statistics, prepare, ran_first)
from src.config import Config, load_config

A = [101, 103, 98, 102, 100, 103, 98, 101, 102, 100]
B = [100, 102, 98, 101, 99, 103, 97, 100, 102, 98]
EDGE_A = [105, 97, 103, 98, 97, 101, 98, 100, 97, 103]
EDGE_B = [104, 96, 102, 97, 96, 100, 98, 98, 96, 104]
FAR_A = [99, 100, 104, 104, 100, 99, 99, 104, 100, 99]
FAR_B = [100, 98, 104, 103, 101, 98, 96, 103, 98, 100]
APPROX = dict(abs=5e-6)


def worked(margin=None, a=A, b=B, f=5):
    stats = paired_statistics(a, b, f)
    return stats, decide(stats, margin)


# ---- 1.1 calculation ------------------------------------------------------------------

def test_worked_example_numbers():
    s, _ = worked()
    assert s["ratio_of_means"] == pytest.approx(1.008, **APPROX)
    assert s["logt_ratio"] == pytest.approx(1.008034, **APPROX)
    assert (s["diff_low"], s["diff_high"]) == pytest.approx((1.001464, 1.014647), **APPROX)
    assert (s["equiv_low"], s["equiv_high"]) == pytest.approx((1.002328, 1.013773), **APPROX)
    assert s["diff_level"] == pytest.approx(0.99) and s["equiv_level"] == pytest.approx(0.98)
    assert s["smallest_margin"] == pytest.approx(0.013773, **APPROX)
    assert s["sd_log"] * 100 == pytest.approx(0.636, abs=5e-4)
    assert s["reruns_2a_gt_2b"] == 7


def test_levels_depend_on_family_size():
    assert paired_statistics(A, B, 5)["diff_level"] == pytest.approx(0.99)
    assert paired_statistics(A, B, 2)["diff_level"] == pytest.approx(0.975)
    assert paired_statistics(A, B, 2)["equiv_level"] == pytest.approx(0.95)


def test_edge_and_far_scenarios():
    s, (verdict, _) = worked(None, EDGE_A, EDGE_B)
    assert s["diff_low"] == pytest.approx(1.000039, **APPROX) and verdict == "2A faster"
    s, (verdict, _) = worked(0.02, FAR_A, FAR_B)
    assert s["equiv_high"] == pytest.approx(1.020070, **APPROX) and verdict != "equivalent"


def test_intervals_are_paired_not_independent():
    # Independent scores vary a lot, but the ratios do not: paired interval must be tight.
    a = [100, 200, 300, 400, 500]
    b = [x / 1.01 * (1 + 0.0001 * i) for i, x in enumerate(a)]
    s = paired_statistics(a, b, 1)
    assert s["diff_high"] - s["diff_low"] < 0.001


# ---- pairing failures ------------------------------------------------------------------

def rows(a=A, b=B, threads=(1, 2), benchmark="mb1_rawNoFinalize", strategies=("2a", "2b"), **extra):
    out = []
    for t in threads:
        for i, (x, y) in enumerate(zip(a, b), 1):
            rerun = f"{i:02d}"
            for strategy, score in zip(strategies, (x, y)):
                out.append(observation(strategy, rerun, t, score, benchmark_short=benchmark, **extra))
    return out


def cfg(tmp_path, margin=0.05, benchmarks=("mb1_rawNoFinalize",)):
    return Config(tmp_path / "l.csv", tmp_path / "out", benchmarks=benchmarks, comparison_margin=margin)


def table(rs, tmp_path, margin=0.05):
    return prepare(pd.DataFrame(rs), cfg(tmp_path, margin))["mb1_rawNoFinalize"]


def test_missing_pair_names_benchmark_thread_rerun(tmp_path):
    rs = [r for r in rows() if not (r["strategy"] == "2b" and r["rerun"] == "04" and r["threads_blobshuffle"] == "2")]
    with pytest.raises(ValueError, match=r"mb1_rawNoFinalize.*threads=2.*rerun 04"):
        table(rs, tmp_path)


def test_rerun_missing_from_both_at_one_thread_count(tmp_path):
    rs = [r for r in rows() if not (r["rerun"] == "04" and r["threads_blobshuffle"] == "2")]
    with pytest.raises(ValueError, match=r"threads=2.*rerun 04"):
        table(rs, tmp_path)


def test_absent_strategy_too_few_reruns_and_nonpositive(tmp_path):
    with pytest.raises(ValueError, match="2b"):
        table([r for r in rows() if r["strategy"] == "2a"], tmp_path)
    with pytest.raises(ValueError, match="at least 2 reruns"):
        table(rows(A[:1], B[:1]), tmp_path)
    with pytest.raises(ValueError, match="positive"):
        table(rows([0] + A[1:], B), tmp_path)


def test_warns_when_not_ten_reruns(tmp_path):
    with pytest.warns(UserWarning, match="9 reruns"):
        table(rows(A[:9], B[:9]), tmp_path)


def test_no_margin_warns_once_and_changes_verdict(tmp_path):
    with pytest.warns(UserWarning) as caught:
        t = table(rows(threads=(1, 2, 3, 4, 5)), tmp_path, margin=None)
    assert sum("equivalence cannot be claimed" in str(w.message) for w in caught) == 1
    assert set(t.verdict) == {"2A faster"} and t.margin.isna().all()


# ---- 1.2 verdicts ----------------------------------------------------------------------

def fake(low, high, ratio, equiv_low=None, equiv_high=None, spread=True):
    return {"spread": spread, "diff_low": low, "diff_high": high, "logt_ratio": ratio,
            "equiv_low": equiv_low, "equiv_high": equiv_high}


def test_worked_example_verdict_branches():
    assert worked(0.05)[1] == ("equivalent", "2A 0.8% faster, below margin")
    assert worked(None)[1] == ("2A faster", "")
    assert worked(0.01)[1] == ("2A faster", "size unclear")
    assert worked(0.0001)[1] == ("2A faster", "beyond margin")


def test_each_branch_literal():
    assert decide(fake(1.0, 1.01, 1.005, 1.0, 1.01), 0.05) == ("equivalent", "")
    assert decide(fake(0.98, 0.99, 0.985, 0.97, 0.995), 0.05) == ("equivalent", "2B 1.5% faster, below margin")
    assert decide(fake(0.9, 1.1, 1.0, 0.8, 1.2), 0.05) == ("inconclusive", "")
    assert decide(fake(0.9, 0.95, 0.92, 0.8, 0.96), 0.05) == ("2B faster", "beyond margin")
    assert decide(fake(0.97, 0.99, 0.98, 0.9, 1.0), 0.05) == ("2B faster", "size unclear")
    assert decide(fake(0.97, 0.99, 0.98, 0.9, 1.0), None) == ("2B faster", "")
    assert decide(fake(1.1, 1.2, 1.15, 1.0, 1.3), 0.05) == ("2A faster", "beyond margin")
    assert decide(fake(None, None, 1.0, spread=False), 0.05) == ("no spread observed", "")


def test_equivalence_bounds_are_strict_and_asymmetric():
    # Upper bound 1.05 exactly is not inside; lower bound is 1/1.05.
    assert decide(fake(1.0, 1.0, 1.0, 1.0, 1.05), 0.05)[0] != "equivalent"
    assert decide(fake(1.0, 1.0, 1.0, 1 / 1.05, 1.0), 0.05)[0] != "equivalent"
    assert decide(fake(1.0, 1.0, 1.0, 1 / 1.05 + 1e-9, 1.05 - 1e-9), 0.05)[0] == "equivalent"
    assert decide(fake(1.0, 1.0, 1.0, 0.96, 1.0), 0.05)[0] == "equivalent"  # 1/1.05 = 0.952


def test_difference_beats_nothing_when_equivalent_comes_first():
    assert decide(fake(1.001, 1.009, 1.005, 1.002, 1.008), 0.05)[0] == "equivalent"


def test_no_spread_on_rounding_only_ratios():
    a = [1.1, 2.2, 3.3, 4.4, 5.5, 6.6]
    b = [x / 1.1 for x in a]
    stats, (verdict, note) = worked(0.05, a, b)
    assert verdict == "no spread observed" and stats["diff_low"] is None
    assert stats["smallest_margin"] is None and stats["sd_log"] < 1e-9


@pytest.mark.parametrize("seed", range(40))
def test_equivalent_exactly_when_margin_exceeds_smallest_provable(seed):
    rng = np.random.default_rng(seed)
    a = 100 * np.exp(rng.normal(0.01, 0.03, 10))
    b = 100 * np.exp(rng.normal(0, 0.03, 10))
    s = paired_statistics(a, b, 5)
    smallest = s["smallest_margin"]
    assert decide(s, smallest * 1.0001)[0] == "equivalent"
    assert decide(s, smallest * 0.9999)[0] != "equivalent"


# ---- 1.3 flags and order ---------------------------------------------------------------

CLEAN = dict(hostname="h", git_sha="s", git_dirty="FALSE", run_timestamp="2026-01-01T00:00:00")


def clean_rows(**override):
    """Ten reruns at one thread count; 2A runs first in reruns 1-6 and second in 7-10."""
    out = []
    for i, (x, y) in enumerate(zip(A, B), 1):
        first, second = ("2026-01-01T00:00:00", "2026-01-01T00:01:00")
        if i > 6:
            first, second = second, first
        for strategy, score, stamp in (("2a", x, first), ("2b", y, second)):
            extra = {**CLEAN, "run_timestamp": stamp, "aux_recordBytes": str(score * 500), **override}
            out.append(observation(strategy, f"{i:02d}", 1, score, **extra))
    return out


def flags(rs, tmp_path):
    return table(rs, tmp_path).iloc[0]["flags"]


def test_clean_ledger_has_no_flags_and_adjusted_ratio(tmp_path):
    t = table(clean_rows(), tmp_path).iloc[0]
    assert t["flags"] == ""
    assert t.order_2a_first == 6
    assert t.order_adjusted_ratio_geo == pytest.approx(1.008387, **APPROX)
    assert t.verdict == "equivalent"


def test_order_columns_directly():
    y = np.log(np.array(A) / np.array(B))
    count, adjusted, unknown, unbalanced = order_columns(y, [1] * 6 + [-1] * 4)
    assert (count, unknown, unbalanced) == (6, False, False) and adjusted == pytest.approx(1.008387, **APPROX)
    assert order_columns(y, [1] * 10)[1] is None
    assert order_columns(y, [1] * 9 + [None])[1:3] == (None, True)
    assert order_columns(y, [1] * 10)[3] is True and order_columns(y, [1] * 6 + [-1] * 4)[3] is False and order_columns(y, [1] * 7 + [-1] * 3)[3] is True


def test_ran_first_rules():
    first, second = {"run_timestamp": "2026-01-01T00:00:00"}, {"run_timestamp": "2026-01-01T00:00:05"}
    assert ran_first(first, second) == 1 and ran_first(second, first) == -1
    assert ran_first(first, first) is None
    assert ran_first(first, {"run_timestamp": "garbage"}) is None
    assert ran_first(first, {"run_timestamp": ""}) is None
    assert ran_first(first, {}) is None
    assert ran_first(first, {"run_timestamp": "2026-01-01T00:00:05+00:00"}) is None


def numbers(t):
    return t[["mean_2a", "mean_2b", "logt_ratio", "diff_low", "diff_high", "equiv_low", "smallest_provable_margin",
              "verdict", "boot95_low"]].tolist()


def test_each_flag_fires_and_numbers_stay(tmp_path):
    base = table(clean_rows(), tmp_path).iloc[0]

    def changed(index_filter, **cells):
        rs = clean_rows()
        for r in rs:
            if index_filter(r):
                r.update(cells)
        return table(rs, tmp_path).iloc[0]

    host = changed(lambda r: r["strategy"] == "2b" and r["rerun"] == "01", hostname="other")
    sha = changed(lambda r: r["strategy"] == "2a" and r["rerun"] == "01", git_sha="other")
    dirty = changed(lambda r: r["rerun"] == "02", git_dirty="TRUE")
    blank = changed(lambda r: r["rerun"] == "02", git_dirty="")
    work = changed(lambda r: r["strategy"] == "2b", aux_recordBytes="n/a")
    scaled = clean_rows()
    for r in scaled:
        if r["strategy"] == "2b":
            r["aux_recordBytes"] = str(float(r["score"]) * 520)
    work_scaled = table(scaled, tmp_path).iloc[0]
    assert "work_differs" in work_scaled["flags"].split(";") and numbers(work_scaled) == numbers(base)
    unbalanced = changed(lambda r: r["rerun"] in ("07", "08", "09", "10") and r["strategy"] == "2b",
                        run_timestamp="2026-01-01T00:09:00")
    for cell, flag in ((host, "host_differs"), (sha, "sha_differs"), (dirty, "dirty_build"),
                       (blank, "dirty_build"), (work, "work_differs")):
        assert flag in cell["flags"].split(";")
        assert numbers(cell) == numbers(base)
    assert base["flags"] == ""
    unknown = changed(lambda r: r["rerun"] == "03", run_timestamp="not a time")
    assert "order_unknown" in unknown["flags"].split(";") and pd.isna(unknown.order_adjusted_ratio_geo)
    assert numbers(unknown) == numbers(base)
    assert unbalanced.order_2a_first == 10 and "order_unbalanced" in unbalanced["flags"].split(";")
    assert numbers(unbalanced) == numbers(base)


def test_sha_must_differ_within_a_strategy_not_between(tmp_path):
    rs = clean_rows()
    for r in rs:
        r["git_sha"] = "a" if r["strategy"] == "2a" else "b"
    assert "sha_differs" not in flags(rs, tmp_path).split(";")


def test_work_unknown_and_missing_columns_never_crash(tmp_path):
    rs = clean_rows(aux_recordBytes="n/a")
    assert "work_differs" in flags(rs, tmp_path).split(";")
    rs = [{k: v for k, v in r.items() if k not in ("aux_recordBytes", "git_dirty", "hostname", "git_sha", "run_timestamp")}
          for r in clean_rows()]
    got = flags(rs, tmp_path).split(";")
    assert {"work_differs", "dirty_build", "order_unknown"} <= set(got)


# ---- 2.1 config ------------------------------------------------------------------------

def write_config(tmp_path, extra):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"input": {"path": "x.csv"}, **extra}))
    return path


def test_margin_loads_default_none_and_shipped():
    shipped = load_config(Path(__file__).parents[1] / "config.yaml")
    assert shipped.comparison_margin == 0.05
    text = (Path(__file__).parents[1] / "config.yaml").read_text()
    assert "Why 5%:" in text and "before anyone had seen 2A vs 2B results." in text


def test_margin_optional(tmp_path):
    assert load_config(write_config(tmp_path, {})).comparison_margin is None
    assert load_config(write_config(tmp_path, {"comparison": {}})).comparison_margin is None
    assert load_config(write_config(tmp_path, {"comparison": {"margin": 0.02}})).comparison_margin == 0.02


@pytest.mark.parametrize("bad", [0, 1, 2, 5, -0.1, True, False, "0.05", None, float("nan")])
def test_bad_margin_fails_naming_setting(tmp_path, bad):
    with pytest.raises(ValueError, match="comparison.margin"):
        load_config(write_config(tmp_path, {"comparison": {"margin": bad}}))


def test_unknown_comparison_key_fails(tmp_path):
    with pytest.raises(ValueError, match="comparison"):
        load_config(write_config(tmp_path, {"comparison": {"margin": 0.05, "level": 0.9}}))


# ---- 2.2 / 2.3 command -----------------------------------------------------------------

def invoke(name, config_path, cwd):
    return subprocess.run([str(Path(sys.executable).with_name(name)), "--config", str(config_path)],
                          cwd=cwd, capture_output=True, text=True, env={**os.environ, "MPLBACKEND": "Agg"})


def noisy_ledger(path, benchmarks=("mb1_rawNoFinalize", "mb2_rawFinalize")):
    rs = []
    for k, benchmark in enumerate(benchmarks):
        rs += rows(A if k == 0 else FAR_A, B if k == 0 else FAR_B, threads=(1, 2, 4, 8, 16),
                   benchmark=benchmark, **CLEAN)
    pd.DataFrame(rs).rename(columns={"strategy": ""}).to_csv(path, index=False)


def test_command_writes_one_csv_per_benchmark_and_no_plot(tmp_path):
    noisy_ledger(tmp_path / "in.csv")
    config = write_config(tmp_path, {"output": {"directory": "out"}, "comparison": {"margin": 0.05},
                                     "benchmarks": ["mb1_rawNoFinalize", "mb2_rawFinalize"]})
    config.write_text(yaml.safe_dump({"input": {"path": "in.csv"}, "output": {"directory": "out"},
                                      "comparison": {"margin": 0.05},
                                      "benchmarks": ["mb1_rawNoFinalize", "mb2_rawFinalize"]}))
    result = invoke("compare", config, tmp_path)
    assert result.returncode == 0, result.stderr
    paths = [Path(line) for line in result.stdout.splitlines()]
    assert [p.name for p in paths] == ["compare_mb1_rawNoFinalize_avg.csv", "compare_mb2_rawFinalize_avg.csv"]
    assert paths[0].parent.name.endswith("_compare") and list(paths[0].parent.iterdir()) == paths
    first = pd.read_csv(paths[0])
    assert list(first.columns) == COLUMNS
    assert first.threads_blobshuffle.tolist() == [1, 2, 4, 8, 16]
    assert first.diff_level.iloc[0] == pytest.approx(0.99) and first.equiv_level.iloc[0] == pytest.approx(0.98)
    assert first.logt_ratio.iloc[0] == pytest.approx(1.008034, **APPROX)
    assert first.verdict.iloc[0] == "equivalent" and first.note.iloc[0] == "2A 0.8% faster, below margin"
    assert first.smallest_provable_margin.iloc[0] == pytest.approx(0.013773, **APPROX)
    assert json.loads(first.contributing_reruns.iloc[0]) == [f"{i:02d}" for i in range(1, 11)]
    assert first.ci_seed.notna().all() and (first.ci_resamples == 10000).all()
    second = pd.read_csv(paths[1])
    assert second.equiv_high.iloc[0] == pytest.approx(1.020070, **APPROX)
    again = invoke("compare", config, tmp_path)
    assert again.returncode == 0
    assert Path(again.stdout.splitlines()[0]).parent != paths[0].parent
    assert len(list((tmp_path / "out").iterdir())) == 2


def test_failure_leaves_no_output_folder(tmp_path):
    rs = [r for r in rows() if not (r["strategy"] == "2b" and r["rerun"] == "04" and r["threads_blobshuffle"] == "2")]
    pd.DataFrame(rs).rename(columns={"strategy": ""}).to_csv(tmp_path / "in.csv", index=False)
    config = tmp_path / "c.yaml"
    config.write_text(yaml.safe_dump({"input": {"path": "in.csv"}, "output": {"directory": "out"},
                                      "benchmarks": ["mb1_rawNoFinalize"]}))
    result = invoke("compare", config, tmp_path)
    assert result.returncode == 1 and "rerun 04" in result.stderr
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())


def test_colliding_benchmark_names_fail_before_output(tmp_path):
    from src.compare import run
    noisy_ledger(tmp_path / "in.csv", ("a/b", "a?b"))
    config = Config(tmp_path / "in.csv", tmp_path / "out", benchmarks=("a/b", "a?b"), comparison_margin=0.05)
    with warnings.catch_warnings(), pytest.raises(ValueError, match="share the file name"):
        warnings.simplefilter("ignore")
        run(config)
    assert not (tmp_path / "out").exists()


@pytest.mark.skipif(not (Path(__file__).parents[1] / "docs" / "jmh_sample.csv").exists(),
                    reason="docs/jmh_sample.csv is gitignored and absent from this clone")
def test_sample_ledger_says_no_spread_everywhere(tmp_path):
    root = Path(__file__).parents[1]
    config = tmp_path / "c.yaml"
    config.write_text(yaml.safe_dump({"input": {"path": str(root / "docs" / "jmh_sample.csv")},
                                      "output": {"directory": str(tmp_path / "out")},
                                      "comparison": {"margin": 0.05}}))
    result = invoke("compare", config, tmp_path)
    assert result.returncode == 0, result.stderr
    for line in result.stdout.splitlines():
        t = pd.read_csv(line)
        assert (t.verdict == "no spread observed").all()
        assert t[["diff_low", "equiv_low", "smallest_provable_margin", "boot95_low"]].isna().all().all()
