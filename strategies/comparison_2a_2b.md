> Owner decisions of 2026-11-01 override this report: paired log-t decides (no Fieller), no Cohen's d, no primary benchmark, and at that time no plot. The owner added a ratio plot on 2026-11-03 (`add-compare-plot`). See `openspec/changes/archive/2026-11-02-add-2a-2b-comparison/design.md`.

# 2A vs 2B comparison

The research agent's full report from 2026-11-01, unedited below the line. It is planned as OpenSpec change `add-2a-2b-comparison`. Its thesis changes are summarized in [notes_for_thesis.md](notes_for_thesis.md). The simulation scripts it names live in a temporary session folder and may no longer exist.

---

# 2A vs 2B: procedure, evidence and thesis alignment

Nothing was edited. I aligned this with the new plot decision (95% plain percentile bootstrap with the t interval beside it) and I do not recommend the expanded percentile. The verdict table uses a different interval, and the reason is in Q2.

Numbers marked "my simulation" are mine, not peer reviewed and not citable. They come from `/tmp/claude-1000/-home-lesheid-workspaces-thesis-visualization/3b802712-e8dd-4361-89fc-923857fb35fa/scratchpad/ab/sim.py`. The worked example comes from `/tmp/claude-1000/-home-lesheid-workspaces-thesis-visualization/3b802712-e8dd-4361-89fc-923857fb35fa/scratchpad/ab/ex.py`. No real data was analysed, because `jmh_sample.csv` is synthetic.

## Read this first
1. **The ±2% margin in the idea file can only be proven if noise is low.** At n = 10 and a true ratio of 1, you need SD of log ratios ≤ about 1.5% for 80% power. At CV 2% and ρ = 0.5, it passes only 34% of the time (my simulation). See Q8.
2. **The 99% + 90% pair is inconsistent for per-thread claims.** The difference test is adjusted but the equivalence test is not. Use 99% + 98%, or keep 90% and make only one joint claim across all N.
3. **I could not confirm what `mb1_rawNoFinalize` times.** It is not defined in the thesis, and the source is not on this machine or in the public BlobShuffle repo. If the merge is not in its timed region, mb1 cannot answer this question and becomes a negative control.
4. **`runner.conf` still says `RERUNS=3`, with the comment "run each on a different day".** That contradicts the fixed 10 reruns on one instance.
5. **The 2B sort may be cheaper than §3.10.1 says.** Per-thread runs are already ascending (§3.9.1), and stable sorts such as TimSort exploit existing runs. Check which sort is used before keeping "O(M log M)". I could not see the code.

---

# 1. The recommended procedure

**Definition: ratio R = 2A/2B on throughput.** R > 1 means 2A is faster.

## Inputs
From `ledger_all.csv` (same columns as `jmh_sample.csv`), keep rows with `mode == thrpt` and strategy in {2a, 2b}. Strategy is the first, unnamed column.

| Column | Use |
|---|---|
| `benchmark_short`, `threads_blobshuffle`, `rerun` | Pair keys. Do not use `threads_jmh`, which is 1 on every row in the sample. |
| `score` | The rerun value (A_i for 2a, B_i for 2b) |
| `run_timestamp` | Which ran first, and drift |
| `cfg_*`, `jvm_args`, `jdk_version`, `hostname`, `git_sha`, `git_dirty`, `aux_*` | Pre-checks |

## Steps, for each benchmark and N
**Step 0: fix before the final data.** Fix the margin m, the primary benchmark and the family size. Write them down with a date. Lower bound L = 1/(1+m), upper bound U = 1+m.

**Step 1: pair.** Require exactly 10 complete pairs, otherwise stop with an error.

**Step 2: run the pre-checks** (Q6). Failures add a flag to the row and do not silently change the analysis.

**Step 3: point estimate.** R = Ā / B̄, the ratio of means.

**Step 4: paired Fieller interval.** This is the set of θ for which the paired t test on A_i − θB_i does not reject. Using sample variances s_AA and s_BB and covariance s_AB (divisor 9):
- q = t²/10
- a = B̄² − q·s_BB
- b = Ā·B̄ − q·s_AB
- c = Ā² − q·s_AA
- lo, hi = (b ∓ √(b² − a·c)) / a

If a ≤ 0, the interval is unbounded and the verdict is inconclusive.

t values for df = 9:

| Family | Difference CI | t | Equivalence CI | t |
|---|---|---|---|---|
| 5 comparisons (default, mb2 primary) | 99% | 3.2498 | 98% | 2.8214 |
| 10 comparisons (both benchmarks confirmatory) | 99.5% | 3.6897 | 99% | 3.2498 |

**Step 5: verdict** (first match wins).

| Condition | Verdict |
|---|---|
| SD of log ratios is 0 | "no spread observed", no verdict (the synthetic sample hits this at every N) |
| 98% CI lies inside (L, U) | **equivalent**. If the 99% CI also excludes 1, add "2A/2B x% faster, below margin". |
| 99% lo > 1 | **2A faster** |
| 99% hi < 1 | **2B faster** |
| Otherwise | **inconclusive** |

Note beside "faster":
- 99% CI entirely beyond the margin: "beyond margin"
- 99% CI crosses the margin: "size unclear"

**Global sentence for the thesis:**
- "Merge order does not matter on this benchmark, within ±m, at the tested N" only if all five rows say equivalent.
- "Merge order matters" if at least one row says faster.
- Otherwise, report that the result is mixed.

## Output table
**In the thesis:** N, mean 2A, mean 2B, R, 99% CI, 98% equivalence CI, smallest provable margin, SD of log ratios (%), reruns with 2A > 2B (k of 10), verdict.

Smallest provable margin = max(hi98, 1/lo98) − 1. Readers can apply their own margin with this, which takes the heat out of the margin debate.

**Sensitivity columns (never used for the verdict):**
- geometric mean ratio with paired log-t 99% CI
- 95% plain percentile bootstrap of R over whole reruns (same indices for A and B, same B and seed policy as the plots)
- 95% Fieller interval, the t interval for a ratio, matching the "t beside it" plots
- order-adjusted ratio
- flags

If the log-t verdict differs from the Fieller verdict, set a flag and report the row as inconclusive.

## Worked example (hand-checkable, invented numbers)
A = 101, 103, 98, 102, 100, 103, 98, 101, 102, 100
B = 100, 102, 98, 101, 99, 103, 97, 100, 102, 98

- Ā = 100.8, B̄ = 100, R = **1.008**
- s_AA = 29.6/9 = 3.2889, s_BB = 36/9 = 4, s_AB = 31/9 = 3.4444

| Level | t | q | a | b | c | √(b² − ac) | CI |
|---|---|---|---|---|---|---|---|
| 99% | 3.2498 | 1.05614 | 9995.775 | 10076.362 | 10157.166 | 65.725 | **[1.00149, 1.01464]** |
| 98% | 2.8214 | 0.79605 | 9996.816 | 10077.258 | 10158.022 | 57.063 | **[1.00234, 1.01375]** |

With m = 2%, L = 0.98039 and U = 1.02.
- The 98% CI is inside (L, U), so the verdict is **equivalent**.
- The 99% CI excludes 1, so the note reads "2A 0.8% faster, below margin".
- Smallest provable margin = 1.38%.

**TOST cross-check** on A − θB:
- θ = 0.98039: mean 2.7608, SD 0.6162, t = +14.17, which is above 2.8214.
- θ = 1.02: mean −1.2, SD 0.6510, t = −5.83, which is below −2.8214.

Both reject, which agrees with the interval.

**Log-t check:** geometric ratio 1.00803, s_d = 0.636%, 99% CI [1.00146, 1.01465]. Same verdict.

---

# Answers to the eight questions

**Q1. Effect statistic: ratio of means.**
- [35] Rule 4 says to summarise the rates behind a ratio and use the geometric mean only if those are unavailable. They are available here.
- [38] §10.1 also estimates a ratio of means.
- Both statistics are direction-invariant: 2B/2A is the exact reciprocal. So reciprocity does not separate them, although it does rule out the arithmetic mean of ratios.
- The numbers barely differ. The gap has p99.9 ≤ 0.10% at CV 3%, against an estimator SD of 1–1.3% (my simulation).
- On §2.2.4's log-scale sentence: "usually" remains true but the thesis would not do it, so reword it (see the alignment section).
- The strongest argument for logs: they remove a multiplicative rerun factor exactly, and they are the standard equivalence analysis [Keene]. That is why log-t stays as the sensitivity column.

**Q2. Interval: paired Fieller. Bonferroni is right here.**
- Fieller gives one estimand, R, and needs no extreme bootstrap quantiles. The covariance term is what keeps the pairing. [38] §10.1 gives Fieller without a covariance term; Fieller 1954 itself covers estimated covariances.
- Coverage (my simulation, 3,000–100,000 datasets per cell):

| Nominal | Fieller | Log-t | Plain percentile |
|---|---|---|---|
| 90% | 90% | 90% | 84% |
| 95% | 95% | 95% | 90% |
| 98% | 98% | 98% | 94% |
| 99% | 99% | 99% | 96% |

- Fieller and log-t agreed on the verdict in at least 99.4% of datasets. With 10% one-sided outlier reruns, every method dropped (81–98% at nominal 99%, in the rows with outliers).
- **Why the table may differ from the plots.** Plain percentile is acceptable as a descriptive error bar if labelled, but a "99%" percentile interval is only about a 96% interval at n = 10. Hesterberg says percentile intervals tend to be too narrow and under-cover in small samples. Verdicts need the stated error rate. The plots' CIs also cannot decide 2A vs 2B, because they sit around separate means (§2.2.4 overlap sentence).
- **Level.** Bonferroni is valid under any dependence (reruns are shared across N) and suits a small pre-chosen family [Dunn]. Perneger's objection is about specific hypotheses mixed with a universal null. Here the global question "does merge order matter at any tested N?" is the universal null, which he names as an appropriate case. Šidák gives about the same level.
- **Two benchmarks.** Do not automatically correct across both. Declare mb2 primary (§2.2.2 says primary metrics are selected beforehand), and report mb1 as a secondary or control row. If both are confirmatory, use the 10-comparison row of Step 4.

**Q3. TOST is the right claim. A margin is required.**
- Non-significance alone does not establish equivalence [Walker; §2.2.4 already says this]. TOST rejects differences at both ends of the margin [Lakens 2017].
- Caution: Berger & Hsu argue that size-α tests do not generally correspond to 100(1−2α)% confidence sets. So treat the 98% interval only as the calculation of a 1% test. Do not call it "98% confidence of equivalence".
- **Proposed way to choose the margin, with no 2A vs 2B data:**
  1. **Decision anchor.** 2A and 2B produce byte-identical objects (§3.10.2), so S3 and cross-AZ costs are identical. The only cost difference is CPU capacity, so m is the extra compute you would ignore in exchange for 2B's independence from buffer order. Only the user can set this cap. I suggest no more than 5%.
  2. **Resolution anchor: an A/A pilot.** Run 2A twice per rerun under two labels, for mb2 and thrpt only, for 5 pilot reruns. That is about 7 hours by my calculation (5 N × 2 runs × 500 s × 5 reruns). The true ratio is 1, so this shows the noise without showing the answer. Take s_AA as the largest SD of log ratios across N. It needs a runner entry but no new JMH code. Exclude the pilot from the final analysis.
  3. **Rule.** m = the smallest of {2%, 3%, 5%} with m ≥ 1.36·s_AA. If none qualifies, declare before the final run that equivalence cannot be claimed, and report only faster/inconclusive and the smallest provable margin.
  4. **Be open about it.** If the resolution anchor sets the value, say so. The claim is then "no difference beyond what this design resolves". Lakens 2017 allows bounds set by resources when no better anchor exists.
- ±2% has no literature source, and neither do the ladder values. Do not present them as standards.

**Q4. Verdict rule: Step 5.**
- Reasoning (mine, not from a source): each N gets α = 1%, and at any true ratio essentially only one kind of false claim is possible. If the true ratio is 1, "faster" is the error. If it is outside the margin, "equivalent" is the error. So 99% + 98% keeps the family near 5%.
- The 98% interval sits inside the 99% one, so every combination is defined.
- Different-and-equivalent resolves to "equivalent" with a note, matching Lakens 2017's four outcomes.
- The 99% + 90% pair is valid only for the joint claim "all five within margin": by the intersection-union logic of Berger 1982, testing each and requiring all to pass needs no adjustment. It does not support individual per-N equivalence claims.

**Q5. Effect size: drop Cohen's d.**
- The ratio with its interval is the effect size [38 "Effect size" paragraph; Baguley].
- Pooled d (§2.2.4) includes shared rerun variation and has no pairing.
- d_z (idea file) is just t/√n, so it adds nothing. With the example's numbers, d_z ≈ 1.26.
- Baguley prefers simple effect sizes, and Dunlap warns that correlation inflates effect sizes computed from paired statistics.
- Change: remove d from Feature 4, and report the SD of log ratios instead. Fix §2.2.4 as below.

**Q6. Pre-checks.**

| Check | How | From ledger? |
|---|---|---|
| Same setup | one value of `cfg_*`, `jvm_args`, `jdk_version`, `hostname`; `git_dirty` false; one `git_sha` per strategy | Yes |
| Equal work | `aux_recordBytes`/`score` constant across strategies (1032 B/op in sample); `aux_batches` × batch bytes ≈ `aux_recordBytes`; similar `batch_fill` | Yes |
| Execution order | Recover from `run_timestamp`; count 2A-first per N | Yes |
| Order effect | OLS of y = log(A/B) on o = ±1 (2A first or not). exp(intercept) is the order-adjusted ratio. | Yes |
| Drift | y and both scores against time; slope × 9 reruns | Yes |
| Rerun outliers | Dots of y; k of 10; `stdev_agg_forks`. `stdevs` appears to hold one value per fork (not confirmed). | Yes |
| Fork outliers | Per-fork means | `ledger_raw` only |
| Warm-up | Warm-up lines against measurement iterations, at N = 1 and 8, for 2a and 2b. A fixed warm-up guarantees nothing ([37], [24], [53]). | No: `results/logs/<tag>.log` |
| Profiler overhead | Runs with and without profilers | No: new runs |
| Merge in timed region | Read source of mb1 and mb2 | No: source |
| Carryover | See below | Only indirectly |

- **Do not use a carryover pre-test to pick the analysis.** Freeman shows that this two-stage approach inflates error rates. Report the order-adjusted column, and do not switch. Fresh JVMs remove JVM-level carryover (§2.3.2). Machine-level carryover cannot be separated from the order effect.
- **Order imbalance is real at seed 42.** I replayed the runner's shuffle for 10 reruns. This depends on the `shuf` version, so `run_timestamp` remains the truth.

| N | 2A first (of 10) | Mean gap between 2A and 2B (job positions) |
|---|---|---|
| 1 | 6 | 1.7 |
| 2 | 3 | 2.2 |
| 4 | 6 | 2.1 |
| 6 | 4 | 2.5 |
| 8 | 4 | 2.4 |

- An order effect π biases log R by π × (2B-first − 2A-first)/10. At N = 2 that is 0.4π. If the final runs have not started, forcing 5/5 per N removes this. It requires a `runner.sh` change, so that is the user's call.
- Timing, by my calculation: one job is about 33 minutes if each job runs both benchmarks in both modes, so a rerun is about 23 hours. 2A and 2B at the same N are typically about 1–1.5 hours apart, while the thread counts run in fixed ascending order. That order does not affect within-N comparisons.
- A hidden setup bias between the two builds cannot be excluded ([44], and the fixed instance). The scope is "these builds on this instance".

**Q7. Merge-only benchmark: optional for the question. New JMH work.**
- **Not needed** to answer "does merge order affect throughput here?". The throughput benchmark with an equivalence verdict answers that, if mb2 times the merge.
- **Needed if** you want either of these:
  - to explain the merge cost itself
  - to test §3.10.1's expectation that the difference grows with threads and partitions, which fixed P = 216 cannot test

  It is also the natural next step if most rows are inconclusive. §3.8.5 p.38 already foreshadows it.
- **Sample-mode tails cannot substitute.** One op is one record (1032 B), so 16 MiB is about 16,257 ops per batch. The merge hits about 1 op in 16,000, beyond p99.99, and that region also mixes with GC pauses.
- **Design:**
  - Single-threaded run, timing only the merge call, with the result passed to a Blackhole (§2.3.4, [26]).
  - State holds several frozen collector sets built from real writes, cycled to avoid hot-cache repetition (§2.3.3).
  - Use trial-level setup if the merge leaves the state unchanged. Otherwise use per-invocation setup and note its effect.
  - `@Param` N and partitions, sweeping upward.
  - Sample-time mode: the score gives the mean, and percentiles come with it.
  - Same replication and Fieller steps, reporting absolute µs per merge and the ratio.
- **Bridge to throughput (my approximation):** effect ≈ ΔT_merge(N) × aux_batches(N) / N, with both in µs, assuming only the finalizing thread stalls (§3.9.1). Illustration only: assumed ΔT = 100 µs, and the synthetic rate scaled linearly to N = 8, gives about 0.6%. Measure this rather than trusting it. If all threads stalled, it would be N times larger.
- §3.9.3 p.40 already expects the difference "may be small", so an "equivalent" verdict would be unsurprising and not very informative. This bridge calculation says whether it was predictable.

**Q8. Power at n = 10.** Define s_d = SD of log ratios ≈ CV·√(2(1−ρ)), where ρ is the 2A–2B correlation across reruns. Half-widths are t·s_d/√10:
- 90%: 0.580·s_d
- 98%: 0.892·s_d
- 99%: 1.028·s_d

80% power (my simulation, 400,000 draws per point):
- Detect a difference at 99%: true effect 1.34·s_d. At 99.5%: 1.49·s_d.
- Prove equivalence when the true ratio is 1: 98% needs m = 1.36·s_d; 90% needs 1.01·s_d.
- Prove equivalence when the true effect is half the margin: 98% needs 2.38·s_d; 90% needs 1.71·s_d.

| CV | ρ | s_d | Half-width 99% | Half-width 98% | Half-width 90% | Detectable at 99% | Provable margin (98%) | Provable margin (90%) |
|---|---|---|---|---|---|---|---|---|
| 1% | 0 | 1.41% | 1.45% | 1.26% | 0.82% | 1.9% | 1.9% | 1.4% |
| 1% | 0.5 | 1.00% | 1.03% | 0.89% | 0.58% | 1.3% | 1.4% | 1.0% |
| 2% | 0.5 | 2.00% | 2.06% | 1.78% | 1.16% | 2.7% | 2.7% | 2.0% |
| 2% | 0.8 | 1.26% | 1.30% | 1.13% | 0.73% | 1.7% | 1.7% | 1.3% |
| 3% | 0 | 4.24% | 4.36% | 3.79% | 2.46% | 5.7% | 5.8% | 4.3% |
| 3% | 0.5 | 3.00% | 3.08% | 2.68% | 1.74% | 4.0% | 4.1% | 3.0% |
| 3% | 0.8 | 1.90% | 1.95% | 1.69% | 1.10% | 2.5% | 2.6% | 1.9% |

Verdict rates with m = 2% and true ratio 1 at ρ = 0.5 (my simulation):

| CV | Equivalent |
|---|---|
| 1% | 99.6% |
| 2% | 34% |
| 3% | 3.6% |

ρ is unknown until measured, and the runner's spacing means it may be well below 1.

---

# 2. Ranked alternatives

## 1. Ratio of means with paired Fieller, 99% difference and 98% equivalence
What it is: the procedure above. For each candidate ratio θ, t-test the rerun values A_i − θB_i.
Why it fits: it uses the same estimand as the speedup and throughput summaries, keeps the pairing, has one mechanism for both the CI and TOST, and needs no extreme bootstrap tails.
Trade-offs / risks: it is less familiar than log-t. It assumes roughly normal paired contrasts, which 10 reruns cannot confirm ([35] Rule 6). Outlier reruns hurt it like everything else. Only the 98% rule costs width: about 54% wider than 90%.
What to take from each source:
- [A] The Fieller construction, with variances and covariances jointly estimated (abstract).
- [B] Fieller fits the general linear model, so ratios of linear combinations are covered (abstract). Supporting only.
- [C] Bonferroni-style pre-selected t-intervals with a guaranteed overall level (abstract).
- [D] TOST and intersection-union logic, ratio problems are no harder than difference problems, and the warning about 1−2α confidence sets (abstract only).
- [E] The margin must be fixed before data, and non-significance does not show equivalence (full text).
- [F] The four NHST + TOST outcomes, and the resource-based fallback for bounds (full text).
- [G] Prefer simple effect sizes with CIs (abstract).
- [H] Same-instance randomized test and control can detect slowdowns of 10% or less (abstract). It uses rank-sum and overlapping bootstrap intervals, not this method, so it supports the design only.
References:
- [A] E. C. Fieller, "Some Problems in Interval Estimation", JRSS Series B 16:175–185, 1954. https://doi.org/10.1111/j.2517-6161.1954.tb00159.x — SJR Q1 (secondary source; SCImago blocked) — VERIFIED
  Checks: metadata=Crossref · link=DOI resolves to OUP · content=abstract · rank=aggregator pages
- [B] G. O. Zerbe, "On Fieller's Theorem and the General Linear Model", The American Statistician 32:103–105, 1978. https://doi.org/10.1080/00031305.1978.10479267 — JCR Q1 per Peeref, older listing Q2; treat rank as uncertain — VERIFIED except rank
  Checks: metadata=Crossref · link=DOI resolves to T&F · content=abstract via OpenAlex · rank=earlier report's secondary page
- [C] O. J. Dunn, "Multiple Comparisons Among Means", JASA 56:52–64, 1961. https://doi.org/10.1080/01621459.1961.10482090 — SJR Q1 (secondary) — VERIFIED
  Checks: metadata=Crossref · link=resolves to T&F · content=abstract · rank=aggregator
- [D] R. L. Berger, J. C. Hsu, "Bioequivalence Trials, Intersection-Union Tests and Equivalence Confidence Sets", Statistical Science 11:283–319, 1996. https://doi.org/10.1214/ss/1032280304 — SJR Q1 (secondary) — VERIFIED
  Checks: metadata=Crossref · link=resolves to Project Euclid · content=abstract · rank=aggregator
- [E] E. Walker, A. S. Nowacki, "Understanding Equivalence and Noninferiority Testing", J Gen Intern Med 26:192–196, 2011. https://doi.org/10.1007/s11606-010-1513-8 — JCR Q1 per Peeref, SJR Q2 per researcher.life; conflict — VERIFIED except rank conflict
  Checks: metadata=Crossref · link=resolves to Springer · content=full text (PMC3019319) · rank=aggregators
- [F] D. Lakens, "Equivalence Tests: A Practical Primer for t Tests, Correlations, and Meta-Analyses", Social Psychological and Personality Science 8:355–362, 2017. https://doi.org/10.1177/1948550617697177 — SJR Q1 (secondary) — VERIFIED
  Checks: metadata=Crossref · link=resolves to SAGE · content=full text (PMC5502906) · rank=aggregators
- [G] T. Baguley, "Standardized or Simple Effect Size: What Should Be Reported?", British Journal of Psychology 100:603–617, 2009. https://doi.org/10.1348/000712608X377117 — Q1 (secondary) — VERIFIED
  Checks: metadata=Crossref · link=resolves to Wiley · content=abstract · rank=aggregators
- [H] C. Laaber, J. Scheuner, P. Leitner, "Software Microbenchmarking in the Cloud. How Bad Is It Really?", Empirical Software Engineering 24:2469–2508, 2019. https://doi.org/10.1007/s10664-019-09681-1 — Scopus Q1, one JCR listing Q2 (secondary) — VERIFIED at abstract level
  Checks: metadata=Crossref · link=DOI resolves to Springer · content=abstract via Chalmers record · rank=aggregators

## 2. Geometric mean with paired t on log ratios (same 99%/98% rule)
What it is: y_i = log(A_i/B_i). CI = exp(ȳ ± t·s/√10).
Why it fits: three lines of code, exactly matches §2.2.4's log sentence and is standard for equivalence. Verdicts nearly always match method 1. A strong choice if the user values familiarity over one consistent estimand.
Trade-offs / risks: the centre of the interval differs slightly from the plots' ratio of means. It also sits awkwardly with [35] Rule 4 (geometric mean only when rates are unavailable), and with the stated ratio-of-means decision, so you would need a sentence explaining it.
What to take from each source:
- [A] Log analyses should often be preferred for positive data, and do not let the data decide whether to transform (abstract).
References:
- [A] K. N. Keene, "The Log Transformation Is Special", Statistics in Medicine 14:811–819, 1995. https://doi.org/10.1002/sim.4780140810 — SJR Q1 (secondary) — VERIFIED at abstract level
  Checks: metadata=Crossref · link=resolves to Wiley · content=abstract · rank=aggregators

## 3. Method 1 with the 90% rule, for a joint claim only
What it is: 99% for "faster", and a 90% interval for all N together. Only one equivalence statement exists: "within margin at all five N".
Why it fits: it is closest to the idea file, and a proven margin is about 26% smaller than at 98%. If the thesis's question is truly global, this is valid and more powerful.
Trade-offs / risks: you must not read individual N rows as equivalence claims. A single failing N blocks the whole claim, and the table becomes harder to read.
What to take from each source:
- [A] Test each parameter individually and accept only if all pass, with the prespecified risk attained exactly (abstract).
References:
- [A] R. L. Berger, "Multiparameter Hypothesis Testing and Acceptance Sampling", Technometrics 24:295–300, 1982. https://doi.org/10.1080/00401706.1982.10487790 — Q1 by most secondary sources, one lists Q2 — VERIFIED except rank conflict
  Checks: metadata=Crossref · link=resolves to T&F · content=abstract via OpenAlex · rank=aggregators

## 4. Sign-flip permutation on rerun contrasts
What it is: apply all 1024 sign flips of A_i − θB_i (or of log ratios), and invert for the interval and TOST.
Why it fits: no normality assumption if contrasts are symmetric, and the smallest two-sided p is 2/1024 = 0.002, so 99% is reachable. A useful robustness check when a rerun looks odd.
Trade-offs / risks: intervals are discrete and coarse at n = 10. Exchangeability comes from an assumption, because the runner randomizes order, not labels. More code and more to explain.
What to take from each source:
- [A] When permutation methods are exact and distribution-free (abstract).
References:
- [A] M. D. Ernst, "Permutation Methods: A Basis for Exact Inference", Statistical Science 19:676–685, 2004. https://doi.org/10.1214/088342304000000396 — SJR Q1 (secondary) — VERIFIED at abstract level
  Checks: metadata=Crossref · link=DOI listed by Crossref, redirect not separately opened · content=abstract · rank=aggregators

## 5. Percentile bootstrap at the verdict levels
What it is: reuse the plots' bootstrap at 99% and 98%.
Why it fits: one method for everything.
Trade-offs / risks: real coverage is about 96% and 94% (my simulation), so verdicts would be overconfident. Use it only at 95% as a sensitivity column, as in Step 5's outputs.
What to take from each source:
- [A] Percentile intervals tend to be too narrow and under-cover in small samples. This also must be disclosed for the plots.
References:
- [A] T. C. Hesterberg, "What Teachers Should Know About the Bootstrap", The American Statistician 69(4):371–386, 2015. https://doi.org/10.1080/00031305.2015.1089789 — JCR Q1 per secondary page; older listing Q2 — VERIFIED except rank uncertainty
  Checks: metadata=Crossref (earlier report) · link=T&F (earlier report) · content=full text re-read this session · rank=secondary

---

# 3. Thesis alignment

## What the existing text and bibliography already support
| Point | Source | Limit |
|---|---|---|
| Ratio with interval as the effect size | [38] "Effect size" paragraph | ISMM is ICORE2026 C (checked); not sole support |
| Ratio of means with Fieller | [38] §10.1 | No covariance term, not paired |
| Summarise rates, not ratios | [35] Rule 4, §3.1.1 | That section concerns deterministic data; [35] §3.1.2 repeats it per earlier report |
| Normality needs checking | [35] Rule 6 | |
| Overlap is not a test | [38] "Visual tests" | [35] Rule 7 and [28] §3.3 use overlap, so do not cite those for it |
| Paired analysis | **None** | [28] §3.3 uses an unpaired difference, and [38] §10.1 has no covariance |
| Equivalence needs a margin | §2.2.4 sentence, uncited | No entry covers TOST or margins |
| Multiplicity | **None** | |
| Practical significance | §2.2.4 p.11, uncited | |
| Order, drift, warm-up, validation | §2.2.1, §2.2.3, §2.3.4 with [44], [37], [24], [53] | |
| Bias from setup | [44] (ASPLOS, A*) | Abstract and search listing only; I did not read magnitudes |
| Benchmark validity for a merge benchmark | §2.3.2–§2.3.4, [26], [45], §3.8.5 p.38 | |
| Cohen's d | [25] | Book not opened, so whether it names d_z is unverified |

## Bibliography additions
Required if the procedure is used:
- **Berger & Hsu 1996** (full citation in method 1). No entry covers TOST or equivalence.
- **Walker & Nowacki 2011**. No entry covers prespecifying a margin.
- **Lakens 2017**. No entry covers the combined difference + equivalence outcomes or bound-setting.
- **Fieller 1954**. [38] has no covariance, and the covariance is what makes it paired.
- **Dunn 1961**. No entry covers multiplicity.
- **Hesterberg 2015**. Already pending, and now needed to disclose the percentile limit.

Recommended:
- **Baguley 2009**, if d is dropped.
- **Freeman 1989**: "The Performance of the Two-Stage Analysis of Two-Treatment, Two-Period Crossover Trials", Statistics in Medicine 8:1421–1432. https://doi.org/10.1002/sim.4780081202 — Q1 secondary, abstract checked. Use it if you mention order or carryover.
- **Laaber 2019**, for the same-instance randomized design.

Only if used:
- **Berger 1982** (method 3)
- **Keene 1995** (method 2)
- **Ernst 2004** (method 4)
- **Zerbe 1978**
- **Dunlap et al. 1996**: "Meta-Analysis of Experiments with Matched Groups or Repeated Measures Designs", Psychological Methods 1:170–177. https://doi.org/10.1037/1082-989X.1.2.170 — Q1, SCImago category page via search snippet, abstract checked. Use it if you discuss paired d.
- **Auger, Jugé, Nicaud, Pivoteau 2018**: "On the Worst-Case Complexity of TimSort", ESA 2018, LIPIcs 112, 4:1–4:13. https://doi.org/10.4230/LIPIcs.ESA.2018.4 — CORE ICORE2026 A (checked). Abstract: O(n + n log ρ) is proved for Python's version, with Java analysed only in worst case, so do not stretch it to Java's run bound. Use it only if §3.10.1 changes after checking the code.
- **Perneger 1998**: "What's Wrong with Bonferroni Adjustments", BMJ 316:1236–1238. https://doi.org/10.1136/bmj.316.7139.1236 — Q1 secondary, full text read. Use it if you defend Bonferroni against the common objection.

## Chapter 2 wording changes
**Must change** (the four "process level" and replicate edits in `notes_for_thesis.md` §2 still stand, and are not repeated here).

§2.2.4 p.11. Current: "Likewise, an interval that includes no effect does not establish equivalence, which would require an explicitly stated equivalence margin."
Replacement: "Likewise, an interval that includes no effect does not establish equivalence [Walker]. Equivalence requires a margin fixed before the data are seen [Walker], and it is shown by two one-sided tests that reject differences at both ends of the margin [Berger & Hsu, Lakens 2017]."

§2.2.4 p.11. Current: "With the low variance typical of benchmarks, even small differences yield very large values of d, so it complements the ratio rather than replacing it."
Replacement: "With the low variance typical of benchmarks, even small differences yield very large values of d, so this thesis reports the ratio and its confidence interval instead [38, Baguley]."

`notes_for_thesis.md` §3 (not yet in the thesis). Current: "Ten reruns cannot establish normality [35], so intervals are computed by resampling whole reruns with the expanded percentile bootstrap, which corrects the narrowness of the percentile interval in small samples [Hesterberg 2015]."
Replacement: "Plot intervals are plain percentile bootstrap intervals over whole reruns, shown beside the t interval. Percentile intervals tend to be too narrow in small samples [Hesterberg 2015], so they are descriptive, and comparisons do not rest on them."

**Add to the methods chapter** (no Chapter 4 exists in this PDF): "Merge order is compared by the ratio of mean throughputs, 2A/2B, per thread count. Intervals use Fieller's construction [Fieller], applied to paired reruns. To hold the family-wise error at 5% across five thread counts, the difference test uses 99% intervals and the equivalence test uses a 1% significance level in each one-sided test [Dunn]. The margin of ±m was fixed before the final runs, from [the decision anchor and the A/A pilot]."

**Nice to change**

§2.2.4 p.11. Current: "The direction of a ratio must be defined once and used consistently, and ratios are usually analyzed on a logarithmic scale."
Replacement: "The direction of a ratio must be defined once and used consistently. A ratio of means and a mean of logarithmic ratios nearly coincide when variation is low, and this thesis uses the former, in line with [35]."

§2.2.4 p.11. Current: "Cohen's d is a standardized effect size that divides the difference of two means by their pooled standard deviation [25]."
Add: "A paired variant divides by the standard deviation of the differences, which is a different quantity." Only if you keep the d paragraph.

§2.2.1 p.10. The sentence ending "…or the limitation must be stated [44]." Add: "Here, the order of strategies is shuffled within each rerun and thread count."

Outside Chapter 2:
- §3.8.5 p.38, "If the difference between the two merges is below resolution": consider "If the comparison is inconclusive".
- §3.9.3 p.40: "(??)" needs its reference.
- §3.10.1 p.40: the O(M log M) statement depends on the sort check.
- §§3.9–3.10: several paragraphs render as run-together math text running off the page in this PDF (for example "S2Amaybesmall"). This is a LaTeX macro problem, separate from the statistics.

## Forced deviations
**Must change**
- Replicates are reruns, not processes (already noted).
- Idea-file step 5, "99% + 90% with per-N verdicts": use 99% + 98%, or a joint claim only.
- Idea-file Cohen's d: remove it.
- Idea-file "fresh EC2 instance" and "Update §2.2.4 for fresh instances" are void. Set `RERUNS=10` and remove "different day" in `runner.conf`.
- Disclose the plots' percentile narrowness.

**Nice to change**
- Log-scale sentence.
- Force 5/5 order balance if the final runs have not started.
- Add A/A pilot.
- Merge-only benchmark.

## Decisions only the user can make
1. **The margin.** Specifically, the largest extra compute you would ignore (suggested cap 5%) and whether to run the A/A pilot.
2. **Primary benchmark.** Confirm what mb1 and mb2 time. Default: mb2 primary, family of 5. If unclear, use the 10-comparison row.
3. **99% + 98% per N (recommended), or 99% + 90% with one joint claim.**
4. **Fieller (recommended) or log-t** as the primary.
5. **Balance order and build the merge-only benchmark.** Both are optional and need new code. The JMH work is new work.

## Could not verify
- **SCImago direct access was blocked**, so journal ranks are from aggregators, except for a Psychological Methods snippet from SCImago's own page. Conflicts are marked. Confirm on scimagojr.com before relying on any rank.
- **Abstract-level only:** every "abstract" reference. Berger & Hsu's full text would be worth reading before you quote its details. Do not cite section numbers from it. The Walker and Lakens passages came through a page-extraction tool, so check wording before quoting.
- **OOPSLA rank** relies on the earlier report (A); this session's search did not return that row. SC is A, ASPLOS A*, ISMM C, ESA A (all checked).
- **Not seen:** mb1/mb2 source, `collect.py`, `ledger_raw` contents, real ledger data, [25], [39], [58].
- **JMH console log format** for warm-up lines was not confirmed against a real log file.

RECOMMENDATION: Use the ratio of means with a paired Fieller interval, 99% for "faster" and 98% for "equivalent", against a margin fixed from the compute-cost cap and an A/A pilot, with Cohen's d dropped and log-t plus the 95% percentile bootstrap kept as non-deciding sensitivity columns.

REJECTED:
- Expanded percentile bootstrap — ruled out by the user's plot decision; no longer proposed anywhere.
- Schuirmann 1987, J. Pharmacokinetics and Biopharmaceutics (https://doi.org/10.1007/BF01068419) — metadata and abstract confirmed, and it is the classic TOST paper, but its journal's successor is listed Q2 or Q3 by secondary sources, so it fails the bar. Berger & Hsu covers TOST instead. A supervisor may accept it.
- Locke 1984, same journal (https://doi.org/10.1007/BF01059558) — exact ratio CI for crossover data on untransformed values, the closest fit to paired Fieller, but it fails the same venue rule.
- Lauzon & Caffo 2009, The American Statistician (https://doi.org/10.1198/tast.2009.0029) — its (k−1) rule is for all pairwise comparisons of k independent groups, which is not this design, so it does not fit.
- Lakens, Scheel & Isager 2018, AMPPS (https://doi.org/10.1177/2515245918770963) — exists, but full text was blocked, so its margin-setting approaches could not be checked; not used.
- Lakens 2022, Collabra (https://doi.org/10.1525/collabra.33267) — abstract only, rank not established, not needed.
- Lakens 2013, Frontiers in Psychology (https://doi.org/10.3389/fpsyg.2013.00863) — content confirms the d_z distinctions, but rank was not established and the venue is mass-publishing, so it fails the bar as a cited source.
- Morris & DeShon 2002, Psychological Methods (https://doi.org/10.1037/1082-989X.7.1.105) — meta-analysis focus, not needed.
- Piaggio et al. 2012, JAMA (https://doi.org/10.1001/jama.2012.87802) — abstract did not show the margin claim, and rank was not looked up; not used.
- Bland & Altman 1995 BMJ on Bonferroni (https://doi.org/10.1136/bmj.310.6973.170) — publisher page blocked and no full text read; not used.
- Altman & Bland 1995 BMJ (https://doi.org/10.1136/bmj.311.7003.485) — not read; Walker covers the point.
- Hills & Armitage 1979, Br J Clin Pharmacol (https://doi.org/10.1111/j.1365-2125.1979.tb05903.x) — no abstract and full text not read; not used.
- Arcuri & Briand 2011, ICSE (https://doi.org/10.1145/1985793.1985795) — only part of the abstract was seen, and its Bonferroni position could not be confirmed; not used.
- Hirschberg & Lye 2010, The American Statistician (https://doi.org/10.1198/tast.2010.08130) — delta vs Fieller geometry, not needed.
- [35] Rule 7 and [28] §3.3 as support for comparing 2A and 2B — they use interval overlap or an unpaired difference, which does not match paired reruns.
- research_questions.txt as authority — stale ideas, tested as a proposal only.
- DOIs 10.1080/00031305.1978.10479266 and 10.2307/1267823's duplicate path — the first resolved to a different paper (Eaton), so I searched for and used the correct Zerbe DOI; Berger 1982 is cited through its T&F DOI.
