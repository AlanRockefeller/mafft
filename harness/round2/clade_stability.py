#!/usr/bin/env python3
"""Track supported unrooted splits across alignment replicates and search seeds.

Support remains conditional on its source alignment. Presence in another ML
tree is not a bootstrap estimate or a biological-accuracy score.
"""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
from analysis import make_mapping, sha256_file, write_json
from analyze_trees import inference_protocol
from tree import read_tree, SHALRT_THRESHOLD, UFBOOT_THRESHOLD, ZERO_THRESHOLD


def non_support_search_signature(protocol):
    """Remove only validated, recorded support settings from a full method.

    inference_protocol has already normalized alignment/prefix paths and seeds.
    Every other command argument remains significant, including flags unknown
    to this collector. Support flags on a purported normal search are retained
    or rejected rather than silently stripped.
    """
    signature = {key: value for key, value in protocol.items()
                 if key not in ['support', 'support_replicates', 'command_template']}
    command = protocol['command_template']
    stripped = []
    index = 0
    removed = defaultdict(int)
    recorded = protocol['support_replicates'] if protocol['support'] else None
    while index < len(command):
        option = command[index]
        if recorded and option in ['-B', '--ufboot', '--alrt', '-alrt']:
            setting = 'ufboot' if option in ['-B', '--ufboot'] else 'shalrt'
            if index + 1 >= len(command) or command[index + 1] != str(recorded[setting]):
                raise ValueError('Support command does not match its recorded settings')
            removed[setting] += 1
            index += 2
        elif recorded and recorded['bootstrap_nni'] and option in ['--bnni', '-bnni']:
            removed['bootstrap_nni'] += 1
            index += 1
        else:
            stripped.append(option)
            index += 1
    if recorded and (removed['ufboot'] != 1 or removed['shalrt'] != 1 or
                     removed['bootstrap_nni'] != int(recorded['bootstrap_nni'])):
        raise ValueError('Missing or duplicate recorded support command options')
    signature['command_template'] = stripped
    identifier = hashlib.sha256(json.dumps(signature, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return identifier, signature


def load(queues, support):
    records, methods, protocols, sources, executed = [], {}, {}, [], {}
    for directory in queues:
        queue_path = directory / 'queue.json'
        log_path = directory / 'runs.jsonl'
        queue = json.loads(queue_path.read_text())
        meta = queue['metadata']
        if meta['fast'] or bool(meta['support']) != support:
            raise ValueError('Expected separate full-search normal/support queues')
        queue_protocols, queue_signatures = set(), set()
        for line in log_path.read_text().splitlines():
            row = json.loads(line)
            if row['completed']:
                protocol_id, protocol = inference_protocol(queue, row)
                signature_id, signature = non_support_search_signature(protocol)
                protocols[protocol_id] = protocol
                methods[signature_id] = signature
                queue_protocols.add(protocol_id)
                queue_signatures.add(signature_id)
                run_path = Path(row['run_json'] if row.get('run_json') else
                                Path(row['directory']) / 'run.json').resolve()
                if run_path not in executed:
                    executed[run_path] = json.loads(run_path.read_text())
                run = executed[run_path]
                if not run.get('completed') or run['plan'] != queue['jobs'][row['job_key']]:
                    raise ValueError('Executed inference plan differs from its completed queue request')
                if run['tree_sha256'] != row['tree_sha256']:
                    raise ValueError('Executed inference tree digest differs from its queue request')
                if sha256_file(row['tree']) != row['tree_sha256']:
                    raise ValueError('Tree changed since inference')
                records.append(row)
        sources.append({'queue': str(directory.resolve()), 'support': support,
                        'queue_sha256': sha256_file(queue_path), 'runs_sha256': sha256_file(log_path),
                        'inference_protocol_ids': sorted(queue_protocols),
                        'non_support_search_signature_ids': sorted(queue_signatures)})
    return records, methods, {'inference_protocols': protocols, 'source_queues': sources}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--support-queues', nargs='+', type=Path, required=True)
    p.add_argument('--normal-queues', nargs='+', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    supported, smethods, sprovenance = load(args.support_queues, True)
    normal, nmethods, nprovenance = load(args.normal_queues, False)
    methods = smethods | nmethods
    if not supported or not normal or len(methods) != 1:
        raise ValueError('Completed normal/support searches must share the full non-support inference protocol')
    signature_id, signature = next(iter(methods.items()))
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
        'protocol_provenance': {
            'non_support_search_signature_id': signature_id,
            'non_support_search_signature': signature,
            'inference_protocols': sprovenance['inference_protocols'] | nprovenance['inference_protocols'],
            'source_queues': sprovenance['source_queues'] + nprovenance['source_queues'],
            'compatibility_rule': 'Validate each completed request against its full prepared and executed IQ-TREE method; normalize only alignment/prefix paths and random seeds, then remove only recorded support metadata and -B/--ufboot, --alrt/-alrt, --bnni/-bnni settings. All other command options and executable/model/thread/memory/search settings must agree.'},
        'interpretation': 'Unrooted split presence across nested alignment/search replicates. Source support is conditional; repeated identical alignment/seed requests are not independent searches. No accuracy ranking.',
        'view': 'All original taxa; original short edges collapse before split comparison.'})


if __name__ == '__main__':
    main()
