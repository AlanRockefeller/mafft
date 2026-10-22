# Round 3: ordered refinement checks

Scripts behind the [ordered-refinement report](../../paper/round3-ordered-refinement.md)
and the independent stock comparison of dikarya4 in the main README. They need Python
3.8+ and private builds; they never touch an installed MAFFT except to run it.

## Builds

Build the release, a test-hook build and a sanitizer build from the same source with the
round-2 helper or plain `make`, each into its own prefix:

```sh
python3 harness/round2/build.py --source "$src" --output /private/dikarya5 --jobs 1
python3 harness/round2/build.py --source "$src" --output /private/dikarya5-test --jobs 1 \
  --flags "-O3 -march=native -ffp-contract=off -g -DMAFFT_ORDERED_TEST"
python3 harness/round2/build.py --source "$src" --output /private/dikarya5-asan --jobs 1 \
  --flags "-O1 -g -march=native -ffp-contract=off -fsanitize=address,undefined -fno-omit-frame-pointer -DMAFFT_ORDERED_TEST"
```

`-DMAFFT_ORDERED_TEST` adds random delays at every alignment copy and commit turn, and
honors `MAFFT_ORDERED_TEST_UNSAFE=n`, which treats the nth commit as outside the
reproducible scope and so exercises the one-worker rerun. Never time those builds.

A stock reference is upstream `main` (`git archive 0a2319b`) built the same way:
`make CC=gcc CFLAGS="-O3 -march=native -ffp-contract=off"`.

## Identity checks

```sh
# every corpus input with two threads, against the one-thread goldens
python3 harness/round3/ordered_checks.py corpus --mafft /private/dikarya5/install/bin/mafft \
  --manifest bench/production-corpus.json --data bench/data \
  --golden bench/golden/production-t1 --out /private/ordered-t2 --threads 2

# 2/3/4 workers under random delays, forced fallbacks and the environment switch
python3 harness/round3/ordered_checks.py stress --mafft /private/dikarya5-test/install/bin/mafft \
  --manifest bench/production-corpus.json --data bench/data \
  --golden bench/golden/production-t1 --out /private/ordered-stress
```

Both exit nonzero if an in-scope (L-INS-i) run fails or differs. Out-of-scope inputs
(FFT-NS-i via `--auto`) and `MAFFT_ORDERED_REFINE=0` runs are recorded but may differ:
they use stock completion-order refinement.

## New production inputs against stock

```sh
python3 harness/round3/stock_replay.py select --jobs /var/www/dikarya/var/jobs \
  --manifest bench/production-corpus.json --set /private/replay-set.json
python3 harness/round3/stock_replay.py replay --set /private/replay-set.json \
  --out /private/replay stock=/private/stock/bin/mafft opt8=/private/frozen-opt8/bin/mafft \
  dikarya4=/opt/mafft-7.526-opt8/bin/mafft
```

`select` keeps each distinct input/option pair whose input hash is not in the corpus and
drops the recorded `--thread`; `replay` runs `--thread 1` at nice 10. Equal SHA-256 per job
across builds is the result; CPU totals are descriptive on a shared VM.

## Latency

```sh
python3 harness/round3/latency.py --mafft /private/dikarya5/install/bin/mafft \
  --inputs /private/latency-inputs.json --out /private/latency --rounds 3
```

`latency-inputs.json` lists `{"name", "path", "options", "golden"}`. Each round runs every
input under A (`--thread 1`), F (`--thread 2 --threadit 1`), B (`--thread 2`,
`MAFFT_ORDERED_REFINE=0`) and O (`--thread 2`) in a shuffled order. Run it with no other
MAFFT work on the machine and check the recorded load average.
