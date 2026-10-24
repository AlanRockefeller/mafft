# Upstream report: heap read past `stra[]` in `searchAnchors` (MAFFT 7.526)

Not fixed in this fork: `searchAnchors()` is byte-identical to stock 0a2319b,
and the fork keeps stock behaviour. In `harness/exact/matrix.py` ASan runs,
FFT-NS-i and NW-NS-i on `test/exact/tiny.fa` therefore exit with the ASan
status in both builds and count as a matching rejection. Reproduced on
2026-10-24 with stock 0a2319b built by gcc 13.3 (`-O1 -g
-fsanitize=address,undefined -march=native -ffp-contract=off`). Not yet
sent; the text below is ready to send upstream.

---

**Subject:** heap-buffer-overflow read in `searchAnchors()` (mltaln9.c) when the
alignment is shorter than `divWinSize`

**Version:** MAFFT 7.526 (upstream commit `0a2319b`), Linux x86-64, gcc 13.3.

**Reproduce** (build with AddressSanitizer):

```sh
cd core
make CFLAGS="-O1 -g -fsanitize=address -fno-omit-frame-pointer" STRIP=true PREFIX=$HOME/mafft-asan
make install CFLAGS="-O1 -g -fsanitize=address -fno-omit-frame-pointer" STRIP=true PREFIX=$HOME/mafft-asan
printf '>t1\nA\n>t2\nAC\n>t3\nG\n>t4\nTT\n>t5\nACG\n>t6\nCA\n' > tiny.fa
ASAN_OPTIONS=detect_leaks=0 $HOME/mafft-asan/bin/mafft --maxiterate 100 tiny.fa > /dev/null
```

FFT-NS-i (`--maxiterate N`, without `--localpair`/`--globalpair`/`--genafpair`)
reaches `dvtditr`, which calls `searchAnchors( njob, seq_g, segment )`.
ASan reports:

```
ERROR: AddressSanitizer: heap-buffer-overflow ... READ of size 8
    #0 searchAnchors mltaln9.c:11314
    #1 main dvtditr.c:891
0x... is located 0 bytes after 32-byte region
allocated by: AllocateDoubleVec  <- searchAnchors mltaln9.c:11297
```

**Cause.** `stra` has `len = strlen( seq[0] )` elements, but the window sum

```c
for( j=0; j<divWinSize; j++ ) score += stra[j];
```

reads `divWinSize` (20 in dvtditr.c) elements whatever `len` is. With an
aligned length below 20 it reads past the allocation.

**Effect on output: none that we could find.** When `len <= divWinSize` the
following loop `for( i=1; i<len-divWinSize; i++ )` does not run, `status`
stays 0, and the function returns 0 without using `score`. It is still an
out-of-bounds read (undefined behaviour) and stops sanitizer runs.

**Suggested fix** (no output change, since `score` is unused in that case):

```c
for( j=0; j<divWinSize && j<len; j++ ) score += stra[j];
```

or return 0 early when `len <= divWinSize`.
