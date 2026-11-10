# Tried and not adopted

Every speedup attempted on the Dikarya VM (Broadwell AVX2, GCC 13.3) from dikarya2 through
opt9 that did not ship, or shipped without gaining anything. Don't repeat one without a new
reason. Each row links to the report with the measurements. Earlier work on Apple silicon
and AWS (opt1–opt8) has its own "what did not help" paragraphs in the
[technical report](mafft-apple-silicon.pdf).

Run records cited in the reports by workspace path (`bench/...`, `round2/...`) were moved
into a compressed archive on the Dikarya VM when the workspace was cleaned on 2026-10-27:
`/home/tree/mafft-optimized/archive/research-records-2026-10.tar.xz`. It is private, not in
git, and keeps paths as cited.

## Exact (byte-identical) attempts

| Attempt | Result | Decision | Where |
| --- | --- | --- | --- |
| Four-lane striped local DP | 0.978 ns/cell vs 0.562 for deferred eight lanes | rejected | [dikarya2](broadwell-dikarya2.md#experiments-rejected-or-not-selected) |
| Eight-lane fill without the deferred end-cell search | 0.702 vs 0.719 ns/cell; the deferred search gave the real gain | superseded by the deferred form | same |
| Packed three-bitplane traceback | 0.562 ns/cell, no gain over byte flags | off by default (`MAFFT_KB_PACKED`) | same |
| Native AVX2 lane blend instead of xor/and/xor | 0.551 vs 0.531 ns/cell after warm-up | rejected | same |
| 16- and 32-row importance bands | slower than the original 128 rows in real refinement replays | rejected | same |
| Branchless importance bounds | slower in replays | off by default | same |
| Importance bands off everywhere | faster on small replays only | replaced by a 16 MiB cutoff | same |
| GCC PGO trained on the development inputs (dikarya2) | aggregate regression, worst on accurate direction | rejected | same |
| PGO plus LTO (dikarya2) | regressed | rejected | same |
| Clang 15, GCC LTO alone, freshly trained PGO (round 2) | 0.989×, 0.983×, 1.010× CPU on the 12 development inputs; PGO's gain was on its own training set only | ordinary GCC kept | [round 2](round2-performance.md#compiler-research-and-rejected-alternatives) |
| Eight independent pairs in one AVX2 matrix | 3.73× slower with independent queries, 14.15× with ragged lengths | rejected; the common-query form stays open ([1.8](future-optimizations.md#18-common-query-batching-for-the-pairs-the-band-declines-item-4)) | [round 2 score kernels](round2-score-kernels.md#independent-matrices-useful-restriction-rejected-general-dispatch) |
| Stack-only `scarr` (profile stack-buffer removal) | 58.22 vs 58.22 CPU s | off | [round 2](round2-performance.md#compiler-research-and-rejected-alternatives) |
| Cache for dynamic duplicate realignments | production settings never make those calls | not implemented | same |
| Prefix/suffix/substring DP reuse | equal substrings don't prove equal affine, endpoint and tie state | not implemented; research ([1.10](future-optimizations.md#110-smaller-or-longer-term-items)) | same |
| Rejected-branch memo v1 | the guard was never active (`cooling=1` even at `cut=0`), so its golden matches tested only the bypass | fixed in v2 | [round 2](round2-performance.md#additional-refinement-reuse-audit) |
| Rejected-branch memo v2–v4 | v4 passed 100/100 goldens, but review found short-block scratch dependencies its broad predicate did not exclude | replaced by v5 (per-call safety guards on the verified terminal no-gain suffix), which shipped in dikarya4 | same; [refinement reuse](round2-refinement-reuse.md) |
| Residue-map cache | 10,448 exact matrix/bound comparisons and sanitizer cleanup checks passed; never timed or checked against the corpus goldens | not in any release. Code on branch `experiment/residue-map-cache` (`f8d8fe6`) | [round 2](round2-performance.md#additional-refinement-reuse-audit) |
| AVX2 importance gather (round 4, item 1) | 1.019× on `dna80dup`, 0.994× on the 12 development inputs; the function is 1.5% of production CPU | kept (exact, free), no production gain | [round 4](round4-audit-fixes.md#item-1-avx2-importance-gather-f52e4fe) |
| Ordered refinement look-ahead for `--thread 2` (round 4, item 5) | measured waiting worth up to about 7% of wall time | not implemented for opt9 ([1.6](future-optimizations.md#16-ordered-refinement-look-ahead-for---thread-2-latency-only)) | [round 4](round4-audit-fixes.md), "Item 5" |

## Output-changing attempts

These change the alignment, so they were judged by trees, not speed.

| Attempt | Result | Decision | Where |
| --- | --- | --- | --- |
| `--thread 2 --threadit 0` (completion-order refinement) | changed 24 of 46 alignments; lowest homology F1 0.965, and `zdku` lost a 100/100-supported split | not used; ordered refinement gives the one-thread output with two threads | [phylogenetics](round2-phylogenetics.md) |
| FFT-NS-i in place of L-INS-i when one sequence is ≥ 1,000 bp (`--auto`'s choice) | 4 of 8 topologies moved beyond seed noise; 2 of 8 flipped 3–5 clades with SH-aLRT ≥ 80 | Dikarya routes tree jobs to L-INS-i | Dikarya `app/config.py` (2026-10-07/08) |
| Capping L-INS-i refinement at 2 or 3 cycles (2026-10-27/28) | saves 23% (short jobs) to 33% (long jobs) of their CPU at cap 2, but cap 2 contradicts well-supported clades beyond seed noise on 2 short and 4 long jobs (`2x7u`, 265 sequences: 10–11, counting both trees; `ib0b`: 7–8), and cap 3 on 3 (`9rjy`: 9, of which 5 of the current tree's 15) | not a blanket switch; cap 3 at ≥ 100 sequences would need a larger tree test | [future optimizations 2.1](future-optimizations.md#21-fewer-refinement-cycles) |

## The two experiment branches

Two prototypes are kept as branches on GitHub. This section says what each one holds, so
the next optimization pass does not need to read the branches to decide whether to pick
them up.

### `experiment/rejected-branch-memo` (`ad2ccd1`): shipped; the branch is history

- **Idea.** In iterative refinement, a branch attempt that was rejected gives the same
  result if it is tried again before any alignment change is accepted. The prototype stores
  the exact post-attempt score and an alignment epoch per branch (16 bytes per branch,
  `2*njob-3` branches). Every acceptance advances the epoch and invalidates all entries.
- **Size of the opportunity.** 3,668 of 94,762 branch attempts (3.87%) over the 100
  production jobs repeated a rejection with no accepted change in between; up to 30.51% of
  attempts in one job (`xwu3`). This counts attempts, not CPU.
- **What went wrong on the way.** v1's guard never ran (`cooling=1` even at `cut=0`), so
  its golden matches tested only the bypass. v2–v4 used one broad eligibility test that
  missed short-block scratch dependencies, although v4 matched 100/100 goldens. Lesson: a
  golden match does not show that a guard is active or sufficient; check that the fast path
  actually ran.
- **Outcome.** v5, with per-call safety guards on the verified terminal no-gain suffix,
  shipped in dikarya4 as `MAFFT_REJECT_MEMO` (`core/mltaln.h`, `core/tditeration.c`);
  `-DMAFFT_REJECT_MEMO=0` builds without it. Nothing remains to be done from this branch.
- **Full account.** [Refinement reuse](round2-refinement-reuse.md).

### `experiment/residue-map-cache` (`f8d8fe6`): unfinished; never timed

- **Idea.** Keep the gapped-row to residue-column map that the importance fill rebuilds
  (`makeresmap`, used by `fillimp_track`) from one call to the next, per worker thread,
  instead of rebuilding it for rows that have not changed.
- **How it stays exact.** A slot only proposes a candidate; the complete row bytes,
  including the NUL, must match before a map is reused. Rows borrowed in the current call
  are pinned, so an aliased sequence ID with a changed row gets its own temporary map.
  Storage is capped at 16 MiB per worker (`MAFFT_FILLIMP_RESMAP_CACHE_BYTES`) and 65,536
  slots; an allocation failure falls back to the old temporary path.
- **Where the code is.** Off by default behind `-DMAFFT_FILLIMP_RESMAP_CACHE=1`. The cache
  is in `core/mltaln9.c`, with release hooks in `core/Dalignmm.c`, `core/Salignmm.c` and
  `core/partSalignmm.c`. The differential checker is `harness/round2/resmap_cache_check.py`
  (parser tests in `test_resmap_cache_parser.py`). The branch sits on top of the five
  rejected-branch-memo prototype commits and predates dikarya4, so it needs rebasing.
- **What was verified.** Six native variants passed 10,448 exact matrix/bound comparisons
  (150,646,080 matrix cells), covering banded and direct operation, a 1,024-byte budget, a
  zero budget, allocation failures, repeated edits, aliased IDs, epoch wrap and real
  pthread isolation. ASAN, UBSAN and leak detection passed, and all nine public NULL
  cleanup paths released the warmed cache.
- **What was never done.** No run against the corpus goldens and no timing. Whether it
  saves any time is unknown.
- **Before retrying.** Measure how much of the importance fill (13.2% of production CPU,
  [future optimizations 1.4](future-optimizations.md#14-importance-fill-132)) is spent
  building these maps. If that share is small, drop the idea. If not: rebase onto the
  current release, run `harness/verify-against-stock.sh --full`, then time it.
- **Earlier notes.** [Round 2](round2-performance.md#additional-refinement-reuse-audit).
