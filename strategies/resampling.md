# Resampling

The research agent's full report on how to calculate resampled confidence intervals, from 2026-11-01, unedited below the line.

Decisions made since the report:

| Topic | Decision |
|---|---|
| Speedup across reruns | Ratio of means. Plots draw the 95% paired Fieller interval (`fieller_low`, `fieller_high`); the table keeps the whole-rerun resampling for checks. |
| CI method for across-reruns plots | **95% t interval of reruns (owner decision 2026-11-02, `plot-t-intervals`).** Plots draw `t_low`/`t_high` (throughput), `gc_alloc_rate_norm_t_low`/`_t_high` (GC) and `fieller_low`/`fieller_high` (speedup). The plain percentile bootstrap (2.5% / 97.5%) stays in the tables (`ci_low`, `ci_high`, `gc_alloc_rate_norm_ci_low`/`_ci_high`) for checks and is not drawn. The expanded percentile recommended below was not adopted: see the fact-check. |
| Resamples | 10,000, Hesterberg's routine value |
| Single-rerun throughput plots | Error bars are that rerun's ledger bounds, with no resampling. Ledger bounds are never resampled or averaged. |
| Mean-of-reruns throughput plots | Bars are the mean of the rerun scores. Error bars are the t interval of those scores; the resampled CI of the same scores is in the CSV for checks. |
| Speedup single-rerun plots | No error bars |
| Groups with no spread or fewer than two reruns | No CI, with a warning. Their label says `none`, and a title names error bars only if some are drawn. |
| `gc_alloc_rate_norm` plot (built, `add-gc-alloc-plot`) | Bars of the mean of reruns, linear axis from zero, no log axis. Error bars are the 95% t interval (`gc_alloc_rate_norm_t_low`, `_t_high`). The plain percentile bootstrap, 10,000 resamples, stays in two other columns of the GC `_avg` CSV, for checks. Rerun values are taken in rerun order, so ledger row order cannot change the CI. |
| Other GC metrics | Every averaged metric has `<metric>_t_low` and `<metric>_t_high` in the `_avg` CSV (blank without spread or with a blank contributor). Only the `gc_alloc_rate_norm` plot and the `batch_fill` plot of `mb2_rawFinalize` draw them. `pauses_max` stays dots plus a median line, and `pauses_p99` stays in the table, not plotted. |
| 2A vs 2B (built, `add-2a-2b-comparison`) | Paired log-t on per-rerun log(2A/2B), decided by the owner over Fieller. mb1 and mb2 are analysed separately. One verdict per thread count, at 99% and 98%. Cohen's d is dropped: see [notes_for_thesis.md](notes_for_thesis.md). The margin is 5%, set in `config.yaml`. |

**Where this table and the report below differ, this table wins.** The report is kept as written, so it still recommends the expanded percentile, 50,000 resamples and rerun dots, and its thesis wording mentions the expanded percentile. None of those were adopted.

Thesis changes that follow from this report are collected in [notes_for_thesis.md](notes_for_thesis.md). The simulation scripts it names live in a temporary session folder and may no longer exist.

## Fact-check: how accepted is each method? (2026-11-01)

This was checked after the report below, and it weakens the report's support for the expanded percentile.

| Method | Peer reviewed? | Widely used? | Evidence |
|---|---|---|---|
| Expanded percentile | Named in one journal article, Hesterberg 2015. The formula was found only in the unreviewed arXiv version (1411.5279, §5.3); Hesterberg 1999 and the `resample` R package docs also appear to cover it, seen only as search snippets. | **Could not be confirmed.** No independent peer-reviewed evaluation or use was found, and it appears to live mostly in Hesterberg's own work and software. This is an absence of evidence, not a disproof. | PMC4784504 §§4.1, 6; arXiv 1411.5279 §5.3 |
| Percentile and BCa | Yes: Efron 1987 (JASA), DiCiccio & Efron 1996 (Statistical Science) | Yes, treated as the standard bootstrap intervals, though no usage survey was found | Crossref records; content not opened |
| Bootstrap CIs in microbenchmarking | Yes: Laaber, Scheuner & Leitner, Empirical Software Engineering 24(4):2469–2508, 2019, doi:10.1007/s10664-019-09681-1 | One confirmed example, not a field survey | Full text: 95% level, hierarchical resampling over instance, trial and iteration, 100 resamples per CI. The interval variant is not named. |
| Percentile bootstrap for benchmarks | Kalibera & Jones, University of Kent TR 4-12, 2012 (arXiv:2007.10899), §7.2.1. A technical report: only a portion appeared at a workshop (EPEW 2012). | Cited by Laaber et al. | Full text: percentile method with hierarchical resampling |

Do not describe the expanded percentile as "widely used and cited".

---

## 1. Expanded percentile bootstrap (recommended)
What it is: Resample the 10 rerun values with replacement, as a normal percentile bootstrap does, but read wider quantiles. The tail is α′/2 = Φ(−√(n/(n−1))·t(α/2, n−1)). For n = 10 at 95% that is 0.855% and 99.145% instead of 2.5% and 97.5%. This corrects the known narrowness of the bootstrap at small n.
Why it fits: In my simulation, coverage at n = 10 was close to the t interval, and it has no resampled SE that can go to zero. Every endpoint comes from real resampled means, so `batch_fill` cannot get a bound above 1. The same recipe works for every metric, including a paired ratio.
Trade-offs / risks:
- It undercovers skewed data on the long-tail side (about 91–92% in my runs).
- At n = 10 it is close to a t interval by construction, so it adds bounds and some asymmetry, not much extra accuracy.
- Peer-reviewed support for this specific variant is thin (see [A]).
What to take from each source:
- [A] §§3.2, 4.1: the bootstrap is too narrow by √((n−1)/n), and the percentile interval also uses z instead of t. §4.1 and §6 name the expanded percentile as the correction, "with improved small-sample coverage". The formula and coverage plots are only in the unreviewed long version (arXiv 1411.5279 §§5.3, 5.6), so cite the journal article and treat the formula's source as a preprint. §2.4: exhaustive resampling is possible for small n. §3.6: Monte Carlo error.
- [B] Abstract: coverage of bootstrap intervals is "characteristically less than the specified nominal level", and any method "can be unreliable for small samples". Authors should state the method and the number of resamples.
References:
- [A] Tim C. Hesterberg, "What Teachers Should Know About the Bootstrap: Resampling in the Undergraduate Statistics Curriculum", The American Statistician 69(4):371–386, 2015. https://doi.org/10.1080/00031305.2015.1089789 — JCR 2026 Q1 — VERIFIED (rank from secondary source)
  Checks: metadata=Crossref · link=DOI resolves to T&F · content=full text (PMC copy, via Europe PMC) · rank=Peeref page; SCImago returned 403
- [B] Marie-Therese Puth, Markus Neuhäuser, Graeme D. Ruxton, "On the variety of methods for calculating confidence intervals by bootstrapping", Journal of Animal Ecology 84(4):892–897, 2015. https://doi.org/10.1111/1365-2656.12382 — JCR 2026 Q1 — VERIFIED (rank from secondary source)
  Checks: metadata=Crossref · link=DOI resolves to Wiley · content=abstract · rank=Peeref page

## 2. Bootstrap-t (studentized)
What it is: Resample, compute t* = (mean* − mean)/SE* for each resample, and use the quantiles of t* in place of the t table.
Why it fits: It has the best coverage on skewed data. In my runs it reached 94% on exponential data, against 91% for expanded percentile. It works as a sensitivity check on throughput and speedup.
Trade-offs / risks:
- The instability you suspected is real, and tied values make it fail outright.
- With nine values at 1.0 and one at 0.98, 34.9% of resamples (0.9^10) have SE* = 0, so the interval becomes infinite.
- With one outlier among tight values, it gave an upper bound of 1041 when the largest data value was 1020.
- Do not use it for `batch_fill`, or for any cell with heavy ties.
What to take from each source:
- [A] §§4.3–4.4: "does best, by a substantial margin" on exponential data, but within 10% of nominal only for n ⩾ 101. On normal data it "does extremely well, even for very small samples". These figures report coverage, and the article does not discuss instability.
- [B] Abstract: percentile-t and accelerated bias-correction are the most promising methods in theory. This is asymptotic and says nothing specific about n = 10.
- [C] Abstract only: survey covering BCa and bootstrap-t. I could not open the full text.
- [D] Abstract: variance stabilization can improve the bootstrap-t. It does not fix the zero-SE case for tied data.
- [E] Abstract: small B can lengthen percentile-t intervals, and the nominal level should be a multiple of 1/(B+1).
References:
- [A] Hesterberg 2015, as in method 1 — VERIFIED
- [B] Peter Hall, "Theoretical Comparison of Bootstrap Confidence Intervals", Annals of Statistics 16(3):927–953, 1988. https://doi.org/10.1214/aos/1176350933 — SJR Q1 — VERIFIED (rank from secondary source)
  Checks: metadata=Crossref · link=DOI resolves to Project Euclid · content=abstract · rank=journalsinsights SJR listing
- [C] Thomas J. DiCiccio, Bradley Efron, "Bootstrap Confidence Intervals", Statistical Science 11(3):189–228, 1996. https://doi.org/10.1214/ss/1032280214 — SJR Q1 — VERIFIED for abstract-level claims only (rank from secondary source)
  Checks: metadata=Crossref · link=search result on Project Euclid · content=abstract · rank=researcher.life page
- [D] Robert Tibshirani, "Variance Stabilization and the Bootstrap", Biometrika 75(3):433–444, 1988. https://doi.org/10.1093/biomet/75.3.433 — SJR Q1 — VERIFIED (rank from secondary source)
  Checks: metadata=Crossref · link=DOI registered in Crossref, redirect not separately opened · content=abstract via OpenAlex · rank=journalsinsights SJR listing
- [E] Peter Hall, "On the Number of Bootstrap Simulations Required to Construct a Confidence Interval", Annals of Statistics 14(4):1453–1462, 1986. https://doi.org/10.1214/aos/1176350169 — SJR Q1 — VERIFIED (rank from secondary source)
  Checks: metadata=Crossref · link=DOI resolves to Project Euclid · content=abstract · rank=journalsinsights SJR listing

## 3. Plain Student-t interval (baseline, not resampling)
What it is: mean ± t(n−1)·s/√n on the rerun values.
Why it fits: At n = 10 it is hard to beat when skew is mild. It is the recommended sensitivity column, and for 2A vs 2B at 99% I would keep it as the primary interval (see question 6).
Trade-offs / risks: It can exceed physical bounds (upper bound 1.0025 on `batch_fill` in the tie example). It assumes normality that 10 reruns cannot confirm, and it does not meet your decision to use a resampled CI.
What to take from each source:
- [A] Abstract and §6: percentile intervals are less accurate than t intervals in small samples.
- [B] §3.2.2: the t interval for n < 30. This is already in your bibliography as [28].
- [C] Rule 6 (§3.1.2): "Do not assume normality of collected data (e.g., based on the number of samples) without diagnostic checking." This is the limit on [B], and it is already [35].
- [D] Abstract: how skewness drives t coverage.
References:
- [A] Hesterberg 2015 — VERIFIED
- [B] Andy Georges, Dries Buytaert, Lieven Eeckhout, "Statistically Rigorous Java Performance Evaluation", OOPSLA 2007, 57–76. https://doi.org/10.1145/1297027.1297033 — ICORE2026 A — VERIFIED
  Checks: metadata=Crossref · link=DOI resolves to ACM · content=full text (dri.es copy) · rank=CORE portal
- [C] Torsten Hoefler, Roberto Belli, "Scientific Benchmarking of Parallel Computing Systems", SC 2015. https://doi.org/10.1145/2807591.2807644 — ICORE2026 A — VERIFIED
  Checks: metadata=Crossref · link=DOI resolves to ACM · content=full text (ETH copy) · rank=CORE portal
- [D] Dennis D. Boos, Jacqueline M. Hughes-Oliver, "How Large Does n Have to be for Z and t Intervals?", The American Statistician 54(2):121–128, 2000. https://doi.org/10.1080/00031305.2000.10474524 — JCR 2026 Q1 — VERIFIED (rank from secondary source)
  Checks: metadata=Crossref · link=DOI registered in Crossref, redirect not separately opened · content=abstract via OpenAlex · rank=Peeref page

## 4. BCa
What it is: Percentile bootstrap with adjusted quantiles for bias and skew.
Why it fits poorly: It is second-order accurate in large samples and is the `scipy.stats.bootstrap` default. At n = 10 it covered only about 90% on normal data, the same as the plain percentile interval, because it has no small-sample width correction.
Trade-offs / risks: It undercovers at this n, and SciPy can return NaN when the bootstrap distribution is degenerate.
What to take from each source:
- [A] Abstract: second-order correctness.
- [B] Abstract: recommended generally, but with the small-sample warning quoted in method 1. Its general recommendation does not outweigh that warning here.
- [C] Abstract: BCa as one of the two most promising methods in asymptotic theory, which says nothing specific about n = 10.
References:
- [A] Bradley Efron, "Better Bootstrap Confidence Intervals", JASA 82(397):171–185, 1987. https://doi.org/10.1080/01621459.1987.10478410 — SJR Q1 — VERIFIED (rank from secondary source)
  Checks: metadata=Crossref · link=DOI resolves to T&F · content=abstract via OpenAlex · rank=journalsinsights SJR listing
- [B] Puth et al. 2015 — VERIFIED
- [C] Hall 1988 — VERIFIED

## 5. Plain percentile
What it is: The 2.5% and 97.5% quantiles of the resampled means.
Why it fits poorly: It is the simplest option, but Hesterberg advises against it at this n. It covered about 90% on normal data, equivalent to using z and a divisor of n.
Trade-offs / risks: Too narrow by about 18% against t at n = 10 (1.96·√0.9 ≈ 1.86 versus 2.26).
What to take from each source:
- [A] §4.1: "poor in small samples—they tend to be too narrow".
References:
- [A] Hesterberg 2015 — VERIFIED

## Own simulation (not peer reviewed; not citable as evidence)
Setup: n = 10, 95% level, exhaustive bootstrap over all 92,378 resamples, 2,000 datasets per row, so about ±1 percentage point. Median width is relative to the t row.

| Population | t | percentile | BCa | expanded | bootstrap-t | bootstrap-t notes |
|---|---|---|---|---|---|---|
| Normal | .955 | .905 | .905 | .950 | .951 | median width +7% |
| Lognormal σ = .1 (skew .3) | .949 | .901 | .901 | .949 | .952 | |
| Lognormal σ = .5 | .919 | .877 | .882 | .921 | .936 | p99 width 3.15 vs 1.69 for t |
| Exponential | .903 | .863 | .870 | .909 | .939 | p99 width 7.4 vs 3.0 for t |
| 10% outlier mix | .720 | .674 | .668 | .713 | .736 | median width 7.4 vs 4.6 for t |
| Bounded fill (1 − exponential) | .894 | .849 | .870 | .904 | .935 | |
| Tied fill (80% at 1.0) | .893 | .893 | .897 | .898 | .899 | infinite in 78% of datasets |
| Poisson(30) | .948 | .911 | .908 | .948 | .950 | |
| Poisson(3) | .944 | .912 | .912 | .958 | .955 | |

Scripts: /tmp/claude-1000/-home-lesheid-workspaces-thesis-visualization/3b802712-e8dd-4361-89fc-923857fb35fa/scratchpad/sim.py and /tmp/claude-1000/-home-lesheid-workspaces-thesis-visualization/3b802712-e8dd-4361-89fc-923857fb35fa/scratchpad/ex.py

## Answers to your questions

**Q1. Hesterberg claim.** Partly correct.
- The article exists and §3.5 is right. §§5.5–5.6 is the arXiv numbering; in the journal these are §§4.3–4.4.
- The bootstrap-t coverage claim holds, but the journal's comparison leaves out BCa and says nothing about instability.
- §3.5 also warns: "Bootstrapping does not overcome the weakness of small samples as a basis for inference."
- **Your concern is real.** I found no Q1 source that states it directly. The direct statements are in Polansky 2000 and Owen 2025, and both fail the bar (see REJECTED).
- There is no clean fix for ties. Dropping zero-SE resamples changes the distribution, and Owen's positive-weight variant is an unreviewed preprint. So avoid bootstrap-t on tied cells.

**Q2. One method?** Yes, with the exceptions below.
- **Throughput and speedup:** expanded percentile. Compute the speedup statistic on resampled reruns.
- **`gc_alloc_rate_norm`, `gc_time`:** expanded percentile.
- **`gc_time_pct`:** this is `gc_time` divided by a fixed 250,000 ms, so its interval is the `gc_time` interval rescaled. It is not a binomial proportion, so Wilson-style intervals do not apply.
- **`gc_count`:** expanded percentile worked even at Poisson(3) in simulation. No Poisson model is needed.
- **Exception 1, any metric:** if all 10 values are identical, show no CI.
- **Exception 2, `batch_fill`:** (later note, 2026-11-03: the original advice no longer matches the plot.) The `batch_fill` plot of `mb2_rawFinalize` draws bars of the mean of reruns with the 95% t interval (`batch_fill_t_low`, `batch_fill_t_high`); a group with fewer than two reruns or no spread has a bar, no error bar and a warning. The "k of 10 reruns" display is not drawn.
- **Exception 3, `pauses_max`:** stays dots plus median, as already decided.
- **Skew and outliers:** always show the rerun dots. Do not remove outliers unless the fork data shows a specific fault. If the t and expanded endpoints differ by more than about 25% of the half-width, flag the cell. That threshold is my own heuristic.

**Q3. Degenerate data.**
- Treat a cell as identical if max − min is at or below about 1e-9 × |mean|. That tolerance is my choice, intended to catch floating-point and CSV rounding noise.
- Print "no spread observed in 10 reruns" and draw no error bar. A zero-width bar would falsely imply perfect precision.
- Near-constant but distinct values go through the normal method.
- Zero-spread resamples are harmless for the expanded percentile, which uses no SE.
- Ten values of 1.0 only bound the rate of non-full reruns at about 26% (one-sided 95%, 1 − 0.05^(1/10), my calculation).

**Q4. Settings.**
- Use a 95% level.
- 10,000 is Hesterberg's routine value (§3.6), and he derives r ≥ 15,000 for 10% accuracy at a 2.5% tail. The same formula at the 0.855% tail gives about 44,500 (my calculation), so use **B = 50,000**.
- Measured Monte Carlo SD of the lower endpoint, in SE units on one normal test sample: 0.037 at B = 10,000, 0.028 at 20,000 and 0.010 at 100,000.
- Report the seed, generator, B, quantile rule and the largest endpoint Monte Carlo SE. Estimate that SE by resampling the replicates (§3.6) or from √(p(1−p)/B) divided by the density.
- Derive each cell's seed from the base seed plus benchmark, strategy, N and metric, so adding a strategy changes nothing else.
- Alternative: enumerate all 92,378 distinct resamples with multinomial weights. This removes both the seed and the Monte Carlo error (§2.4). I ran this in the simulation.

**Q5. Fit with the benchmarking literature.**
- Using the top repeated level as the replicate matches [38]: its variance estimator and repetition count are at the highest level of the hierarchy (§§9–10). That literature does not name "complete sweep" as a level, so this is your design applying its rule.
- [38] §10.1 estimates Y′/Y, a ratio of means. Its Fieller formula uses separate half-widths h and h′, with no covariance term, so it does not exploit pairing. Whole-rerun resampling does.
- In my simulation at speedup 4 with ρ = 0–0.7, mean of ratios exceeded ratio of means by 0.003–0.009% at CV 1%, 0.02–0.08% at CV 3%, and 0.3–0.9% at CV 10%. That is well below the estimator's own spread at CV 1–3%.
- The fixed instance is acceptable if stated. [35] §4.1.1 supports fixing the environment, and §4.1.2 reports experiments repeated in the same allocation.
- The interval then means: "variation between complete sweeps on this instance in this period". It does not cover other instances or hidden setup bias ([44]). If reruns drift, it can also be too narrow, so check execution-order plots.

**Q6. Comparisons.**
- Use a CI on the paired rerun-level log ratio, as §2.2.4 already says. Overlap is not a test: [E] below, and the "Visual tests" paragraph of [38].
- Keep the planned Bonferroni across the 5 thread counts for 2A vs 2B.
- At 99%, the expanded tail is about 0.03%, which relies on extreme resamples and needs about 1.25 million draws. So keep the planned paired-t on log ratios as primary, and use the bootstrap as a check.
- Label the figure's error bars as pointwise and unadjusted. If you report only strategies picked after seeing the data, selection breaks coverage ([F], abstract).
- [E] Nathaniel Schenker, Jane F. Gentleman, "On Judging the Significance of Differences by Examining the Overlap Between Confidence Intervals", The American Statistician 55(3):182–186, 2001. https://doi.org/10.1198/000313001317097960 — JCR 2026 Q1 — VERIFIED (rank from secondary source)
  Checks: metadata=Crossref · link=DOI resolves to T&F · content=abstract via OpenAlex · rank=Peeref page
- [F] Yoav Benjamini, Daniel Yekutieli, "False Discovery Rate–Adjusted Multiple Confidence Intervals for Selected Parameters", JASA 100(469):71–81, 2005. https://doi.org/10.1198/016214504000001907 — SJR Q1 — VERIFIED (rank from secondary source)
  Checks: metadata=Crossref · link=DOI resolves to T&F · content=abstract via OpenAlex · rank=journalsinsights SJR listing

**Limits of 10 reruns.**
- Skew cannot be diagnosed reliably.
- An event occurring in 10% of reruns is absent from 35% of samples. Every method then fell to about 67–74% coverage in the outlier row, and no method fixes that.
- Treat GC intervals as descriptive secondary results, in line with §2.2.2.

**SciPy.** The `scipy.stats.bootstrap` docs list only `percentile`, `basic` and `BCa`, with no studentized option. Expanded percentile should be reachable through `method='percentile'` with `confidence_level=0.98290`, but I did not run that call. It needs no new dependency: about 10 lines of numpy will do, using stdlib `statistics.NormalDist` and a stored t value (2.262157 for n = 10). Use the same indices for S and B if speedup becomes a ratio of means.

## Alignment with the thesis (Chapter 2 and bibliography)

**1. Already covered**
- Rerun-level replicates, by principle: §2.2.4 p.11 (nesting, pseudoreplication) with [38].
- Not using the JMH score error: §2.3.2 pp.12–13 with [45].
- CI as a statement of precision: §2.2.4 p.11 with [35], which supports CIs in general. [35] §7 says bootstrap is "beyond the scope", so it does not support the method.
- t interval as baseline: [28] §3.2.2.
- Normality caution: [35] Rule 6.
- Fixed instance: [35] §§4.1.1–4.1.2.
- Setup bias caveat: §2.2.1 p.10 with [44] (ASPLOS, ICORE2026 A*; abstract checked).
- External validity limits: §2.2.4 p.11 with [58]. I checked the thesis wording, not the book.
- Order and drift checks: §2.2.1 p.10 and §2.3.4 p.13.
- Overlap sentence: §2.2.4 p.11 is correct but uncited. Do not cite [35] Rule 7 or [28] for it, as both treat overlap checks as usable. Use [38] "Visual tests" or Schenker & Gentleman.
- `pauses_max` without CI: §2.2.2 p.10 with [35].
- GC as secondary metrics: §2.2.2 p.10.
- **No existing entry that I could check supports bootstrap intervals.** [38] contains no use of the word. I could not check [39]; its Chapter 4 "Statistical Measurements" is worth a look.

**2. Bibliography additions**
- Required: Tim C. Hesterberg. 2015. What Teachers Should Know About the Bootstrap: Resampling in the Undergraduate Statistics Curriculum. The American Statistician, 69, 4, 371–386. doi: 10.1080/00031305.2015.1089789. It is the only verified source here for the small-n narrowness and the expanded percentile. No existing entry covers either.
- Recommended: Schenker & Gentleman 2001 as a citation for the overlap sentence. [38] already covers the point, but ISMM is ranked low (see flags below).
- Only if you discuss them: Hall 1988 (bootstrap-t theory), Efron 1987 (BCa), Benjamini & Yekutieli 2005 (selection).

**3. Chapter 2 wording changes (proposed; nothing edited)**

Must change:
- §2.2.4 p.11. Current: "Operations run within iterations, and iterations run within runtime processes, with variation arising at each level [38]." Replace with: "Operations run within iterations, iterations within runtime processes, and processes within reruns, which are complete sweeps of all configurations, with variation arising at each level [38]."
- §2.2.4 p.11. Current: "These process-level summaries then serve as the replicates for the comparison." Replace with: "These process-level summaries are averaged per rerun, and the rerun values serve as the replicates for the comparison."
- §2.2.4 p.11. Current: "The interval should be constructed for the comparison itself, such as the ratio, and at the process level, so that the nested structure of the measurements is preserved." Replace "at the process level" with "at the rerun level".
- §2.3.2 p.12. Current: "Multiple forks provide the process-level replicates required by the measurement hierarchy [28, 38]. As all forks share the same machine, they are still not perfectly independent." Replace with: "Multiple forks provide process-level repetition within a rerun [28, 38]. As all forks of a rerun share the same machine and time window, complete reruns serve as the replicates for intervals. All reruns use one instance, so intervals do not cover variation between instances."
- Add after the sentence ending "…with wide intervals indicating an imprecise result [35]." (§2.2.4 p.11, or in your methods chapter): "Ten reruns cannot establish normality [35], so intervals are computed by resampling whole reruns with the expanded percentile bootstrap, which corrects the narrowness of the percentile interval in small samples [Hesterberg 2015]."

Nice to change:
- §2.2.1 p.9. Current: "In a managed runtime, this is usually a fresh runtime process." Add: "In this thesis, reruns on one fixed instance serve as repeated comparison blocks, not independent machine samples."
- §2.2.4 p.11. Current: "…ratios are usually analyzed on a logarithmic scale." If you keep the mean of ratios, change to "…comparisons between two strategies are usually analyzed on a logarithmic scale." With a ratio of means it can stay: percentile endpoints map exactly between the log and raw scales, apart from the choice of quantile rule.
- §2.2.4 p.11. Current: "Cohen's d … divides the difference of two means by their pooled standard deviation [25]." Your 2A/2B plan divides the mean log ratio by the SD of the log ratios, which is a different quantity. Reword this when Feature 4 is written.

**4. Forced deviations**

Must change:
- **Process-level replicates are not defensible as the interval's unit.** Pooling the 50 forks across reruns treats forks sharing a sweep as independent, which §2.2.4 itself calls pseudoreplication. Use reruns.
- **Mean of ratios conflicts with your own source.** [35] §3.1.1: "In general, ratios should never be averaged as such an average is meaningless." Rule 4 says to summarize the rates instead, and allows the geometric mean only if those are unavailable. They are available here. [35] §3.1.2 repeats the restriction for nondeterministic data, and [38] §10.1 uses a ratio of means. Rules 3 and 4 sit in the section on deterministic data, so this is not an absolute ban.
- **My recommendation is to change the speedup decision to a ratio of means.** Compute mean(S)/mean(B) and resample whole reruns with the same indices for S and B. Pairing is kept, the numbers barely move, and the per-rerun ratios can stay as dots. This is your decision to reverse. Mean of ratios is statistically harmless at low CV, so if you keep it, add a sentence stating the deviation from [35] and report the ratio of means as a check.
- **Disclose the arithmetic mean of throughput.** [35] Rule 3 rejects it for rates, but with fixed-duration iterations it equals total operations divided by total time, which is what [35] recommends. One sentence settles this, provided measured durations really are equal.
- **Disclose `gc_alloc_rate_norm` and `batch_fill`.** These are averages of ratios with varying denominators. The effect should be small at low CV, but it is the same issue.

Nice to change:
- Everything in the nice-to-change wording list above.

Inconsistency outside Chapter 2: `research_questions.txt` lines 67, 101 and 103 still require a fresh instance per rerun, which contradicts the fixed-instance design.

**Unverified or weak points**
- **SJR ranks:** SCImago blocked access (403 or challenge page). Journal ranks come from secondary pages, so confirm them on scimagojr.com before relying on them. An older listing gave The American Statistician JCR Q2.
- **[38] venue:** Kalibera & Jones (ISMM) is ranked ICORE2026 **C**, below the quality bar. I read the corrected author copy and metadata and link check out. Use it as an existing background citation, not as the sole support for a method.
- **Abstract-only references:** every paper marked "abstract" was checked only at abstract level, including DiCiccio & Efron.

RECOMMENDATION: Use the expanded percentile bootstrap on whole reruns for all CI metrics (95%, B = 50,000, fixed reported seed), no CI for identical cells, with t and bootstrap-t endpoints as a sensitivity check except on tied cells, and change speedup to a ratio of means to match [35] and [38].

REJECTED:
- Polansky, "Stabilizing bootstrap-t confidence intervals for small samples", Canadian Journal of Statistics 2000 (https://doi.org/10.2307/3315961) — fails venue rule: secondary sources list Q2 to Q4. Its abstract does directly say these intervals "can be unusually wide and unstable" on small data sets, so your supervisor may accept it as a supporting citation.
- Owen, "Better bootstrap t confidence intervals for the mean", arXiv 2508.10083 — not peer reviewed. Useful pointer on infinite-length intervals and zero weights.
- Hesterberg arXiv 1411.5279 — unreviewed long version. Cite the journal article; the expanded percentile formula appears only here.
- Fisher & Hall, "Bootstrap algorithms for small samples", JSPI 1991 — fails venue rule: secondary sources list Q2/Q3. Hesterberg §2.4 covers exhaustive resampling instead.
- Carpenter & Bithell, Statistics in Medicine 2000 — only the abstract was reachable, and its method recommendations could not be read.
- Andrews & Buchinsky, Econometrica 2000 — valid, but not needed, as the Hesterberg formula sets B here.
- Efron & Narasimhan, JCGS 2020 — full text not read, rank not established, not needed.
- Laaber et al., Empirical Software Engineering 2019 — full text and abstract not reachable.
- Davison & Hinkley 1997; Efron & Tibshirani 1993 — textbook content not opened, so not used.
- Fleming & Wallace, CACM 1986 — concerns summarizing across benchmarks, not reruns; does not fit.
- Brown, Cai & DasGupta, Statistical Science 2001 — binomial proportion intervals; `gc_time_pct` and `batch_fill` are not binomial, so it does not fit.
- Rousselet et al., AMPPS 2021 — percentile tutorial; rank not checked, not needed.
