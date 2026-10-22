"""Process-group/RSS lifecycle and recorded-policy fixtures; no real jobs."""
from contextlib import contextmanager
import io
import signal
import subprocess
import threading
from types import SimpleNamespace
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


class ProcRssTests(unittest.TestCase):
    def setUp(self):
        self.reads=[]

    def sample(self, names, groups, pgids, files):
        @contextmanager
        def directories(path):
            self.assertEqual(path,'/proc')
            yield iter(SimpleNamespace(name=name,path='/proc/'+name) for name in names)
        def query(pid):
            result=pgids[pid]
            if isinstance(result,BaseException):raise result
            return result
        def read(path):
            self.reads.append(path)
            result=files[path]
            if isinstance(result,BaseException):raise result
            return io.StringIO(result)
        with patch('throughput.os.scandir',side_effect=directories), \
             patch('throughput.os.getpgid',side_effect=query) as getpgid, \
             patch('throughput.os.sysconf',return_value=4096), \
             patch('builtins.open',side_effect=read):
            total=throughput.proc_rss(groups)
        return total,getpgid.call_args_list

    @staticmethod
    def stat(pid,pgrp,comm='worker'):
        return f'{pid} ({comm}) S 1 {pgrp} {pgrp} 0 0 0\n'

    def test_members_sum_statm_and_foreign_processes_open_no_files(self):
        total,calls=self.sample(['10','20','30'],{100,200},
                                {10:100,20:999,30:200},
                                {'/proc/10/statm':'50 7 1\n','/proc/30/statm':'80 11 2\n'})
        self.assertEqual(total,(7+11)*4096)
        self.assertEqual(calls,[call(10),call(20),call(30)])
        self.assertEqual(self.reads,['/proc/10/statm','/proc/30/statm'])

    def test_nonnumeric_entries_are_ignored(self):
        total,calls=self.sample(['self','thread-self','net','23abc','23'],{100},
                                {23:999},{})
        self.assertEqual(total,0)
        self.assertEqual(calls,[call(23)])
        self.assertFalse(self.reads)

    def test_query_denial_falls_back_and_comm_parentheses_do_not_shift_fields(self):
        total,_=self.sample(['10'],{100},{10:PermissionError('denied')},
                            {'/proc/10/stat':self.stat(10,100,'worker ) ( name )'),
                             '/proc/10/statm':'80 9 1\n'})
        self.assertEqual(total,9*4096)
        self.assertEqual(self.reads,['/proc/10/stat','/proc/10/statm'])

    def test_foreign_fallback_does_not_read_statm(self):
        total,_=self.sample(['10'],{100},{10:PermissionError('denied')},
                            {'/proc/10/stat':self.stat(10,999)})
        self.assertEqual(total,0)
        self.assertEqual(self.reads,['/proc/10/stat'])

    def test_query_exit_race_may_find_a_replacement_in_fallback(self):
        total,_=self.sample(['10'],{100},{10:ProcessLookupError('exited')},
                            {'/proc/10/stat':self.stat(10,100),'/proc/10/statm':'80 4 1\n'})
        self.assertEqual(total,4*4096)

    def test_missing_denied_or_malformed_fallback_is_skipped(self):
        for value in [FileNotFoundError('exited'),PermissionError('denied'),
                      '10 (worker) S 1\n','10 (worker) S 1 bad 100\n']:
            with self.subTest(value=value):
                total,_=self.sample(['10'],{100},{10:ProcessLookupError('exited')},
                                    {'/proc/10/stat':value})
                self.assertEqual(total,0)

    def test_member_exit_or_malformed_statm_is_skipped(self):
        for value in [FileNotFoundError('exited'),PermissionError('denied'),'','80 bad 1\n']:
            with self.subTest(value=value):
                total,_=self.sample(['10'],{100},{10:100},{'/proc/10/statm':value})
                self.assertEqual(total,0)

    def test_membership_is_queried_again_after_pgrp_change_or_pid_reuse(self):
        # Identical directory names (and even identical reused inode numbers)
        # never authorize a cached nonmember classification.
        for pgrp,expected in [(999,0),(100,3*4096),(999,0),(100,3*4096)]:
            total,calls=self.sample(['10'],{100},{10:pgrp},{'/proc/10/statm':'80 3 1\n'})
            self.assertEqual(total,expected)
            self.assertEqual(calls,[call(10)])

    def test_newly_registered_group_is_visible_on_the_next_sample(self):
        total,_=self.sample(['10'],set(),{10:100},{})
        self.assertEqual(total,0)
        total,_=self.sample(['10'],{100},{10:100},{'/proc/10/statm':'80 6 1\n'})
        self.assertEqual(total,6*4096)

    def test_proc_enumeration_failure_is_not_silently_hidden(self):
        with patch('throughput.os.scandir',side_effect=OSError('proc unavailable')):
            with self.assertRaisesRegex(OSError,'proc unavailable'):throughput.proc_rss({100})


if __name__=='__main__':unittest.main()
