#!/usr/bin/env python3
"""Adversarial stress of ordered refinement against stock --thread 1.

Needs a -DMAFFT_ORDERED_TEST build of the candidate (random delays at every
alignment copy and commit turn; MAFFT_ORDERED_TEST_UNSAFE=n makes the nth
computed commit unsafe and forces the one-worker rerun).  For each DNA input:

  repeat   REPEATS runs at each of --thread 2, 3 and 4
  oversub  REPEATS // 5 runs at --thread 8 (more workers than this VM has cores)
  unsafe   one run per forced-fallback position: the first commit, the last and
           first commits around each of the first two generation boundaries
           (nbranch = 2 * (nseq - 1) - 1 commits per generation when nothing is
           cached), some random positions, and one past any plausible end
           (never fires: the run must then finish as ordered refinement)
  switch   MAFFT_ORDERED_REFINE=0/off/FALSE/no with --thread 2: must exit 0 and
           must not print "Ordered refinement:" (its output is stock completion
           order, which may differ from --thread 1, so it is not compared)

Every run is compared byte for byte with the same input through stock
--thread 1 (L-INS-i by default; --strategy picks G-INS-i or E-INS-i).
Results and resume rules are those of matrix.py; exit status 1 on any failure.
"""
import argparse
import concurrent.futures
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import caserun  # noqa: E402
import matrix  # noqa: E402

DEFAULT_INPUTS = ['dna12small', 'dna24div', 'dna20alldup', 'dna30near', 'dna40outlier']


def nseq(path):
    return sum(1 for line in open(path) if line.startswith('>'))


def cases(inputs, repeats, strategy, seed):
    rng = random.Random(seed)
    opts = matrix.STRATEGIES[strategy]
    for name in inputs:
        path = str(matrix.input_path(name))
        ref = ['--thread', '1', *opts, path]
        for t in (2, 3, 4):
            for r in range(repeats):
                yield 'repeat %s t%d' % (strategy, t), '%s.r%d' % (name, r), ['--thread', str(t), *opts, path], \
                    {'MAFFT_MATRIX_REPEAT': str(r)}, ref
        for r in range(max(1, repeats // 5)):
            yield 'oversubscribed %s t8' % strategy, '%s.r%d' % (name, r), ['--thread', '8', *opts, path], \
                {'MAFFT_MATRIX_REPEAT': str(r)}, ref
        nb = 2 * (nseq(path) - 1) - 1
        positions = {1, 2, nb - 1, nb, nb + 1, 2 * nb, 2 * nb + 1, 10 ** 6}
        positions |= {rng.randint(1, 3 * nb) for _ in range(6)}
        for n in sorted(p for p in positions if p >= 1):
            for t in (2, 4):
                yield 'forced fallback %s t%d' % (strategy, t), '%s.u%d' % (name, n), \
                    ['--thread', str(t), *opts, path], {'MAFFT_ORDERED_TEST_UNSAFE': str(n)}, ref
        for value in ('0', 'off', 'FALSE', 'no'):
            yield 'switch off %s t2' % strategy, '%s.%s' % (name, value), ['--thread', '2', *opts, path], \
                {'MAFFT_ORDERED_REFINE': value}, None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--stock', required=True)
    p.add_argument('--test-build', required=True, help='candidate built with -DMAFFT_ORDERED_TEST')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--inputs', nargs='+', default=DEFAULT_INPUTS, help='fixture names in test/exact')
    p.add_argument('--repeats', type=int, default=50)
    p.add_argument('--strategy', choices=matrix.ORDERED, default='linsi')
    p.add_argument('--seed', type=int, default=5)
    p.add_argument('--jobs', type=int, default=1)
    p.add_argument('--timeout', type=float, default=1800)
    p.add_argument('--nice', type=int, default=10)
    a = p.parse_args()
    stock, test = str(Path(a.stock).resolve()), str(Path(a.test_build).resolve())
    env = {k: v for k, v in os.environ.items() if k != 'MAFFT_BINARIES'
           and not k.startswith(('MAFFT_ORDERED', 'MAFFT_NO'))}
    env.update(LC_ALL='C', LANG='C')
    a.out.mkdir(parents=True, exist_ok=True)
    runner = matrix.Runner(a.out, a.timeout, a.nice)
    tally = caserun.Tally()
    todo = list(cases(a.inputs, a.repeats, a.strategy, a.seed))
    print('%d cases' % len(todo), flush=True)
    markers = {}

    def one(case):
        category, name, args, extra, ref_args = case
        got = runner.run(test, args, dict(env, **extra), category + ' ' + name)
        # "switch off" is not ordered refinement: stock completion order may
        # differ from --thread 1, so only a clean exit and the stderr are checked.
        ref = runner.run(stock, ref_args, env, 'stock ' + name) if ref_args else None
        return case, got, ref

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        for n, (case, got, ref) in enumerate(pool.map(one, todo), 1):
            category, name = case[0], case[1]
            if ref is not None:
                reason = matrix.compare(got, ref)
            else:
                reason = caserun.verdict(dict(got, launch_error=got['launch_error']), None)
                if reason is None:
                    err = Path(got['stderr']).read_bytes()
                    if b'Ordered refinement:' in err:
                        reason = 'MAFFT_ORDERED_REFINE=%s did not switch it off' % case[3]['MAFFT_ORDERED_REFINE']
            if reason is None and got['returncode'] == 0:
                err = Path(got['stderr']).read_bytes()
                markers.setdefault(category, [0, 0, 0])
                markers[category][0] += 1
                markers[category][1] += b'Ordered refinement: ' in err
                markers[category][2] += b'rerunning with one worker' in err
            tally.add(category, name, reason, cached=got['cached'])
            print('%5d/%d %-30s %-22s %s' % (n, len(todo), category, name,
                                             'ok' if reason is None else 'FAIL ' + reason), flush=True)
    runner.manifest.close()
    caserun.write_json(a.out / 'summary.json', dict(tally.as_dict(), stock=stock, test_build=test,
                                                     markers={k: dict(runs=v[0], ordered=v[1], reruns=v[2])
                                                              for k, v in markers.items()}))
    tally.report()
    for k, v in sorted(markers.items()):
        print('%-40s runs %4d  ordered %4d  one-worker reruns %4d' % (k, *v))
    return 1 if tally.failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
