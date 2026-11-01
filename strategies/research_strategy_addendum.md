Your setup is a reasonable starting point. You do not need to discard it or automatically replace the EC2 instance between reruns. Astra’s assessment agrees. The main work is validating the execution procedure and keeping the conclusions within what the experiment supports.

Your thread counts, record sizes, three zones, partitioner, batching deadline, and fixed heap define the workload you are studying. Those values alone do not establish correctness or invalidate the experiment.

With your JMH settings, each configuration receives:

- 50 seconds of warm-up per JVM fork, followed by 50 seconds of measurement.
- Across five forks: 250 seconds of measurement per configuration per rerun, plus 250 seconds of warm-up.
- Zero warm-up forks means no additional discarded JVM executions; it does not remove the configured warm-up iterations. JMH fork settings, warm-up settings.

That is substantial measurement time. It does not, however, turn the nested iterations into hundreds of independent experimental replicates.

Back-to-back reruns are defensible for a study of performance on the tested instance. Keeping the environment fixed helps isolate strategy and thread-count effects. Hoefler and Belli explicitly discuss this design and reused the same allocation for most of their experiments. My recommendation is to keep one instance for the primary comparison and state that scope clearly. Hoefler–Belli, §§4.1.1–4.1.2.

If you want conclusions about variation between EC2 allocations, then repeated provisioning becomes another experimental level. It is not required merely to compare implementations on one machine.

Also, “shutting down” has different meanings: an ordinary reboot retains the host; stop/start usually changes it but can retain it. Neither is a statistical independence guarantee. AWS instance lifecycle.

The concrete checks I recommend before committing the full budget are:

1. Vary and record execution order. Randomize or balance strategy/thread combinations between complete sweeps. Avoid measuring all repetitions of one strategy before moving to another. Keep each baseline associated with its sweep, and investigate drift if a sweep takes hours.
2. Retain per-fork and per-iteration results, alongside the ledger. Inspect them in execution order for trends, unusual forks, and changing performance.
3. Validate warm-up in a pilot. Check representative strategies at low and high concurrency. Five warm-up iterations are a starting choice, not proof of steady state.
4. Check profiler overhead. Compare representative configurations with and without the combined profilers. If the effect is material or differs by strategy, separate primary throughput measurements from profiling.
5. Verify comparable work and concurrency. Confirm what completion boundary the benchmark measures, how state resets, and how JMH workers relate to BlobShuffle threads.

These checks follow the concerns already described in your thesis §§2.2.1–2.3.4. Parameter values cannot replace them.

For ten reruns without established normality, my recommendation is to start with all individual paired speedup ratios and their arithmetic average. An observed range can accompany them, explicitly labeled as a range.

A 95% t interval remains a conditional analysis: ten runs, or a normality test that fails to reject normality, does not establish its assumptions. If those assumptions remain questionable, report the observed scaling and variability without claiming statistical significance or equivalence. The small-sample formula exists; its applicability still needs justification. Georges et al., §3.2.2.

To align the thesis, clarify the hierarchy: iterations within forks, forks within complete reruns, with the instance held fixed if that is your chosen design. Describe reruns as repeated comparison blocks, without calling them independent machine samples.

Your unit plan is correct: multiply ops/us by 1,000,000 for ops/s; retain sample-time results as us/op later. No files changed.