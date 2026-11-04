"""Confidence interval arithmetic and the plot method setting, shared by every feature."""
from functools import lru_cache
import hashlib
import math

import numpy as np


BOOTSTRAP_RESAMPLES = 10000
CI_BASE_SEED = 42
CI_METHOD = "95% percentile bootstrap of reruns"
T_METHOD = "95% t interval of reruns"
FIELLER_METHOD = "95% paired Fieller interval of reruns"

# The words `ci.method` accepts in config.yaml.
T = "t"
RESAMPLED = "resampled"
CI_METHODS = (T, RESAMPLED)
_TITLES = {T: T_METHOD, RESAMPLED: CI_METHOD}
_NAMES = {T: "t interval", RESAMPLED: "resampled CI"}
_COLUMNS = {T: "t", RESAMPLED: "ci"}


def method_title(method):
    """The method as plot titles and `interval_kind` say it."""
    return _TITLES[method]


def method_name(method):
    """The method as warnings say it."""
    return _NAMES[method]


def interval_columns(method, metric=None):
    """(low, high) table columns of a method: `t_low` or, with a metric, `<metric>_t_low`."""
    prefix = f"{metric}_" if metric else ""
    return f"{prefix}{_COLUMNS[method]}_low", f"{prefix}{_COLUMNS[method]}_high"


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


def fieller_interval(strategy_scores, base_scores, level=0.95):
    """Paired Fieller interval for mean(strategy) / mean(base) over reruns.

    Solves (ms - theta*mb)^2 = t^2 * (vss - 2*theta*vsb + theta^2*vbb) / n for theta, with sample
    (ddof=1) variances and covariance of the paired scores and t on n-1 degrees of freedom.
    Returns (low, high), or None when the base mean cannot be told apart from zero at this level
    (the theta^2 coefficient is not positive). The discriminant is never negative in exact
    arithmetic; its check only guards against rounding.
    """
    s = np.asarray(strategy_scores, dtype=float)
    b = np.asarray(base_scores, dtype=float)
    n = len(s)
    ms, mb = s.mean(), b.mean()
    vbb = b.var(ddof=1)
    vsb = float(np.sum((s - ms) * (b - mb)) / (n - 1))
    k = t_quantile(1 - (1 - level) / 2, n - 1) ** 2 / n
    a = mb * mb - k * vbb
    if not a > 0:
        return None
    half_b = ms * mb - k * vsb  # half of the linear coefficient, sign flipped
    # half_b^2 - a*c expanded, because subtracting those two huge numbers loses all precision when
    # the ratios barely vary: k*(mb^2*var(s - point*b) - k*(vss*vbb - vsb^2)).
    residual = vbb * (s - (vsb / vbb) * b).var(ddof=1) if vbb > 0 else 0.0
    disc = k * (mb * mb * (s - (ms / mb) * b).var(ddof=1) - k * residual)
    if not disc >= 0:
        return None
    root = math.sqrt(disc)
    return (half_b - root) / a, (half_b + root) / a


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
