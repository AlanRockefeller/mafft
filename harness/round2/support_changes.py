#!/usr/bin/env python3
"""Quantify conditional branch-support changes on shared unrooted splits."""
import argparse
import json
from pathlib import Path
from analysis import make_mapping, sha256_file, write_json
from throughput import summary
from tree import SHALRT_THRESHOLD, UFBOOT_THRESHOLD, write_tsv


def shared_changes(comparison):
    rows = []
    for item in comparison['views']['zero_collapsed']['split_details']:
        if item['status'] != 'shared':
            continue
        a, b = item['reference'], item['candidate']
        if a['side'] != b['side']:
            raise ValueError('Shared split labels disagree')
        if any(set(x['support']) != {'shalrt', 'ufboot'} for x in (a, b)):
            raise ValueError('Missing or ambiguous support-label order')
        strong = lambda x: (x['support']['shalrt'] >= SHALRT_THRESHOLD and
                            x['support']['ufboot'] >= UFBOOT_THRESHOLD)
        row = {'split_id': a['id'], 'side_ids': a['side'], 'side_size': a['size'],
               'reference_strong': strong(a), 'candidate_strong': strong(b)}
        for measure in ['shalrt', 'ufboot']:
            row['reference_' + measure] = a['support'][measure]
            row['candidate_' + measure] = b['support'][measure]
            row[measure + '_delta'] = b['support'][measure] - a['support'][measure]
        rows.append(row)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--analysis', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    rows, datasets, mappings = [], [], {}
    comparisons = args.analysis / 'comparisons.jsonl'
    for line in comparisons.read_text().splitlines():
        record = json.loads(line)
        if not record['support'] or record['kind'] not in ['A_vs_B', 'A_vs_C', 'A_vs_F']:
            continue
        if record['input_sha256'] not in mappings:
            mapping = make_mapping(args.data / record['input'])
            if mapping['input_sha256'] != record['input_sha256']:
                raise ValueError('Original input changed')
            mappings[record['input_sha256']] = mapping
        names = {r['id']: r['original_header'] for r in mappings[record['input_sha256']]['records']}
        identity = {k: record[k] for k in ['input', 'input_sha256', 'kind', 'protocol_id',
                    'reference_replicate', 'candidate_replicate', 'reference_seed', 'candidate_seed']}
        changes = shared_changes(record['comparison'])
        for item in changes:
            rows.append({**identity, **item, 'side_ids': json.dumps(item['side_ids']),
                         'side_original_labels': json.dumps([names[t] for t in item['side_ids']])})
        strong = record['comparison']['views']['strong_support']
        datasets.append({**identity, 'shared_nonzero_splits': len(changes),
                         'shalrt_delta': summary(x['shalrt_delta'] for x in changes),
                         'ufboot_delta': summary(x['ufboot_delta'] for x in changes),
                         'shared_splits_crossing_up_to_joint_threshold': sum(not x['reference_strong'] and x['candidate_strong'] for x in changes),
                         'shared_splits_crossing_down_from_joint_threshold': sum(x['reference_strong'] and not x['candidate_strong'] for x in changes),
                         'strong_reference_splits': strong['reference_splits'],
                         'strong_candidate_splits': strong['candidate_splits'],
                         'shared_strong_splits': strong['shared_splits'],
                         'strong_incompatible_split_pairs': strong['incompatible_split_pairs']})
    if not datasets:
        raise ValueError('No compatible supported A comparisons')
    args.output.mkdir(parents=True, exist_ok=False)
    if rows:
        write_tsv(args.output / 'shared-split-support.tsv', rows)
    write_json(args.output / 'summary.json', {
        'distinct_input_hashes': len({r['input_sha256'] for r in datasets}),
        'datasets': datasets, 'comparisons_sha256': sha256_file(comparisons),
        'view': 'All original taxa; original edges of length <=1e-6 collapse before split comparison.',
        'thresholds': {'shalrt': SHALRT_THRESHOLD, 'ufboot': UFBOOT_THRESHOLD},
        'interpretation': 'Signed support differences apply only to shared splits. Missing splits and incompatible strong splits are reported separately. Support is conditional on each alignment and model, not a measure of biological accuracy. Splits and repeated comparisons are nested within each input, not independent biological replicates.'})


if __name__ == '__main__':
    main()
