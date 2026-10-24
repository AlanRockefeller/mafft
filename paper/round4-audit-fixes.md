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
build and forced one-worker fallbacks: 160 cases. `--full` widens inputs and repeats;
`--corpus` adds a private corpus; `--allow-clustal-version` accepts CLUSTAL output whose only
difference is the version on line 1 (off by default).

## 3. CI (written, not enabled)

`.github/workflows/exact.yml`: harness unit tests, then `verify-against-stock.sh` (fast) for
gcc scalar `-O3`; gcc AVX2 (`-march=haswell -ffp-contract=off`, so AVX-512 runners still
take the AVX2 paths); the clang FMA class (`x86-64-v3`, candidate `-DMAFFT_STOCK_FMA=1`);
ASan+UBSan (`STRIP=true`; sanitizer errors set exit codes 86/87 so they are compared with
stock, leak checks off because stock MAFFT does not free everything at exit); a threaded job
(full-mode ordered, jitter and forced-fallback L-INS-i); and a non-blocking arm64 NEON job on
`ubuntu-24.04-arm`. Stock builds are cached by compiler, version and flags. None of this has
run on GitHub yet.

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
| `ordered_turn` / `commit_turn` | the wait loop rechecks `ndone == mypos || orderedabort` under the mutex; `ndone` changes and the broadcast happen under the same mutex, so a wake-up cannot be lost. |
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
