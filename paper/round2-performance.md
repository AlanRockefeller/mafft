# Round 2: further exact MAFFT speedups on the Dikarya VM

Research dates: 2026-10-19–20. Starting version: **7.526-opt8-dikarya2**.
Selected version for final validation: **7.526-opt8-dikarya4**.

**This is an interim performance report.** Component experiments and the earlier
dikarya3 tracked build/regression sweep are complete. Fresh dikarya4 release and
sanitizer builds and their checks have also passed. The final 100-file paired
benchmark, final profiles and production scheduling recommendation remain to be
finalized. No final whole-corpus speedup or production deployment is claimed here.
Phylogenetic findings belong in the
[separate threading study](round2-phylogenetics.md).

## Starting-point audit and preservation

The previous report, [Exact speedups on the Dikarya AVX2 virtual
machine](broadwell-dikarya2.md), was checked against the actual filesystem rather
than assumed correct from remembered paths. All 88 relevant core source files in
the previous working tree matched the verified handoff and `bench/final2`
snapshot. Those successful, previously uncommitted changes were preserved in
commit `8894774` and branch `archive/dikarya2-verified`. This round uses the
separate `mafft-round2` worktree and branch `perf/dikarya-round2`.

The original opt8 source remains independently available at commit
`322527b8f0b4b6b030cd4325a8aafe7c970a5764` and `bench/opt8`. Its production
installation was hashed and copied to private `round2/frozen-opt8`; only the
private launcher's helper prefix changed. Existing golden alignments and previous
benchmark directories were preserved. No production executable, service, thread
setting or system package was changed.

Dikarya2 was rebuilt from a Git archive of the tracked checkpoint into
`round2/install-baseline`. The code, read-only data and data sections of all 32
ELF helpers matched the previous `bench/install-final2` installation. In a fresh
12-input paired rebaseline, the old and rebuilt dikarya2 took 61.274 and 61.422
CPU seconds, respectively; all 24 outputs matched the frozen opt8 goldens.
The section audit is `round2/baseline-rebuild-sections.json`.

The previous 100-file result remains historical context:

| Previous paired experiment | opt8 wall seconds | dikarya2 wall seconds | Speedup |
|---|---:|---:|---:|
| Development, 12 inputs | 102.38 | 61.12 | 1.675x |
| Holdout, 78 inputs | 610.55 | 468.65 | 1.303x |
| Difficult, 10 inputs | 152.22 | 117.64 | 1.294x |
| All 100 | 865.15 | 647.41 | 1.336x |

Those denominators are **not** used to calculate this round's incremental gains.
Multiplying that historical 1.336x by component or development ratios would not
constitute a fresh paired opt8-versus-dikarya4 measurement.

## Machine, workload and measurement discipline

The VM exposes two vCPUs, approximately 16 GiB RAM and Haswell-compatible AVX2
under VMware. Its CPU brand string is not a reliable physical-cache or physical-
frequency description. Hardware performance counters are unavailable; software
CPU-clock samples identify where execution time is spent, not cache-miss counts
or execution-unit stalls. GCC 13.3 is the reference compiler. Clang 15.0.7 was
later found already installed at `/usr/lib/llvm-15/bin/clang`.

The unchanged corpus has 100 files, 97 distinct raw input hashes, 13,356 sequence
rows and 8,780,208 ungapped residues. The frozen partition remains 12 development,
78 holdout and 10 difficult files. One development input (`khx6`) is byte-identical
to holdout `vudu`. The original 78-file partition remains visible, accompanied by
a strict supplement of **75 distinct holdout hashes excluding every development
hash**. The other repeated input pairs are `1mhv`/`f1q8` and `o9i3`/`zdku`.
Production totals retain all 100 actual jobs; a separate 97-hash summary averages
repeated copies before giving each input one weight. Approximately 15.6% of sequence rows are exact
duplicates. Actual production strategy and direction flags are retained:
97 direction-adjusted L-INS-i jobs, two direction-adjusted `--auto` jobs and one
accurate-direction prepass. Deterministic performance comparisons explicitly use
`--thread 1`; they do not silently substitute a different alignment strategy.

`sample.tgz` itself was not found during the filesystem audit. The recovered
original extracted FASTAs in `bench/data` match all 100 archived manifest hashes;
the experiments use those actual fungal sequences, not generated substitutes.

Timed experiments were serialized, with builds, training and correctness runs
stopped. Build order alternated or rotated within each paired experiment. Earlier
kernel/stage probes used CPU 1; later compiler, ordinary-profile and FFT repeats
used CPU 0. Every comparison holds affinity fixed within its own experiment.
Absolute totals from different sessions are not pooled as paired observations.
Shared setup-window checks and statistics replays are excluded from speed claims.
VM/background activity remains a source of noise; CPU and wall results are both
reported, without treating a few repeats as a confidence interval.

The selected arithmetic class is `-O3 -march=native -ffp-contract=off -g` with
GCC's existing C99/pthread definitions. Fast math, reassociation and reduced score
precision are excluded. Exactness means every byte of single-thread alignment
stdout, including names, order, direction markers, gaps and formatting, against
the frozen production-compatible opt8 golden for the same options. Diagnostic
`VERSION` identifies the measured release; CLUSTAL's `ALIGNMENT_VERSION` remains opt8.

Full methods and resource definitions are in [round2-methods.md](round2-methods.md).
Paths beginning `round2/` and `bench/` below refer to private sibling directories
outside this Git tree. Raw inputs, alignments, binaries, learned profiles and logs
are intentionally not committed.

## Remeasured starting profile

These are selected dikarya2 software profiles, not a corpus-weighted allocation:

| Input / real strategy | Integer local DP fill | Importance filling | Ordinary profile alignment | Partial profile alignment |
|---|---:|---:|---:|---:|
| `bpfd`, L-INS-i | 30.11% | 13.52% | 9.89% | 7.97% |
| `gopv`, L-INS-i | 31.64% | 13.33%, banded | 8.65% | 10.76% |

For the 1,042-sequence `buh4` FFT-NS-2 job, `naivepairscorefast` accounted for
29.85%, ordinary profile alignment 27.67% and six-mer comparison 14.06%.
These measurements justified separate FFT scoring work instead of assuming that
the local-pair DP kernel dominated every strategy. Artifacts are
`round2/profile-baseline-{bpfd,gopv,buh4}.*`. Final selected-build profiles are
**pending**; the starting percentages are not presented as the new bottleneck mix.

## Selected exact transformations

The following are selected for final integration and validation. Their evidence
has different scopes; a kernel ratio is not a whole-program speedup.

### Rolling score-only DP and exact identity shortcut

The new integer score-only path keeps one in-place striped score row, one vertical
state row and substitution profiles. It removes retained traceback/history,
endpoint scans and unused row reductions. The final maximum is taken at the
same recurrence point, excluding padded lanes. Fractional/range failures retain
the original double path; every zero result also falls back to preserve signed-
zero return bits. A representative four-character, 700-residue target needs
16.5 KiB of integer workspace versus approximately 1.90 MiB of old score history
alone. These are workspace formulas, not process RSS.

An identical-sequence shortcut avoids DP only under a proven bound: used diagonal
scores equal a nonnegative M, all used substitutions are at most M, gap opening
and extension are nonpositive, and the threshold is at most M. The ungapped
identity path attains the `n*M` upper bound. Unsupported conditions retain DP;
no approximate identity threshold is used.

Rolling score-only CPU speedups were 1.418–1.503x for the longer measured shapes
and 1.195x at 64x64. No isolated identity-shortcut speedup was measured. These
changes principally help accurate direction rather than every L-INS-i job.
Details, proofs and test commands are in [round2-score-kernels.md](round2-score-kernels.md).

### Constant-zero extension in the traceback kernel

A shared inline row body receives an explicit constant-zero extension argument
when the original extension is zero. This eliminates five vector arithmetic
operations per cell while retaining strict comparisons, tie priority, offsets,
traceback tags and endpoints. Full-fill CPU speedups were 1.069–1.153x for the
longer shapes, and 1.032x at 64x64. The generic shared-body refactor alone was
within about 0.7% of the original. `Lfill_kb` text grew from 6,673 to 8,089 bytes,
so instruction-cache/code-layout effects remain a tradeoff.

Follow-up guard hardening checks floating finiteness/range before integer
conversion, widens bound arithmetic and avoids `abs(INT_MIN)` and
negation-overflow risks inside that guard. Inherited public scalar initializers
still need separate review before claiming support for INT_MIN offset/penalty
parameters. Its focused sanitizer checks passed; frozen component
timings precede that hardening, so final integrated timing remains necessary.

### Fused common-gap filtering and pair scoring

`naivepairscorefast`, `naivepairscore11` and the dynamic-matrix scorer previously
allocated/zeroed two gap-filtered strings, then traversed them again to return one
number. The new scorer consumes the same events directly. Common-gap columns
leave the active gap side unchanged; switching gap sides still costs another
opening penalty. Every original substitution or penalty addition occurs in the
same order. This preserves fractional dynamic matrices, signed zeros, arbitrary
supported alphabets and gap-run semantics without an integer-sum assumption.
The fast variant retains its skip-table traversal. No temporary strings or new
persistent workspace are required.

On `buh4`, direct scoring alone reduced the three-round CPU total from 23.490
to 22.220 seconds, a 1.057x whole-job gain in that experiment. It was essentially
neutral on `t4lr` FFT-NS-i (15.664 versus 15.644 seconds).

### Exact repeated raw profile rows

Within a profile-alignment call, bit-identical first-profile columns imply
identical raw substitution-score rows against the fixed second profile and
matrix. Hashes locate candidates; equality checks every double's bits, including
signed zeros. The cache contains raw rows before row-specific importance and DP
operations. Initialization row zero and the terminal boundary row bypass it.
State is per worker, bounded and cleared at the next initialization/public NULL
cleanup. Allocation/budget failures recompute the original row.

The partial-profile cache was first measured independently of other new kernels:

| Three refinement replays | Candidate1 CPU seconds | Partial-cache CPU seconds | Speedup |
|---|---:|---:|---:|
| `bpfd` | 7.503 | 7.245 | 1.036x |
| `gopv` | 13.657 | 12.973 | 1.053x |

All rewritten `pre` files matched byte-for-byte. An untimed/statistics replay
found 485,180 hits among 516,126 interior rows (94.00%) across 4,451 profile calls;
median distinct profile-column count was four. Peak requested persistent cache
storage was 89,505 bytes in that replay. High reuse does not imply a 94% runtime
reduction: DP, importance, hashing, copies and other work remain.

The separate ordinary `A__align` path still computed raw rows in scalar sparse-
column loops. A new reusable `rawrowcache.h` utility extends the same proof to
that path and its actual dynamic matrix, while leaving the earlier partial cache
unchanged. Matrix generation/pointer, profile pointer, alphabet and dimensions
are guarded; call scope is essential because pointer equality alone would not
prove unchanged matrix contents. Child-profile updates occur outside the row loop.

Ordinary cache+stack, candidate2 ordinary code and stack-only were compared in
one interleaved pass over all 12 development inputs:

| Ordinary-profile variant | CPU seconds | Wall seconds | CPU speedup vs candidate2 |
|---|---:|---:|---:|
| Candidate2, ordinary unchanged | 58.22 | 58.852 | 1.000x |
| Stack buffer only | 58.22 | 58.846 | 1.000x |
| Ordinary cache + stack buffer | 56.51 | 57.224 | 1.030x |

All 36 full alignments matched their goldens. Cache+stack improved CPU time on
11 of 12 files. `2dl9` slightly regressed, 7.90 to 7.93 CPU seconds (wall 7.941
to 7.965); that small difference is not evidence of a systematic regression.
The separate two-case refinement replay was nearly neutral: 19.771 versus
19.724 CPU seconds across three rounds (1.002x), with all outputs identical.
No material ordinary-cache refinement-stage gain is claimed from those replays.

A 32-double automatic `scarr` buffer was also exact: the original calloc zeros
are unused because `scarr_fill` assigns every element. It showed no standalone
development gain and is **not selected**. The final choice enables ordinary reuse
with the stack-buffer flag off. The measured ordinary variant above had both
enabled; that precise final combination still requires its final paired run.

### Immutable byte histograms for DNA six-mers

The original per-pair six-mer scorer increments and resets a temporary histogram.
The new strategy prepares immutable 4,096-byte DNA histograms once, then computes
the exact multiset intersection with AVX2 unsigned minima and integer SAD sums.
Counts are checked before narrowing. Non-DNA/unsupported tuple/table shapes,
counts above 255, more than 4,096 rows or allocation failure retain the original
algorithm. Histograms count the original transformed point lists, preserving
ambiguity/gap handling. The caller's distance arithmetic and taxon order remain
unchanged. Stage-local storage is at most 16 MiB plus alignment padding.

For `buh4`, the combined candidate's three-round CPU total was 18.150 seconds
versus dikarya2's 23.490 (1.294x); wall totals were 19.214 versus 25.519 (1.328x).
Disabling dense intersection in the **same combined binary** increased CPU to
20.250 seconds, isolating an additional 1.116x dense-intersection effect. These
are this FFT-NS-2 job's paired results, not a general all-strategy claim.

## Development checkpoints and FFT-NS-i follow-up

| Fresh paired development pass | Reference CPU / wall seconds | Candidate CPU / wall seconds | CPU / wall speedup |
|---|---:|---:|---:|
| Candidate1: rolling + direct scorer; 2 rounds, 12 inputs | 121.526 / 123.358 | 120.013 / 121.533 | 1.013x / 1.015x |
| Candidate2: adds identity/zero-extension/partial-cache/dense work; 1 round, 12 inputs | 60.660 / 61.399 | 58.184 / 58.886 | 1.043x / 1.043x |

Candidate1 produced 48/48 and candidate2 24/24 golden-identical outputs across
both builds. These are separate passes. The later ordinary-profile comparison
above adds evidence but is not multiplied into a fictional combined baseline.

An initial three-round FFT-NS-i component comparison had a candidate outlier and
an aggregate regression (16.125 versus 15.664 CPU seconds). That result was
retained rather than dismissed. After ordinary-row reuse was added, a new
five-round, four-build interleaved `t4lr` experiment gave:

| FFT-NS-i build | CPU total seconds | Wall total seconds | CPU speedup vs opt8 |
|---|---:|---:|---:|
| Frozen opt8 | 25.42 | 25.848 | 1.000x |
| Frozen/rebuilt dikarya2 | 25.68 | 26.036 | 0.990x |
| Ordinary cache + stack, combined changes | 23.48 | 23.793 | 1.083x |
| Same binary, dense six-mers disabled | 23.58 | 23.912 | 1.078x |

All 20 alignments matched. The new ordinary variant was 1.094x faster than
dikarya2 in CPU time here. Dense on/off differed by only about 0.4%, so it is not
assigned a meaningful FFT-NS-i gain from this experiment. This follow-up supports
improvement on the actual FFT-NS-i input while preserving the unfavorable earlier
measurement. The final stack-off selected configuration remains to be measured.

## Additional refinement reuse audit

An additional investigation counted repeated rejected branch attempts before
changing refinement. In the verified historical dikarya2 traces, 3,668 of 94,762
completed branch attempts (3.87%, retaining all 100 production jobs) revisited a
branch whose previous rejection had no intervening accepted alignment change.
No such repeat later accepted a change. The fraction varies: 30.51% of attempts
in `xwu3`, 28.29% in `najy` and 22.47% in `iyam`. This is an opportunity count,
not a CPU fraction or a speedup; branch costs differ considerably.

`harness/round2/rejected_branch_audit.py` validates archived stderr hashes and
preserves each refinement invocation. FFT-NS-i calls the refiner repeatedly, so
iteration/branch coordinates alone cannot identify an attempt across the whole
job. Only adjacent preliminary/final status messages are merged. The initial
exploratory audit that merged keys across invocations is superseded by the
tested parser and `round2/rejected-branch-audit`.

The isolated, default-off prototype is preserved on branch
`experiment/rejected-branch-memo` in the separate `mafft-reject-memo` worktree.
It retains exact rejected scores and original convergence-history updates,
invalidating every branch after an acceptance. Guards restrict it to one worker,
BAATARI2, fixed tree/order, zero cut, DNA, ordinary constrained profile alignment,
complete targets and no RNA/specificity scoring. Allocation failure disables the
optional cache. Its guarded final form is now selected for dikarya4 validation;
the earlier dikarya3 build remains independently frozen.

The first build's guard was inactive because dvtditr initializes `cooling=1`
even with inert `cut=0`. Those eight golden matches validate only the bypass.
The corrected active version matched all eight targeted real-input goldens and
skipped 1,259 attempts. A later revision adds explicit RNA exclusion, allocation
fallback and independently enabled semantic tracing.

Same-source memo-off/on trace builds then matched every semantic record on five
inputs: **2,469 attempts, 171 accepted intermediate alignments, 6,603 original-order
master rows and 508 cached attempts**. This checks raw double score/gain bits,
including signed zero, acceptance, subgeneration and convergence/finish flags,
as well as exact intermediate sequence strings. All ten final alignments also
matched the goldens. `round2/reject-memo-v3-semantic-analysis` retains the result
and hashed stderr paths. End-output identity alone was not used to assert equal
search paths. A later explicit suffix check also requires zero current gain and
valid stamps for every remaining actual branch before any hit, preventing
skipped scratch updates from feeding a later expensive attempt.

That earlier suffix-guarded prototype (`2b9008d`, artifact label v4) passed
**100/100** full golden outputs and **612/612** option/format comparisons.
Its three-round timing screens produced the following observed ratios, with
background contention retained rather than discarded:

| Earlier prototype screen | Dikarya3 CPU seconds per pass | Prototype CPU seconds per pass | Observed ratio | Flagged jobs / total |
|---|---:|---:|---:|---:|
| 12 development inputs | 56.657 | 54.743 | 1.035x | 70/72 |
| Three high-reuse inputs | 25.023 | 21.777 | 1.149x | 18/18 |

These are **not clean confirmation or accepted release gains**. Brief pre-job
quiet checks did not prevent background activity during most jobs. They also
precede the latest narrower eligibility rules, so cannot substitute for a new
measurement of that source.

Independent code review found inherited short-block scratch dependencies that
the broad predicate did not exclude. The latest isolated source, `ad2ccd1`
(artifact label v5), adds per-call safety reporting: blocks shorter than two
columns after gap removal, interrupted calls, warp/nonstandard endpoint paths
and asymmetric/nonfinite pair matrices cannot authorize reuse. Unsafe attempts
clear old stamps. It leaves inherited scoring arithmetic unchanged.
The [scoped dependency and terminal-tail argument](round2-refinement-reuse.md)
explains those restrictions and the exact reset sites.

Fresh v5 memo-off/on traces again match all **2,469 attempts, 171 accepted
alignments and 6,603 master rows**, retaining all **508** active hits on the
five-input subset. The latest source also passed **100/100** goldens,
**612/612** option/format comparisons, four real ASAN/UBSAN checks and 20 short
comparisons. One-column cases actually exercised the protective bypass; two
fractional-scoring cases exercised reuse. The actual matrix guard separately
passed 373 expectations per strict GCC and sanitizer build. On the corpus,
97 jobs entered the guard and 84 reused at least one attempt, totaling 3,632
hits; these counts do not measure saved time.

A read-only idle-service calibration eventually accepted its twentieth
two-minute window: median busy load 0.128 cores, p95 0.178, no busy scientific
process. Its prespecified rule selected a **prospective 0.20-core** ceiling,
with the scientific-process limit still 0.05 cores. Earlier measurements retain
their original flags. No external process was stopped or reprioritized.

The fresh v5 screen then completed:

| Guarded v5 screen, three paired rounds | Dikarya3 CPU seconds per pass | Memo CPU seconds per pass | CPU speedup | Flagged jobs / total |
|---|---:|---:|---:|---:|
| 12 development inputs | 57.727 | 55.797 | 1.035x | 3/72 |
| Three deliberately selected high-reuse holdout inputs | 25.417 | 22.090 | 1.151x | 0/18 |

All 90 outputs matched goldens. Each round favored reuse in both screens;
no input crossed the prespecified material-regression threshold. The high-reuse
set establishes a useful restricted improvement, not an unbiased holdout or
whole-corpus estimate. The development result retains its three flagged jobs;
`khx6` and `jqh7` have a separate three-round confirmation plan. Results are
`reject-memo-v5-idle-{development,high-reuse}-analysis`.

This evidence, the dependency argument and differential/sanitizer checks support
selecting the guarded code for **dikarya4**. The common macro is defined in
`mltaln.h` so worker and Falign agree; `-DMAFFT_REJECT_MEMO=0` remains an ablation.
Fresh tracked-source release/sanitizer builds and the final paired corpus,
profiles and scheduling campaign use new `dikarya4-r2-*` directories. The unused
zero-job dikarya3 final timing plans remain preserved and paused. Earlier v2
full-corpus output was preserved at nine successful jobs when superseded.
No speedup is inferred from correctness-run durations.

The first fresh dikarya4 packaging attempt retained the old launcher-template
version. MAFFT's own consistency check refused the first fixture before
producing alignment output. The failed build and logs remain in `dikarya4-*`.
The template was corrected, a build preflight and focused regression tests now
reject inconsistent diagnostic stamps, and fresh sources/builds use the
`dikarya4-r2-*` paths. The frozen corrected source is `9d963ee`; the numerical
kernels were unchanged by this packaging recovery.

A separate default-off residue-map cache prototype is preserved on branch
`experiment/residue-map-cache`. Complete row bytes, including NUL, establish
identity; per-call pins protect aliased IDs. Persistent requested storage is
capped at 16 MiB per worker, excluding temporary fallbacks and allocator
overhead. Review added release hooks outside DP allocation guards, including
the D path. The first checker-generation attempt failed before compilation
because its C signature parser did not accept inline comments. That parser was
corrected and passed seven focused Python fixtures. Source `f8d8fe6` is frozen
separately. While production load kept the timing gate closed, two explicitly
exclusive correctness windows paused only the three waiting research Python
processes, verified that none had launched a native job, and resumed them
afterward. No production process was signaled.

Six native variants then passed **10,448 exact matrix/bound comparisons and
150,646,080 matrix cells**, counting repeated strict/sanitizer backends. These
cover normal banded/direct operation, a 1,024-byte budget, zero budget,
allocation failures, repeated edits, aliased IDs, epoch wrap and actual pthread
isolation. ASAN/UBSAN and leak detection passed. A separate full-object sanitizer
probe exercised all **nine actual public NULL cleanup paths** before DP
workspace allocation; every path released the warmed cache. The preserved
commands and sources are in `resmap-cache-v3-check` and
`resmap-cache-v4-public-cleanup`. These results establish the tested internal and
cleanup behavior, not full-alignment equivalence or saved time. The prototype
has no full-corpus golden or performance result and is not included in dikarya4.

## Compiler research and rejected alternatives

Clang 15, GCC LTO alone and newly trained GCC PGO were rebuilt from the optimized
candidate2, rather than treating earlier PGO failures as conclusive. Clang forced
`MAFFT_STOCK_FMA=0` and all builds used `-ffp-contract=off` to retain the GCC
arithmetic class. PGO generation used atomic profile updates and trained only on
the 12 development inputs; its use build used `-fprofile-use` and
`-fprofile-correction`. No holdout was used for training.

| One 12-input compiler screen | CPU seconds | Wall seconds | CPU speedup vs GCC |
|---|---:|---:|---:|
| GCC 13.3, ordinary `-O3` | 58.10 | 58.804 | 1.000x |
| Clang 15.0.7 | 58.73 | 59.554 | 0.989x |
| GCC, `-flto=2` | 59.09 | 59.786 | 0.983x |
| GCC, fresh PGO | 57.54 | 58.348 | 1.010x |

All 48 outputs matched. Clang/LTO were slower overall; the accurate-direction
probe also regressed from 2.60 CPU seconds under GCC to 2.95/2.90 respectively.
PGO had a small measured gain, **not a regression**. It was not selected because
this was one screen on its training files, with no held-out confirmation and no
training on the later ordinary-cache implementation. A roughly 1% training-set
gain is insufficient evidence to adopt the extra build/profile dependency.
Ordinary GCC is the selected final-validation toolchain, not a claim that no
future PGO/LTO combination can improve it.

The initial build helper omitted expected empty packaging directories, so Clang's
completed compile/link was followed by a packaging failure. The helper was fixed
and the same Clang source/flags completed via recorded recovery commands. That
incident is not counted as a compiler failure; both build/recovery logs remain.

Other major decisions:

| Experiment | Evidence and decision |
|---|---|
| Eight independent DP matrices in AVX2 lanes | Equal-length common-query prototype improved 1.244x at 700 residues, but independent queries were 3.73x slower and ragged pairs about 14.15x slower. Reject general dispatch; retain restricted standalone research code. |
| Common-query groups from real FASTA shapes | 1.145–1.253x in three probes using a synthetic +600/-450 matrix. No production scoring API/dispatch; no production speedup claim. |
| Stack-only ordinary `scarr` allocation removal | Exact, but 58.22 versus 58.22 development CPU seconds; leave off. |
| Additional dynamic duplicate realignment cache | Production specificity setting did not execute the proposed calls; rejected by path inspection rather than implementing an unused optimization. |
| Prefix/suffix/substring DP reuse | Audit of 97 raw input hashes finds substantial exact prefix structure, but matching substrings do not prove matching incoming affine states, endpoint/tie handling or striped layout. No unjustified block reuse implemented; measured structure is detailed below. |
| More importance-band tuning | Prior 16/32-row bands and branchless bounds already regressed. Preserved the successful previous policy instead of repeating those failures. |

## Correctness and memory evidence

Internal counts include repeated backends and sanitizer executions; they are not
counts of independent fungal datasets. All comparisons below passed:

| Component | Focused evidence |
|---|---|
| Rolling score-only | 80,000 cases / 283.3 million cells against original-double public return bits; three long boundary cases add 201.2 million nominal cells. |
| Identity shortcut | 13,000 cases / 303.1 million nominal cells, including 1,625 deliberately eligible cases and sanitizer repeats. |
| Full traceback specialization | 75,000 cases / 272.0 million cells, 35,000 forced zero extension; every cell score, resolved offset and endpoint. Full tracking/golden checks are separate. |
| Direct scorers | 147,381 pair fixtures / 62.0 million columns, all three APIs against an independent buffered reference; actual retained original bodies additionally agreed on 442,143 public calls / 186.1 million scored columns. |
| Partial raw-row cache | 2,000 fixtures / 515,466 rows / 66.9 million cells; sanitizer repeat 500 fixtures / 16.3 million cells. |
| Ordinary cache + stack | 500 fixtures / 136,270 rows / 18.2 million cells against the extracted frozen original scorer; sanitizer repeat 200 fixtures / 6.9 million cells; stack-only repeat also exact. |
| Dense six-mers | 153,600 exact intersection checks plus overflow/shape/cap fallback tests, repeated under ASAN/UBSAN. |

Direct-score tests exhaust all four pair-column gap states through width eight,
then cover random lengths, common gaps within runs, opposing gap runs, arbitrary
nonzero byte alphabets, ambiguity/proteins, extreme integer penalties and dynamic
fractions/signed zeros. Profile tests cover repeated/unique vectors, output-buffer
poisoning, boundary sentinels, matrix/generation guards and alphabets through 256.
The unchanged `scarr_fill` arithmetic is isolated in profile unit drivers; full
alignment comparisons remain the whole-program oracle. See the retained checker
sources and linked kernel report for exact scope and reproduction commands.

Each raw-row cache allows up to 8 MiB of row data per worker plus bounded metadata.
Separate ordinary and partial instances can retain up to 16 MiB per worker plus
metadata; temporary grouping allocations have their own bounds. These are not
whole-process memory limits. The score-only history reduction and bounded caches
change memory use in opposite directions on different paths.

The new GNU-time-based harness records one job's own peak RSS. In the ordinary
development pass, the maximum among the 12 jobs was 69,520 KiB for candidate2 and
cache+stack, and 69,644 KiB for stack-only. In the five-round FFT-NS-i probe, peaks
were 93,312 KiB opt8, 96,384 KiB dikarya2 and 95,360 KiB ordinary. These selected
probes are not final corpus-wide maxima. Those runs had zero major faults and
zero input blocks; output blocks matched across builds. Earlier cumulative child-
RSS high-water fields are not reinterpreted as individual-job peaks.

## Reproduction and final validation

The final selected policy is GCC 13.3 ordinary `-O3`, with rolling score-only,
identity, zero-extension specialization, direct scoring, partial raw-row reuse,
dense six-mers, ordinary raw-row reuse and guarded terminal rejected-branch reuse
enabled. The standalone ordinary stack buffer remains off. Commit `9d963ee`
contains these defaults, consistent diagnostic stamps for
**7.526-opt8-dikarya4**, and the build preflight. The earlier source at `91146f2`
remains independently preserved as dikarya3. `ALIGNMENT_VERSION` remains
`7.526-opt8` so version labels inside alignment formats do not themselves alter
the byte oracle. Archive the selected tracked commit as described in the
[build instructions](../harness/round2/README.md), then build privately:

```sh
python3 harness/round2/build.py --source /absolute/final/source \
  --output /absolute/new/private/build --jobs 1 --cc gcc \
  --flags '-O3 -march=native -ffp-contract=off -g'

python3 harness/round2/paired.py \
  --manifest /absolute/bench/production-corpus.json --data /absolute/bench/data \
  --golden /absolute/bench/golden/production-t1 --partition all --rounds 1 --cpu 0 \
  --build dikarya2=/absolute/frozen/dikarya2/bin/mafft \
  --build dikarya4=/absolute/new/private/build/install/bin/mafft \
  --output /absolute/new/paired-results
```

Use new output directories and wait for a quiet machine before timing. The
paired runner stores commands, input/output/golden hashes, installed-file hashes,
versions, strategy, per-job CPU/wall/RSS and process-group execution results. Its
resume checks reject changed plans or prior failed/changed records. Build metadata
preserves source hashes, compiler flags, commands and installed-binary hashes.
No production installation is required. Quiet checks are on by default; CPU
contention flags remain attached to every timed pair and flagged results are
retained. An unrelated production phylogenetic job delayed the final measurements;
its processes were neither stopped nor changed. Sanitizer and scientific tree
runtimes collected during that load are excluded from isolated speedup claims.

The preserved dikarya3 `release-preservation-audit.json` verifies all **90** build-relevant tracked source
files against commit `91146f2`, the current worktree, the archived source and the
build's source copy as recorded at that checkpoint. It also verifies the private installed-file manifest, all
100 original input hashes, all 100 frozen goldens, the production opt8 binary
manifest and the independently copied opt8 manifest. The reproducing tool is
`harness/round2/release_audit.py`. A disassembly scan of all 32 release ELF helpers
found zero fused multiply-add instructions (`final-machine-code.json`).

The earlier tracked dikarya3 release passed **612** complete option/format comparisons on
36 generated fixtures, **six** long-boundary comparisons and **35** placement
comparisons against frozen opt8. Both programs exited successfully in every
comparison. An ASAN/UBSAN build additionally passed **36** fixture comparisons
and two real production inputs (`1msv`, `icv1`) against the verified release or
golden as recorded. Another 144 thread-zero and 72 noniterative two-thread
comparisons passed. These last checks do not establish equivalence of threaded
iterative refinement. Leak detection was disabled; address and undefined-behavior
failures were configured to halt execution.

The corrected tracked dikarya4 source (`9d963ee`) now independently passes the
same **612** option/format comparisons, **six** long boundaries and **35**
placements against frozen opt8, plus **36** sanitizer fixtures and **two** real
sanitizer/golden checks. Its 76 harness tests passed; the scheduling aggregator
subsequently passed nine separate mocked tests. These are verification counts,
not independent datasets. The new private build is
`dikarya4-r2-build/install/bin/mafft`, with a separate `dikarya4-r2-asan` build.

An additional **92** F runs (`--thread 2 --threadit 1`, twice on all 46 originally
two-threaded inputs) matched every one-thread golden. The new reuse guard was
active in 90 runs, totaling 3,172 hits and zero unsafe rejections in this finite
production check; it was not merely compiled and bypassed. These descriptive
correctness runs are separate from the biological experiment and from timing.
`dikarya4-r2-refine-one-analysis/summary.json` retains all alignment comparisons.

`dikarya4-r2-preservation-audit.json` verifies the 90 tracked core files, both
source copies, installed manifest, all 100 original inputs and goldens, and both
original opt8 manifests. `dikarya4-r2-machine-code.json` reports zero FMA
instructions across all 32 ELF helpers. The serialized native measurement
campaign now waits automatically for the user-requested quiet window. Its
three-build corpus comparison, confirmations, profiles, isolated jobs, sustained
queues and observed-start projections have new versioned output paths.

| Required final deliverable | Status in this report |
|---|---|
| Final tracked source/defaults/version and binary provenance | Dikarya4 source `9d963ee`, fresh builds and preservation/instruction audits passed; earlier dikarya3 preserved at `91146f2` |
| Fresh 100-file opt8/dikarya2/dikarya4 paired comparison, all partitions | Pending |
| Final per-file speedups/regressions and individual peak RSS | Pending |
| Final selected-build profiles | Pending |
| Integrated alternate scoring/ambiguity/length/placement/output-format and sanitizer sweep | Passed for fresh dikarya4, with earlier dikarya3 and guarded v5 evidence retained |
| Two-vCPU latency/throughput/reproducibility scheduling comparison | Pending; separate threading study |
| Production binary, thread and concurrency recommendation | Pending; no production settings changed |

Private evidence directories include `candidate1-dev`, `candidate2-dev`,
`rowcache-stage-benchmark`, `fft-components`, `ordinary-stage`,
`ordinary-development`, `fftnsi-repeat`, `compiler-screen`,
`ordinary-rowcache-prototype` and `score-kernel-check-*` under `round2/`.
Every measured snapshot remains separate from subsequent source changes.

An additional data audit (`reuse-data-audit`, reproduced by
`harness/round2/reuse_audit.py`) uses verified golden strand orientations,
uppercase/ungapped strings and one vote per original raw input hash. After
deduplicating strings within each input, 7,266,647 characters remain. Adjacent
lexicographically sorted sequences share **1,749,674 prefix characters (24.08%)**;
the equivalent reverse-string trie shares **1,730,336 suffix characters (23.81%)**.
These are trie-structure counts, not percentages of executed DP work or time.
Across 942,936 distinct unordered sequence pairs, 5,395 are proper exact
containments; pure A/C/G/T containments cover only **0.517% of nominal pair cells**.
None has multiple literal occurrences in the longer string. Containment alone
therefore looks narrow in this corpus, while common-target prefix batches deserve
more investigation.

One precise future experiment is to sort variable queries for a fixed striped
target and restore complete DP frontiers at shared prefixes. Full traceback would
also need the matching prefix history and global endpoint/tie state. Current
pair order, variable striped widths, guard/storage classes and checkpoint-copy
costs prevent treating the character fraction as an available speedup. Suffix
counts do not authorize reversing recurrences or tie-breaking. The audit enables
no shortcut and supplies no runtime estimate.

The strongest remaining research candidates must be prioritized with the final
profile: further exact full-traceback work, ordered importance-contribution
locality/reuse, and repeated second-profile-column arithmetic on cache misses.
Common-query SIMD batching needs actual production matrices, per-lane fallbacks
and an integrated API before deployment. PGO could be retrained on the final
implementation and retested with independent data if its marginal gain matters.
No unmeasured time-saving estimate is assigned to these ideas. Threading policy
and phylogenetic reliability require their own measured evidence; single-thread
byte identity establishes reproducibility, not superior biological accuracy.

An additional source-based investigation derives a
[candidate exact local-DP band certificate](round2-band-certificate.md). Under
integral/range guards, nonpositive gaps and a verified feasible positive raw
score bound, a pair-budget inequality strictly excludes all winning and tied
histories outside a computable band. Positive local restarts require an explicit
virtual-pair argument. Two static reviews found no counterexample under the
listed conditions. This remains an analytical candidate: obtaining the bound
cheaply, mapping the band to striped SIMD and preserving sentinel/gap-carry
semantics all need implementation and differential tests. No kernel or speedup
is claimed from it.
