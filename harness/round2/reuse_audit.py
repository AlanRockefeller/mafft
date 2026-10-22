#!/usr/bin/env python3
"""Measure exact prefix and containment structure, without claiming DP reuse.

Goldens supply verified strand orientation. Unique sequence strings are uppercase
and ungapped. Trie counts are a data property, not an instruction/runtime bound:
DP reuse still requires identical scoring, layout, incoming gap/score state,
global maxima and traceback semantics.
"""
import argparse
import csv
import json
from pathlib import Path
from analysis import make_mapping, read_alignment, sha256_file, ungap, write_json


def prefix_saved(strings):
    strings = sorted(strings)
    saved = 0
    for a, b in zip(strings, strings[1:]):
        k = 0
        for x, y in zip(a, b):
            if x != y:
                break
            k += 1
        saved += k
    return saved


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--golden', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    rows, seen = [], set()
    for f in json.loads(args.manifest.read_text())['files']:
        if f['sha256'] in seen:
            continue
        seen.add(f['sha256'])
        mapping = make_mapping(args.data / f['file'])
        if mapping['input_sha256'] != f['sha256']:
            raise ValueError('Original input changed')
        aligned = read_alignment(args.golden / f['file'], mapping)
        sequences = sorted(set(ungap(s) for s in aligned.sequences), key=lambda s: (len(s), s))
        total_length = sum(map(len, sequences))
        prefix = prefix_saved(sequences)
        suffix = prefix_saved([s[::-1] for s in sequences])
        pairs = cells = hits = hitcells = purehits = purecells = multiple = 0
        for i, short in enumerate(sequences):
            for long in sequences[i+1:]:
                pairs += 1; cell = len(short) * len(long); cells += cell
                if len(short) == len(long):
                    continue
                start = long.find(short)
                if start >= 0:
                    hits += 1; hitcells += cell
                    if set(short + long) <= set('ACGT'):
                        purehits += 1; purecells += cell
                    multiple += long.find(short, start + 1) >= 0
        rows.append({'input': f['file'], 'input_sha256': f['sha256'],
                     'golden_sha256': sha256_file(args.golden/f['file']),
                     'unique_oriented_casefolded_sequences': len(sequences),
                     'sequence_characters': total_length, 'prefix_trie_shared_characters': prefix,
                     'suffix_trie_shared_characters': suffix,
                     'prefix_fraction': prefix/total_length, 'suffix_fraction': suffix/total_length,
                     'unordered_distinct_pairs': pairs, 'nominal_pair_cells': cells,
                     'proper_containment_pairs': hits, 'proper_containment_nominal_cells': hitcells,
                     'pure_ACGT_containment_pairs': purehits, 'pure_ACGT_containment_nominal_cells': purecells,
                     'containments_with_multiple_occurrences': multiple})
    args.output.mkdir(parents=True, exist_ok=False)
    with (args.output/'per-input.tsv').open('x') as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]), delimiter='\t')
        writer.writeheader(); writer.writerows(rows)
    fields = ['sequence_characters', 'prefix_trie_shared_characters', 'suffix_trie_shared_characters',
              'unordered_distinct_pairs', 'nominal_pair_cells', 'proper_containment_pairs',
              'proper_containment_nominal_cells', 'pure_ACGT_containment_pairs',
              'pure_ACGT_containment_nominal_cells', 'containments_with_multiple_occurrences']
    totals = {key: sum(r[key] for r in rows) for key in fields}
    write_json(args.output/'summary.json', {'distinct_input_hashes': len(rows), 'totals': totals,
               'manifest_sha256': sha256_file(args.manifest), 'runner_sha256': sha256_file(__file__),
               'interpretation': 'Descriptive input structure after verified golden strand orientation and uppercase normalization. Distinct raw FASTA hashes are counted once. Nominal pair cells are not measured executed DP cells. Matching prefixes/substrings alone do not prove reusable DP states or traceback equivalence. No new shortcut is enabled by this audit.'})
    print(json.dumps(totals))


if __name__ == '__main__':
    main()
