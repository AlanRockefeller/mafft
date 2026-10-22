# Round 2: exact AVX2 score kernels

These experiments compare with tracked dikarya2 checkpoint `8894774`. They measure
kernels, not the final whole-corpus speedup. Source changes are in commit `45d3da5`;
the later guard hardening described below is separate from the frozen timing
artifacts. Build/test instructions are in [the round 2 harness](../harness/round2/README.md)
and [the existing differential harness](../harness/broadwell/README.md).

## Changes and exactness

The new score-only fill retains one in-place striped score row, one vertical-state
row and substitution profiles. It discards traceback tags, retained history,
endpoint searches and per-row maximum reductions. Preloading each old row
vector before overwriting it supplies the next cell's predecessor. The global
maximum is taken before local-threshold clamping and substitution addition, as in
the original double recurrence. Padding is excluded from that maximum.

The arithmetic/alphabet guard remains shared with the full fill. Fractional or
excessive-range scores use the original double path; every zero result does too,
preserving signed-zero return bits. The score-only kernel needs no full-history
256-MiB allocation limit. Three long cases test this expanded accepted domain.
For four used characters and a 700-residue target, its integer workspace is
16.5 KiB, compared with approximately 1.90 MiB of retained score history alone
in the old fill. These are allocation formulas, not whole-process RSS measurements.

A separate identical-sequence shortcut is exact when all used diagonals equal
M>=0, every used substitution is <=M, gap opening/extension are nonpositive and
the threshold is <=M. Every raw predecessor is bounded by `min(i,j)*M`; the
ungapped identity path attains n*M. Nonuniform diagonals and other settings retain
DP. Zero scores still take the public double fallback.

The full traceback fill has a shared inline row body with an explicit constant-zero
extension argument. This removes affine column-offset arithmetic when extension
is zero, preserving strict tie comparisons, markers, embedded scores and endpoints.
Experiment snapshots defaulted to rolling on, self shortcut off and zero-extension
specialization off; `MAFFT_KB_SCORE_ONLY`, `MAFFT_KB_SELF_SCORE` and
`MAFFT_KB_ZERO_EXT` select controlled comparisons. Final promoted defaults and
the selected build are separate from those historical snapshots. Commit `91146f2`
enabled all three by default for **7.526-opt8-dikarya3**. The current selected
**7.526-opt8-dikarya4** source at `9d963ee` retains those defaults and the later
guard hardening. It also retains ordinary profile stack-buffer removal disabled
because that experiment showed no gain.

## Differential evidence

Counts below include repeated execution under different backends/sanitizers;
they are not counts of independent biological datasets.

| Test family | Executed cases | Nominal DP cells | Comparison |
|---|---:|---:|---|
| Rolling: ordinary, wide scores, four lanes, sanitizer repeat | 80,000 | 283,257,884 | All 64 public return bits vs original double |
| Rolling: lengths 8,180/8,190/8,200 | 3 | 201,228,500 | Same, across old history-cap boundary |
| Identical shortcut: ordinary + sanitizer repeat | 13,000 | 303,092,765 | Public return bits; 1,625 deliberately eligible cases |
| Full traceback: generic/zero extension, wide scores, four lanes, byte/packed/embedded layouts, sanitizer repeat | **75,000** | 271,995,535 | Every DP score, resolved offset and endpoint |
| Independent matrices: ordinary + sanitizer repeat | 3,000 batches / 24,000 matrices | 77,329,921 | Raw scores vs scalar double and striped DP |

All passed. Of the full-traceback cases, 35,000 explicitly force zero extension.
Those drivers report zero full `Ltracking` calls: they check cell states, offsets
and endpoints; full alignment/golden validation is a separate requirement.
Sanitizer repeats use ASAN/UBSAN and poisoned workspaces, with leak detection
disabled. Tests cover asymmetric matrices, ties, fractional/range fallback,
signed zeros, positive nonstandard gap parameters, ambiguity/protein alphabets,
length boundaries and repeated cleanup. Raw integer-zero batch scores are compared
numerically to scalar double; the separate public-call tests check zero signs.

## Interleaved kernel timing

GCC uses `-O3 -march=native -ffp-contract=off -g`, CPU 1, three alternating-order
rounds and 512 fills per invocation. Builds and other experiments were stopped.
CPU and wall times and matching checksums are retained. Values below are CPU
ns/cell; short runs describe these probes and do not establish confidence bounds.

| Shape | Old score-only | Rolling score-only | Speedup | Old full traceback | Zero-extension full traceback | Speedup |
|---|---:|---:|---:|---:|---:|---:|
| 700 x 700 | 0.5353 | 0.3683 | 1.454x | 0.5356 | 0.4647 | 1.153x |
| 200 x 1,200 | 0.5555 | 0.3917 | 1.418x | 0.5518 | 0.4903 | 1.125x |
| 1,200 x 200 | 0.5787 | 0.3956 | 1.463x | 0.5834 | 0.5187 | 1.125x |
| 1,024 x 1,025 | 0.5339 | 0.3551 | 1.503x | 0.5359 | 0.5011 | 1.069x |
| 64 x 64 | 1.4762 | 1.2353 | 1.195x | 1.3098 | 1.2691 | 1.032x |

The generic shared-body refactor alone was within about 0.7% of the original
full fill across these shapes. At 700 x 700, full-fill summed CPU/wall times were
0.4031/0.4205 s originally and 0.3497/0.3634 s with zero specialization.
The score-only probes include guards and profile setup; no isolated self-shortcut
speedup was measured. Score-only improvements principally help accurate direction
and must not be equated with the same gain in ordinary L-INS-i.

## Independent matrices: useful restriction, rejected general dispatch

Eight independent alignments occupy the eight int32 lanes. A common query permits
packed profile loads; differing query characters require gathers. Profile packing
and guard checks are included in CPU timings. These experiments have **no production
batch API or dispatch**.

| 700-residue synthetic experiment | Striped/batch CPU speedup | Outcome |
|---|---:|---|
| Common query, equal lengths | 1.244x | Promising restricted prototype |
| Independent queries, equal lengths | 0.268x | 3.73x slower; reject general dispatch |
| Independent queries, ragged lengths | 0.071x | 14.15x slower; only 23.5% matrix utilization |

Two eight-pair common-query groups from each real FASTA gave aggregate CPU
speedups of 1.233x (`icv1`), 1.145x (`bpfd`) and 1.253x (`t04t`). These use actual
sequence shapes with an explicitly **synthetic +600/-450 matrix**, not the actual
production scoring setup. They establish neither a whole-program gain nor an
alignment/biological result. Common-query production integration requires actual
matrix tests, per-lane fallback, cleanup, shape calibration and golden comparisons.

## Static review and remaining limits

Stored assembly confirms that zero-extension loops eliminate five vector
arithmetic operations per SIMD inner-loop iteration (eight cells on this AVX2
build). `Lfill_kb` text grows from 6,673 to 8,089 bytes
(21.2%); instruction-cache effects remain a compiler/layout tradeoff. A future
exact experiment could replace the final stop-tag select with
`ij | (clamp & 3)`, since preceding tags are in {0,1,2}. It has not been implemented
or measured; the earlier rejected native-blend experiment is not evidence for it.

Both public NULL-input cleanup paths release the rolling thread-local allocation
and reset its pointer/capacity. No new cleanup/fallback defect was found in static
review. The largest workspace is retained until that cleanup, as intended.

The additional ordinary/partial profile-row caches were also reviewed statically:
hashing locates candidates, while every profile-double bit must match. Only raw
substitution scores are copied, before importance or DP modifies the caller's row.
Initialization/NULL cleanup clears context; boundary rows bypass; matrix pointer,
generation and alphabet are checked. The second profile is immutable within the
one-call cache scope. Each 8-MiB setting bounds row payload, with metadata and
temporary indexing additional; the two thread-local caches can coexist.

Review found inherited pre-conversion risks in `lfill_prepare`: nonfinite/out-of-int
floating values were cast before validation, `abs(INT_MIN)` and integer bound sums
could overflow, and integer `-offset` could overflow. The focused follow-up checks
finite/range conditions before conversion, widens bound arithmetic and rejects an
impossible lower bound before sequence scanning. All 39 direct helper cases
passed ASAN, UBSAN and float-cast-overflow sanitization. A further 512 public-call
comparisons / 1,543,002 cells passed original-double return-bit comparison under
the same sanitizers. These post-timing checks are retained in
`../round2/score-guard-check-02`. Direct helper tests do not
claim that the upstream scalar algorithms support arbitrary nonfinite/extreme
parameters; public scalar initializers still need separate review for INT_MIN
offset. The earlier tracked dikarya3 build and the corrected dikarya4 build each
passed 612 option/format comparisons, six long alignment comparisons, 35 placement
comparisons, 36 sanitizer fixtures and two real-input sanitizer comparisons.
Additional dikarya3 thread-zero and noniterative two-thread fixtures passed 144
and 72 comparisons, respectively. These compare
complete outputs against frozen original opt8 or the verified release as
specified in the retained run metadata. Final whole-corpus timing is separate.

Private artifacts are `../round2/score-kernel-check-01` and
`../round2/score-kernel-check-03`, particularly `microbench.jsonl`,
`ext-microbench.jsonl`, `batch-microbench.jsonl`, `batch-real-shapes.jsonl` and the
checking logs. Previous `../bench/` goldens and timing results were preserved.
