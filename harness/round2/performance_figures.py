#!/usr/bin/env python3
"""Plot completed performance/scheduling summaries, without launching native work.

Each analysis argument accepts its directory or summary.json. Performance inputs
must include per-input.tsv and the paired experiment.json next to recorded runs.
Scheduling inputs use summarize_scheduling.py's v1 schema. All three cohorts must
share the frozen manifest, full filename/hash set, goldens and candidate native
installation. Native binaries are not opened or executed by this tool.

Matplotlib is imported only by main, after validation. Every output directory and
file is new. ECDF observations are paired-round file means or distinct-hash means;
isolated latency percentiles describe the selected subset's two-round file means,
retaining each input's actual native repetition count.
No round ranges or workload-bootstrap intervals are plotted as timing-noise CIs.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys


FULL = ('production', 'two_threads', 'two_jobs', 'selective', 'refine_one')
ISOLATED = ('one_job', 'two_threads', 'selective', 'refine_one')
CONFIGS = dict(one_job='A', two_threads='B', selective='C', refine_one='F')
POLICY_LABELS = {
    'production': 'Recorded production threads\n(one job at a time)',
    'two_threads': 'B: two threads\n(one job at a time)',
    'two_jobs': 'A: one thread per job\n(two concurrent jobs)',
    'selective': 'C: two threads, threadit 0',
    'refine_one': 'F: two threads, threadit 1',
}
COLORS = dict(production='#777777', two_threads='#E69F00', two_jobs='#0072B2',
              selective='#009E73', refine_one='#CC79A7')


def positive(value, name):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Expected positive finite value: ' + name)
    return value


def count(value, name):
    if isinstance(value, bool):
        raise ValueError('Expected nonnegative integer: ' + name)
    number = int(value)
    if number < 0 or str(number) != str(value):
        raise ValueError('Expected nonnegative integer: ' + name)
    return number


def same_number(value, expected, name):
    if not math.isclose(float(value), expected, rel_tol=1e-8, abs_tol=1e-6):
        raise ValueError('Summary/table disagreement: ' + name)


def percentile(values, p):
    values = sorted(values)
    position = (len(values) - 1) * p
    i = int(position)
    return values[i] + (values[min(i + 1, len(values) - 1)] - values[i]) * (position - i)


def resolve(value, base):
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve()


class Sources:
    """Hash only the small summary/table/experiment files actually consumed."""
    def __init__(self):
        self.hashes = {}

    def read(self, path):
        path = Path(path).resolve()
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if str(path) in self.hashes and self.hashes[str(path)] != digest:
            raise ValueError('Input changed during figure preparation: ' + str(path))
        self.hashes[str(path)] = digest
        return data.decode('utf-8')

    def document(self, path):
        return json.loads(self.read(path))


def summary_path(value):
    value = Path(value).resolve()
    return value / 'summary.json' if value.is_dir() else value


def native_signature(record):
    signature = {key: record[key] for key in ('launcher_sha256', 'version', 'installed_file_sha256')}
    if not all(signature.values()):
        raise ValueError('Incomplete recorded native build signature')
    return signature


def unique_hash_rows(rows):
    """Average filename copies' times before calculating one ratio per hash."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['input_sha256']].append(row)
    result = []
    for digest, copies in sorted(grouped.items()):
        out = {'comparison': copies[0]['comparison'], 'reference': copies[0]['reference'],
               'candidate': copies[0]['candidate'], 'input_sha256': digest,
               'production_filenames': sorted(row['input'] for row in copies), 'filename_copies': len(copies)}
        for metric in ('wall', 'cpu'):
            for build in ('reference', 'candidate'):
                out[build + '_' + metric + '_seconds'] = statistics.mean(row[build + '_' + metric + '_seconds'] for row in copies)
            out[metric + '_speedup'] = out['reference_' + metric + '_seconds'] / out['candidate_' + metric + '_seconds']
        out['paired_rounds_by_filename'] = {row['input']: row['paired_rounds'] for row in copies}
        for build in ('reference', 'candidate'):
            for state in ('flagged', 'unknown'):
                out[build + '_contention_' + state + '_rounds'] = sum(row[build + '_contention_' + state + '_rounds'] for row in copies)
        result.append(out)
    return result


def load_performance(path, role, sources):
    path = summary_path(path)
    summary = sources.document(path)
    with_lines = sources.read(path.parent / 'per-input.tsv').splitlines()
    records = list(csv.DictReader(with_lines, delimiter='\t'))
    if not records:
        raise ValueError('Performance table is empty')
    reference, candidate = summary['reference'], summary['candidate']
    if reference == candidate:
        raise ValueError('Performance reference and candidate must differ')
    rows, seen = [], set()
    for record in records:
        name = record['input']
        if name in seen or record['all_exact'] != 'True':
            raise ValueError('Duplicate input or non-exact performance row')
        seen.add(name)
        rounds = count(record['paired_rounds'], 'paired rounds')
        if not rounds:
            raise ValueError('Performance input has no paired rounds')
        row = {key: record[key] for key in ('input', 'input_sha256', 'partition')}
        row.update(comparison=role, reference=reference, candidate=candidate, paired_rounds=rounds)
        for metric in ('wall', 'cpu'):
            for build in ('reference', 'candidate'):
                row[build + '_' + metric + '_seconds'] = positive(record[build + '_' + metric], metric)
            row[metric + '_speedup'] = row['reference_' + metric + '_seconds'] / row['candidate_' + metric + '_seconds']
            same_number(record[metric + '_speedup'], row[metric + '_speedup'], metric + ' speedup')
        for build in ('reference', 'candidate'):
            for state in ('flagged', 'unknown'):
                key = build + '_contention_' + state + '_rounds'
                row[key] = count(record[key], key)
            if sum(row[build + '_contention_' + state + '_rounds'] for state in ('flagged', 'unknown')) > rounds:
                raise ValueError('Contention counts exceed paired rounds')
        rows.append(row)
    expected_jobs = sum(row['paired_rounds'] for row in rows)
    if summary['completed_exact_jobs'] != 2 * expected_jobs:
        raise ValueError('Performance completed-job denominator disagrees')
    contention = []
    for build, name in [('reference', reference), ('candidate', candidate)]:
        item = summary['contention'][name]
        flagged = sum(row[build + '_contention_flagged_rounds'] for row in rows)
        unknown = sum(row[build + '_contention_unknown_rounds'] for row in rows)
        expected = dict(jobs=expected_jobs, flagged=flagged, unknown=unknown, not_flagged=expected_jobs - flagged - unknown)
        if any(item[key] != number for key, number in expected.items()):
            raise ValueError('Performance contention summary disagrees')
        contention.append({'cohort': role, 'kind': 'paired_build', 'build_or_policy': name,
                           'measured_jobs': expected_jobs, 'flagged_jobs': flagged,
                           'not_flagged_jobs': expected['not_flagged'], 'unknown_job_flags': unknown,
                           'policy_contention_flag': None, 'individual_external_attribution_unavailable': unknown,
                           'max_external_busy_cores_estimate': item.get('max_external_busy_cores_estimate')})
    unique = unique_hash_rows(rows)
    totals = {record['partition']: record for record in summary['totals']}
    if len(totals) != len(summary['totals']):
        raise ValueError('Duplicate performance total partition')
    for partition, selected in [('all', rows), ('all_unique_inputs', unique)]:
        total = totals[partition]
        if total['jobs_per_corpus_pass'] != len(selected) or total['distinct_input_hashes'] != len({row['input_sha256'] for row in selected}):
            raise ValueError('Performance filename/hash denominator disagrees')
        for metric in ('wall', 'cpu'):
            a = sum(row['reference_' + metric + '_seconds'] for row in selected)
            b = sum(row['candidate_' + metric + '_seconds'] for row in selected)
            for key, value in [(metric + '_reference', a), (metric + '_candidate', b), (metric + '_speedup', a / b)]:
                same_number(total[key], value, partition + ' ' + key)
    # Check recorded candidate bytes against the scheduling installation. The
    # source/binary correspondence remains the retained build audit's evidence.
    runs = resolve(summary['runs'], path.parent)
    experiment_path = runs.parent / 'experiment.json'
    experiment = sources.document(experiment_path)
    builds = [build for build in experiment['builds'] if build['name'] == candidate]
    if len(builds) != 1 or experiment['manifest_sha256'] != summary['manifest_sha256']:
        raise ValueError('Paired experiment/candidate provenance disagrees')
    experiment_inputs = {record['file']: record['sha256'] for record in experiment['files']}
    if len(experiment_inputs) != len(experiment['files']) or experiment_inputs != {row['input']: row['input_sha256'] for row in rows}:
        raise ValueError('Paired experiment has another input set')
    return {'path': path, 'summary': summary, 'rows': rows, 'unique': unique, 'contention': contention,
            'candidate_native': native_signature(builds[0]), 'goldens': experiment['golden_sha256'],
            'experiment': str(experiment_path)}


def prepare(dikarya2_analysis, opt8_analysis, scheduling_analysis):
    sources = Sources()
    performances = [load_performance(dikarya2_analysis, 'dikarya2-comparison', sources),
                    load_performance(opt8_analysis, 'opt8-comparison', sources)]
    path = summary_path(scheduling_analysis)
    scheduling = sources.document(path)
    if scheduling['schema'] not in ('mafft-round2-scheduling-summary-v1', 'mafft-round2-scheduling-summary-v2'):
        raise ValueError('Unsupported scheduling summary schema')
    mixed_protocol = scheduling['schema'] == 'mafft-round2-scheduling-summary-v2'
    inputs = {row['input']: row['input_sha256'] for row in performances[0]['rows']}
    candidates = {performance['summary']['candidate'] for performance in performances}
    if len(candidates) != 1 or performances[0]['summary']['reference'] == performances[1]['summary']['reference']:
        raise ValueError('Comparisons require distinct references and the same candidate')
    for performance in performances:
        if performance['summary']['manifest_sha256'] != scheduling['manifest_sha256']:
            raise ValueError('Performance/scheduling frozen manifests differ')
        if {row['input']: row['input_sha256'] for row in performance['rows']} != inputs:
            raise ValueError('Performance filename/hash cohorts differ')
        if performance['candidate_native'] != native_signature(scheduling['native_build_and_source']):
            raise ValueError('Performance/scheduling candidate native builds differ')
        if performance['goldens'] != scheduling['golden_sha256']:
            raise ValueError('Performance/scheduling frozen goldens differ')
    files, hashes = len(inputs), len(set(inputs.values()))
    if set(scheduling['golden_sha256']) != set(inputs):
        raise ValueError('Incomplete full-workload golden set')
    if scheduling['production_files'] != files or scheduling['distinct_production_input_hashes'] != hashes:
        raise ValueError('Scheduling full-workload denominator disagrees')
    if len(scheduling['full_planned_queue_order']) != files or set(scheduling['full_planned_queue_order']) != set(inputs):
        raise ValueError('Scheduling full planned queue differs')
    selected = scheduling['selected_latency_inputs']
    if not selected or len(set(selected)) != len(selected) or any(name not in inputs for name in selected):
        raise ValueError('Selected isolated-latency cohort is invalid')
    if scheduling['distinct_latency_input_hashes'] != len({inputs[name] for name in selected}):
        raise ValueError('Selected latency hash denominator disagrees')
    policies, contention = [], [row for p in performances for row in p['contention']]
    full = {row['policy']: row for row in scheduling['full_policies']}
    if set(full) != set(FULL) or len(full) != len(scheduling['full_policies']):
        raise ValueError('Missing/duplicate full scheduling policy')
    for policy in FULL:
        row = dict(full[policy])
        raw_path = resolve(row['summary'], path.parent)
        raw = sources.document(raw_path)
        if sources.hashes[str(raw_path)] != row['summary_sha256']:
            raise ValueError('Selected scheduling policy summary changed')
        quiet = raw.get('contention', {})
        if quiet.get('available') is not True or quiet.get('contention_flag') is not False or quiet.get('external_attribution_valid') is not True:
            raise ValueError('Selected scheduling policy lacks explicit uncontended attribution')
        if row['jobs'] != files or row['distinct_input_hashes'] != hashes or raw['queue_order'] != scheduling['full_planned_queue_order']:
            raise ValueError('Scheduling policy used another workload/order')
        for metric in ('jobs_per_hour', 'cpu_per_job_seconds', 'makespan_seconds'):
            positive(row[metric], metric); same_number(raw[metric], row[metric], metric)
        same_number(row['jobs_per_hour'], files * 3600 / row['makespan_seconds'], 'sustained jobs/hour')
        matches = count(row['matches_A'], 'golden matches')
        if matches > files or matches != raw['matches_A'] or (policy in ('two_jobs', 'refine_one') and matches != files):
            raise ValueError('Scheduling deterministic A/F output mismatch')
        flagged = count(row['serial_jobs_flagged'], 'flagged jobs')
        unknown = count(row['worker_jobs_without_individual_attribution'], 'unattributed worker jobs')
        if flagged + unknown > files:
            raise ValueError('Scheduling contention counts exceed jobs')
        row.update(label=POLICY_LABELS[policy].replace('\n', ' '), policy_contention_flag=False)
        policies.append(row)
        contention.append({'cohort': 'full-scheduling', 'kind': 'policy', 'build_or_policy': policy,
                           'measured_jobs': files, 'flagged_jobs': flagged, 'not_flagged_jobs': files - flagged - unknown,
                           'unknown_job_flags': unknown, 'policy_contention_flag': False,
                           'individual_external_attribution_unavailable': unknown,
                           'max_external_busy_cores_estimate': quiet.get('external_busy_cores_estimate')})
    per_input = scheduling['isolated_per_input']
    expected = {(name, policy) for name in selected for policy in ISOLATED}
    keys = [(row['input'], row['policy']) for row in per_input]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError('Isolated latency cohort is incomplete or duplicated')
    native_counts = {}
    for row in per_input:
        repeats = count(row['repeats'], 'isolated native repeats')
        if row['input_sha256'] != inputs[row['input']] or repeats not in ((2, 40) if mixed_protocol else (2,)) or row['config'] != CONFIGS[row['policy']]:
            raise ValueError('Isolated latency identity/repeats disagree')
        if mixed_protocol and (row.get('paired_rounds') != 2 or row.get('native_repeats_per_input') != repeats):
            raise ValueError('Isolated paired rounds/native repeat counts disagree')
        if row['input'] in native_counts and native_counts[row['input']] != repeats:
            raise ValueError('Isolated native counts differ across configurations')
        native_counts[row['input']] = repeats
        positive(row['mean_service_seconds'], 'isolated latency')
        if count(row['matches_A'], 'isolated golden matches') > repeats or count(row['serial_jobs_flagged'], 'isolated flagged jobs') > repeats:
            raise ValueError('Isolated per-input counts exceed native repeats')
        if row['config'] in ('A', 'F') and row['matches_A'] != repeats:
            raise ValueError('Isolated deterministic A/F output mismatch')
    by_config = {row['policy']: row for row in scheduling['isolated_by_config']}
    if set(by_config) != set(ISOLATED) or len(by_config) != len(scheduling['isolated_by_config']):
        raise ValueError('Missing/duplicate isolated configuration summary')
    isolated = []
    for policy in ISOLATED:
        row = dict(by_config[policy]); records = [r for r in per_input if r['policy'] == policy]
        values = [r['mean_service_seconds'] for r in records]
        if row['input_files'] != len(selected) or row['measured_runs'] != sum(r['repeats'] for r in records) or row['config'] != CONFIGS[policy]:
            raise ValueError('Isolated aggregate denominator/configuration disagrees')
        if row['distinct_input_hashes'] != len({inputs[name] for name in selected}):
            raise ValueError('Isolated aggregate hash denominator disagrees')
        same_number(row['median_file_mean_latency_seconds'], statistics.median(values), 'isolated median')
        same_number(row['p95_file_mean_latency_seconds'], percentile(values, .95), 'isolated p95')
        if row['matches_A'] != sum(r['matches_A'] for r in records) or row['serial_jobs_flagged'] != sum(r['serial_jobs_flagged'] for r in records):
            raise ValueError('Isolated output/contention aggregate disagrees')
        isolated.append(row)
        flagged = count(row['serial_jobs_flagged'], 'isolated flagged jobs')
        contention.append({'cohort': 'isolated-selected', 'kind': 'configuration', 'build_or_policy': row['config'],
                           'measured_jobs': row['measured_runs'], 'flagged_jobs': flagged,
                           'not_flagged_jobs': row['measured_runs'] - flagged, 'unknown_job_flags': 0,
                           'policy_contention_flag': None, 'individual_external_attribution_unavailable': 0,
                           'max_external_busy_cores_estimate': None})
    return {'performances': performances, 'scheduling': scheduling, 'sources': sources.hashes,
            'analysis_summaries': {'dikarya2': str(performances[0]['path']),
                                  'opt8': str(performances[1]['path']), 'scheduling': str(path)},
            'tables': {'per-file-speedups': [row for p in performances for row in p['rows']],
                       'unique-input-speedups': [row for p in performances for row in p['unique']],
                       'scheduling-policies': policies, 'isolated-config-latency': isolated,
                       'isolated-input-latency': per_input, 'contention': contention},
            'counts': {'production_filenames': files, 'distinct_production_input_hashes': hashes,
                       'selected_latency_filenames': len(selected),
                       'distinct_selected_latency_input_hashes': scheduling['distinct_latency_input_hashes'],
                       'native_isolated_runs_per_configuration': sum(native_counts.values())}}


def write_table(path, rows):
    with path.open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter='\t', lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, sort_keys=True) if isinstance(value, (list, dict)) else value
                             for key, value in row.items()})


def contention_note(performance):
    return '; '.join(f"{row['build_or_policy']}: flagged {row['flagged_jobs']}/{row['measured_jobs']}, unknown {row['unknown_job_flags']}"
                     for row in performance['contention'])


def render(plt, result, save):
    """Use the pyplot object supplied by main; importing this module is cheap."""
    counts, tables = result['counts'], result['tables']
    fig, axes = plt.subplots(2, 2, figsize=(11.8, 8.8), squeeze=False)
    for index, performance in enumerate(result['performances']):
        reference, candidate = performance['summary']['reference'], performance['summary']['candidate']
        for column, (key, title) in enumerate([('rows', f"{counts['production_filenames']} production filenames"),
                                               ('unique', f"{counts['distinct_production_input_hashes']} distinct input hashes")]):
            ax = axes[index][column]
            for metric, color in [('wall', '#0072B2'), ('cpu', '#D55E00')]:
                values = sorted(row[metric + '_speedup'] for row in performance[key])
                y = [(i + 1) / len(values) for i in range(len(values))]
                ax.step([values[0], *values], [0, *y], where='post', color=color, linewidth=1.7, label=metric.upper() if metric == 'cpu' else 'Wall')
            ax.axvline(1, color='#555555', linestyle='--', linewidth=.9)
            ax.set(title=f'{reference} → {candidate}\n{title}', xlabel='Speedup (reference / candidate; >1 faster candidate)',
                   ylabel='Cumulative fraction', ylim=(0, 1.02))
            ax.grid(alpha=.18); ax.legend(loc='lower right', frameon=False)
        axes[index][0].text(0, -.30, contention_note(performance), transform=axes[index][0].transAxes,
                             fontsize=8, va='top')
    fig.suptitle('Paired single-thread speedup distributions', fontsize=15)
    fig.subplots_adjust(top=.89, bottom=.20, hspace=.63, wspace=.28)
    fig.text(.06, .045, 'File observations average paired rounds. Hash observations first average filename copies’ times.\n'
             'All completed pairs retained, including flagged/unknown timing; repeated rounds are not timing confidence intervals.', fontsize=9)
    save(fig, 'paired-speedup-distributions')

    rows = tables['scheduling-policies']; positions = list(range(len(rows)))
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 6.3), sharey=True)
    for ax, key, label in [(axes[0], 'jobs_per_hour', 'Completed alignments/hour'),
                           (axes[1], 'cpu_per_job_seconds', 'CPU seconds/completed alignment')]:
        values = [row[key] for row in rows]
        ax.barh(positions, values, color=[COLORS[row['policy']] for row in rows], height=.62)
        ax.set_xlabel(label); ax.set_xlim(0, max(values) * 1.30); ax.grid(axis='x', alpha=.18); ax.set_axisbelow(True)
        for y, value in zip(positions, values):
            ax.text(value + max(values) * .02, y, f'{value:,.1f}', va='center', fontsize=10)
    axes[0].set_yticks(positions, [POLICY_LABELS[row['policy']] for row in rows]); axes[0].invert_yaxis()
    flagged = sum(row['serial_jobs_flagged'] for row in rows)
    unknown = sum(row['worker_jobs_without_individual_attribution'] for row in rows)
    matches = '; '.join(f"{row['policy']} {row['matches_A']}/{row['jobs']}" for row in rows)
    candidate = result['performances'][0]['summary']['candidate']
    fig.suptitle(f"{candidate} sustained scheduling: {counts['production_filenames']} jobs, {counts['distinct_production_input_hashes']} distinct input hashes", fontsize=14)
    fig.subplots_adjust(left=.25, top=.87, bottom=.31, wspace=.16)
    fig.text(.05, .13, f'Policy-level contention flags: false for all five selections. Serial-job flags retained: {flagged}.\n'
             f'Concurrent-worker job flags without individual external attribution: {unknown}; aggregate policy attribution is used.\n'
             f'Exact A-byte matches: {matches}.', fontsize=8.5)
    fig.text(.05, .035, 'All jobs arrive at zero (saturated backlog); this is throughput, not individual isolated latency.\n'
             'B/C and recorded two-thread jobs may change alignments. These measurements do not establish biological accuracy.', fontsize=9)
    save(fig, 'sustained-scheduling')

    rows = tables['isolated-config-latency']; positions = list(range(len(rows)))
    fig, ax = plt.subplots(figsize=(9.4, 6.2))
    for shift, key, label, marker, color in [(-.09, 'median_file_mean_latency_seconds', 'Median of per-file means', 'o', '#0072B2'),
                                           (.09, 'p95_file_mean_latency_seconds', 'p95 of per-file means', 's', '#D55E00')]:
        values = [row[key] for row in rows]
        ax.scatter([x + shift for x in positions], values, marker=marker, color=color, s=52, label=label, zorder=3)
        for x, value in zip(positions, values):
            ax.annotate(f'{value:.2f}', (x + shift, value), xytext=(0, 9), textcoords='offset points', ha='center', fontsize=9)
    labels = ['A: thread 1', 'B: thread 2', 'C: thread 2\nthreadit 0', 'F: thread 2\nthreadit 1']
    ax.set_xticks(positions, labels); ax.set_yscale('log'); ax.set_ylabel('Service time (seconds, log scale)')
    ax.set_xlim(-.5, len(rows) - .5); ax.grid(axis='y', alpha=.2); ax.legend(frameon=False, loc='upper left')
    repeat_labels = '/'.join(str(n) for n in sorted({r['repeats'] for r in tables['isolated-input-latency']}))
    ax.set_title(f"{candidate}: isolated latency across {counts['selected_latency_filenames']} inputs\n"
                 f"Two paired rounds; {repeat_labels} native runs per input/configuration")
    fig.subplots_adjust(top=.88, bottom=.28, left=.12, right=.96)
    flagged = sum(row['serial_jobs_flagged'] for row in rows)
    fig.text(.06, .115, 'Median/p95 use equally weighted two-round input means, not full-corpus percentiles or confidence intervals.\n'
             f"This subset has {counts['distinct_selected_latency_input_hashes']} distinct input hashes; serial-job flags retained: {flagged}.\n"
             'A/F require exact A-byte matches; B/C output hashes and finite repeatability remain in the plotting tables.', fontsize=9)
    save(fig, 'isolated-selected-latency')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dikarya2-analysis', '--full-analysis', dest='dikarya2_analysis', type=Path, required=True)
    parser.add_argument('--opt8-analysis', type=Path, required=True)
    parser.add_argument('--scheduling-analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output directory; existing artifacts are never overwritten')
    result = prepare(args.dikarya2_analysis, args.opt8_analysis, args.scheduling_analysis)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
                         'svg.fonttype': 'none', 'pdf.fonttype': 42})
    args.output.mkdir(parents=True, exist_ok=False)
    outputs = {}
    for name, rows in result['tables'].items():
        target = args.output / (name + '.tsv'); write_table(target, rows)
        outputs[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()

    generator_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    provenance = {key: result['sources'][path] for key, path in result['analysis_summaries'].items()}
    provenance['generator'] = generator_hash

    def save(figure, name):
        footer = '; '.join(key + ' ' + digest[:12] for key, digest in provenance.items())
        figure.text(.01, .004, 'SHA-256: ' + footer + ' (full hashes: manifest.json)', fontsize=6.5, color='#555555')
        for extension in ('svg', 'pdf', 'png'):
            target = args.output / (name + '.' + extension)
            metadata = {'Creator': 'MAFFT round2 performance_figures.py'} if extension in ('svg', 'pdf') else {'Software': 'MAFFT round2 performance_figures.py'}
            if extension == 'svg': metadata.update(Date=None, Description=json.dumps(provenance, sort_keys=True))
            if extension == 'pdf': metadata.update(CreationDate=None, Subject=json.dumps(provenance, sort_keys=True))
            if extension == 'png': metadata['Description'] = json.dumps(provenance, sort_keys=True)
            with target.open('xb') as stream:
                figure.savefig(stream, format=extension, dpi=180, metadata=metadata)
            outputs[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        plt.close(figure)

    render(plt, result, save)
    manifest = {'schema': 'mafft-round2-performance-figures-v2', 'command': sys.argv,
        'generator_sha256': generator_hash, 'analysis_summaries': result['analysis_summaries'],
        'matplotlib_version': matplotlib.__version__, 'counts': result['counts'],
        'input_files_sha256': result['sources'], 'output_files_sha256': outputs,
        'candidate_native_and_frozen_source': result['scheduling']['native_build_and_source'],
        'paired_sources': [{'reference': p['summary']['reference'], 'candidate': p['summary']['candidate'],
                            'summary': str(p['path']), 'paired_experiment': p['experiment'],
                            'recorded_runs': p['summary']['runs'], 'recorded_runs_sha256': p['summary']['runs_sha256'],
                            'completed_exact_jobs': p['summary']['completed_exact_jobs'],
                            'contention': p['summary']['contention']} for p in result['performances']],
        'interpretation': {
            'provenance': 'Consumed summaries, TSVs and paired/full-policy experiments are hashed. Native identity is compared using recorded installation hashes; binaries and full raw run logs are not rehashed by this plotting tool. Retained build audits establish source/binary correspondence.',
            'units': 'Production filenames, distinct original-input hashes and the selected isolated-latency subset are separate panels/tables. No dataset or replicate counts are pooled.',
            'repeat_handling': 'File wall/CPU values average paired rounds; distinct-hash points average filename copies’ times before taking the ratio. Isolated medians/p95 weight input means equally across two paired rounds; actual native repeats (2 or 40 per input/configuration) remain explicit. No round ranges or workload-bootstrap intervals are timing-noise confidence intervals.',
            'contention': 'All completed paired observations are retained. Explicit flagged/unknown counts are exported. Scheduling policy aggregates must be explicitly uncontended; transient serial flags remain counted. Two concurrent workers lack per-job external attribution; their aggregate policy is used. Host interference is not ruled out.',
            'outputs': 'A/F match frozen A bytes in these observations. B/C and recorded two-thread jobs may change outputs. Timing and finite hash repeatability do not establish biological or universal production superiority.'}}
    with (args.output / 'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True, allow_nan=False); stream.write('\n')
    print(json.dumps({'output': str(args.output.resolve()), 'figures': 3, 'formats': ['svg', 'pdf', 'png'],
                      'plotting_tables': len(result['tables']), 'counts': result['counts']}, sort_keys=True))


if __name__ == '__main__':
    main()
