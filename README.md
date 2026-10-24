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

This fork makes MAFFT 7.526 faster under one constraint: for the same input and options, the
**output is byte-identical to unmodified MAFFT** built for the same platform and rounding class
([what is guaranteed](#what-is-guaranteed)). Downstream analyses calibrated on stock MAFFT
output are unaffected; only how fast the result arrives changes. On the 100-input Dikarya production corpus,
dikarya4 (by OpenAI Codex) is **1.45x faster than opt8** with `--thread 1`; on 56 later production inputs it
used **9.6x less CPU than stock MAFFT 7.526**, with all 56 outputs byte-identical to stock's.
dikarya5 adds ordered refinement, which gives DNA L-INS-i runs with `--thread 2` the
`--thread 1` alignment at **1.50x lower latency** than `--thread 1` (same 56 inputs). dikarya4
was deployed to Dikarya production on 2026-10-21 and dikarya5 on 2026-10-22; this branch is the
unreleased `7.526-opt9` (audit fixes and verification, no new speedup;
[changelog](CHANGELOG.md)).

It was built for two projects that align fungal ITS sequences in production:

- **A phylogenetics pipeline** running L-INS-i (`--localpair --maxiterate 1000`) on
  alignments of about 180 sequences, on Apple silicon and on AWS (x86 and Graviton).
- **[Dikarya](https://dikarya.us)**, a phylogenetics web service for fungal ITS barcodes, on
  an Intel Broadwell server without AVX-512 (the Dikarya host). Of the 100 production jobs in
  the round-2 corpus, 97 were direction-adjusted L-INS-i (`--adjustdirection --localpair
  --maxiterate 1000`), two direction-adjusted `--auto` and one an accurate-direction prepass
  ([corpus](paper/round2-performance.md)); `--auto` runs L-INS-i or FFT-NS-i depending on the
  input.

One source tree builds for Apple silicon, Linux arm64, x86-64 with AVX-512 or AVX2, and NVIDIA
GPUs. The vector code is chosen at compile time; everything else is shared.

## Install

Build into a prefix of your own. `make install` without `PREFIX` installs into `/usr/local`
and would replace a MAFFT already there.

```sh
cd core
make clean
make PREFIX=$HOME/inst/mafft-opt9 CC=gcc CFLAGS="-O3 -march=native"
make install PREFIX=$HOME/inst/mafft-opt9 CC=gcc CFLAGS="-O3 -march=native"
$HOME/inst/mafft-opt9/bin/mafft --version
```

Use the same `CC` and `CFLAGS` for both commands, chosen for your machine:

| machine | `CC` and `CFLAGS` |
|---|---|
| macOS, Apple silicon | `CC=clang` (default flags) |
| Linux arm64 (e.g. Graviton3/4) | `CC=clang CFLAGS="-O3 -mcpu=neoverse-v1"` |
| x86-64 with AVX-512 | `CC=clang CFLAGS="-O3 -march=x86-64-v4 -DMAFFT_STOCK_FMA=1"` |
| x86-64 with AVX2, no AVX-512 | `CC=gcc CFLAGS="-O3 -march=native"` |
| any Linux build with an NVIDIA GPU | add `CUDA=1` to both `make` commands |

- **macOS:** Accelerate and Metal are linked automatically. If Xcode's Metal compiler is
  installed, the GPU kernel is compiled at build time; otherwise at run time.
- **AVX-512:** on CPUs with VBMI2 (AMD Zen 4 and later, Intel Ice Lake and later), adding
  `-mavx512vpopcntdq -mavx512vbmi2` enables one more vector path; it was about 2% faster on
  Zen 4 and is how the c8a and c8i results in the [changelog](CHANGELOG.md#opt8-and-earlier)
  were built.
- **AVX2:** the gcc build is the Dikarya host's. clang works too, with `-DMAFFT_STOCK_FMA=1`
  when the target has FMA (see [rounding classes](#rounding-classes)); it is the CPU side of
  the g6 results.
- **CUDA:** needs the CUDA toolkit; `CUDA_HOME`, `NVCC` and `CUDA_ARCH` can be set. Pairs
  outside the kernel's limits, a missing GPU or any CUDA error fall back to the CPU.
- **Other targets** compile the same algorithms in scalar code. They were not verified;
  before relying on one, compare its output with a stock build made by the same compiler
  (`harness/verify-against-stock.sh`, or `harness/avx2/compare-builds.sh`, which runs six
  option sets).

**Check your build against stock** (needs a full clone, for upstream commit `0a2319b`):

```sh
harness/verify-against-stock.sh --cc gcc --cflags "-O3 -march=native"
```

It builds stock MAFFT 7.526 and this tree with the same compiler and flags into a temporary
directory, runs the 163-case fast matrix (every strategy, option set and thread setting on the
fixtures in `test/exact/`) and exits nonzero on any byte difference; `--full` runs 675 cases.
For clang on an FMA target add `--candidate-cflags -DMAFFT_STOCK_FMA=1`.

## Warnings

- **Don't add `-ffast-math` or `-ffp-contract=fast`** on any target: they change results.
- **clang on x86-64 with FMA** must be told the rounding class: the build stops with `#error`
  unless `-DMAFFT_STOCK_FMA=1` (stock built the same way contracts `a*b+c`) or
  `-DMAFFT_STOCK_FMA=0` with `-ffp-contract=off` for both builds is given.
- **Cache keys:** the version string doesn't record the compiler or rounding class, so if you
  cache alignments, include both in the cache key.
- **Multi-threaded runs outside the ordered scope** (below) are as non-reproducible as stock's.
- **Measured hardware:** the Dikarya rounds (dikarya2–dikarya5) were measured only on the
  Dikarya AVX2 VM. Other architectures build the same source; since opt8 they have been checked
  only by the fast matrix in CI (gcc on x86-64 and arm64 Linux, clang on x86-64), not timed.
  Metal and CUDA builds have not been rerun since opt8.

## What is guaranteed

"Stock" means unmodified MAFFT 7.526 (upstream commit `0a2319b`) built with the same compiler
and flags.

| run | output | reproducible |
|---|---|---|
| no `--thread` option (MAFFT's single-threaded default) | stock's, same options | yes |
| `--thread 1` | stock `--thread 1` | yes |
| `--thread N`, DNA L-INS-i, G-INS-i or E-INS-i (`--localpair`, `--globalpair`, `--genafpair` with `--maxiterate`), in the ordered scope | stock **`--thread 1`** | yes |
| `--thread N`, other iterative refinement (FFT-NS-i, protein, RNA, the options below) | stock's own multi-threaded refinement | no, as in stock |
| `--thread N`, no iterative refinement (e.g. FFT-NS-2) | stock's with the same threads; round 2 checked 72 non-iterative two-thread comparisons against opt8 | as in stock |
| Metal or CUDA build | the same build without the GPU (733,956 GPU alignments = CPU on the opt8 g6 test) | as above |

The ordered scope is decided at run time by `refinement_attempt_reproducible()` in
`core/tditeration.c`; stderr says `Ordered refinement: N workers commit in one-worker order.`
when it applies. Measured with a DNA input and `--localpair` or `--globalpair` with
`--maxiterate 2` (round 4), ordered refinement stays on with `--nofft`, `--kimura`, `--fmodel`, `--weighti`, `--retree`,
`--op`/`--ep`, `--lop`/`--lexp`, `--oldgenafpair`, `--adjustdirection`,
`--adjustdirectionaccurately`, `--reorder`, `--treeout`, `--anysymbol`, `--leavegappyregion`
and `--clustalout`. With 16 such option sets (`--maxiterate 1000`, including `--auto`) on three
DNA fixtures at `--thread 2` and `--thread 3`, all 96 runs used ordered refinement and were
byte-identical to stock `--thread 1` (gcc AVX2). These turn it off:

| option or input | why (condition in `refinement_attempt_reproducible()`) |
|---|---|
| protein input or `--amino` | DNA only (`dorp == 'd'`) |
| FFT-NS-i (`--maxiterate` without `--localpair`/`--globalpair`/`--genafpair`), and `--6merpair`, which overrides them | constrained refinement only (`constraint == 2`) |
| `--bestfirst` | branch-order parallel strategy only (`BAATARI2`) |
| `--unalignlevel` > 0, `--allowshift` (G-INS-i only; it sets `--unalignlevel 0.8`) | `specificityconsideration == 0` (`dvtditr -s`) |
| `--randomchain` | fixed guide tree and branch order (inferred from the run, not traced in the code) |
| `--threadit 0`, `--threadit 1` | refinement with one worker or none (nothing to order) |
| `--seed` | no seed alignments (`!nkozo`) |
| RNA structure options, score output, asymmetric scoring matrices | `!rnakozo`, `RNAscoremtx != 'r'`, `!scoreout`, symmetric scores |

`--memsave`, `--add` and `--addfragments` combined with L-INS-i or G-INS-i refinement exit with
an error in stock MAFFT and in this fork alike. Why ordered refinement is exact, and its
fallback to one worker, are in the [ordered-refinement report](paper/round3-ordered-refinement.md).

### Rounding classes

Stock MAFFT itself doesn't produce the same alignments on every platform. Apple's clang
fuses `a*b+c` into one rounding (FMA); gcc with the Makefile's `-std=c99` never does. On the
pipeline's test set, a gcc build on x86 differed from the Mac on about a third of inputs. This
fork matches **the stock build of the same platform and rounding class**:

- **arm64 with clang** (macOS or Linux): stock built with the same compiler, which fuses by
  default. Linux arm64 clang builds reproduce the Mac's alignments.
- **x86-64 with clang** and an FMA-capable `-march`: clang fuses too. Build this fork the same
  way plus `-DMAFFT_STOCK_FMA=1` and it matches that stock build (and, in our tests, the
  Mac's alignments).
- **gcc** (any target): stock gcc, no flag needed.

`MAFFT_STOCK_FMA` must match what the compiler does to the rest of the code: a clang build
for an FMA target *without* the flag matches neither class, which is why opt9 refuses to
build it.

## Runtime switches

None of these changes the output, except as noted for `MAFFT_ORDERED_REFINE`.

| variable | effect |
|---|---|
| `MAFFT_ORDERED_REFINE=0` (or `off`, `false`, `no`, any case) | stock completion-order multi-worker refinement instead of ordered refinement (`--thread N` output may then vary run to run, as in stock) |
| `MAFFT_REJECT_MEMO_STATS=1` | print `REJECT_MEMO hits=… misses=… unsafe=…` on stderr after `--thread 1` refinement (opt9) |
| `MAFFT_NOGPU` (any value) | Metal and CUDA builds run everything on the CPU |
| `MAFFT_GPU_SOURCE` (any value) | macOS: compile the Metal kernel from source at run time instead of using the built-in library |
| `L11GPU_VERBOSE`, `L11GPU_LOG=FILE` | GPU timing diagnostics on stderr, or one line per call appended to `FILE` |
| `MAFFT_NODUPCACHE` (any value) | turn off reuse of duplicate-pair local alignments |
| `MAFFT_NODENSEKMER` (any value) | turn off the dense six-mer counts |
| `MAFFT_ORDERED_TEST_UNSAFE=n` | `-DMAFFT_ORDERED_TEST` builds only: force the one-worker fallback at the nth commit |
| `MAFFT_BINARIES`, `MAFFT_TMPDIR` | as in stock MAFFT |

With `MAFFT_NODUPCACHE=1`, `MAFFT_NODENSEKMER=1`, `MAFFT_NOGPU=1` or
`MAFFT_REJECT_MEMO_STATS=1`, output on two duplicate-rich fixtures (`dna80dup`, `dna20alldup`)
with L-INS-i, FFT-NS-2 (`--retree 2`) and `--auto` was identical to stock (round 4, gcc AVX2).

## Threading and phylogenies

The round-2 [threading and phylogenetics study](paper/round2-phylogenetics.md) ran 610
alignments of the 46 production inputs that Dikarya had originally run with two threads, built
trees from them, and found a reproducibility problem, not an accuracy ranking:

- ordinary `--thread 2` refinement gave different alignments on repeated runs for **25/46**
  inputs;
- `--thread 2 --threadit 0` was repeatable but differed from the one-thread alignment on
  **24/46**;
- `--thread 2 --threadit 1` matched every one-thread golden (92/92 runs).

The trees reflect it: a fast IQ-TREE screen found topology differences between the one-thread
alignment's tree and the first `--thread 2` tree for **19/46** inputs, and against
`--threadit 0` for **24/46** (this 24 counts trees and is a separate measure from the 24
alignments above). The trees showed meaningful topology and resolution sensitivity to these
differences, and some strongly supported splits disappeared, but there is no independent
evidence that any threading setting is biologically more accurate. dikarya5's ordered refinement removes the choice for in-scope runs:
`--thread 2` gives the one-thread alignment, so the tree no longer depends on thread timing.

## How it works

Every change keeps the original arithmetic: the same operations on the same values in the
same order wherever floating point is involved, or integer arithmetic where MAFFT's scores
are provably integral. The main pieces, by stage of an L-INS-i run:

- **All-pairs local alignment** (`Lalign11.c`, about 60% of stock L-INS-i). An integer fill
  that stores a marker instead of the gap-start offset in cells where a gap wins, and
  recomputes those offsets along the one traceback path by replaying the original rule;
  vectorized for NEON, SVE, AVX2 and AVX-512, plus a striped (Farrar-layout) fill on AVX-512
  and now AVX2. The Dikarya follow-up also reuses exact duplicate ordered pairs.
  On Apple silicon and NVIDIA GPUs the same integer recurrence, tie rules and traceback run
  on the GPU (Metal, CUDA).
- **Refinement** (`dvtditr`, `partSalignmm.c`, `tddis.c`). A cache of per-pair scores that
  reuses a score while neither row has changed; closed-form pair scores (matrix products via
  Accelerate on macOS, exact integer sums elsewhere); vector DP rows whose multiply-adds
  round like the stock build's; the importance matrix filled in cache-sized bands and read
  back only over the columns written; substitution profiles skipping the letters absent from
  a column. dikarya4 reuses the final no-gain tail of refinement attempts, and dikarya5's
  workers commit attempts in one-worker order.
- **Bookkeeping everywhere.** Gap runs, residue maps, column profiles and anchor search
  scan 16–64 bytes at a time with byte-compare masks; gap-column removal moves runs instead
  of characters; work buffers are no longer cleared when every byte is written before it is
  read.

All vector code tests the feature macros defined once in `core/mafft_simd.h`; machines
without a vector unit run the same algorithms in scalar code.

## Verification

- **Against stock** ([`harness/verify-against-stock.sh`](harness/verify-against-stock.sh),
  [fixtures](test/exact/README.md)): stock and this tree built the same way, every strategy,
  option set and thread setting compared byte for byte; run in CI
  ([`.github/workflows/exact.yml`](.github/workflows/exact.yml)) for gcc scalar, gcc AVX2,
  clang FMA class, ASan+UBSan, the threaded ordered-refinement cases and gcc arm64.
- **Per-call harnesses** ([`harness/`](harness)) patch a scratch copy of the source so that
  every call of a rewritten function is also computed the original way, compared bit for bit,
  and aborted on the first difference. They run across L-INS-i with three gap settings, the
  protein `test/sample`, FFT-NS-i and placement (`--add`, `--addfragments`).
- **Whole alignments** are compared with stock references: the pipeline's 183-alignment
  test set, a canary kit of 13 L-INS-i windows and 12 placement cases, and on the Dikarya VM
  the 100-input production corpus and later production inputs.
- **Ordered refinement** is stressed with random delays, more workers than CPUs and forced
  one-worker fallbacks, and checked with ThreadSanitizer against stock
  ([round-4 report](paper/round4-audit-fixes.md#4-concurrency-audit-of-ordered-refinement-dikarya5)).
- **Object comparison** (`harness/objcheck.sh`) shows which compiled objects a change touches
  in each build configuration.

Per-version results are in the [changelog](CHANGELOG.md); the reports are indexed in
[`paper/README.md`](paper/README.md).

## Glossary

| term | meaning |
|---|---|
| stock | unmodified MAFFT 7.526 (upstream `0a2319b`) built with the same compiler and flags |
| rounding class | whether a build fuses `a*b+c` into one FMA rounding; output is compared only within a class |
| golden | a frozen reference output; the Dikarya goldens were frozen from opt8 and match stock `--thread 1` |
| L-INS-i, G-INS-i, E-INS-i | `--localpair`, `--globalpair`, `--genafpair` with `--maxiterate`: refinement constrained by pairwise alignments |
| FFT-NS-2, FFT-NS-i | progressive alignment (`--retree 2`), and the same with iterative refinement (`--maxiterate`) |
| `--auto` | MAFFT picks the strategy from the input size: L-INS-i, FFT-NS-i or FFT-NS-2 |
| refinement worker | a thread of iterative refinement; `--threadit` sets how many |
| ordered refinement | dikarya5's multi-worker refinement that commits attempts in one-worker order |
| A, B, C, F, O | the threading configurations of the round-2 and round-3 studies: `--thread 1`; `--thread 2` (stock refinement); `--thread 2 --threadit 0`; `--thread 2 --threadit 1`; `--thread 2` with ordered refinement |
| corpus, holdout | the 100 Dikarya production jobs used for timing (12 development, 78 holdout, 10 difficult) |
| canary | a small kit of L-INS-i windows and placement cases compared with stock references |
| optN, dikaryaN | releases for general targets (opt) and for the Dikarya host (dikarya); see the [changelog](CHANGELOG.md) |

## Versions and documentation

- [CHANGELOG.md](CHANGELOG.md): every release, its results, and why there is no dikarya3.
- [paper/README.md](paper/README.md): index of the technical reports.
- [harness/round2/DEPLOY.md](harness/round2/DEPLOY.md): the Dikarya deployment script, rollback,
  and the draft opt9 procedure.

## For the MAFFT maintainers

`git diff 0a2319b v7.526-opt8-dikarya5 -- core/` is the whole change of the latest release
against upstream `main` (`0a2319b`, the newest official source as of 2026-10-07); `git diff
0a2319b audit-fixes -- core/` includes the unreleased opt9 fixes. The feature tests are all in
`core/mafft_simd.h`, and each vector path keeps a scalar version of the same algorithm. Only
`core/` differs from upstream; `extensions/` is unchanged.

## Credits and license

- **opt1–opt5**: designed, implemented, verified and benchmarked by Claude Code (Claude Opus
  5.5, Anthropic), with Josh Walker advising and setting the research direction. See
  `paper/`.
- **dikarya1** (the AVX2 paths): designed, implemented, verified and benchmarked by Claude
  Code (Claude Opus 5.5, Anthropic), with Alan Rockefeller directing the work for Dikarya.
- **opt6–opt8**: Claude Code (Claude Opus 5.5, Anthropic), with Josh Walker advising, building
  on Alan Rockefeller's dikarya1 work. opt7 was written by three Claude Code agents in
  parallel, one per architecture, and merged by a coordinating session.
- **dikarya2 and dikarya4**: designed, implemented, verified and benchmarked by OpenAI
  Codex, with Alan Rockefeller directing the work for Dikarya.
- **dikarya5** (ordered refinement) and the independent check of dikarya4 against stock
  MAFFT on new production inputs: Claude Code (Claude Opus 5.5, Anthropic), with Alan
  Rockefeller directing.
- **opt9**: a collaboration between OpenAI Codex (max reasoning) and Claude Code (Claude Opus
  5.5, Anthropic), with Alan Rockefeller directing. opt9 builds on dikarya4, whose 1.45x over
  opt8 is Codex's work.
- MAFFT itself is by Kazutaka Katoh and colleagues. Please cite MAFFT as its authors ask
  (see the official site).

The code in `core/` is distributed under the BSD license in [`license`](license), and the
changes in this fork are offered under the same license. `extensions/` is covered by
[`license.extensions`](license.extensions).

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
