#!/usr/bin/env python3
"""Supplement tree comparisons where exact reverse complements add duplicates.

Primary analyses retain their original-forward-string duplicate definition.
This separate DNA sensitivity counts an exact sequence and its reverse
complement once. Inference trees and original taxon identities stay unchanged.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
from analysis import make_mapping, reverse_complement, sha256_file, write_json
from analyze_trees import inference_protocol
from tree import compare_view, read_tree


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--queues', type=Path, nargs='+', required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    groups = defaultdict(list)
    for directory in args.queues:
        queue = json.loads((directory/'queue.json').read_text())
        for line in (directory/'runs.jsonl').read_text().splitlines():
            row = json.loads(line)
            if row['completed']:
                protocol, _ = inference_protocol(queue, row)
                if sha256_file(row['tree']) != row['tree_sha256']:
                    raise ValueError('Changed inference tree')
                groups[(row['input_sha256'], protocol)].append(row)
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for (digest, protocol), records in groups.items():
        mapping = make_mapping(args.data/records[0]['input'])
        if mapping['input_sha256'] != digest:
            raise ValueError('Original input changed')
        if any(set(r['sequence']) - set('ACGTRYSWKMBDHVNX?') for r in mapping['records']):
            continue  # No T/U equivalence or protein reverse complementation.
        forward, strand = {}, {}
        for r in mapping['records']:
            sequence = r['sequence']
            forward.setdefault(sequence, r['id'])
            strand.setdefault(min(sequence, reverse_complement(sequence)), r['id'])
        if len(strand) == len(forward):
            continue
        selected = sorted(strand.values())
        by = defaultdict(list)
        for r in records:
            by[(r['config'], r['seed'])].append(r)
        for (config, seed), rows in by.items():
            if config == 'A' or not by.get(('A', seed)):
                continue
            a = min(by[('A', seed)], key=lambda r: r['replicate'])
            reference = read_tree(a['tree'])
            for b in rows:
                candidate = read_tree(b['tree'])
                expected = {r['id'] for r in mapping['records']}
                if set(reference.taxa) != expected or set(candidate.taxa) != expected:
                    raise ValueError('Inference did not retain full taxon set')
                results.append({'input': a['input'], 'input_sha256': digest, 'protocol_id': protocol,
                                'config': config, 'replicate': b['replicate'], 'seed': seed,
                                'original_taxa': len(expected), 'forward_duplicate_representatives': len(forward),
                                'strand_duplicate_representatives': len(strand), 'selected_ids': selected,
                                'reference_tree': a['tree'], 'candidate_tree': b['tree'],
                                'comparison': compare_view(reference, candidate, selected, zero=True,
                                                           quartets=10000, details=True)})
    write_json(args.output/'summary.json', {'comparisons': results,
               'distinct_input_hashes': len({r['input_sha256'] for r in results}),
               'interpretation': 'Supplementary exact reverse-complement duplicate sensitivity; primary forward-string results are preserved. Original edges <=1e-6 collapse before pruning. Full inference retains all original taxa. Non-ITS biological truth and near-identical sequences are not inferred from this grouping.'})
    print(json.dumps({'comparisons': len(results), 'distinct_inputs': len({r['input_sha256'] for r in results})}))


if __name__ == '__main__':
    main()
