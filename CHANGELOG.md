# Changelog

Each released version is a tag on the `exact-speedups` branch, a linear series on upstream
`main` (7.526 plus commits from December 2025). `mafft --version` reports the tag's suffix
(`v7.526-opt1` reports plain `v7.526`). Use the newest unless you need to reproduce an older
build. Newest first. Terms are explained in the
[glossary](README.md#glossary).

## `v7.526-opt9m` — opt9 with a larger GPU memory cap

opt9 (below, unchanged otherwise) with two changes for Apple silicon, plus measurements of
opt9 on the pipeline's machines.

- **GPU precompute cap raised from 256 MiB to 1 GiB**, and `MAFFT_GPU_MAXMB` (MiB) sets
  another value. At 256 MiB the M1 skipped the GPU on the pipeline's largest L-INS-i windows
  (532 and 498 ITS sequences need 256–320 MiB): the all-pairs stage of the 532-sequence window
  took 24–26 s instead of 8.3 s. With the GPU on, that window peaks at about 460 MB against
  290 MB for stock; 1 GiB is about opt8's limit. Only Metal and CUDA builds contain this code;
  CPU builds compile exactly as opt9. Output is unchanged (the cap only chooses the GPU or the
  CPU, which agree).
- `harness/verify-against-stock.sh` runs under macOS's bash 3.2 (it stopped with
  `extra[@]: unbound variable`).

**opt9 on the pipeline's L-INS-i test set** (real ITS alignments of about 180 sequences,
`--thread 1` per run): a batch of 16 run four at a time on 4-vCPU AWS instances, eight at a
time on the M1, plus single runs; stock, opt8 and opt9 interleaved in one session per machine
(2026-10-27). On these inputs the GPU cap does not apply, so opt9 and opt9m run the same way.

| machine | batch: stock / opt8 / opt9 | opt9 vs stock | single runs: stock / opt8 / opt9 | opt9 vs stock | opt9 vs opt8 |
|---|---|---|---|---|---|
| Apple M1, clang, with the Metal GPU | 324.5 / 60.0 / 57.5 s | 5.6× | 1051.9 / 128.8 / 120.5 s | 8.7× | 1.04–1.07× |
| AWS c7a (Zen 4), clang AVX-512 | 408.3 / 48.0 / 46.7 s | 8.7× | 206.3 / 20.0 / 19.3 s | 10.7× | 1.03–1.04× |
| AWS c7i (Sapphire Rapids), clang AVX-512 | 622 / 88 / 84 s | 7.4× | 188.3 / 24.5 / 22.8 s | 8.3× | 1.05–1.07× |
| AWS c8g (Graviton4), clang | 377.3 / 77.3 / 74.0 s | 5.1× | 174.0 / 32.2 / 30.7 s | 5.7× | 1.05× |

With `--adjustdirection` the batch times were within 1–3% of these on every machine. Every
timed output matched stock's. Verification of opt9 on these machines: canary 25/25 everywhere
(M1 with and without the GPU); the 183-alignment test set = Mac references on c7a, c7i and
c8g; the 163-case fast matrix of `verify-against-stock.sh` 163/163 on all four;
`--adjustdirection` outputs = stock.

## `7.526-opt9` — not tagged in this fork (released here as opt9m, above)

An exact banded all-pairs fill, audit fixes and verification infrastructure. Details and
evidence: [round-4 report](paper/round4-audit-fixes.md).

- **Exact banded all-pairs local DP** (AVX2 builds without AVX-512). A narrow first pass
  proves a lower bound on each pair's score; by the band certificate, only the cells within
  the resulting band are then filled. Pairs where that would not pay take the unchanged full
  fill. On the 100-input Dikarya production corpus with `--thread 1`, **1.10x less CPU**
  than without the band (holdout 1.10x, development 1.17x, difficult 1.07x), every output
  identical. `MAFFT_NOLBAND` turns it off; `MAFFT_LBAND_CHECK` runs every pair both ways and
  stops on a difference.
- An AVX2 gather in the importance row read: 1.9% less CPU on a refinement-heavy fixture, no
  measurable change on production inputs.
- The CLUSTAL header prints stock's `v7.526`, so `--clustalout` output is byte-identical to
  stock. Through dikarya5 it said `7.526-opt8`, so that CLUSTAL files stayed identical to
  opt8's.
- The `REJECT_MEMO hits=… misses=… unsafe=…` line, which every `--thread 1` refinement printed
  on stderr, is printed only with `MAFFT_REJECT_MEMO_STATS` set to a value other than `0`,
  `off`, `false` or `no` (for example `1`).
- `MAFFT_ORDERED_REFINE` accepts `0`, `off`, `false` and `no` in any case; through dikarya5
  only `0` turned it off.
- clang on x86-64 with FMA stops with `#error` unless `-DMAFFT_STOCK_FMA` says which rounding
  class the stock reference is (a guess silently differed from stock).
- Every allocation the fast paths added is checked; an allocation failure takes the original
  computation where the function has one, else exits as stock's allocators do. The GPU
  all-pairs precompute is skipped (every pair on the CPU) when it would need more than 256 MB
  of host memory (1 GiB in opt9m).
- A restored EUC-JP comment, a deleted unreachable AVX-512 block, the version string
  `v7.526-opt9 (2026/Oct/24)`.
- New verification: `harness/verify-against-stock.sh` (builds stock and this tree with the same
  flags and compares output byte for byte), the public fixtures in `test/exact/`, a CI workflow,
  ordered-refinement stress and ThreadSanitizer triage tools, and harness runners whose resumes
  cannot hide failures.

## `v7.526-opt8-dikarya5` — reproducible two-thread refinement

Stock MAFFT's multi-threaded iterative refinement is not reproducible: workers accept
improvements in whatever order they finish, so `--thread 2` can give a different alignment
on every run. dikarya5 makes the refinement workers **commit their attempts in the order a
single worker would**, recomputing the rare attempt whose starting alignment changed. The
result is that, for DNA runs of L-INS-i, G-INS-i and E-INS-i (`--localpair`,
`--globalpair`, `--genafpair` with `--maxiterate`), **`--thread N` produces exactly the
`--thread 1` alignment**, which is itself byte-identical to stock MAFFT 7.526 with
`--thread 1` — while refinement still uses every thread.

On the Dikarya VM (two vCPUs), with every job given both CPUs:

| | `--thread 1` | `--thread 2 --threadit 1` | stock-order `--thread 2` | **dikarya5 `--thread 2`** |
|---|---:|---:|---:|---:|
| 12-input latency cohort, mean seconds | 4.22 | 3.62 | 2.76 | **2.87** |
| 56 new production inputs, mean seconds | 4.96 | 4.26 | 3.19 | **3.31** |
| speedup over `--thread 1` (56 inputs) | 1.00x | 1.16x | 1.56x | **1.50x** |
| L-INS-i runs identical to stock `--thread 1` | 106/106 | 106/106 | 78/106 | **106/106** |

So a two-thread job now finishes about 1.5x faster than a one-thread job, within 4% of
stock's non-reproducible two-thread refinement, and 1.26x faster than the previous
reproducible option (`--threadit 1`). The 97 L-INS-i inputs of the round-2 corpus matched
their one-thread goldens with 2 threads, and the 11 development L-INS-i inputs with 2, 3 and 4
threads under randomly injected delays. Those goldens were frozen from opt8; a fresh build of
stock MAFFT 7.526 with `--thread 1` reproduced all 100 of them byte for byte. It uses about 8%
more CPU than `--thread 1`, so for a backlog of jobs, independent `--thread 1` jobs still give
more alignments per hour.

How it works, why it is exact, and its scope are in the
[ordered-refinement report](paper/round3-ordered-refinement.md). Outside that scope
(FFT-NS-i refinement, protein, RNA, `--bestfirst` and similar) `--thread N` behaves as stock
MAFFT. `MAFFT_ORDERED_REFINE=0` in the environment restores the stock behavior.

Production: the Dikarya command `/opt/mafft-7.526-opt8/bin/mafft` was switched to the dikarya5
release by `harness/round2/deploy.py` on 2026-10-22 at 05:59 UTC (its activation link), and
Dikarya's job logs report `Version 7.526-opt8-dikarya5` from that day on.

## `v7.526-opt8-dikarya4` — round 2

dikarya4 adds rolling score-only DP, a guarded identical-sequence score shortcut,
zero-extension traceback specialization, exact raw-profile reuse, direct gap-aware pair
scoring, immutable six-mer histograms and exact reuse of the final no-gain tail of
refinement attempts. On the 100-input Dikarya production corpus, `--thread 1`, it is
**1.45x faster than opt8** and 1.084x faster than dikarya2 in wall time, with all 300
outputs of the three builds byte-identical to the frozen goldens
([performance report](paper/round2-performance.md)). It was deployed to Dikarya production
on 2026-10-21.

**Independent check on new production inputs.** After deployment, the 56 distinct
alignment inputs that Dikarya received from 2026-10-16 to 2026-10-22 and that are not in
that corpus (53 L-INS-i, 3 `--auto`; 36–277 sequences, median 135) were rerun with their
production flags and `--thread 1` through a fresh stock MAFFT 7.526 build (gcc 13.3,
`-O3 -march=native`), the frozen opt8 build and the installed dikarya4. **All 56 outputs
were byte-identical across the three builds.** dikarya4 used **9.6x less CPU than stock**
(2,943 to 307 seconds; per input 3.3x–20.3x, median 10.0x) and **1.45x less than opt8**
(446 to 307 seconds; every input faster, 1.03x–2.54x). These ran on the shared production
VM at reduced priority, so the totals carry some contention noise; the ratios agree with
the corpus study.

The round-2 [threading and phylogenetics study](paper/round2-phylogenetics.md) is
summarized in the README ([threading and phylogenies](README.md#threading-and-phylogenies)).
See also the [experimental methods](paper/round2-methods.md), the
[optimization harness](harness/round2/README.md), the
[alignment/tree analysis harness](harness/round2/README-analysis.md) and the
[production deployment script](harness/round2/DEPLOY.md) (verified bundle, golden checks,
atomic activation and rollback; dry run by default).

### Why there is no dikarya3 tag

`7.526-opt8-dikarya3` was an intermediate round-2 build (commit `91146f2`, which enabled the
rolling score-only, identity and zero-extension defaults). It was used in some round-2 checks
and is preserved privately, but it was never tagged or deployed: the selected round-2 source,
`9d963ee`, kept those defaults, added the later guard hardening and the other round-2 changes,
and was released as dikarya4 ([score kernels](paper/round2-score-kernels.md),
[performance report](paper/round2-performance.md)).

## `v7.526-opt8-dikarya2` — Dikarya VM follow-up

dikarya2 adds eight-lane AVX2 striped local DP with deferred end-cell search, exact ordered
duplicate-pair reuse, AVX2 profile improvements, cache-size-aware importance filling, and
faster accurate-direction scoring. It targets the actual two-vCPU VMware guest, whose
CPUID/GCC tuning is Haswell despite its Xeon E5-2690 v4 brand string. On all 100 production
inputs with `--thread 1` it is **1.34x faster than opt8** (865 to 647 seconds) with
**100/100 byte-identical outputs**; the separate 78-input holdout improves 1.30x. One
FFT-NS-2 case regressed about 5%. See the [Dikarya VM report](paper/broadwell-dikarya2.md)
and [its harness](harness/broadwell/README.md).

The Dikarya rounds (dikarya2–dikarya5) were measured only on that AVX2 VM, built with GCC
and `-O3 -march=native -ffp-contract=off`. Other architectures build the same source but
have not received new runtime validation since opt8.

## opt8 and earlier

### Speedups

Speedup over stock MAFFT 7.526 built the same way on the same machine, wall time, `--thread 1`
per run. "Pipeline L-INS-i" is the pipeline's test set of real ITS alignments (about 180
sequences each): a batch of 16 run four at a time on a 4-vCPU instance (eight at a time on the
M1), and single runs. Where that report's version (`v7.526-opt8`) was not measured on a machine, the
row gives the most recent version that was.

| machine | workload | version | several runs at once | single run |
|---|---|---|---|---|
| Apple M1 (macOS), clang, with the Metal GPU | pipeline L-INS-i | opt8 | 5.1× | 8.1× |
| AWS c7a (AMD Zen 4), clang | pipeline L-INS-i | opt8 | 8.6×&nbsp;† | 10.5×&nbsp;† |
| AWS c7i (Intel Sapphire Rapids), clang | pipeline L-INS-i | opt8 | 7.5×&nbsp;† | 8.1×&nbsp;† |
| AWS c8a (AMD Zen 5), clang | pipeline L-INS-i | opt7 | 9.8× | 12× |
| AWS c8i (Intel Granite Rapids), clang | pipeline L-INS-i | opt7 | 8.2× | 9.3× |
| AWS c8g (Graviton4), clang | pipeline L-INS-i | opt8 | 5.0×&nbsp;† | 5.6×&nbsp;† |
| AWS c7g (Graviton3), clang | pipeline L-INS-i | opt7 | 5.1× | 5.6× |
| AWS g6 (NVIDIA L4, AMD Zen 3 host), clang + CUDA | pipeline L-INS-i | opt8 | 8.0×&nbsp;‡ | 9.6×&nbsp;‡ |
| AWS g4dn (NVIDIA T4, Intel Cascade Lake host), clang + CUDA | pipeline L-INS-i | opt7 | 5.8× | |
| AMD Zen 3 with AVX2 only (the g6 host), gcc `-march=broadwell` | pipeline L-INS-i | opt8 | 5.9× | 7.1× |
| the same | FFT-NS-i on the same alignments | opt8 | 2.6× | |
| Dikarya host: Intel Xeon E5-2690 v4 (Broadwell, AVX2), gcc | Dikarya `--auto` jobs | dikarya1 | | 6.4× on local-pair jobs, 2.3× on FFT-NS-i jobs, 6.7× on full L-INS-i |

† Stock was timed on the same instance type in an earlier session; on c8g, opt7 timed in both
sessions agreed within 1%, but c7i speed has drifted by up to 15% between sessions.<br>
‡ Against a stock gcc build (clang stock is 2–6% faster). The GPU's share: the same build
without CUDA was 6.5× and 7.6×.

The speedups depend on the workload and the hardware: sequence count and length, the
alignment strategy, cache sizes and vector units. Other inputs may gain much less. On AWS,
GPU instances pair the GPU with few CPU cores, and refinement, which stays on the CPU,
dominates: at on-demand prices, and at the throughput of the 16-alignment batches, the L4 costs
about $1.10 per 1,000 pipeline alignments, against about $0.17 on a c7a. The GPU path is for
machines with a strong CPU and an idle GPU.

The technical report [`paper/mafft-apple-silicon.pdf`](paper/mafft-apple-silicon.pdf)
describes the methods, the verification and what didn't work in detail, including how the
per-architecture work of opt7 was done by parallel agents and consolidated in opt8.

### Verification state of `v7.526-opt8`

| machine | build | result |
|---|---|---|
| Apple M1 | clang, with and without the GPU | canary 25/25 (13/13 without the GPU); harnesses identical |
| AWS c7a (Zen 4) | clang+FMA AVX-512, with and without VBMI2 flags | 183/183 test alignments = Mac reference; canary 25/25; harnesses identical |
| AWS c7a | gcc `x86-64-v3`, `x86-64-v4` | = stock gcc on a 43-alignment subset |
| AWS c7i (Sapphire Rapids) | clang+FMA AVX-512 | canary 25/25 |
| AWS c8g (Graviton4) | clang `-mcpu=neoverse-v1` | 183/183 = Mac reference; harnesses identical |
| AWS c8g | gcc | harnesses identical (gcc builds match stock gcc, not the Mac) |
| AWS g6 (L4, Zen 3 host) | clang+FMA `x86-64-v3` + CUDA | 183/183 = Mac reference; canary 25/25 with and without the GPU; 733,956 GPU alignments = CPU |
| AWS g6 host (Zen 3, AVX2 only) | gcc `-march=broadwell` (the Dikarya host's build class) | 183/183 = `v7.526-opt5-dikarya1`; 13/13 canary windows = stock gcc; FFT-NS-i 16/16 = dikarya1 |

The original opt8 report did not include a run on the Dikarya host itself. The
[VM follow-up](paper/broadwell-dikarya2.md) now uses installed opt8 as its directly measured
reference. In its build class on the earlier Zen 3 host, opt8 was 1.2× faster than dikarya1
(L-INS-i and FFT-NS-i) with identical output. `v7.526-opt5-dikarya1` retains that earlier code.
Some historical harness runs were on an opt8 commit before the final tuning commit;
`harness/opt8/README.md` lists which harness scripts apply to which version.

### Tags

| tag | adds |
|---|---|
| `v7.526-opt1` | bookkeeping rewrites, NEON DP rows, closed-form pair scores via Accelerate (Apple silicon) |
| `v7.526-opt2` | refinement pair-score cache, vector prefix scan, run-based gap counts |
| `v7.526-opt3` | all-pairs local alignment on the GPU (Metal) |
| `v7.526-opt4` | GPU kernel compiled at build time, occupancy tuning |
| `v7.526-opt5` | x86-64 AVX-512 kernels; multiply-adds rounded per platform |
| `v7.526-opt5-dikarya1` | AVX2 paths for x86-64 without AVX-512, for the Dikarya host: the marker fill, sparse profile scores, uncleared `Falign` work rows |
| `v7.526-opt6` | the marker fill on AVX-512 builds too |
| `v7.526-opt7` | Linux arm64 (NEON/SVE), newer x86 AVX-512 kernels, the CUDA all-pairs stage |
| `v7.526-opt8` | one feature-test header and shared portable code for every target; the AVX-512 kernels without extra flags |
| `v7.526-opt8-dikarya2` | AVX2 striped local DP with deferred end search, duplicate-pair reuse, AVX2 profile and importance-fill tuning for the Dikarya VM |
| `v7.526-opt8-dikarya4` | rolling score-only DP, raw-profile row reuse, direct pair scoring, dense six-mer counts, terminal no-gain refinement reuse |
| `v7.526-opt8-dikarya5` | ordered multi-worker refinement: `--thread N` gives the `--thread 1` alignment |
