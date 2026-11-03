from pathlib import Path

import pytest
import yaml

from src.config import load_config


def config_file(tmp_path, **settings):
    value = {"input": {"path": "ledger.csv"}, **settings}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(value))
    return path


def test_relative_paths_and_defaults(tmp_path):
    config = load_config(config_file(tmp_path))
    assert config.ledger == tmp_path / "ledger.csv"
    assert config.output == tmp_path / "out"
    assert not config.show
    assert config.formats == ("png",)


@pytest.mark.parametrize("settings,match", [
    ({"aggregate": "median"}, "Unknown"),
    ({"reruns": [1]}, "Unknown"),
    ({"plot": {"show": "false"}}, "plot.show"),
    ({"plot": {"dpi": -1}}, "plot.dpi"),
    ({"plot": {"figsize": [12, float("inf")]}}, "figsize"),
    ({"output": {"formats": ["jpeg"]}}, "formats"),
    ({"benchmarks": []}, "benchmarks"),
    ({"strategies": {"colors": {"base": "notacolour"}}}, "colors"),
])
def test_invalid_settings(tmp_path, settings, match):
    with pytest.raises(ValueError, match=match):
        load_config(config_file(tmp_path, **settings))


def test_supplied_config():
    root = Path(__file__).parents[1]
    config = load_config(root / "config.yaml")
    assert config.ledger.is_absolute()  # Owners point input.path at their own ledger.
    assert config.labels["3"] == "S3"


@pytest.mark.skipif(not (Path(__file__).parents[1] / "docs" / "jmh_sample.csv").exists(),
                    reason="docs/jmh_sample.csv is gitignored and absent from this clone")
def test_supplied_config_points_to_an_existing_sample():
    assert load_config(Path(__file__).parents[1] / "config.yaml").ledger.is_file()
