#!/usr/bin/env python3
"""Replay recent production alignments through several builds and compare bytes.

  select  read Dikarya job logs, keep each distinct (input hash, options) whose
          input is not in the corpus manifest, drop --thread, write SET
  replay  run every selected input with --thread 1 through each named build,
          in a seeded random order per input, recording each run in
          OUT/manifest.jsonl (see harness/exact/caserun.py); exits nonzero if
          any run fails or any build's bytes differ from the reference build

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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'exact'))
import caserun  # noqa: E402

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
    return 0


def replay(a):
    builds = dict(b.split('=', 1) for b in a.builds)
    reference = a.reference or next(iter(builds))
    if reference not in builds:
        raise SystemExit('--reference %s is not one of the builds' % reference)
    digests = {name: caserun.install_digest(path) for name, path in builds.items()}
    items = json.loads(a.set.read_text())
    env = {k: v for k, v in os.environ.items() if k != 'MAFFT_BINARIES'}
    rng = random.Random(a.seed)
    a.out.mkdir(parents=True, exist_ok=True)
    manifest = caserun.Manifest(a.out / 'manifest.jsonl')
    runs = caserun.Tally()
    outputs = {}

    def show(record):
        print('%-28s %-10s %8.1fs %s' % (record['job'], record['build'], record['wall'],
                                         record['failure'] or record['output_sha256'][:12]), flush=True)

    for item in items:
        order = list(builds)
        rng.shuffle(order)
        source = Path(item['input'])
        for name in order:
            argv = [builds[name], '--thread', '1', *item['opts'], item['input']]
            case = dict(name='%s.%s' % (item['job'], name), category='run ' + name, argv=argv, env=env,
                        out=a.out / ('%s.%s.fasta' % (item['job'], name)), expected=None,
                        record=dict(job=item['job'], build=name, load=os.getloadavg()[0]),
                        key=caserun.case_key(input=str(source.resolve()), input_sha256=item['sha256'],
                                             binary=digests[name], options=item['opts'],
                                             env=caserun.relevant_env(env), timeout=a.timeout))
            if caserun.file_digest(source) != item['sha256']:
                runs.add(case['category'], case['name'], 'input hash changed since select')
                continue
            if caserun.execute(case, manifest, runs, timeout=a.timeout, retry=a.retry, nice=10, log=show) is None:
                outputs[item['job'], name] = manifest.attempts(case['key'])[-1]['output_sha256']
    manifest.close()
    # Identity: every build must reproduce the reference build's bytes for every job.
    for item in items:
        ref = outputs.get((item['job'], reference))
        for name in builds:
            if name == reference:
                continue
            got = outputs.get((item['job'], name))
            reason = (None if ref is not None and got == ref else
                      'not compared: a run failed' if ref is None or got is None else
                      'differs from ' + reference)
            runs.add('%s == %s' % (name, reference), item['job'], reason)
    runs.report()
    caserun.write_json(a.out / 'summary.json', runs.as_dict())
    return 1 if runs.failed else 0


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
    r.add_argument('--reference', help='build every other build must match (default: the first)')
    r.add_argument('--timeout', type=float, default=3600, help='seconds per MAFFT run (default 3600)')
    r.add_argument('--retry', action='store_true', help='rerun recorded failures (they still count)')
    r.add_argument('builds', nargs='+', metavar='NAME=PATH')
    a = p.parse_args()
    return select(a) if a.mode == 'select' else replay(a)


if __name__ == '__main__':
    raise SystemExit(main())
