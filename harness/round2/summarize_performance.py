#!/usr/bin/env python3
"""Summarize paired jobs by input, with explicit workload and timing uncertainty.

Bootstrap intervals resample original-input hash clusters. They describe changes
in the sampled workload mix, not repeatability of timing on this shared VM.
"""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import random
import statistics
from analysis import sha256_file, write_json


def quantile(values, p):
    values = sorted(values)
    x = (len(values) - 1) * p
    i = int(x)
    return values[i] + (values[min(i + 1, len(values) - 1)] - values[i]) * (x - i)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--runs', type=Path, required=True)
    ap.add_argument('--manifest', type=Path, required=True)
    ap.add_argument('--reference', default='dikarya2')
    ap.add_argument('--candidate', default='dikarya3')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--bootstrap', type=int, default=5000)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = [json.loads(line) for line in args.runs.read_text().splitlines()]
    records = [r for r in source if r['build'] in [args.reference, args.candidate]]
    if any(r['returncode'] or not r['identical'] or r['cpu'] is None for r in records):
        raise ValueError('Only successful exact-output jobs with CPU records can be summarized')
    manifest = {f['file']: f for f in json.loads(args.manifest.read_text())['files']}
    grouped = defaultdict(dict)
    for record in records:
        key = (record['input'], record['round'])
        if record['build'] in grouped[key]:
            raise ValueError('Duplicate paired record')
        grouped[key][record['build']] = record
        if manifest[record['input']]['sha256'] != record['input_sha256']:
            raise ValueError('Input hash disagrees with frozen manifest')
    if not grouped or any(set(pair) != {args.reference, args.candidate} for pair in grouped.values()):
        raise ValueError('Every selected input/round must have both builds')
    inputs = defaultdict(list)
    for (name, rnd), pair in grouped.items():
        a, b = pair[args.reference], pair[args.candidate]
        if a['golden_sha256'] != b['golden_sha256']:
            raise ValueError('Pair used different goldens')
        inputs[name].append((a, b))
    rows = []
    for name, pairs in sorted(inputs.items()):
        f = manifest[name]
        row = {'input': name, 'input_sha256': f['sha256'], 'partition': f['partition'],
               'sequences': f['sequences'], 'residues': f['residues'], 'paired_rounds': len(pairs),
               'all_exact': True}
        for index, label in [(0, 'reference'), (1, 'candidate')]:
            for metric in ['wall', 'cpu']:
                values = [pair[index][metric] for pair in pairs]
                row[label + '_' + metric] = statistics.mean(values)
                row[label + '_' + metric + '_range'] = max(values) - min(values)
            rss = [pair[index].get('resources', {}).get('rss_kib') for pair in pairs
                   if pair[index].get('resources')]
            row[label + '_max_rss_mib'] = max(rss) / 1024 if rss else None
        for metric in ['wall', 'cpu']:
            row[metric + '_speedup'] = row['reference_' + metric] / row['candidate_' + metric]
            row[metric + '_saved'] = row['reference_' + metric] - row['candidate_' + metric]
        row['cpu_regression_over_5pct_and_50ms'] = row['cpu_speedup'] < 1 / 1.05 and row['cpu_saved'] < -.05
        rows.append(row)
    totals = []
    for partition in ['all', 'development', 'holdout', 'difficult']:
        selected = [r for r in rows if partition == 'all' or r['partition'] == partition]
        if not selected:
            continue
        total = {'partition': partition, 'jobs_per_corpus_pass': len(selected),
                 'distinct_input_hashes': len({r['input_sha256'] for r in selected})}
        for metric in ['wall', 'cpu']:
            a = sum(r['reference_' + metric] for r in selected)
            b = sum(r['candidate_' + metric] for r in selected)
            total.update({metric + '_reference': a, metric + '_candidate': b,
                          metric + '_speedup': a / b, metric + '_reduction_percent': 100 * (1 - b / a)})
        totals.append(total)
    clusters = defaultdict(lambda: [0., 0., 0., 0.])
    for row in rows:
        for i, key in enumerate(['reference_cpu', 'candidate_cpu', 'reference_wall', 'candidate_wall']):
            clusters[row['input_sha256']][i] += row[key]
    samples = list(clusters.values())
    rng = random.Random(20261020)
    cpu_ratios, wall_ratios = [], []
    for _ in range(max(0, args.bootstrap)):
        selected = [rng.choice(samples) for _ in samples]
        sums = [sum(x[i] for x in selected) for i in range(4)]
        cpu_ratios.append(sums[0] / sums[1])
        wall_ratios.append(sums[2] / sums[3])
    interval = {'replicates': args.bootstrap, 'seed': 20261020,
                'unit': 'Original input SHA-256 cluster, keeping repeated production files together',
                'interpretation': 'Workload-mix resampling; not a confidence interval for measurement noise, biological accuracy or independent fungal taxa.'}
    if cpu_ratios:
        interval.update(cpu_speedup_95percentile=[quantile(cpu_ratios, .025), quantile(cpu_ratios, .975)],
                        wall_speedup_95percentile=[quantile(wall_ratios, .025), quantile(wall_ratios, .975)])
    with (args.output / 'per-input.tsv').open('x') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)
    write_json(args.output / 'summary.json', {
        'runs': str(args.runs.resolve()), 'runs_sha256': sha256_file(args.runs),
        'manifest_sha256': sha256_file(args.manifest), 'reference': args.reference, 'candidate': args.candidate,
        'completed_exact_jobs': len(records), 'totals': totals, 'bootstrap_workload_mix': interval,
        'per_input_summary': {'cpu_faster': sum(r['cpu_saved'] > 0 for r in rows),
                              'cpu_slower': sum(r['cpu_saved'] < 0 for r in rows),
                              'cpu_unchanged': sum(r['cpu_saved'] == 0 for r in rows),
                              'regressions_over_5pct_and_50ms': [r['input'] for r in rows if r['cpu_regression_over_5pct_and_50ms']]},
        'regression_policy': 'Individual small regressions are descriptive; repeat controlled pairs before calling them systematic.',
        'mean_policy': 'Per-input values average matching paired rounds; totals represent one pass through the selected production workload.'})
    print(json.dumps(totals, indent=2))


if __name__ == '__main__':
    main()
