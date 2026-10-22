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

import isolated_batch as batch

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
        if m.get('schema') == batch.SCHEMA:
            self.batch_experiment(root, m)
        self.metadata[root] = m
        return root, m

    def batch_experiment(self, root, m):
        """Check the prospective short cohort and its independently frozen evidence."""
        if (m.get('protocol') != batch.SCHEMA or m['rounds'] != batch.ROUNDS or
                m['paired_rounds'] != batch.ROUNDS or m['native_repeats_per_round'] != batch.REPLICATES or
                m['native_repeats_per_input'] != batch.REPLICATES * batch.ROUNDS or
                m['selected_policies'] != list(batch.POLICIES) or len(m['files']) != 1 or
                m['policy_order_by_round'] != [batch.policy_order(r) for r in range(batch.ROUNDS)] or
                m['selection_contention_source'] != batch.SELECTION_FIELD):
            raise ValueError('Short-input batch protocol changed')
        config = m['quiet_window']
        if (config.get('enabled') is not True or config.get('max_busy_cores') != batch.CEILING or
                config.get('max_scientific_cores') != .05):
            raise ValueError('Short-input batch quiet ceilings changed')
        priority = m['priority']
        if (priority['driver_at_measurement'], priority['gnu_time_inherits_driver_nice'],
                priority['native_nice_increment'], priority['expected_native_nice']) != (10, 10, 10, 19):
            raise ValueError('Short-input batch priority protocol changed')
        eligible = m['eligibility']
        runs_path, plan_path = resolve(eligible['runs'], root), resolve(eligible['experiment'], root)
        if (self.e.digest(runs_path) != eligible['full_corpus_runs_sha256'] or
                self.e.digest(plan_path) != eligible['full_corpus_experiment_sha256']):
            raise ValueError('Frozen full-corpus eligibility evidence changed')
        plan = self.e.document(plan_path)
        if plan['manifest_sha256'] != self.e.digest(self.manifest_path):
            raise ValueError('Eligibility full corpus used another manifest')
        build, means, short = batch.eligibility(self.e.document(self.manifest_path), plan,
                                               self.e.lines(runs_path), eligible['build'])
        name = m['files'][0]['file']
        if (name not in short or eligible['strict_threshold_seconds'] != batch.SHORT_SECONDS or
                eligible['metric'] != 'mean A wall across all frozen corpus rounds' or
                eligible['input_mean_A_wall_seconds'] != means[name] or eligible['eligible_short_inputs'] != short):
            raise ValueError('Input is outside the prespecified short full-corpus cohort')
        if any(build[k] != m[k] for k in ('launcher_sha256', 'installed_file_sha256', 'version')):
            raise ValueError('Batch differs from the frozen eligibility native build')
        if self.e.digest('/usr/bin/time') != m['gnu_time_sha256']:
            raise ValueError('GNU-time binary changed')
        harness = m['harness_source_at_startup']
        if set(harness) != {'isolated_batch', 'throughput', 'quiet', 'alignments'}:
            raise ValueError('Batch harness provenance is incomplete')
        for record in harness.values():
            retained = resolve(record['retained_copy'], root)
            if not retained.is_relative_to(root / 'harness') or self.e.digest(retained) != record['sha256']:
                raise ValueError('Retained batch harness source changed')

    def policy(self, path, policy, rnd, names):
        path = Path(path).resolve()
        if path in self.used:
            raise ValueError('Duplicate/reused selected summary: ' + str(path))
        self.used.add(path)
        root, m = self.experiment(path)
        if m.get('schema') == batch.SCHEMA:
            return self.batch_policy(path, policy, rnd, names, root, m)
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
        row.update(policy_raw_cpu_residual_seconds=c.get('global_busy_minus_job_cpu_seconds_raw'),
                   policy_external_busy_cores_estimate=c.get('external_busy_cores_estimate'),
                   policy_global_busy_seconds=c.get('global_busy_seconds'),
                   policy_accounted_job_cpu_seconds=c.get('accounted_job_cpu_seconds'),
                   observer_cpu_seconds=s.get('observer_cpu_seconds'),
                   observer_cpu_cores=s.get('observer_cpu_cores'))
        for key, prefix in [('service_latency_seconds', 'service'), ('queue_seconds', 'queue'), ('response_latency_seconds', 'response')]:
            for k in ['median', 'p95']:
                row[prefix + '_' + k + '_seconds'] = s[key][k]
        return row, runs, s['queue_order']

    def batch_policy(self, path, policy, rnd, names, root, m):
        if policy not in batch.POLICIES or names != [m['files'][0]['file']] or rnd not in range(batch.ROUNDS):
            raise ValueError('Invalid selected batch input/policy/round')
        name = names[0]; f = self.files[name]
        s, runs = self.e.document(path), self.e.lines(path.parent / 'runs.jsonl')
        batch.validate_batch_summary(s, runs, name, policy, rnd)
        chosen = s[batch.SELECTION_FIELD]
        if (chosen.get('available') is not True or chosen.get('contention_flag') is not False or
                chosen.get('external_attribution_valid') is not True):
            raise ValueError('Selected batch is contended or its contention is unknown')
        if (s['quiet_window'].get('ready') is not True or s['quiet_window'].get('settings') != m['quiet_window'] or
                self.e.document(path.parent / 'quiet-window.json') != s['quiet_window']):
            raise ValueError('Batch quiet-window records disagree or are not ready')
        config = POLICIES[policy][0]
        for r in runs:
            if (r['input_sha256'] != f['sha256'] or r['threads'] != int(FLAGS[config][1]) or
                    r['recorded_production_threads'] != f['production_threads']):
                raise ValueError('Batch run input/thread identity changed')
            source = resolve(name, resolve(m['data'], root))
            expected = ['nice', '-n', '10', 'taskset', '-c', '0,1', str(resolve(m['binary'], root)),
                        *FLAGS[config], *f['production_options'], str(source)]
            if r['argv'] != expected:
                raise ValueError('Batch command differs from production options')
            alignment = resolve(r['alignment'], path.parent)
            if alignment != path.parent / f"replicate-{r['replicate']:03d}" / name / 'alignment.fasta':
                raise ValueError('Batch replicate reuses another result directory')
            if not alignment.stat().st_size or self.e.digest(alignment) != r['output_sha256']:
                raise ValueError('Batch alignment result changed or is empty')
            if (r['golden_sha256'] != self.golden[name] or
                    r['matches_A'] is not (r['output_sha256'] == self.golden[name])):
                raise ValueError('Batch golden match is inconsistent')
            resources = alignment.parent / 'resources.json'
            self.e.digest(resources)
            if resource_record(resources) != r['resources']:
                raise ValueError('Batch GNU-time resource file differs from its run')
            finite(r['resources']['rss_kib'], 'batch per-job RSS')
            if self.e.digest(alignment.parent / 'stderr.txt') != r['stderr_sha256']:
                raise ValueError('Batch stderr changed')
        if s['matches_A'] != sum(r['matches_A'] for r in runs):
            raise ValueError('Batch golden match count changed')
        for key, field in [('service_latency_seconds', 'service_seconds'), ('queue_seconds', 'queue_seconds'),
                           ('response_latency_seconds', 'response_seconds')]:
            for k, expected in stats([r[field] for r in runs]).items():
                equal_number(s[key][k], expected, 'batch ' + key + '.' + k)
        equal_number(s['cpu_per_job_seconds'], s['cpu_total_seconds']/batch.REPLICATES, 'batch GNU CPU/job')
        equal_number(s['jobs_per_hour'], batch.REPLICATES*3600/s['makespan_seconds'], 'batch descriptive jobs/hour')
        equal_number(s['max_per_job_rss_mib'], max(r['resources']['rss_kib'] for r in runs)/1024, 'batch GNU RSS')
        finite(s['peak_sampled_process_tree_rss_mib'], 'batch sampled RSS')
        row = {k: s[k] for k in ['policy', 'round', 'jobs', 'makespan_seconds', 'jobs_per_hour',
               'cpu_per_job_seconds', 'child_cpu_per_job_seconds', 'max_per_job_rss_mib',
               'peak_sampled_process_tree_rss_mib', 'matches_A']}
        row.update(protocol=batch.SCHEMA, paired_rounds=batch.ROUNDS, native_repeats_per_round=batch.REPLICATES,
                   selection_contention_source=batch.SELECTION_FIELD,
                   short_threshold_seconds=m['eligibility']['strict_threshold_seconds'],
                   eligibility_full_corpus_runs_sha256=m['eligibility']['full_corpus_runs_sha256'],
                   eligibility_full_corpus_experiment_sha256=m['eligibility']['full_corpus_experiment_sha256'],
                   distinct_input_hashes=1, summary=str(path), summary_sha256=self.e.digest(path),
                   serial_jobs_flagged=sum(r['contention']['contention_flag'] is True for r in runs),
                   worker_jobs_without_individual_attribution=0)
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
    if m.get('schema') == batch.SCHEMA:
        status = validator.e.document(root / 'STATUS.json')
        summaries = [validator.e.document(Path(result['summary'])) for result, _ in results]
        if status.get('finished') is not True or status.get('accepted') is not True or not batch.clean_batch_set(summaries):
            raise ValueError('Selected batch needs the complete clean eight-window set')
        by_key = {(s['round'], s['policy']): s for s in summaries}
        ordered = [by_key[(r, p)] for r in range(batch.ROUNDS) for p in batch.policy_order(r)]
        for first, second in zip(ordered, ordered[1:]):
            if (first['cpu_stat_after']['monotonic'] > second['cpu_stat_before']['monotonic'] or
                    first['rusage_children_after']['pid'] != second['rusage_children_before']['pid'] or
                    first['rusage_children_after']['monotonic'] > second['rusage_children_before']['monotonic']):
                raise ValueError('Batch policy rotation overlaps or mixes parent processes')
    return names, results


def isolated_input_rows(name, results, input_sha256):
    """Average technical replicates within each round, then weight both rounds equally."""
    rows = []
    for policy in ISOLATED:
        groups = sorted([(result, runs) for result, runs in results if result['policy'] == policy],
                        key=lambda group: group[0]['round'])
        if len(groups) != 2 or [result['round'] for result, _ in groups] != [0, 1]:
            raise ValueError('Isolated input needs both paired rounds')
        counts = {len(runs) for _, runs in groups}
        if len(counts) != 1 or next(iter(counts)) not in (1, batch.REPLICATES):
            raise ValueError('Unequal or unsupported within-round replicate counts')
        is_batch = all(result.get('protocol') == batch.SCHEMA for result, _ in groups)
        if is_batch is not (next(iter(counts)) == batch.REPLICATES):
            raise ValueError('Batch counts/protocol disagree')
        rr = [r for _, runs in groups for r in runs]
        hashes = sorted({r['output_sha256'] for r in rr})
        first = groups[0][0]
        rows.append({'input': name, 'input_sha256': input_sha256, 'policy': policy, 'config': POLICIES[policy][0],
            'protocol': batch.SCHEMA if is_batch else 'legacy-isolated-n1-two-rounds',
            'repeats': len(rr), 'native_repeats_per_input': len(rr), 'paired_rounds': 2,
            'native_repeats_per_round': len(groups[0][1]),
            'averaging': 'arithmetic mean within each round, then arithmetic mean of the two round means',
            'mean_service_seconds': statistics.mean(statistics.mean(r['service_seconds'] for r in runs) for _, runs in groups),
            'min_service_seconds': min(r['service_seconds'] for r in rr), 'max_service_seconds': max(r['service_seconds'] for r in rr),
            'mean_cpu_seconds': statistics.mean(statistics.mean(r['cpu'] for r in runs) for _, runs in groups),
            'cpu_accounting_source': 'Original per-job GNU time user+system, unchanged',
            'mean_child_tree_cpu_seconds': statistics.mean(result['child_cpu_per_job_seconds'] for result, _ in groups) if is_batch else None,
            'child_tree_cpu_scope': 'Policy waited child-tree CPU divided by 20, including GNU time; diagnostic only' if is_batch else None,
            'mean_per_job_rss_mib': statistics.mean(statistics.mean(r['resources']['rss_kib']/1024 for r in runs) for _, runs in groups),
            'max_per_job_rss_mib': max(r['resources']['rss_kib']/1024 for r in rr),
            'matches_A': sum(r['matches_A'] for r in rr), 'distinct_output_hashes': len(hashes),
            'serial_jobs_flagged': sum(r['contention']['contention_flag'] is True for r in rr),
            'repeatable_across_native_runs': len(hashes) == 1, 'output_hashes': hashes,
            'selection_contention_source': batch.SELECTION_FIELD if is_batch else 'contention (original summed GNU CPU)',
            'short_threshold_seconds': first['short_threshold_seconds'] if is_batch else None,
            'eligibility_full_corpus_runs_sha256': first['eligibility_full_corpus_runs_sha256'] if is_batch else None,
            'eligibility_full_corpus_experiment_sha256': first['eligibility_full_corpus_experiment_sha256'] if is_batch else None})
    return rows


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
        isolated.extend(isolated_input_rows(name, results, validator.files[name]['sha256']))
    if seen != set(pilot_names):
        raise ValueError('Isolated selection does not cover the frozen pilot/latency input set')
    by_config = []
    for policy in ISOLATED:
        rows = [r for r in isolated if r['policy'] == policy]
        latency = stats([r['mean_service_seconds'] for r in rows])
        by_config.append({'policy': policy, 'config': POLICIES[policy][0], 'input_files': len(rows),
            'distinct_input_hashes': len({r['input_sha256'] for r in rows}),
            'measured_runs': sum(r['native_repeats_per_input'] for r in rows), 'paired_rounds': 2,
            'native_repeats_per_input': {r['input']: r['native_repeats_per_input'] for r in rows},
            'mean_file_mean_latency_seconds': latency['sum'] / len(rows),
            'median_file_mean_latency_seconds': latency['median'], 'p95_file_mean_latency_seconds': latency['p95'],
            'mean_file_mean_cpu_seconds': statistics.mean(r['mean_cpu_seconds'] for r in rows),
            'max_per_job_rss_mib': max(r['max_per_job_rss_mib'] for r in rows),
            'matches_A': sum(r['matches_A'] for r in rows),
            'serial_jobs_flagged': sum(r['serial_jobs_flagged'] for r in rows),
            'inputs_repeatable_across_native_runs': sum(r['repeatable_across_native_runs'] for r in rows)})
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
    return {'schema': 'mafft-round2-scheduling-summary-v2', 'manifest': str(validator.manifest_path),
        'manifest_sha256': e.digest(validator.manifest_path), 'production_files': len(validator.files),
        'distinct_production_input_hashes': len({f['sha256'] for f in validator.files.values()}),
        'native_build_and_source': validator.identity, 'golden_sha256': validator.golden,
        'full_policies': sorted(full, key=lambda r: FULL.index(r['policy'])), 'full_planned_queue_order': orders[0],
        'isolated_per_input': isolated, 'isolated_by_config': by_config, 'pilot_rounds': pilot_rows, 'pilot_ranges': ranges,
        'selected_latency_inputs': pilot_names,
        'paired_rounds': 2,
        'native_repeats_per_input': {r['input']: r['native_repeats_per_input'] for r in isolated if r['policy'] == ISOLATED[0]},
        'isolated_averaging': 'Within-round replicate means, then two-round means; each input has equal weight in the latency summary.',
        'selection_contention_sources': sorted({r['selection_contention_source'] for r in isolated}),
        'short_batch_threshold_seconds': batch.SHORT_SECONDS,
        'short_batch_eligibility_full_corpus_runs_sha256': sorted({r['eligibility_full_corpus_runs_sha256'] for r in isolated if r['eligibility_full_corpus_runs_sha256']}),
        'short_batch_eligibility_full_corpus_experiment_sha256': sorted({r['eligibility_full_corpus_experiment_sha256'] for r in isolated if r['eligibility_full_corpus_experiment_sha256']}),
        'distinct_latency_input_hashes': len({validator.files[name]['sha256'] for name in pilot_names}),
        'selection_ledgers': {'full': full_prov, 'isolated': isolated_prov, 'pilot': pilot_prov},
        'source_and_result_sha256': e.hashes,
        'interpretation': {
            'selection': 'Consume explicit ledger selections only; never choose by speed. Unselected attempts and deferrals remain in provenance.',
            'workload': f"Full replay retains every production filename, including repeated copies of raw hashes ({len(validator.files)} jobs versus {len({f['sha256'] for f in validator.files.values()})} hashes). No deduplication changes throughput denominators.",
            'latency': f'The frozen selected pilot/latency subset ({len(pilot_names)} inputs) has two paired isolated rounds per input/configuration. Prospectively short inputs use 20 native invocations in each round; other inputs retain one per round. Average within each round, then average both rounds; each production filename receives one vote. Native replicates are technical repeats, not independent biological datasets; this is not the full-corpus latency distribution.',
            'arrival': 'Full/pilot throughput uses a saturated backlog with all jobs arriving at zero. Isolated measurements launch one invocation at a time; new short batches record queue=0 and response=service. Batch descriptive jobs/hour is not sustained throughput.',
            'contention': 'Selections use policy aggregates passing the prespecified counter filter. The new short protocol uses the separately named RUSAGE_CHILDREN policy delta, including GNU time, without subtracting observer CPU; original GNU CPU, raw aggregate contention and all per-job flags are retained. Long inputs and full/pilot runs retain original GNU-based selection. All eight batch policy/round windows must pass together. Passing the filter does not prove physical CPU isolation or rule out host interference.',
            'signed_cpu_residual': 'Global busy minus job CPU is exported before clamping. A negative residual is a disagreement between the global and accumulated process counters, not evidence of zero external work. Large negative residuals cannot be explained by endpoint centisecond rounding alone; no cause is established here and the original flags/selections remain unchanged.',
            'cpu': 'Mean CPU comparisons retain original GNU user+system for every input. The separate child-tree CPU diagnostic includes GNU time and is available only for short batches; it is not substituted into mean_cpu_seconds.',
            'memory': 'GNU-time per-job peak RSS and sampled simultaneous process-group RSS are different statistics. The sampled concurrent maximum is not an exact peak.',
            'outputs': 'A/F must match frozen A bytes. B/C and production B jobs can change outputs; hash stability over the recorded 2 or 40 native invocations is finite evidence, not a universal guarantee or a biological-accuracy conclusion.',
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
