# The t interval, explained

## 1. What a confidence interval is

You measure something 10 times. You get a mean of reruns. That mean is not the true value. A new set of 10 reruns would give a different mean.

A **confidence interval (CI)** is an error bar around the mean. It gives a range of plausible values for the true value.

### What "95%" means

95% is about the **method**, not about one interval.

Imagine you repeat the whole experiment 1000 times. Each time you compute a new interval. About 950 of those intervals contain the true value. About 50 miss it.

You only ever hold one interval. You do not know if it is one of the 950 or one of the 50. So do not say "there is a 95% chance the true value is in this interval". Say "the method that made this interval catches the true value 95% of the time".

## 2. t interval and t-test are not the same thing

They use the same **t table**. That is why they get mixed up. They answer different questions.

| | t-test | t interval |
|---|---|---|
| Question | Is there a difference? | How big is the error bar? |
| Answer | yes or no | two numbers: low and high |
| Output | a p-value | the two ends of a bar |

An interval and a test agree. A value lies outside the 95% interval exactly when the 5% t-test rejects that value. The interval just shows more: how far the bar reaches.

**Our plots use an interval.** We draw the bar and let the reader see it. `uv run compare` is the one place that decides a verdict from t. See `comparison_2a_2b.md`.

## 3. The formula

```
mean of reruns  ±  t(0.975, n-1)  ×  sd / √n
```

Here **n** is the number of reruns. For us n = 10. Now each piece.

**Mean of reruns.** Add the n values. Divide by n. This is the centre of the bar.

**sd, the standard deviation.** It says how far the values spread around the mean. Steps: subtract the mean from each value, square it, add them up, divide by **n − 1** (not n), take the square root.

Why n − 1? The mean is computed from the same values. That makes the values look a bit closer to the mean than the truth. Dividing by n − 1 instead of n corrects it.

**sd / √n, the standard error.** It is the spread of the *mean*, not of single values. A mean of many values is steadier than one value. With 10 reruns the mean is steadier by a factor of √10 = 3.16. 40 reruns instead of 10 would make the bar 0.447 of its width, a bit less than half, because t shrinks too (from 2.262 to 2.023).

**t(0.975, n − 1), the multiplier.** It turns the standard error into a 95% bar. Why not the familiar z = 1.96?

- z = 1.96 is right only if we knew the true spread.
- We do not. We guessed it from only 10 values. The guess can be too small.
- t pays for that. It is bigger than z when n is small. With 9 degrees of freedom it is 2.262, not 1.96.

**Degrees of freedom** = n − 1. It is the number of values that are free to vary once the mean is fixed. With 10 reruns, df = 9.

| df | t(0.975) |
|---|---:|
| 2 | 4.303 |
| 9 | **2.262** |
| 29 | 2.045 |
| infinity | 1.960 (this is z) |

Small n, big t, wide bar. Large n, t shrinks to z.

### Worked example: 10 small numbers

Ten reruns: 50, 52, 47, 51, 49, 53, 48, 50, 52, 48.

1. Mean = 500 / 10 = **50**.
2. Differences from the mean: 0, 2, −3, 1, −1, 3, −2, 0, 2, −2.
3. Squares added: 0 + 4 + 9 + 1 + 1 + 9 + 4 + 0 + 4 + 4 = 36.
4. Divide by n − 1 = 9: 36 / 9 = 4. Square root: sd = **2**.
5. Standard error = 2 / √10 = **0.632**.
6. Half-width = 2.262 × 0.632 = **1.431**.
7. Interval = 50 ± 1.431 = **[48.57, 51.43]**.

### The spec's example

Scores 100, 110, 120, ..., 190.

- Mean = 145.
- sd = 30.2765.
- Standard error = 30.2765 / √10 = 9.5743.
- Half-width = 2.262157 × 9.5743 = 21.658.
- Interval = 145 ± 21.658 = **[123.34, 166.66]**.

## 4. Why the replicate is the rerun

A benchmark is nested, like boxes in boxes:

```
reruns  >  forks  >  iterations  >  operations
```

Operations run inside an iteration. Iterations run inside a fork (a fresh JVM process). Forks belong to a rerun. The rerun is the whole benchmark run again from the start.

The t interval needs **independent** values. Two iterations in the same fork are not independent. They share one JVM, with the same compiled code and the same memory layout. We treat reruns as independent. This is an assumption.

So the n in the formula is the number of reruns. Each rerun is first summarized into one number. The t interval is then computed over those 10 numbers.

Kalibera & Jones (ISMM 2013, §9.3) say the same: "the multi-level interval is the same as if we had used a single-level interval for the means of all data from all but the highest level (e.g. binary means)." Their §9 intro says repetition at lower levels "is purely for optimisation of the experimental time, as repetition there is never needed for correctness".

### Pseudoreplication

Pooling all iterations of all reruns as if each were independent is called **pseudoreplication**. Take 10 reruns of 25 iterations each. Pooled, n looks like 250. Then √250 = 15.8, against √10 = 3.16 for the reruns. The divisor is 5 times too big. The bar is much too narrow. It claims more certainty than the data holds.

Thesis §2.2.4 says exactly this: pooling all iterations of all processes "is called pseudoreplication and produces uncertainty intervals that are too narrow".

### "Process level" or "rerun level"?

The thesis says "process level". It means the top level of the nesting, where each unit is a fresh JVM process measured from scratch. In this project the top level is the **rerun**. A rerun contains forks, and each fork is a process. We first summarize each rerun into one value. Those rerun values are the replicates. So the thesis words and this project's words point at the same idea: the highest level that is repeated.

## 5. What resampling (bootstrap) is

Resampling builds an error bar without a formula. It uses only the data you have.

Tiny example. Three reruns: 10, 12, 14. Mean = 12.

1. Draw 3 values from them at random, with repeats allowed. For example 10, 10, 14. Mean = 11.33.
2. Draw again. For example 12, 14, 14. Mean = 13.33.
3. Draw again. For example 10, 12, 12. Mean = 11.33.
4. Do this thousands of times. You get thousands of resampled means.
5. Sort them. Cut off the lowest 2.5% and the highest 2.5%. What is left is the **95% percentile bootstrap interval**. Its name is "resampled CI" in this project.

No t table. No formula. That is the appeal.

### Why it is too narrow with 10 reruns

Hesterberg (The American Statistician, 2015, text on PubMed Central) says: "The percentile interval is not a good alternative to standard t intervals for the mean of small samples". Also: "the common bootstrap percentile confidence interval is poor for small samples; it is like a t-interval computed using z instead of t quantiles and estimating s with a divisor of n instead of n − 1."

Those are mainly the two things from section 3, both done wrong. (The same passage also mentions a partial skewness correction, which we leave out.)

- z = 1.96 instead of t = 2.262. That alone is a factor 1.96 / 2.262 = 0.87.
- sd with divisor n instead of n − 1. That is a factor √(9/10) = 0.95.

Together: 0.87 × 0.95 = about **0.82** of the t width. With n = 10 the plain percentile bootstrap is about 0.82 of the t interval.

A bar that is 0.82 as wide as it should be catches the truth about **90%** of the time, not 95%. Exact check: if a bar uses 1.96 × √0.9 standard errors with 9 degrees of freedom, the chance of catching the truth is 90.4%.

**More resamples do not fix this.** 10 000 resamples instead of 1000 only makes the bootstrap answer less noisy. The problem is that you only have 10 reruns to resample from. That is a limit of the data, not of the computer.

## 6. What we do in this project

The owner decided this on 2026-11-02.

| Where | Interval | Columns |
|---|---|---|
| Throughput mean-of-reruns plots | 95% t interval of reruns | `t_low`, `t_high` |
| `gc_alloc_rate_norm` mean-of-reruns plots | 95% t interval of reruns | `gc_alloc_rate_norm_t_low`, `gc_alloc_rate_norm_t_high` |
| `batch_fill` mean-of-reruns plot, `mb2_rawFinalize` only | 95% t interval of reruns | `batch_fill_t_low`, `batch_fill_t_high` |
| Tables, and plots with `ci.method: resampled` | 95% percentile bootstrap | `ci_low`, `ci_high` and `<metric>_ci_low`, `<metric>_ci_high` |
| Latency mean-of-reruns plots | The method `ci.method` picks, as bands | `<p>_t_low`, `<p>_t_high`, `<p>_ci_low`, `<p>_ci_high` |
| GC `_avg` tables, every averaged metric | 95% t interval of reruns, drawn for `gc_alloc_rate_norm`, `gc_time_pct` and, for `mb2_rawFinalize`, `batch_fill` | `<metric>_t_low`, `<metric>_t_high` (blank without spread or with a blank contributor) |
| Speedup mean-of-reruns plots | 95% paired Fieller interval | `fieller_low`, `fieller_high` |
| Speedup tables, for checks | paired resampled CI, and t on the per-rerun ratios | `ci_low`, `ci_high`, `t_low`, `t_high` |
| Single-rerun throughput bars | JMH's own 99.9% bounds from the ledger | `score_confidence_low`, `score_confidence_high` |

**Status: built** (OpenSpec change `plot-t-intervals`). The mean-of-reruns plots draw the columns in the table. The t interval function is `t_interval` in `src/ci.py` and the Fieller function is `fieller_interval` there. Since `add-ci-module`, `ci.method: resampled` draws the bootstrap instead on the throughput and GC bar plots. Bar heights and all means are unchanged, and the bootstrap columns stay in the tables.

**Why speedup is different.** The plotted point of speedup is a ratio of means, not a plain mean. The t formula is for a plain mean, so it can't go straight on a ratio. Paired Fieller is the t version for a ratio. It finds every ratio θ for which a t interval on (strategy − θ × base), across the reruns, contains zero. It uses the same t value and keeps each strategy score paired with its base score from the same rerun. The bar then matches the plotted point. Sources: Fieller, JRSS B, 1954, https://doi.org/10.1111/j.2517-6161.1954.tb00159.x ; von Luxburg & Franz, Statistica Sinica 19(3), 2009, https://arxiv.org/pdf/0711.0198 .

**Why single-rerun bars are different.** With one rerun there is nothing to resample or to take a t over. The bars come from JMH itself, over the iterations of that one rerun. That is a different thing, at a different level, at 99.9%. Do not compare them with the 95% bars.

## 7. Assumptions and limits

The t interval is only as good as these:

1. **Reruns are independent.** One rerun does not influence the next.
2. **Reruns are alike.** They measure the same thing under the same conditions.
3. **The mean of 10 reruns is close to normal.** Do not argue "the central limit theorem makes it fine". The 2012 Kalibera & Jones report (§3.2) rejects people who "incorrectly state that the Central Limit Theorem is enough to overcome it". With only 10 values that argument does not hold. Ten reruns cannot prove normality. We state the assumption and show the rerun values. On the final ledger we will look at skew and at scores in time order. That check is planned, not built. The t interval is a safe choice (§8.6 of the report: "using the t-distribution even when normality assumptions cannot be made seems a safe choice").

Hoefler & Belli (SC 2015) give a rule for this: "Do not assume normality of collected data (e.g., based on the number of samples) without diagnostic checking."

### How badly can it fail?

Our own simulations (not citable). Each dataset is 10 reruns. Nominal level 95%. The table shows how often the interval caught the true mean. t: 40,000 datasets per row. Bootstrap: 4,000 datasets per row, 1,000 resamples each, so its numbers carry about 0.5 points of noise.

| Data shape | t interval | percentile bootstrap |
|---|---:|---:|
| Normal | 95.1% | 90.3% |
| Skewed (lognormal, skew 0.8) | 94.2% | 88.5% |
| Very skewed (exponential) | 90.2% | 86.3% |

Read it like this:

- With normal data the t interval is near 95%. The exact value is 95.0%.
- With strong skew, t falls to about 90%.
- The bootstrap is lower than t in every row.

#### A rare large effect

Setup: each rerun is "odd" with probability 10%. An odd rerun is the normal value plus a shift of 10 sd. Two ways to define the truth:

| Truth is | t interval | percentile bootstrap |
|---|---:|---:|
| the mean including the odd reruns (shift 10 sd) | 71.6% | 68.1% |
| the clean mean (shift 3 sd) | 93.7% | 85.4% |
| the clean mean (shift 10 sd) | 96.6% | 80.0% |
| the clean mean (shift 20 sd) | 97.0% | 80.7% |

Where do the misses come from? Look at the first row, split by how many odd reruns the dataset has (shift 10 sd):

| Odd reruns in the 10 | share of datasets | t coverage |
|---|---:|---:|
| 0 | 35.0% | 19.8% |
| 1 | 38.9% | 100% |
| 2 | 19.4% | 100% |
| 3 | 5.7% | 100% |

The misses come from datasets with **no** odd rerun. They look clean, so the bar is narrow. It misses the true mean, which the rare effect pulled away. When an odd rerun is in the data, the sd grows and the bar widens, and it catches the truth.

A rare large effect is the danger: with 10 reruns you may never see it. An odd rerun you do see widens the bar. Investigate it and keep it.

### The limit of the whole study

All results hold only for the one EC2 instance the benchmarks ran on. The interval says how steady the mean is across reruns on that instance. It says nothing about other machines.

## 8. Corrections to the owner's Evaluation notes

| The note said | The truth |
|---|---|
| No paper prescribes a bootstrap. | Laaber et al. (2019) use one. The 2012 Kalibera & Jones report describes one (§7.2.1). |
| You must resample reruns, then forks. | Wrong. Resampling only the top level (reruns) is valid (Field & Welsh, JRSS B, 2007). |
| Never resample raw iterations. | Half right. Do not pool them flat across reruns. Resampling iterations *within* a fork is what the 2012 report and Laaber do. |

**A trap.** Laaber attributes "1000 bootstrap iterations" to Kalibera & Jones 2013. That number is in the 2012 report, not in the 2013 paper.

## 9. Exam questions

**Why not bootstrap?**
At 10 reruns the plain percentile bootstrap is about 0.82 of the t width and catches the truth about 90% of the time, not 95% (our simulation; Hesterberg 2015 says it is "poor for small samples"). We still compute it, and keep it in the tables as a sanity check.

**Laaber says a simple interval would be invalid.**
Laaber et al. (§5.1) write: "Due to the non-normality of performance data, a simple computation of the confidence interval of the mean would be invalid." They argue from non-normal raw performance data, and they resample down to the iterations. We apply t to 10 means of 25 scores each, as Kalibera & Jones 2013 §9.3 prescribe. Ten reruns cannot prove normality. We state the assumption and show the rerun values. A check of skew and time order on the final ledger is planned, not built.

**Is it exact?**
No. The Kalibera & Jones paper calls it "the asymptotic confidence interval". Their 2012 report says "We do not claim that Y has a t-distribution" and §8.4.1 says "In most cases, the asymptotic method would be the best choice." It is a good and defended choice, not an exact one.

**Georges et al. (2007) use t too. Is that the same argument?**
They also use t with n − 1 degrees of freedom for n < 30. But they argue that "computing confidence intervals does not require that the underlying data is Gaussian". The other sources reject that argument. We do not use it.

**Why 95% and not 99%?**
It is the usual level. We state it in the plot title. The single-rerun bars are 99.9% and are labelled as JMH's own.

**What if the rerun values are skewed or have an odd one?**
Strong skew lowers coverage to about 90% (our simulation). An odd rerun that you see widens the bar. Investigate it and keep it. The real danger is a rare large effect that none of your 10 reruns shows.

## Sources

- Kalibera & Jones, "Rigorous Benchmarking in Reasonable Time", ISMM 2013, §9.3. https://doi.org/10.1145/2464157.2464160 (peer reviewed).
- Kalibera & Jones, Univ. of Kent TR 4-12, 2012. https://arxiv.org/pdf/2007.10899 (not peer reviewed).
- Hesterberg, "What Teachers Should Know About the Bootstrap", The American Statistician 69(4):371-386, 2015. https://doi.org/10.1080/00031305.2015.1089789
- Georges, Buytaert & Eeckhout, OOPSLA 2007. https://doi.org/10.1145/1297027.1297033
- Hoefler & Belli, SC 2015. https://doi.org/10.1145/2807591.2807644
- Laaber, Scheuner & Leitner, Empirical Software Engineering 24(4), 2019. https://doi.org/10.1007/s10664-019-09681-1
- Field & Welsh, JRSS B 69(3), 2007. https://doi.org/10.1111/j.1467-9868.2007.00593.x (abstract only was read)
- Simulation numbers in section 7 are ours and are not citable.
