# Dikarya VM performance and exactness harness

These tools use private input/output directories **outside the repository**. Do not commit
FASTA files, aligned output, application logs, profiles, learned PGO data or binaries.
The research report is [../../paper/broadwell-dikarya2.md](../../paper/broadwell-dikarya2.md).

## Build and freeze the reference

Record the original commit, compiler, flags and the production installation before changing
anything. This investigation froze opt8 commit `322527b8f0b4b6b030cd4325a8aafe7c970a5764` and
the installed `/opt/mafft-7.526-opt8`. Do not substitute the distribution's `/usr/bin/mafft`.
Keep the reference source/build and the candidate in separate directories and prefixes.

The selected GCC build uses the existing Makefile's C99/multithread definitions and:

```sh
make -C core -j1 CC=gcc CFLAGS='-O3 -march=native -ffp-contract=off -g'
make -C core -W mafft.tmpl mafft PREFIX=/absolute/private/prefix
make -C core install CC=gcc CFLAGS='-O3 -march=native -ffp-contract=off -g' \
  PREFIX=/absolute/private/prefix STRIP=true
```

Use a fresh source/build directory when changing flags: Make does not notice changed CFLAGS.
Regenerate `mafft` for each new prefix even when reusing a build directory: otherwise the
installed launcher may still call the previous prefix's helpers. Verify its `prefix=` line.
Here `STRIP=true` retains debug symbols for profiling. No system installation is necessary.
`-march=native` builds for the **guest's** ISA; this VM resolves to Haswell despite its
Broadwell brand string. This is not a portable binary. Do not use fast math or change FMA
contraction to match a different compiler/platform's rounding class.

```sh
python3 harness/broadwell/corpus.py ../bench/data ../bench/production-corpus.json \
  --job-logs /read-only/path/to/application/jobs
python3 harness/broadwell/bench.py --data ../bench/data \
  --manifest ../bench/production-corpus.json --mode production \
  --build reference=/absolute/reference/bin/mafft --rounds 1 --cpu 1 \
  --golden ../bench/golden --freeze --output ../bench/freeze-production
```

`corpus.py` reads only each matching job's command line, not arbitrary application files.
Without recoverable commands, benchmark a clearly labelled `--mode auto --threads 1`;
do not pretend that this reproduces a different production strategy. A corpus with no
per-file options cannot be run in `production` mode.

The manifest freezes SHA-256 hashes and the development/holdout/difficult split. Freeze
goldens only with the designated reference. The harness creates them exclusively and
read-only; never regenerate them with a candidate. Archive SHA-256 manifests separately.

## Exact comparison and paired timing

```sh
python3 harness/broadwell/bench.py --data ../bench/data \
  --manifest ../bench/production-corpus.json --mode production --threads 1 \
  --build reference=/absolute/reference/bin/mafft \
  --build candidate=/absolute/candidate/bin/mafft --rounds 1 --cpu 1 \
  --golden ../bench/golden --output ../bench/final-corpus
```

Build order alternates by input/round. All outputs, stderr, argv, locale, time, output/input
hashes, faults, block I/O and context switches are retained. A mismatch or failed command
stops immediately. Use a new results directory each time. The RSS field is a cumulative
child-process high-water mark, **not** the individual run's peak RSS. `--partition` is for
development experiments; use all files for final validation. Stop competing builds and
training during final timing. Do not compare against old, contended golden-generation times.
Normal shared-VM scheduling noise is still possible; CPU time and repeat runs help expose it.

```sh
python3 harness/broadwell/summarize.py ../bench/final-corpus/runs.jsonl \
  --manifest ../bench/production-corpus.json
python3 harness/broadwell/provenance.py --reference-prefix /absolute/reference \
  --rebuilt-prefix /absolute/frozen-rebuild --candidate-prefix /absolute/candidate \
  --candidate-source /absolute/candidate-source --manifest ../bench/production-corpus.json \
  --golden-manifest ../bench/golden.sha256 \
  --cflags='-O3 -march=native -ffp-contract=off -g' --output ../bench/provenance.json
```

The summarizer refuses incomplete/mismatched or unverified comparisons unless explicitly
asked for a partial progress report. Aggregate speedup is summed reference time divided by
summed candidate time, not an average of cherry-picked individual ratios. Provenance also
compares all reference/rebuild ELF code/read-only-data/data sections and the candidate's
core source files against its actual build snapshot. Keep compiler/build logs as well.

## Differential and adversarial checks

The patching scripts below rewrite **scratch copies only**. Their internal checking toggles
and statistics are not thread-safe: run a single-threaded checking executable. Do not apply
them to production sources/binaries or run the full checking build with thread 2.

- `../opt8/kb_check.py SCRATCH_CORE`: scalar offset-fill reference versus every striped DP
  score, resolved traceback offset, endpoint, and full traceback strings/offsets. Copy
  `l11_avx2.h` beside the scratch `Lalign11.c`. Compile with AVX2 and `MAFFT_POISON_TEST=1`.
- `../opt5/checks/row_check.py`, `mcmatch_check.py`, and `../opt8/fillimp_check.py`: original versus
  new profile rows, profile scores and importance matrices. Read their CLI help/docstrings;
  these are checking builds, not performance builds.
- `noalign_check.py SCRATCH_CORE`: original double score-only DP versus the fast path,
  compared in all 64 return-value bits. This includes signed-zero/fractional/range fallback.
- `profile_check.c`: direct randomized differential tests for profile matching/row scans.
- `l11_bench.c`: isolated fill timing or randomized checking. Link `core/defs.o` with
  `-ffunction-sections -fdata-sections -Wl,--gc-sections -lm -lpthread`. `-DL11_CHECK` enables
  random scoring and boundary lengths. `-DL11_SCORE` tests public score-only calls including
  periodic cleanup, using a source patched by `noalign_check.py`. `L11_SOURCE` can select
  the scratch source. Microbenchmarks supplement, never replace, real FASTA measurements.

Current AVX2 defaults use `MAFFT_KB_EMBED=1`: the existing score range bound permits a
lossless shift by two, with traceback tags in those vacated bits. `-DMAFFT_KB_EMBED=0`
selects the separate byte layout; add `-DMAFFT_KB_PACKED=1` for the three-bitplane experiment.
`-DMAFFT_AVX2_STRIPED=4` selects four lanes. `-DL11_WIDE` in a checking driver adds larger
integer scores/penalties/thresholds. Always rebuild both timing drivers from the **same
current harness**: an obsolete executable may still contain per-iteration matrix allocation
even after the driver source is fixed. Check identical checksums and allocation placement.

```sh
python3 harness/broadwell/cases.py ../bench/adversarial
python3 harness/broadwell/regress.py --data ../bench/adversarial \
  --reference /absolute/reference/bin/mafft --candidate /absolute/candidate/bin/mafft \
  --output ../bench/regression --modes auto direction accurate-direction linsi gap \
  fractional ginsi shift einsi fftnsi fftns2
python3 harness/broadwell/placements.py --data ../bench/adversarial \
  --reference /absolute/reference/bin/mafft --candidate /absolute/candidate/bin/mafft \
  --output ../bench/placements --files edge0065.fasta edge0513.fasta \
  ordered-ties.fasta ambiguous.fasta protein.fasta
```

`regress.py` distinguishes nonempty successful alignments from matching option rejections.
Do not count matching rejections as aligned test cases. `fractional` uses `--weighti 0.37`.
Add sanitizer builds (`-fsanitize=address,undefined -fno-omit-frame-pointer`), then run the
same comparisons. This study disabled leak detection because of upstream process-lifetime
allocations; that is not a claim that all upstream code is leak-free.

Optional memory-limit tests are generated separately with `cases.py --large-only NEW_DIR`:
three pairs of length 8,180/8,190/8,200 straddle the integer-row size fallback. Run just
`regress.py --modes lins1` on that directory, not the entire short-fixture strategy grid.
They can use over a GiB of working memory; confirm headroom and do not run concurrently
with timing or other large jobs. An internal fill declining for memory bounds is valid:
compare the final original-fallback result, not merely whether both fills took a fast path.

Also test `--modes reorder clustal clustal-reorder phylip`. CLUSTAL writes a version stamp
to alignment stdout, so it must retain the reference `ALIGNMENT_VERSION` even if diagnostic
`VERSION`/`mafft --version` identifies a new performance build. Testing only FASTA misses this.

`--threads 0` compares reference 0 against candidate 0. For concurrent pair-stage checks use
`--threads 2 --cpu 0,1 --modes lins1 accurate-direction`; `lins1` omits iterative refinement.
`MAFFT_DUPCACHE_CHECK` recomputes every cache hit and compares strings, offsets and score bits;
it is safe with the cache's synchronized workers. `MAFFT_DUPCACHE_STATS` reports hit counts
and table/result bytes. `MAFFT_NODUPCACHE=1` bypasses the cache. Threaded iterative refinement
may vary even between runs of the **reference**; no new byte-identity guarantee is made there.
`--modes direction-lins1` is the corresponding direction-adjusted, non-iterative diagnostic
for the recorded local-pair jobs. It is not a substitute for production's 1,000-iteration
limit. Keep full iterative reference repeats when diagnosing threaded discrepancies.

## Profiling and controlled experiments

On this VM hardware perf events are unavailable even with `perf_event_paranoid=2`. Software
sampling works without changing the system:

```sh
perf record -e cpu-clock:u -F 499 -o ../bench/candidate.data -- \
  nice -n 10 taskset -c 1 /absolute/candidate/bin/mafft --thread 1 \
  --adjustdirection --localpair --maxiterate 1000 ../bench/data/bpfd.fasta \
  > ../bench/profile.fasta 2> ../bench/profile.stderr
perf report -i ../bench/candidate.data --stdio --no-children --call-graph none
```

`stages.py` captures/replays refinement stages. `dvtditr` rewrites `pre` and reads it on stdin;
its normally discarded stdout is not the alignment to compare. `variant.sh` reuses unchanged
objects for local macro experiments; use it only when unchanged objects have compatible
flags. `host.c` measures dependent-load latency and a streaming triad; it does not establish
physical-host peak bandwidth or actual shared-cache topology inside a VM.
