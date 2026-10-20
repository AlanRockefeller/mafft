#!/usr/bin/env python3
"""Small mathematical/provenance fixtures; no alignment or inference programs."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analysis import make_mapping, sha256_file
from biological_reference import score_queue_quartets, validated_queue_records
import figures
from figures import Cohort, taxon_stability_rows
from tree import compare_trees, read_tree
from support_changes import shared_changes


class ScienceExportTests(unittest.TestCase):
    def test_support_changes_distinguish_joint_thresholds_and_missing_splits(self):
        edge = lambda sh, uf: {'id': 'split', 'side': ['one', 'two'], 'size': 2,
                               'support': {'shalrt': sh, 'ufboot': uf}}
        comparison = {'views': {'zero_collapsed': {'split_details': [
            {'status': 'shared', 'reference': edge(90, 96), 'candidate': edge(79, 99)},
            {'status': 'reference_only', 'reference': edge(100, 100), 'candidate': None}]}}}
        rows = shared_changes(comparison)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['reference_strong'])
        self.assertFalse(rows[0]['candidate_strong'])
        self.assertEqual(rows[0]['shalrt_delta'], -11)
        self.assertEqual(rows[0]['ufboot_delta'], 3)
        comparison['views']['zero_collapsed']['split_details'][0]['candidate']['side'] = ['one', 'three']
        with self.assertRaisesRegex(ValueError, 'Shared split labels disagree'):
            shared_changes(comparison)

    def original(self, root):
        source = root / 'fixture.fasta'
        source.write_text('>one original label\nACGTA\n>two original label\nACGTT\n'
                          '>three original label\nACGTC\n>four original label\nACGTG\n>five original label\nACGAA\n')
        return make_mapping(source)

    def quartet_fixture(self, root):
        mapping = self.original(root)
        source = root / 'rpb2.tre'
        source.write_text('((s1:1,s2:1)98:1,(s3:1,s4:1):1,s5:1);')
        reference = read_tree(source, support_order=['bootstrap'])
        same = root / 'same.treefile'
        same.write_text('((t000001:1,t000002:1):1,(t000003:1,t000004:1):1,t000005:1);')
        other = root / 'other.treefile'
        other.write_text('((t000001:1,t000003:1)99/99:1,(t000002:1,t000004:1)99/99:1,t000005:1);')
        other.with_suffix('.iqtree').write_text('SH-aLRT support (%) / ultrafast bootstrap support (%)\n')
        matched = {'fixture': [{'id': f't{i:06d}', 'accepted_reference_tip': f's{i}'} for i in range(1, 5)]}
        protocol = {'fast': False, 'support': False}
        requests = []
        for config, path, reused, support in [('A', same, False, False), ('B', same, True, False), ('C', other, False, True)]:
            requests.append({'tree': str(path), 'input': 'fixture.fasta', 'input_sha256': mapping['input_sha256'],
                             'config': config, 'replicate': 0, 'seed': 73111, 'named_alignment_sha256': config,
                             'tree_sha256': sha256_file(path), 'reused': reused,
                             'inference_counts_as_independent': not reused, 'source_stage': 'fixture-stage',
                             'source_runs_log': 'fixture-runs', 'run_json': str(path.with_suffix('.json')),
                             'protocol_id': 'support' if support else 'normal',
                             'protocol': dict(protocol, support=support), 'support': support})
        return mapping, reference, same, other, matched, requests

    def test_reference_checks_keep_all_taxa_and_separate_reuse_from_biological_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mapping, reference, _, _, matched, requests = self.quartet_fixture(root)
            rows, summary, protocols = score_queue_quartets(matched, {'fixture': mapping}, {'rpb2': reference}, requests)
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row['full_original_taxa'] == 5 for row in rows))
            self.assertTrue(all(row['reference_classical_bootstrap_ge95_witness'] for row in rows))
            self.assertEqual([row['raw_congruent'] for row in rows], [True, True, False])
            self.assertEqual([row['strongly_supported_conflict'] for row in rows], [False, False, True])
            self.assertEqual(rows[0]['original_headers'][0], 'one original label')
            groups = {row['config']: row for row in summary['fixture']['rpb2']['groups']}
            self.assertEqual(groups['B']['search_jobs_owned_by_config'], 0)
            self.assertEqual(groups['B']['reused_logical_requests'], 1)
            self.assertTrue(all(row['biological_input_hashes'] == 1 for row in groups.values()))
            self.assertEqual(set(protocols), {'normal', 'support'})

    def test_missing_taxon_and_nonbijective_reference_mapping_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mapping, reference, same, _, matched, requests = self.quartet_fixture(root)
            same.write_text('((t000001,t000002),(t000003,t000004));')
            with self.assertRaisesRegex(ValueError, 'full original taxon set'):
                score_queue_quartets(matched, {'fixture': mapping}, {'rpb2': reference}, requests[:1])
            duplicate = deepcopy(matched)
            duplicate['fixture'].append(duplicate['fixture'][0])
            with self.assertRaisesRegex(ValueError, 'Non-bijective'):
                score_queue_quartets(duplicate, {'fixture': mapping}, {'rpb2': reference}, [])

    def test_standalone_runs_log_validates_hashes_without_directory_column(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mapping = self.original(root)
            job = root / 'job'; job.mkdir()
            named = job / 'named.fasta'; named.write_text('>t000001\nAC\n')
            tree = job / 'inference.treefile'; tree.write_text('(t000001,t000002,t000003);')
            meta = {'binary_sha256': 'fixture-binary', 'iqtree_version': 'fixture2', 'model': 'GTR+F+G4',
                    'threads': 1, 'nstop': None, 'fast': False, 'support': False}
            command = ['/fixture/iqtree2', '-s', str(named), '-m', 'GTR+F+G4', '-T', '1',
                       '--seed', '73111', '--mem', '6G', '--prefix', str(job / 'inference')]
            plan = dict(meta, command=command, seed=73111, directory=str(job), support_replicates=None,
                        input_sha256=mapping['input_sha256'], named_alignment_sha256=sha256_file(named))
            run = job / 'run.json'
            run.write_text(json.dumps({'completed': True, 'plan': plan, 'tree_sha256': sha256_file(tree)}))
            row = {'completed': True, 'run_json': str(run), 'job_key': 'fixture-job', 'tree': str(tree),
                   'tree_sha256': sha256_file(tree), 'config': 'A', 'replicate': 0, 'seed': 73111,
                   'input_sha256': mapping['input_sha256'], 'named_alignment_sha256': sha256_file(named)}
            log = root / 'runs.jsonl'; log.write_text(json.dumps(row) + '\n' + json.dumps(row) + '\n')
            records, sources = validated_queue_records([log], {'fixture': mapping})
            self.assertEqual(len(records), 1)
            self.assertFalse(records[0]['support'])
            self.assertEqual(sources[0]['duplicate_log_records'], 1)
            named.write_text('changed')
            with self.assertRaisesRegex(ValueError, 'Changed named alignment'):
                validated_queue_records([log], {'fixture': mapping})

    def test_participation_export_joins_original_labels_without_recomputing_quartets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mapping, _, same, other, _, _ = self.quartet_fixture(root)
            result = compare_trees(read_tree(same), read_tree(other), mapping=mapping, quartets=100, details=True)
            info = {'input': 'fixture.fasta', 'input_sha256': mapping['input_sha256'], 'kind': 'A_vs_C',
                    'protocol_id': 'fixture', 'reference_config': 'A', 'candidate_config': 'C',
                    'reference_replicate': 0, 'candidate_replicate': 0, 'reference_seed': 73111,
                    'candidate_seed': 73111, 'comparison': result}
            (root / 'comparisons.jsonl').write_text(json.dumps(info) + '\n')
            cohort = Cohort(root, 'fixture', {}, [], {})
            selected = [{k: v for k, v in info.items() if k != 'comparison'}]
            rows = taxon_stability_rows(cohort, selected, ['raw'], root, {})
            self.assertEqual(len(rows), 5)
            self.assertEqual(rows[0]['original_header'], 'one original label')
            self.assertEqual(sum(row['sampled_quartets'] for row in rows), 20)
            self.assertEqual(sum(row['discordant_quartets'] for row in rows), 4 * result['views']['raw']['quartets']['different'])
            self.assertTrue(all(row['quartet_panel_exact'] for row in rows))
            self.assertTrue(all('not a placement-error' in row['interpretation'] for row in rows))
            flat = dict(selected[0], view='raw', normalized_rf=result['views']['raw']['normalized_rf'],
                        incompatible_split_pairs=result['views']['raw']['incompatible_split_pairs'])
            cohort.rows = [flat]
            cohort.protocol = {'fast': False, 'support': False}
            (root / 'summary.json').write_text('{}')
            (root / 'comparisons.tsv').write_text('mocked provenance fixture\n')
            output = root / 'export'
            argv = ['figures.py', '--tree-analysis', str(root), '--data', str(root), '--output', str(output),
                    '--tables-only', '--views', 'raw']
            with patch('sys.argv', argv), patch('figures.load_cohorts', return_value=[cohort]), \
                 patch('figures.queue_index', return_value={}), \
                 patch('figures.plt_import', side_effect=AssertionError('Rendering was requested')), \
                 patch('sys.stdout', new_callable=io.StringIO):
                figures.main()
            self.assertTrue((output / 'taxon-quartet-stability.tsv').exists())
            self.assertEqual(json.loads((output / 'figure-manifest.json').read_text())['selected_detailed_cases'], 1)


if __name__ == '__main__':
    unittest.main()
