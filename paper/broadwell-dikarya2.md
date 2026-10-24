# Exact speedups on the Dikarya AVX2 virtual machine

Research date: 2026-10-19. Candidate version: **7.526-opt8-dikarya2**.
Frozen starting commit: `322527b8f0b4b6b030cd4325a8aafe7c970a5764` (opt8).

This investigation targets the actual Dikarya VM and its supplied 100-input production
corpus, not an idealized Broadwell machine. The comparison baseline is **already optimized
opt8**, not stock MAFFT. No production executable, application configuration, kernel setting,
service or package was changed. The candidate is installed only in a private benchmark prefix.

## Result and scope

The selected artifact is `bench/install-final2`, built from `bench/final2`; its full paired
measurement is in `bench/final2-corpus/runs.jsonl`, outside Git. **All 100 outputs were
byte-identical** to their frozen goldens. Total wall time fell from **865.15 s to 647.41 s:
1.336x faster, 25.17% less time**. Child CPU time fell from **857.87 s to 641.20 s: 1.338x,
25.26% less**.

| Frozen partition | Inputs | Reference wall (s) | Candidate wall (s) | Speedup | CPU speedup |
|---|---:|---:|---:|---:|---:|
| Development | 12 | 102.38 | 61.12 | 1.675x | 1.678x |
| Holdout | 78 | 610.55 | 468.65 | 1.303x | 1.305x |
| Difficult | 10 | 152.22 | 117.64 | 1.294x | 1.293x |
| All | 100 | 865.15 | 647.41 | 1.336x | 1.338x |

These are fresh, sequential interleaved reference/candidate runs on CPU 1 with alternating
build order, nice 10, locale C and no competing scratch builds/training. All commands and
hashes are retained. They are **not** comparisons against earlier golden-generation times.
One whole-corpus paired round is descriptive evidence, not a confidence interval on VM
scheduling noise. Median per-input wall speedup is 1.286x; geometric mean 1.330x. The
candidate was faster on 98 of 100 inputs in wall time and 99 in CPU time.

An earlier separate-byte-traceback checkpoint also matched all 100 goldens and measured
1.343x wall speedup in its own paired pass (`bench/final-corpus`). Embedded tags were
selected on a direct, clean two-round development comparison (another 1.8%), not by
comparing totals from different sessions. The baseline itself drifted between sessions;
these two corpus ratios are not an estimate of that small incremental layout gain.

The 97 direction-adjusted L-INS-i jobs alone improved **1.314x wall / 1.316x CPU**
(828.74 to 630.54 wall seconds). The accurate-direction prepass improved 6.335x wall /
6.336x CPU (23.62 to 3.73 wall seconds), so it is deliberately reported separately rather
than used as a representative case. The two `--auto` jobs selected FFT-NS-2 and FFT-NS-i;
they were about 2.8% slower combined in this round (12.79 to 13.14 wall seconds). Their
three-round repeats confirmed the FFT-NS-2 regression (about 4.6% wall / 4.9% CPU), while
FFT-NS-i was within about 0.5%. No across-the-board speedup is claimed.

Major faults totaled 0/5 for reference/candidate. Input block reads totaled just 40/88 blocks,
respectively; output/temp block writes were almost identical (513,376/513,360 blocks).
Aggregate CPU/wall ratios were 99.2%/99.0%. This supports the
compute/cache diagnosis, not system-wide I/O wait as an explanation of MAFFT runtime.

Separate GNU `time -v` probes (each output compared with its golden) reported these maximum
resident set sizes. These are selected examples, not a per-build maximum over the corpus;
the benchmark runner's cumulative child-RSS high-water field is not used as per-run RSS.

| Probe | Reference peak (MiB) | Candidate peak (MiB) |
|---|---:|---:|
| Representative local-pair job | 41.98 | 40.36 |
| Accurate-direction prepass | 11.54 | 11.53 |
| Expensive local-pair job | 122.99 | 101.91 |

The cache caps apply to requested table/result allocation bytes; allocator overhead and
small grouping arrays are additional. They are not a promise that whole-process RSS is
limited to 64 MiB. No swap or memory-pressure failure was observed in these tests.

The exactness contract is alignment **stdout in every byte**, including headers, ordering,
direction labels, gaps and line wrapping, against the frozen production-compatible reference
with the same input, scoring/strategy/direction flags and one thread. Stderr contains a new
version and performance-related diagnostics; it is not claimed byte-identical. This does not
extend stock's nondeterministic threaded iterative refinement into a deterministic algorithm.
No faster alignment strategy, reduced iteration limit or changed score was substituted.

As a limited threaded audit, two development inputs whose recorded setting was thread 2
were each run three times per build, with affinity allowing both guest CPUs. All 12 outputs
matched, including repeated reference runs. No variability was observed in this sample;
it does not establish determinism for other iterative thread-2 workloads or schedules.

That limitation matters in the actual corpus: a supplemental pass on **all 46 inputs whose
recorded thread setting was 2**, using their recorded strategy/direction flags and allowing
both CPUs, produced 26 matching reference/candidate pairs and **20 different alignments**.
These are not accepted as byte-identical results, and their timings are not counted in the
headline speedup. Repeated runs of the unchanged frozen reference on four affected inputs
itself produced multiple distinct alignments under identical flags/environment/affinity.
Four repeats per affected input yielded 4, 4, 2 and 4 distinct reference outputs. In a
separate diagnostic, all 45 actual two-thread local-pair inputs matched before refinement
with `--adjustdirection --localpair --maxiterate 0 --thread 2`. Disabling iterations was
only an isolation test, not a changed production command or a claimed performance gain.
Thus strict byte identity is supported here for **thread 1**, not general threaded iterative
refinement. No production thread setting has been changed to hide this distinction.

## What hardware was actually exposed

| Property | Guest observation |
|---|---|
| Virtualization | VMware full virtualization; Ubuntu, Linux 6.8.0-139 |
| CPU brand | Intel Xeon E5-2690 v4 @ 2.60 GHz |
| Actual CPUID | Family 6, model 63, stepping 0; GCC native tuning resolves to **Haswell** |
| Topology | 2 logical CPUs, 2 guest sockets, 1 core/socket, no guest SMT, 1 NUMA node |
| ISA | AVX2, FMA, BMI1/BMI2, POPCNT, SSE4.2, MOVBE, AES/PCLMUL; no AVX-512 |
| Reported caches per vCPU | L1D/L1I 32 KiB each; L2 256 KiB; L3 35 MiB |
| Cache sharing | Guest sharing masks are private; physical placement/sharing cannot be inferred |
| Frequency | Nominal 2.6 GHz, invariant virtual TSC; no guest cpufreq/turbo observation |
| Restrictions | CPUs 0–1 available; no cgroup CPU quota/throttling found |
| Memory | Approximately 16 GiB, no swap; about 10 GiB available during tests |
| Toolchain | GCC 13.3.0, GNU binutils/linker 2.42; no clang on PATH |
| Counters | `perf_event_paranoid=2`; hardware events unsupported by this VM, software sampling works |

The brand string is not sufficient to select a physical Broadwell-specific implementation.
`-march=native` follows the guest CPUID. No AVX-512 kernel was executed here. The compiler's
existing C99, non-contracting GCC arithmetic class was preserved with an explicit
`-ffp-contract=off`; fast math, reassociation and precision reduction were not used.

A small read-only-in-effect benchmark (private user memory only) measured about **11.4 GiB/s
of thread-CPU-time streaming-triad bandwidth** for a 192 MiB working set, about 10.4 GiB/s wall
bandwidth in that session. Dependent loads measured approximately 1.55 ns in L1, 3 ns at
64 KiB, 4 ns at 256 KiB, 9 ns at 1 MiB, 38 ns at 4 MiB, 90 ns at 16 MiB and 100 ns at 64 MiB.
These are VM/session measurements, not physical-host peak specifications. Some exploratory
measurements overlapped work on the other guest CPU; final paired timing does not.

MAFFT profiles and child resource accounting show CPU work, not substantial input block I/O.
Most input is page-cache resident, temporary output is small relative to computation, and
final CPU/wall ratios are recorded per run. The user's system-wide I/O-wait snapshot is not
evidence that MAFFT itself is I/O-bound. Physical host scheduling/cache contention remains
unobservable; no physical-frequency or hardware-cache-miss claim is made.
Software sampling alone cannot separate execution-unit limits from cache/memory stalls.
The evidence supports a compute/cache workload, not a claim that every hot loop is purely
arithmetic-bound or that virtualization has zero performance cost.

## Production reference, options and corpus

`/usr/bin/mafft` is distribution 7.505 and is **not** the reference. Accessible live application
job logs identify `/opt/mafft-7.526-opt8/bin/mafft` in 85 jobs and
`/opt/mafft-7.526-opt5-dikarya1/bin/mafft` in 15. A clean opt8 rebuild with GCC 13.3,
`-O3 -march=native -g`, the existing C99 mode and pthread support has exactly the same
`.text`, `.rodata` and `.data` contents as **all 32 installed opt8 ELF executables**.
All 15 earlier-release jobs were also rerun single-threaded and matched the opt8 goldens.
The reference was not chosen by accepting a convenient candidate's output.

Immutable goldens were generated with installed opt8, SHA-256 hashed and made read-only
before candidate evaluation. Complete argv, input/output hashes and selected environment
are retained per run. The selected candidate source snapshot and all binary hashes are in
`bench/final-provenance.json`; all 88 core C/header/template/Makefile files matched the built
snapshot. Production binary and golden manifests were rechecked unchanged.

The actual per-input production flags recovered from matching live job logs were:

| Count | Strategy/direction flags |
|---:|---|
| 97 | `--adjustdirection --localpair --maxiterate 1000` |
| 2 | `--adjustdirection --auto` |
| 1 | `--adjustdirectionaccurately --retree 1 --maxiterate 0 --quiet` |

Of those jobs, 54 used thread 1 and 46 used thread 2. The primary **deterministic** corpus
benchmark keeps the recorded strategy/direction flags, but explicitly sets **thread 1 for
all 100**. Thus it is not silently presented as a measurement of the live mixed-thread job
stream. The accurate-direction invocation appears to be a prepass; it is still a real
recorded MAFFT task and is kept in the corpus. Most supplied inputs were not production
`--auto` invocations; generic `--auto` timing alone would misrepresent this workload.

The 100 files contain 97 distinct raw inputs, **13,356 sequences** and **8,780,208 ungapped
residues**. Counts range from 8 to 1,042 sequences/file (median 131). Ungapped sequence lengths
range from 164 to 1,906; the 10th/50th/90th/99th percentiles are 540/650/761/1,210. Length
coefficient of variation by file ranges from 0.0084 to 0.482 (median 0.102). The sum of pairwise
length products is about **596.8 billion cells**, a complexity proxy, not the number actually
executed by every MAFFT strategy.

There are 2,086 duplicate sequence rows (15.6%), but only three duplicated whole inputs.
This motivates exact **within-job** pair reuse, not whole-job result caching. Composition
includes 11,480 ambiguous residues (about 0.131%), mostly N, as well as IUPAC alternatives;
37,705 input gaps in 9,115 runs are present. Sampled canonical 11-mer Jaccard medians range
from 0.087 to 0.971 across files (median 0.510). This is a similarity proxy, not aligned
percent identity. Final golden widths range from 533 to 2,391, median 1,045. Weighted alignment
gap fraction is 46.16%, with 85.14% of gaps terminal—different from the report's earlier
roughly 71%-gap pipeline corpus.

A deterministic cost-stratified/hash-selected split was frozen before tuning: **12
development**, **78 holdout**, and **10 difficult** files. Difficult selection includes high
pair cost, long/variable lengths and ambiguity, not merely the largest file. Holdout inputs
were not used to choose optimization parameters or train PGO. Raw sequences, alignments,
headers, logs and performance profiles remain outside Git.

## Accepted implementation changes

1. **Eight-lane AVX2 striped integer local DP.** The existing AVX-512 striped recurrence was
   adapted to AVX2, retaining int32 scores, strict comparisons and the original diagonal,
   horizontal, vertical tie priority. The existing integrality/range guard falls back to
   double precision when necessary. Full integer rows and traceback kinds allow exact
   original gap-origin replay; no saturating/reduced-precision approximation is used.
2. **Deferred end-cell search.** Once the best score exceeds the clamp threshold, only the
   final winning row is searched. Decoding its score and subtracting the identical profile recovers
   unclamped cell scores; the first winning row/column remains the same. The unused full
   int traceback allocation and reads are skipped when striped traceback is available.
3. **Ordered exact-duplicate pair cache.** Hashing finds candidate duplicate groups, but
   actual `strcmp` equality is required. `(A,B)` is not conflated with `(B,A)`. Only the
   fixed-matrix, zero-offset local-pair call is cached; dynamic-matrix realignment, distances,
   local-homology processing and output ordering still run as before. Immutable results are
   published under a mutex and released after workers join. Table size is capped at 16 MiB,
   table/results at 64 MiB; allocation/budget failures simply recompute. No input/output
   survives an invocation. Apple/CUDA GPU paths are excluded. `MAFFT_NODUPCACHE=1` disables it.
4. **AVX2 profile operations.** Four independent grouped profile scores use four lanes and
   ordinary stores (no AVX2 scatter). Each lane retains the original multiply/add order.
   A block-independent prefix scan shortens the inter-block carry chain, retaining the
   earliest winner on ties, including signed-zero cases. Neither change reassociates sums.
5. **Importance-matrix locality.** Haswell/Broadwell-tuned AVX2 builds skip band-list setup
   when the estimated matrix fits 16 MiB, retaining the original 128-row bands for larger
   matrices. In non-contracting builds a repeated identical segment-weight product is
   rounded once outside the cell loop, with the original cell-add operand order unchanged.
6. **Accurate-direction score-only DP.** Guarded integral score-only calls reuse the same
   exact striped recurrence instead of the old scalar double fill. Zero scores fall back
   to the original path to preserve possible negative zero; fractional/range failures also
   fall back. Per-thread workspace is freed at public NULL-input cleanup boundaries so
   repeated direction-check worker creation does not introduce workspace leaks.
7. **Lossless score/traceback co-storage.** The existing range guard bounds stored scores
   within +/-500 million, leaving room for an exact left shift by two in int32. Two
   traceback bits occupy the vacated low bits; arithmetic right shifts recover every
   original score, including negative ones. This removes separate traceback packing,
   stores and allocation without reducing the precision of any accepted score. The
   separate-byte layout remains available with `MAFFT_KB_EMBED=0`.

The selected build is ordinary `-O3 -march=native -ffp-contract=off -g`, not PGO or LTO.
The version label is changed to distinguish its diagnostics from frozen opt8; alignment
formatting, algorithm/strategy selection and scoring defaults are unchanged. In particular,
CLUSTAL output embeds a version stamp: `ALIGNMENT_VERSION` deliberately preserves the
reference's `7.526-opt8` header, separately from the new diagnostic build `VERSION`.

## Experiments rejected or not selected

| Experiment | Observation and decision |
|---|---|
| Four-lane striped DP | About 0.978 ns/cell versus 0.562 for deferred eight lanes; rejected |
| Initial eight-lane fill | About 0.702 ns/cell versus 0.719 marker baseline; deferred search mattered |
| Packed three-bitplane traceback | About 0.562 ns/cell, no additional win; off by default |
| Importance bands 16/32 rows | Slower than original 128 rows in real refinement replays; rejected |
| Branchless importance bounds | Slower in tested replays; off by default |
| Disable importance bands globally | Helpful on smaller replays, but use a 16 MiB cutoff instead of extrapolating to all sizes |
| GCC PGO | Trained only on development inputs; aggregate regression, especially accurate direction; rejected |
| PGO plus LTO | Also regressed in tested development runs; rejected; LTO alone was not established slower |
| Native AVX2 lane blend instead of XOR/AND/XOR | Fewer logical instructions, but about 0.551 versus 0.531 ns/cell after warm-up; rejected |

Fill microbenchmarks reused the integer matrix, as real MAFFT does. An early version allocated
it on every iteration and obscured kernel effects; those old numbers were discarded. The
reported approximately 1.0-billion-cell microbench runs supplement real-input timing, not
replace it. Four-lane and packed variants remain compile-time experiments for reproduction.
Exploratory runs sometimes overlapped work on the other vCPU and are not used as final wall
speedup claims. Preliminary accurate-direction icv1 CPU time fell from about 23.5 to 4.25 s;
the whole-corpus result must be reported separately to avoid cherry-picking this unusual job.

After embedding, fresh comparable drivers measured about 0.568 ns/cell for byte traceback
versus 0.533 for embedded tags, with identical checksums. Two interleaved rounds on all
12 development inputs gave 123.084 versus 120.872 CPU seconds (1.0183x), and 124.477 versus
122.218 wall seconds (1.0185x); 22 of 24 paired CPU measurements favored embedding. All
96 runs of this four-build comparison matched frozen goldens. In the same production-option
comparison, installed opt8 took 202.563 CPU / 204.070 wall seconds, versus dikarya1's
223.864 / 225.561: opt8 was **1.105x faster here**, not merely assumed faster from another
architecture. Compiler PGO trials preceded the score-only acceleration; their regression is
not proof that a newly trained PGO build of the latest kernel can never help.

## Differential evidence

- First byte-traceback fill/trace checking on two real development jobs: **11,199 pair calls,
  5,002,422,549 DP cells**, every score, resolved gap offset, endpoint, and full traceback
  strings/offsets matched the original scalar integer-offset recurrence. Allocation poison
  checks exercise unwritten-padding assumptions. The original opt5 harness establishes
  the integer recurrence against the original double fill under its guard.
- Profile differential checking: 84,331,736 original A-row cells and 100,455,737 partial
  A-row cells; 1,021 importance matrices containing 534,630,109 cells. Randomized profile
  tests add 5,000 cases/643,511 cells with ties, signed zeros, subnormals, fractional weights,
  ragged lengths and DNA/protein alphabets.
- The scalar-reference harness was corrected to actually disable the existing AVX2 loops;
  testing a vector path against itself is not accepted as differential evidence.
- Four-lane and packed-eight-lane variants each passed 5,000 extra randomized cases and
  18,579,363 cells, although neither was selected for speed.
- Score-only differential checking: all 14,746 calls in the real accurate-direction job
  matched the original double return value in every bit. A further **20,000 cases,
  70,814,471 cells**, including signed-zero/fractional/range fallback and periodic cleanup,
  passed both ordinary and ASAN/UBSAN checking builds (17,430 fast-path cases).
- Every duplicate-cache hit was recomputed in checking builds and compared in score bits,
  offsets and both aligned strings. Concurrent thread-2 checks included real execution on
  CPUs 0 and 1. Full iterative thread-2 output is not thereby proved deterministic.
- Earlier byte-traceback candidate: **396 successful nonempty adversarial alignments** in eleven option
  sets, **35 addition-mode comparisons**, **18 thread-0 comparisons**, and **12 concurrent
  thread-2 pair-stage/accurate-direction comparisons**, all byte-identical to the matching
  reference invocation. Tests cover boundary lengths 1–1,906, ties, homopolymers, duplicate
  orderings, input gaps, ambiguity, opposite strands, RNA and proteins.
- Pre-embedding sanitizer build: 30 successful strategy/score comparisons and the real
  accurate-direction job, plus the 20,000-case score fuzzer, no ASAN/UBSAN error. Leak
  detection was disabled because of existing process-lifetime allocations; no whole-program
  leak-free claim is made. A relocated launcher initially pointed at older helpers; it was
  corrected and tests rerun. The report uses the corrected runs, not that false coverage.
- Portable x86-64 and AVX-512 syntax checks pass for the changed translation units. Other
  architectures were not executed here; no new cross-platform performance claim is made.

An earlier test used an unsupported `--consweight` CLI flag; its 36 matching rejections were
not counted as alignments. Final fractional tests use the accepted `--weighti 0.37`.
Per-call harness checking toggles are single-threaded; cache-only synchronized hit checking
is the separate tool used for concurrency tests.

The rebuilt selected artifact additionally passed **540/540** successful comparisons across
15 strategy/format sets, including reordered output, CLUSTAL, reordered CLUSTAL and PHYLIP;
all 35 placements, 18 thread-0 and 12 concurrent non-iterative checks passed again. Three
two-sequence cases at lengths 8,180/8,190/8,200 matched across the integer-row size cutoff
and fallback. Default embedded, four-lane embedded, separate-byte and three-bitplane layouts
each passed 10,000 wide-score cases / 37,469,778 cells with poisoned padding. The default
wide test and 20,000-case public score-only test passed ASAN/UBSAN too. The selected kernel
object's code/data sections match the measured embedded experiment exactly; the corrected
I/O object's code/data sections match frozen opt8 exactly.

## Reproduction, artifacts and next questions

See [the harness instructions](../harness/broadwell/README.md) for independent builds, frozen
goldens, paired benchmarks, per-call differential tests, adversarial fixtures, additions,
sanitizers, software perf sampling and refinement replay. All `bench/` paths above refer to
the private sibling benchmark directory, not files committed to this repository.

The core implementation is in [l11_avx2.h](../core/l11_avx2.h),
[Lalign11.c](../core/Lalign11.c), [paircache.h](../core/paircache.h),
[partSalignmm.c](../core/partSalignmm.c) and [mltaln9.c](../core/mltaln9.c).
The harness refuses to overwrite goldens/results and records full commands. Reusing a build
directory for a new install prefix requires regenerating its launcher; otherwise it can
silently call old helpers. `provenance.py` audits source snapshots and reference ELF sections.

This is one measured research iteration, not a proof of global optimality. Remaining work
includes separating remaining refinement/profile work from pair DP, dedicated rolling-row score-only kernels (the current
reuse stores traceback it does not need), broader compiler tuning under the same rounding
class, and host-placement/cache-counter measurements if the VM ever exposes them. Any next
candidate must retain the frozen reference and rerun independent holdout/full-corpus checks.

Post-change software samples on development inputs put approximately 30-32% in the integer
fill, 13% in direct/banded importance filling, 8-11% in partial-profile alignment and 9% in
ordinary profile alignment. Both importance paths are exercised in real jobs. The FFT-NS-2
case is different: about 30% pair scoring, 26% ordinary profile alignment and 14% six-mer
comparison. Its scalar pair-scoring source and function length are unchanged, but its
address moved by 48 bytes; code/data/allocator-layout sensitivity is a hypothesis, not a
demonstrated cause. That function still builds two gap-filtered buffers and scores them on
non-Apple targets. A future direct scorer must preserve gap-run semantics and ordered
additions, or prove integer exactness before reordering. Existing skip tables contain
per-residue gap counts, not per-column jump offsets.
