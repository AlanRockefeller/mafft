#!/usr/bin/env python3
"""Project fixed MAFFT scheduling policies on an observed stage-start schedule.

This is discrete-event modeling using separately measured service times, not a
native timing experiment or a replay of the full production pipeline.
"""
import argparse
from collections import defaultdict
import heapq
import json
from pathlib import Path
import statistics
from analysis import sha256_file, write_json
from throughput import summary


def simulate(arrivals, services, workers):
    free = [(0., slot) for slot in range(workers)]
    heapq.heapify(free)
    result = []
    for row in sorted(arrivals, key=lambda r: (r['relative_seconds'], r['input'])):
        available, slot = heapq.heappop(free)
        start = max(row['relative_seconds'], available)
        end = start + services[row['input']]
        result.append({'input': row['input'], 'slot': slot, 'arrival': row['relative_seconds'],
                       'start': start, 'finish': end, 'queue': start-row['relative_seconds'],
                       'service': services[row['input']], 'response': end-row['relative_seconds']})
        heapq.heappush(free, (end, slot))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--starts', type=Path, required=True)
    p.add_argument('--paired-runs', type=Path, required=True)
    p.add_argument('--build', default='dikarya3')
    p.add_argument('--throughput-selection', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    trace = json.loads(args.starts.read_text())
    starts = trace['records']
    paired_meta = json.loads((args.paired_runs.parent/'experiment.json').read_text())
    build = next(b for b in paired_meta['builds'] if b['name'] == args.build)
    isolated = defaultdict(list)
    input_hashes = {}
    flags = []
    for line in args.paired_runs.read_text().splitlines():
        row = json.loads(line)
        if row['build'] == args.build:
            if row['returncode'] or not row['identical']:
                raise ValueError('Failed or nonidentical deterministic timing')
            isolated[row['input']].append(row['wall'])
            input_hashes[row['input']] = row['input_sha256']
            flags.append(row.get('contention', {}).get('contention_flag'))
    a_service = {name: statistics.mean(values) for name, values in isolated.items()}
    for row in starts:
        if input_hashes.get(row['input']) != row['input_sha256']:
            raise ValueError('Observed start and measured input identity differ')
    selected = {}
    for line in args.throughput_selection.read_text().splitlines():
        row = json.loads(line)
        if row.get('selected'):
            if row['policy'] in selected:
                raise ValueError('Multiple selected native measurements for one policy')
            selected[row['policy']] = row
    results = []
    for policy, row in selected.items():
        path = Path(row['summary'])
        native = json.loads(path.read_text())
        experiment = json.loads((path.parents[2]/'experiment.json').read_text())
        if (native['contention']['contention_flag'] is not False or
            experiment['installed_file_sha256'] != build['installed_file_sha256']):
            raise ValueError('Selected throughput is contended or uses a different executable')
        runs = [json.loads(line) for line in (path.parent/'runs.jsonl').read_text().splitlines()]
        if any(r['returncode'] or r['timed_out'] or r['input_sha256'] != input_hashes.get(r['input']) for r in runs):
            raise ValueError('Failed or mismatched native throughput record')
        services = a_service if policy == 'two_jobs' else {r['input']: r['service_seconds'] for r in runs}
        projected = simulate(starts, services, native['concurrency'])
        # Calibrate the simple service model on the measured saturated order.
        backlog = [{'input': name, 'relative_seconds': 0.} for name in native['queue_order']]
        # Iterate the actual seeded order directly for this saturated model.
        queue_free = [0.] * native['concurrency']
        for job in backlog:
            slot = min(range(len(queue_free)), key=lambda i: (queue_free[i], i))
            queue_free[slot] += services[job['input']]
        predicted_backlog = max(queue_free)
        results.append({'policy': policy, 'workers': native['concurrency'], 'observed_jobs': len(starts),
                        'projected_response_seconds': summary(r['response'] for r in projected),
                        'projected_queue_seconds': summary(r['queue'] for r in projected),
                        'jobs_with_projected_wait': sum(r['queue'] > 0 for r in projected),
                        'modeled_schedule': projected,
                        'native_saturated_makespan_seconds': native['makespan_seconds'],
                        'modeled_saturated_makespan_seconds': predicted_backlog,
                        'saturated_calibration_relative_error': predicted_backlog/native['makespan_seconds']-1,
                        'native_summary': str(path), 'native_summary_sha256': sha256_file(path)})
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output/'summary.json', {'results': results,
               'starts_sha256': sha256_file(args.starts), 'excluded_starts': trace['excluded'],
               'paired_runs_sha256': sha256_file(args.paired_runs),
               'paired_A_contention_flags': {'flagged': sum(x is True for x in flags), 'unknown': sum(x is None for x in flags)},
               'assumptions': 'A service uses isolated deterministic measurements; other policies use the selected single-job native policy service times. Two independent A workers share a fixed pool with no dynamic slowdown model. Native saturated calibration reports its approximation error. Selected execution-start timestamps are not user submissions or the complete arrival stream. Upstream/downstream jobs, cache/frequency changes and other production CPU users are omitted. These are projections, never measured latency or throughput guarantees.'})
    print(json.dumps([{k: r[k] for k in ['policy', 'observed_jobs', 'jobs_with_projected_wait', 'projected_response_seconds']} for r in results]))


if __name__ == '__main__':
    main()
