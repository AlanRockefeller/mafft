#!/usr/bin/env python3
"""Byte-identity checks for ordered multi-worker refinement (dikarya5).

Every mode compares MAFFT stdout with a one-thread golden and appends one JSON
record per run to OUT/runs.jsonl.  Runs recorded there are not rerun on restart
but still count toward the result.

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
import subprocess
import time
from pathlib import Path


def run(mafft, threads, options, path, out, env):
    started = time.monotonic()
    # Write to a partial file so an interrupted run is never taken as finished.
    part = out.with_name(out.name + '.part')
    with open(part, 'wb') as stdout, open(str(out) + '.err', 'wb') as stderr:
        proc = subprocess.Popen([mafft, '--thread', str(threads), *options, str(path)],
                                stdout=stdout, stderr=stderr, env=env,
                                preexec_fn=lambda: os.nice(19))
        _, status, usage = os.wait4(proc.pid, 0)
    os.replace(part, out)
    err = Path(str(out) + '.err').read_bytes()
    return dict(status=status, wall=time.monotonic() - started,
                cpu=usage.ru_utime + usage.ru_stime,
                ordered=b'Ordered refinement: ' in err,
                rerun=b'rerunning with one worker' in err)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('mode', choices=['corpus', 'stress'])
    p.add_argument('--mafft', required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--golden', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--threads', type=int, default=2)
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
    log_path = a.out / 'runs.jsonl'
    done = {}
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            rec = json.loads(line)
            done[rec['case']] = rec
    bad = 0
    with open(log_path, 'a') as log:
        for f, threads, extra in cases:
            tag = '.'.join([f['file'], 't%d' % threads] + ['%s%s' % kv for kv in extra.items()])
            out = a.out / (tag + '.fa')
            golden = (a.golden / f['file']).read_bytes()
            in_scope = '--localpair' in f['production_options'] and 'MAFFT_ORDERED_REFINE' not in extra
            if out.exists() and tag in done:
                # Finished on an earlier invocation: recount it rather than rerun it.
                rec = dict(done[tag], same=out.read_bytes() == golden)
            else:
                env = dict(base)
                env.update(extra)
                rec = run(a.mafft, threads, f['production_options'], a.data / f['file'], out, env)
                rec.update(case=tag, options=f['production_options'], same=out.read_bytes() == golden)
                log.write(json.dumps(rec) + '\n')
                log.flush()
            bad += in_scope and (not rec['same'] or rec['status'] != 0)
    print('in-scope failures:', bad)
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
