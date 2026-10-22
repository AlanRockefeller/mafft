#!/usr/bin/env python3
"""Export selected inferred trees, named alignments and reversible taxon maps.

Original Newicks are copied byte-for-byte. A separate readable-label Newick keeps
the stable ID plus the complete original header, with Newick quoting. Relabeling
is checked by reversing it and comparing every split and branch length.
"""
import argparse
import csv
import json
from pathlib import Path
import re
import shutil
from analysis import make_mapping, sha256_file, write_json
from tree import IndexedTree, parse_newick


def readable_newick(text, mapping):
    labels = {r['id']: r['id'] + ' | ' + r['original_header'] for r in mapping['records']}
    before = IndexedTree(parse_newick(text))
    if set(before.taxa) != set(labels):
        raise ValueError('Tree does not retain the complete original taxon set')
    seen = []
    def replace(match):
        identifier = match.group(1)
        if identifier not in labels:
            raise ValueError('Unknown stable tip ID')
        seen.append(identifier)
        return "'" + labels[identifier].replace("'", "''") + "'"
    result = re.sub(r'(?<=[(,])\s*(t[0-9]{6,})(?=\s*[:,);])', replace, text)
    if sorted(seen) != sorted(labels):
        raise ValueError('Could not identify every stable tip token exactly once')
    reverse = {label: identifier for identifier, label in labels.items()}
    restored = IndexedTree(parse_newick(result))
    for node in restored.nodes:
        if not node.children:
            node.label = reverse[node.label]
    restored = IndexedTree(restored.root)
    if before.taxa != restored.taxa or before.splits() != restored.splits():
        raise ValueError('Readable-label export changed the inferred tree')
    if [(n.label, n.length) for n in before.nodes] != [(n.label, n.length) for n in restored.nodes]:
        raise ValueError('Readable-label export changed branch labels or lengths')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--queues', type=Path, nargs='+', required=True)
    p.add_argument('--files', nargs='+')
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    mappings, directories, exported, sources = {}, {}, [], []
    for queue in args.queues:
        metadata = json.loads((queue / 'queue.json').read_text())['metadata']
        sources.append({'queue': str(queue.resolve()), 'queue_sha256': sha256_file(queue / 'queue.json'),
                        'runs_sha256': sha256_file(queue / 'runs.jsonl'), 'method': metadata})
        for line in (queue / 'runs.jsonl').read_text().splitlines():
            row = json.loads(line)
            if not row['completed'] or args.files and row['input'] not in args.files:
                continue
            if row['input_sha256'] not in mappings:
                mapping = make_mapping(args.data / row['input'])
                if mapping['input_sha256'] != row['input_sha256']:
                    raise ValueError('Original input changed')
                mappings[row['input_sha256']] = mapping
                directory = args.output / Path(row['input']).stem
                directory.mkdir()
                directories[row['input_sha256']] = directory
                write_json(directory / 'taxa.json', mapping)
                shutil.copyfile(args.data / row['input'], directory / 'original.fasta')
                with (directory / 'taxa.tsv').open('x') as out:
                    writer = csv.DictWriter(out, fieldnames=['id', 'ordinal', 'original_header', 'length', 'sequence_sha256'], delimiter='\t')
                    writer.writeheader()
                    writer.writerows({k: r[k] for k in writer.fieldnames} for r in mapping['records'])
            mapping = mappings[row['input_sha256']]
            source = Path(row['tree'])
            run = json.loads(Path(row['run_json']).read_text())
            plan = run['plan']
            named = Path(plan['directory']) / 'named.fasta'
            if (sha256_file(source) != row['tree_sha256'] or
                sha256_file(named) != row['named_alignment_sha256'] or
                plan['input_sha256'] != row['input_sha256'] or
                plan['seed'] != row['seed']):
                raise ValueError('Changed or mislabeled inference artifact')
            directory = directories[row['input_sha256']]
            label = f"{queue.name}-{Path(row['input']).stem}-{row['config']}-r{row['replicate']}-seed{row['seed']}"
            target = directory / label
            target.mkdir()
            shutil.copyfile(source, target / 'stable-ids.nwk')
            (target / 'original-labels.nwk').write_text(readable_newick(source.read_text(), mapping))
            shutil.copyfile(named, target / 'alignment.fasta')
            report = source.with_suffix('.iqtree')
            if report.exists():
                shutil.copyfile(report, target / 'inference.iqtree')
            write_json(target / 'provenance.json', {'logical_request': row, 'executed_plan': plan})
            exported.append({'input': row['input'], 'input_sha256': row['input_sha256'],
                             'stage': queue.name, 'config': row['config'], 'replicate': row['replicate'], 'seed': row['seed'],
                             'directory': str(target.resolve()), 'source_tree': str(source),
                             'tree_sha256': sha256_file(target / 'stable-ids.nwk'),
                             'named_alignment_sha256': row['named_alignment_sha256'],
                             'executed_run_json': row['run_json'], 'reused_logical_request': row['reused']})
    write_json(args.output / 'manifest.json', {'sources': sources, 'exports': exported,
              'exporter_sha256': sha256_file(__file__),
              'distinct_input_hashes': len(mappings),
              'distinct_executed_job_paths': len({r['executed_run_json'] for r in exported}),
              'interpretation': 'Logical requests can reuse inference jobs. Original labels are for display; stable-ID Newicks are the analysis originals. No pruning, rooting, collapse, branch rescaling or cross-alignment likelihood ranking is performed.'})
    print(json.dumps({'logical_exports': len(exported), 'distinct_inputs': len(mappings)}))


if __name__ == '__main__':
    main()
