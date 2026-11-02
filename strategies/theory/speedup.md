# Speedup, explained

## The question speedup answers

**How many times more work per second does this strategy do than plain single-threaded BlobShuffle?**

Speedup 1 means the same as single-threaded Base. Speedup 4 means four times as much. It has no unit, because ops/s divided by ops/s cancels out.

Base at one thread is the fixed reference for everything: it has no threading machinery, so it shows what the code does with no locking cost.

```
speedup = throughput of the strategy at N threads / throughput of Base at 1 thread
```

## Step 1: one rerun

Each rerun measures Base and every strategy, so each rerun gives its own speedup. We always divide by the Base from the same rerun, because those two were measured in the same sweep.

| Rerun | Base at N=1 (ops/s) | S1_A at N=4 (ops/s) | Speedup in this rerun |
|---|---:|---:|---:|
| 1 | 100 | 200 | 200 / 100 = 2.00 |
| 2 | 200 | 300 | 300 / 200 = 1.50 |

These are the per-rerun plots. Each point is one division, so there is nothing to put an error bar on.

## Step 2: one number across all reruns

The rerun results need combining into one line. There are two ways to do it, and they give different answers.

| Method | Calculation | Result |
|---|---|---|
| Mean of ratios (what we used to do) | (2.00 + 1.50) / 2 | 1.75 |
| **Ratio of means (what we do now)** | ((200 + 300) / 2) / ((100 + 200) / 2) = 250 / 150 | **1.67** |

### Why they differ

The mean of ratios gives each rerun's ratio equal say, however much work happened in that rerun. Rerun 1 had the slower Base, so its ratio has as much weight as rerun 2's, even though rerun 2 did more work.

The ratio of means works on the actual work done. Reruns last the same time, so it is the same as this: the strategy did 500 ops in total and Base did 300, and 500 / 300 = 1.67. That is the same calculation as 250 / 150, and it reads as "across everything we measured, the strategy did 1.67 times the work".

### Why we chose ratio of means

- **Your sources:** Hoefler & Belli [35] §3.1.1 say "ratios should never be averaged", and Kalibera & Jones [38] §10.1 use a ratio of means.
- **It barely moves real numbers:** the gap above is large only because this example has very different reruns. In the research agent's simulation, at up to 3% variation between reruns, the two methods differed by less than 0.1%.

So this makes the method defensible rather than changing your conclusions.

### What happens if a rerun is missing

If a strategy has no result in one rerun, that rerun's Base is left out too, so both means always cover the same reruns.

| Rerun | Base | Strategy |
|---|---:|---:|
| 1 | 100 | 200 |
| 2 | 200 | 300 |
| 3 | 900 | missing |

The result is still 250 / 150 = 1.67 with coverage 2/3. Including the 900 would compare the strategy against a Base from a rerun it was not part of.

## Step 3: the error bar

The method is a 95% percentile bootstrap, with a t interval reported beside it.

We have one speedup number, and the CI asks how much it could plausibly move given how much the reruns varied. We get that by resampling reruns, without rerunning any benchmark.

Take three reruns:

| Rerun | Base | Strategy |
|---|---:|---:|
| 1 | 100 | 200 |
| 2 | 200 | 300 |
| 3 | 150 | 330 |

The real result is (200 + 300 + 330) / (100 + 200 + 150) = 830 / 450 = **1.84**. Equal rerun counts cancel, so sums give the same answer as means.

Now pretend we had happened to get a different set of reruns, by picking three at random with repeats allowed:

| Reruns picked | Strategy total | Base total | Speedup |
|---|---:|---:|---:|
| 2, 2, 3 | 300 + 300 + 330 = 930 | 200 + 200 + 150 = 550 | 1.69 |
| 1, 1, 2 | 200 + 200 + 300 = 700 | 100 + 100 + 200 = 400 | 1.75 |
| 1, 3, 3 | 200 + 330 + 330 = 860 | 100 + 150 + 150 = 400 | 2.15 |

Do this 10,000 times and you get 10,000 speedups. Sort them: the value 2.5% of the way up is the bottom of the CI, and the value 97.5% of the way up is the top.

**A picked rerun brings its strategy and its Base together.** We never combine the strategy from rerun 2 with the Base from rerun 3. That pairing is the benefit of measuring both in the same sweep: if a whole rerun runs slightly fast or slow, it affects both sides, and that shared effect cancels.

**Base at N=1 gets no CI.** Its speedup is Base / Base, which is exactly 1 in every resample, so there is no spread.

## Reading the plot

```
speedup
  8 |                         /  dashed line: ideal, y = N
    |                       /
  4 |                  /   ___--- a strategy flattening out
    |             / __--
  1 |. . . ._/_ . . . . . . . . . dotted line: y = 1, single-threaded Base
    |  /
    +----------------------------
       1   2   4   6   8   threads
```

| What you see | What it means |
|---|---|
| Line on the dashed diagonal | Perfect scaling: N threads give N times the work |
| Line below the diagonal | Sub-linear: it gains less than each added thread could give |
| Line above the diagonal | Super-linear: unusual, so check it before believing it |
| Line flattening | Adding threads stops helping. Something is limiting it, but the shape alone does not say what |
| Line below the dotted y = 1 | Slower than single-threaded Base despite using more threads |
| Strategy at N=1 below 1 | The cost of the threading machinery when there is nothing to gain from it |

## What the program writes

`uv run speedup` writes, per benchmark:

| Output | Contents |
|---|---|
| One plot and CSV per rerun | The per-rerun speedups, with `score`, `baseline_score` and `speedup` |
| One plot and CSV across reruns | `speedup` (the ratio of means), `mean_score`, `mean_baseline_score`, `n_reruns`, `total_reruns`, and the CI columns `ci_low`, `ci_high`, `ci_method`, `ci_resamples`, `ci_seed`, `t_low`, `t_high`, `t_basis` |

The per-rerun CSV already holds everything the resampling will need, so no new table is required.
