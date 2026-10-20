"""Process-group/RSS lifecycle and recorded-policy fixtures; no real jobs."""
import io
import signal
import subprocess
import threading
import unittest
from unittest.mock import Mock, call, patch

import throughput


class ThroughputExecutionTests(unittest.TestCase):
    def invoke(self, process, groups, cancelled=None):
        return throughput.run_tracked_process(
            ['fixture'], stdout=io.BytesIO(), stderr=io.BytesIO(), env={}, timeout=.01,
            groups=groups, lock=threading.Lock(), cancelled=cancelled or threading.Event())

    def test_timeout_tracks_group_through_term_and_final_kill(self):
        process=Mock(pid=12345)
        process.wait.side_effect=[subprocess.TimeoutExpired(['fixture'],.01),-15,-15]
        groups=set()
        def signal_group(pid, _signal):
            self.assertIn(pid,groups)
        with patch('throughput.subprocess.Popen',return_value=process) as popen, patch('throughput.os.killpg',side_effect=signal_group) as kill:
            result=self.invoke(process,groups)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        self.assertTrue(result['timed_out'])
        self.assertEqual(kill.call_args_list,[call(12345,signal.SIGTERM),call(12345,signal.SIGKILL)])
        self.assertFalse(groups)

    def test_keyboard_interrupt_kills_and_unregisters_group(self):
        process=Mock(pid=12345)
        process.wait.side_effect=[KeyboardInterrupt(),-9]
        groups=set()
        with patch('throughput.subprocess.Popen',return_value=process), patch('throughput.os.killpg') as kill:
            with self.assertRaises(KeyboardInterrupt):self.invoke(process,groups)
        kill.assert_called_once_with(12345,signal.SIGKILL)
        self.assertFalse(groups)

    def test_cancellation_before_wait_cleans_a_racing_launch(self):
        process=Mock(pid=12345)
        process.wait.return_value=-9
        cancelled=threading.Event();cancelled.set();groups=set()
        with patch('throughput.subprocess.Popen',return_value=process), patch('throughput.os.killpg') as kill:
            with self.assertRaisesRegex(RuntimeError,'cancelled'):self.invoke(process,groups,cancelled)
        kill.assert_called_once_with(12345,signal.SIGKILL)
        self.assertFalse(groups)

    def test_success_does_not_signal(self):
        process=Mock(pid=12345)
        process.wait.return_value=0;groups=set()
        with patch('throughput.subprocess.Popen',return_value=process), patch('throughput.os.killpg') as kill:
            result=self.invoke(process,groups)
        kill.assert_not_called();self.assertEqual(result['returncode'],0);self.assertFalse(groups)

    def test_production_policy_uses_each_recorded_thread_count(self):
        self.assertEqual(throughput.job_config('production',{'production_threads':1}),'A')
        self.assertEqual(throughput.job_config('production',{'production_threads':2}),'B')
        self.assertEqual(throughput.job_config('C',{'production_threads':1}),'C')
        with self.assertRaises(ValueError):throughput.job_config('production',{'production_threads':0})


if __name__=='__main__':unittest.main()
