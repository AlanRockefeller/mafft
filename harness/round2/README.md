# Round 2 experiments

Use a private artifact directory, retain the original goldens and stop competing
builds or experiments before timing. Experiment drivers use private prefixes.

The separate [production deployment script](DEPLOY.md) stages the audited build
under a versioned prefix and supports atomic activation and rollback. It is a
read-only dry run unless invoked with `--apply` and an explicit production
entrypoint.

The dikarya4 validation source is frozen at Git commit `9d963ee`. To rebuild
that exact source without using existing object files:

```sh
validation_source=$(mktemp -d)
git archive 9d963ee | tar -x -C "$validation_source"
python3 harness/round2/build.py --source "$validation_source" \
  --output /private/new-dikarya4-build --jobs 1
```

`build.py` uses GCC and `-O3 -march=native -ffp-contract=off -g` by default.
Its new private output contains source, build commands/logs, installed files
and hashes. It refuses an existing output directory. Preserve that directory
and use another path for each variant. The CLUSTAL alignment stamp remains
opt8 while diagnostics identify dikarya4.

`MAFFT_REJECT_MEMO` defaults to one in the shared header. Its narrow runtime
guards authorize exact reuse only in a verified terminal no-gain suffix of
one-worker refinement. For an ablation, pass `--flags` with the full strict
flags above plus `-DMAFFT_REJECT_MEMO=0` to a separate build. The
[dependency argument](../../paper/round2-refinement-reuse.md) explains the
short-block, scoring-symmetry and scratch-state exclusions. Independent
`MAFFT_REJECT_MEMO_TRACE=1` enables semantic traces; do not time a trace build
as the normal release. The residue-map cache on its separate experimental
branch remains default-off and is not part of this source.

The score-only rolling fill is in `core/l11_avx2.h`. Its public return values can
be checked against the original double recurrence with
`harness/broadwell/noalign_check.py` and the existing `L11_SCORE` driver, as
described in [the Broadwell harness](../broadwell/README.md).

Two additional AVX2 switches support independent experiments:

- `MAFFT_KB_SELF_SCORE=1` enables the guarded analytic score for identical strings.
  It is enabled by default; compare with `=0` for a controlled ablation.
- `MAFFT_KB_ZERO_EXT=1` enables an explicit zero-extension specialization of the
  full traceback row, enabled by default. It shares the generic row body; the compiler receives a constant
  zero extension. Compare with `=0`, and check the resulting assembly.

For the analytic shortcut, every used character must have the same nonnegative
diagonal score M, every used substitution must be <=M, both gap penalties must be
nonpositive, and the local threshold must be <=M. The result is then exactly n*M:
the identity path attains the upper bound. Other settings retain the DP fill;
zero scores retain the public original-double fallback for signed-zero behavior.

Create a scratch copy and use the focused self-score checker:

```sh
scratch=$(mktemp -d)
cp core/Lalign11.c core/l11_avx2.h "$scratch"
python3 harness/broadwell/noalign_check.py "$scratch"
gcc -O3 -march=native -ffp-contract=off -std=c99 -Denablemultithread \
  -DMAFFT_KB_SELF_SCORE=1 -DMAFFT_POISON_TEST=1 \
  "-DL11_SOURCE=\"$scratch/Lalign11.c\"" -Icore \
  -ffunction-sections -fdata-sections harness/round2/self_score_check.c \
  core/defs.o -Wl,--gc-sections -lm -lpthread -o "$scratch/self_score_check"
"$scratch/self_score_check" 10000
```

The wrapper checks all 64 return-value bits against the original double fill.
The focused generator includes eligible self pairs, unequal lengths, nonuniform
diagonals, dominating off-diagonal scores, positive gap penalties/extensions,
large positive thresholds, signed zeros, fractional scores, ambiguity codes and
protein alphabets. Add ASAN/UBSAN flags for memory/undefined-behavior checks.

`batch_dp.c` is an experimental driver, with no production dispatch. It puts
eight independent score-only matrices in eight AVX2 lanes and masks unequal
sequence lengths. A shared query permits ordinary packed profile loads;
independent query characters use gathers. Its checker compares all raw integer
scores against both rolling striped DP and an independent scalar double
transcription. The raw zero scores are compared numerically with scalar double;
the separate public-call checker covers signed-zero fallback.

```sh
gcc -O3 -march=native -ffp-contract=off -std=c99 -Denablemultithread \
  -ffunction-sections -fdata-sections harness/round2/batch_dp.c core/defs.o \
  -Wl,--gc-sections -lm -lpthread -o "$scratch/batch_dp"
"$scratch/batch_dp" check 2000
"$scratch/batch_dp" bench 64 700 700 shared
"$scratch/batch_dp" bench 64 700 700 independent
"$scratch/batch_dp" bench 64 700 700 ragged
"$scratch/batch_dp" fasta /private/input.fasta 8 shared
```

FASTA mode benchmarks actual sequence shapes with an explicitly synthetic
+600/-450 nucleotide scoring matrix, zero gap extension and -1,530 gap opening.
It is a kernel experiment, not a whole-MAFFT performance or biological result.
The timing includes profile packing and arithmetic/alphabet guard checks and
alternates implementation order over three rounds. `valid_cells` and
`padded_cells` expose wasted work from length imbalance. FASTA normalization in
this driver uppercases letters and drops preexisting `-`/`.` gaps; it never
rewrites the input file or creates a biological alignment.

`lfill_guard_check.c` tests integer-path rejection before unsafe conversions,
including nonfinite/out-of-int scores, INT_MIN/MAX scoring parameters, impossible
length bounds and ordinary/fractional/signed-zero controls:

```sh
gcc -O1 -g -march=native -ffp-contract=off -std=c99 -Denablemultithread \
  -fsanitize=address,undefined,float-cast-overflow -fno-omit-frame-pointer \
  -ffunction-sections -fdata-sections harness/round2/lfill_guard_check.c \
  core/defs.o -Wl,--gc-sections -lm -lpthread -o "$scratch/lfill_guard_check"
ASAN_OPTIONS=detect_leaks=0 "$scratch/lfill_guard_check"
```

This direct helper test deliberately does not execute the upstream scalar DP on
unsupported extreme parameters. It does not establish their end-to-end support.

## Paired timing and shared-VM CPU checks

`paired.py` replays the recorded production strategy and direction options with
one MAFFT thread, verifies complete alignment bytes against frozen goldens, and
records each installed binary's hashes and checked version. Two builds alternate
order for each input across rounds. The experiment plan freezes inputs, goldens,
build hashes and timing settings; a differing resume plan is rejected. Give every
new experiment a new output directory. A `PAUSE` file in an experiment directory
holds execution at job boundaries.

```sh
python3 harness/round2/paired.py \
  --manifest /private/manifest.json --data /private/inputs \
  --golden /private/golden --output /private/new-paired-experiment \
  --build reference=/private/reference/bin/mafft \
  --build candidate=/private/candidate/bin/mafft --rounds 2
```

Both timing runners enable the read-only quiet gate by default. Before each
paired job or throughput policy, it requires two consecutive one-second samples
with at most 0.10 busy CPU cores globally and no recognized scientific process
using more than 0.05 cores. The gate waits at most 300 seconds, then stops before
launching the timed work. It never stops or signals an external process.
`--quiet-timeout`, `--quiet-interval`, `--quiet-samples` and `--quiet-max-cpu`
configure these limits. `--no-quiet` or `MAFFT_BENCH_QUIET=0` disables the gate;
the override is recorded in the immutable experiment plan. Gate waits occur
outside service and policy timing, with progress in `STATUS.json` and retained
sample metadata.

Every timed job also records `/proc/stat` snapshots. Global guest busy CPU minus
GNU-time job CPU estimates unaccounted work; negative residuals from sampling or
rounding remain recorded, and contention is flagged without excluding results.
For simultaneous workers, individual residuals include the other measured job
and are explicitly unsuitable for external attribution. The throughput policy's
aggregate uses the sum of all measured job CPU instead. Steal time is recorded
separately. These estimates cannot rule out host-side VM interference.

The reusable API is `quiet.settings(args)`, `quiet.wait_for_quiet(config,
status_path=..., context=...)`, `quiet.cpu_snapshot()` and
`quiet.busy_delta(before, after, job_cpu_seconds, ...)`. `quiet.add_arguments`
adds the runner controls. Quiet checks read process names, identities and CPU
counters; they do not read external command lines or send signals.

## Throughput replay

`throughput.py` uses a saturated queue with all selected jobs available at time
zero. Seeded queue orders are shared across policies within each round; policy
order rotates between rounds. This models backlog throughput rather than
observed production arrival times. Service time, queue delay and response time
are reported separately.

The default policies are serial `two_threads` (configuration B), concurrent
`two_jobs` (two configuration-A workers pinned to separate CPUs), and serial
`selective` (configuration C). Optional `production` runs jobs serially with
their recorded per-file one- or two-thread settings. `one_job` and `refine_one`
are additional explicitly selected controls. Use `--all-inputs` to include the
full production mix; otherwise the recorded two-thread inputs are selected.

```sh
python3 harness/round2/throughput.py \
  --manifest /private/manifest.json --data /private/inputs \
  --golden /private/golden --binary /private/candidate/bin/mafft \
  --source /private/frozen-source --output /private/new-throughput-experiment \
  --all-inputs --policies production two_threads two_jobs selective --rounds 2
```

Supply `--source` for the frozen source snapshot actually used to build the
binary. Its hashes complement installed-file, launcher, manifest and golden
hashes; retain separate compiler/build provenance to establish correspondence.
The harness never assumes that the current checkout produced an installed
binary. Sampled process-group RSS measures concurrent measured jobs only;
per-job GNU-time maximum RSS is a separate statistic. Timeouts and interruption
terminate only measured child groups, which remain registered for RSS sampling
through cleanup. A failed throughput experiment is preserved and requires a new
output directory.

The startup manifest also records the running Python version, executable and
SHA-256 hashes of `throughput.py`, `quiet.py` and `alignments.py`. Each policy
reports `observer_cpu_seconds` and `observer_cpu_cores`: one process CPU interval
covering all Python runner threads around the global CPU snapshots, excluding
child jobs. This identifies sampling and bookkeeping cost absent from GNU job
CPU. It is a diagnostic only: the harness does not subtract it from contention,
alter quiet thresholds or change which prospective attempt is accepted.

RSS sampling still runs every 0.2 seconds and sums resident pages from `statm`
for the registered process groups. It enumerates `/proc` with `os.scandir` and
queries each numeric PID's current group using `os.getpgid`, avoiding a `stat`
file read for every unrelated process. An `OSError` falls back to the original
`stat` parser. No PID/inode membership cache is retained: proc inode numbers
are not permanent process-generation identifiers. The sampler retains its
existing non-atomic membership/read behavior when processes exit or PIDs are
reused; the mocked fixtures cover membership changes, fallback and read races.

The mocked runner checks launch no scientific jobs:

```sh
python3 -m unittest discover -s harness/round2 -p test_quiet.py
python3 -m unittest discover -s harness/round2 -p test_throughput.py
python3 -m unittest discover -s harness/round2 -p test_execution.py
```

## Confirming per-input regressions

After the full paired summary, `confirmation_plan.py` creates an immutable
follow-up plan for inputs exceeding both 5% CPU regression and 50ms, plus any
input whose original timings had flagged or unknown CPU contention. Its three
new interleaved rounds use the same binaries, inputs and goldens. Execute the
generated plan with `run_plan.py`. Original corpus results remain intact and
remain in the original totals; confirmation measurements are reported separately.
