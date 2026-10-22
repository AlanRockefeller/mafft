#!/usr/bin/env python3
"""Check independently executed same-alignment, same-seed tree searches."""
import argparse
import json
from pathlib import Path
from analysis import make_mapping, sha256_file, write_json
from analyze_trees import inference_protocol
from tree import compare_trees, read_tree


def records(directory):
    queue = json.loads((directory / 'queue.json').read_text())
    result = {}
    for line in (directory / 'runs.jsonl').read_text().splitlines():
        row = json.loads(line)
        if not row['completed']:
            raise ValueError('Unsuccessful same-seed control search')
        if sha256_file(row['tree']) != row['tree_sha256']:
            raise ValueError('Recorded tree hash changed')
        protocol, _ = inference_protocol(queue, row)
        key = (row['input_sha256'], row['config'], row['replicate'], row['seed'], protocol)
        if key in result:
            raise ValueError('Duplicate same-seed control identity')
        result[key] = row
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--repeat', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    reference, repeated = records(args.reference), records(args.repeat)
    rows = []
    for key, b in repeated.items():
        a = reference[key]
        if a['named_alignment_sha256'] != b['named_alignment_sha256']:
            raise ValueError('Same-seed controls used different alignments')
        if Path(a['tree']).resolve() == Path(b['tree']).resolve() or b['reused']:
            raise ValueError('Control requires a separate executed inference process')
        mapping = make_mapping(args.data / a['input'])
        if mapping['input_sha256'] != a['input_sha256']:
            raise ValueError('Original input changed')
        comparison = compare_trees(read_tree(a['tree']), read_tree(b['tree']),
                                   mapping=mapping, quartets=0)
        rows.append({'input': a['input'], 'input_sha256': a['input_sha256'], 'seed': a['seed'],
                     'protocol_id': key[-1], 'reference_tree': a['tree'], 'repeated_tree': b['tree'],
                     'reference_tree_sha256': a['tree_sha256'], 'repeated_tree_sha256': b['tree_sha256'],
                     'byte_identical_newick': a['tree_sha256'] == b['tree_sha256'],
                     'comparison': comparison})
    if not rows:
        raise ValueError('No independently executed repeat searches')
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / 'summary.json', {
        'repeated_searches': len(rows), 'byte_identical_newicks': sum(r['byte_identical_newick'] for r in rows),
        'raw_topologies_identical': sum(r['comparison']['views']['raw']['rf'] == 0 for r in rows),
        'results': rows,
        'interpretation': 'Finite reproducibility check of actual separate one-thread IQ-TREE processes on identical alignments and seeds. Distinct-seed search variability is analyzed separately. No guarantee for every input, model or inference version.'})


if __name__ == '__main__':
    main()
