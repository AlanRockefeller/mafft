# Round 4: audit fixes and verification infrastructure

Branch `audit-fixes`, based on `perf/ordered-refinement` (`54e638c`, the dikarya5 tree with
the CodeRabbit review fixes). Two audits of 2026-10-22 found no alignment-changing bug; they
found verification tooling that could hide failures, no public fixtures or CI, and a few
output, stderr and hygiene issues. This report records what was changed and how each change
was checked. All measurements are on the Dikarya VM (2 vCPU Broadwell, E5-2690 v4, gcc 13.3)
at nice 19 alongside production; times are descriptive unless stated otherwise.

## 1. Harness: resumes that cannot hide failures

### What was wrong

| Script | Defect |
| --- | --- |
| `round3/ordered_checks.py` | skipped any case whose output file existed; wrote MAFFT output straight to the final path (a killed run left a truncated file that the next run skipped); restarted the failure count at 0. The CodeRabbit fix (`875d789`) wrote a `.part` file and recounted recorded runs, but still trusted any record whose output existed, keyed cases by name only (a rebuilt binary reused old results) and had no timeout. |
| `round3/stock_replay.py` | skipped existing outputs; renamed `.tmp` into place after a nonzero exit; never failed. |
| `round3/latency.py` | no timeout, no failure exit, duplicate records on restart. |
| `round2/alignments.py` | compared only the launcher script's hash on resume; a rebuilt `libexec/mafft` program could extend an old campaign. |
| `round2/queue_trees.py` | trusted recorded trees without rechecking their hashes; queued alignment rows marked `completed: false`. |
| `broadwell/{bench,regress,placements,stages}.py` | `subprocess.run(timeout=)` kills only the direct child (`nice`, then the `mafft` shell script), leaving `disttbfast`/`tbfast`/`dvtditr` running; `regress.py` would have counted two equal timeouts as a matching rejection. |

`round2/paired.py` already refused to resume over failed records after the CodeRabbit fix;
`run_plan.py`, `isolated_batch.py`, `throughput.py` and `confirmation_plan.py` either never
resume over outputs or already validate records and were left as they were.

### What replaced it

`harness/exact/caserun.py` is shared by the round-3 scripts and the new verification tools:

- **Completion manifest** (`OUT/manifest.jsonl`, fsynced per record), one record per attempt,
  keyed by input path and SHA-256, the hash of the launcher and every program under
  `libexec/mafft`, the options and the `MAFFT_*`/locale environment.
- **Atomic output**: stdout and stderr go to temporary files renamed into place only after
  exit status 0; anything else is kept as `*.failed`.
- **Timeouts**: every process gets its own session; on timeout the group gets SIGTERM, then
  SIGKILL after a grace period, and the runner waits until no group member is still running.
  Members left behind by a leader that exited are killed and reported.
- **Failure rules**: a timeout, nonzero exit, launch error, leaked process, empty output or
  hash mismatch is a failure. On resume a cached pass counts only if its output still exists
  with the recorded hash and the expected hash is unchanged; anything else counts as a
  failure. Failures are sticky: a case that failed in any attempt stays failed, also after
  `--retry` reruns it. A record cut off mid-write is moved aside and that case runs again; a
  corrupt record in the middle stops the run.
- **Per-category pass/fail counts** in `summary.json`.

### Tests

`harness/exact/test_caserun.py` (18 tests) drives the real scripts against a fake MAFFT
prefix whose behaviour is set through a mode file: a recorded failure on resume and after
`--retry`; a truncated output left by a killed run (not skipped); an output truncated or
deleted after a recorded pass (failure); a rebuilt `libexec` program with an unchanged
launcher (rerun, not reused); a changed golden; a nonzero exit with complete output; empty
output; a sleeping fake MAFFT (`--timeout 1`; the whole group is killed); a descendant that
ignores SIGTERM; a manifest line cut off mid-write; a corrupt line. `stock_replay.py` and
`latency.py` have their own cases. New tests in `round2/test_execution.py` and
`round2/test_queue.py` cover the rebuilt-binary and changed-tree refusals. All round-2 tests
still pass. One flake was found and fixed while doing this: a SIGKILLed child can be a zombie
for a moment, so the tests treat a zombie as dead and the runner waits for the group to be
gone; `test_caserun.py` then passed six times in a row under four busy-loop CPU hogs.

## 2. Public fixtures and one verification command

`test/exact/` (412 KB) holds seed-fixed simulated families from the appendix generator
(`harness/exact/simseq.py`, seeds in `recipes.txt`), literal edge cases and `--add`/`--seed`
inputs; see [its README](../test/exact/README.md). There are deliberately no expected outputs:
output legitimately differs between compilers and rounding classes, so the reference is stock
7.526 built in the same job with the same flags.

`harness/verify-against-stock.sh` builds stock `0a2319b`, the working tree and a
`-DMAFFT_ORDERED_TEST` build of it into a work directory (never a system prefix) and runs
`harness/exact/matrix.py`. A case passes if stdout bytes and exit status match, or both builds
reject the input with the same status. The fast mode covers every strategy of the regression
matrix on four small inputs with `--thread 1`, the default thread setting (no flag), every
option set, ordered `--thread 2/3/4` DNA L/G/E-INS-i against stock `--thread 1`, the jitter
build and forced one-worker fallbacks: 160 cases when first written, 163 after the `rounding`
group was added (section 5). `--full` (675 cases) widens inputs and repeats;
`--corpus` adds a private corpus; `--allow-clustal-version` accepts CLUSTAL output whose only
difference is the version on line 1 (off by default).

## 3. CI

`.github/workflows/exact.yml`: harness unit tests, then `verify-against-stock.sh` (fast) for
gcc scalar `-O3`; gcc AVX2 (`-march=haswell -ffp-contract=off`, so AVX-512 runners still
take the AVX2 paths); the clang FMA class (`x86-64-v3`, candidate `-DMAFFT_STOCK_FMA=1`);
ASan+UBSan (`STRIP=true`; sanitizer errors set exit codes 86/87 so they are compared with
stock, leak checks off because stock MAFFT does not free everything at exit); a threaded job
(full-mode ordered, jitter and forced-fallback L-INS-i); and a non-blocking arm64 NEON job on
`ubuntu-24.04-arm`. Stock builds are cached by compiler, version and flags.

It first ran on GitHub, with the owner's approval, when `audit-fixes` was pushed (run
40107264417): every job passed except ASan+UBSan, which stopped in its cache step because
cache keys may not contain commas (`-fsanitize=address,undefined` was part of the key). With
the flags hashed into the key, run 40108614898 passed all seven jobs: harness tests; gcc scalar,
gcc AVX2, clang FMA class, ASan+UBSan and gcc arm64 NEON 163/163 each; the threaded job
119/119. arm64 was then made blocking. A review of the PR (#3) found that the cached stock
installation kept the launcher of its original work directory, which a later job would not
have; stock is now built directly into its cache prefix.

## 4. Concurrency audit of ordered refinement (dikarya5)

Builds: stock `0a2319b` and the branch at `54e638c` with `-DMAFFT_ORDERED_TEST`, gcc 13.3.
No core/ change was needed; everything below was measured on the unchanged dikarya5 code.

### ThreadSanitizer

`harness/exact/tsan-run.sh` (gcc `-O1 -g -fsanitize=thread`, run under `setarch -R`
because gcc 13's TSan aborts on this kernel's mmap randomization) ran L-INS-i, G-INS-i,
E-INS-i and FFT-NS-i at `--thread 2` and `--thread 4` on `dna12small`, `dna24div`,
`dna20alldup`, `dna30near` and `dna40`: 40 runs for stock, 40 for the branch, plus 24 branch
runs with `MAFFT_ORDERED_TEST_UNSAFE=5` or `=1` (each one-worker rerun was taken: 18 of 18
ordered runs). `harness/exact/tsan_triage.py` groups reports by kind and the innermost
function, file and access type of both accesses; a second pass by exact line (mapped
between the two trees) checked that grouping did not hide a new race.

| Race (branch line ↔ stock line) | Reports, branch | Reports, stock | Verdict |
| --- | ---: | ---: | --- |
| `chudan` poll: `*chudanpt != chudanref` in `partA__align`/`A__align` (partSalignmm.c:2044, Salignmm.c:2083 ↔ 1123, 1815) vs `*subgenerationpt += 1` under the mutex (tditeration.c:1466 ↔ 1170) | 46 | 34 | upstream; see below |
| `*subgenerationpt != subgenerationatfirst` after the alignment (tditeration.c:1322 ↔ 1052) vs the same write | 7 | 0 | upstream; same pattern, stock has the same unlocked read at 1052 and TSan happened not to catch it |
| progress `fprintf` reading `*ndonept` (1351, 1513, 1523 ↔ 1061, 1190, 1200) vs `(*ndonept)++` under the mutex (1612 ↔ 1238) | 24 | 53 | upstream; affects only the `\r` progress text on stderr |
| `treebasethread` in disttbfast.c and tbfast.c (guide-tree threads) | 15 | 15 | upstream; both `treebasethread` functions are identical to stock |
| identity check `strcmp( localcopy[s1], mastercopy[s1] )` (1349 ↔ 1058) vs `strcpy` into `mastercopy` under the mutex | 18 | 44 | upstream; reported only in FFT-NS-i runs (not ordered, nondeterministic in stock too). In ordered runs the check runs while holding the commit turn, and only the turn holder writes `mastercopy`. |
| New on the branch | 0 | | |

Why the upstream polling races do not reach ordered output: in ordered mode a worker
commits only inside `ordered_turn()`, which takes the mutex, waits until `ndone == mypos`
and returns 1 if `subgeneration` changed since the attempt started; the worker then
recomputes from the current `mastercopy` (`yarinaoshi`), exactly as when the unlocked poll
sees the change early. The poll can therefore only make a doomed attempt stop sooner; the
attempt that commits was always computed against the mastercopy of its turn.

All 48 ordered L/G/E-INS-i outputs of the TSan builds (`--thread 2`, `--thread 4`, and the
forced-fallback runs) are byte-identical to stock `--thread 1` built with gcc `-O3
-march=native -ffp-contract=off`.

### The objects named in the audit, by inspection

| Object | Argument |
| --- | --- |
| `ordered_turn` / `commit_turn` | the wait loop rechecks `ndone == mypos` or `orderedabort` under the mutex; `ndone` changes and the broadcast happen under the same mutex, so a wake-up cannot be lost. |
| `ordered_abort` | called only by the turn holder before `ndone++`; sets `orderedabort`, `collecting = -1`, broadcasts `commit_turn` and `collection_end`, signals `collection_start`. Every wait (`commit_turn`; the coordinator's `ndone < nbranch && !orderedabort`; the workers' `collecting > 0`) rechecks the abort flag, so no thread sleeps through it. Because only the turn holder aborts, an abort cannot fall between two generations, and the coordinator's reset of `collecting` from 1 to 0 cannot overwrite −1. |
| one-worker rerun | restores `bseq` from `orderedinput`, runs `nwa = 2` with fresh per-thread (TLS, calloc'd) scratch, as stock `--thread 1` does with its single fresh worker; `finish`, `ntry`, gain and `tscore` lists are reset and stale `tscorehistory_detail` entries are overwritten before they are read. |
| `omemo` | `stamp`, `score`, `epoch`, `checked_epoch`, `verified` are read and written only under `targ->mutex`; writes happen while holding the commit turn (tditeration.c:1554–1565, 1482–1490). |
| `gainlist` / `tscorelist` | ordered mode uses slot 1 for every worker (`gainslot`), written only by the turn holder under the mutex, which is the slot stock `--thread 1` uses. |
| `igs_cache` | allocated per `athread` call (tditeration.c:649), never shared. |
| paircache `dup_*` | table, ids and counts are built before the all-pairs threads start; each entry is published once under `dup_mutex` after it is filled and never changed, and readers fetch the pointer under the same mutex. |
| TLS row caches | `TLS` is `__thread` (mltaln.h:373, dp.h:2). |

### Stress under the jitter build

`harness/exact/stress.py` against stock `--thread 1` (gcc AVX2 builds; `base-test` =
dikarya5 with `-DMAFFT_ORDERED_TEST`), 2-vCPU VM, nice 19, timeout 1800 s per process:

| Category | Pass | Fail |
| --- | ---: | ---: |
| repeat L-INS-i `--thread 2` (5 inputs × 50) | 250 | 0 |
| repeat L-INS-i `--thread 3` | 250 | 0 |
| repeat L-INS-i `--thread 4` | 250 | 0 |
| oversubscribed `--thread 8` (5 × 10) | 50 | 0 |
| forced fallback `--thread 2` (69 positions) | 69 | 0 |
| forced fallback `--thread 4` | 69 | 0 |
| switch off (`MAFFT_ORDERED_REFINE=0/off/FALSE/no`) | 5 | 15 |

Inputs: `dna12small`, `dna24div` (high divergence), `dna20alldup` (all duplicates),
`dna30near` (near-identical), `dna40outlier`. Forced positions: 1, 2, the last and first
commits around the first two generation boundaries, six random positions and 10⁶ (never
reached); 64 runs took the one-worker rerun and the five 10⁶ runs finished as ordered
refinement, as intended. No run timed out or leaked a process. The 15 switch-off failures
are item 2.7: only `0` turns ordered refinement off; `off`, `FALSE` and `no` leave it on.

## 5. Regression matrix before any core/ change

Fast matrix (`harness/exact/matrix.py`), branch at `54e638c` against stock built with the same
compiler and flags. "Fast" is 163 cases with a `-DMAFFT_ORDERED_TEST` build, 106 without
(no jitter or forced-fallback cases).

| Build class | Flags | Result |
| --- | --- | --- |
| gcc 13.3 AVX2 | `-O3 -march=native -ffp-contract=off` (Broadwell) | 161/163 |
| gcc 13.3 AVX2, via `verify-against-stock.sh` from a clean work directory | same | 158/160 (before the rounding group was added) |
| gcc 13.3 scalar | `-O3` | 104/106 |
| gcc 13.3 ASan+UBSan | `-O1 -g -fsanitize=address,undefined -march=native -ffp-contract=off`, `STRIP=true` | 158/160 (before the rounding group) |
| clang 18.1.8 FMA class | `-O3 -march=x86-64-v3`, candidate `-DMAFFT_STOCK_FMA=1` | 161/163 |
| clang 18.1.8, candidate **without** `-DMAFFT_STOCK_FMA=1` | `-O3 -march=x86-64-v3` | rounding group 1/3: FFT-NS-2 and FFT-NS-i on `dna40` differ from stock |

The only failures in every class are the two `--clustalout` cases, whose sole difference is
line 1 (`CLUSTAL format alignment by MAFFT L-INS-i (v7.526-opt8)` against `(v7.526)`), item 2.1.
Matching rejections (both builds exit 1 with the same status): FFT-NS-i and NW-NS-i on the
one-sequence input (stock: "At least 2 sequences"), and under ASan only, the same two on
`tiny`, where stock and branch both hit the upstream `searchAnchors` overread (item 2.10).

The last row is item 2.8's footgun, reproduced: clang contracts `a*b+c` into FMA on an FMA
target, so stock built that way is in the FMA rounding class, and a candidate built without
the define takes the non-FMA paths. The 12-sequence fast inputs did not show it; the
`rounding` group (FFT-NS-2, FFT-NS-i and L-INS-i on `dna40`) was added to the fast matrix
for that reason.

Not verified here: arm64 NEON (the CI job exists but has not run), Metal, CUDA, AVX-512
(this CPU has none), and Windows/macOS builds.

## 6. Code fixes (Phase 2)

One commit per item; each commit message says how it was verified. Decisions by the owner:
the CLUSTAL header prints stock's version (option a), and the release name is `opt9`.

| Item | Commit | Change | Verified by |
| --- | --- | --- | --- |
| 2.1 CLUSTAL header | `2fb4604` | `ALIGNMENT_VERSION` is `"7.526"`; `--clustalout` is byte-identical to stock. Dikarya never uses `--clustalout` (0 of 13,441 pipeline logs). | `option clustalout` cases 2/2 (were 0/2) |
| 2.2 `REJECT_MEMO` stderr line | `47e1d06` | printed only with `MAFFT_REJECT_MEMO_STATS` (not 0/off/false/no) or in `MAFFT_REJECT_MEMO_TRACE` builds, whose checker reads it | each value at `--thread 1`; stdout equals stock `--thread 1` |
| 2.3 EUC-JP comment | `b4e6440` | `Lalign11.c` line restored from stock; no other tracked file has U+FFFD | byte comparison; identical `Lalign11.o` disassembly |
| 2.4 unchecked allocations | `227ef8b` | every allocation added over stock checked; exact fallback where the function has one, else `ErrorExit` | fast matrix 163/163; `harness/exact/force_fallbacks.py`: 109/109 on three builds with each fallback forced on every call, every site hit |
| 2.5 GPU precompute memory | `800f1ee` | skipped (all pairs on the CPU) when its host memory would exceed 256 MB | compiles with `-DMAFFT_CUDA`; GPU paths not run (no Apple or CUDA hardware) |
| 2.6 dead AVX-512 block | `654a632` | unreachable `#elif defined(MAFFT_AVX512)` scan deleted; its exactness argument moved to the AVX2 scan | `objcheck.sh`: 0/65 objects differ (gcc native, icelake-server, x86-64; clang x86-64-v4) |
| 2.7 `MAFFT_ORDERED_REFINE` values | `97480fe` | 0, off, false, no in any case | each value; stress switch-off 20/20 (was 5/20) |
| 2.8 clang FMA footgun | `c7f129a` | `#error` for clang on x86-64 with FMA unless `-DMAFFT_STOCK_FMA` is given | both offered choices 163/163 against stock clang built the same way; no error for arm64 clang (syntax check), gcc, non-FMA x86 |
| 2.9 version | `c005835` | `7.526-opt9`, launcher `v7.526-opt9 (2026/Oct/24)` | `check_version_stamps`; round-2 tests |
| 2.10 upstream overread | `4a29e8e` | report only: `harness/upstream/searchanchors-overread.md` (not sent) | reproducer gives the quoted ASan trace on stock |

The fallback paths of item 2.4 were tested by forcing them, not by exhausting memory: in a
scratch copy, `force_fallbacks.py` makes each new allocation check always true and prints a
marker, and the forced builds were run through the matrix against stock. Hits per site
(markers in the 109 runs' stderr): `cprf` 7929, `dz` 11520, `fillimp` 6732 (with
`-DMAFFT_FILLIMP_DIRECT_BYTES=0`; on Broadwell the banded walk otherwise starts at 16 MB),
`igscache` 7173, `mcbuild` 18642, AVX2 `Lfill_int` 4725 and `igsx` 9705 (with
`-DMAFFT_AVX2_STRIPED=0`, since the striped fill normally runs first), and on a scalar build
the portable `Lfill_int` 4732 and `nxt` 18639. The Apple Accelerate distance path and the Metal
and CUDA sources were neither compiled nor run here; the arm64 NEON `kx` check passed only a
clang syntax check for an aarch64 target (borrowed x86 libc headers), not a build.

## 7. Regression matrix after Phase 2

Tree `4a29e8e` (all Phase 2 items), each run through `harness/verify-against-stock.sh` from a
clean work directory: stock `0a2319b`, the candidate and its `-DMAFFT_ORDERED_TEST` build,
all with the same compiler and flags (gcc 13.3), 2-vCPU VM at nice 19.

| Run | Flags | Result |
| --- | --- | --- |
| full matrix | `-O3 -march=native -ffp-contract=off` (AVX2) | 675/675 (strategies 204, options 102, default threads 12, ordered `--thread 2/3/4` 63, jitter 189, forced fallback 105), 1 h 57 min |
| fast matrix, ASan+UBSan | `-O1 -g -fno-omit-frame-pointer -march=native -ffp-contract=off -fsanitize=address,undefined`, `STRIP=true` | 163/163 |
| fast matrix, scalar | `-O3` | 163/163 |
| stress (`stress.py`, 10 repeats) | AVX2 jitter build of the full run | 318/318: repeats at `--thread 2/3/4` 150, `--thread 8` 10, forced fallbacks 138 (128 reruns taken), switch off 20 |

Matching rejections in the ASan run are those of section 5 (FFT-NS-i and NW-NS-i on `one` and,
under ASan only, on `tiny`). The clang FMA class and its `-ffp-contract=off` alternative were
run at item 2.8 (163/163 each). arm64, Metal and CUDA remain unverified here.

## 8. Phase 4: speedups

Branch `opt9-speedups`, on top of the Phase 2 and 3 commits. Two speedups are implemented:
the AVX2 importance gather (item 1) and an exact banded all-pairs fill (item 3). Item 5 was
measured and is waiting for a decision; the other items have design notes below.

### Production profile

The brief's ranking came from Audit A's simulated 80 × 650 bp input, where refinement was
about 90% of L-INS-i CPU. On the 12 development inputs of the production corpus (production
flags, `--thread 1`, build `312056a`, `perf record -e cpu-clock`, 52,470 samples) the shares
are different:

| Stage | Share of CPU |
| --- | ---: |
| all-pairs local DP: `Lfill_kb` 29.8%, `Lscore_kb` 4.0%, `L__align11` 2.0%, `lfill_prepare` 0.6% | 36.4% |
| refinement DP, `partA__align` | 11.8% |
| importance fill: `fillimp_track` 8.2%, `fillimp_banded` 5.1% | 13.2% |
| progressive alignment, `A__align` | 7.5% |
| `alignableReagion` | 3.7% |
| `match_calc` | 3.3% |
| refinement bookkeeping: `cpmx_calc_new` 2.0%, `commongappick_record` 1.9%, `gapruns` 1.6%, `makeresmap` 1.0%, `gapcountf` 0.3% | 6.8% |
| importance gather, `part_imp_match_out_vead_gapmap` | 1.5% |
| an unresolved libc address called from the refinement workers (probably the clearing `memset`) | 1.4% |
| `igs_pairscores_x` | 0.7% |

By program: `dvtditr` 50.7%, `tbfast` 43.8%, `makedirectionlist` 4.3%. All-pairs local DP,
not refinement, is the largest single target on real ITS data.

### Item 1: AVX2 importance gather (`f52e4fe`)

The given branch in `part_imp_match_out_vead_gapmap`: a vector load where `gapmap2` maps four
consecutive `j` to four consecutive columns, else `_mm256_i32gather_pd`. Every element still
gets one add of the same two values, so `imp[]` is bit-identical.

| Inputs | Before | After | Ratio |
| --- | ---: | ---: | ---: |
| `dna80dup`, L-INS-i `--thread 1`, 5 rounds | 26.62 s | 26.13 s | 1.019× |
| 12 development inputs, production flags, 3 rounds | 55.10 s | 55.45 s | 0.994× |

As the audit estimated on the refinement-heavy fixture, but no gain in production, where the
function is 1.5% of CPU. It is kept because it is exact and costs nothing measurable. Fast
matrix 163/163.

### Item 3: exact banded all-pairs fill

**Bound first.** `paper/round2-band-certificate.md` shows that, with a feasible raw score
`L > max(T, 0)` (T the end threshold `ithr`), every cell of the optimal history lies within
`r = N − ceil(L/M)` diagonals of the band `[min(0,D) − r, max(0,D) + r]`, where M is the
largest substitution score among the letters used, N the shorter length and D the length
difference; any superset of that band is also exact. An instrumented build measured, over
all 607,463 all-pairs pairs of the 100 production inputs, the fraction of cells outside the
band (0 bound violations):

| Lower bound L | Cells skipped, all | Cells skipped, holdout |
| --- | ---: | ---: |
| the exact score (the limit) | 65.4% | 66% |
| a ±32-diagonal band's maximum | 24.7% | 26% |
| iterative: that band's maximum, then the certificate's band | 48% | 47% |

Since all-pairs was 36% of CPU, skipping about half its cells predicted roughly 10% less CPU
end to end if time scaled with cells.

**Implementation** (`core/Lalign11.c`, AVX2 builds without AVX-512, `MAFFT_LBAND`):

- `Lband_try` runs before the full striped fill. It needs gap penalties ≤ 0, both lengths at
  least 4·w (w = 32), M > 0 and `ithr ≤ M`. Pass 1 computes only the score over
  `[min(0,D) − w, max(0,D) + w]`; its maximum is the score of a real history, so it is a valid
  L. If L ≤ max(T, 0), or the certificate band would cover more than 0.6 of the matrix, the
  pair declines and takes the unchanged full fill. Otherwise the certificate band is filled
  with traceback.
- The band kernel is striped in diagonal coordinates (slot d is column `i − hi + d`): the
  diagonal predecessor is the same slot, the vertical state moves one slot per row, and the
  horizontal gap term is a prefix maximum built from lane-local maxima and a carry from the
  previous row. Scores come from `permutevar8x32` lookups on pre-striped letter codes; cells
  outside the matrix score `INT_MIN/2`, so they cannot win without masks. Stored cells hold
  `value·4 | kind`, and the traceback reads them through the same tie rules as `Lfill_kb`
  (`LKB_ISSTOP`, `lband_ijv`). Selects are xor/and/xor, because `vpblendvb` costs two port-5
  micro-ops on Broadwell.
- Each thread keeps a running estimate of the cells saved (pass 1 costs 0.8 of a full-fill
  cell, the traceback band 1.3). Once the estimate is negative after 64 attempts, the thread
  tries the band only on every 32nd pair. This changes only which exact fill runs.
- `MAFFT_NOLBAND` turns the band off. `MAFFT_LBAND_CHECK` aligns every pair without and
  then with the band (the adaptive skip off, so every eligible pair is compared), compares
  score, offsets and both aligned strings, and exits 1 on any difference.

Builds where `MAFFT_LBAND` is 0 compile to the same objects as before
(`harness/objcheck.sh`: icelake-server, x86-64 baseline and clang x86-64-v4 have
0 differing objects; gcc native has 1, `Lalign11.o`). arm64, Metal and CUDA builds do not
contain the band; they were not built here.

**Correctness.**

- Check mode over all 100 production inputs (production flags, final source): no
  difference, every output equal to its golden (development 12, holdout 78, difficult 10).
  An earlier build, before the adaptive skip, also passed.
- `harness/exact/band_check.py`: 270 simulated DNA families (5 divergences × 3 indel rates ×
  3 lengths × 6 extras: duplicates, IUPAC codes and N runs, fragments, outliers, reverse
  complements with lower case), L-INS-i with `--maxiterate 0` or `--adjustdirection
  --maxiterate 2`, in check mode, each output also compared with stock: 270/270 (135 per
  strategy).
- The same grid under ASan+UBSan (UBSan halting): 270/270, no sanitizer report. An earlier
  run, in which UBSan
  only printed, reported one signed overflow (`LBAND_NEG * 4` in the row-0 store of slots that
  are never read); it is fixed, and `band_check.py` now fails any case with a sanitizer report
  on stderr.
- Regression matrices on the final source: section 9.

**Speed.** Alternating builds `bt-off` (`-DMAFFT_LBAND=0`) and band, both from `f52e4fe`
with the same flags (`-O3 -march=native -ffp-contract=off`), production options,
`--thread 1`, medians of `RUSAGE_CHILDREN` CPU. All outputs identical to the goldens.

| Partition | Inputs | Band off | Band on | CPU | Wall | Per-input median (min) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| development (tuning) | 12 | 55.2 s | 47.3 s | 1.167× | 1.171× | 1.175× (1.002×) |
| holdout | 78 | 430.8 s | 391.1 s | 1.102× | 1.101× | 1.113× (0.986×) |
| difficult | 10 | 110.0 s | 103.0 s | 1.068× | 1.064× | 1.037× (0.972×) |
| all | 100 | 596.0 s | 541.3 s | **1.101×** | **1.100×** | 1.111× (0.972×) |

Two rounds per build (the three-round rule was relaxed for this 4-hour run; the earlier
partition runs below had 2–3 rounds and agree). Measured end to end, 9.2% less CPU,
close to the 10% predicted from cells.

**Tuning and the holdout, disclosed.** w and the refill threshold were swept on the
development inputs only (every setting gave 1.16–1.17×). The first version, without the
adaptive skip, gave 1.096× on the holdout (430.4 → 392.6 s), but slowed 6 holdout inputs by
up to 5.8% (min 0.942× per input): divergent inputs whose pairs paid for pass 1 and then
declined. The adaptive skip was added because of that result, so **the holdout is not
untouched for the skip**. On those six inputs it moved the total from 0.986× to 1.022× (worst
0.950× → 0.995×). The difficult partition, untouched by the w and threshold sweep, gave
1.071× without the skip and 1.068× with it. Three inputs remain below 0.995× in the table:
`buh4` (difficult, 1042 sequences at low similarity) 0.972×, consistent across rounds and
with an earlier run; `26et` 0.986×; `eifm` 0.991×. `buh4` never enters the band: its
production options (`--adjustdirection --auto`) select FFT-NS-2, and a `perf record -e
cpu-clock` profile of opt9 on it (unstripped build of `37cc54c`, output equal to the
installed build's) has no samples in `L__align11`; the time is in `localcommonsextet_p2`
(makedirectionlist, 29.8%), `A__align` (22.0%) and `naivepairscorefast` (15.0%). The two
builds differ only in `Lalign11.o` (`MAFFT_LBAND` is used nowhere else), so its 2.8% comes
from the different binary, most likely code layout, not from band work; that is not
verified. `26et` and `eifm` are L-INS-i inputs and do run the band; for them a likely cause,
not verified, is per-pair fixed cost (`lfill_prepare`, computing M, buffer setup) that the
estimate does not count. They were left untuned so that the remaining inputs stay untouched.

**The refinement DP.** The certificate rests on the local DP's zero floor and a score that
only grows by substitution scores bounded by M. `partA__align` is a global profile DP with
fractional, position-dependent gap costs and importance terms, and no zero floor, so the
certificate does not carry over. An exact band there would need a different bound (an upper
bound on the score still reachable from each cell), not studied here.

### Final benchmark: stock, dikarya5 and opt9

Builds: stock `0a2319b` and opt9 `fd6820f`, both gcc 13.3 `-O3 -march=native
-ffp-contract=off`; dikarya5 as deployed (`/opt/mafft-releases/…-dikarya5-c81eb9794e14`).
All 100 production inputs, production options, `--thread 1`, 3 alternating rounds, nice 19
alongside production, 2026-10-26; medians of `RUSAGE_CHILDREN` CPU and of wall time per input,
summed. Every one of the 1,200 runs matched its golden (stock's output).

| Partition | Inputs | Stock CPU | dikarya5 CPU | opt9 CPU | Stock / opt9 CPU (wall) | dikarya5 / opt9 CPU | opt9 per input vs stock: median (min) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| development | 12 | 562.9 s | 55.4 s | 47.4 s | 11.87× (11.77×) | 1.169× | 11.84× (7.36×) |
| holdout | 78 | 3,933.8 s | 432.4 s | 393.2 s | 10.00× (9.94×) | 1.099× | 9.87× (2.81×) |
| difficult | 10 | 1,005.8 s | 110.7 s | 104.5 s | 9.63× (9.60×) | 1.060× | 8.43× (2.18×) |
| all | 100 | 5,502.6 s | 598.5 s | 545.1 s | **10.09× (10.03×)** | **1.098×** | 9.71× (2.18×) |

Round totals (CPU): stock 5,499.6 / 5,497.3 / 5,550.6 s; dikarya5 597.4 / 597.0 / 612.1 s;
opt9 545.2 / 542.6 / 558.3 s. Against dikarya5, 8 inputs were below 0.995×: `buh4` 0.944×
(the same input as in the band-off comparison, where the band does not run; dikarya5 lacks
the gather as well as the band, and was built separately), then `3on0` 0.984×, `zdku` 0.986×, `3wt3` 0.988× and `7b9f` 0.989×.
A fourth build, labelled opt8, was run from `/opt/mafft-7.526-opt8`; that path now reports
`v7.526-opt8-dikarya5` (the deployment repointed it), so it measured dikarya5 a second time
(598.4 s, within 0.02% of the release path) and is left out of the table. opt8 itself was
not re-measured; dikarya4's 1.45× over opt8 is the measurement in the
[CHANGELOG](../CHANGELOG.md)'s dikarya4 entry.

### Item 5: waiting in ordered `--thread 2` (measured, not implemented)

An instrumented build on the 11 development L-INS-i inputs: 94 of 7,572 attempts (1.2%) were
recomputed because an earlier position was accepted; the workers spent 5.6 s waiting for
their commit turn, summed over both threads; CPU `--thread 2`/`--thread 1` 1.053, latency
`--thread 1`/`--thread 2` 1.547. Waiting dominates, not recomputation. Removing all of it
would shorten `--thread 2` wall time by at most about 2.8 s of 39 s (about 7%) and save no
CPU. Holding the turn only during scoring would not help, because scoring
(`igs_pairscores_x`) is under 1% of CPU; the waiting comes from attempts that finish out of
order. Recovering it needs a look-ahead: a worker that finishes early starts the next
uncommitted position, and whoever holds the turn commits parked results in order. That is a
substantial change to the commit protocol, put to the maintainer before implementation.

### Design notes for the items not implemented

**Item 2, refinement efficiency.** Refinement DP and importance work are about 27% of
production CPU (`partA__align` 11.8%, importance fill 13.2%, gather 1.5%).

- (a) *Fuse the importance add into `partA_row`.* It removes one pass over each row, a share
  of the 1.5% gather plus the row traffic; expected ≤ 1–2%. The order `(match + imp) + wm`
  and `impclean`'s `+ 0.0` must stay.
- (b) *Generation-stamped importance rows.* The clearing is the probable owner of the
  unresolved 1.4% libc address; profile with libc symbols before building this.
- (c) *Incremental profiles and gap counts across rejected attempts.* The rebuild functions
  total about 6.8%. Within a generation each branch is attempted once, and an acceptance
  usually changes every row, so reuse would come only from groups whose rows a hash shows
  unchanged. The bound is 6.8%; the likely gain is much smaller.
- (d) *Row-mask reuse in `igs_pairscores_x`.* 0.7% of production CPU (17.5% in the audit's
  FFT-NS-i run). Only 2 of 100 production jobs are `--auto`; worth measuring only for
  FFT-NS-i workloads.
- (e) *int16 `ijp`.* Readers of `ijp` are spread over nine aligners (`Salignmm.c`,
  `partSalignmm.c`, `Dalignmm.c`, `Lalign11.c`, `Galign11.c`, `MSalignmm.c`, `SAalignmm.c`,
  `Lalignmm.c`, `MSalign11.c`); the all-pairs band does not use it. A narrower matrix saves
  memory traffic only, needs a fallback above 32,767 and touches every traceback; not
  recommended before (a)–(c).

**Item 4, common-query batching.** With the band, the all-pairs stage is smaller (the CPU
saved is about a quarter of it), and batching works on full fills, so the two compete for the
same pairs. Revisit only for inputs where the band declines most pairs (the divergent ones),
where batching could take the declined pairs.

**Item 6, ordered refinement for DNA FFT-NS-i.** The threaded FFT-NS-i attempt calls
`Falign` (`tditeration.c:1326`), which calls `A__align`, `A__align_variousdist`, `MSalignmm`
or `Aalign` per anchor block; acceptance comes from `intergroup_score`. The commit protocol
carries over unchanged; what must be shown is that an attempt is a function of the master
alignment and the branch only. The round-2 table covers `Falign_localhom` and
`partA__align`, not these. `Falign.c` has 95 `static TLS` objects and `Salignmm.c` 51; each
needs the same written-before-read argument, including dikarya1's uncleared `Falign` work
rows and the anchor search's buffers (`alignableReagion`, `crossscore`). `A__align` may share
`partA__align`'s one-column-block read of gap scratch, so the same guard and one-worker
fallback would be needed. Test plan: extend `refinement_attempt_reproducible` behind a
test switch and run the jitter and forced-fallback groups with FFT-NS-i. The `searchAnchors`
overread (item 2.10) is in `main`, once per run, and does not affect attempts.

**Item 7, runtime CPU dispatch.** The vector paths are chosen at compile time from
`__AVX2__`/`__AVX512*__` in `mafft_simd.h`, and intrinsics are confined to four files
(`Lalign11.c`, `partSalignmm.c`, `Salignmm.c`, `mltaln9.c`). `target_clones` does not fit,
because the code selects paths with `#if`, not inside functions. The practical design: compile
those four files twice more (AVX2, AVX-512) with renamed external symbols, and pick one set
at start-up with `cpuid`, either per entry point (an ifunc resolver) or with one table of
function pointers. Constraints: the clones must use the generic stock build's rounding class,
so `MAFFT_STOCK_FMA` is 0 and no clone may contract `a*b+c` (the Makefile's ISO `-std`
already stops gcc from contracting; clang contracts by default and needs
`-ffp-contract=off`, as in item 2.8); thread-local statics are
per clone, which is fine because a process uses one set. Verification: the full matrix with
each clone forced on this CPU, plus objcheck that the baseline objects are unchanged.

**Item 8, workload-aware kernel selection.** The band's adaptive skip is a first instance:
a run-time estimate that changes only which exact kernel runs. The same pattern fits the
other choices (striped vs marker fill, row caches, the direct fill-imp threshold), driven by
quantities already computed (lengths, letter counts, the k-mer distances of the guide tree),
with the current choice as default. Not benchmarked in this round.

**Item 9, `--unalignlevel`.** Profile of G-INS-i (`--globalpair --maxiterate 1000
--thread 1`) on a simulated 40 × 650 bp input (`simseq.py 2 40 650 dna rate=0.12
dup=0.15`), build `312056a`, at nice 19 alongside other jobs:

| | User CPU | Peak RSS | Top functions |
| --- | ---: | ---: | --- |
| without `--unalignlevel` | 9.0 s | 33 MB | `G__align11` 44.7%, `partA__align` 27.3%, gather 5.9%, `fillimp_track` 2.9% |
| `--unalignlevel 0.6` | 38.2 s | 46 MB | `partA__align_variousdist` 77.2%, `G__align11` 9.9%, `scarr_fill` 2.4%, `match_calc_add` 1.7% |

`partA__align_variousdist` has no vector code: `partA__align` has AVX2, AVX-512 and NEON row
kernels (`partA_row`), the variousdist version none. The recurrence is the same apart from
the match row, which is a weighted sum over several matrices (`match_calc_add`). Sending its
row loop through `partA_row`, with the match row computed as now, is the plausible exact
speedup. Its gain is bounded by that function's 77% share (at most 4.4× end to end on this
input) and depends on how much faster the vector row is than the scalar one there; not
measured.

The exclusion from ordered refinement (`specificityconsideration == 0.0`) is not required by
the commit protocol, which needs only that an attempt depend on the master alignment and the
branch. It is required by the proof as written, which covers `partA__align` and one scoring
matrix, not `partA__align_variousdist` and `intergroup_score_multimtx`. Empirical check: a
scratch build with only that condition removed, G-INS-i (`--globalpair --maxiterate 1000`)
on 7 inputs (`dna12small`, `dna20alldup`, `dna24div`, `dna30near`, `dna40`, `dna40outlier` and
the 40 × 650 input above). With `--unalignlevel 0.6` and `0.2` at `--thread 2` and `3`, all
28 runs used ordered refinement and were byte-identical to stock `--thread 1`.
`--allowshift` stayed outside the ordered scope through another condition (14 runs not
ordered; 8 differed from `--thread 1`, stock's usual completion-order behaviour). So
extending the scope to `--unalignlevel` looks feasible; it needs the dependency table
extended to `partA__align_variousdist` and the multi-matrix scoring, and the jitter and
forced-fallback groups run with it, before it can ship.

## 9. Regression matrix after Phase 4

The final source of the band (the working tree later committed), each run from a clean work
directory through `harness/verify-against-stock.sh`: stock `0a2319b`, the candidate and its
`-DMAFFT_ORDERED_TEST` build, the same compiler and flags (gcc 13.3), 2-vCPU VM at nice 19.

| Run | Flags | Result |
| --- | --- | --- |
| full matrix | `-O3 -march=native -ffp-contract=off` (AVX2, band on) | 675/675 |
| fast matrix, ASan+UBSan (UBSan halting) | `-O1 -g -fno-omit-frame-pointer -march=native -ffp-contract=off -fsanitize=address,undefined`, `STRIP=true` | 163/163 |
| `band_check.py` grid against stock | production flags | 270/270 |
| `band_check.py` grid, ASan+UBSan | as the fast matrix | 270/270 |
| check mode over the production corpus | production flags | 100/100 |
| objcheck, final source against `f52e4fe` | gcc icelake-server, gcc x86-64, clang x86-64-v4, gcc native | 0, 0, 0 and 1 (`Lalign11.o`) of 65 objects differ |

Matching rejections are those of section 5. The first ASan+UBSan run, on the source before
the fix, failed 39 L-INS-i cases (all of them on the `LBAND_NEG * 4` overflow of section 8),
which is how the overflow was found. The scalar build does not compile the band
(`MAFFT_LBAND` is 0), and objcheck shows its objects unchanged. arm64, Metal and CUDA remain
unverified here.

**`dna20alldup`, regenerated after the review of PR #3.** Up to this point `simseq.py` made
`dup=1` by random copying, so the fixture called "all duplicates" in section 4
held a few distinct sequences, about six copies each. `dup=1` now makes every sequence the
same, and the fixture holds 20 identical sequences. Every result above used the old file.
The new one, at production flags (gcc 13.3 `-O3 -march=native -ffp-contract=off`), passed
the fast matrix (163/163, which includes it in the threaded cases) and every full-matrix case
on it (63/63: 12 strategies, ordered and jitter runs at 2–4 threads, and the forced
fallbacks).
