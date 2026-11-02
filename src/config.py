"""Safe YAML configuration with explicit, small supported settings."""
from dataclasses import dataclass, field
from pathlib import Path
import math

import yaml
from matplotlib.colors import is_color_like


# Presentation defaults adapted from blobplot.py.
STRATEGY_LABELS = {
    "base": "Base", "1a": "S1_A", "1b": "S1_B", "1c": "S1_C",
    "2null": "S2_0", "2a": "S2_A", "2b": "S2_B", "3": "S3", "4": "S4",
}


@dataclass(frozen=True)
class Config:
    ledger: Path
    output: Path
    delimiter: str | None = None
    benchmarks: tuple[str, ...] = ("mb1_rawNoFinalize", "mb2_rawFinalize")
    labels: dict[str, str] = field(default_factory=lambda: dict(STRATEGY_LABELS))
    order: tuple[str, ...] = tuple(STRATEGY_LABELS)
    colors: dict[str, str] = field(default_factory=dict)
    formats: tuple[str, ...] = ("png",)
    figsize: tuple[float, float] = (12, 6)
    dpi: int = 150
    show: bool = False
    comparison_margin: float | None = None


def _mapping(value, location, allowed=None):
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be a mapping")
    if allowed is not None:
        unknown = set(value) - set(allowed)
        if unknown:
            raise ValueError(f"Unknown {location} setting(s): {sorted(unknown, key=str)}")
    return value


def _strings(value, location):
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{location} must be a nonempty list of nonempty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{location} contains duplicate entries")
    return tuple(value)


def load_config(path):
    path = Path(path).resolve()
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    raw = _mapping(raw, "config", {"input", "output", "benchmarks", "strategies", "plot", "comparison"})
    source = _mapping(raw.get("input", {}), "input", {"path", "delimiter"})
    output = _mapping(raw.get("output", {}), "output", {"directory", "formats"})
    strategies = _mapping(raw.get("strategies", {}), "strategies", {"labels", "order", "colors"})
    plot = _mapping(raw.get("plot", {}), "plot", {"figsize", "dpi", "show"})

    comparison = _mapping(raw.get("comparison", {}), "comparison", {"margin"})
    margin = comparison.get("margin")
    if "margin" in comparison and (isinstance(margin, bool) or not isinstance(margin, (int, float))
                                   or not 0 < margin < 1):
        raise ValueError("comparison.margin must be a number greater than 0 and less than 1 (a fraction, not a percent)")

    def resolve(value, name):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty path string")
        return (path.parent / Path(value).expanduser()).resolve()

    ledger = resolve(source.get("path"), "input.path")
    destination = resolve(output.get("directory", "out"), "output.directory")
    delimiter = source.get("delimiter", "auto")
    if delimiter not in ("auto", ",", "\t"):
        raise ValueError("input.delimiter must be auto, ',' or a tab")
    benchmarks = _strings(raw.get("benchmarks", list(Config.benchmarks)), "benchmarks")
    labels = dict(STRATEGY_LABELS)
    for key, value in _mapping(strategies.get("labels", {}), "strategies.labels").items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip():
            raise ValueError("strategies.labels must map nonempty string identifiers to labels")
        labels[key] = value
    order = _strings(strategies.get("order", list(labels)), "strategies.order")
    colors = _mapping(strategies.get("colors", {}), "strategies.colors")
    if any(not isinstance(k, str) or not isinstance(v, str) or not is_color_like(v)
           for k, v in colors.items()):
        raise ValueError("strategies.colors must map string identifiers to valid colours")
    formats = _strings(output.get("formats", ["png"]), "output.formats")
    if set(formats) - {"png", "pdf", "svg"}:
        raise ValueError("output.formats supports png, pdf, svg")
    size = plot.get("figsize", [12, 6])
    if not isinstance(size, list) or len(size) != 2 or any(
        type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in size
    ):
        raise ValueError("plot.figsize must contain two positive finite numbers")
    dpi = plot.get("dpi", 150)
    if type(dpi) is not int or dpi <= 0:
        raise ValueError("plot.dpi must be a positive integer")
    show = plot.get("show", False)
    if type(show) is not bool:
        raise ValueError("plot.show must be true or false")
    return Config(ledger, destination, None if delimiter == "auto" else delimiter,
                  benchmarks, labels, order, colors, formats, tuple(size), dpi, show,
                  None if margin is None else float(margin))
