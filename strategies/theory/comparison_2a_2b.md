# The 2A vs 2B comparison, explained

Command: `uv run compare`. Code: `src/compare.py`. Where this file and the code disagree, the code is right.

Every number below was run through the code, unless it says otherwise.

## 1. The question

**Are 2A and 2B the same speed?**

Both are variants of Strategy 2 (thesis §3.8.1). In Strategy 2 each thread has its own buffers, and its own topology, so the partitions must be merged at the end. 2A and 2B do this merge in two different ways (thesis §3.9.2 and §3.10.1):

| | How it merges |
|---|---|
| 2A | One cursor per thread. It repeatedly takes the smallest partition among the cursor heads. No sort. |
| 2B | Gathers every non-empty pair of thread and partition buffer into one list, sorts the list by partition, then joins the pieces. |

The thesis says the merge order is the only variable between them (§3.10.3). The output is byte-identical (§3.10.2).

We compare their throughput. Throughput is ops per second. We do it for two benchmarks, mb1 and mb2, and we do it **separately** for each:

- mb1 has no finalization. The handoff to the S3 writer is not measured.
- mb2 includes finalization and the handoff.
- Neither measures the S3 write.

### "No difference found" is not "the same"

Say a test finds no difference. That can have two causes. The two are really the same. Or the data are too noisy to see a difference. The test cannot tell these apart.

The thesis says this in §2.2.4: an interval that includes no effect does not establish equivalence. Equivalence needs an explicitly stated margin.

So we do two things:

1. We state a margin before we look: 5%.
2. We ask the data to prove the gap is **inside** the margin. This is called an equivalence test.

If the data are too noisy, the answer is "inconclusive". That is an honest answer.

### The table and the plot

`uv run compare` writes one table per benchmark. It also saves one plot per benchmark, `compare_<benchmark>_ratio`, drawn from the same table. Per thread count the plot shows one dot at `logt_ratio`, a thick bar for the equivalence interval, a thin bar for the difference interval, a line at 1, and (with a margin) lines at 1 + margin and 1 / (1 + margin). The plot adds no numbers: the verdict, the note and the flags stay in the table.

Read equivalence from the thick bar against the dashed lines, and a difference from the thin bar against the solid line. A thin bar may cross a dashed line in a row that is equivalent. Do not say "98% confident of equivalence" (section 5).

## 2. Pairing

One rerun is one complete sweep of all configurations. We have 10 reruns. Each rerun measures 2A and 2B.

A rerun has its own conditions. One rerun may be a bit slow, another a bit fast. Both 2A and 2B suffer from this the same way inside one rerun. So we compare 2A with 2B **from the same rerun**. That is a pair.

Small example, 3 reruns:

| Rerun | 2A (ops/s) | 2B (ops/s) | 2A / 2B |
|---|---:|---:|---:|
| 1 | 80 | 79 | 1.0127 |
| 2 | 120 | 118 | 1.0169 |
| 3 | 100 | 99 | 1.0101 |

If we ignore the pairs, we compare two piles. The mean of reruns is 100.0 for 2A and 98.7 for 2B. The spread inside each pile is huge: SD 20.0 and 19.5. The gap of 1.3 is lost inside it.

If we use the pairs, we look at the ratio per rerun. The three ratios are 1.0127, 1.0169, 1.0101. They are close together. The rerun noise cancelled out.

Why it cancels: say a rerun makes everything 20% slower. Then 2A becomes 0.8 × 2A and 2B becomes 0.8 × 2B. The ratio is the same. The 0.8 divides away.

Limits:

- It removes the noise that is **shared** by 2A and 2B in one rerun. It does not remove noise that hits only one of them.
- The thesis says reruns on one instance are repeated comparison blocks, not independent machines (notes_for_thesis.md §2, a planned thesis edit). The result is about this machine and this period.

The code is strict. If one rerun has 2A but not 2B at some thread count, the command stops and names the benchmark, thread count and rerun. It also stops if fewer than 2 reruns exist, or if a score is zero or negative. It warns if the number of reruns is not 10.

## 3. Why logs

We want to say "2A is x times 2B". A ratio is awkward to average. Look:

| Rerun | 2A / 2B | 2B / 2A |
|---|---:|---:|
| 1 | 2.0 | 0.5 |
| 2 | 0.5 | 2.0 |

Arithmetic mean of 2A/2B: (2.0 + 0.5) / 2 = 1.25. That says 2A is 25% faster. Arithmetic mean of 2B/2A: also 1.25. That says 2B is 25% faster. Both cannot be true. The data are perfectly balanced.

Now take the natural log, written ln:

- ln(2.0) = +0.693
- ln(0.5) = −0.693

Equal size, opposite sign. A ratio of 2 and a ratio of 1/2 are the same distance from "no difference", which is 1. On a log scale 1 becomes 0. Mean of the logs = 0. Back-transform with exp: exp(0) = 1. Correct: no difference.

The result of "take logs, take the mean, then exp" is the **geometric mean ratio**. In the table it is the column `logt_ratio`.

- 1.00 means the same.
- 1.05 means 2A is 5% faster.
- 0.95 means 2A is 5% slower.

Swap 2A and 2B and you get exactly 1 / `logt_ratio`. No other choice is needed.

The table also has `ratio_of_means`: the mean of 2A over the mean of 2B. That is the same kind of ratio as in the speedup plots. It is shown so you can link the two. It does not decide anything. In the worked example of section 4 they are 1.008 and 1.008034. The advisor's own simulation (not citable) puts the gap at 0.09% at most, with rerun noise up to 3%.

### Why log-t and not Fieller

Fieller is another way to make an interval for a ratio of means. It was the research report's first choice. The owner chose log-t instead. Reasons:

- Log-t is simple: take logs, use the paired t interval, take exp.
- Both fit the thesis. §2.2.4 already says ratios are usually analysed on a log scale.
- In the research agent's simulation (our own, not citable), the two gave the same verdict in at least 99.4% of datasets, and both had correct coverage at 90, 95, 98 and 99%.
- Keene 1995 argues that logs are the standard for positive data (abstract only checked).
- Pairing is not an argument here. The per-rerun ratio removes the rerun factor (section 2). The log only turns that ratio into a difference. Fieller can be paired too.

The cost, which the thesis must state: the speedup plots use a ratio of means. Hoefler and Belli [35] Rule 4 prefers summarising the rates when they are available. So the table uses a different centre, and we say why.

## 4. The t interval on the paired log ratios

Take one benchmark and one thread count. We have n pairs. For each pair:

```
y = ln(score_2A / score_2B)
```

Then:

- ȳ = the mean of the y values.
- s = the sample SD of the y values (divide by n − 1).
- se = s / √n. This is the standard error: how much ȳ itself wobbles.

The interval for the true log ratio is:

```
ȳ − t × se   to   ȳ + t × se
```

Take exp of both ends to get an interval for the ratio.

What is t? It is a number from **Student's t distribution** with n − 1 degrees of freedom. This is the "t table". You pick the confidence level, you look up t. With 10 reruns, n − 1 = 9. We use t because s is estimated from only 10 numbers, which adds extra doubt. t is bigger than the normal-curve number, so the interval is wider. The code computes t itself; no table is typed in.

### Worked example, 10 literal pairs

This is the example from the spec. Thread count family has 5 thread counts.

| Rerun | 2A | 2B | 2A / 2B | y = ln ratio |
|---|---:|---:|---:|---:|
| 1 | 101 | 100 | 1.0100 | 0.00995 |
| 2 | 103 | 102 | 1.0098 | 0.00976 |
| 3 | 98 | 98 | 1.0000 | 0.00000 |
| 4 | 102 | 101 | 1.0099 | 0.00985 |
| 5 | 100 | 99 | 1.0101 | 0.01005 |
| 6 | 103 | 103 | 1.0000 | 0.00000 |
| 7 | 98 | 97 | 1.0103 | 0.01026 |
| 8 | 101 | 100 | 1.0100 | 0.00995 |
| 9 | 102 | 102 | 1.0000 | 0.00000 |
| 10 | 100 | 98 | 1.0204 | 0.02020 |

Steps:

1. Sum of y = 0.08002. ȳ = 0.008002.
2. s = 0.006362 (this is `sd_log_ratio_pct` = 0.636%).
3. se = 0.006362 / √10 = 0.002012.
4. `logt_ratio` = exp(0.008002) = **1.008034**.
5. 99% interval: t = 3.2498 (9 degrees of freedom). Half-width = 3.2498 × 0.002012 = 0.006538. Log interval 0.001463 to 0.014540. After exp: **[1.001464, 1.014647]**.
6. 98% interval: t = 2.8214. Half-width = 0.005677. After exp: **[1.002328, 1.013773]**.

Also: 2A is above 2B in 7 of 10 reruns (`reruns_2a_gt_2b` = 7). `ratio_of_means` = 100.8 / 100.0 = 1.008.

Why two intervals? That is section 6. Now we need the margin.

**What the interval means:** if the pairs are independent and the log ratios roughly normal, then this method catches the true ratio at the stated level. Ten reruns cannot prove normality ([35] Rule 6, per the research report). So the level is **nominal**. I reran a small simulation (our own, not citable). Setup: 10 reruns, normal noise with SD 2% on the log ratio, and each rerun has a 10% chance to be an outlier shifted up by some number of SDs. The true mean includes the outliers. I measured how often the nominal 99% interval covers that true mean. With a 10 SD shift, it covers 82.5%. With a 5 SD shift, 96.4%. With a 3 SD shift, 98.6%. The risk is outliers that never show up in your 10 reruns (in this setup, 35% of datasets had none). An odd rerun that you do see inflates the SD and widens the interval, which is safe. That is why the table shows the SD and the count of reruns where 2A is above 2B.

## 5. Equivalence testing (TOST) and the margin

### The margin

The margin is 5%: `comparison.margin: 0.05` in `config.yaml`. In words: 2A and 2B count as the same if the ratio 2A/2B is between **1/1.05 and 1.05**.

- 1.05 = 2A is 5% faster.
- 1/1.05 = 0.95238 = 2B is 5% faster, the mirror image.

It is symmetric on the log scale: ln(1.05) = +0.04879 and ln(1/1.05) = −0.04879. That matches section 3.

### TOST in plain words

TOST means "two one-sided tests". We make two claims, and need both:

1. The true ratio is **not below** 1/1.05. (2A is not too slow.)
2. The true ratio is **not above** 1.05. (2A is not too fast.)

Each is a one-sided test with error rate α. We call two things equivalent only if **both** tests succeed. Berger and Hsu 1996 give the theory. Lakens 2017 explains it for practice, and Walker and Nowacki 2011 say the margin must be fixed before the data are seen (all three: see references). The original TOST paper is Schuirmann 1987 (J. Pharmacokinetics and Biopharmaceutics, https://doi.org/10.1007/BF01068419). I have not verified it, and its venue is rejected in strategies/comparison_2a_2b.md unless the supervisor accepts it, so cite Lakens 2017 instead.

Berger and Hsu were read as abstract only. Read the paper before you defend this orally.

### The shortcut we use

Each one-sided test, at error rate α, succeeds exactly when one end of a **1 − 2α interval** is on the right side of the margin. Why 2α: a two-sided interval at level 1 − 2α leaves α in each tail. So the rule is:

> Equivalent if the whole 1 − 2α interval lies inside 1/1.05 to 1.05.

That is what the code does: `equiv_low > 1/(1+margin)` and `equiv_high < 1+margin`. This is a simple fact about one-sided t tests. The reasoning is ours. Lakens 2017 (open access, https://pmc.ncbi.nlm.nih.gov/articles/PMC5502906/) says the same thing for his case. He writes: "If the 90% CI falls within the equivalence bounds, the observed meta-analytic effect is statistically equivalent." A footnote there gives the reason: "because two one-sided tests (each with an α of 5%) are performed." So he uses a 90% interval for two 5% tests. We use 98% for two 1% tests. Same rule.

Caution from Berger and Hsu (abstract only checked, so read the paper first): in general, tests of size α do not match 100(1 − 2α)% confidence sets. So we never say "98% confident of equivalence". We say: the 1% tests on both sides both succeed.

Example from the worked example above, margin 5%: the 98% interval is [1.0023, 1.0138]. It is inside [0.9524, 1.05]. So the verdict is **equivalent**.

### Why 5%, and why before the results

From the comment in `config.yaml` (the margin was fixed on 2026-11-01, while the final runs were still running and before anyone had seen 2A vs 2B results). If the margin were picked after looking, we could choose it to give the answer we like. Walker and Nowacki say it must be fixed first.

The four reasons:

- **Cost.** 2A and 2B write byte-identical objects (thesis §3.10.2), so S3 and cross-AZ costs match. A gap costs only compute. 5% is the most we accept to ignore.
- **Provable.** In our own simulation (10 reruns, not citable): with 2% rerun noise and 0.5 correlation between 2A and 2B, a truly equal pair passes a 5% margin about 100% of the time, but a 2% margin only about 34%. At 3% noise it is 95% against 4%. A tighter margin would mostly report noise as "inconclusive".
- **Still strict.** A true gap of exactly 5% is wrongly called equivalent at most about 1% of the time, in each row. (That is the α = 0.01 of each one-sided test.)
- **Open.** No published standard sets this number. It is a stated project decision. The table gives the smallest provable margin per row, so a reader can apply a tighter one.

## 6. Many comparisons: why 99% and 98%

We do the test for 5 thread counts, in each benchmark. That is 5 chances to be fooled. If each used a 5% error rate, the chance of at least one false claim would be about 1 − 0.95⁵ = 23%. (This is plain arithmetic for independent tests, shown only to give a feeling.)

The fix is Bonferroni (Dunn 1961): split the 5% error rate over the F thread counts.

```
α = 0.05 / F
```

For F = 5: α = 0.05 / 5 = **0.01**.

| Claim | Test type | Interval level | F = 5 | F = 4 |
|---|---|---|---|---|
| "2A faster" or "2B faster" (a difference) | two-sided, α in total | 1 − α | **99%** | 98.75% |
| "equivalent" | two one-sided, α each | 1 − 2α | **98%** | 97.5% |

The levels differ because the two claims are built differently:

- A difference claim is one two-sided test. Its error rate α is split over two tails, α/2 each. So the interval has level 1 − α.
- An equivalence claim is two one-sided tests, each with the full α in one tail. So the interval leaves α on each side. The interval has level 1 − 2α, which is lower, so the interval is narrower.

The 98% interval always sits inside the 99% interval. So a row that excludes 1 at 99% also excludes it at 98%.

The reasoning that this keeps the family error near 5% is our own (research report Q4), not from a source. Dunn 1961 is checked at abstract level only.

**mb1 and mb2 are separate.** Each benchmark has its own F, its own table, its own verdicts. No correction across the two. This is the owner's decision. A benchmark with 4 thread counts uses 98.75% and 97.5%; this is `levels()` in the code. Say it in the thesis: each benchmark answers its own question.

## 7. The verdicts

The code picks the first rule that matches (`decide()`):

1. **no spread observed.** The per-rerun ratios are all the same (apart from rounding). With no spread there is no way to build an interval. The code leaves the intervals and the smallest margin empty. It does not test the SD against 0, because rounding leaves a tiny SD like 1e-12. It checks the range of the ratios instead (`has_spread`).
2. **equivalent.** A margin is set, and the 1 − 2α interval is inside 1/1.05 to 1.05. If the 1 − α interval also excludes 1, a note is added: "2A x% faster, below margin" (or 2B). x is `logt_ratio − 1`, or `1/logt_ratio − 1`.
3. **2A faster.** The 1 − α interval's low end is above 1.
4. **2B faster.** The 1 − α interval's high end is below 1.
5. **inconclusive.** Anything else.

For rows 3 and 4 with a margin set, a note says how big the gap is:

- **beyond margin**: the whole 1 − α interval is outside the margin. For 2A: low > 1.05. For 2B: high < 1/1.05 = 0.9524.
- **size unclear**: the gap is real (excludes 1), but we cannot say it is bigger than 5%.

Without a margin, row 2 is skipped (no "equivalent" is ever given) and the notes stay empty. One warning is printed.

Examples. F = 5, margin 5%. 2B is 100 in each of the 10 reruns, to keep it simple. The 2A values are listed.

| 2A values | `logt_ratio` | 99% interval | 98% interval | Verdict | Note |
|---|---:|---|---|---|---|
| 99.5, 100.5, 99, 101, 100, 100.5, 99.5, 100, 99.5, 100.5 | 1.0000 | [0.9936, 1.0064] | [0.9944, 1.0056] | equivalent | none |
| 100.5, 101, 100.2, 101.3, 100.8, 100.6, 101, 100.4, 100.9, 100.9 | 1.0076 | [1.0042, 1.0110] | [1.0046, 1.0106] | equivalent | 2A 0.8% faster, below margin |
| 110, 111, 109, 112, 110, 111, 109, 110, 111, 110 | 1.1030 | [1.0933, 1.1127] | [1.0945, 1.1115] | 2A faster | beyond margin |
| 104, 106, 105, 107, 103, 106, 105, 104, 107, 105 | 1.0519 | [1.0385, 1.0655] | [1.0402, 1.0637] | 2A faster | size unclear |
| 90, 91, 89, 90.5, 90, 91, 89.5, 90, 90.5, 89.5 | 0.9010 | [0.8942, 0.9078] | [0.8951, 0.9069] | 2B faster | beyond margin |
| 96.2, 94.3, 95.2, 93.5, 97.1, 94.3, 95.2, 96.2, 93.5, 95.2 | 0.9506 | [0.9384, 0.9630] | [0.9400, 0.9613] | 2B faster | size unclear |
| 100, 110, 95, 105, 98, 112, 96, 104, 99, 108 | 1.0254 | [0.9661, 1.0884] | [0.9737, 1.0799] | inconclusive | none |
| 100 in every rerun (2B also 100) | no intervals | empty | empty | no spread observed | none |

Look at the "size unclear" rows. The 99% interval says 2A is faster. But its low end (1.0385) is below 1.05, so we cannot say the gap exceeds the margin. And the 98% interval reaches past 1.05, so it is not equivalent either. Both claims fail. Only "faster, size unclear" remains.

"Equivalent" and "faster" can be true at once (row 2). Then equivalence wins, because it is the stronger statement about size, and the note says which side is faster.

### The `smallest_provable_margin` column

It answers: **how small could the margin have been, so that this row still counts as equivalent?**

```
smallest_provable_margin = exp( |ȳ| + t_equiv × se ) − 1
```

It is the far end of the 98% interval, mirrored to the larger side, minus 1. Examples from the table above:

- Equivalent row 1: 0.56%. Any margin more than 0.56% would pass. Exactly 0.56% fails, because the code uses strict inequalities.
- Equivalent row 2: 1.06%.
- Spec worked example: 1.3773%. Check: the upper end of its 98% interval is 1.013773.
- The inconclusive row: 7.99%. At 5% it fails. At 8% it would pass.

It does not depend on the configured margin. Readers can apply a tighter margin than 5% themselves. It is empty when there is no spread.

## 8. Flags

Flags are warnings in the `flags` column, separated by `;`. **They never change a number or a verdict.** They tell the reader that the comparison may be less clean. Source: design.md and `flag_list()`.

| Flag | Raised when |
|---|---|
| `host_differs` | The contributing rows (2A and 2B) come from more than one `hostname`. |
| `sha_differs` | Within 2A, or within 2B, there is more than one `git_sha`. |
| `dirty_build` | Any contributing row has `git_dirty` that is not "false" (blank also counts). |
| `work_differs` | The mean of `aux_recordBytes` / `original_score` differs between 2A and 2B by more than 0.1%, or cannot be computed. |
| `order_unbalanced` | The count of reruns where 2A ran first and the count where 2B ran first differ by more than 2. |
| `order_unknown` | Some `run_timestamp` is missing, unparsable or tied between 2A and 2B. |

Notes:

- `work_differs` is an "equal work" check from the research report (Q6): the ratio is meant to be the same for both strategies. The 0.1% and the 2 (for order) are the advisor's proposals, not sourced values.
- Order: in each rerun, one of the two runs first. Two columns report it: `order_2a_first` (how many reruns 2A ran first) and `order_adjusted_ratio_geo` (the ratio after a regression on order). They are for reading only. The owner decided: no order balancing. So order is reported, and never changes the verdict.
- Some conditions (settings covered by `validate_comparability`) are not flags. They stop the command.

Why flags do not change numbers: one fixed rule makes the table easy to defend. A flag is a note to the reader, like a footnote. The reader decides what it means.

There are also `boot95_low` and `boot95_high`: a 95% bootstrap interval of the ratio of means, for reference. It never decides.

## 9. Why few reruns give "inconclusive"

The interval width is `t × s / √n`. Few reruns hurt twice:

1. √n is small. √3 = 1.73. √10 = 3.16.
2. t is large, because s is estimated from so few numbers.

The t values:

| Level | t with 3 reruns (2 df) | t with 10 reruns (9 df) | Ratio |
|---|---:|---:|---:|
| 99% | 9.925 | 3.250 | 3.05 times bigger |
| 98% | 6.965 | 2.821 | 2.47 times bigger |

Same data spread, say s = 1% and mean ratio exactly 1. The smallest provable margin (98% level) is:

- 10 reruns: 0.90%.
- 3 reruns: 4.10%.

So 3 reruns need a 4.1% margin to prove equivalence even in a perfect case. At 10 reruns, 0.9% is enough. This shows why 10 reruns help.

### What `docs/raw3.csv` shows

`docs/raw3.csv` has 3 reruns. I ran `uv run compare` on it with a copy of `config.yaml` in the scratch folder. The command warned "3 reruns instead of 10". Result, 5 thread counts per benchmark (1, 2, 4, 6, 8):

| Benchmark | inconclusive | equivalent |
|---|---:|---:|
| mb1 | 5 | 0 |
| mb2 | 3 | 2 (threads 6 and 8) |

Four rows (threads 2 and 8, in both benchmarks) carry `order_unbalanced`. With 3 reruns, that means the same strategy ran first in all three (3 − 0 = 3, which is more than 2). Flags do not change the verdicts.

**These verdicts say nothing about 2A vs 2B.** Three reruns are too few. A tiny SD from 3 numbers can happen by luck, and the equivalence in two rows can be one such luck. The final verdicts come from 10 reruns. We draw no conclusion about the strategies from this file.

## 10. What it cannot tell you, and exam questions

### What it cannot tell you

- **Not the mechanism.** It says whether the speeds are equal. It does not say why.
- **Only this machine.** All reruns use one instance. Intervals do not cover other instances.
- **Only these benchmarks.** mb1 and mb2 as the owner defines them (see section 1). Not the S3 write.
- **Nominal levels.** The 99% and 98% hold only if the log ratios are roughly normal and the pairs are independent. Ten reruns cannot prove that. Outlier reruns hurt (section 4 gives the simulation).
- **No order correction.** Order is reported, not corrected.
- **Margin is a decision.** 5% is stated, not derived from a standard.

### Exam questions

**Why not just compare the error bars of 2A and 2B?**
Overlapping intervals around two separate means are not a test of their difference (thesis §2.2.4; Kalibera and Jones [38], "Visual tests"). They also ignore the pairing, so the rerun noise stays in. We build one interval for the ratio itself. Schenker and Gentleman 2001 are suggested in notes_for_thesis.md for this sentence. They are not in the thesis bibliography yet, and I did not check their content.

**Why not Cohen's d?** (owner decision, 2026-11-01)
The ratio with its interval already is the effect size, in units a reader can use. There are several versions of d: on an invented example, pooled d gave 0.42 and the paired version d_z gave 1.26 (our own calculation). d_z is just t/√n, so it adds nothing to the paired test. With very low noise a tiny gap gives a huge d, and d is undefined with no spread. The "small, medium, large" labels come from behavioural science. Sources: Kalibera and Jones [38]; Baguley 2009 (abstract only checked); Lakens 2013 (weak venue; do not rely on it alone). The thesis must disclose that Hoefler and Belli [35] recommend a standardised effect size.

**Why not Fieller?**
It also works, and gave the same verdict in at least 99.4% of datasets in our simulation (not citable). The owner chose log-t because it is simpler and fits §2.2.4. The cost is a centre that differs from the speedup plots, which the thesis states.

**Why log and not plain ratios?**
2.0 and 0.5 should balance. On the log scale they do (+0.693 and −0.693). Swapping 2A and 2B gives exactly the reciprocal.

**Why 5%?**
The cost of a gap is only compute. It is provable with our noise. A true 5% gap is wrongly called equivalent at most about 1% of the time. No published standard exists; it is a stated decision, fixed before the results (section 5).

**Why was the margin fixed first?**
So we cannot pick it to suit the data (Walker and Nowacki 2011).

**Why is 98% lower than 99%?**
Equivalence is two one-sided tests. Each has the full α in one tail. So its interval is 1 − 2α (section 6).

**Why not correct across mb1 and mb2?**
Owner decision: they are two separate questions, each with its own table.

**What if the data are too noisy?**
The verdict is "inconclusive", and `smallest_provable_margin` shows how big a margin the data could support.

**Why does the table not use the bootstrap?**
A percentile bootstrap interval is too narrow at 10 reruns (Hesterberg 2015, per notes_for_thesis.md; about 96% real coverage for a "99%" interval in our simulation). So it is only a reference column.

## References

Only sources listed in `strategies/notes_for_thesis.md`, `strategies/comparison_2a_2b.md` or the thesis bibliography. "Abstract only" means the research agent read only the abstract.

- [28] A. Georges, D. Buytaert, L. Eeckhout. 2007. Statistically Rigorous Java Performance Evaluation. OOPSLA '07, pp. 57-76. doi:10.1145/1297027.1297033. (Thesis bibliography. Used here only for fresh processes and repetition.)
- [35] T. Hoefler, R. Belli. 2015. Scientific Benchmarking of Parallel Computing Systems. SC '15. doi:10.1145/2807591.2807644. (Thesis bibliography. Rule 4: summarise rates; Rule 6: normality.)
- [38] T. Kalibera, R. E. Jones. 2013. Rigorous Benchmarking in Reasonable Time. ISMM '13, pp. 63-74. doi:10.1145/2464157.2464160. (Thesis bibliography. CORE C venue: not sole support.)
- Berger, Hsu. 1996. Bioequivalence Trials, Intersection-Union Tests and Equivalence Confidence Sets. Statistical Science 11:283-319. https://doi.org/10.1214/ss/1032280304 (abstract only; supports TOST and the 1 − 2α caution)
- Walker, Nowacki. 2011. Understanding Equivalence and Noninferiority Testing. J Gen Intern Med 26:192-196. https://doi.org/10.1007/s11606-010-1513-8 (full text; margin fixed before data; non-significance is not equivalence)
- Lakens. 2017. Equivalence Tests: A Practical Primer for t Tests, Correlations, and Meta-Analyses. Soc Psychol Personal Sci 8:355-362. https://doi.org/10.1177/1948550617697177 (full text; TOST outcomes; resource-based bounds when no better anchor)
- Dunn. 1961. Multiple Comparisons Among Means. JASA 56:52-64. https://doi.org/10.1080/01621459.1961.10482090 (abstract only; Bonferroni-style intervals)
- Keene. 1995. The Log Transformation Is Special. Statistics in Medicine 14:811-819. https://doi.org/10.1002/sim.4780140810 (abstract only)
- Baguley. 2009. Standardized or Simple Effect Size: What Should Be Reported? Br J Psychol 100:603-617. https://doi.org/10.1348/000712608X377117 (abstract only)
- Lakens. 2013. Calculating and Reporting Effect Sizes to Facilitate Cumulative Science. Front Psychol 4:863. https://doi.org/10.3389/fpsyg.2013.00863 (cited in notes_for_thesis.md; do not rely on it alone)
- Hesterberg. 2015. What Teachers Should Know About the Bootstrap. Am Stat 69(4):371-386. https://doi.org/10.1080/00031305.2015.1089789
- Schenker, Gentleman. 2001. On Judging the Significance of Differences by Examining the Overlap Between Confidence Intervals. Am Stat 55(3):182-186. https://doi.org/10.1198/000313001317097960 (suggested in notes_for_thesis.md; content not checked by me)

Unverified, so do not cite as fact: all "our simulation" figures (they are not peer reviewed); the 0.1% and 2 flag tolerances (advisor's proposals); Berger and Hsu section details (not read in full).
