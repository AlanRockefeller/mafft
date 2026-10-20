# Exact reuse of a terminal rejected-refinement suffix

The guarded experiment is preserved default-off on branch
`experiment/rejected-branch-memo`, snapshot `ad2ccd1`. Its exact guarded code
is now selected, enabled through a shared header switch, for dikarya4 validation
on `perf/dikarya-round2`. The earlier dikarya3 source/build and every experimental
snapshot remain available; no production setting or installation changed.

## What is reused

In the one-worker BAATARI2 implementation, a branch attempt rebuilds a candidate
from the current master alignment. A rejected attempt leaves the master
unchanged and finishes with `tscore=mscore`, the score of the unchanged input.
The experiment stores that exact post-attempt double and an alignment epoch for
each branch. Every acceptance advances the epoch and invalidates all old entries.
An overflowing epoch clears every stamp before restarting at one.

A hit reproduces the original history, convergence, finish, attempt-count and
coordinator bookkeeping. It does not return early from the refinement call or
skip branches in the search order. Diagnostic cache counters change; alignment
stdout remains the byte oracle.

The allocation is 16 bytes per branch on this binary64/uint64 build, plus a
small fixed set of worker fields. There are `2*njob-3` branches. Allocation
failure disables the optional cache. This is additional storage, not a claim
about whole-process peak RSS.

## Scope and runtime conditions

Eligibility requires one worker plus coordinator, BAATARI2, at least three
sequences, fixed tree and branch order, random seed zero, zero cut, DNA,
ordinary constrained profile alignment, score checking, FFT decomposition,
complete targets, per-cluster gap removal, ordinary end-gap handling and no
warp. RNA/structure, specificity-dependent matrices, score reporting and debug
scoring paths are excluded.

The actual 128-character pair-scoring matrix must be finite and bitwise
symmetric, including zero signs. This checks the assumption needed by the
inherited intergroup cache's unordered taxon-pair keys; it does not change that
cache or its arithmetic. The check occurs once per eligible worker.

Each actual `Falign_localhom` call reports a thread-local safety result. An
attempt is ineligible if any post-gap-removal block has fewer than two columns,
or a call is interrupted. Unsafe retries remain unsafe. An unsafe or nonfinite
rejection clears its stamp rather than leaving an older entry usable.

These exclusions are deliberate. The inherited partial aligner can return for
an empty block without assigning the caller's importance result. Its one-column
path also reads gap scratch at index one, while gap-count rebuilding covers
only `[0,length)`. The experiment does not initialize those old values or alter
their scoring behavior. It excludes their results from reuse. The `unsafe`
counter records such rejected attempts, not accepted attempts or all blocks.

## Why repeated rejection is reproducible in this scope

The dependency argument is specific to the guarded implementation:

| Consumed state | How it is rebuilt |
|---|---|
| Branch distances, membership and order | `distFromABranch` resets its result; `OneClusterAndTheOther_fast` rebuilds masks, lists and terminators from the fixed tree. |
| Weights | Unweighted mode assigns every weight. `weightFromABranch` resets every weight and traverses the fixed tree in fixed order; `fastconjuction_noname` resets and normalizes group totals in list order. |
| Input rows | `yarinaoshi` copies every row of the master before candidate construction. |
| Local homology | `msshrinklocalhom_fast_half` overwrites consumed pair pointers to immutable homology records. |
| Importance | `part_imp_match_init_strict` clears prior dirty ranges, resets their bounds and rebuilds ordered contributions. Banded segment cursors start at zero. |
| Fragment strings and maps | Each fragment is copied again; `commongappick_record` rebuilds maps through the terminating NUL. |
| Profiles and row caches | Live profile columns and sparse matching structures are rebuilt; raw-row caches reset at initialization. |
| DP and traceback | Initial rows, live vertical states and traceback boundaries are assigned before use. Blocks of at least two columns ensure the gap-state initialization reads live, rebuilt entries. |
| Returned importance | `Atracking_localhom` initializes `*impmatch=0.0` before ordered accumulation; Falign clears accumulated results at call entry. |

With fixed endpoints and no warp, unrebuilt terminal scratch can affect only
dummy values or final state updates, not a subsequent live row or the selected
traceback. This refinement path ignores Falign's returned DP score and computes
acceptance from fresh intergroup scoring and traceback importance.

Generation numbers support interruption detection and history, not the scoring
objective. The one worker prevents a concurrent master update during an
attempt. Zero cut remains zero under the cooling flag. Seed zero disables
branch shuffling, and this alignment path has no active random-number calls.
Thus the same master and branch rebuild the same live inputs, candidate rows,
importance and acceptance comparison. The finite, symmetric pair-cache
contract removes cache-order dependence. A previously rejected attempt is
rejected again with the same post-attempt score bits.

This is a scoped argument about supported, valid scoring inputs and the
existing arithmetic class, supplemented by differential tests. It is not a
claim that all historical MAFFT corner paths are independent of scratch state.

## Why skipped scratch cannot affect a later expensive attempt

Before its first possible hit in an epoch, the worker holds the existing
coordinator mutex and verifies both:

1. The current generation's accumulated gain is exactly zero.
2. The current branch and every remaining entry of the actual `branchtable`
   have a valid rejection stamp for the current epoch.

The current branch is checked separately; the scan starts at the already
advanced next-job position. Success or failure is cached for the epoch.
Acceptance invalidates the verification. Failure retains ordinary computation.

Each branch occurs once per generation. The previous generation's last
acceptance invalidated all earlier stamps; its later rejected suffix can have
the current epoch. A current-generation acceptance would invalidate those
entries again and prevent a hit. A successful check therefore identifies an
unchanged, no-gain generation whose remaining attempts all reuse verified
rejections. No later expensive attempt consumes skipped scratch updates.
The coordinator then takes its original no-gain termination path. Finite-score
or safety holes prevent this shortcut rather than weakening the argument.

The first prototype checked only each individual stamp. Explicit suffix
verification was added after review identified possible nonfinite holes.
Short-block and matrix guards were added in a separate later revision.

## Evidence and current decision

The historical audit found 3,668 potentially redundant attempts among 94,762
attempts across the 100 production jobs. This is a count of opportunities,
not a fraction of CPU time. Every benchmark artifact remains separate from
later source revisions.

The latest same-source memo-off/on trace comparison passed on five real inputs:
2,469 attempts, 171 accepted intermediate alignments, 6,603 exact original-order
master rows and 508 active hits. Every post-attempt raw score/gain bit,
acceptance, subgeneration and convergence/finish decision matched, including
signed-zero representations. All ten final alignments matched their frozen
goldens. The additional guards preserved all 508 hits in this subset; none of
its rejected attempts used the short-block exclusion.

See [the v5 semantic result](../../round2/reject-memo-v5-semantic-analysis/summary.json).
The latest v5 source also passed all 100 full outputs and 612 option/format
comparisons. Its active guard ran on 97 production jobs, with hits in 84:
3,632 attempts were reused and 82,371 computed normally. These job-weighted
counts do not estimate CPU fractions. No production rejection used the
short-block exclusion in this run.

An ASAN/UBSAN build matched four real goldens and all 20 short-input
comparisons. Three one-column settings each exercised nine unsafe rejections
and zero hits, confirming that the exclusion actually ran. Fractional-scoring
two- and three-column cases also exercised active hits while remaining exact.
The extracted actual matrix guard passed 373 expectations in each of strict
GCC and ASAN/UBSAN builds, covering symmetric/asymmetric values and zero signs,
NaN/Inf, null rows and seeded mutations. Repeated backends are not independent
data. See [the corpus validation summary](../../round2/reject-memo-v5-validation-summary.json),
[sanitizer completion record](../../round2/memo-v5-sanitizer-finished.json) and
[matrix-guard check](../../round2/reject-memo-v5-score-guard/manifest.json).

The later v5 three-round screen, under the prospectively calibrated idle-service
criterion, reduced the three deliberately selected high-reuse inputs' CPU total
per pass from 25.417 to 22.090 seconds (**1.151x**), with 18/18 exact outputs and
zero contention flags. All three rounds favored reuse. Development averaged
57.727 versus 55.797 CPU seconds (**1.035x**), retaining three flagged jobs among
72; those two affected inputs have a separate confirmation plan. Earlier v4
screens retain their contention caveat and are not the selection evidence.

The guarded implementation is selected for dikarya4 on this restricted measured
gain plus its exactness evidence. Its final fresh tracked builds, corpus timing,
profiles and scheduling measurements remain pending. The high-reuse set was
purposefully selected and does not estimate the whole-corpus gain. See the
[v5 development](../../round2/reject-memo-v5-idle-development-analysis/summary.json)
and [high-reuse](../../round2/reject-memo-v5-idle-high-reuse-analysis/summary.json)
results, with every original record and prospective criterion retained.
