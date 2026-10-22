"""Synthetic /proc-counter fixtures; no waits, scientific jobs or signals."""
import unittest
from unittest.mock import patch

import quiet


def snapshot(seconds,ticks,steal=0):
    return {'available':True,'monotonic':seconds,'busy_ticks':ticks,'steal_ticks':steal,'tick_hz':100,'cpu_count':2}


class QuietTests(unittest.TestCase):
    def test_external_busy_estimate_and_rounding_are_explicit(self):
        result=quiet.busy_delta(snapshot(1,100),snapshot(3,370,5),2.0)
        self.assertAlmostEqual(result['external_busy_seconds_estimate'],.7)
        self.assertAlmostEqual(result['external_busy_cores_estimate'],.35)
        self.assertTrue(result['contention_flag'])
        self.assertEqual(result['steal_seconds'],.05)
        rounded=quiet.busy_delta(snapshot(1,100),snapshot(2,199),1.0)
        self.assertLess(rounded['global_busy_minus_job_cpu_seconds_raw'],0)
        self.assertEqual(rounded['external_busy_seconds_estimate'],0)

    def test_parallel_job_residual_is_not_attributed_as_external(self):
        result=quiet.busy_delta(snapshot(1,100),snapshot(3,500),2.0,other_measured_jobs_possible=True)
        self.assertTrue(result['global_unaccounted_busy_flag'])
        self.assertIsNone(result['contention_flag'])
        self.assertFalse(result['external_attribution_valid'])
        policy=quiet.busy_delta(snapshot(1,100),snapshot(3,500),4.0)
        self.assertFalse(policy['contention_flag'])

    def test_missing_resources_do_not_fabricate_cpu_accounting(self):
        result=quiet.busy_delta(snapshot(1,100),snapshot(3,500),None)
        self.assertIsNone(result['external_busy_seconds_estimate'])
        self.assertIsNone(result['contention_flag'])

    def test_quiet_wait_needs_consecutive_samples_after_busy_interval(self):
        clock=[0.0]
        config={'enabled':True,'timeout_seconds':10,'interval_seconds':1,'consecutive_samples':2,
                'max_busy_cores':.1,'max_scientific_cores':.05}
        def sleep(seconds):clock[0]+=seconds
        with patch('quiet.time.monotonic',side_effect=lambda:clock[0]), patch('quiet.time.sleep',side_effect=sleep), \
             patch('quiet.cpu_snapshot',side_effect=[snapshot(0,0),snapshot(1,100),snapshot(2,101),snapshot(3,102)]), \
             patch('quiet.scientific_snapshot',return_value={}):
            result=quiet.wait_for_quiet(config)
        self.assertTrue(result['ready']);self.assertEqual(result['wait_seconds'],3)
        self.assertEqual([sample['quiet'] for sample in result['samples']],[False,True,True])

    def test_quiet_deadline_returns_failure_without_launching_anything(self):
        clock=[0.0]
        config={'enabled':True,'timeout_seconds':2,'interval_seconds':1,'consecutive_samples':2,
                'max_busy_cores':.1,'max_scientific_cores':.05}
        def sleep(seconds):clock[0]+=seconds
        with patch('quiet.time.monotonic',side_effect=lambda:clock[0]), patch('quiet.time.sleep',side_effect=sleep), \
             patch('quiet.cpu_snapshot',side_effect=[snapshot(0,0),snapshot(1,100),snapshot(2,200)]), \
             patch('quiet.scientific_snapshot',return_value={}):
            result=quiet.wait_for_quiet(config)
        self.assertFalse(result['ready']);self.assertTrue(result['timed_out'])
        self.assertEqual(result['wait_seconds'],2)


if __name__=='__main__':unittest.main()
