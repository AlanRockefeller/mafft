#!/usr/bin/env python3
"""Read-only idle-service calibration, independent of any speed comparison.

Produces a prospective quiet-threshold recommendation, never changes process
priority/affinity, stops jobs, or rewrites earlier contention classifications.
The caller must review the result before applying an explicit CLI threshold.
"""
import argparse
import math
from pathlib import Path
import statistics
import time

from analysis import sha256_file, write_json
from quiet import busy_delta, cpu_snapshot, scientific_snapshot


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--wait-for', type=Path, action='append', default=[])
    p.add_argument('--samples', type=int, default=120)
    p.add_argument('--interval', type=float, default=1)
    args = p.parse_args()
    if args.samples < 30 or not math.isfinite(args.interval) or args.interval < 1:
        p.error('Use at least 30 samples at intervals of at least one second')
    args.output.mkdir(parents=True, exist_ok=False)
    policy = {'samples': args.samples, 'interval_seconds': args.interval,
              'scientific_process_limit_cores': .05, 'maximum_sample_cores': .50,
              'minimum_recommended_cores': .10, 'maximum_recommended_cores': .20,
              'recommendation': 'max(0.10, round_up_to_0.01(p95 idle busy cores + 0.02))',
              'acceptance': 'No busy scientific process; all samples <=0.50 cores; recommendation <=0.20 cores. Otherwise preserve the window and keep waiting.',
              'code_sha256': sha256_file(__file__),
              'wait_for': [str(x.resolve()) for x in args.wait_for],
              'purpose': 'Measure routine idle service load after scientific jobs finish. No performance result is used to set the criterion.'}
    write_json(args.output / 'policy.json', policy)
    while not all(x.exists() for x in args.wait_for):
        time.sleep(2)
    attempt = 0
    while True:
        attempt += 1
        samples = []
        previous, processes = cpu_snapshot(), scientific_snapshot()
        for _ in range(args.samples):
            time.sleep(args.interval)
            current, now = cpu_snapshot(), scientific_snapshot()
            delta = busy_delta(previous, current, 0)
            hot = []
            if delta.get('available'):
                for key, process in now.items():
                    prior = processes.get(key)
                    ticks = process['cpu_ticks'] - (prior['cpu_ticks'] if prior else 0)
                    cores = max(0, ticks) / current['tick_hz'] / delta['elapsed_seconds']
                    if cores > .05:
                        hot.append(dict(process, busy_cores=cores, newly_observed=prior is None))
            samples.append({'cpu': delta, 'busy_scientific_processes': hot})
            previous, processes = current, now
        available = all(row['cpu'].get('available') for row in samples)
        values = sorted(row['cpu']['external_busy_cores_estimate'] for row in samples) if available else []
        p95 = values[math.ceil(.95 * len(values)) - 1] if values else None
        recommended = max(.10, math.ceil((p95 + .02) * 100) / 100) if p95 is not None else None
        accepted = bool(available and not any(row['busy_scientific_processes'] for row in samples)
                        and max(values) <= .50 and recommended <= .20)
        record = {'attempt': attempt, 'accepted': accepted, 'samples': samples,
                  'median_busy_cores': statistics.median(values) if values else None,
                  'p95_busy_cores': p95, 'maximum_busy_cores': max(values) if values else None,
                  'recommended_max_busy_cores': recommended,
                  'policy_sha256': sha256_file(args.output / 'policy.json')}
        name = args.output / f'window-{attempt:04d}.json'
        write_json(name, record)
        if accepted:
            write_json(args.output / 'calibration.json', {k: v for k, v in record.items() if k != 'samples'} |
                       {'accepted_window': str(name.resolve()), 'accepted_window_sha256': sha256_file(name)})
            return


if __name__ == '__main__':
    main()
