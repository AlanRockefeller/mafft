import unittest
from observed_starts import command_identity
from trace_replay import service_contention, simulate


class TraceReplayTests(unittest.TestCase):
    def test_slots_and_queues_are_separate_from_service(self):
        arrivals = [{'input': name, 'relative_seconds': t} for name, t in [('a', 0.), ('b', 0.), ('c', 1.)]]
        services = {'a': 4., 'b': 1., 'c': 1.}
        serial = simulate(arrivals, services, 1)
        parallel = simulate(arrivals, services, 2)
        self.assertEqual([r['queue'] for r in serial], [0., 4., 4.])
        self.assertEqual([r['response'] for r in serial], [4., 5., 5.])
        self.assertEqual([r['queue'] for r in parallel], [0., 0., 0.])
        self.assertEqual([r['finish'] for r in parallel], [4., 1., 2.])

    def test_command_prefix_and_option_order_preserve_identity(self):
        plain = ['/opt/mafft', '--adjustdirection', '--thread', '2', '--auto', '/input.fa']
        self.assertEqual(command_identity(plain), command_identity(['CMD:', *plain]))
        self.assertEqual(command_identity(plain), ('/opt/mafft', 2, ['--adjustdirection', '--auto'], '/input.fa'))

    def test_service_uncertainty_is_restricted_to_the_modeled_inputs(self):
        rows = [{'input': 'a', 'contention': {'contention_flag': False}},
                {'input': 'a', 'contention': {'contention_flag': True}},
                {'input': 'b'},
                {'input': 'c', 'contention': {'contention_flag': False}}]
        selected = service_contention(rows, ['a', 'b', 'a'])
        self.assertEqual(selected['inputs'], 2)
        self.assertEqual(selected['observations'], 3)
        self.assertEqual(selected['flagged_observations'], 1)
        self.assertEqual(selected['unknown_observations'], 1)
        self.assertEqual(selected['affected_inputs'], ['a', 'b'])
        clear = service_contention(rows, ['c'])
        self.assertFalse(clear['has_flagged_or_unknown_observations'])
        self.assertEqual(clear['observations'], 1)

    def test_unknown_concurrent_attribution_cannot_be_called_clear(self):
        description = service_contention([{'input': 'a', 'contention': {'contention_flag': None}}], ['a'])
        self.assertTrue(description['has_flagged_or_unknown_observations'])
        with self.assertRaisesRegex(ValueError, 'Missing service-time evidence'):
            service_contention([{'input': 'a'}], ['b'])
        with self.assertRaisesRegex(ValueError, 'boolean or unknown'):
            service_contention([{'input': 'a', 'contention': {'contention_flag': 0}}], ['a'])


if __name__ == '__main__':
    unittest.main()
