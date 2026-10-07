# MAFFT 7.526 with exact speedups (unofficial fork)

> [!WARNING]
> **This is an unofficial fork of MAFFT. It is not affiliated with or endorsed by the MAFFT
> authors.**
>
> - Official MAFFT: **https://mafft.cbrc.jp/alignment/software/**
> - Official source: **https://gitlab.com/sysimm/mafft**
>
> For general use, install the official release. Please don't report problems with this
> fork to the MAFFT developers. Open an issue here instead.

## AVX2 additions (`v7.526-opt5-dikarya1`)

The opt5 x86 kernels need AVX-512 (`-march=x86-64-v4`). On x86 CPUs without it (every
Intel server part before Skylake-SP, and many cloud and desktop machines today), opt5 falls
back to its SSE4.1 and scalar paths, which leaves most of the run time where it was in stock MAFFT.
`v7.526-opt5-dikarya1` adds AVX2 paths for that class of machine, under the same rule as
the rest of this fork: **output byte-identical to stock MAFFT built the same way and run
with `--thread 1`.**

They were written for, and measured on, the server that runs
[Dikarya](https://dikarya.us), a phylogenetics web service for fungal ITS barcodes:

- Intel Xeon E5-2690 v4 (Broadwell-EP), 2 vCPUs of a VM, AVX2 + FMA, no AVX-512
- Ubuntu 24.04, gcc 13.3, `make CC=gcc CFLAGS="-O3 -march=native"` (gcc with the Makefile's
  `-std=c99` never contracts `a*b+c`, so `MAFFT_STOCK_FMA` is 0 and every vector multiply-add
  below is a separate multiply and add)

All new code is guarded by `__AVX2__ && !MAFFT_STOCK_FMA` (and `!__AVX512F__` where opt5
already has an AVX-512 path), so builds for other targets compile exactly the code they did
before.

### What changed

| where | change | effect on this machine |
|---|---|---|
| `Lalign11.c`: `Lfill_int`, `Ltracking` | An AVX2 integer fill, 8 cells per step. A cell whose best move is a gap stores a marker instead of the gap's start offset; every DP row is kept instead of two alternating ones; `Ltracking`, which reads `ijp` only along its one path, recomputes the offset for a marked cell by replaying the original scalar rule over the stored rows. The arithmetic is integer, so the offsets are exactly the ones the full fill would store. This removes the first-index prefix scan and the `vmp[]` state from the inner loop. | 1.68 to 0.82 ns per DP cell (the all-pairs local alignments are ~60% of L-INS-i) |
| `mltaln9.c`: `scarr_fill()`; `Salignmm.c`, `partSalignmm.c`, `Dalignmm.c`, `SAalignmm.c`; `mc_match` | The 26x26 profile-score loop at the top of every `match_calc` (`scarr[l] += mtx[j][l] * cpmx1[j][i1]`): gcc vectorised it as gathers plus a serial add chain. It now skips the letters absent from the column (exact: the sum starts at +0, and under round-to-nearest a sum of nonzero terms is never -0, so adding +0 or -0 never changes it) and does 4 letters at a time, each lane with the same multiply-then-add sequence in the same order. | most of `A__align`, i.e. of FFT-NS-i |
| `Falign.c`, `mtxutl.c`: `AllocateCharMtxNoZero()` | `Falign` allocated eight `njob x alloclen` character matrices per call with `calloc`, i.e. cleared ~6 MB per call. They are only ever used as C strings written before they are read (`rndseq` is filled whole), so the rows are now `malloc`ed. `-DMAFFT_POISON_TEST` fills them with junk instead, which is how that claim was checked. | ~25% of an FFT-NS-i run |
| `Salignmm.c` `A_row`, `partSalignmm.c` `partA_row` | AVX2 (4 doubles) versions of opt5's AVX-512-only row fills. | small |
| `Lalign11.c` | 8-wide search for the first cell holding a row maximum. | small |

Tried and dropped: a straight AVX2 port of the opt5 `Lfill_int` prefix scans (only 1.17x
over SSE4.1: the loop is bound by the number of vector uops per cell, not by width); a
variant that computed the gap offsets only for blocks that needed them (slower: on related
ITS sequences gaps win across large areas, and the branch mispredicts); restructuring the
scan to shorten the loop-carried chain (no change).

### Results on the machine above

14 real inputs from the service (49-444 ITS/LSU sequences), `--adjustdirection --auto`,
so `--auto` chose L-INS-i-style local pairs for 10 of them and FFT-NS-i for 4. Seconds,
single runs:

| | stock 7.526 | `opt5` | `opt5-dikarya1` | vs. stock | vs. opt5 |
|---|---|---|---|---|---|
| `--auto` chose local-pair (10 inputs), `--thread 1` | 361 | 97 | 56 | 6.4x | 1.7x |
| `--auto` chose FFT-NS-i (4 inputs), `--thread 1` | 242 | 207 | 107 | 2.3x | 1.9x |
| all 14, `--thread 2` | not run | 199 | 99 | n/a | 2.0x |
| full L-INS-i, 176 seqs, `--thread 1` | 93.2 | 23.9 | 13.9 | 6.7x | 1.7x |

### How it was verified

- Byte-identical alignments to stock MAFFT (Ubuntu's 7.505 package and a gcc build of
  7.526) at `--thread 1` on 52 real inputs, both as built and with `-DMAFFT_POISON_TEST`.
- `harness/avx2/compare-builds.sh` runs six option sets (`--auto`, L-INS-i, G-INS-i,
  FFT-NS-i, FFT-NS-2 with `--retree 2`, `--adjustdirection`) against a reference build.
  The ones run were a synthetic DNA family, `test/sample` (protein) and three real inputs:
  all identical to stock 7.526.
- Multi-threaded runs are not reproducible in any MAFFT build, so `--thread 2` was checked
  for completion, not identity.

### For the MAFFT maintainers

This branch is upstream `main` (`0a2319b`, the newest official source as of 2026-10-07)
plus the opt1-opt5 series plus one commit for the AVX2 work, so
`git diff 0a2319b v7.526-opt5-dikarya1 -- core/` is the whole change against upstream, and
`git diff v7.526-opt5 v7.526-opt5-dikarya1 -- core/` is the AVX2 part on its own. Each part keeps the
original code as the fallback for every other target.

### Credits

- **opt1-opt5** (everything up to `v7.526-opt5`): designed, implemented, verified and
  benchmarked by Claude Code (Claude Opus 5.5, Anthropic), with Josh Walker advising and
  setting the research direction. See the section below and `paper/`.
- **dikarya1** (the AVX2 additions above): designed, implemented, verified and benchmarked
  by Claude Code (Claude Opus 5.5, Anthropic), with Alan Rockefeller directing the work for
  Dikarya.
- MAFFT itself is by Kazutaka Katoh and colleagues; please cite MAFFT as its authors ask.

---

This fork adds performance changes to MAFFT 7.526 under one constraint: for the same input
and options, the **output must be byte-identical to unmodified MAFFT** built for the same
platform and run with `--thread 1`. Downstream analyses calibrated on stock MAFFT output
are unaffected. Only how fast the result arrives changes.

The work targeted one workload: L-INS-i (`--localpair --maxiterate 1000`) on fungal ITS
DNA, typically about 180 sequences per alignment. Results on that workload:

| platform | build | L-INS-i speedup vs. stock 7.526 |
|---|---|---|
| Apple M1 (macOS) | `v7.526-opt4` / `opt5` | 6.5× single run, 4.6× at 8 concurrent runs |
| AWS c7i / c7a (Sapphire Rapids, Zen 4) | `v7.526-opt5` | 5.7–7.0× single run, 5.1–5.9× at 4 concurrent runs |
| Intel Xeon E5-2690 v4 (Broadwell, AVX2, no AVX-512), gcc | `v7.526-opt5-dikarya1` | 6.4x single run on `--auto` L-INS-i jobs, 5.5-6.9x on full L-INS-i (see [below](#avx2-additions-v7526-opt5-dikarya1)) |

The speedups depend on the workload and the hardware: sequence count and length, the
alignment strategy, cache sizes and vector units. Other inputs may gain much less. The
technical report [`paper/mafft-apple-silicon.pdf`](paper/mafft-apple-silicon.pdf) describes
every change, how exactness was verified, the results, and what didn't work. The per-call
verification harnesses for the opt5 kernels are in [`harness/opt5`](harness/opt5).

## What "identical to stock" means

Stock MAFFT itself doesn't produce the same alignments on every platform. Apple's clang
fuses `a*b+c` into one rounding (FMA). gcc with the Makefile's `-std=c99` never does. On
our test set, a gcc build on x86 differed from the Mac on about a third of inputs. This
fork matches **the stock build of the same platform and rounding class**:

- On **arm64 with clang**, it matches stock built with the same compiler (fused
  multiply-adds, the default).
- On **x86-64**, clang with an FMA-capable `-march` fuses too. Build this fork the same way
  plus `-DMAFFT_STOCK_FMA=1`, and it matches that stock build. In our tests, those
  alignments also matched the Mac's. Built with gcc (no flag), it matches stock gcc
  instead. The flag must match what the compiler does to the rest of the code. A clang
  build for an FMA target *without* the flag matches neither.

Multi-threaded runs (`--thread` > 1) of the iterative methods are not reproducible even in
stock MAFFT. The guarantee covers single-threaded runs only.

## Building

The build is the same as upstream (see below), with these additions:

- **macOS (Apple silicon).** `cd core && make CC=clang && make install`. The Makefile links
  Accelerate and Metal automatically. If Xcode's Metal compiler is installed, the GPU kernel
  is compiled at build time; otherwise MAFFT compiles it at run time. Setting `MAFFT_NOGPU=1`
  disables the GPU path.
- **x86-64 with AVX-512** (verified on Intel Sapphire Rapids and AMD Zen 4):

      cd core
      make CC=clang CFLAGS="-O3 -march=x86-64-v4 -DMAFFT_STOCK_FMA=1"
      make install

  Don't add `-ffast-math` or `-ffp-contract=fast`, since they change results. The version
  string doesn't record the compiler or rounding class. If you cache alignments, include the
  compiler and rounding class in the cache key.
- **Other targets.** The vector paths are selected at compile time (`__ARM_NEON`,
  `__AVX512F__`/`__AVX512BW__`/`__AVX512VL__`, `__SSE4_1__`). Without them, the original
  scalar code is used, along with the portable algorithmic changes. These configurations
  compile but were not verified. Before relying on one, compare its output with a stock build
  made by the same compiler.

Only `core/` changed. `extensions/` is identical to upstream.

## Versions

The changes are a linear series of commits on the `exact-speedups` branch, on top of
upstream `main` (7.526 plus commits from December 2025). Each build is tagged:

| tag | `mafft --version` | summary |
|---|---|---|
| `v7.526-opt1` | `v7.526` | bookkeeping fixes, NEON DP rows, matrix-product scores via Accelerate |
| `v7.526-opt2` | `v7.526-opt2` | refinement pair-score cache, vector prefix scan, run-based counts |
| `v7.526-opt3` | `v7.526-opt3` | all-pairs local alignment on the GPU (Metal) |
| `v7.526-opt4` | `v7.526-opt4` | shader compiled at build time, occupancy tuning |
| `v7.526-opt5` | `v7.526-opt5` | AVX-512 kernels for x86-64, multiply-add rounding matched per platform |
| `v7.526-opt5-dikarya1` | `v7.526-opt5-dikarya1` | AVX2 paths for x86-64 without AVX-512: marker-based `Lfill_int`, sparse `scarr_fill`, uncleared `Falign` work rows ([details](#avx2-additions-v7526-opt5-dikarya1)) |

`v7.526-opt1` reports plain `v7.526`, so it can't be told apart from stock by version.
Prefer a later tag.

## Authorship and license

The changes were designed, implemented, verified and benchmarked by Claude Code (Claude Opus
5.5, Anthropic), with Josh Walker advising and setting the research direction. See the
report for details.

MAFFT is copyright Kazutaka Katoh. The code in `core/` is distributed under the BSD license
in [`license`](license), and the changes in this fork are offered under the same license.
`extensions/` is covered by [`license.extensions`](license.extensions). Please cite MAFFT as
the MAFFT authors request (see the official site).

---

*The upstream README follows, unchanged.*

# MAFFT version 7.526
Multiple sequence alignment program
<br>
https://mafft.cbrc.jp/alignment/software/

## COMPILE
     % cd core
     % make clean
     % make
     % cd ..

If you have the `./extensions` directory, which is for RNA alignments,

     % cd extensions
     % make clean
     % make
     % cd ..


## INSTALL (select a or b below)
###  a. Install to /usr/local/ using root account
     # cd core
     # make install
     # cd ..

If you have the `./extensions` directory,

     # cd extensions 
     # make install
     # cd ..

By this procedure (a), programs are installed into `/usr/local/bin/`. Some binaries, which are not directly used by a user, are installed into `/usr/local/libexec/mafft/`.

If the MAFFT_BINARIES environment variable is set to `/somewhare/else/`, the binaries in this directory are used, instead of those in `/usr/local/libexec/mafft/`.

### b. Install to non-default location (root account is not necessary)
     % cd core/
          Edit the first line of Makefile 
          From:
          PREFIX = /usr/local
          To:
          PREFIX = /home/your_home/somewhere

          Edit the third line of Makefile 
          From:
          BINDIR = $(PREFIX)/bin
          To:
          BINDIR = /home/your_home/bin 
                   (or elsewhere in your command-search path)
     % make clean
     % make
     % make install

If you have the `./extensions` directory,

     % cd ../extensions/
          Edit the first line of Makefile 
          From:
          PREFIX = /usr/local
          To:
          PREFIX = /home/your_home/somewhere
     % make clean
     % make
     % make install

The `MAFFT_BINARIES` environment variable *must not be* set.

If the `MAFFT_BINARIES` environment variable is set to `/somewhare/else/`, it overrides the setting of `PREFIX` (`/home/your_home/somewhere/` in the above example) in Makefile.

## CHECK
     % cd test
     % rehash                                                   # if necessary
     % mafft sample > test.fftns2                               # FFT-NS-2
     % mafft --maxiterate 100  sample > test.fftnsi             # FFT-NS-i
     % mafft --globalpair sample > test.gins1                   # G-INS-1 
     % mafft --globalpair --maxiterate 100  sample > test.ginsi # G-INS-i 
     % mafft --localpair sample > test.lins1                    # L-INS-1 
     % mafft --localpair --maxiterate 100  sample > test.linsi  # L-INS-i 
     % diff test.fftns2 sample.fftns2
     % diff test.fftnsi sample.fftnsi
     % diff test.gins1 sample.gins1
     % diff test.ginsi sample.ginsi
     % diff test.lins1 sample.lins1

If you have the `./extensions` directory,

     % mafft-qinsi samplerna > test.qinsi                       # Q-INS-i
     % mafft-xinsi samplerna > test.xinsi                       # X-INS-i
     % diff test.qinsi samplerna.qinsi
     % diff test.xinsi samplerna.xinsi

If you use the multithread version, the results of iterative refinement methods (`*-*-i`) are not always identical.  So try this test in the single-thread mode (`--thread 0`).


## INPUT FORMAT
Fasta format.

The type of input sequences (nucleotide or amino acid) is automatically recognized based on the frequency of A, T, G, C, U and N.


##  USAGE
     % /usr/local/bin/mafft input > output

See also https://mafft.cbrc.jp/alignment/software/


## UNINSTALL
     # rm -r /usr/local/libexec/mafft
     # rm /usr/local/bin/mafft
     # rm /usr/local/bin/fftns
     # rm /usr/local/bin/fftnsi
     # rm /usr/local/bin/nwns
     # rm /usr/local/bin/nwnsi
     # rm /usr/local/bin/linsi
     # rm /usr/local/bin/ginsi
     # rm /usr/local/bin/mafft-*
     # rm /usr/local/share/man/man1/mafft*


## LICENSE
See `./license` and `./license.extensions`.
