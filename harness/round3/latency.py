#!/usr/bin/env python3
"""Rotated latency comparison of the four two-CPU configurations.

  A  --thread 1
  F  --thread 2 --threadit 1
  B  --thread 2 with MAFFT_ORDERED_REFINE=0 (stock completion-order refinement)
  O  --thread 2 (ordered refinement)

INPUTS is a JSON list of {"name", "path", "options", "golden"}; "golden" is a
file whose bytes are the expected one-thread output.  Each round runs every
input under all four configurations in a freshly shuffled order (seeded) and
appends one record per run to OUT/runs.jsonl.  Run on an otherwise idle machine.
"""
import argparse
import hashlib
import json
import os
import random
import subprocess
import time
from pathlib import Path

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
    a = p.parse_args()

    items = json.loads(a.inputs.read_text())
    base = {k: v for k, v in os.environ.items() if k != 'MAFFT_BINARIES'}
    rng = random.Random(a.seed)
    a.out.mkdir(parents=True, exist_ok=True)
    with open(a.out / 'runs.jsonl', 'a') as log:
        for rnd in range(a.rounds):
            for item in items:
                golden = Path(item['golden']).read_bytes()
                order = list(CONFIGS)
                rng.shuffle(order)
                for config in order:
                    threads, extra = CONFIGS[config]
                    env = dict(base)
                    env.update(extra)
                    started = time.monotonic()
                    proc = subprocess.Popen([a.mafft, *threads, *item['options'], item['path']],
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                            preexec_fn=lambda: os.nice(5))
                    out = proc.stdout.read()
                    _, status, usage = os.wait4(proc.pid, 0)
                    log.write(json.dumps(dict(
                        round=rnd, input=item['name'], config=config, status=status,
                        wall=time.monotonic() - started, cpu=usage.ru_utime + usage.ru_stime,
                        same=out == golden, sha256=hashlib.sha256(out).hexdigest(),
                        load=os.getloadavg()[0])) + '\n')
                    log.flush()


if __name__ == '__main__':
    main()
