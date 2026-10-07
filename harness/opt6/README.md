# opt6 per-call verification harness

`marks_check.py` rewrites `core/Lalign11.c` in a scratch copy of the source so that every call
of the marker-based `Lfill_int` (the AVX2 fill from `v7.526-opt5-dikarya1`, which opt6 also
uses in AVX-512 builds) runs twice on the same inputs:

1. with its vector loops off, so the scalar tail fills every cell of every row, into a scratch
   `ijp`;
2. with them on, into the real `ijp`.

The return value, the maximum score, the end point, every `ijp` cell (markers, 0 and
`localstop`) and every stored DP row that `Ltracking` replays must match bit for bit, or the
program aborts. The first-index search for each row's maximum is gated the same way. Each
process reports `MARKS_CHECK: N calls, M cells, all identical` on stderr at exit (strip `\r`
from the progress output to read it). The script also gates 16-wide loops, so it applied
unchanged to a 16-lane AVX-512 variant that was tried and not adopted.

Usage: copy the source tree, run `python3 marks_check.py <copy>/core`, build an AVX2 or AVX-512
configuration (for example `make CC=clang CFLAGS="-O3 -march=x86-64-v4 -DMAFFT_STOCK_FMA=1"` or
`make CC=gcc CFLAGS="-O3 -march=x86-64-v3"`), then run L-INS-i (`--localpair`) without `--quiet`.

The script patches the source textually and may not apply to later revisions.
