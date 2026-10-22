# Prospective short-input isolated measurements

This is a separate protocol for the remaining short isolated jobs. Existing
measurements, their GNU time records, their contention flags and the accepted
long-input sets remain untouched. Full-corpus sustained replay and mixed pilot
throughput continue to use the original protocol.

Select short inputs **before** measuring them with this protocol, using mean
single-thread candidate wall time strictly below 1 second in the frozen exact
full-corpus experiment. The entire original manifest must be represented, with
one successful exact candidate record per input/round. Record the full-corpus
experiment and runs SHA-256 values, candidate identity, selected input, strict
threshold and complete eligible cohort. A failed or incomplete corpus cannot
define the cohort. Filenames that are copies of one raw input remain production
jobs, while technical runs are never counted as independent biological inputs.

For each selected short input, run four policies (A/B/C/F) in two paired rounds.
Each policy/round contains 20 fresh, sequential MAFFT invocations, each with a
new result directory. The order is A/B/C/F in round 0 and B/C/F/A in round 1.
Both vCPUs remain available to each invocation. This is isolated service latency,
not a backlog or independent-job concurrency experiment. The RSS observer retains
the existing 0.2-second process-group sampling helper.

The new driver uses niceness 10, inherited by GNU time, and retains the existing
`nice -n 10` native command. On this Linux system the expected native niceness is
therefore capped at 19. The driver records its actual niceness and this expected
native value separately; it does not pretend to have sampled every descendant.
Only the new driver's own priority may be lowered to 10. Inherited niceness above
10 is rejected rather than silently changing the comparison.

Before every policy batch, the existing gate must observe no more than 0.2 busy
cores globally and 0.05 cores for scientific processes. A pause is honored before
and after that gate. A batch is one prospective window: no individual replicate
is selectively dropped. A complete candidate set requires all eight policy/round
windows. A continuation controller selects the **first complete clean set**,
retaining every prior failed, partial, contended or deferred attempt. The driver
refuses an existing output directory; retries require a new directory.

## CPU accounting and selection

Retain all original GNU time per-job values, raw resources files and original
per-job and policy-wide contention calculations. The new field
`contention_rusage_children_v1` uses a **separate** parent-process
`resource.getrusage(RUSAGE_CHILDREN)` user+system delta, bracketing all 20 reaped
GNU time children. No other subprocess may run within this interval. The same
0.2-core residual ceiling applies. Observer CPU remains part of the residual and
is never subtracted. The observer's process CPU is diagnostic only.

The resource counter is process-wide and includes GNU time itself plus resource
usage propagated through normally waited descendant chains. It is not a
per-job attribution or a counter that can be independently added across
overlapping workers. Every direct GNU time child is waited before the final
snapshot. RSS monitoring creates a Python thread, not another subprocess.
Version probes occur outside the interval.

This accounting avoids losing the fractions below the printed centiseconds in
each GNU `%U` and `%S` value. Simply adding 20 individually truncated GNU values
would retain their proportional bias. Neither arbitrary printf precision
modifiers nor multiplying timer precision by the number of records fixes that.
The primary CPU comparison remains the recorded GNU user+system value for both
legacy and new measurements. The separate mean child-tree CPU includes GNU time
and is used only as a batch diagnostic and for the new contention field.

The summary schema is `mafft-round2-scheduling-summary-v2`. Per input/policy it
reports `paired_rounds=2`, `native_repeats_per_round=1` or `20`, and
`native_repeats_per_input=2` or `40` (also the honest `repeats` count). Service and
GNU CPU are first averaged within each round and then across the two rounds.
The pooled latency median/p95 uses these per-input means, giving each filename
equal weight. A short input's 40 technical runs do not receive 20 times the
weight of a long input's two runs. Optional diagnostic fields are `null` on
legacy rows so mixed tables retain uniform columns.

## Limitations and checks

The representation of child user/system CPU is finer than GNU's printed values;
this does not establish microsecond measurement accuracy. Aggregate `/proc/stat`
ticks, snapshot endpoints, the VM host, warm file/executable caches and transient
foreign work remain limitations. Twenty sequential jobs amortize endpoint
quantization but do not remove these limits. Aggregate quiet does not imply
every individual invocation was quiet; every original transient flag remains
visible. Two rounds give descriptive replication rather than a timing confidence
interval. The first-clean-set rule conditions results on the observed quiet gate;
it must be stated alongside all retained attempts. Batch jobs/hour is descriptive
and must not be used as the sustained production throughput result.

The observed full two-job replay had a signed global-minus-GNU CPU residual of
−28.17 seconds over 416.68 seconds. That is larger than endpoint rounding alone.
The new counter does not resolve such disagreement between VM/global and process
accounting, and can make a negative residual more negative. Passing the recorded
counter filter is an observational criterion, not proof of physical CPU
isolation. Signed residuals and original flags must remain visible.

`test_isolated_batch.py` contains synthetic-only fixtures for the precision
difference, invalid/mixed child counters, exact replicate identities and ordering,
unchanged GNU fields, complete eight-window selection, priority handling, strict
cohort/provenance checks and mixed-protocol equal-input averaging/export. It
launches no native programs.

The coordinating agent validated this protocol at a serialized boundary after
the sustained and repeated-pilot queues completed. All 18 batch/aggregation
mocked tests passed. A real GNU-time child with a normally waited grandchild
confirmed propagation of both generations' CPU into the parent counter, and
20 fresh single-thread MAFFT invocations exercised the actual collector with
20/20 golden-identical outputs and all children reaped. These smoke records
explicitly have `quiet_window.ready=false` and cannot be selected as timing
measurements. Commands, raw resources, source hashes and the owned-process
boundary proof are preserved in `round2/isolated-batch-v1-check` outside the
Git tree. The coordinating agent owns the serialized measurement schedule.

Primary references:

- [Python resource documentation](https://docs.python.org/3/library/resource.html)
  defines waited-child accounting and the floating user/system CPU fields.
- [Linux getrusage documentation](https://www.man7.org/linux/man-pages/man2/getrusage.2.html)
  describes cumulative waited children/descendants and the CPU time fields.
- [GNU time format documentation](https://www.gnu.org/software/time/manual/html_node/Format-String.html)
  specifies the percent conversion syntax.
- [GNU time source retained by Debian](https://sources.debian.org/src/time/1.7-21/time.c)
  shows the integer centisecond formatting of `%U` and `%S`. This source version
  explains the representation issue; the installed GNU time binary is separately
  identified by SHA-256 rather than claimed to be this source release.
- The [official GNU time 1.9 source archive](https://ftp.gnu.org/pub/gnu/time/time-1.9.tar.gz)
  also confirms this formatting in `src/time.c` (lines 530–538), using integer
  `TV_MSEC/10`; `src/resuse.h` defines `TV_MSEC` through integer microsecond-to-
  millisecond conversion. The upstream archive SHA-256 is
  `fbacf0c81e62429df3e33bda4cee38756604f18e01d977338e23306a3e3b521e`.
  Installed package metadata identifies `time 1.9-0.2build1`; matching upstream
  version does not assert a bit-identical unpatched build. The local executable
  is separately hashed in every batch experiment.
