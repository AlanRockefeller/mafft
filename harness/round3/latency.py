#!/usr/bin/env python3
"""Rotated latency comparison of the four two-CPU configurations.

  A  --thread 1
  F  --thread 2 --threadit 1
  B  --thread 2 with MAFFT_ORDERED_REFINE=0 (stock completion-order refinement)
  O  --thread 2 (ordered refinement)

INPUTS is a JSON list of {"name", "path", "options", "golden"}; "golden" is a
file whose bytes are the expected one-thread output.  Each round runs every
input under all four configurations in a freshly shuffled order (seeded) and
appends one record per run to OUT/manifest.jsonl (harness/exact/caserun.py);
a restart skips runs whose record still checks out.  Every run has a timeout,
and the exit status is nonzero if any run failed or differed from its golden.
Run on an otherwise idle machine.
"""
import argparse
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'exact'))
import caserun  # noqa: E402

CONFIGS = {
    'A': (['--thread', '1'], {}),
    'F': (['--thread', '2', '--threadit', '1'], {}),
    'B': (['--thread', '2'], {'MAFFT_ORDERED_REFINE': '0'}),
    'O': (['--thread', '2'], {}),
}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--mafft', required=True)
    p.add_argument('--inputs', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--rounds', type=int, default=2)
    p.add_argument('--seed', type=int, default=11)
    p.add_argument('--timeout', type=float, default=3600, help='seconds per MAFFT run (default 3600)')
    a = p.parse_args()

    items = json.loads(a.inputs.read_text())
    base = {k: v for k, v in os.environ.items() if k != 'MAFFT_BINARIES'}
    binary = caserun.install_digest(a.mafft)
    rng = random.Random(a.seed)
    a.out.mkdir(parents=True, exist_ok=True)
    manifest = caserun.Manifest(a.out / 'manifest.jsonl')
    tally = caserun.Tally()

    def show(record):
        print('r%d %-24s %s %8.2fs cpu %8.2fs %s' % (record['round'], record['input'], record['config'],
                                                    record['wall'], record['cpu'] or 0, record['failure'] or 'same'),
              flush=True)

    for rnd in range(a.rounds):
        for item in items:
            order = list(CONFIGS)
            rng.shuffle(order)
            for config in order:
                threads, extra = CONFIGS[config]
                env = dict(base)
                env.update(extra)
                name = 'r%d.%s.%s' % (rnd, item['name'], config)
                case = dict(name=name, category='config ' + config, env=env,
                            argv=[a.mafft, *threads, *item['options'], item['path']],
                            out=a.out / 'outputs' / (name + '.fa'),
                            expected=caserun.file_digest(item['golden']),
                            # B is stock completion-order refinement: recorded, may differ.
                            in_scope=config != 'B',
                            record=dict(round=rnd, input=item['name'], config=config, load=os.getloadavg()[0]),
                            key=caserun.case_key(round=rnd, input=str(Path(item['path']).resolve()),
                                                 input_sha256=caserun.file_digest(item['path']), binary=binary,
                                                 threads=threads, options=item['options'],
                                                 env=caserun.relevant_env(env)))
                case['out'].parent.mkdir(exist_ok=True)
                caserun.execute(case, manifest, tally, timeout=a.timeout, nice=5, log=show)
    manifest.close()
    tally.report()
    return 1 if tally.failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
