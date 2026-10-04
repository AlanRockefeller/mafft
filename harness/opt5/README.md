# opt5 per-call verification harnesses

Each script rewrites one opt5 source file in a scratch copy of `core/` so that every call
of the rewritten function runs the vector/new path and the original scalar path on the same inputs,
compares all outputs bit for bit, and aborts on the first difference. They report "all identical"
counts on stderr (at power-of-two call counts; strip `\r` from dvtditr's progress output to read
them).

| script | function | file |
|---|---|---|
| lfill_check.py | Lfill_int (AVX-512/SSE4.1 row scan, rowmax search) | Lalign11.c |
| row_check.py | partA_row, A_row (row fills, MI prefix scans) | partSalignmm.c, Salignmm.c |
| mcmatch_check.py | mc_match | partSalignmm.c |
| areg_check.py | alignableReagion (presence/profile passes) | fftFunctions.c |
| gapruns_check.py | gapruns | mltaln9.c |
| cgp_check.py | commongappick_record | mltaln9.c |
| igs_check.py | igs_pairscores (gathered int sums) | mltaln9.c |
| fillimp_check.py | banded fillimp_track (rejected, not in opt5) | mltaln9.c |

Usage: copy the source tree, run `python3 SCRIPT.py <copy>/core` for each check wanted (they can be
combined), build normally (e.g. `make CC=clang CFLAGS="-O3 -march=x86-64-v4 -DMAFFT_STOCK_FMA=1"`),
then run MAFFT on small inputs in several modes (L-INS-i with default and changed --lop/--lexp,
G-INS-i, FFT-NS-2; DNA and the bundled protein test/sample) and also compare end to end with stock.

These scripts patch the opt5 sources textually (tag `v7.526-opt5`) and may not apply to later
revisions. Section "Verification" of the report (`paper/mafft-apple-silicon.pdf`) describes
how they were used.
