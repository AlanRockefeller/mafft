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
| Residue-map cache | 10,448 exact matrix/bound comparisons and sanitizer cleanup checks passed; never timed or checked against the corpus goldens | not in any release. Code on local branch `experiment/residue-map-cache` (`f8d8fe6`) | [round 2](round2-performance.md#additional-refinement-reuse-audit) |
| AVX2 importance gather (round 4, item 1) | 1.019× on `dna80dup`, 0.994× on the 12 development inputs; the function is 1.5% of production CPU | kept (exact, free), no production gain | [round 4](round4-audit-fixes.md#item-1-avx2-importance-gather-f52e4fe) |
| Ordered refinement look-ahead for `--thread 2` (round 4, item 5) | measured waiting worth up to about 7% of wall time | not implemented for opt9 ([1.6](future-optimizations.md#16-ordered-refinement-look-ahead-for---thread-2-latency-only)) | [round 4](round4-audit-fixes.md), "Item 5" |

## Output-changing attempts

These change the alignment, so they were judged by trees, not speed.

| Attempt | Result | Decision | Where |
| --- | --- | --- | --- |
| `--thread 2 --threadit 0` (completion-order refinement) | changed 24 of 46 alignments; lowest homology F1 0.965, and `zdku` lost a 100/100-supported split | not used; ordered refinement gives the one-thread output with two threads | [phylogenetics](round2-phylogenetics.md) |
| FFT-NS-i in place of L-INS-i when one sequence is ≥ 1,000 bp (`--auto`'s choice) | 4 of 8 topologies moved beyond seed noise; 2 of 8 flipped 3–5 clades with SH-aLRT ≥ 80 | Dikarya routes tree jobs to L-INS-i | Dikarya `app/config.py` (2026-10-07/08) |
| Capping L-INS-i refinement at 2 or 3 cycles (2026-10-27/28) | saves 23% (short jobs) to 33% (long jobs) of their CPU at cap 2, but cap 2 contradicts well-supported clades beyond seed noise on 2 short and 4 long jobs (`2x7u`, 265 sequences: 10–11, counting both trees; `ib0b`: 7–8), and cap 3 on 3 (`9rjy`: 9, of which 5 of the current tree's 15) | not a blanket switch; cap 3 at ≥ 100 sequences would need a larger tree test | [future optimizations 2.1](future-optimizations.md#21-fewer-refinement-cycles) |
