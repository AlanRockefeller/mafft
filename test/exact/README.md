# Public fixtures for byte-identity checks

Small, non-sensitive inputs for [`harness/verify-against-stock.sh`](../../harness/verify-against-stock.sh)
and [`harness/exact/matrix.py`](../../harness/exact/matrix.py). They replace, for public
verification, the private Dikarya production corpus that most reports in `paper/` use.
There are no expected outputs here on purpose: output legitimately differs between
compilers and rounding classes, so the check is always against stock MAFFT 7.526 built in
the same job with the same flags.

| File | Contents | Made by |
| --- | --- | --- |
| `dna40.fa` … `dna12small.fa`, `aa60.fa`, `aa40div.fa`, `aa12small.fa` | simulated families: duplicates, reverse complements, N runs, IUPAC codes, lower case, fragments, outliers, near-identical and all-duplicate sets, one 6 × 5 kb case, short protein families | `harness/exact/simseq.py` with the seeds in [`recipes.txt`](recipes.txt) |
| `one.fa` | a single sequence | `make_fixtures.py` (literal) |
| `tiny.fa` | one- and two-residue sequences | `make_fixtures.py` (literal) |
| `gappedu.fa` | a pre-gapped RNA-style input with U | `make_fixtures.py` (literal) |
| `add-base.aln` | sequences 1–25 of `dna40.fa`, aligned once with stock 7.526 FFT-NS-2 (`--thread 1`) | committed as an input |
| `add-new.fa` | sequences 26–33 of `dna40.fa` (for `--add`, `--addfull`, `--seed`) | committed |
| `add-frag.fa` | 60–200 bp fragments of sequences 34–40 (for `--addfragments`) | committed (Python `random.Random(41)`) |
| `add-long.fa` | sequences 34–36 with 300 random bases on each side (for `--addlong`) | committed (same generator) |

`python3 harness/exact/make_fixtures.py --check` confirms that the simulated and literal
files match their recipes. `test/sample` (upstream) is used as well.

What these fixtures cover, and do not: every strategy and option set in `matrix.py`,
single-thread, default-thread and ordered `--thread 2..4` identity, random-delay and
forced-fallback builds, and small edge cases. They are far smaller than production inputs
(the largest is 120 × 600 bp), so they say little about performance, memory use or rare
paths that need large inputs, such as the GPU precompute.
