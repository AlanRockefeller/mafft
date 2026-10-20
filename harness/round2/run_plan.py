#!/usr/bin/env python3
"""Run an immutable plan, retaining every attempt and explicit exit policy.

Each step defaults to accepted_returncodes=[0]. A partial-result stage can
explicitly set [0,1]; its nonzero result stays visible in ledger/final status.
Failed/interrupted steps receive a new log on retry.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from alignments import write_json


def accepted_codes(step):
    codes = step.get('accepted_returncodes', [0])
    if not isinstance(codes, list) or not codes or any(type(code) is not int for code in codes):
        raise ValueError('accepted_returncodes must be a nonempty list of integer exit codes')
    return codes


def next_attempt(out, label, recorded_attempts):
    """Count orphaned logs too: interruption may precede the ledger append."""
    maximum = recorded_attempts
    if (out / (label + '.log')).exists():
        maximum = max(maximum, 1)
    pattern = re.compile(re.escape(label) + r'\.attempt-([0-9]+)\.log$')
    for path in out.glob(label + '.attempt-*.log'):
        match = pattern.fullmatch(path.name)
        if match:
            maximum = max(maximum, int(match[1]))
    attempt = maximum + 1
    path = out / (label + '.log' if attempt == 1 else f'{label}.attempt-{attempt:03d}.log')
    return attempt, path


def execute_plan(plan_path):
    plan_path = Path(plan_path)
    plan = json.loads(plan_path.read_text())
    labels = [step['label'] for step in plan['steps']]
    if len(labels) != len(set(labels)) or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', label) for label in labels):
        raise ValueError('Step labels must be unique safe log-file names')
    policies = {step['label']: accepted_codes(step) for step in plan['steps']}
    for step in plan['steps']:
        if not isinstance(step['command'], list) or not step['command'] or any(not isinstance(x, str) for x in step['command']):
            raise ValueError('Each command must be a nonempty argument array')
    out = Path(plan['output']).resolve()
    out.mkdir(exist_ok=True, parents=True)
    digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    meta = out / 'plan-sha256.txt'
    if meta.exists():
        if meta.read_text().strip() != digest:
            raise ValueError('Plan changed; preserve campaign and use another output')
    else:
        with meta.open('x') as stream:
            stream.write(digest + '\n')
    done, attempts = {}, defaultdict(int)
    log = out / 'steps.jsonl'
    if log.exists():
        for line in log.read_text().splitlines():
            record = json.loads(line)
            label = record['label']
            attempts[label] = max(attempts[label], record.get('attempt', 1))
            if label in policies and not record.get('launch_error') and record['returncode'] in policies[label]:
                done[label] = record
    if plan.get('wait_for'):
        status = Path(plan['wait_for'])
        while not status.exists() or not json.loads(status.read_text()).get('finished', False):
            write_json(out / 'STATUS.json', {'waiting_for': str(status)})
            time.sleep(2)
    with log.open('a', buffering=1) as records:
        for step in plan['steps']:
            label, codes = step['label'], policies[step['label']]
            if label in done:
                continue
            while (out / 'PAUSE').exists():
                write_json(out / 'STATUS.json', {'paused': True, 'next': label})
                time.sleep(.5)
            attempt, path = next_attempt(out, label, attempts[label])
            write_json(out / 'STATUS.json', {'paused': False, 'running': label, 'attempt': attempt,
                                             'log': str(path), 'command': step['command'], 'accepted_returncodes': codes})
            start, launch_error = time.monotonic(), None
            with path.open('x') as stdout:
                try:
                    result = subprocess.run(step['command'], cwd=plan['cwd'], stdout=stdout,
                                            stderr=subprocess.STDOUT, env=dict(os.environ, LC_ALL='C', LANG='C'))
                    returncode = result.returncode
                except OSError as exc:
                    launch_error, returncode = str(exc), 127
                    stdout.write('Command launch failed: ' + launch_error + '\n')
            accepted = launch_error is None and returncode in codes
            record = {'label': label, 'attempt': attempt, 'log': str(path), 'command': step['command'],
                      'returncode': returncode, 'elapsed_seconds': time.monotonic() - start,
                      'accepted_returncodes': codes, 'accepted': accepted, 'launch_error': launch_error}
            records.write(json.dumps(record, sort_keys=True) + '\n')
            print(json.dumps(record), flush=True)
            attempts[label] = attempt
            if not accepted:
                write_json(out / 'STATUS.json', {'failed': label, 'finished': False, 'attempt': attempt, 'log': str(path),
                                                 'returncode': returncode, 'accepted_returncodes': codes, 'launch_error': launch_error})
                return returncode if returncode > 0 else 1
            done[label] = record
    nonzero = [{'label': label, 'returncode': record['returncode'], 'attempt': record.get('attempt', 1)}
               for label, record in done.items() if record['returncode'] != 0]
    write_json(out / 'STATUS.json', {'finished': True, 'steps': len(plan['steps']), 'accepted_nonzero_steps': nonzero,
                                     'completion_policy': 'Every step returned an explicitly accepted code; accepted nonzero exits may represent documented partial results.'})
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    args = parser.parse_args()
    raise SystemExit(execute_plan(args.plan))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))
