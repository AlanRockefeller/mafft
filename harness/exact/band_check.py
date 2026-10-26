#!/usr/bin/env python3
"""Differential test of the exact banded all-pairs fill (Lalign11.c, MAFFT_LBAND).

  band_check.py --mafft PREFIX/bin/mafft --out DIR [--stock STOCK/bin/mafft] [--seeds N]

Generates seed-fixed DNA families with harness/exact/simseq.py over a grid of
divergence, indel rate, length, duplicates, ambiguity codes, N runs, fragments,
outliers and reverse complements, and runs L-INS-i on each with
MAFFT_LBAND_CHECK=1: every pair is then aligned without and with the band and
the run stops at the first difference in score, offsets or aligned strings.
With --stock, each output is also compared byte for byte with stock MAFFT.
A sanitizer report on stderr (UBSan keeps going by default) also fails the case.
Exit status 1 on any failure (a difference, a timeout or a nonzero exit).
"""
import argparse
import itertools
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import caserun  # noqa: E402

HERE = Path(__file__).resolve().parent
RATES = ['0.005', '0.03', '0.08', '0.15', '0.4']
INDELS = ['0.05', '0.3', '0.8']
LENGTHS = ['160', '320', '700']
EXTRAS = ['', 'dup=0.4', 'iupac=0.5 n=0.3', 'frag=0.3', 'outlier=0.15', 'rc=0.3 lower=1']
OPTIONS = [['--localpair', '--maxiterate', '0'], ['--adjustdirection', '--localpair', '--maxiterate', '2']]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--mafft', required=True)
    p.add_argument('--stock')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--seeds', type=int, default=1, help='seeds per grid point')
    p.add_argument('--nseq', type=int, default=12)
    p.add_argument('--timeout', type=float, default=1800)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith('MAFFT_')}
    env.update(LC_ALL='C', LANG='C')
    tally = caserun.Tally()
    grid = list(itertools.product(range(a.seeds), RATES, INDELS, LENGTHS, EXTRAS))
    for n, (seed, rate, indel, length, extra) in enumerate(grid):
        # one strategy per family, alternating, so both are covered across the grid
        opts = OPTIONS[n % len(OPTIONS)]
        name = 'r%s_i%s_l%s_%s_s%d' % (rate, indel, length, extra.replace(' ', '_').replace('=', '') or 'plain', seed)
        fa = a.out / (name + '.fa')
        args = [sys.executable, str(HERE / 'simseq.py'), str(1000 + 97 * n + seed), str(a.nseq), length, 'dna',
                'rate=' + rate, 'indel=' + indel] + extra.split()
        try:
            fa.write_bytes(subprocess.run(args, check=True, capture_output=True, timeout=a.timeout).stdout)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            tally.add(' '.join(opts), name, 'simseq.py failed: %s' % type(e).__name__)
            print('%4d/%d %-48s FAIL simseq.py' % (n + 1, len(grid), name), flush=True)
            continue
        out = a.out / (name + '.out')
        got = caserun.run_case([a.mafft, '--thread', '1', *opts, str(fa)], out, env=dict(env, MAFFT_LBAND_CHECK='1'),
                               timeout=a.timeout, nice=19, err=a.out / (name + '.err'))
        reason = caserun.verdict(got, None)
        if reason is None and a.stock:
            ref = caserun.run_case([a.stock, '--thread', '1', *opts, str(fa)], a.out / (name + '.stock'), env=env,
                                   timeout=a.timeout, nice=19, err=a.out / (name + '.stock.err'))
            if ref['returncode'] != 0 or ref['timed_out']:
                reason = 'stock failed'
            elif ref['output_sha256'] != got['output_sha256']:
                reason = 'output differs from stock'
        errtext = Path(got['stderr']).read_bytes()
        if reason is None and b'LBAND_CHECK' in errtext:
            reason = 'LBAND_CHECK reported a difference'
        if reason is None and (b'runtime error:' in errtext or b'Sanitizer:' in errtext):
            reason = 'sanitizer report on stderr'
        tally.add(' '.join(opts), name, reason)
        print('%4d/%d %-48s %s' % (n + 1, len(grid), name, 'ok' if reason is None else 'FAIL ' + reason), flush=True)
    caserun.write_json(a.out / 'summary.json', tally.as_dict())
    tally.report()
    return 1 if tally.failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
