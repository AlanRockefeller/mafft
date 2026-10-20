import unittest
from refinement_semantics import read_semantics


def semantic(score=b'0000000000000000', accepted=b'0'):
    return (b'REFINE_SEMANTIC iter=0 branch=0 score='+score+b' accepted='+accepted+
            b' subgeneration=0 converged=0 finish=0 gainlist=0000000000000000'
            b' tscorelist=0000000000000000 basegain=0000000000000000\n')


class RefinementSemanticTests(unittest.TestCase):
    def test_exact_records_keep_signed_zero_and_full_accepted_rows(self):
        positive, _ = read_semantics(semantic(), 2)
        negative, _ = read_semantics(semantic(b'8000000000000000'), 2)
        self.assertNotEqual(positive, negative)
        raw = semantic(accepted=b'1') + b'REFINE_MASTER iter=0 branch=0 row=0 seq=AC-G\nREFINE_MASTER iter=0 branch=0 row=1 seq=ACGG\n'
        rows, summary = read_semantics(raw, 2)
        self.assertEqual(len(rows), 3)
        self.assertEqual(summary['exact_master_rows'], 2)
        with self.assertRaisesRegex(ValueError, 'Misordered'):
            read_semantics(raw.replace(b'row=1', b'row=0'), 2)
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            read_semantics(raw.rsplit(b'REFINE_MASTER', 1)[0], 2)

    def test_active_cache_diagnostics_must_agree(self):
        raw = b'001-0000-0 (thread    1) cached-rejection\r\n' + semantic() + b'REJECT_MEMO hits=1 misses=2\n'
        self.assertEqual(read_semantics(raw, 2)[1]['cached_attempts'], 1)
        with self.assertRaisesRegex(ValueError, 'Cache-hit diagnostic'):
            read_semantics(raw.replace(b'hits=1', b'hits=0'), 2)


if __name__ == '__main__':
    unittest.main()
