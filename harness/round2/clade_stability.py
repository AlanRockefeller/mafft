#!/usr/bin/env python3
"""Track supported unrooted splits across alignment replicates and search seeds.

Support remains conditional on its source alignment. Presence in another ML
tree is not a bootstrap estimate or a biological-accuracy score.
"""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
from analysis import make_mapping, sha256_file, write_json
from tree import read_tree, SHALRT_THRESHOLD, UFBOOT_THRESHOLD, ZERO_THRESHOLD


def load(queues, support):
    records, methods = [], set()
    for directory in queues:
        queue = json.loads((directory / 'queue.json').read_text())
        meta = queue['metadata']
        if meta['fast'] or bool(meta['support']) != support:
            raise ValueError('Expected separate full-search normal/support queues')
        methods.add(tuple(meta[k] for k in ['binary_sha256', 'model', 'threads', 'nstop']))
        for line in (directory / 'runs.jsonl').read_text().splitlines():
            row = json.loads(line)
            if row['completed']:
                if sha256_file(row['tree']) != row['tree_sha256']:
                    raise ValueError('Tree changed since inference')
                records.append(row)
    return records, methods


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--support-queues', nargs='+', type=Path, required=True)
    p.add_argument('--normal-queues', nargs='+', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    supported, smethods = load(args.support_queues, True)
    normal, nmethods = load(args.normal_queues, False)
    if len(smethods | nmethods) != 1:
        raise ValueError('Executables, models or full-search settings differ')
    args.output.mkdir(parents=True, exist_ok=False)
    trees, mappings, rows = {}, {}, []
    for record in supported + normal:
        if record['input_sha256'] not in mappings:
            mapping = make_mapping(args.data / record['input'])
            if mapping['input_sha256'] != record['input_sha256']:
                raise ValueError('Original input changed')
            mappings[record['input_sha256']] = mapping
        if record['tree'] not in trees:
            tree = trees[record['tree']] = read_tree(record['tree'])
            if set(tree.taxa) != {r['id'] for r in mappings[record['input_sha256']]['records']}:
                raise ValueError('Incomplete taxon set')
    seen = set()
    for source in supported:
        key = (source['input_sha256'], source['config'], source['replicate'], source['seed'])
        if key in seen:
            raise ValueError('Duplicate supported logical replicate')
        seen.add(key)
        tree = trees[source['tree']]
        mapping = mappings[source['input_sha256']]
        names = {r['id']: r['original_header'] for r in mapping['records']}
        edges = tree.splits(edge_filter=lambda e: e.length is not None and e.length > ZERO_THRESHOLD
                            and e.support.get('shalrt', -1) >= SHALRT_THRESHOLD
                            and e.support.get('ufboot', -1) >= UFBOOT_THRESHOLD)
        matches = [r for r in normal if r['input_sha256'] == source['input_sha256']]
        groups = defaultdict(list)
        for r in matches:
            groups[(r['config'], 'paired-seed' if r['seed'] == source['seed'] else 'other-search-seeds')].append(r)
        for mask, edge in edges.items():
            side = tree.side_taxa(mask)
            for (config, role), records in groups.items():
                found = [r for r in records if mask in trees[r['tree']].splits(
                    edge_filter=lambda e: e.length is None or e.length > ZERO_THRESHOLD)]
                rows.append({'input': source['input'], 'input_sha256': source['input_sha256'],
                             'support_source_config': source['config'], 'support_source_replicate': source['replicate'],
                             'support_source_seed': source['seed'], 'support_source_tree': source['tree'],
                             'side_ids': json.dumps(side), 'side_original_labels': json.dumps([names[t] for t in side]),
                             'smaller_side_taxa': min(len(side), len(tree.taxa) - len(side)),
                             'shalrt': edge.support['shalrt'], 'ufboot': edge.support['ufboot'],
                             'tested_config': config, 'search_role': role,
                             'logical_requests': len(records), 'present_requests': len(found),
                             'unique_named_alignments': len({r['named_alignment_sha256'] for r in records}),
                             'unique_executed_jobs': len({r['job_key'] for r in records}),
                             'present_unique_jobs': len({r['job_key'] for r in found})})
    with (args.output / 'supported-split-stability.tsv').open('x') as out:
        if rows:
            writer = csv.DictWriter(out, fieldnames=list(rows[0]), delimiter='\t')
            writer.writeheader()
            writer.writerows(rows)
    write_json(args.output / 'summary.json', {
        'supported_logical_requests': len(supported), 'normal_logical_requests': len(normal),
        'original_input_hashes': len({r['input_sha256'] for r in supported}), 'rows': len(rows),
        'thresholds': {'shalrt': SHALRT_THRESHOLD, 'ufboot': UFBOOT_THRESHOLD, 'zero_length': ZERO_THRESHOLD},
        'interpretation': 'Unrooted split presence across nested alignment/search replicates. Source support is conditional; repeated identical alignment/seed requests are not independent searches. No accuracy ranking.',
        'view': 'All original taxa; original short edges collapse before split comparison.'})


if __name__ == '__main__':
    main()
