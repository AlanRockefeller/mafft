# Future optimization candidates

What could still make MAFFT faster for the Dikarya workload, after opt9 (2026-10-27). Part 1
lists changes that keep output byte-identical to stock 7.526; part 2 lists changes that
alter output and would have to pass a tree-level test first. Nothing here is implemented.
Gains are bounds taken from measured CPU shares, or are marked unmeasured. A share bounds
what removing a stage could save; it is not a prediction.

## The workload and where the time goes

Dikarya's MAFFT jobs (100-input production corpus, [round-2 report](round2-performance.md)):
97 direction-adjusted L-INS-i (`--adjustdirection --localpair --maxiterate 1000`), 2
`--adjustdirection --auto` and 1 accurate-direction prepass. Inputs have a median of 131
sequences (90th percentile 170, maximum 1,042), and the longest sequence is 912 bp at the
median. 94 of 100 inputs contain duplicate sequences, a median of 12.6% of rows.
The launcher caps `--maxiterate` at 16 refinement cycles (`core/mafft.tmpl`,
`iteratelimit`), so production L-INS-i runs at most 16. Dikarya picks the strategy in
`/var/www/dikarya/app/config.py`: full L-INS-i for IQ-TREE, RAxML-NG, MrBayes and Quick Tree
jobs below 200 sequences and 3,000 bp, and `--auto` otherwise.

CPU profile of L-INS-i on the 12 development inputs, before the opt9 band
([round-4 report](round4-audit-fixes.md#production-profile)):

| Stage | Share |
| --- | ---: |
| all-pairs local DP (`Lfill_kb`, `Lscore_kb`, `L__align11`, `lfill_prepare`) | 36.4% |
| importance fill (`fillimp_track`, `fillimp_banded`) | 13.2% |
| refinement DP (`partA__align`) | 11.8% |
| progressive alignment (`A__align`) | 7.5% |
| refinement bookkeeping (profile and gap-count rebuilds) | 6.8% |
| `alignableReagion` | 3.7% |
| `match_calc` | 3.3% |
| importance gather | 1.5% |
| unresolved libc address in refinement workers (probably `memset`) | 1.4% |

By program: `dvtditr` (refinement) 50.7%, `tbfast` 43.8%, `makedirectionlist` 4.3%. The band
removed about a quarter of the all-pairs CPU (1.10× end to end), so all-pairs is still the
largest single stage. A fresh opt9 profile is the first step of any new round.

The 1,042-sequence `--auto` job (`buh4`, FFT-NS-2) has a different profile:
`localcommonsextet_p2` (in `makedirectionlist`) 29.8%, `A__align` 22.0%,
`naivepairscorefast` 15.0%.

## Part 1: byte-identical

Each needs the existing proof of exactness:

- `harness/verify-against-stock.sh --full` (675 cases);
- the 100-input corpus against `bench/golden/production-t1` (the `deploy.py` rehearsal does
  this);
- objcheck, to show the change touches only the intended objects;
- `force_fallbacks.py` for any new allocation or fallback path.

Ranked by expected value for Dikarya.

### 1.1 A tighter lower bound for the all-pairs band

The band certificate allows any lower bound L that is the score of a real alignment history.
Measured over all 607,463 production pairs, the iterative bound used now skips 48% of cells;
the exact score, the limit, would skip 65.4%
([round-4 report, item 3](round4-audit-fixes.md#item-3-exact-banded-all-pairs-fill)).

- **Bound.** The extra 17.4 points of all-pairs cells are about 6% of pre-band CPU, if time
  scales with cells. For the band itself, that scaling predicted 10% and 1.10× was measured.
- **Cheap valid bounds:**
  - the score of the ungapped segment on the best 6-mer-anchored diagonal;
  - pass 1 centred on that diagonal instead of on `[min(0,D) − w, max(0,D) + w]`;
  - for a near-duplicate of an already-aligned sequence, the score of the copied history.
- **Constraint.** Each candidate bound must be the score of a history the DP could actually
  take: same gap model, same letters and endpoints. Otherwise the certificate does not apply.

### 1.2 Per-pair fixed cost in all-pairs

`26et` and `eifm` (L-INS-i) ran slightly slower with the band. The probable cause, not
verified, is per-pair setup that the band's cost estimate ignores: `lfill_prepare`,
computing M, buffer setup. Amortizing per-query setup (each sequence's striped profile and
letter codes) over all its pairs would help every pair, banded or not. Measure first:
`lfill_prepare` was 0.6% of CPU before the band.

### 1.3 Refinement work items (round-4 design notes, item 2)

From [the design notes](round4-audit-fixes.md#design-notes-for-the-items-not-implemented),
with their bounds:

- **(a)** Fuse the importance add into `partA_row`: ≤ 1–2%. Keep the order
  `(match + imp) + wm` and `impclean`'s `+ 0.0`.
- **(b)** Generation-stamped importance rows instead of clearing: ≤ 1.4%. First resolve the
  libc address with libc symbols.
- **(c)** Incremental profiles and gap counts across rejected attempts: ≤ 6.8%, likely much
  less. An acceptance usually changes every row.
- **(e)** int16 `ijp`: memory traffic only. It touches nine aligners; not recommended
  before (a)–(c).

### 1.4 Importance fill (13.2%)

`fillimp_track` and `fillimp_banded` build the consistency (importance) terms from the
all-pairs library. They are the second-largest stage and have had only band-width tuning.
Some ideas already failed:

- 16- and 32-row bands regressed;
- branchless bounds regressed.

The untried direction is locality: process contributions in an order that keeps the target
rows in cache, and reuse a pair's contribution across refinement cycles when neither group's
rows changed. The bound is the stage's share; no estimate.

### 1.5 The direction check and the pair re-scoring

`makedirectionlist` is 4.3% of L-INS-i CPU. On the large `--auto` job, two stages take 45%:

- **`localcommonsextet_p2`** (the direction check, `core/makedirectionlist.c`) counts shared
  6-mers in integers, so any faster counting is exact by construction:
  - sorted-list intersection, vectorized;
  - bitset AND plus popcount;
  - reuse of each sequence's table across its pairs.
- **`naivepairscorefast`** (`core/mltaln9.c`) scores pairs of aligned rows for FFT-NS-2's
  second guide tree. Its terms are integers (`amino_dis`, the gap penalty) summed into a
  double, so the sum is the same in any order while it stays below 2^53. That permits a
  vectorized version; the fork's direct version (`MAFFT_DIRECT_PAIRSCORE`) is still scalar.

This matters most for large inputs and for the tail. On 2026-09-04 one genome-scale
submission spent 7.5 hours in `makedirectionlist`; Dikarya now skips the check above 5 Mbp
(`MAFFT_ADJUSTDIRECTION_MAX_BASES`).

### 1.6 Ordered refinement look-ahead for `--thread 2` (latency only)

Measured in round 4 ([item 5](round4-audit-fixes.md#item-5-waiting-in-ordered---thread-2-measured-not-implemented)):
ordered workers spend most of their lost time waiting for their commit turn, not
recomputing.

- **Bound.** At most about 7% of `--thread 2` wall time, and no CPU.
- **Change needed.** A worker that finishes early starts the next uncommitted position, and
  whoever holds the turn commits the parked results in order.
- **Status.** It changes the commit protocol and was deferred for opt9. Worth it only if
  single-job latency matters more than backlog throughput, for which two `--thread 1` jobs
  are better.

### 1.7 Ordered refinement for DNA FFT-NS-i (item 6)

`--auto` jobs that fall to FFT-NS-i still refine in completion order with two threads, so
their `--thread 2` output varies from run to run. Extending the ordered protocol makes them
deterministic and equal to `--thread 1`. That is reproducibility rather than speed: these
jobs could then use `--thread 2` for latency without changing output. The work is the
written-before-read argument for `Falign`'s 95 and `Salignmm.c`'s 51 thread-local statics.

### 1.8 Common-query batching for the pairs the band declines (item 4)

Batching eight pairs that share a query into the lanes of one AVX2 DP gave 1.145–1.253× in
three probes on real sequence shapes with a synthetic score matrix ([round-2 report](round2-performance.md#compiler-research-and-rejected-alternatives)).
The band now takes most similar pairs, so batching competes with it. It is worth revisiting
only for the declined (divergent) pairs, and only on inputs where many pairs decline.

### 1.9 Exact output cache in the service

Outside MAFFT: key on (release install hash, full argument list, input SHA-256) and return
the stored bytes. The output is the same by construction for every run that is
deterministic: `--thread 1`, and ordered L-INS-i with threads. That rules out completion-order
FFT-NS-i. The corpus had 3 duplicate input files out of 100. Check first whether Dikarya's
`cache/` already does this.

### 1.10 Smaller or longer-term items

- **PGO, retrained on opt9.** A 1.010× screen on the training inputs only, never confirmed
  on holdout ([round 2](round2-performance.md#compiler-research-and-rejected-alternatives)).
- **Exact prefix-frontier reuse.** Adjacent sorted sequences share 24% of their characters as
  prefixes. Reusing DP frontiers would need the matching affine state, endpoint and tie
  state; this is research.
- **Vectorize `partA__align_variousdist`** (`--unalignlevel`, 77% of CPU there). Dikarya does
  not use it.
- **Runtime CPU dispatch (item 7).** This is portability (one binary for AVX2 and AVX-512),
  not a speedup on the Dikarya VM.

### Tried and rejected (don't repeat without a new reason)

The full list, exact and output-changing, with results and sources, is in
[tried-and-rejected.md](tried-and-rejected.md).

## Part 2: changes to output

### How to judge "doesn't matter"

Dikarya has already measured alignment changes this way (`app/config.py`, 2026-10-07 and
2026-10-08): IQ-TREE GTR+F+G4 with SH-aLRT, after the same trimming. A change passes if:

1. the topology moves no more than two tree-search seeds move it on one alignment; and
2. no clade with SH-aLRT ≥ 80 is contradicted.

By that test, `--auto` and full L-INS-i agreed below 1,000 bp. Where one sequence of
≥ 1,000 bp pushed `--auto` to FFT-NS-i, 4 of 8 topologies moved beyond seed noise and 2 of 8
flipped 3–5 well-supported clades.

Small alignment differences can still matter. In the [phylogenetics study](round2-phylogenetics.md),
`--thread 2 --threadit 0` changed 24 of 46 alignments. The lowest homology F1 against the
one-thread alignment was 0.965, yet `zdku` lost a 100/100-supported bipartition. Any change
in this part therefore needs the tree test on the full corpus, including the 10 difficult
inputs and the ≥ 1,000 bp inputs, not just an alignment similarity score. The round-2 tools
(`harness/round2/tree.py`, `same_seed_trees.py`, `clade_stability.py`, `support_changes.py`)
already compute seed-noise baselines and support changes.

Ranked by expected gain against risk.

### 2.1 Fewer refinement cycles

L-INS-i runs up to 16 cycles; `--auto` uses 2 for 100–199 sequences below 1,000 bp. The
option is `--localpair --maxiterate N` and needs no code change; the output is stock's for
those options.

**Cycles actually run** (dikarya5 `--thread 1` logs, which give the same output as opt9).
Refinement stops after a cycle that accepts nothing, so a job that ran k cycles already had
its final alignment after cycle k − 1.

| L-INS-i jobs | Jobs | Cycles run, median (range) | Reached 16 |
| --- | ---: | ---: | ---: |
| longest sequence < 1,000 bp | 56 | 3 (1–8) | 0 |
| ≥ 1,000 bp | 41 | 4 (2–8) | 0 |

**Measured on the corpus L-INS-i jobs** (2026-10-27): the deployed opt9, production options,
`--thread 1`, one run per cap, alternating order, nice 19 beside production. CPU is child
user + system; wall time was dominated by production load and is not used.

| Jobs | Cap | CPU | Saved | Byte-identical to current | Homology F1 of the changed (min / median) |
| --- | ---: | ---: | ---: | ---: | --- |
| 56 short (< 1,000 bp) | 16 (now) | 235.4 s | — | 56/56 | — |
| | 3 | 210.1 s | 10.8% | 47/56 | 0.988 / 0.9997 |
| | 2 | 181.7 s | 22.8% | 34/56 | 0.983 / 0.9996 |
| 41 long (≥ 1,000 bp) | 16 (now) | 360.8 s | — | 41/41 | — |
| | 3 | 287.1 s | 20.4% | 24/41 | 0.993 / 0.9987 |
| | 2 | 242.7 s | 32.7% | 15/41 | 0.978 / 0.9991 |

- **Which jobs stay identical.** Mostly the ones the stopping rule predicts. At cap 2 on short
  jobs, exactly the 34 that converged within 3 cycles. Elsewhere a few changed although they
  converged early enough, which is not explained: at cap 3, `3on0` and `8q2l` (short) and 3 of
  27 long jobs; at cap 2, 1 of 16 long jobs.
- **Size of the saving.** The 56 short jobs are about 43% of all corpus CPU (235 s of opt9's
  545 s in the round-4 benchmark, a separate measurement), so cap 2 on them alone would save
  about 10% of all production MAFFT CPU. Long jobs save more per job, because they run more
  cycles. Per-job savings vary (short jobs at cap 2: median 15%), and single runs are noisy.
- **Size of the changes.** They are smaller than those of `--threadit 0` in round 2 (lowest
  F1 0.965 there, 0.978 here). That says little about trees (below).

**Tree test on the short jobs** (2026-10-27). For each of the 22 short jobs that change at
cap 2, and for the 9 distinct cap-3 alignments, the current and capped alignments went through
Dikarya's pipeline: trimAl 1.5 `-gt 0.1`, then IQ-TREE 3.1.4-dikarya1 `-m GTR+F+G4 --alrt
1000 -T 1` with seeds 73111 and 73129. Topology is compared by normalized Robinson–Foulds
distance with zero-length branches collapsed. Contradictions count clades with SH-aLRT ≥ 80 in
either tree that are incompatible with a split of the other, so the counts in the tables add
both directions. Seed noise is measured as the same two quantities between the two seeds on
one alignment.

| Cap | Jobs changed | Mean RF above seed noise | Strong-clade contradictions in every cross pair, above seed noise |
| ---: | ---: | ---: | --- |
| 2 | 22 | 10, by 0.002–0.05 (`ib0b` 0.12) | 2: `ib0b` (7–8 clades; seeds 0–1), `ppo5` (1; seeds 0) |
| 3 | 9 | 2, by 0.025 and 0.072 | 1: `7xqo` (1; seeds 0) |

- **`ib0b` fails clearly.** It has 42 sequences and an F1 of 0.9996 against the current
  alignment, yet the current and capped trees contradict 7–8 of each other's well-supported
  clades; the capped trees contradict 3–4 of the current trees' 16–17. Alignment similarity
  does not predict the tree effect.
- **Below 100 sequences is outside Dikarya's earlier evidence.** `--auto` itself runs 1000
  iterations there, and 10 of the 56 short jobs are in that range. Any cap should exclude
  them, and even 100–199 sequences gave one consistent single-clade loss (`ppo5`).
**Tree test on the long jobs** (2026-10-28), the same pipeline, on the 26 jobs that change
at cap 2 and the 17 that change at cap 3. Three corpus entries (`1mhv`, `f1q8`, `asmt`) give
the same renamed alignments, as do `o9i3` and `zdku`, so they count once below.

| Cap | Distinct alignments changed | Mean RF above seed noise | Strong-clade contradictions in every cross pair, above seed noise |
| ---: | ---: | ---: | --- |
| 2 | 23 | 10, by 0.011–0.056 (`cbsy` 0.105) | 4: `2x7u` (10–11 clades; seeds 1–2), `cbsy` (4; seeds 0), `ceuc` (2–4; seeds 0), `4gfa` (2; seeds 0) |
| 3 | 16 | 6, by 0.008–0.056 (`cbsy` 0.105, `9rjy` 0.381) | 2: `9rjy` (9; seeds 0), `cbsy` (3; seeds 0) |

- **Cap 2 fails on long jobs of every size.** `2x7u` has 265 sequences, `ceuc` 140 and `4gfa`
  135.
- **Cap 3 is not always closer than cap 2.** On `9rjy` (24 sequences), the cap-2 trees equal
  the current ones, but the cap-3 trees contradict 5 of the current trees' 15 well-supported
  clades (9 counting both directions), at an F1 of 0.994.
- **Both long cap-3 failures have fewer than 100 sequences** (`9rjy`, `cbsy` with 22).

**Conclusion.** No cap is safe as a blanket switch. Cap 3 restricted to jobs with at least 100
sequences comes closest: it changed 21 distinct alignments (9 short, 12 long), and one of
them (`7xqo`, short) consistently lost one clade. Offering even that would need a larger tree
test first.
The rows are in [tried and rejected](tried-and-rejected.md#output-changing-attempts).

Scripts and run records: [`harness/round2/cycle_cap/`](../harness/round2/cycle_cap/).
`cycles.json` holds the cycles run per job, from the dikarya5 `--thread 1` refinement logs.
`itercap.py` runs the caps and writes `short.jsonl` and `long.jsonl`. `treetest.py` runs and
scores the tree test, and `trees-short.json` and `trees-long.json` are its per-job output.

### 2.2 Align the distinct sequences, copy duplicates back

- **Status.** The fork already reuses duplicate pairs exactly in all-pairs (dikarya2).
- **Possible gain.** Progressive alignment, refinement and the importance terms still pay
  for every row. Those terms scale with the number of pairs, so removing the median 12.6% of
  rows removes about 24% of pair work at the median, on those stages.
- **Why output changes.** Duplicates change sequence weights and consistency, so the other
  rows can align differently.
- **Precedent.** Dikarya already collapses identical sequences before long tree searches
  (`app/services/identical_sequences.py`). For RAxML-NG it measured 14–39% less CPU, with
  results within seed noise.
- **Test.** Copy each duplicate's row from its kept twin, then run the tree test.

### 2.3 Allow FMA contraction

Broadwell has FMA. Building with `-ffp-contract=fast` changes the rounding class, not the
algorithm: the output equals stock built the same way (the `MAFFT_STOCK_FMA` class), so it
is a stock result, just a different one.

- **Gain.** Unmeasured, probably a few percent in the double-precision profile DPs.
- **Cost.** It needs only the build and the tree test, not new code.

### 2.4 Single precision in the profile DPs

`partA__align`, `A__align` and the importance terms use double. Single precision doubles the
AVX2 lane count.

- **Bound.** Those stages are 32.5% of CPU (11.8 + 7.5 + 13.2), so the gain is at most
  about 16%.
- **Risk.** Rounding changes tie-breaks. Gap costs are fractional, so ties are not rare.
- **Cost.** Per-kernel work.

### 2.5 Heuristic band for divergent pairs

The certified band declines pairs whose score is too low to prove a narrow band. A fixed
band around the 6-mer-anchored diagonal would also cover those pairs.

- **Gain.** Possibly most of the remaining all-pairs CPU.
- **Risk.** The changed local alignments feed the consistency library directly, so this is
  riskier than 2.1–2.3.

### 2.6 Sparse consistency library

Compute local alignments and importance terms only for each sequence's k nearest neighbours
by 6-mer distance, instead of all pairs. This shrinks both all-pairs (≈ 28–36%) and the
importance fill (13%) from O(N²) to O(Nk) work. It is the largest possible saving and the
largest change to the method; research.

### Not recommended

- **Completion-order threads (`--thread 2`, `--threadit 0`).** Measured to change trees
  (above). Ordered refinement already gives the one-thread output with two threads.
- **FFT-NS-i in place of L-INS-i above 1,000 bp.** Dikarya measured clade flips; that is why
  tree jobs get L-INS-i.
- **The fast direction check in `fix_direction_with_mafft`** (the prepass for MUSCLE,
  Clustal Omega and IQ-TREE `--align-only`). Dikarya keeps the accurate check there on
  purpose, because the fast one was measured only on the MAFFT path. It may well be safe,
  but it is a direction decision, not an alignment detail, so it needs its own measurement.

## Suggested order

1. Profile opt9 on the corpus.
2. Run 2.2 (duplicates) and 2.3 (FMA) through the tree test. They need little or no code and
   have the largest likely gains. 2.1 failed the test on short and long jobs (`ib0b`, `2x7u`,
   `9rjy`, above), so it is not a blanket switch; cap 3 at ≥ 100 sequences would need a
   larger test.
3. In parallel, the exact items: 1.1 (tighter band bound), 1.5 (direction check and
   re-scoring) and 1.3(a)–(b).
4. 1.4 (importance locality), 2.4 and 2.5 only if the profile still points there.
