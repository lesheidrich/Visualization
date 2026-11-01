# Notes for the thesis

Changes the thesis will need, to deal with later. Nothing here has been applied. The source for each is [resampling.md](resampling.md).

## 1. Bibliography

| Entry | Status | Why |
|---|---|---|
| Hesterberg 2015, "What Teachers Should Know About the Bootstrap", The American Statistician 69(4):371–386, doi:10.1080/00031305.2015.1089789 | Required for any bootstrap CI | The thesis does not mention the bootstrap yet |
| Schenker & Gentleman 2001, "On Judging the Significance of Differences by Examining the Overlap Between Confidence Intervals", The American Statistician 55(3):182–186, doi:10.1198/000313001317097960 | Recommended | Citation for the uncited overlap sentence in §2.2.4 p.11 |

## 2. Reruns as the replicates

Reruns stay on one machine. These edits only change which level counts as one sample: a complete rerun instead of a fork.

| Where | Current | Replacement |
|---|---|---|
| §2.2.4 p.11 | "Operations run within iterations, and iterations run within runtime processes, with variation arising at each level [38]." | "Operations run within iterations, iterations within runtime processes, and processes within reruns, which are complete sweeps of all configurations, with variation arising at each level [38]." |
| §2.2.4 p.11 | "These process-level summaries then serve as the replicates for the comparison." | "These process-level summaries are averaged per rerun, and the rerun values serve as the replicates for the comparison." |
| §2.2.4 p.11 | "The interval should be constructed for the comparison itself, such as the ratio, and at the process level, so that the nested structure of the measurements is preserved." | Replace "at the process level" with "at the rerun level". |
| §2.3.2 p.12 | "Multiple forks provide the process-level replicates required by the measurement hierarchy [28, 38]. As all forks share the same machine, they are still not perfectly independent." | "Multiple forks provide process-level repetition within a rerun [28, 38]. As all forks of a rerun share the same machine and time window, complete reruns serve as the replicates for intervals. All reruns use one instance, so intervals do not cover variation between instances." |
| §2.2.1 p.9 (optional) | "In a managed runtime, this is usually a fresh runtime process." | Add: "In this thesis, reruns on one fixed instance serve as repeated comparison blocks, not independent machine samples." |

## 3. The CI method

### What we use

| Used for | Method | Citation |
|---|---|---|
| Error bars on across-reruns plots | 95% percentile bootstrap: 10,000 resamples of whole reruns, read at 2.5% and 97.5% | Hesterberg 2015 (to add) |
| Reported beside it as a check | 95% t interval | Already cited: [28] Georges 2007 |

Per-rerun throughput plots keep the ledger's CI, and `pauses_max` has no CI.

### What the thesis has to say

- **Coverage:** at ten reruns the percentile bootstrap really covers about 90% on normal data, not 95%. State this openly.
- **New citation:** the thesis has no bootstrap reference yet, so one must be added. Hesterberg 2015 states this narrowness.
- **Scope:** all reruns use one instance, so the CI describes variation on this machine in this period.

Suggested wording, after the sentence ending "…with wide intervals indicating an imprecise result [35]." in §2.2.4 p.11, or in the methods chapter:

> Ten reruns cannot establish normality [35], so intervals are computed by resampling whole reruns with a 95% percentile bootstrap. This interval is known to be somewhat narrow in small samples [Hesterberg 2015], covering roughly 90% rather than 95% on normal data at ten reruns, so a t interval [28] is reported beside it as a check.

The 90% figure comes from our own calculation, not a citable source, so present it as such.

### What we considered and did not use

| Method | What it is | Why not |
|---|---|---|
| **Expanded percentile bootstrap** (recommended first) | The same resampling, read at 0.855% and 99.145% for ten reruns, which corrects the narrowness and covers about 95% on normal data | We wanted peer-reviewed, widely used methods. Hesterberg 2015 names and recommends it, but the formula was found only in the unreviewed arXiv version (1411.5279), and no independent evaluation or use was found. |
| BCa bootstrap | Percentile bootstrap with bias and skew adjustments | It covered about 90% at ten reruns in our simulation, no better than plain percentile, and adds complexity. |
| Bootstrap-t | Resamples a t statistic | It becomes infinite when many values are tied (for example `batch_fill` at 1.0) and is unstable with outliers. |

Details are in [resampling.md](resampling.md).

## 4. Sentences to add in the methods chapter

| Topic | What to say |
|---|---|
| Throughput | Scores are operations per second, and [35] warns against the arithmetic mean of such values in general. With fixed-length iterations, the mean equals total operations divided by total time, so it is valid here. This holds only if measured iteration durations really are equal. |
| Speedup | Speedup across reruns is a ratio of means, mean(strategy) / mean(base), matching [35] and [38]. CIs resample whole reruns so each strategy stays paired with its base. |
| `gc_alloc_rate_norm`, `batch_fill` | These are means of per-rerun ratios with varying denominators. The effect should be small at low variation, but state it. |

## 5. 2A vs 2B comparison

Full details and citations are in [comparison_2a_2b.md](comparison_2a_2b.md). The procedure is not final until the decisions in the plan `add-2a-2b-comparison` are made.

### Cohen's d

§2.2.4 p.11 says: "Cohen's d … divides the difference of two means by their pooled standard deviation [25]."

The old 2A vs 2B idea instead divided the mean log ratio by the SD of the log ratios, which is a different quantity. The research recommends dropping d and reporting the ratio with its CI. If so, replace "…so it complements the ratio rather than replacing it." with "…so this thesis reports the ratio and its confidence interval instead [38, Baguley]."

### Equivalence

Replace "Likewise, an interval that includes no effect does not establish equivalence, which would require an explicitly stated equivalence margin." (§2.2.4 p.11) with:

> Likewise, an interval that includes no effect does not establish equivalence [Walker]. Equivalence requires a margin fixed before the data are seen [Walker], and it is shown by two one-sided tests that reject differences at both ends of the margin [Berger & Hsu, Lakens 2017].

### References this procedure needs

Fieller 1954, Dunn 1961, Berger & Hsu 1996, Walker & Nowacki 2011, Lakens 2017. Recommended: Baguley 2009, Freeman 1989, Laaber 2019.

### Other points the research found

| Where | Point |
|---|---|
| §2.2.4 p.11, log-scale sentence | Optional rewording, since we use a ratio of means |
| §3.8.5 p.38 | Consider "If the comparison is inconclusive" |
| §3.9.3 p.40 | "(??)" needs its reference |
| §3.10.1 p.40 | The O(M log M) claim for 2B depends on which sort the code uses |
| §§3.9–3.10 | Some paragraphs render as run-together text running off the page, a LaTeX macro problem |

## 6. Citation caution

Kalibera & Jones [38] appeared at ISMM, which ranks low (CORE C). Keep it as background, not as the only support for a method.
