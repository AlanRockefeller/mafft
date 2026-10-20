#!/usr/bin/env python3
"""Prespecify follow-up timing for material regressions or uncertain CPU load."""
import argparse
import csv
import json
from pathlib import Path
from analysis import sha256_file, write_json


def select_rows(rows):
    result = []
    for row in rows:
        reasons = []
        if row['cpu_regression_over_5pct_and_50ms'] == 'True':
            reasons.append('CPU regression exceeds both 5% and 50ms')
        if any(int(row[label + '_contention_' + state + '_rounds'])
               for label in ['reference', 'candidate'] for state in ['flagged', 'unknown']):
            reasons.append('At least one original paired timing has flagged or unknown contention')
        if reasons:
            result.append({'input': row['input'], 'input_sha256': row['input_sha256'], 'reasons': reasons})
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--analysis', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--golden', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    table = args.analysis / 'per-input.tsv'
    with table.open() as stream:
        selected = select_rows(list(csv.DictReader(stream, delimiter='\t')))
    source = json.loads((args.analysis / 'summary.json').read_text())
    paired_runs = Path(source['runs'])
    metadata = json.loads((paired_runs.parent / 'experiment.json').read_text())
    builds = {b['name']: b['binary'] for b in metadata['builds']}
    this = Path(__file__).resolve().parent
    steps = []
    if selected:
        steps = [
            {'label': 'repeat-selected-pairs', 'command': [
                'python3', str(this / 'paired.py'), '--manifest', str(args.manifest),
                '--data', str(args.data), '--golden', str(args.golden), '--rounds', '3',
                '--build', source['reference'] + '=' + builds[source['reference']],
                '--build', source['candidate'] + '=' + builds[source['candidate']],
                '--files', *[r['input'] for r in selected], '--output', str(args.output / 'paired')]},
            {'label': 'summarize-confirmations', 'command': [
                'python3', str(this / 'summarize_performance.py'), '--manifest', str(args.manifest),
                '--runs', str(args.output / 'paired/runs.jsonl'), '--reference', source['reference'],
                '--candidate', source['candidate'], '--output', str(args.output / 'analysis')]}]
    selection = {'selected': selected, 'original_per_input_sha256': sha256_file(table),
                 'original_summary_sha256': sha256_file(args.analysis / 'summary.json'),
                 'rule': 'Three new interleaved A/B rounds on every input exceeding both 5% CPU and 50ms regression, or having any flagged/unknown original timing contention. All original results are retained; confirmations are separate. No selective replacement in full-corpus totals.'}
    plan = {'output': str(args.output / 'campaign'), 'cwd': str(this.parents[1]), 'steps': steps}
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in [('selection.json', selection), ('plan.json', plan)]:
        path = args.output / name
        if path.exists():
            if json.loads(path.read_text()) != data:
                raise ValueError('Confirmation plan changed; preserve output and use a new directory')
        else:
            write_json(path, data)
    print(json.dumps({'selected_inputs': len(selected), 'plan': str(args.output / 'plan.json')}))


if __name__ == '__main__':
    main()
