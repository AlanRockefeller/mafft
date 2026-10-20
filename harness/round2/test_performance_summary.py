"""Check workload units and contention reporting without executing MAFFT."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from summarize_performance import contention_summary, main, unique_input_rows


class PerformanceSummaryTests(unittest.TestCase):
    def test_duplicate_input_timing_is_averaged_before_equal_hash_weighting(self):
        def row(digest, value):
            return dict(input_sha256=digest, **{key: value for key in
                        ['reference_cpu', 'candidate_cpu', 'reference_wall', 'candidate_wall']})
        result = unique_input_rows([row('a', 1), row('a', 5), row('b', 9)])
        self.assertEqual([r['reference_cpu'] for r in result], [3, 9])

    def test_unknown_contention_remains_unknown_and_flagged_jobs_are_retained(self):
        result = contention_summary([{}, {'contention': {'contention_flag': True,
                                    'external_busy_cores_estimate': .5}},
                                    {'contention': {'contention_flag': False}}])
        self.assertEqual((result['jobs'], result['flagged'], result['not_flagged'], result['unknown']), (3, 1, 1, 1))
        self.assertEqual(result['max_external_busy_cores_estimate'], .5)

    def test_strict_holdout_excludes_development_hash_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = [{'file': name, 'partition': part, 'sha256': digest, 'sequences': 4, 'residues': 20}
                     for name, part, digest in [('dev', 'development', 'a'), ('leak', 'holdout', 'a'),
                                               ('hold', 'holdout', 'b'), ('copy', 'holdout', 'b')]]
            manifest = root / 'manifest.json'; manifest.write_text(json.dumps({'files': files}))
            records = []
            for f in files:
                for build, cpu in [('dikarya2', 2.), ('dikarya3', 1.)]:
                    records.append({'build': build, 'input': f['file'], 'input_sha256': f['sha256'],
                                    'round': 0, 'returncode': 0, 'identical': True, 'cpu': cpu, 'wall': cpu,
                                    'golden_sha256': 'gold', 'resources': {'rss_kib': 1024},
                                    'contention': {'contention_flag': f['file'] == 'leak'}})
            runs = root / 'runs.jsonl'; runs.write_text(''.join(json.dumps(r)+'\n' for r in records))
            output = root / 'summary'
            args = ['summary', '--runs', str(runs), '--manifest', str(manifest),
                    '--output', str(output), '--bootstrap', '0']
            with patch('sys.argv', args), redirect_stdout(io.StringIO()):
                main()
            result = json.loads((output/'summary.json').read_text())
            totals = {r['partition']: r for r in result['totals']}
            self.assertEqual(totals['holdout']['jobs_per_corpus_pass'], 3)
            self.assertEqual(totals['holdout_unique_excluding_development']['jobs_per_corpus_pass'], 1)
            self.assertEqual(totals['holdout_unique_excluding_development']['cpu_speedup'], 2.)
            self.assertEqual(result['holdout_audit']['development_hashes_also_in_holdout_files'], ['leak'])
            self.assertEqual(result['contention']['dikarya2']['flagged'], 1)
            self.assertEqual(result['completed_exact_jobs'], 8)


if __name__ == '__main__':
    unittest.main()
