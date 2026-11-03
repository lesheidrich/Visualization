"""strategies.exclude (openspec change exclude-strategy-2null): one test per scenario, literal values.

Ledger built below (mb1_rawNoFinalize, reruns 01..03, rerun r scores v * r, so the mean of reruns is 2 * v):
  strategy / threads : v    mean of reruns   speedup (mean / 200)
  base 1             : 100  200              1.0
  1a 1 / 2           : 50 / 150    100 / 300   0.5 / 1.5
  2a 1 / 2           : 110 / 130   220 / 260   1.1 / 1.3
  2b 1 / 2           : 100 / 120   200 / 240   1.0 / 1.2
  2null 1 / 2        : 70 / 90     140 / 180   (the strategy being excluded)
The 2null rows close each rerun block; no matter where they sit, a kept row keeps its ledger line number.
"""
import glob
from pathlib import Path
import warnings

import matplotlib
import pandas as pd
import pytest
import yaml

from audit_support import read_csv, write_run
from conftest import gc_observation
from src import cli
from src.config import Config, load_config
from src.gc_metrics import prepare as gc_prepare, render_alloc, render_pauses
from src.speedup import prepare as speedup_prepare, render as speedup_render
from src.thrpt import prepare as thrpt_prepare, render as thrpt_render
from src.utils import normalize_throughput, strategy_order

BENCH = "mb1_rawNoFinalize"
KEPT = {("base", 1): 100, ("1a", 1): 50, ("1a", 2): 150, ("2a", 1): 110, ("2a", 2): 130,
        ("2b", 1): 100, ("2b", 2): 120}
EXCLUDED = {("2null", 1): 70, ("2null", 2): 90}
MAINS = ["main", "speedup_main", "gc_main", "compare_main"]
MAIN_IDS = ["thrpt", "speedup", "gc", "compare"]


def make_rows(spelling="2null", values=None, drop=None, bad_score=None):
    """Kept strategies first, then the excluded one, rerun by rerun."""
    table = {**KEPT, **{(spelling, n): v for (_, n), v in EXCLUDED.items()}} if values is None else values
    rows = []
    for rerun in (1, 2, 3):
        for (strategy, threads), v in table.items():
            if (strategy, threads, rerun) == drop:
                continue
            row = gc_observation(strategy=strategy, threads=threads, rerun=f"0{rerun}", score=v * rerun)
            if bad_score is not None and strategy == spelling:
                row["score"] = bad_score
            rows.append(row)
    return rows


def project(tmp_path, rows, exclude=None, raw_exclude=None):
    """Write ledger and config; `exclude` is the list to configure, None leaves the setting out."""
    path = write_run(tmp_path, rows)
    settings = yaml.safe_load(path.read_text())
    if raw_exclude is not None:
        settings["strategies"] = {"exclude": raw_exclude}
    elif exclude is not None:
        settings["strategies"] = {"exclude": exclude}
    path.write_text(yaml.safe_dump(settings))
    return path


def invoke(name, config_path):
    """(exit code, printed paths, warning messages) of one command."""
    import io
    import contextlib
    out, err = io.StringIO(), io.StringIO()
    with warnings.catch_warnings(record=True) as caught, contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        warnings.simplefilter("always")
        code = getattr(cli, name)(["--config", str(config_path)])
    return code, out.getvalue().split(), [str(w.message) for w in caught], err.getvalue()


def csv_named(paths, name):
    (path,) = [p for p in paths if Path(p).name == name]
    return read_csv(path)


def prepared(tmp_path, rows, exclude, prepare):
    config = load_config(project(tmp_path, rows, exclude))
    return config, prepare(pd.DataFrame(rows), config)


def bars(ax):
    """(label, [(centre, height), ...]) of every bar container."""
    return [(c.get_label(), [(round(p.get_x() + p.get_width() / 2, 4), round(p.get_height(), 4))
                             for p in c.patches]) for c in ax.containers if hasattr(c, "patches")]


# ---- config and normalize_throughput units --------------------------------------------------


def test_config_exclude_defaults_to_empty_and_loads_a_list(tmp_path):
    assert Config(tmp_path / "l.csv", tmp_path / "o").exclude == ()
    assert load_config(project(tmp_path, make_rows())).exclude == ()
    assert load_config(project(tmp_path, make_rows(), exclude=[])).exclude == ()
    assert load_config(project(tmp_path, make_rows(), exclude=["2null", "3"])).exclude == ("2null", "3")


def test_shipped_config_excludes_2null_and_keeps_the_label_and_order():
    config = load_config(Path(__file__).parents[1] / "config.yaml")
    assert config.exclude == ("2null",)
    assert config.labels["2null"] == "S2_0"
    assert config.order == ("base", "1a", "1b", "1c", "2null", "2a", "2b", "3", "4")


def test_normalize_throughput_drops_the_excluded_rows_before_any_check():
    rows = make_rows(bad_score="abc")  # every 2null score is malformed
    with pytest.raises(ValueError, match=r"Invalid score at ledger row\(s\) \[9, 10, 18, 19, 27, 28\]"):
        normalize_throughput(pd.DataFrame(rows), (BENCH,), {})
    kept = normalize_throughput(pd.DataFrame(rows), (BENCH,), {}, exclude=("2null",))
    assert len(kept) == 21 and set(kept["strategy"]) == {"base", "1a", "2a", "2b"}
    assert kept["score"].tolist()[:7] == [100.0, 50.0, 150.0, 110.0, 130.0, 100.0, 120.0]


def test_normalize_throughput_exclusion_gives_no_unknown_strategy_warning():
    frame = pd.DataFrame(make_rows(spelling="newcomer"))
    labels = {"base": "Base", "1a": "S1_A", "2a": "S2_A", "2b": "S2_B"}
    with pytest.warns(UserWarning, match=r"Unknown strategies retained: \['newcomer'\]"):
        normalize_throughput(frame, (BENCH,), labels)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        normalize_throughput(frame, (BENCH,), labels, exclude=("newcomer",))
    assert [str(w.message) for w in caught] == []


# ---- scenario: Excluded from plots and tables ------------------------------------------------


def test_scenario_excluded_thrpt_table_and_plot(tmp_path):
    rows = make_rows()
    code, paths, messages, _ = invoke("main", project(tmp_path, rows, ["2null"]))
    assert code == 0 and not [m for m in messages if "2null" in m or "S2_0" in m]
    average = csv_named(paths, f"thrpt_{BENCH}_avg.csv")
    assert average["strategy"].tolist() == ["base", "1a", "1a", "2a", "2a", "2b", "2b"]
    assert average["threads_blobshuffle"].tolist() == ["1", "1", "2", "1", "2", "1", "2"]
    assert [float(s) for s in average["score"]] == [200, 100, 300, 220, 260, 200, 240]
    assert len(csv_named(paths, f"thrpt_{BENCH}_r01.csv")) == 7
    assert not any("2null" in Path(p).read_text().lower() for p in paths if str(p).endswith(".csv"))
    config, (_, summary) = prepared(tmp_path, rows, ["2null"], thrpt_prepare)
    fig = thrpt_render(summary, config, "t", strategies=strategy_order(summary, config), threads=[1, 2])
    assert bars(fig.axes[0]) == [
        ("Base", [(-0.3, 200.0)]), ("S1_A", [(-0.1, 100.0), (0.9, 300.0)]),
        ("S2_A", [(0.1, 220.0), (1.1, 260.0)]), ("S2_B", [(0.3, 200.0), (1.3, 240.0)])]
    assert [t.get_text() for t in fig.axes[0].get_legend().get_texts()] == ["Base", "S1_A", "S2_A", "S2_B"]


def test_scenario_excluded_speedup_table_and_plot(tmp_path):
    rows = make_rows()
    code, paths, messages, _ = invoke("speedup_main", project(tmp_path, rows, ["2null"]))
    assert code == 0 and not [m for m in messages if "2null" in m or "S2_0" in m]
    average = csv_named(paths, f"speedup_{BENCH}_avg.csv")
    assert average["strategy"].tolist() == ["base", "1a", "1a", "2a", "2a", "2b", "2b"]
    assert [float(s) for s in average["speedup"]] == pytest.approx([1.0, 0.5, 1.5, 1.1, 1.3, 1.0, 1.2])
    assert len(csv_named(paths, f"speedup_{BENCH}_r02.csv")) == 7
    assert not any("2null" in Path(p).read_text().lower() for p in paths if str(p).endswith(".csv"))
    config, (_, summary) = prepared(tmp_path, rows, ["2null"], speedup_prepare)
    fig = speedup_render(summary, config, "t", strategies=strategy_order(summary, config), threads=[1, 2])
    lines = {line.get_label(): list(line.get_ydata()) for line in fig.axes[0].lines
             if not line.get_label().startswith("_")}
    assert list(lines) == ["Ideal (y=N)", "Base N=1", "Base", "S1_A", "S2_A", "S2_B"]
    assert lines["S2_A"] == pytest.approx([1.1, 1.3]) and lines["S1_A"] == pytest.approx([0.5, 1.5])


def test_scenario_excluded_gc_tables_and_plots(tmp_path):
    rows = make_rows()
    code, paths, messages, _ = invoke("gc_main", project(tmp_path, rows, ["2null"]))
    assert code == 0 and not [m for m in messages if "2null" in m or "S2_0" in m]
    assert len(csv_named(paths, f"gc_{BENCH}_r_all.csv")) == 21
    assert len(csv_named(paths, f"gc_{BENCH}_avg.csv")) == 7
    pivot = csv_named(paths, f"gc_{BENCH}_gc_count_pivot.csv")
    assert pivot.iloc[:, 0].tolist() == ["base", "1a", "2a", "2b"]
    assert len(csv_named(paths, f"gc_{BENCH}_pauses_max_median.csv")) == 7
    assert not any("2null" in Path(p).read_text().lower() for p in paths if str(p).endswith(".csv"))
    config = load_config(project(tmp_path, rows, ["2null"]))
    observations, averages = gc_prepare(pd.DataFrame(rows), config)
    order = strategy_order(observations, config)
    assert order == ["base", "1a", "2a", "2b"]
    from src.gc_metrics import pause_medians
    fig = render_pauses(observations, pause_medians(observations), config, "t", strategies=order, threads=[1, 2])
    dots = {c.get_label(): len(c.get_offsets()) for c in fig.axes[0].collections
            if type(c).__name__ == "PathCollection"}
    assert dots == {"Base": 3, "S1_A": 6, "S2_A": 6, "S2_B": 6}
    legend = [t.get_text() for t in fig.axes[0].get_legend().get_texts()]
    assert legend == ["Base", "S1_A", "S2_A", "S2_B", "median (per strategy and threads)"]
    fig = render_alloc(averages, config, BENCH, strategies=order, threads=[1, 2])
    assert [(label, len(rects)) for label, rects in bars(fig.axes[0])] == [
        ("Base", 1), ("S1_A", 2), ("S2_A", 2), ("S2_B", 2)]


def test_scenario_excluded_compare_table(tmp_path):
    code, paths, messages, _ = invoke("compare_main", project(tmp_path, make_rows(), ["2null"]))
    assert code == 0 and not [m for m in messages if "2null" in m or "S2_0" in m]
    table = csv_named(paths, f"compare_{BENCH}_avg.csv")
    assert table["threads_blobshuffle"].tolist() == ["1", "2"]
    assert [float(x) for x in table["mean_2a"]] == [220, 260] and [float(x) for x in table["mean_2b"]] == [200, 240]
    assert not any("2null" in Path(p).read_text().lower() for p in paths if str(p).endswith(".csv"))


# ---- scenario: Other strategies keep their values --------------------------------------------


def test_scenario_other_strategies_keep_their_values(tmp_path):
    rows = make_rows()
    outputs = {}
    for tag, exclude in (("kept", None), ("excluded", ["2null"])):
        directory = tmp_path / tag
        directory.mkdir()
        files = {}
        for name in MAINS[:3]:
            code, paths, _, _ = invoke(name, project(directory, rows, exclude))
            assert code == 0
            files.update({Path(p).name: read_csv(p) for p in paths if str(p).endswith(".csv")})
        outputs[tag] = files
    assert outputs["kept"].keys() == outputs["excluded"].keys() and len(outputs["kept"]) == 18
    checked = 0
    for name, with_all in outputs["kept"].items():
        without = outputs["excluded"][name]
        if name.endswith("_pivot.csv"):
            with_all = with_all.set_index(with_all.columns[0]).drop(index="2null").reset_index()
        elif "strategy" in with_all.columns:
            with_all = with_all[with_all["strategy"].ne("2null")].reset_index(drop=True)
        paths_differ = [c for c in without.columns if "source_file" in c]  # two folders
        with_all, without = with_all.drop(columns=paths_differ), without.drop(columns=paths_differ)
        assert with_all.equals(without), name   # every other column: scores, intervals, total_reruns, source_row
        checked += 1
    assert checked == 18
    thrpt = outputs["excluded"][f"thrpt_{BENCH}_avg.csv"]
    assert [float(s) for s in thrpt["score"]][:3] == [200, 100, 300] and thrpt["n_reruns"].eq("3").all()
    assert thrpt["total_reruns"].eq("3").all()


# ---- scenario: Bad excluded row does not stop the run ----------------------------------------


@pytest.mark.parametrize("name", MAINS, ids=MAIN_IDS)
def test_scenario_bad_excluded_row_does_not_stop_the_run(tmp_path, name):
    rows = make_rows(bad_score="abc")
    code, paths, messages, error = invoke(name, project(tmp_path, rows, ["2null"]))
    assert code == 0 and error == "" and len(paths) > 0
    assert not [m for m in messages if "2null" in m]
    other = tmp_path / "control"
    other.mkdir()
    code, paths, _, error = invoke(name, project(other, rows))
    assert code == 1 and paths == [] and "Invalid score at ledger row(s) [9, 10, 18, 19, 27, 28]" in error


# ---- scenario: no warning about excluded rows --------------------------------------------------


@pytest.mark.parametrize("name", MAINS, ids=MAIN_IDS)
def test_scenario_excluded_rows_cause_no_warning(tmp_path, name):
    rows = make_rows(drop=("2null", 2, 3))  # one 2null rerun missing
    code, _, messages, _ = invoke(name, project(tmp_path, rows, ["2null"]))
    assert code == 0 and not [m for m in messages if "2null" in m or "S2_0" in m]
    other = tmp_path / "control"
    other.mkdir()
    code, _, messages, _ = invoke(name, project(other, rows))
    assert code == 0
    if name != "compare_main":  # compare only pairs 2a with 2b, so it never warned about 2null
        assert f"{BENCH}/2null_n02_r03/thrpt missing." in messages


# ---- scenario: Ledger spelling -----------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["2NULL", " 2null ", "2null.0"])
@pytest.mark.parametrize("name", MAINS, ids=MAIN_IDS)
def test_scenario_ledger_spelling_is_excluded_too(tmp_path, name, spelling):
    code, paths, messages, _ = invoke(name, project(tmp_path, make_rows(spelling=spelling), ["2null"]))
    assert code == 0 and not [m for m in messages if "2null" in m.lower() or "S2_0" in m]
    texts = [Path(p).read_text().lower() for p in paths if str(p).endswith(".csv")]
    assert texts and not any("2null" in t for t in texts)
    if name == "main":
        assert len(csv_named(paths, f"thrpt_{BENCH}_avg.csv")) == 7


# ---- scenario: Comparison unchanged ------------------------------------------------------------


def test_scenario_comparison_unchanged(tmp_path):
    rows = make_rows()
    results = {}
    for tag, exclude in (("kept", None), ("excluded", ["2null"])):
        directory = tmp_path / tag
        directory.mkdir()
        code, paths, messages, _ = invoke("compare_main", project(directory, rows, exclude))
        assert code == 0
        results[tag] = ([Path(p).read_bytes() for p in paths if str(p).endswith(".csv")], messages)
    assert len(results["kept"][0]) == 1 and results["kept"][0] == results["excluded"][0]
    assert results["kept"][1] == results["excluded"][1]
    table = pd.read_csv(glob.glob(str(tmp_path / "excluded" / "out" / "*" / "*.csv"))[0])
    assert table["ratio_of_means"].round(6).tolist() == [1.1, 1.083333]


# ---- scenario: Excluded key absent ---------------------------------------------------------------


@pytest.mark.parametrize("name", MAINS, ids=MAIN_IDS)
def test_scenario_excluded_key_absent(tmp_path, name):
    rows = make_rows(values=KEPT)  # no 2null rows at all
    code, paths, messages, _ = invoke(name, project(tmp_path, rows, ["2null", "3"]))
    other = tmp_path / "control"
    other.mkdir()
    code_all, paths_all, messages_all, _ = invoke(name, project(other, rows))
    assert code == code_all == 0 and len(paths) == len(paths_all) > 0
    assert messages == messages_all
    assert not [m for m in messages if "2null" in m or "'3'" in m or "Unknown" in m]


# ---- scenario: Default keeps everything --------------------------------------------------------


@pytest.mark.parametrize("exclude", [None, []], ids=["setting absent", "empty list"])
def test_scenario_default_keeps_everything(tmp_path, exclude):
    code, paths, _, _ = invoke("main", project(tmp_path, make_rows(), exclude))
    assert code == 0
    average = csv_named(paths, f"thrpt_{BENCH}_avg.csv")
    assert average["strategy"].tolist() == ["base", "1a", "1a", "2null", "2null", "2a", "2a", "2b", "2b"]
    assert [float(s) for s in average["score"]] == [200, 100, 300, 140, 180, 220, 260, 200, 240]
    config = load_config(tmp_path / "config.yaml")
    _, summary = thrpt_prepare(pd.DataFrame(make_rows()), config)
    fig = thrpt_render(summary, config, "t", strategies=strategy_order(summary, config), threads=[1, 2])
    assert [t.get_text() for t in fig.axes[0].get_legend().get_texts()] == ["Base", "S1_A", "S2_0", "S2_A", "S2_B"]
    assert [len(rects) for _, rects in bars(fig.axes[0])] == [1, 2, 2, 2, 2]


# ---- scenario: Malformed setting -----------------------------------------------------------------


MALFORMED = ["2null", 5, {"a": 1}, [""], ["  "], [1], [None], [["2null"]], ["2null", "2null"]]


@pytest.mark.parametrize("value", MALFORMED, ids=[repr(v) for v in MALFORMED])
def test_scenario_malformed_setting_fails_naming_it_before_any_output(tmp_path, value):
    path = project(tmp_path, make_rows(), raw_exclude=value)
    with pytest.raises(ValueError, match=r"strategies\.exclude"):
        load_config(path)
    for name in MAINS:
        code, paths, _, error = invoke(name, path)
        assert code == 1 and paths == [] and "strategies.exclude" in error
    assert not (tmp_path / "out").exists()


# ---- README ----------------------------------------------------------------------------------------


def test_readme_documents_strategies_exclude():
    import re
    text = re.sub(r"\s+", " ", (Path(__file__).parents[1] / "README.md").read_text())
    for part in ("`strategies.exclude` is a list of strategy keys", "default: empty, so every strategy is kept",
                 "`thrpt`, `speedup`, `gc` and `compare` drop the rows of a listed strategy",
                 "before any other check", "does not stop a run", "A listed key the ledger lacks is ignored",
                 "excludes `2null` (S2_0)", "the ledger keeps its rows"):
        assert part in text, part
    plan = re.sub(r"\s+", " ", (Path(__file__).parents[1] / "strategies" / "plots_and_tables.md").read_text())
    assert "`strategies.exclude: [2null]`" in plan and "none of the four commands draws or tabulates it" in plan
