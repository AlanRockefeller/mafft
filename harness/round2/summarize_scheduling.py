#!/usr/bin/env python3
"""Validate explicit scheduling selections and summarize them without running jobs.

Paths in selection ledgers are relative to their ledger directory. Full rows
select one summary per policy; isolated rows select one two-round campaign per
input; the pilot ledger selects one two-round campaign. No timing-based choice,
bootstrap interval, alignment calculation or external program is performed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys

FULL = ('production', 'two_threads', 'two_jobs', 'selective', 'refine_one')
ISOLATED = ('one_job', 'two_threads', 'selective', 'refine_one')
PILOT = ('two_threads', 'two_jobs', 'selective', 'refine_one')
POLICIES = {'production': ('production', 1), 'one_job': ('A', 1), 'two_threads': ('B', 1),
            'two_jobs': ('A', 2), 'selective': ('C', 1), 'refine_one': ('F', 1)}
FLAGS = {'A': ['--thread', '1'], 'B': ['--thread', '2'],
         'C': ['--thread', '2', '--threadit', '0'], 'F': ['--thread', '2', '--threadit', '1']}


def resolve(value, base):
    p = Path(value)
    return (p if p.is_absolute() else base / p).resolve()


def finite(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError('Missing/invalid nonnegative measurement: ' + label)
    return value


def equal_number(a, b, label):
    if not math.isclose(finite(a, label), finite(b, label), rel_tol=1e-8, abs_tol=1e-6):
        raise ValueError('Recorded summary disagrees with runs: ' + label)


def stats(values):
    v = sorted(values)
    def percentile(p):
        position = (len(v) - 1) * p
        i = int(position)
        return v[i] + (v[min(i + 1, len(v) - 1)] - v[i]) * (position - i)
    return dict(n=len(v), min=v[0], max=v[-1], sum=sum(v), median=statistics.median(v), p95=percentile(.95))


def resource_record(path):
    for line in reversed(path.read_text().splitlines()):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and all(k in row for k in ['user', 'system', 'rss_kib', 'exit']):
            return row
    raise ValueError('Incomplete GNU-time resource file: ' + str(path))


class Evidence:
    def __init__(self):
        self.hashes, self.documents = {}, {}

    def digest(self, path):
        path = Path(path).resolve()
        value = hashlib.sha256(path.read_bytes()).hexdigest()
        if str(path) in self.hashes and self.hashes[str(path)] != value:
            raise ValueError('Source/result changed during aggregation: ' + str(path))
        self.hashes[str(path)] = value
        return value

    def document(self, path):
        path = Path(path).resolve()
        self.digest(path)
        if path not in self.documents:
            self.documents[path] = json.loads(path.read_text())
        return self.documents[path]

    def lines(self, path):
        self.digest(path)
        return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def ledger(path, evidence):
    path = Path(path).resolve()
    rows = evidence.lines(path)
    if any('selected' in row and type(row['selected']) is not bool for row in rows):
        raise ValueError('Ledger selected must be a JSON boolean')
    chosen = [(row, path.parent) for row in rows if row.get('selected') is True]
    if not chosen:
        raise ValueError('Ledger has no explicit selected result: ' + str(path))
    rejected = [row for row in rows if row.get('selected') is not True]
    reasons = Counter(str(row.get('reason', row.get('status', 'unspecified'))) for row in rejected)
    return chosen, {'path': str(path), 'sha256': evidence.digest(path), 'rows': len(rows),
                    'selected_rows': len(chosen), 'unselected_rows': len(rejected),
                    'explicit_deferrals': sum(row.get('deferred') is True or row.get('quiet_deferred_only') is True
                                              or 'defer' in str(row.get('reason', row.get('status', ''))).lower() for row in rejected),
                    'unselected_reasons': dict(reasons), 'original_rows': rows}


def row_path(row, base, full=False):
    for key in (('summary', 'summary_path', 'output', 'directory') if full else ('output', 'directory', 'output_dir')):
        if row.get(key):
            p = resolve(row[key], base)
            if full and p.is_dir():
                direct = p / 'summary.json'
                p = direct if direct.exists() else p / f"r{row.get('round', 0)}" / row['policy'] / 'summary.json'
            return p
    raise ValueError('Selected ledger row lacks a result path')


class Validator:
    def __init__(self, manifest_path, evidence):
        self.e = evidence
        self.manifest_path = Path(manifest_path).resolve()
        records = evidence.document(self.manifest_path)['files']
        self.files = {row['file']: row for row in records}
        if len(self.files) != len(records) or not self.files:
            raise ValueError('Manifest input filenames must be unique and nonempty')
        if any(f['production_threads'] not in [1, 2] for f in records):
            raise ValueError('Recorded production threads must be one or two')
        self.identity, self.golden, self.metadata, self.used = None, {}, {}, set()

    def experiment(self, summary_path):
        root = next((p for p in summary_path.parents if (p / 'experiment.json').is_file()), None)
        if root is None:
            raise ValueError('Cannot locate throughput experiment.json: ' + str(summary_path))
        if root in self.metadata:
            return root, self.metadata[root]
        m = self.e.document(root / 'experiment.json')
        if m['manifest_sha256'] != self.e.digest(self.manifest_path):
            raise ValueError('Selected campaign used another manifest')
        selected = {f['file']: f for f in m['files']}
        if len(selected) != len(m['files']) or any(self.files.get(name) != f for name, f in selected.items()):
            raise ValueError('Campaign inputs/options differ from frozen manifest')
        source = m.get('supplied_source_snapshot')
        if not source or not source.get('core_file_sha256') or not m.get('installed_file_sha256'):
            raise ValueError('Native installation and frozen source hashes are required')
        identity = {k: m[k] for k in ['launcher_sha256', 'version', 'installed_file_sha256']}
        identity['core_file_sha256'] = source['core_file_sha256']
        if self.identity is not None and identity != self.identity:
            raise ValueError('Selected results mix native builds or frozen sources')
        self.identity = identity
        binary = resolve(m['binary'], root)
        if self.e.digest(binary) != m['launcher_sha256']:
            raise ValueError('Installed launcher changed')
        for base, hashes in [(binary.parent.parent, m['installed_file_sha256']),
                             (resolve(source['root'], root), source['core_file_sha256'])]:
            for name, expected in hashes.items():
                path = resolve(name, base)
                if not path.is_relative_to(base) or self.e.digest(path) != expected:
                    raise ValueError('Installed native/source file changed: ' + str(path))
        if set(m['golden_sha256']) != set(selected) or m['input_count'] != len(selected):
            raise ValueError('Incomplete campaign golden/input manifest')
        for name, f in selected.items():
            golden = m['golden_sha256'][name]
            if name in self.golden and self.golden[name] != golden:
                raise ValueError('Selected campaigns used different goldens')
            self.golden[name] = golden
            if self.e.digest(resolve(name, resolve(m['golden'], root))) != golden:
                raise ValueError('Frozen golden changed: ' + name)
            if self.e.digest(resolve(name, resolve(m['data'], root))) != f['sha256']:
                raise ValueError('Original input changed: ' + name)
        self.metadata[root] = m
        return root, m

    def policy(self, path, policy, rnd, names):
        path = Path(path).resolve()
        if path in self.used:
            raise ValueError('Duplicate/reused selected summary: ' + str(path))
        self.used.add(path)
        root, m = self.experiment(path)
        s = self.e.document(path)
        config, concurrency = POLICIES[policy]
        if (s['policy'], s['round'], s['config'], s['concurrency']) != (policy, rnd, config, concurrency):
            raise ValueError('Selected policy/round/configuration mismatch')
        if policy not in m['selected_policies'] or rnd not in range(m['rounds']):
            raise ValueError('Selected policy/round absent from experiment')
        if set(names) != {f['file'] for f in m['files']}:
            raise ValueError('Selected policy has an incomplete/different input set')
        c = s.get('contention', {})
        if c.get('available') is not True or c.get('contention_flag') is not False or c.get('external_attribution_valid') is not True:
            raise ValueError('Selected policy is contended or its contention is unknown')
        if s.get('quiet_window', {}).get('ready') is not True:
            raise ValueError('Selected policy lacks a ready quiet-window record')
        if self.e.document(path.parent / 'quiet-window.json') != s['quiet_window']:
            raise ValueError('Quiet-window records disagree')
        order = list(m['files'])
        random.Random(m['seed'] + rnd).shuffle(order)
        if s['queue_order'] != [f['file'] for f in order]:
            raise ValueError('Queue order does not match the recorded seeded workload')
        runs = self.e.lines(path.parent / 'runs.jsonl')
        if len(runs) != len(names) or len({r['input'] for r in runs}) != len(runs) or {r['input'] for r in runs} != set(names):
            raise ValueError('Incomplete or duplicate policy runs')
        for r in runs:
            name = r['input']; f = self.files[name]
            actual = ('A' if f['production_threads'] == 1 else 'B') if config == 'production' else config
            if (r['policy'], r['round'], r['config'], r['input_sha256']) != (policy, rnd, actual, f['sha256']):
                raise ValueError('Run identity/configuration disagrees with policy')
            if r['returncode'] != 0 or r['timed_out'] is not False or r.get('resources_error') or not r.get('resources'):
                raise ValueError('Selected policy contains failed/incomplete runs')
            usage = r['resources']
            if usage.get('exit') != 0:
                raise ValueError('Missing/successless GNU-time resource record')
            cpu = finite(usage['user'], 'user CPU') + finite(usage['system'], 'system CPU')
            equal_number(r['cpu'], cpu, 'run CPU')
            finite(usage['rss_kib'], 'RSS')
            service = finite(r['service_seconds'], 'service', positive=True)
            equal_number(r['response_seconds'], finite(r['queue_seconds'], 'queue') + service, 'response')
            contention = r.get('contention', {})
            # Selection was prescribed at the policy level. A transient serial
            # job flag remains visible; it must not silently redefine selection.
            known_serial = (concurrency == 1 and type(contention.get('contention_flag')) is bool
                            and contention.get('external_attribution_valid') is True)
            other_worker = concurrency == 2 and contention.get('contention_flag') is None and contention.get('external_attribution_valid') is False
            if contention.get('available') is not True or not (known_serial or other_worker):
                raise ValueError('Selected run is contended or has unknown attribution')
            alignment = resolve(r['alignment'], path.parent)
            if not alignment.stat().st_size or self.e.digest(alignment) != r['output_sha256']:
                raise ValueError('Alignment result changed or is empty')
            if r['golden_sha256'] != self.golden[name] or r['matches_A'] is not (r['output_sha256'] == self.golden[name]):
                raise ValueError('Recorded golden match is inconsistent')
            if actual in ['A', 'F'] and not r['matches_A']:
                raise ValueError('Deterministic A/F output differs from frozen A')
            source = resolve(name, resolve(m['data'], root))
            expected = [str(resolve(m['binary'], root)), *FLAGS[actual], *f['production_options'], str(source)]
            if r['slot'] not in range(concurrency):
                raise ValueError('Invalid worker slot')
            affinity = str(r['slot']) if concurrency == 2 else '0,1'
            prefix = ['nice', '-n', '10', 'taskset', '-c', affinity]
            if (r['argv'] != prefix + expected or r['threads'] != int(FLAGS[actual][1]) or
                    r['recorded_production_threads'] != f['production_threads']):
                raise ValueError('Recorded command differs from production flags/thread configuration')
            resources = alignment.parent / 'resources.json'
            self.e.digest(resources)
            if resource_record(resources) != usage:
                raise ValueError('GNU-time resource file differs from its run record')
            if self.e.digest(alignment.parent / 'stderr.txt') != r['stderr_sha256']:
                raise ValueError('Run stderr changed')
        if s['jobs'] != len(runs) or s['matches_A'] != sum(r['matches_A'] for r in runs):
            raise ValueError('Recorded job/match totals disagree with runs')
        elapsed = finite(s['makespan_seconds'], 'makespan', positive=True)
        if elapsed + 1e-6 < max(r['response_seconds'] for r in runs):
            raise ValueError('Makespan is shorter than a completed response')
        for key, values in [('service_latency_seconds', [r['service_seconds'] for r in runs]),
                            ('queue_seconds', [r['queue_seconds'] for r in runs]),
                            ('response_latency_seconds', [r['response_seconds'] for r in runs])]:
            for k, expected in stats(values).items():
                equal_number(s[key][k], expected, key + '.' + k)
        equal_number(s['cpu_total_seconds'], sum(r['cpu'] for r in runs), 'CPU total')
        equal_number(s['cpu_per_job_seconds'], s['cpu_total_seconds'] / len(runs), 'CPU/job')
        equal_number(s['jobs_per_hour'], len(runs) * 3600 / elapsed, 'jobs/hour')
        equal_number(s['max_per_job_rss_mib'], max(r['resources']['rss_kib'] for r in runs) / 1024, 'per-job RSS')
        finite(s['peak_sampled_process_tree_rss_mib'], 'sampled concurrent RSS')
        row = {k: s[k] for k in ['policy', 'round', 'jobs', 'makespan_seconds', 'jobs_per_hour', 'cpu_per_job_seconds',
               'max_per_job_rss_mib', 'peak_sampled_process_tree_rss_mib', 'matches_A']}
        row.update(distinct_input_hashes=len({r['input_sha256'] for r in runs}), summary=str(path), summary_sha256=self.e.digest(path))
        row.update(serial_jobs_flagged=sum(r['contention']['contention_flag'] is True for r in runs),
                   worker_jobs_without_individual_attribution=sum(r['contention']['contention_flag'] is None for r in runs))
        for key, prefix in [('service_latency_seconds', 'service'), ('queue_seconds', 'queue'), ('response_latency_seconds', 'response')]:
            for k in ['median', 'p95']:
                row[prefix + '_' + k + '_seconds'] = s[key][k]
        return row, runs, s['queue_order']


def campaign(row, base, validator, policies, expected_names=None):
    root = row_path(row, base)
    _, m = validator.experiment(root / 'r0' / policies[0] / 'summary.json')
    names = [f['file'] for f in m['files']]
    if (m['rounds'] != 2 or set(m['selected_policies']) != set(policies) or
            (expected_names is not None and set(names) != set(expected_names))):
        raise ValueError('Two-round campaign has incomplete/different inputs')
    results = []
    for rnd in [0, 1]:
        orders = []
        for policy in policies:
            result, runs, order = validator.policy(root / f'r{rnd}' / policy / 'summary.json', policy, rnd, names)
            results.append((result, runs)); orders.append(order)
        if any(order != orders[0] for order in orders[1:]):
            raise ValueError('Policies within a pilot/isolated round used different input orders')
    return names, results


def write_table(path, rows):
    with path.open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter='\t', lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def aggregate(args):
    e = Evidence(); validator = Validator(args.manifest, e)
    full_selected, full_prov = ledger(args.full_selection, e)
    isolated_selected, isolated_prov = ledger(args.isolated_selection, e)
    pilot_selected, pilot_prov = ledger(args.pilot_selection, e)
    if len(pilot_selected) != 1:
        raise ValueError('Select exactly one complete two-round pilot campaign')
    full, orders, policies = [], [], set()
    for selected, base in full_selected:
        policy = selected['policy']
        if policy not in FULL or policy in policies:
            raise ValueError('Duplicate/unknown selected full policy')
        policies.add(policy)
        path = row_path(selected, base, full=True)
        result, _, order = validator.policy(path, policy, selected.get('round', e.document(path)['round']), list(validator.files))
        full.append(result); orders.append(order)
    if policies != set(FULL) or any(order != orders[0] for order in orders[1:]):
        raise ValueError('Full selection needs every policy with identical input sets/orders')
    pilot_row, pilot_base = pilot_selected[0]
    pilot_names, pilot = campaign(pilot_row, pilot_base, validator, PILOT)
    isolated, seen = [], set()
    for selected, base in isolated_selected:
        name = selected.get('input', selected.get('file'))
        if name in seen or name not in pilot_names:
            raise ValueError('Duplicate/unknown selected isolated input')
        seen.add(name)
        _, results = campaign(selected, base, validator, ISOLATED, [name])
        for policy in ISOLATED:
            rr = [runs[0] for result, runs in results if result['policy'] == policy]
            hashes = sorted({r['output_sha256'] for r in rr})
            isolated.append({'input': name, 'input_sha256': validator.files[name]['sha256'], 'policy': policy,
                'config': POLICIES[policy][0], 'repeats': 2,
                'mean_service_seconds': statistics.mean(r['service_seconds'] for r in rr),
                'min_service_seconds': min(r['service_seconds'] for r in rr), 'max_service_seconds': max(r['service_seconds'] for r in rr),
                'mean_cpu_seconds': statistics.mean(r['cpu'] for r in rr),
                'mean_per_job_rss_mib': statistics.mean(r['resources']['rss_kib'] / 1024 for r in rr),
                'max_per_job_rss_mib': max(r['resources']['rss_kib'] / 1024 for r in rr),
                'matches_A': sum(r['matches_A'] for r in rr), 'distinct_output_hashes': len(hashes),
                'serial_jobs_flagged': sum(r['contention']['contention_flag'] is True for r in rr),
                'repeatable_in_two_runs': len(hashes) == 1, 'output_hashes': hashes})
    if seen != set(pilot_names):
        raise ValueError('Isolated selection does not cover the frozen pilot/latency input set')
    by_config = []
    for policy in ISOLATED:
        rows = [r for r in isolated if r['policy'] == policy]
        latency = stats([r['mean_service_seconds'] for r in rows])
        by_config.append({'policy': policy, 'config': POLICIES[policy][0], 'input_files': len(rows),
            'distinct_input_hashes': len({r['input_sha256'] for r in rows}), 'measured_runs': 2 * len(rows),
            'mean_file_mean_latency_seconds': latency['sum'] / len(rows),
            'median_file_mean_latency_seconds': latency['median'], 'p95_file_mean_latency_seconds': latency['p95'],
            'mean_file_mean_cpu_seconds': statistics.mean(r['mean_cpu_seconds'] for r in rows),
            'max_per_job_rss_mib': max(r['max_per_job_rss_mib'] for r in rows),
            'matches_A': sum(r['matches_A'] for r in rows),
            'serial_jobs_flagged': sum(r['serial_jobs_flagged'] for r in rows),
            'inputs_repeatable_in_two_runs': sum(r['repeatable_in_two_runs'] for r in rows)})
    pilot_rows = [result for result, _ in pilot]
    ranges = []
    for policy in PILOT:
        rows = [r for r in pilot_rows if r['policy'] == policy]
        row = {'policy': policy, 'rounds': 2, 'jobs_per_round': len(pilot_names)}
        for metric in ['makespan_seconds', 'jobs_per_hour', 'cpu_per_job_seconds', 'service_median_seconds', 'service_p95_seconds',
                       'queue_median_seconds', 'queue_p95_seconds', 'response_median_seconds', 'response_p95_seconds',
                       'max_per_job_rss_mib', 'peak_sampled_process_tree_rss_mib', 'matches_A', 'serial_jobs_flagged']:
            row[metric + '_min'] = min(r[metric] for r in rows)
            row[metric + '_max'] = max(r[metric] for r in rows)
        ranges.append(row)
    return {'schema': 'mafft-round2-scheduling-summary-v1', 'manifest': str(validator.manifest_path),
        'manifest_sha256': e.digest(validator.manifest_path), 'production_files': len(validator.files),
        'distinct_production_input_hashes': len({f['sha256'] for f in validator.files.values()}),
        'native_build_and_source': validator.identity, 'golden_sha256': validator.golden,
        'full_policies': sorted(full, key=lambda r: FULL.index(r['policy'])), 'full_planned_queue_order': orders[0],
        'isolated_per_input': isolated, 'isolated_by_config': by_config, 'pilot_rounds': pilot_rows, 'pilot_ranges': ranges,
        'selected_latency_inputs': pilot_names,
        'distinct_latency_input_hashes': len({validator.files[name]['sha256'] for name in pilot_names}),
        'selection_ledgers': {'full': full_prov, 'isolated': isolated_prov, 'pilot': pilot_prov},
        'source_and_result_sha256': e.hashes,
        'interpretation': {
            'selection': 'Consume explicit ledger selections only; never choose by speed. Unselected attempts and deferrals remain in provenance.',
            'workload': f"Full replay retains every production filename, including repeated copies of raw hashes ({len(validator.files)} jobs versus {len({f['sha256'] for f in validator.files.values()})} hashes). No deduplication changes throughput denominators.",
            'latency': f'The frozen selected pilot/latency subset ({len(pilot_names)} inputs) has exactly two isolated repeats per input/configuration. Aggregate latency gives each production filename one vote after averaging its two repeats; it is not the full-corpus latency distribution.',
            'arrival': 'Saturated backlog: all jobs arrive at zero. Service, queue delay and response are distinct; jobs/hour is jobs divided by policy makespan.',
            'contention': 'Selections use explicitly uncontended policy aggregates. Transient serial-job flags are retained and counted, not a reason to redefine the prospective selection rule. Concurrent-worker per-job attribution includes the other measured worker; only the policy aggregate establishes observed external quiet. Host-side interference is not ruled out.',
            'memory': 'GNU-time per-job peak RSS and sampled simultaneous process-group RSS are different statistics. The sampled concurrent maximum is not an exact peak.',
            'outputs': 'A/F must match frozen A bytes. B/C and production B jobs can change outputs; two-repeat hash stability is finite evidence, not a universal guarantee or a biological-accuracy conclusion.',
            'uncertainty': 'Two-round ranges are descriptive controls. No statistical timing-noise confidence interval or independent biological replication is claimed.'}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ['full-selection', 'isolated-selection', 'pilot-selection', 'manifest', 'output']:
        p.add_argument('--' + key, required=True, type=Path)
    args = p.parse_args()
    result = aggregate(args)
    args.output.mkdir(parents=True, exist_ok=False)
    for name, key in [('full-policies.tsv', 'full_policies'), ('isolated-per-input.tsv', 'isolated_per_input'),
                      ('isolated-by-config.tsv', 'isolated_by_config'), ('pilot-rounds.tsv', 'pilot_rounds'),
                      ('pilot-ranges.tsv', 'pilot_ranges')]:
        write_table(args.output / name, result[key])
    result['aggregator_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result['command'] = sys.argv
    with (args.output / 'summary.json').open('x') as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'output': str(args.output.resolve()), 'full_policies': len(result['full_policies']),
                      'latency_inputs': len(result['selected_latency_inputs'])}, sort_keys=True))


if __name__ == '__main__':
    main()
