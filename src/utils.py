"""Shared ledger and output helpers, adapted from the legacy draft."""
import csv
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import math
from pathlib import Path
import re
import time
import warnings

import numpy as np
import pandas as pd


BOOTSTRAP_RESAMPLES = 10000
CI_BASE_SEED = 42
CI_METHOD = "95% percentile bootstrap of reruns"
AVERAGE_PART = "avg"
ALL_RERUNS_PART = "r_all"


_MAX_FIELD_CHARS = 2**31 - 1


def warn(message):
    warnings.warn(message, UserWarning, stacklevel=2)


def run_tag(strategy, threads, rerun):
    """The ledger's tag form: strategy, thread count and rerun."""
    return f"{strategy}_n{int(threads):02d}_r{rerun}"


def row_tag(row):
    """A row's own run_tag if it agrees with the row, or the same form built from its fields."""
    built = run_tag(row["strategy"], row["threads_blobshuffle"], row["rerun"])
    tag = str(row.get("run_tag", "")).strip()
    return tag if tag.endswith(built) else built


def warn_incomplete(benchmark, strategy, threads, group, all_reruns):
    """One readable warning per rerun that a group lacks."""
    for rerun in sorted(set(all_reruns) - set(group["rerun"])):
        warn(f"{benchmark}/{run_tag(strategy, threads, rerun)}/thrpt missing.")


def sorted_for_csv(table, config):
    """Rows by configured strategy order, threads and rerun. The last step before writing a CSV."""
    order = {name: i for i, name in enumerate(strategy_order(table, config))}
    keys = pd.DataFrame({"strategy": table["strategy"].map(order),
                         "threads": pd.to_numeric(table["threads_blobshuffle"])}, index=table.index)
    if "rerun" in table:
        numbers = pd.to_numeric(table["rerun"], errors="coerce")
        keys["rerun"] = numbers if numbers.notna().all() else table["rerun"].astype(str)
    positions = keys.reset_index(drop=True).sort_values(list(keys.columns), kind="stable").index
    return table.iloc[positions]


def strategy_key(value):
    """Canonical identifier, independent of its display label (draft behaviour)."""
    return re.sub(r"\.0$", "", str(value).strip().lower())


def load_ledger(path, delimiter=None):
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
        raise ValueError("Excel input is not supported yet; export a CSV or TSV ledger")
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            if delimiter is None:
                delimiter = csv.Sniffer().sniff(stream.read(65536), delimiters=",\t").delimiter
                stream.seek(0)
            limit = csv.field_size_limit(_MAX_FIELD_CHARS)  # JVM and JSON fields can be long.
            try:
                header, lines = _ledger_lines(csv.reader(stream, delimiter=delimiter))
            finally:
                csv.field_size_limit(limit)
        cleaned_header = [name.strip() for name in header]
        if len(set(cleaned_header)) != len(cleaned_header):
            raise ValueError("Ledger contains duplicate column names after trimming")
        frame = pd.read_csv(path, sep=delimiter, dtype=str, keep_default_na=False,
                            encoding="utf-8-sig")
    except (csv.Error, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ValueError(f"Cannot read ledger {path}: {exc}") from exc
    frame.columns = [str(c).strip() for c in frame.columns]
    if frame.columns.duplicated().any():
        raise ValueError("Ledger contains duplicate column names after trimming")
    if len(frame.columns) and frame.columns[0] == "Unnamed: 0":
        if "strategy" in frame.columns:
            raise ValueError("Ambiguous strategy: both unnamed first column and strategy exist")
        frame = frame.rename(columns={"Unnamed: 0": "strategy"})
    if len(lines) != len(frame):
        warn(f"Cannot match ledger lines to records in {path}; source_row values may be off")
    frame["source_row"] = lines if len(lines) == len(frame) else np.arange(len(frame)) + 2
    frame["source_file"] = str(path.resolve())
    return frame


def _ledger_lines(reader):
    """Header and the first line of every record; blank lines count as lines but hold no record."""
    header = next((fields for fields in reader if not _blank_record(fields)), [])
    lines = []
    previous = reader.line_num
    for fields in reader:
        if not _blank_record(fields):
            if len(fields) > len(header):
                raise ValueError(f"Ledger line {previous + 1} has {len(fields)} fields but the header "
                                 f"has {len(header)}: a trailing delimiter, wrong delimiter or broken quote")
            lines.append(previous + 1)
        previous = reader.line_num
    return header, lines


def _blank_record(fields):
    return len(fields) <= 1 and "".join(fields).strip() == ""


def numeric(frame, column, *, optional=False):
    raw = frame[column].astype(str).str.strip()
    values = pd.to_numeric(raw.mask(raw.eq("")), errors="coerce")
    bad = (~np.isfinite(values)) & (~raw.eq("") if optional else True)
    if bad.any():
        rows = frame.loc[bad, "source_row"].tolist()
        raise ValueError(f"Invalid {column} at ledger row(s) {rows}: {raw[bad].tolist()}")
    return values


def normalize_throughput(frame, benchmarks, labels, *, include_bounds=True):
    """Select and validate throughput; do not require later-feature metrics."""
    required = {"strategy", "benchmark_short", "mode", "rerun", "threads_blobshuffle",
                "score", "score_unit"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing ledger column(s): {sorted(missing)}")
    frame = frame.copy()
    # Permit programmatic DataFrames while retaining the same diagnostics.
    if "source_row" not in frame:
        frame["source_row"] = np.arange(len(frame)) + 2
    for col in ("strategy", "benchmark_short", "mode", "rerun", "score_unit"):
        frame[col] = frame[col].fillna("").astype(str).str.strip()
    frame = frame[frame["mode"].eq("thrpt") & frame["benchmark_short"].isin(benchmarks)].copy()
    absent = set(benchmarks) - set(frame["benchmark_short"])
    if absent:
        raise ValueError(f"No throughput results for benchmark(s): {sorted(absent)}")
    for col in ("strategy", "rerun"):
        if frame[col].eq("").any():
            raise ValueError(f"Missing {col} at ledger row(s) {frame.loc[frame[col].eq(''), 'source_row'].tolist()}")
    frame["strategy_original"] = frame["strategy"]
    frame["strategy"] = frame["strategy"].map(strategy_key)
    unknown = sorted(set(frame["strategy"]) - set(labels))
    if unknown:
        warn(f"Unknown strategies retained: {unknown}")
    threads = numeric(frame, "threads_blobshuffle")
    invalid_threads = (threads <= 0) | (threads % 1 != 0) | (threads >= 2**63)
    if invalid_threads.any():
        raise ValueError("threads_blobshuffle must contain positive integers within int64 range "
                         f"at ledger row(s) {frame.loc[invalid_threads, 'source_row'].tolist()}")
    frame["threads_blobshuffle"] = threads.astype("int64")
    score_columns = ("score", "score_confidence_low", "score_confidence_high", "score_error") if include_bounds else ("score",)
    for col in score_columns:
        if col not in frame:
            if col == "score":
                raise ValueError("Missing score column")
            frame[col] = ""
        # NaN in programmatic DataFrames is treated as a missing optional value.
        if col != "score":
            frame[col] = frame[col].fillna("")
        frame[f"original_{col}"] = frame[col]
        frame[col] = numeric(frame, col, optional=col != "score")
    if (frame["score"] < 0).any():
        negative = frame["score"] < 0
        context = "" if include_bounds else f"; observations: {frame.loc[negative, ['benchmark_short', 'rerun', 'strategy', 'threads_blobshuffle']].to_dict('records')}"
        raise ValueError(f"score must be nonnegative at ledger row(s) {frame.loc[negative, 'source_row'].tolist()}{context}")
    frame["original_score_unit"] = frame["score_unit"]
    unit = frame["score_unit"].str.replace("µ", "u").str.replace("μ", "u")
    factors = unit.map({"ops/s": 1, "ops/ms": 1000, "ops/us": 1_000_000})
    if factors.isna().any():
        bad = factors.isna()
        raise ValueError(f"Unsupported score_unit at ledger row(s) {frame.loc[bad, 'source_row'].tolist()}: "
                         f"{frame.loc[bad, 'score_unit'].tolist()}")
    for col in score_columns:
        frame[col] *= factors
        if (~np.isfinite(frame[col]) & frame[col].notna()).any():
            raise ValueError(f"Overflow normalizing {col}")
    frame["score_unit"] = "ops/s"
    if include_bounds:
        low, high = frame["score_confidence_low"], frame["score_confidence_high"]
        invalid = (low.notna() & (low > frame["score"])) | (high.notna() & (high < frame["score"]))
        if invalid.any():
            raise ValueError(f"Invalid confidence bounds at ledger row(s) {frame.loc[invalid, 'source_row'].tolist()}")
        incomplete = low.isna() | high.isna()
        if incomplete.any():
            warn(f"Missing ledger bounds at row(s) {frame.loc[incomplete, 'source_row'].tolist()}; error bars omitted")
            frame.loc[incomplete, ["score_confidence_low", "score_confidence_high"]] = np.nan
    identity = ["benchmark_short", "strategy", "threads_blobshuffle", "rerun"]
    duplicated = frame.duplicated(identity, keep=False)
    if duplicated.any():
        context = "" if include_bounds else f"; observations: {frame.loc[duplicated, identity].to_dict('records')}"
        raise ValueError(f"Duplicate throughput observations at ledger row(s) {frame.loc[duplicated, 'source_row'].tolist()}{context}")
    validate_comparability(frame)
    return frame


def strategy_order(observations, config):
    present = set(observations["strategy"])
    return [s for s in config.order if s in present] + sorted(present - set(config.order))


_NUMERIC_SETTINGS = {
    "forks", "warmup_iterations", "warmup_batch_size",
    "measurement_iterations", "measurement_jmh_internal_batchSize",
    "cfg_keyBytes", "cfg_valueBytes", "cfg_partitions", "cfg_batchDesiredBytes",
    "cfg_batchMaxDurationMs", "cfg_zones",
}
_TEXT_SETTINGS = {"benchmark", "jmh_version", "jdk_version", "vm_name", "vm_version", "jvm_args"}
_DURATION_SETTINGS = {"warmup_time", "measurement_time"}
# Differ by design inside one benchmark: the thread count, and the partition buffer only 1c sets.
_VARYING_JVM_ARGS = ("-Dbench.threads=", "-Dbench.partitionBufferBytes=")


def _comparable_jvm_args(text):
    """jvm_args without the flags that vary by design; text that isn't a JSON list stays as it is."""
    try:
        arguments = json.loads(text)
    except ValueError:
        return text
    if not isinstance(arguments, list):
        return text
    return json.dumps([a for a in arguments if not str(a).startswith(_VARYING_JVM_ARGS)])


_DURATION_FACTORS = {"ns": "1e-9", "us": "1e-6", "µs": "1e-6", "μs": "1e-6",
                     "ms": "1e-3", "s": "1", "m": "60", "h": "3600", None: "1"}


def _parse_duration(text):
    """Seconds as a Decimal, or None when the text is not a duration."""
    match = re.fullmatch(r"([+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(ns|us|µs|μs|ms|s|m|h)?", text)
    if not match:
        return None
    return Decimal(match[1]) * Decimal(_DURATION_FACTORS[match[2]])


def duration_seconds(text):
    """Duration such as '10 s', '500ms' or '1 m' in seconds; a bare number is seconds."""
    seconds = _parse_duration(str(text).strip())
    if seconds is None:
        raise ValueError(f"Malformed duration: {text!r}")
    return float(seconds)


def _setting(value, column):
    text = str(value).strip()
    if text == "":
        return ""
    if column in _NUMERIC_SETTINGS:
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"Malformed comparison field {column}: {text}") from exc
        if not number.is_finite():
            raise ValueError(f"Malformed comparison field {column}: {text}")
        return number
    if column in _DURATION_SETTINGS:
        seconds = _parse_duration(text)
        if seconds is None:
            raise ValueError(f"Malformed comparison field {column}: {text}")
        return seconds
    return _comparable_jvm_args(text) if column == "jvm_args" else text


def _jvm_flag(text, prefix):
    """Value of one -D flag in a jvm_args JSON list, or None."""
    try:
        arguments = json.loads(text)
    except ValueError:
        return None
    if not isinstance(arguments, list):
        return None
    return next((str(a)[len(prefix):] for a in arguments if str(a).startswith(prefix)), None)


def _check_varying_jvm_args(frame, benchmark):
    """The flags ignored by the settings check must still agree with what the rows say."""
    flags = frame["jvm_args"].map(lambda text: _jvm_flag(str(text), _VARYING_JVM_ARGS[0]))
    wrong = flags.notna() & (pd.to_numeric(flags, errors="coerce")
                             != pd.to_numeric(frame["threads_blobshuffle"], errors="coerce"))
    if wrong.any():
        raise ValueError(f"Incompatible settings in {benchmark}: -Dbench.threads differs from "
                         f"threads_blobshuffle at ledger row(s) {frame.loc[wrong, 'source_row'].tolist()}")
    buffers = frame["jvm_args"].map(lambda text: _jvm_flag(str(text), _VARYING_JVM_ARGS[1]))
    varying = buffers.groupby(frame["strategy"]).nunique(dropna=False)
    if (varying > 1).any():
        raise ValueError(f"Incompatible settings in {benchmark}: -Dbench.partitionBufferBytes differs "
                         f"within strategy {varying[varying > 1].index.tolist()}")


def validate_comparability(frame):
    columns = (set(frame.columns) & (_NUMERIC_SETTINGS | _TEXT_SETTINGS | _DURATION_SETTINGS)) | {
        c for c in frame.columns if c.startswith("cfg_") and c != "cfg_timeUnit"
    }
    for benchmark, group in frame.groupby("benchmark_short", sort=False):
        differing = []
        for column in sorted(columns):
            try:
                normalized = group[column].map(lambda value: _setting(value, column))
            except ValueError as exc:
                raise ValueError(f"{benchmark}: {exc}") from exc
            if normalized.nunique(dropna=False) > 1:
                differing.append(column)
        if differing:
            raise ValueError(f"Incompatible settings in {benchmark}: {', '.join(differing)}")
        if "jvm_args" in group:
            _check_varying_jvm_args(group, benchmark)


def output_directory(parent, feature):
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    while True:
        directory = parent / (datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S") + f"_{feature}")
        try:
            directory.mkdir()
            return directory
        except FileExistsError:  # never share a folder: wait for the next second
            time.sleep(0.05)


def safe_name(identifier):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(identifier)).strip("_") or "value"


def file_names(identifiers, kind):
    """Map identifiers to safe names, refusing two that would share a file."""
    names, seen = {}, {}
    for identifier in identifiers:
        name = safe_name(identifier)
        other = seen.setdefault(name.casefold(), identifier)
        if other != identifier:
            raise ValueError(f"{kind}s {other!r} and {identifier!r} would share the file name {name!r}")
        names[identifier] = name
    return names


def output_stem(feature, benchmark_name, part):
    return f"{feature}_{benchmark_name}_{part}"


def rerun_part(rerun_name):
    return f"r{rerun_name}"


def save_figure(fig, stem, config):
    """Save using configured output formats; callers own figure closure."""
    paths = []
    for extension in config.formats:
        path = Path(stem).with_suffix(f".{extension}")
        fig.savefig(path, dpi=config.dpi, bbox_inches="tight")
        paths.append(path)
    return paths


def _t_cdf_above_zero(t, df, steps=2000):
    """P(0 < T <= t) by Simpson integration of the t density."""
    const = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi)
    pdf = lambda x: const * (1 + x * x / df) ** (-(df + 1) / 2)
    h = t / steps
    total = pdf(0) + pdf(t)
    total += sum((4 if i % 2 else 2) * pdf(i * h) for i in range(1, steps))
    return total * h / 3


@lru_cache(maxsize=None)
def t_quantile(probability, df):
    """Student t quantile by bisection on the integrated CDF (no scipy)."""
    if not 0 < probability < 1:
        raise ValueError("probability must be between 0 and 1")
    if probability == 0.5:
        return 0.0
    if probability < 0.5:
        return -t_quantile(1 - probability, df)
    target = probability - 0.5
    low, high = 0.0, 1.0
    while _t_cdf_above_zero(high, df) < target:
        high *= 2
    for _ in range(60):
        mid = (low + high) / 2
        if _t_cdf_above_zero(mid, df) < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def t_interval(values, level=0.95):
    """Mean +/- t * sample SD / sqrt(n)."""
    values = np.asarray(values, dtype=float)
    n = len(values)
    mean = values.mean()
    half = t_quantile(1 - (1 - level) / 2, n - 1) * values.std(ddof=1) / math.sqrt(n)
    return mean - half, mean + half


def cell_seed(*parts):
    """Reproducible per-cell seed, independent of row order and other cells."""
    text = "|".join(str(p) for p in (CI_BASE_SEED, *parts))
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big") % 2**63


def bootstrap_ci(numerators, denominators=None, *, seed, resamples=BOOTSTRAP_RESAMPLES, level=0.95):
    """Percentile bootstrap over reruns; the same indices pair numerators and denominators."""
    numerators = np.asarray(numerators, dtype=float)
    n = len(numerators)
    index = np.random.default_rng(seed).integers(0, n, size=(n, resamples))
    if denominators is None:
        statistic = numerators[index].mean(axis=0)
    else:
        statistic = numerators[index].sum(axis=0) / np.asarray(denominators, dtype=float)[index].sum(axis=0)
    alpha = (1 - level) / 2
    low, high = np.quantile(statistic, [alpha, 1 - alpha])
    return float(low), float(high)


def has_spread(values):
    values = np.asarray(values, dtype=float)
    return len(values) >= 2 and values.max() - values.min() > 1e-9 * abs(values.mean())
