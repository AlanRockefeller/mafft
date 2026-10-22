#!/usr/bin/env python3
"""Replay recent production alignments through several builds and compare bytes.

  select  read Dikarya job logs, keep each distinct (input hash, options) whose
          input is not in the corpus manifest, drop --thread, write SET
  replay  run every selected input with --thread 1 through each named build,
          in a seeded random order per input, appending OUT/runs.jsonl

Job directories are JOBS/<id>/logs/pipeline.log with the streamed MAFFT command
and an event=alignment.mafft_completed line.  Builds are NAME=PATH pairs, for
example stock=/opt/stock/bin/mafft dikarya4=/opt/mafft-7.526-opt8/bin/mafft.
"""
import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import time
from pathlib import Path

COMMAND = re.compile(r'Running command \(streaming\): (\S*mafft\S* [^\n]*)')


def select(a):
    corpus = {f['sha256'] for f in json.loads(a.manifest.read_text())['files']}
    jobs = sorted(a.jobs.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)[:a.recent]
    seen = {}
    for job in jobs:
        try:
            log = (job / 'logs/pipeline.log').read_text(errors='replace')
        except OSError:
            continue
        cmd = COMMAND.search(log)
        if not cmd or 'event=alignment.mafft_completed' not in log:
            continue
        tokens = cmd.group(1).split()
        path = Path(tokens[-1])
        if not path.exists():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in corpus:
            continue
        options, rest = [], tokens[1:-1]
        while rest:
            tok = rest.pop(0)
            if tok == '--thread':
                rest.pop(0)
            else:
                options.append(tok)
        seen.setdefault((digest, tuple(options)), dict(job=job.name, input=str(path), sha256=digest, opts=options))
    a.set.write_text(json.dumps(list(seen.values()), indent=1))
    print(len(seen), 'distinct inputs not in the corpus')


def replay(a):
    builds = dict(b.split('=', 1) for b in a.builds)
    items = json.loads(a.set.read_text())
    env = {k: v for k, v in os.environ.items() if k != 'MAFFT_BINARIES'}
    rng = random.Random(a.seed)
    a.out.mkdir(parents=True, exist_ok=True)
    with open(a.out / 'runs.jsonl', 'a') as log:
        for item in items:
            order = list(builds)
            rng.shuffle(order)
            for name in order:
                out = a.out / ('%s.%s.fasta' % (item['job'], name))
                if out.exists():
                    continue
                started = time.monotonic()
                with open(str(out) + '.tmp', 'wb') as stdout:
                    proc = subprocess.Popen([builds[name], '--thread', '1', *item['opts'], item['input']],
                                            stdout=stdout, stderr=subprocess.DEVNULL, env=env,
                                            preexec_fn=lambda: os.nice(10))
                    _, status, usage = os.wait4(proc.pid, 0)
                os.rename(str(out) + '.tmp', out)
                log.write(json.dumps(dict(
                    job=item['job'], build=name, status=status, wall=time.monotonic() - started,
                    user=usage.ru_utime, sys=usage.ru_stime,
                    sha256=hashlib.sha256(out.read_bytes()).hexdigest(), load=os.getloadavg()[0])) + '\n')
                log.flush()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='mode', required=True)
    s = sub.add_parser('select')
    s.add_argument('--jobs', type=Path, required=True)
    s.add_argument('--manifest', type=Path, required=True)
    s.add_argument('--recent', type=int, default=300, help='newest job directories to read')
    s.add_argument('--set', type=Path, required=True)
    r = sub.add_parser('replay')
    r.add_argument('--set', type=Path, required=True)
    r.add_argument('--out', type=Path, required=True)
    r.add_argument('--seed', type=int, default=7)
    r.add_argument('builds', nargs='+', metavar='NAME=PATH')
    a = p.parse_args()
    select(a) if a.mode == 'select' else replay(a)


if __name__ == '__main__':
    main()
