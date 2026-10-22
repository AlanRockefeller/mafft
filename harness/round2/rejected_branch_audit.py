#!/usr/bin/env python3
"""Count repeated rejected refinement branches from verified one-worker traces.

Counterfactual opportunity counts only: no branches are skipped and no speedup
is inferred. A correct implementation must also preserve temporary scoring and
buffer state, all acceptance decisions and convergence-history values.
"""
import argparse
import json
from pathlib import Path
import re
from analysis import sha256_file, write_json

EVENT = re.compile(r'(\d{3,})-(\d{4,})-(\d) \(thread\s+(\d+)\) (better|worse|identical|skip)')


def audit_trace(text):
    events = []
    for match in EVENT.finditer(text):
        iteration, branch, side, worker, status = match.groups()
        if int(worker) != 1:
            raise ValueError('Expected exactly one refinement worker')
        key = (int(iteration), int(branch))
        # An attempt first prints "identical" even when the later outcome is
        # "better" or "worse". Collapse only adjacent messages for that attempt.
        if events and events[-1][0] == key:
            events[-1] = (key, status)
        else:
            events.append((key, status))
    epoch, rejected, hits, accepted, calls = 0, {}, 0, 0, int(bool(events))
    previous, contradictions = None, []
    for (iteration, branch), status in events:
        # FFT-NS-i calls the refiner repeatedly. Its restarted coordinates must
        # never be mistaken for the same branch in the previous invocation.
        if previous is not None and (iteration, branch) < previous:
            epoch, rejected = 0, {}
            calls += 1
        previous = (iteration, branch)
        if rejected.get(branch) == epoch:
            hits += 1
            if status == 'better':
                contradictions.append([calls, iteration, branch])
        if status == 'better':
            epoch += 1
            accepted += 1
        else:
            rejected[branch] = epoch
    return {'completed_branch_attempts': len(events), 'accepted_changes': accepted,
            'refinement_invocations': calls, 'potential_same_state_rejections': hits,
            'potential_attempt_fraction': hits / len(events) if events else None,
            'contradictions': contradictions}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs', type=Path, required=True, help='Frozen harness/broadwell/bench.py runs.jsonl (traces in BUILD/rN/STEM.stderr)')
    p.add_argument('--build', default='candidate')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    rows = []
    for line in args.runs.read_text().splitlines():
        row = json.loads(line)
        if row['build'] != args.build:
            continue
        if row['threads'] != 1 or row['returncode'] or not row['identical']:
            raise ValueError('Require successful exact-output one-thread historical jobs')
        path = args.runs.parent / args.build / ('r' + str(row['round'])) / (Path(row['input']).stem + '.stderr')
        if sha256_file(path) != row['stderr_sha256']:
            raise ValueError('Historical trace changed')
        rows.append({'input': row['input'], 'input_sha256': row['input_sha256'],
                     'stderr': str(path), 'stderr_sha256': row['stderr_sha256'],
                     **audit_trace(path.read_text(errors='replace'))})
    if not rows:
        raise ValueError('No selected historical traces')
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / 'summary.json', {
        'datasets': rows, 'recorded_jobs': len(rows),
        'unique_input_hashes': len({r['input_sha256'] for r in rows}),
        'total_branch_attempts': sum(r['completed_branch_attempts'] for r in rows),
        'total_potential_rejections': sum(r['potential_same_state_rejections'] for r in rows),
        'contradictions': sum(len(r['contradictions']) for r in rows),
        'source_runs_sha256': sha256_file(args.runs),
        'scope': 'Fixed-order, one-worker traces in this corpus; every input with refinement has at least two branch positions. Degenerate one-branch invocations cannot be separated by coordinate restart alone. No arbitrary shuffled-branch or multithread trace inference.',
        'interpretation': 'Job-weighted opportunity count; not a fraction of CPU time or a speedup. An alignment-accept epoch conservatively invalidates all previous rejections. Scoring state, temporary-buffer effects, convergence and exact differential execution still require proof before enabling any shortcut.'})


if __name__ == '__main__':
    main()
