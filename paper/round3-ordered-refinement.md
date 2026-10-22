# Ordered multi-worker refinement

Version **7.526-opt8-dikarya5** makes the iterative refinement of DNA L-INS-i, G-INS-i
and E-INS-i runs (`--localpair`, `--globalpair`, `--genafpair` with `--maxiterate`)
compute exactly the alignment of one worker when it uses more than one. On the Dikarya
VM this gives `--thread 2` the output of `--thread 1` (and of stock MAFFT 7.526 with
`--thread 1`), while the refinement still uses both CPUs. Other modes, including
FFT-NS-i, protein and RNA, keep stock multi-worker refinement ([scope](#why-the-speculative-result-equals-the-one-worker-result)).

## Results

Measured on the Dikarya VM (two vCPUs, GCC 13.3, `-O3 -march=native -ffp-contract=off`)
on 2026-10-22, at nice 5 with no other MAFFT work running. Each input ran under four
configurations in a freshly shuffled order per input and round:

- **A** `--thread 1`
- **F** `--thread 2 --threadit 1` (one refinement worker; round 2's reproducible latency setting)
- **B** `--thread 2` with `MAFFT_ORDERED_REFINE=0` (stock completion-order refinement, which
  is what plain `--thread 2` did through dikarya4)
- **O** `--thread 2` (ordered refinement, the dikarya5 default)

**Round-2 latency cohort** (the same 12 inputs as round 2's isolated-latency study,
three rounds, 144 runs):

| | mean wall s | vs A | mean CPU s | outputs identical to one-thread golden |
|---|---:|---:|---:|---:|
| A | 4.222 | 1.00x | 4.129 | 36/36 |
| F | 3.618 | 1.17x | 4.283 | 36/36 |
| B | 2.756 | 1.53x | 4.635 | 18/36 (6 of 12 inputs gave 3 different alignments in 3 runs) |
| **O** | **2.870** | **1.47x** | 4.486 | **36/36** |

A, F and B agree with round 2's measurements of the same configurations within 4%.
O is 1.26x faster than F (per input 1.01x–1.39x; the smallest gains are the two
sub-second inputs and the FFT-NS-i input, which O leaves as stock).

**56 new production inputs** (the inputs Dikarya received 2026-10-16 to 2026-10-22 that
are not in the round-2 corpus; 53 L-INS-i, 3 `--auto`; two rounds, 448 runs):

| | mean wall s | vs A | mean CPU s | identical to stock 7.526 `--thread 1` |
|---|---:|---:|---:|---:|
| A | 4.962 | 1.00x | 4.851 | 112/112 |
| F | 4.262 | 1.16x | 5.036 | 112/112 |
| B | 3.186 | 1.56x | 5.410 | 80/112 |
| **O** | **3.309** | **1.50x** | 5.211 | **109/112** |

All three O differences are `--auto` inputs that MAFFT ran as FFT-NS-i, outside the
ordered scope; **all 106 L-INS-i runs under O were identical to stock**. On the 53 L-INS-i
inputs O was 1.50x faster than A (median per input; 1.12x–1.61x) and 1.26x faster than F
(median; one input was slower in one round, together with A, during outside
interference).

B remains about 4% faster than O, because its workers never wait for each other, but it
gives a different alignment on a quarter to half of the inputs from run to run. O costs
about 8% more CPU than A (speculative attempts that are recomputed, a second all-pairs
thread, and waiting), so for a backlog round 2's advice still holds: two independent
`--thread 1` jobs give the highest throughput. O is the choice when one job should finish
as fast as possible on an otherwise idle machine and its output must not depend on timing.


## The problem

L-INS-i refinement (`dvtditr`, `tditeration.c`) visits the branches of the
guide tree in a fixed order. For each branch it splits the alignment into two
groups, realigns them, and accepts the result if the objective improves. With
one worker each attempt starts from the alignment left by all earlier attempts.

The stock multi-worker implementation (`BAATARI2`) lets every worker take the
next branch and accept as soon as it finishes. Acceptances therefore happen in
completion order, which depends on thread timing; a worker whose starting
alignment changed underneath it is interrupted and restarted. The result is a
valid refinement but not a reproducible one. In the round-2 study, ordinary
two-thread refinement varied between repeats on 25 of 46 production inputs.
`--threadit 0` is repeatable but uses a different serial implementation and
differed from one-thread output on 24 of 46 inputs; `--threadit 1` matches the
one-thread output by running refinement on one worker.

## Ordered commits

Most attempts change nothing: on the 138-sequence job profiled for this work,
1,051 attempts were computed and 34 accepted. A later attempt can therefore be
computed speculatively, in parallel with the attempts before it, and it will
usually still be valid when its turn comes. The ordered mode keeps the stock
worker loop, branch order and interruption mechanism, and adds one rule:

- Every attempt knows its position in the generation's branch order.
- An attempt may be computed at any time, from a copy of the alignment and its
  subgeneration (the count of acceptances so far).
- It is committed only when every earlier position has committed. At that turn
  the worker checks the subgeneration. If an earlier position was accepted
  after the copy was taken, the attempt is recomputed from the current
  alignment, which no other worker can now change.
- Gains are recorded in the one-worker slot, so the coordinator's convergence
  and oscillation checks see what they would see with one worker.

Commit order equals one-worker order, and every committed attempt started from
the alignment the one worker would have used. The stock interruption check
still stops a speculative attempt early when an earlier position is accepted.
Only the worker holding the turn can change the alignment, so the identity
check, scoring and acceptance run without races between workers.

## Why the speculative result equals the one-worker result

The argument is the dependency argument of the
[terminal-reuse proof](round2-refinement-reuse.md): within its guarded scope a
branch attempt rebuilds every input it consumes from the current alignment and
the fixed tree. Its result is a function of the alignment and the branch, not
of which thread runs it or what that thread computed before. The intergroup
score cache, the one other piece of state carried between attempts, compares
row contents and is keyed on unordered row pairs; the same symmetric, finite
scoring-matrix check guards it.

The ordered mode is enabled only in that scope: DNA, L-INS-i-style constrained
refinement (`alg 'A'`, `constraint 2`, FFT decomposition), fixed tree and branch
order, seed zero, zero cut, no RNA or structure scoring, complete targets and
no score reporting. Outside it, `--thread N` behaves as stock MAFFT. FFT-NS-i
refinement, in particular, keeps its completion-order behavior.

The proof excludes one inherited corner: a block of fewer than two columns,
where the stock code reads gap scratch beyond its rebuilt prefix. The value it
reads is left by the previous call on the same thread, so a speculative worker
could see a different value from the one worker. `Falign_localhom` reports such
blocks. If an attempt about to be committed used one, every thread stops and
refinement restarts from its input with one worker, which reproduces the
`--thread 1` computation including that read. None of the production inputs
tested reached this fallback; it is exercised in tests by forcing it.

## Terminal reuse with ordered workers

The one-worker build reuses rejected attempts in the final no-gain tail of the
refinement (the round-2 memo). Ordered workers share the same structure under
the coordinator mutex. A worker decides a hit when it takes its position, with
the one-worker rule: the branch's stamp is current, and once per epoch the base
gain is zero and every later position's stamp is current. The decision can be
premature, because earlier positions may not have committed yet. It is kept
only if the subgeneration is unchanged at the worker's turn; otherwise the
attempt is computed. Acceptances are the only events that change the epoch,
the base gain or the stamps of positions not yet committed, so a decision that
survives to the turn is the one-worker decision. On the profiled job the
two-worker run reused the same 41 attempts as the one-worker run.

## Controls

- `MAFFT_ORDERED_REFINE=0` in the environment restores stock completion-order
  refinement for that run; `-DMAFFT_ORDERED_REFINE=0` removes the mode from a
  build.
- stderr reports `Ordered refinement: N workers commit in one-worker order.`
  when the mode is active, and `Ordered refinement: rerunning with one worker.`
  if the fallback ran.
- `-DMAFFT_ORDERED_TEST` builds add random delays that vary which worker
  computes, waits for or recomputes each position, and
  `MAFFT_ORDERED_TEST_UNSAFE=n` forces the fallback at the nth commit. Do not
  time those builds.

## Evidence

All on the Dikarya VM with the strict GCC build; outputs compared byte for byte.

- **Release build** (`harness/round2/build.py` from a clean archive of the release
  commit): the 100-input corpus with `--thread 1` matched all 100 goldens, so the
  one-thread path is unchanged; with `--thread 2` all 97 L-INS-i inputs matched, ordered
  mode active on all 97, no fallback.

- **Round-2 corpus, `--thread 2`**: 97/97 L-INS-i inputs identical to their frozen
  one-thread goldens, before and after ordered reuse was added; ordered mode was active on
  all 97 and the fallback never ran. The other three inputs (two FFT-NS-i `--auto` runs and
  one `--maxiterate 0` run) took the stock paths as intended; one FFT-NS-i output differed,
  as stock two-thread FFT-NS-i does.
- **Interleaving stress** (`-DMAFFT_ORDERED_TEST`, random delays at every copy and turn):
  11 development L-INS-i inputs x 2, 3 and 4 workers, 33/33 identical.
- **Forced fallback**: `MAFFT_ORDERED_TEST_UNSAFE` = 1, 40 and 400 on four inputs; all 12
  runs reran with one worker and were identical.
- **Other constrained strategies**: `--globalpair`, `--genafpair --ep 0` and `--localpair`
  on three inputs with 2 and 3 threads: 9/9 identical to `--thread 1`, which matched stock
  MAFFT 7.526 `--thread 1` in all 9.
- **Terminal reuse**: on the profiled 138-sequence job, the two-worker run reused the same 41
  attempts as the one-worker run.
- **ASAN/UBSAN** (`-O1`, test hooks on): three inputs with 2 and 3 workers and one forced
  fallback, no reports, all identical.
- **Latency runs above**: 139/139 in-scope (L-INS-i) runs under O identical to
  stock or golden output.

Raw run records are in the private archive (`round2/dikarya5-evidence/`,
`round2/dikarya5-validation-t1/`, `round2/dikarya5-validation-t2/`, build
`round2/dikarya5-build/`). The test hooks and the stress, fallback and family scripts are described in
[the round-3 harness notes](../harness/round3/README.md).

## Limits

- Exactness rests on the round-2 dependency argument plus these finite tests, not on a
  machine-checked proof.
- FFT-NS-i (`--maxiterate` without pairwise constraints), protein, RNA and the other
  excluded settings keep stock completion-order refinement and stay non-reproducible with
  more than one thread.
- Only this AVX2 VM was measured. Speedups depend on how many attempts are accepted (each
  acceptance forces the next worker to recompute) and on the number of free CPUs.
