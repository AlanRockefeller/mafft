import unittest
from observed_starts import command_identity
from trace_replay import simulate


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


if __name__ == '__main__':
    unittest.main()
