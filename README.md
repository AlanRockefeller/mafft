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
