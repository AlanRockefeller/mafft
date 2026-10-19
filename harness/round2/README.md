# Round 2 kernel experiments

Use a private artifact directory, retain the original goldens and stop competing
builds or experiments before timing. None of these drivers installs MAFFT.

The score-only rolling fill is in `core/l11_avx2.h`. Its public return values can
be checked against the original double recurrence with
`harness/broadwell/noalign_check.py` and the existing `L11_SCORE` driver, as
described in [the Broadwell harness](../broadwell/README.md).

Two additional AVX2 switches support independent experiments:

- `MAFFT_KB_SELF_SCORE=1` enables the guarded analytic score for identical strings.
  The default is off until production validation and measurements are completed.
- `MAFFT_KB_ZERO_EXT=1` enables an explicit zero-extension specialization of the
  full traceback row. The default is off until validation and measurements are
  completed. It shares the generic row body; the compiler receives a constant
  zero extension. Compare with `=0`, and check the resulting assembly.

For the analytic shortcut, every used character must have the same nonnegative
diagonal score M, every used substitution must be <=M, both gap penalties must be
nonpositive, and the local threshold must be <=M. The result is then exactly n*M:
the identity path attains the upper bound. Other settings retain the DP fill;
zero scores retain the public original-double fallback for signed-zero behavior.

Create a scratch copy and use the focused self-score checker:

```sh
scratch=$(mktemp -d)
cp core/Lalign11.c core/l11_avx2.h "$scratch"
python3 harness/broadwell/noalign_check.py "$scratch"
gcc -O3 -march=native -ffp-contract=off -std=c99 -Denablemultithread \
  -DMAFFT_KB_SELF_SCORE=1 -DMAFFT_POISON_TEST=1 \
  "-DL11_SOURCE=\"$scratch/Lalign11.c\"" -Icore \
  -ffunction-sections -fdata-sections harness/round2/self_score_check.c \
  core/defs.o -Wl,--gc-sections -lm -lpthread -o "$scratch/self_score_check"
"$scratch/self_score_check" 10000
```

The wrapper checks all 64 return-value bits against the original double fill.
The focused generator includes eligible self pairs, unequal lengths, nonuniform
diagonals, dominating off-diagonal scores, positive gap penalties/extensions,
large positive thresholds, signed zeros, fractional scores, ambiguity codes and
protein alphabets. Add ASAN/UBSAN flags for memory/undefined-behavior checks.

`batch_dp.c` is an experimental driver, with no production dispatch. It puts
eight independent score-only matrices in eight AVX2 lanes and masks unequal
sequence lengths. A shared query permits ordinary packed profile loads;
independent query characters use gathers. Its checker compares all raw integer
scores against both rolling striped DP and an independent scalar double
transcription. The raw zero scores are compared numerically with scalar double;
the separate public-call checker covers signed-zero fallback.

```sh
gcc -O3 -march=native -ffp-contract=off -std=c99 -Denablemultithread \
  -ffunction-sections -fdata-sections harness/round2/batch_dp.c core/defs.o \
  -Wl,--gc-sections -lm -lpthread -o "$scratch/batch_dp"
"$scratch/batch_dp" check 2000
"$scratch/batch_dp" bench 64 700 700 shared
"$scratch/batch_dp" bench 64 700 700 independent
"$scratch/batch_dp" bench 64 700 700 ragged
"$scratch/batch_dp" fasta /private/input.fasta 8 shared
```

FASTA mode benchmarks actual sequence shapes with an explicitly synthetic
+600/-450 nucleotide scoring matrix, zero gap extension and -1,530 gap opening.
It is a kernel experiment, not a whole-MAFFT performance or biological result.
The timing includes profile packing and arithmetic/alphabet guard checks and
alternates implementation order over three rounds. `valid_cells` and
`padded_cells` expose wasted work from length imbalance. FASTA normalization in
this driver uppercases letters and drops preexisting `-`/`.` gaps; it never
rewrites the input file or creates a biological alignment.
