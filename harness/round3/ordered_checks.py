#!/usr/bin/env python3
"""Byte-identity checks for ordered multi-worker refinement (dikarya5).

Every mode compares MAFFT stdout with a one-thread golden.  Each finished run
is appended to OUT/manifest.jsonl (see harness/exact/caserun.py) under a key
covering the input and its hash, the installed binary hash, the options and
the MAFFT environment.  On restart a case is skipped only if its manifest
record still checks out; earlier failures stay in the failure count.

  corpus  every input of the corpus manifest with --thread THREADS
  stress  development L-INS-i inputs with 2, 3 and 4 threads, then forced
          fallbacks and MAFFT_ORDERED_REFINE=0 (use a -DMAFFT_ORDERED_TEST build)

The manifest is the round-2 production-corpus.json: files[].file,
files[].production_options and files[].partition.  Inputs are DATA/<file>,
goldens GOLDEN/<file>.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'exact'))
import caserun  # noqa: E402

MARKERS = {'ordered': b'Ordered refinement: ', 'rerun': b'rerunning with one worker'}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('mode', choices=['corpus', 'stress'])
    p.add_argument('--mafft', required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--golden', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--threads', type=int, default=2)
    p.add_argument('--timeout', type=float, default=3600, help='seconds per MAFFT run (default 3600)')
    p.add_argument('--retry', action='store_true', help='rerun recorded failures (they still count)')
    a = p.parse_args()

    files = json.loads(a.manifest.read_text())['files']
    base = {k: v for k, v in os.environ.items() if k != 'MAFFT_BINARIES'}
    cases = []
    if a.mode == 'corpus':
        cases = [(f, a.threads, {}) for f in files]
    else:
        dev = [f for f in files if f['partition'] == 'development' and '--localpair' in f['production_options']]
        cases = [(f, t, {}) for f in dev for t in (2, 3, 4)]
        for f in dev[:4]:
            cases += [(f, 2, {'MAFFT_ORDERED_TEST_UNSAFE': n}) for n in ('1', '40', '400')]
            cases.append((f, 2, {'MAFFT_ORDERED_REFINE': '0'}))

    a.out.mkdir(parents=True, exist_ok=True)
    binary = caserun.install_digest(a.mafft)
    manifest = caserun.Manifest(a.out / 'manifest.jsonl')
    tally = caserun.Tally()

    def show(record):
        print('%-48s %-9s %8.1fs %s' % (record['case'], 'ok' if record['failure'] is None else 'FAIL',
                                        record['wall'], record['failure'] or ''), flush=True)

    for f, threads, extra in cases:
        tag = '.'.join([f['file'], 't%d' % threads] + ['%s%s' % kv for kv in extra.items()])
        env = dict(base)
        env.update(extra)
        source = a.data / f['file']
        argv = [a.mafft, '--thread', str(threads), *f['production_options'], str(source)]
        in_scope = '--localpair' in f['production_options'] and 'MAFFT_ORDERED_REFINE' not in extra
        category = ('%s t%d' % (a.mode, threads) if not extra else
                    '%s %s' % (a.mode, ','.join(extra)))
        case = dict(name=tag, category=category, argv=argv, env=env, out=a.out / (tag + '.fa'),
                    expected=caserun.file_digest(a.golden / f['file']), in_scope=in_scope,
                    stderr_markers=MARKERS, record=dict(options=f['production_options']),
                    key=caserun.case_key(input=str(source.resolve()), input_sha256=caserun.file_digest(source),
                                         binary=binary, threads=threads, options=f['production_options'],
                                         env=caserun.relevant_env(env)))
        caserun.execute(case, manifest, tally, timeout=a.timeout, retry=a.retry, nice=19, log=show)
    manifest.close()
    tally.report()
    caserun.write_json(a.out / 'summary.json', tally.as_dict())
    print('in-scope failures:', tally.failed)
    return 1 if tally.failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
