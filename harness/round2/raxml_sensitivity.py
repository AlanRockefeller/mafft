#!/usr/bin/env python3
"""Small, separately recorded RAxML-NG sensitivity study on prepared IQ alignments.

Use --pilot first to estimate the cost, then resume without it. Every search has
one thread, four starting trees and a recorded seed. This is scientific inference,
not a performance benchmark. IQ-TREE results are never pooled into this cohort.
"""
import argparse
import datetime
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time

from alignments import FORMAT, read_resources, run_process_group
from analysis import make_mapping, read_fasta, reverse_complement, sha256_file, ungap, write_json
from export_trees import readable_newick
from quiet import busy_delta, cpu_snapshot
from tree import compare_trees, read_tree


def prepare(args):
    mapping = make_mapping(args.data / args.input)
    requests = [json.loads(line) for line in (args.queue / 'runs.jsonl').read_text().splitlines()]
    sources = {}
    for config, replicate in [('A', 0), ('B', args.b_replicate), ('C', 0)]:
        rows = [r for r in requests if r['input'] == args.input and r['config'] == config
                and r['replicate'] == replicate and r['seed'] == 73111]
        if len(rows) != 1 or not rows[0]['completed']:
            raise ValueError('Need one completed, unambiguous A0/B-selected/C0 source request')
        row = rows[0]
        if row['input_sha256'] != mapping['input_sha256']:
            raise ValueError('Original input hash changed')
        source = Path(row['directory']) / 'named.fasta'
        if sha256_file(source) != row['named_alignment_sha256']:
            raise ValueError('Prepared stable-ID alignment changed')
        aligned = read_fasta(source)
        expected = {r['id']: r['sequence'] for r in mapping['records']}
        if len(aligned) != len(expected) or {name for name, _ in aligned} != set(expected):
            raise ValueError('Prepared alignment changed taxon identities')
        for name, sequence in aligned:
            if ungap(sequence) not in {expected[name], reverse_complement(expected[name])}:
                raise ValueError('Prepared alignment changed original residues')
        sources[config] = {'request': row, 'alignment': str(source.resolve()),
                           'sha256': sha256_file(source)}
    binary = str(args.binary.resolve())
    version = subprocess.run([binary, '--version'], capture_output=True, text=True, check=True, timeout=20)
    plan = {'input': args.input, 'input_sha256': mapping['input_sha256'], 'taxa': len(mapping['records']),
            'binary': binary, 'binary_sha256': sha256_file(binary),
            'version': version.stdout + version.stderr, 'sources': sources,
            'model': 'GTR+F+G4', 'threads': 1, 'workers': 1,
            'starting_trees': 'pars{2},rand{2}', 'lh_epsilon': '0.1',
            'seeds': [88311, 88329, 88341], 'timeout_seconds': args.timeout,
            'purpose': 'Independent inference-method sensitivity, no bootstrap or accuracy ranking; all timing descriptive.'}
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / 'experiment.json'
    if path.exists():
        if json.loads(path.read_text()) != plan:
            raise ValueError('Resume plan changed; choose a new output directory')
    else:
        write_json(path, plan)
        write_json(args.output / 'taxa.json', mapping)
        (args.output / 'alignments').mkdir()
        for config, source in sources.items():
            shutil.copyfile(source['alignment'], args.output / 'alignments' / f'{config}.fasta')
    for config, source in sources.items():
        if sha256_file(args.output / 'alignments' / f'{config}.fasta') != source['sha256']:
            raise ValueError('Private named alignment changed')
    return mapping, plan


def run_search(args, mapping, plan, config, seed):
    directory = args.output / f'{config}-seed{seed}'
    alignment = args.output / 'alignments' / f'{config}.fasta'
    command = [plan['binary'], '--search', '--msa', str(alignment), '--data-type', 'DNA',
               '--model', plan['model'], '--threads', '1', '--workers', '1',
               '--tree', plan['starting_trees'], '--lh-epsilon', plan['lh_epsilon'],
               '--seed', str(seed), '--prefix', str(directory / 'inference')]
    run_path = directory / 'run.json'
    if run_path.exists():
        result = json.loads(run_path.read_text())
        if (result['command'] != command or not result['completed'] or
                sha256_file(result['tree']) != result['tree_sha256']):
            raise ValueError('Failed or changed prior search; preserve it and choose a new directory')
        return result
    directory.mkdir(exist_ok=False)
    write_json(directory / 'command.json', {'command': command, 'config': config, 'seed': seed,
               'alignment_sha256': sha256_file(alignment), 'binary_sha256': plan['binary_sha256']})
    if sha256_file(plan['binary']) != plan['binary_sha256']:
        raise ValueError('RAxML-NG binary changed')
    resources = directory / 'resources.json'
    measured = ['/usr/bin/time', '-f', FORMAT, '-o', str(resources), 'nice', '-n', '10',
                'taskset', '-c', '0,1', *command]
    before = cpu_snapshot()
    started = time.monotonic()
    with (directory / 'stdout.txt').open('xb') as out, (directory / 'stderr.txt').open('xb') as err:
        execution = run_process_group(measured, stdout=out, stderr=err,
                      env=dict(os.environ, LC_ALL='C', LANG='C'), timeout=args.timeout)
    after = cpu_snapshot()
    usage, resource_error = read_resources(resources)
    tree_path = directory / 'inference.raxml.bestTree'
    error = resource_error
    if execution['returncode'] == 0 and tree_path.exists():
        try:
            tree = read_tree(tree_path, support_order=[])
            if set(tree.taxa) != {r['id'] for r in mapping['records']}:
                raise ValueError('RAxML-NG did not retain every original taxon')
        except ValueError as exc:
            error = str(exc)
    cpu = usage['user'] + usage['system'] if usage else None
    result = {'config': config, 'seed': seed, 'command': command, 'execution_argv': measured,
              'execution': execution, 'resources': usage, 'cpu': cpu,
              'wall': time.monotonic() - started, 'contention': busy_delta(before, after, cpu),
              'tree': str(tree_path), 'tree_sha256': sha256_file(tree_path) if tree_path.exists() else None,
              'validation_error': error, 'completed': execution['returncode'] == 0 and tree_path.exists() and error is None,
              'finished_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    write_json(run_path, result)
    print(json.dumps({k: result[k] for k in ['config', 'seed', 'completed', 'cpu', 'wall', 'validation_error']}), flush=True)
    if not result['completed']:
        raise RuntimeError('Preserved failed RAxML-NG job; no taxa were silently removed')
    (directory / 'original-labels.nwk').write_text(readable_newick(tree_path.read_text(), mapping))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binary', type=Path, default=Path('/usr/local/bin/raxml-ng'))
    parser.add_argument('--b-replicate', type=int, required=True)
    parser.add_argument('--timeout', type=float, default=900)
    parser.add_argument('--pilot', action='store_true')
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.b_replicate < 0:
        parser.error('Timeout must be positive and finite; B replicate must be nonnegative')
    args.output = args.output.resolve()
    mapping, plan = prepare(args)
    targets = [('A', 88311)] if args.pilot else [('A', 88311), ('B', 88311), ('C', 88311),
                                               ('A', 88329), ('B', 88329), ('C', 88329), ('A', 88341)]
    results = [run_search(args, mapping, plan, config, seed) for config, seed in targets]
    if args.pilot:
        return
    summary_path = args.output / 'comparisons.json'
    if summary_path.exists():
        return
    comparisons = []
    for a, b in itertools.combinations(results, 2):
        if a['config'] != b['config'] and a['seed'] != b['seed']:
            continue
        comparison = compare_trees(read_tree(a['tree'], support_order=[]), read_tree(b['tree'], support_order=[]),
                                   mapping=mapping, quartets=10000, details=True)
        comparisons.append({'reference': (a['config'], a['seed']), 'candidate': (b['config'], b['seed']),
                            'comparison': comparison})
    write_json(summary_path, {'experiment_sha256': sha256_file(args.output / 'experiment.json'),
                              'separately_executed_searches': len(results), 'comparisons': comparisons,
                              'interpretation': 'One selected input. Different starts/seeds control some search variability; this limited search is not exhaustive. All original taxa retained. No cross-alignment likelihood ranking or claim of biological truth.'})


if __name__ == '__main__':
    main()
