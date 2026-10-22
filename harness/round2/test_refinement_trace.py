import unittest
from rejected_branch_audit import audit_trace


def event(iteration, branch, status):
    return f'{iteration:03d}-{branch:04d}-0 (thread    1) {status}\r'


class RefinementTraceTests(unittest.TestCase):
    def test_acceptance_invalidates_rejected_branches_and_duplicate_statuses(self):
        trace = ''.join(event(i, b, s) for i, b, s in [
            (1, 0, 'identical'), (1, 0, 'worse'), (1, 1, 'identical'),
            (1, 1, 'better'), (1, 2, 'worse'), (2, 0, 'worse'),
            (2, 1, 'worse'), (2, 2, 'worse')])
        result = audit_trace(trace)
        self.assertEqual(result['completed_branch_attempts'], 6)
        self.assertEqual(result['accepted_changes'], 1)
        self.assertEqual(result['potential_same_state_rejections'], 1)
        self.assertEqual(result['contradictions'], [])

    def test_separate_refiner_invocations_do_not_create_false_contradictions(self):
        first = event(1, 0, 'worse') + event(1, 1, 'worse') + event(2, 0, 'worse')
        second = event(1, 0, 'better') + event(1, 1, 'worse')
        result = audit_trace(first + second)
        self.assertEqual(result['refinement_invocations'], 2)
        self.assertEqual(result['potential_same_state_rejections'], 1)
        self.assertEqual(result['contradictions'], [])
        self.assertTrue(audit_trace(first + event(2, 1, 'better'))['contradictions'])

    def test_multiple_workers_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'one refinement worker'):
            audit_trace('001-0000-0 (thread    2) worse\r')


if __name__ == '__main__':
    unittest.main()
