"""Small recorded-artifact fixtures: no MAFFT, inference, rendering or timings."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import performance_figures as figures


def save(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Fixture:
    def __init__(self, root):
        self.root = root
        self.inputs = [('a', 'same'), ('copy', 'same'), ('b', 'other')]
        self.goldens = {name: 'gold-' + name for name, _ in self.inputs}
        self.native = {'launcher_sha256': 'recorded-launcher', 'version': 'v7.526-opt8-dikarya4',
                       'installed_file_sha256': {'bin/mafft': 'recorded-launcher', 'libexec/mafft/dvtditr': 'recorded-native'}}
        self.performance('v2', 'dikarya2', 1)
        self.performance('opt8', 'opt8', 2)
        self.scheduling()

    def performance(self, directory, reference, multiplier):
        root = self.root / directory; root.mkdir()
        rows = []
        for index, ((name, input_hash), ref, cand) in enumerate(zip(self.inputs, [2, 6, 10], [1, 2, 5])):
            row = {'input': name, 'input_sha256': input_hash, 'partition': ['development', 'holdout', 'difficult'][index],
                   'paired_rounds': 2, 'all_exact': True,
                   'reference_cpu': ref * multiplier, 'candidate_cpu': cand,
                   'reference_wall': ref * multiplier * 1.5, 'candidate_wall': cand * 2,
                   'reference_contention_flagged_rounds': int(name == 'copy'),
                   'reference_contention_unknown_rounds': int(name == 'a'),
                   'candidate_contention_flagged_rounds': int(name == 'b'), 'candidate_contention_unknown_rounds': 0}
            for metric in ('cpu', 'wall'):
                row[metric + '_speedup'] = row['reference_' + metric] / row['candidate_' + metric]
            rows.append(row)
        with (root / 'per-input.tsv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter='\t')
            writer.writeheader(); writer.writerows(rows)
        # These expected totals use manual copy averaging, including a group
        # whose ratio-of-means differs from its mean of per-filename ratios.
        totals = []
        for label, n, ref, cand in [('all', 3, 18 * multiplier, 8),
                                     ('all_unique_inputs', 2, 14 * multiplier, 6.5)]:
            total = {'partition': label, 'jobs_per_corpus_pass': n, 'distinct_input_hashes': 2}
            for metric, a, b in [('cpu', ref, cand), ('wall', ref * 1.5, cand * 2)]:
                total.update({metric + '_reference': a, metric + '_candidate': b, metric + '_speedup': a / b})
            totals.append(total)
        save(root / 'summary.json', {'reference': reference, 'candidate': 'dikarya4',
            'manifest_sha256': 'frozen-manifest', 'completed_exact_jobs': 12,
            'runs': str(root / 'runs.jsonl'), 'runs_sha256': 'recorded-log-hash', 'totals': totals,
            'contention': {reference: {'jobs': 6, 'flagged': 1, 'not_flagged': 4, 'unknown': 1},
                           'dikarya4': {'jobs': 6, 'flagged': 1, 'not_flagged': 5, 'unknown': 0}}})
        save(root / 'experiment.json', {'manifest_sha256': 'frozen-manifest',
            'files': [{'file': name, 'sha256': sha} for name, sha in self.inputs],
            'builds': [dict(self.native, name='dikarya4')], 'golden_sha256': self.goldens})

    def scheduling(self):
        root = self.root / 'scheduling'; root.mkdir()
        full = []
        for index, policy in enumerate(figures.FULL):
            directory = root / policy; directory.mkdir()
            row = {'policy': policy, 'round': 0, 'jobs': 3, 'distinct_input_hashes': 2,
                   'makespan_seconds': 50 + index, 'jobs_per_hour': 3 * 3600 / (50 + index),
                   'cpu_per_job_seconds': 3 + index,
                   'matches_A': 3 if policy in ['two_jobs', 'refine_one'] else 2,
                   'serial_jobs_flagged': int(policy == 'production'),
                   'worker_jobs_without_individual_attribution': 3 if policy == 'two_jobs' else 0}
            path = directory / 'summary.json'
            save(path, dict(row, queue_order=['b', 'a', 'copy'],
                            contention={'available': True, 'contention_flag': False, 'external_attribution_valid': True}))
            full.append(dict(row, summary=str(path), summary_sha256=digest(path)))
        per_input, by_config = [], []
        for policy, a, b in [('one_job', 1, 3), ('two_threads', .7, 2), ('selective', .9, 2.5), ('refine_one', .95, 2.9)]:
            config = figures.CONFIGS[policy]
            for name, value in [('a', a), ('b', b)]:
                per_input.append({'input': name, 'input_sha256': dict(self.inputs)[name], 'policy': policy,
                                  'config': config, 'repeats': 2, 'mean_service_seconds': value,
                                  'matches_A': 2 if config in ['A', 'F'] else 0,
                                  'serial_jobs_flagged': int(config == 'A' and name == 'a'),
                                  'distinct_output_hashes': 2 if config == 'B' else 1,
                                  'repeatable_in_two_runs': config != 'B'})
            by_config.append({'policy': policy, 'config': config, 'input_files': 2, 'distinct_input_hashes': 2,
                              'measured_runs': 4, 'median_file_mean_latency_seconds': (a + b) / 2,
                              'p95_file_mean_latency_seconds': a + .95 * (b - a),
                              'matches_A': 4 if config in ['A', 'F'] else 0,
                              'serial_jobs_flagged': int(config == 'A')})
        save(root / 'summary.json', {'schema': 'mafft-round2-scheduling-summary-v1',
            'manifest_sha256': 'frozen-manifest', 'production_files': 3, 'distinct_production_input_hashes': 2,
            'native_build_and_source': dict(self.native, core_file_sha256={'core/example.c': 'frozen-source'}),
            'golden_sha256': self.goldens, 'full_planned_queue_order': ['b', 'a', 'copy'],
            'selected_latency_inputs': ['a', 'b'], 'distinct_latency_input_hashes': 2,
            'full_policies': full, 'isolated_per_input': per_input, 'isolated_by_config': by_config})

    def prepare(self):
        return figures.prepare(self.root / 'v2', self.root / 'opt8', self.root / 'scheduling')

    def mixed_scheduling(self):
        path = self.root / 'scheduling/summary.json'
        value = json.loads(path.read_text()); value['schema'] = 'mafft-round2-scheduling-summary-v2'
        for row in value['isolated_per_input']:
            repeats = 40 if row['input'] == 'a' else 2
            row.update(repeats=repeats, paired_rounds=2, native_repeats_per_input=repeats)
            if row['config'] in ('A', 'F'): row['matches_A'] = repeats
            if row['config'] == 'A' and row['input'] == 'a': row['serial_jobs_flagged'] = 37
        for row in value['isolated_by_config']:
            records = [r for r in value['isolated_per_input'] if r['policy'] == row['policy']]
            row.update(measured_runs=sum(r['repeats'] for r in records),
                       matches_A=sum(r['matches_A'] for r in records),
                       serial_jobs_flagged=sum(r['serial_jobs_flagged'] for r in records))
        save(path, value)
        return path, value


class PerformanceFigureTests(unittest.TestCase):
    def test_import_does_not_import_matplotlib(self):
        original = __import__
        def guarded(name, *args, **kwargs):
            if name.startswith('matplotlib'):
                raise AssertionError('Matplotlib was imported outside main')
            return original(name, *args, **kwargs)
        spec = importlib.util.spec_from_file_location('fixture_figures', figures.__file__)
        module = importlib.util.module_from_spec(spec)
        with patch('builtins.__import__', guarded):
            spec.loader.exec_module(module)

    def test_complete_fixture_preserves_units_flags_and_ratio_of_means(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary)); result = fixture.prepare()
            self.assertEqual(result['counts'], {'production_filenames': 3, 'distinct_production_input_hashes': 2,
                                              'selected_latency_filenames': 2, 'distinct_selected_latency_input_hashes': 2,
                                              'native_isolated_runs_per_configuration': 4})
            self.assertEqual(len(result['tables']['per-file-speedups']), 6)
            self.assertEqual(len(result['tables']['unique-input-speedups']), 4)
            copies = next(row for row in result['tables']['unique-input-speedups']
                          if row['comparison'] == 'dikarya2-comparison' and row['input_sha256'] == 'same')
            self.assertAlmostEqual(copies['cpu_speedup'], 8 / 3)
            self.assertEqual(copies['wall_speedup'], 2)
            self.assertEqual(copies['production_filenames'], ['a', 'copy'])
            self.assertEqual(copies['reference_contention_unknown_rounds'], 1)
            self.assertEqual(len(result['tables']['contention']), 13)
            concurrent = next(row for row in result['tables']['contention'] if row['build_or_policy'] == 'two_jobs')
            self.assertEqual((concurrent['unknown_job_flags'], concurrent['policy_contention_flag']), (3, False))
            self.assertFalse(any(path.endswith('runs.jsonl') for path in result['sources']))
            self.assertEqual(len(result['tables']['isolated-input-latency']), 8)

    def test_mixed_protocol_keeps_native_counts_and_equal_input_weights(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary)); fixture.mixed_scheduling(); result = fixture.prepare()
            self.assertEqual(result['counts']['native_isolated_runs_per_configuration'], 42)
            row = next(r for r in result['tables']['isolated-config-latency'] if r['config'] == 'A')
            self.assertEqual(row['measured_runs'], 42)
            self.assertEqual(row['matches_A'], 42)
            self.assertEqual(row['serial_jobs_flagged'], 37)
            self.assertEqual(row['median_file_mean_latency_seconds'], 2)

    def test_mixed_protocol_rejects_inconsistent_rounds_or_native_counts(self):
        for failure in ['rounds', 'count', 'configuration_count', 'aggregate_count']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary)); path, value = fixture.mixed_scheduling()
                row = value['isolated_per_input'][0]
                if failure == 'rounds': row['paired_rounds'] = 1
                if failure == 'count': row['native_repeats_per_input'] = 2
                if failure == 'configuration_count':
                    row = next(r for r in value['isolated_per_input'] if r['input'] == 'a' and r['config'] == 'B')
                    row.update(repeats=2, native_repeats_per_input=2)
                if failure == 'aggregate_count': value['isolated_by_config'][0]['measured_runs'] = 4
                save(path, value)
                with self.assertRaisesRegex(ValueError, 'repeat counts|counts differ|denominator'):
                    fixture.prepare()

    def test_mixed_protocol_a_or_f_requires_every_native_golden_match(self):
        for config in ['A', 'F']:
            with self.subTest(config=config), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary)); path, value = fixture.mixed_scheduling()
                next(r for r in value['isolated_per_input'] if r['input'] == 'a' and r['config'] == config)['matches_A'] = 39
                save(path, value)
                with self.assertRaisesRegex(ValueError, 'deterministic A/F'):
                    fixture.prepare()

    def test_mixed_manifest_build_or_golden_is_rejected(self):
        for failure in ['manifest', 'build', 'golden']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary)); path = fixture.root / 'scheduling/summary.json'
                value = json.loads(path.read_text())
                if failure == 'manifest': value['manifest_sha256'] = 'different'
                if failure == 'build': value['native_build_and_source']['version'] = 'other-build'
                if failure == 'golden': value['golden_sha256']['a'] = 'changed-golden'
                save(path, value)
                with self.assertRaisesRegex(ValueError, 'manifests differ|native builds differ|goldens differ'):
                    fixture.prepare()

    def test_missing_or_duplicate_isolated_pair_is_rejected(self):
        for failure in ['missing', 'duplicate']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary)); path = fixture.root / 'scheduling/summary.json'
                value = json.loads(path.read_text())
                if failure == 'missing': value['isolated_per_input'].pop()
                else: value['isolated_per_input'].append(dict(value['isolated_per_input'][0]))
                save(path, value)
                with self.assertRaisesRegex(ValueError, 'incomplete or duplicated'):
                    fixture.prepare()

    def test_isolated_a_or_f_mismatch_is_rejected(self):
        for config in ['A', 'F']:
            with self.subTest(config=config), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary)); path = fixture.root / 'scheduling/summary.json'
                value = json.loads(path.read_text())
                next(row for row in value['isolated_per_input'] if row['config'] == config)['matches_A'] = 1
                save(path, value)
                with self.assertRaisesRegex(ValueError, 'deterministic A/F'):
                    fixture.prepare()

    def test_contended_selected_full_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary)); path = fixture.root / 'scheduling/summary.json'
            value = json.loads(path.read_text()); row = value['full_policies'][0]
            raw_path = Path(row['summary']); raw = json.loads(raw_path.read_text())
            raw['contention']['contention_flag'] = True; save(raw_path, raw)
            row['summary_sha256'] = digest(raw_path); save(path, value)
            with self.assertRaisesRegex(ValueError, 'uncontended attribution'):
                fixture.prepare()

    def test_full_f_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary)); path = fixture.root / 'scheduling/summary.json'
            value = json.loads(path.read_text())
            row = next(r for r in value['full_policies'] if r['policy'] == 'refine_one')
            raw_path = Path(row['summary']); raw = json.loads(raw_path.read_text())
            raw['matches_A'] = row['matches_A'] = 2
            save(raw_path, raw); row['summary_sha256'] = digest(raw_path); save(path, value)
            with self.assertRaisesRegex(ValueError, 'deterministic A/F'):
                fixture.prepare()

    def test_changed_policy_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary)); path = fixture.root / 'scheduling/production/summary.json'
            path.write_text(path.read_text() + '\n')
            with self.assertRaisesRegex(ValueError, 'policy summary changed'):
                fixture.prepare()

    def test_existing_output_is_rejected_before_loading_or_rendering(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = ['performance_figures', '--dikarya2-analysis', 'not-opened', '--opt8-analysis', 'not-opened',
                    '--scheduling-analysis', 'not-opened', '--output', temporary]
            with patch.object(sys, 'argv', args), patch.object(figures, 'prepare') as prepare:
                with self.assertRaises(SystemExit) as stopped:
                    figures.main()
                self.assertEqual(stopped.exception.code, 2); prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
