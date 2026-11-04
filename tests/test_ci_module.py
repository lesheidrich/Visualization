"""Change add-ci-module: the CI arithmetic lives in src/ci.py and `ci.method` picks what plots draw.

Expected values are literal, derived by hand in the comments. t(2) = 4.302653 and t(9) = 2.262157.
Resampled bounds on 1, 1, 4: resampled means of three draws have P(all 1) = 8/27 > 2.5% and
P(all 4) = 1/27 > 2.5%, so the 95% percentile bootstrap is exactly [1, 4]."""
from pathlib import Path

import pandas as pd
import pytest
import yaml
from matplotlib.collections import LineCollection

from audit_support import read_csv, run_main, write_run
from conftest import gc_observation, observation
from src import ci, cli, compare, gc_metrics, speedup, thrpt, utils
from src.config import Config, load_config

MB1 = "mb1_rawNoFinalize"
MB2 = "mb2_rawFinalize"
ROOT = Path(__file__).parents[1]
RAW3 = ROOT / "docs" / "raw3.csv"
T_TITLE = "95% t interval of reruns"
RESAMPLED_TITLE = "95% percentile bootstrap of reruns"
HALF_1_1_4 = 4.302653 * 3 ** 0.5 / 3 ** 0.5  # t(2) * SD sqrt(3) / sqrt(3) = 4.302653


def thrpt_rows(strategy, threads, scores, benchmark=MB1):
    return [observation(benchmark_short=benchmark, strategy=strategy, threads=threads, rerun=f"{i + 1:02d}",
                        score=str(score), score_unit="ops/s", score_error="", score_confidence_low="",
                        score_confidence_high="") for i, score in enumerate(scores)]


def gc_rows(strategy, threads, pcts, benchmark=MB1, fills=None, allocs=None):
    rows = []
    for i, pct in enumerate(pcts):
        extra = {"gc_time": str(round(pct * 2500, 6))}
        if fills is not None:
            extra["aux_recordBytes"] = str(1677.7216 * fills[i])  # fill = bytes / 0.0001 / 16777216
        if allocs is not None:
            extra["gc_alloc_rate_norm"] = str(allocs[i])
        rows.append(gc_observation(benchmark_short=benchmark, strategy=strategy, threads=threads,
                                   rerun=f"{i + 1:02d}", **extra))
    return rows


def run(tmp_path, monkeypatch, capsys, module, main, rows, method, benchmarks=(MB1,)):
    """Run one command; return (exit code, folder, warnings, {stem: figure})."""
    figures = {}
    real = module.save_figure

    def spy(fig, stem, config):
        figures[Path(stem).name] = fig
        return real(fig, stem, config)

    monkeypatch.setattr(module, "save_figure", spy)
    place = tmp_path / method
    place.mkdir()
    path = write_run(place, rows, benchmarks=benchmarks, ci_method=method, margin=0.05)
    code, _, messages, _ = run_main(main, path, capsys)
    folders = list((place / "out").iterdir())
    return code, folders[0] if folders else None, messages, figures


def segments(fig):
    return [(float(min(s[:, 1])), float(max(s[:, 1])))
            for c in fig.axes[0].collections if isinstance(c, LineCollection) for s in c.get_segments()]


# ------------------------------------------------------------------------ unit: the module


@pytest.mark.parametrize("name", ["t_quantile", "t_interval", "fieller_interval", "bootstrap_ci", "cell_seed",
                                  "has_spread", "BOOTSTRAP_RESAMPLES", "CI_BASE_SEED", "CI_METHOD", "T_METHOD",
                                  "FIELLER_METHOD"])
def test_unit_one_home(name):
    assert hasattr(ci, name)
    assert not hasattr(utils, name)


@pytest.mark.parametrize("module", [thrpt, speedup, compare, gc_metrics])
def test_unit_features_take_the_arithmetic_from_the_module(module):
    for name in ("bootstrap_ci", "t_interval", "fieller_interval", "t_quantile", "cell_seed", "has_spread"):
        if hasattr(module, name):
            assert getattr(module, name) is getattr(ci, name)


def test_unit_method_words():
    assert ci.CI_METHODS == ("t", "resampled")
    assert ci.method_title("t") == T_TITLE and ci.method_title("resampled") == RESAMPLED_TITLE
    assert ci.method_name("t") == "t interval" and ci.method_name("resampled") == "resampled CI"


@pytest.mark.parametrize("method, metric, expected", [
    ("t", None, ("t_low", "t_high")),
    ("resampled", None, ("ci_low", "ci_high")),
    ("t", "batch_fill", ("batch_fill_t_low", "batch_fill_t_high")),
    ("resampled", "batch_fill", ("batch_fill_ci_low", "batch_fill_ci_high")),
])
def test_unit_interval_columns(method, metric, expected):
    assert ci.interval_columns(method, metric) == expected


def test_spec_same_numbers():
    low, high = ci.t_interval([100 + 10 * i for i in range(10)])
    assert (low, high) == pytest.approx((123.34, 166.66), abs=0.01)
    assert ci.bootstrap_ci([1, 1, 4], seed=1) == (1.0, 4.0)
    assert ci.bootstrap_ci([100, 200], seed=1) == (100.0, 200.0)


def test_unit_seed_did_not_change():
    # Literal, from the code before the move: a changed seed would change every resampled number.
    assert ci.cell_seed("b", "s", 1, "score") == int.from_bytes(
        __import__("hashlib").sha256(b"42|b|s|1|score").digest()[:8], "big") % 2 ** 63


# ------------------------------------------------------------------------ unit: config


def config_with(tmp_path, section):
    settings = {"input": {"path": "x.csv"}}
    settings.update(section)
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(settings))
    return path


def test_spec_default_method(tmp_path):
    assert load_config(config_with(tmp_path, {})).ci_method == "t"
    assert Config(tmp_path, tmp_path).ci_method == "t"


@pytest.mark.parametrize("word", ["t", "resampled"])
def test_unit_method_accepted(tmp_path, word):
    assert load_config(config_with(tmp_path, {"ci": {"method": word}})).ci_method == word


@pytest.mark.parametrize("word", ["bootstrap", "T", "", None, 1, True, ["t"]])
def test_unit_method_rejected(tmp_path, word):
    with pytest.raises(ValueError, match="ci.method"):
        load_config(config_with(tmp_path, {"ci": {"method": word}}))


def test_unit_ci_section_must_be_a_mapping_with_known_keys(tmp_path):
    with pytest.raises(ValueError, match="ci must be a mapping"):
        load_config(config_with(tmp_path, {"ci": "t"}))
    with pytest.raises(ValueError, match="Unknown ci setting"):
        load_config(config_with(tmp_path, {"ci": {"level": 0.9}}))


def test_spec_unknown_method_exits_nonzero_and_creates_nothing(tmp_path, capsys):
    path = write_run(tmp_path, thrpt_rows("base", 1, [1, 2]), ci_method="bootstrap")
    code, _, _, stderr = run_main(cli.main, path, capsys)
    assert code == 1 and "ci.method" in stderr
    assert not (tmp_path / "out").exists()


def test_spec_config_file_lists_the_options_and_defaults_to_t():
    text = (ROOT / "config.yaml").read_text()
    assert yaml.safe_load(text)["ci"] == {"method": "t"}
    comments = "\n".join(line for line in text.splitlines() if line.startswith("#"))
    for word in ("t ", "resampled", "Speedup", "compare"):
        assert word in comments


# ------------------------------------------------------------------------ throughput


def test_spec_throughput_plot_follows_the_method(tmp_path, monkeypatch, capsys):
    rows = thrpt_rows("base", 1, [1, 1, 4])
    for method, title, expected in (("t", T_TITLE, (2 - HALF_1_1_4, 2 + HALF_1_1_4)),
                                    ("resampled", RESAMPLED_TITLE, (1.0, 4.0))):
        code, folder, _, figures = run(tmp_path, monkeypatch, capsys, thrpt, cli.main, rows, method)
        assert code == 0
        fig = figures[f"thrpt_{MB1}_avg"]
        assert len(segments(fig)) == 1 and segments(fig)[0] == pytest.approx(expected, abs=1e-4)
        assert title in fig.axes[0].get_title()
        table = read_csv(folder / f"thrpt_{MB1}_avg.csv")
        assert table["interval_kind"].tolist() == [title]


def test_spec_tables_do_not_depend_on_the_method_throughput(tmp_path, monkeypatch, capsys):
    rows = thrpt_rows("base", 1, [1, 1, 4])
    tables = [read_csv(run(tmp_path, monkeypatch, capsys, thrpt, cli.main, rows, m)[1] / f"thrpt_{MB1}_avg.csv")
              .drop(columns=["interval_kind", "source_files"]) for m in ("t", "resampled")]
    pd.testing.assert_frame_equal(*tables)
    assert float(tables[0].ci_low[0]) == 1 and float(tables[0].ci_high[0]) == 4
    assert float(tables[0].t_low[0]) == pytest.approx(2 - HALF_1_1_4, abs=1e-4)


def test_unit_no_spread_has_no_bar_under_either_method(tmp_path, monkeypatch, capsys):
    rows = thrpt_rows("base", 1, [5, 5, 5])
    for method in ("t", "resampled"):
        _, folder, messages, figures = run(tmp_path, monkeypatch, capsys, thrpt, cli.main, rows, method)
        assert segments(figures[f"thrpt_{MB1}_avg"]) == []
        assert read_csv(folder / f"thrpt_{MB1}_avg.csv")["interval_kind"].tolist() == ["none"]
        assert "Error bars" not in figures[f"thrpt_{MB1}_avg"].axes[0].get_title()
        assert any(m.startswith("No CI for") for m in messages)


def test_spec_per_rerun_plots_ignore_the_method(tmp_path, monkeypatch, capsys):
    rows = thrpt_rows("base", 1, [1, 1, 4])
    for row in rows:
        score = float(row["score"])
        row.update(score_confidence_low=str(score - 0.5), score_confidence_high=str(score + 0.5))
    folders = [run(tmp_path, monkeypatch, capsys, thrpt, cli.main, rows, m)[1] for m in ("t", "resampled")]
    for rerun in ("r01", "r02", "r03"):
        png, csv = f"thrpt_{MB1}_{rerun}.png", f"thrpt_{MB1}_{rerun}.csv"
        assert (folders[0] / png).read_bytes() == (folders[1] / png).read_bytes()
        assert read_csv(folders[0] / csv).drop(columns="source_file").equals(
            read_csv(folders[1] / csv).drop(columns="source_file"))


def test_unit_bound_on_the_wrong_side_draws_no_negative_length(tmp_path):
    config = Config(tmp_path, tmp_path, benchmarks=(MB1,), figsize=(5, 3), dpi=40, ci_method="resampled")
    table = pd.DataFrame([{"strategy": "base", "threads_blobshuffle": 1, "score": 10.0, "t_low": 9.0, "t_high": 11.0,
                           "ci_low": 10.5, "ci_high": 12.0, "interval_kind": RESAMPLED_TITLE}])
    fig = thrpt.render(table, config, "x")
    assert segments(fig) == [(10.0, 12.0)]
    averages = pd.DataFrame([{"strategy": "base", "threads_blobshuffle": 1, "benchmark_short": MB1, "gc_time_pct": 10.0,
                              "gc_time_pct_ci_low": 10.5, "gc_time_pct_ci_high": 12.0}])
    fig = gc_metrics.render_gc_time(averages, config, MB1, strategies=["base"], threads=[1])
    assert segments(fig) == [(10.0, 12.0)]


# ------------------------------------------------------------------------ GC


def test_spec_gc_table_holds_both_for_every_metric(tmp_path, monkeypatch, capsys):
    _, folder, _, _ = run(tmp_path, monkeypatch, capsys, gc_metrics, cli.gc_main,
                          gc_rows("base", 1, [1, 1, 4]), "t")
    table = read_csv(folder / f"gc_{MB1}_avg.csv")
    assert float(table.gc_time_pct_ci_low[0]) == 1 and float(table.gc_time_pct_ci_high[0]) == 4
    assert float(table.gc_time_pct_t_low[0]) == pytest.approx(2 - HALF_1_1_4, abs=1e-4)
    for metric in gc_metrics.AVERAGED:
        assert f"{metric}_t_low" in table and f"{metric}_ci_high" in table
    columns = table.columns.tolist()
    first_new = columns.index("score_ci_low")
    assert columns[first_new:] == [f"{m}_ci_{e}" for m in ("score", "gc_count", "gc_time", "gc_time_pct", "pauses_max",
                                                           "pauses_p99", "batch_bytes", "batch_fill")
                                   for e in ("low", "high")]
    assert all("_ci_" not in c or c.startswith("gc_alloc") for c in columns[:first_new])


def test_unit_gc_new_columns_blank_without_spread_and_silent(tmp_path, monkeypatch, capsys):
    _, folder, messages, _ = run(tmp_path, monkeypatch, capsys, gc_metrics, cli.gc_main,
                                 gc_rows("base", 1, [1, 1, 1]), "t")
    table = read_csv(folder / f"gc_{MB1}_avg.csv")
    assert table.gc_time_pct_ci_low[0] == "" and table.gc_count_ci_high[0] == ""
    assert not any("gc_count" in m or "pauses_p99" in m for m in messages)


def test_unit_gc_new_columns_use_the_metric_seed(config):
    rows = pd.DataFrame(gc_rows("base", 1, [1, 2, 3.5, 5, 9]))
    _, averages = gc_metrics.prepare(rows, config)
    expected = ci.bootstrap_ci([1, 2, 3.5, 5, 9], seed=ci.cell_seed(MB1, "base", 1, "gc_time_pct"))
    assert (averages.gc_time_pct_ci_low[0], averages.gc_time_pct_ci_high[0]) == pytest.approx(expected, abs=1e-9)


def test_unit_gc_score_resampled_matches_throughput_table(config):
    rows = pd.DataFrame([{**row, "score": str(score), "score_confidence_low": "", "score_confidence_high": ""} for row, score in zip(gc_rows("base", 1, [1, 2, 3]), [10, 20, 45])])
    _, gc_table = gc_metrics.prepare(rows, config)
    _, thrpt_table = thrpt.prepare(rows, config)
    assert gc_table.score_ci_low[0] == thrpt_table.ci_low[0] and gc_table.score_ci_high[0] == thrpt_table.ci_high[0]


def test_integration_gc_row_order_does_not_change_new_columns(config):
    rows = gc_rows("base", 1, [1, 2, 3.5, 5, 9])
    _, one = gc_metrics.prepare(pd.DataFrame(rows), config)
    _, two = gc_metrics.prepare(pd.DataFrame(rows[::-1]), config)
    assert one.gc_time_pct_ci_low[0] == two.gc_time_pct_ci_low[0]


@pytest.mark.parametrize("stem, rows", [
    ("gc_time_pct", gc_rows("base", 1, [1, 1, 4])),
    ("gc_alloc_rate_norm", gc_rows("base", 1, [1, 1, 1], allocs=[1, 1, 4])),
])
def test_spec_gc_plots_follow_the_method(tmp_path, monkeypatch, capsys, stem, rows):
    for method, title, expected in (("t", T_TITLE, (2 - HALF_1_1_4, 2 + HALF_1_1_4)),
                                    ("resampled", RESAMPLED_TITLE, (1.0, 4.0))):
        _, _, _, figures = run(tmp_path, monkeypatch, capsys, gc_metrics, cli.gc_main, rows, method)
        fig = figures[f"gc_{MB1}_{stem}"]
        assert len(segments(fig)) == 1 and segments(fig)[0] == pytest.approx(expected, abs=1e-3)
        assert title in fig.axes[0].get_title()
        assert [p.get_height() for p in fig.axes[0].patches] == pytest.approx([2.0])


def test_spec_batch_fill_plot_follows_the_method(tmp_path, monkeypatch, capsys):
    rows = gc_rows("base", 1, [1, 1, 1], benchmark=MB2, fills=[0.2, 0.2, 0.8])
    half = 4.302653 * 0.2 * 3 ** 0.5 / 3 ** 0.5  # SD = 0.2 * sqrt(3) = 0.34641
    for method, title, expected in (("t", T_TITLE, (0.4 - half, 0.4 + half)),
                                    ("resampled", RESAMPLED_TITLE, (0.2, 0.8))):
        _, _, _, figures = run(tmp_path, monkeypatch, capsys, gc_metrics, cli.gc_main, rows, method, benchmarks=(MB2,))
        fig = figures[f"gc_{MB2}_batch_fill"]
        assert len(segments(fig)) == 1 and segments(fig)[0] == pytest.approx(expected, abs=1e-3)
        assert title in fig.axes[0].get_title()
        assert fig.axes[0].get_ylim()[0] == 0 and fig.axes[0].get_ylim()[1] >= 1


def test_spec_gc_time_axis_top_uses_the_drawn_interval(tmp_path, monkeypatch, capsys):
    rows = gc_rows("base", 1, [1, 1, 4])
    # t: high 6.302653, * 1.1 = 6.93 -> 10. resampled: high 4, * 1.1 = 4.4 -> 5.
    for method, top in (("t", 10), ("resampled", 5)):
        _, _, _, figures = run(tmp_path, monkeypatch, capsys, gc_metrics, cli.gc_main, rows, method)
        assert figures[f"gc_{MB1}_gc_time_pct"].axes[0].get_ylim() == (0, top)


def test_spec_gc_warnings_name_the_method(tmp_path, monkeypatch, capsys):
    rows = gc_rows("base", 1, [1, 1, 1], benchmark=MB2)
    for method, word in (("t", "t interval"), ("resampled", "resampled CI")):
        _, _, messages, _ = run(tmp_path, monkeypatch, capsys, gc_metrics, cli.gc_main, rows, method, benchmarks=(MB2,))
        tail = f"for {MB2}/base/threads=1: fewer than two reruns or no spread"
        assert messages.count(f"No batch_fill {word} {tail}") == 1
        assert messages.count(f"No gc_time_pct {word} {tail}") == 1


def test_spec_gc_tables_do_not_depend_on_the_method(tmp_path, monkeypatch, capsys):
    rows = gc_rows("base", 1, [1, 1, 4])
    folders = [run(tmp_path, monkeypatch, capsys, gc_metrics, cli.gc_main, rows, m)[1] for m in ("t", "resampled")]
    names = sorted(p.name for p in folders[0].iterdir())
    assert names == sorted(p.name for p in folders[1].iterdir())
    for name in names:
        if name.endswith(".csv"):
            pd.testing.assert_frame_equal(read_csv(folders[0] / name).drop(columns="source_files", errors="ignore")
                                          .drop(columns="source_file", errors="ignore"),
                                          read_csv(folders[1] / name).drop(columns="source_files", errors="ignore")
                                          .drop(columns="source_file", errors="ignore"))
    assert (folders[0] / f"gc_{MB1}_pauses_max.png").read_bytes() == (folders[1] / f"gc_{MB1}_pauses_max.png").read_bytes()


# ------------------------------------------------------------------------ speedup and compare stay as they are


def same_files(folders):
    names = sorted(p.name for p in folders[0].iterdir())
    assert names == sorted(p.name for p in folders[1].iterdir())
    for name in names:
        first, second = folders[0] / name, folders[1] / name
        if name.endswith(".csv"):
            drop = [c for c in read_csv(first) if c in ("source_file", "source_files", "baseline_source_file",
                                                        "baseline_source_files")]
            assert read_csv(first).drop(columns=drop).equals(read_csv(second).drop(columns=drop)), name
        else:
            assert first.read_bytes() == second.read_bytes(), name


def test_spec_speedup_ignores_the_method(tmp_path, monkeypatch, capsys):
    rows = thrpt_rows("base", 1, [10, 11, 12]) + thrpt_rows("1a", 2, [15, 19, 20])
    results = [run(tmp_path, monkeypatch, capsys, speedup, cli.speedup_main, rows, m) for m in ("t", "resampled")]
    same_files([r[1] for r in results])
    assert ci.FIELLER_METHOD in results[1][3][f"speedup_{MB1}_avg"].axes[0].get_title()
    table = read_csv(results[1][1] / f"speedup_{MB1}_avg.csv")
    for column in ("ci_low", "t_low", "fieller_low"):   # all three already there: nothing is added
        assert table[column].iloc[-1] != ""


def test_spec_compare_ignores_the_method(tmp_path, monkeypatch, capsys):
    rows = thrpt_rows("2a", 1, [10, 11, 12]) + thrpt_rows("2b", 1, [10.5, 11, 12.5])
    results = [run(tmp_path, monkeypatch, capsys, compare, cli.compare_main, rows, m) for m in ("t", "resampled")]
    same_files([r[1] for r in results])


# ------------------------------------------------------------------------ feature, on the real format


@pytest.mark.skipif(not RAW3.exists(), reason="docs/raw3.csv is not in this clone")
def test_feature_raw3_methods_differ_only_in_plots(tmp_path):
    outputs = {}
    for method in ("t", "resampled"):
        place = tmp_path / method
        place.mkdir()
        (place / "config.yaml").write_text(yaml.safe_dump({
            "input": {"path": str(RAW3)}, "output": {"directory": "out", "formats": ["png"]},
            "strategies": {"exclude": ["2null"]}, "plot": {"figsize": [6, 3], "dpi": 40}, "ci": {"method": method}}))
        outputs[method] = gc_metrics.run(load_config(place / "config.yaml"))
    by_name = {method: {p.name: p for p in paths} for method, paths in outputs.items()}
    assert set(by_name["t"]) == set(by_name["resampled"])
    assert by_name["t"][f"gc_{MB2}_avg.csv"].read_bytes() == by_name["resampled"][f"gc_{MB2}_avg.csv"].read_bytes()
    assert by_name["t"][f"gc_{MB2}_gc_time_pct.png"].read_bytes() != by_name["resampled"][f"gc_{MB2}_gc_time_pct.png"].read_bytes()
    assert by_name["t"][f"gc_{MB2}_pauses_max.png"].read_bytes() == by_name["resampled"][f"gc_{MB2}_pauses_max.png"].read_bytes()
