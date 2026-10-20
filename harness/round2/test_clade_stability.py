#!/usr/bin/env python3
"""Tiny recorded-command fixtures; never execute IQ-TREE or other native jobs."""
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analysis import sha256_file
from analyze_trees import inference_protocol
import clade_stability


class CladeCompatibilityTests(unittest.TestCase):
    def fixture(self, root, name, support, *, extra=(), mem='6G'):
        directory = root / name
        directory.mkdir()
        job = directory / 'job'
        job.mkdir()
        original = root / 'fixture.fasta'
        if not original.exists():
            original.write_text('>first label\nAC\n>second label\nAG\n>third label\nAT\n>fourth label\nAA\n')
        tree = job / 'inference.treefile'
        annotation = '90/99' if support else ''
        tree.write_text(f'((t000001:1,t000002:1){annotation}:1,t000003:1,t000004:1);')
        if support:
            tree.with_suffix('.iqtree').write_text('SH-aLRT support (%) / ultrafast bootstrap support (%)\n')
        meta = {'iqtree': '/fixture/iqtree2', 'binary_sha256': 'fixture-executable-digest',
                'iqtree_version': 'fixture2', 'model': 'GTR+F+G4', 'threads': 1,
                'nstop': None, 'fast': False, 'support': support, 'mem': mem}
        command = [meta['iqtree'], '-s', str(job / 'named.fasta'), '--seqtype', 'DNA',
                   '-m', meta['model'], '-T', '1', '--seed', '73111', '--keep-ident',
                   '--mem', mem, '--prefix', str(job / 'inference'), *extra]
        replicates = {'ufboot': 1000, 'shalrt': 1000, 'bootstrap_nni': True} if support else None
        if support:
            command.extend(['-B', '1000', '--alrt', '1000', '--bnni'])
        plan = {**meta, 'command': command, 'directory': str(job), 'seed': 73111,
                'input_sha256': sha256_file(original), 'named_alignment_sha256': 'same-alignment',
                'support_replicates': replicates}
        queue = {'metadata': meta, 'jobs': {'job': plan}}
        row = {'completed': True, 'input': original.name, 'input_sha256': plan['input_sha256'],
               'job_key': 'job', 'config': 'A', 'replicate': 0, 'seed': 73111,
               'support': support, 'fast': False, 'tree': str(tree), 'tree_sha256': sha256_file(tree),
               'named_alignment_sha256': plan['named_alignment_sha256'], 'run_json': str(job / 'run.json')}
        self.write_queue(directory, queue, [row])
        return directory, queue, row

    def write_queue(self, directory, queue, rows, *, executed_plan=None):
        (directory / 'queue.json').write_text(json.dumps(queue))
        (directory / 'runs.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        for row in rows:
            run = {'completed': True, 'plan': executed_plan or queue['jobs'][row['job_key']],
                   'tree_sha256': row['tree_sha256']}
            Path(row['run_json']).write_text(json.dumps(run))

    def signature(self, queue, row):
        _, protocol = inference_protocol(queue, row)
        return clade_stability.non_support_search_signature(protocol)

    def test_only_recorded_support_options_are_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, normal, nrow = self.fixture(root, 'normal', False)
            _, supported, srow = self.fixture(root, 'supported', True)
            expected, baseline = self.signature(normal, nrow)
            self.assertEqual(expected, self.signature(supported, srow)[0])
            self.assertEqual(baseline['mem'], '6G')
            self.assertIn('--keep-ident', baseline['command_template'])
            for old, alias in [('-B', '--ufboot'), ('--alrt', '-alrt'), ('--bnni', '-bnni')]:
                altered = deepcopy(supported)
                command = altered['jobs']['job']['command']
                command[command.index(old)] = alias
                self.assertEqual(expected, self.signature(altered, srow)[0])
            for extra in [('normal-unknown', ['--epsilon', '0.001']), ('normal-bnni', ['--bnni'])]:
                _, altered, row = self.fixture(root, extra[0], False, extra=extra[1])
                self.assertNotEqual(expected, self.signature(altered, row)[0])
            _, other_memory, row = self.fixture(root, 'memory', False, mem='3G')
            self.assertNotEqual(expected, self.signature(other_memory, row)[0])

    def test_metadata_command_and_executed_plan_mismatches_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory, queue, row = self.fixture(root, 'support', True)
            forged = deepcopy(queue)
            command = forged['jobs']['job']['command']
            command[command.index('-B') + 1] = '2000'
            self.write_queue(directory, forged, [row])
            with self.assertRaisesRegex(ValueError, 'support replicates'):
                clade_stability.load([directory], True)
            altered = deepcopy(queue)
            altered['jobs']['job']['command'].extend(['--epsilon', '0.001'])
            self.write_queue(directory, altered, [row], executed_plan=queue['jobs']['job'])
            with self.assertRaisesRegex(ValueError, 'Executed inference plan'):
                clade_stability.load([directory], True)

    def test_full_command_compatibility_preserves_nested_counts_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdir, support, srow = self.fixture(root, 'support', True)
            ndir, normal, nrow = self.fixture(root, 'normal', False)
            normal_rows = [nrow, dict(nrow, replicate=1), dict(nrow, config='B')]
            self.write_queue(ndir, normal, normal_rows)
            output = root / 'analysis'
            argv = ['clade_stability.py', '--support-queues', str(sdir), '--normal-queues', str(ndir),
                    '--data', str(root), '--output', str(output)]
            with patch('sys.argv', argv), patch('sys.stdout', new_callable=io.StringIO):
                clade_stability.main()
            summary = json.loads((output / 'summary.json').read_text())
            self.assertEqual(summary['supported_logical_requests'], 1)
            self.assertEqual(summary['normal_logical_requests'], 3)
            self.assertEqual(summary['original_input_hashes'], 1)
            self.assertEqual(summary['rows'], 2)
            provenance = summary['protocol_provenance']
            self.assertEqual(len(provenance['inference_protocols']), 2)
            self.assertEqual(len(provenance['source_queues']), 2)
            self.assertEqual(len({s['non_support_search_signature_ids'][0] for s in provenance['source_queues']}), 1)
            with (output / 'supported-split-stability.tsv').open() as stream:
                rows = list(csv.DictReader(stream, delimiter='\t'))
            self.assertEqual([(row['tested_config'], int(row['logical_requests']), int(row['present_requests']),
                               int(row['unique_executed_jobs'])) for row in rows], [('A', 2, 2, 1), ('B', 1, 1, 1)])
            altered = deepcopy(normal)
            altered['jobs']['job']['command'].extend(['--epsilon', '0.001'])
            self.write_queue(ndir, altered, normal_rows)
            argv[-1] = str(root / 'incompatible')
            with patch('sys.argv', argv):
                with self.assertRaisesRegex(ValueError, 'full non-support inference protocol'):
                    clade_stability.main()
            self.assertFalse((root / 'incompatible').exists())


if __name__ == '__main__':
    unittest.main()
