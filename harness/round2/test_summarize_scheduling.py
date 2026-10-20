"""Recorded scheduling fixtures only; creating them never launches native jobs."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import tempfile
import unittest

from summarize_scheduling import aggregate, FULL, ISOLATED, PILOT, POLICIES, FLAGS, stats


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data))


class Fixture:
    def __init__(self, root):
        self.root = root
        self.data, self.golden = root / 'data', root / 'golden'
        self.data.mkdir(); self.golden.mkdir()
        self.binary = root / 'install/bin/mafft'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_text('fixture launcher, never executed\n')
        self.helper = root / 'install/libexec/mafft/dvtditr'
        self.helper.parent.mkdir(parents=True)
        self.helper.write_bytes(b'fixture native bytes, never executed')
        self.source = root / 'source'
        (self.source / 'core').mkdir(parents=True)
        (self.source / 'core/a.c').write_text('fixture source\n')
        self.files = []
        for name, sequence, threads in [('a.fasta', 'AC', 1), ('copy.fasta', 'AC', 1), ('b.fasta', 'AG', 2)]:
            (self.data / name).write_text('>taxon\n' + sequence + '\n')
            (self.golden / name).write_text('>taxon\n' + sequence + '-\n')
            self.files.append({'file': name, 'sha256': digest(self.data / name),
                               'production_threads': threads, 'production_options': ['--auto']})
        self.manifest = root / 'manifest.json'
        save(self.manifest, {'files': self.files})
        self.campaign('full', [f['file'] for f in self.files], FULL, rounds=1)
        self.campaign('pilot', ['a.fasta', 'b.fasta'], PILOT)
        for name in ['a.fasta', 'b.fasta']:
            self.campaign('isolated-' + name, [name], ISOLATED)
        self.full_rows = [{'selected': True, 'policy': policy, 'summary': f'full/r0/{policy}/summary.json'} for policy in FULL]
        self.full_rows.insert(0, {'selected': False, 'deferred': True, 'reason': 'quiet deferred', 'output': 'not-created'})
        self.isolated_rows = [{'selected': True, 'input': name, 'output': 'isolated-' + name} for name in ['a.fasta', 'b.fasta']]
        self.pilot_rows = [{'selected': True, 'output': 'pilot'}]
        self.ledgers()

    def ledgers(self):
        for name, rows in [('full', self.full_rows), ('isolated', self.isolated_rows), ('pilot', self.pilot_rows)]:
            (self.root / (name + '-selection.jsonl')).write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def args(self):
        return argparse.Namespace(full_selection=self.root / 'full-selection.jsonl',
                                  isolated_selection=self.root / 'isolated-selection.jsonl',
                                  pilot_selection=self.root / 'pilot-selection.jsonl', manifest=self.manifest,
                                  output=self.root / 'summary')

    def campaign(self, directory, names, policies, rounds=2, seed=0):
        root = self.root / directory
        root.mkdir()
        files = [next(f for f in self.files if f['file'] == name) for name in names]
        metadata = {'binary': str(self.binary), 'launcher_sha256': digest(self.binary), 'version': 'fixture-dikarya4-r2',
                    'installed_file_sha256': {'bin/mafft': digest(self.binary), 'libexec/mafft/dvtditr': digest(self.helper)},
                    'supplied_source_snapshot': {'root': str(self.source), 'core_file_sha256': {'core/a.c': digest(self.source / 'core/a.c')}},
                    'manifest_sha256': digest(self.manifest), 'files': files, 'data': str(self.data), 'golden': str(self.golden),
                    'golden_sha256': {name: digest(self.golden / name) for name in names},
                    'selected_policies': list(policies), 'rounds': rounds, 'seed': seed, 'input_count': len(names)}
        save(root / 'experiment.json', metadata)
        for rnd in range(rounds):
            order = list(files); random.Random(seed + rnd).shuffle(order)
            for policy in policies:
                config, concurrency = POLICIES[policy]
                out = root / f'r{rnd}' / policy; out.mkdir(parents=True)
                runs = []
                for index, f in enumerate(order):
                    actual = ('A' if f['production_threads'] == 1 else 'B') if config == 'production' else config
                    dest = out / f['file']; dest.mkdir()
                    alignment = dest / 'alignment.fasta'
                    extra = '\n' * (rnd + 1) if actual == 'B' else '\n' if actual == 'C' else ''
                    alignment.write_text((self.golden / f['file']).read_text() + extra)
                    stderr = dest / 'stderr.txt'; stderr.write_text('')
                    resources = {'user': .6, 'system': .2, 'rss_kib': 1024 * (index + 1), 'exit': 0}
                    save(dest / 'resources.json', resources)
                    slot = index % concurrency
                    prefix = ['nice', '-n', '10', 'taskset', '-c', str(slot) if concurrency == 2 else '0,1']
                    run = {'input': f['file'], 'input_sha256': f['sha256'], 'config': actual,
                           'threads': int(FLAGS[actual][1]), 'slot': slot, 'recorded_production_threads': f['production_threads'],
                           'policy': policy, 'round': rnd, 'returncode': 0, 'timed_out': False, 'resources_error': None,
                           'resources': resources, 'cpu': .8, 'queue_seconds': index * .5,
                           'service_seconds': 1 + rnd * .2, 'response_seconds': index * .5 + 1 + rnd * .2,
                           'output_sha256': digest(alignment), 'alignment': str(alignment), 'stderr_sha256': digest(stderr),
                           'golden_sha256': metadata['golden_sha256'][f['file']],
                           'matches_A': digest(alignment) == metadata['golden_sha256'][f['file']],
                           'contention': {'available': True, 'contention_flag': None if concurrency == 2 else False,
                                          'external_attribution_valid': concurrency == 1},
                           'argv': [*prefix, str(self.binary), *FLAGS[actual], *f['production_options'], str(self.data / f['file'])]}
                    runs.append(run)
                (out / 'runs.jsonl').write_text(''.join(json.dumps(run) + '\n' for run in runs))
                elapsed = max(run['response_seconds'] for run in runs) + .5
                quiet = {'ready': True, 'enabled': True}
                summary = {'policy': policy, 'round': rnd, 'config': config, 'concurrency': concurrency,
                           'jobs': len(runs), 'matches_A': sum(run['matches_A'] for run in runs), 'makespan_seconds': elapsed,
                           'jobs_per_hour': len(runs) * 3600 / elapsed, 'cpu_per_job_seconds': .8,
                           'cpu_total_seconds': .8 * len(runs), 'max_per_job_rss_mib': len(runs),
                           'peak_sampled_process_tree_rss_mib': len(runs) * concurrency,
                           'queue_order': [f['file'] for f in order], 'quiet_window': quiet,
                           'contention': {'available': True, 'contention_flag': False, 'external_attribution_valid': True}}
                for key, field in [('service_latency_seconds', 'service_seconds'), ('queue_seconds', 'queue_seconds'),
                                   ('response_latency_seconds', 'response_seconds')]:
                    summary[key] = stats([run[field] for run in runs])
                save(out / 'summary.json', summary); save(out / 'quiet-window.json', quiet)


class SchedulingSummaryTests(unittest.TestCase):
    def test_latency_quantiles_are_interpolated_across_nonconstant_values(self):
        result = stats([9, 1, 2])
        self.assertEqual(result['median'], 2)
        self.assertAlmostEqual(result['p95'], 8.3)

    def test_policy_selection_keeps_transient_serial_flags_and_quiet_deferrals(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary)); path = fixture.root / 'full/r0/refine_one/runs.jsonl'
            runs = [json.loads(line) for line in path.read_text().splitlines()]
            runs[0]['contention']['contention_flag'] = True
            path.write_text(''.join(json.dumps(row) + '\n' for row in runs))
            fixture.full_rows.append({'selected': False, 'quiet_deferred_only': True})
            fixture.ledgers()
            result = aggregate(fixture.args())
            selected = next(row for row in result['full_policies'] if row['policy'] == 'refine_one')
            self.assertEqual(selected['serial_jobs_flagged'], 1)
            self.assertEqual(result['selection_ledgers']['full']['explicit_deferrals'], 2)

    def test_consistent_fixture_preserves_file_denominators_repeats_and_deferrals(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            result = aggregate(fixture.args())
            self.assertEqual((result['production_files'], result['distinct_production_input_hashes']), (3, 2))
            self.assertEqual(len(result['full_policies']), 5)
            self.assertTrue(all(row['jobs'] == 3 for row in result['full_policies']))
            self.assertEqual(len(result['isolated_per_input']), 8)
            self.assertEqual(len(result['pilot_rounds']), 8)
            self.assertEqual(result['selection_ledgers']['full']['explicit_deferrals'], 1)
            self.assertEqual(len(result['selection_ledgers']['full']['original_rows']), 6)
            b = [r for r in result['isolated_per_input'] if r['config'] == 'B']
            self.assertTrue(all(r['distinct_output_hashes'] == 2 and r['matches_A'] == 0 for r in b))
            c = [r for r in result['isolated_per_input'] if r['config'] == 'C']
            self.assertTrue(all(r['repeatable_in_two_runs'] and r['matches_A'] == 0 for r in c))
            self.assertTrue(all(r['matches_A'] == 2 for r in result['isolated_per_input'] if r['config'] in ['A', 'F']))

    def test_mixed_recorded_build_or_source_is_rejected(self):
        for key in ['version', 'source']:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary)); path = fixture.root / 'isolated-a.fasta/experiment.json'
                metadata = json.loads(path.read_text())
                if key == 'version':
                    metadata['version'] = 'another native build'
                else:
                    other = fixture.root / 'other-source'; (other / 'core').mkdir(parents=True)
                    (other / 'core/a.c').write_text('other source')
                    metadata['supplied_source_snapshot'] = {'root': str(other), 'core_file_sha256': {'core/a.c': digest(other / 'core/a.c')}}
                save(path, metadata)
                with self.assertRaisesRegex(ValueError, 'mix native builds or frozen sources'):
                    aggregate(fixture.args())

    def test_missing_run_and_contended_policy_are_rejected(self):
        for failure in ['missing', 'contended']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary)); directory = fixture.root / 'full/r0/two_threads'
                if failure == 'missing':
                    path = directory / 'runs.jsonl'; path.write_text('\n'.join(path.read_text().splitlines()[:-1]) + '\n')
                    message = 'Incomplete or duplicate'
                else:
                    path = directory / 'summary.json'; value = json.loads(path.read_text())
                    value['contention']['contention_flag'] = True; save(path, value); message = 'contended'
                with self.assertRaisesRegex(ValueError, message):
                    aggregate(fixture.args())

    def test_unknown_serial_contention_and_resource_fabrication_are_rejected(self):
        for failure in ['unknown', 'resource']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary)); path = fixture.root / 'full/r0/refine_one/runs.jsonl'
                runs = [json.loads(line) for line in path.read_text().splitlines()]
                if failure == 'unknown':
                    runs[0]['contention']['contention_flag'] = None; message = 'unknown attribution'
                else:
                    runs[0]['resources']['rss_kib'] += 1; message = 'resource file differs'
                path.write_text(''.join(json.dumps(row) + '\n' for row in runs))
                with self.assertRaisesRegex(ValueError, message):
                    aggregate(fixture.args())

    def test_a_and_f_mismatch_is_rejected_even_when_recorded_matches_flag_is_honest(self):
        for policy in ['two_jobs', 'refine_one']:
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary)); directory = fixture.root / f'full/r0/{policy}'
                path = directory / 'runs.jsonl'; runs = [json.loads(line) for line in path.read_text().splitlines()]
                alignment = Path(runs[0]['alignment']); alignment.write_text('changed alignment\n')
                runs[0].update(output_sha256=digest(alignment), matches_A=False)
                path.write_text(''.join(json.dumps(row) + '\n' for row in runs))
                with self.assertRaisesRegex(ValueError, 'Deterministic A/F'):
                    aggregate(fixture.args())

    def test_duplicate_selected_policy_or_isolated_input_is_rejected(self):
        for role in ['full', 'isolated']:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary))
                rows = fixture.full_rows if role == 'full' else fixture.isolated_rows
                rows.append(deepcopy(next(row for row in rows if row['selected'])))
                fixture.ledgers()
                with self.assertRaisesRegex(ValueError, 'Duplicate'):
                    aggregate(fixture.args())

    def test_full_policies_cannot_use_different_seeded_workload_orders(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            fixture.campaign('another-order', [f['file'] for f in fixture.files], ['two_threads'], rounds=1, seed=1)
            next(row for row in fixture.full_rows if row.get('policy') == 'two_threads')['summary'] = 'another-order/r0/two_threads/summary.json'
            fixture.ledgers()
            with self.assertRaisesRegex(ValueError, 'identical input sets/orders'):
                aggregate(fixture.args())


if __name__ == '__main__':
    unittest.main()
