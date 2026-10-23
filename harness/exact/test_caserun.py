#!/usr/bin/env python3
"""Resume, failure and timeout fixtures for the case runner and the round-3 scripts.

A fake MAFFT prefix (bin/mafft plus libexec/mafft/disttbfast) stands in for a
build.  Its behaviour is set through a mode file, so the binary hash does not
change between modes unless a test changes the libexec program on purpose.
"""
import contextlib
import io
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'round3'))
import caserun  # noqa: E402
import latency  # noqa: E402
import ordered_checks  # noqa: E402
import stock_replay  # noqa: E402

GOOD = '>a\nAC-GT\n>b\nACCGT\n'

FAKE = r'''#!/bin/bash
# Fake mafft: the last argument is the input; behaviour comes from $FAKE_MODE_FILE.
mode=$(cat "$FAKE_MODE_FILE")
echo run >> "$FAKE_COUNT_FILE"
case $mode in
  good)  printf '%s' "$FAKE_GOOD" ;;
  other) printf '>a\nACGT-\n>b\nACCGT\n' ;;
  exit3) printf '%s' "$FAKE_GOOD"; exit 3 ;;
  half)  printf '>a\nAC' ;;
  empty) ;;
  sleep) printf '>a\n'; sleep 300 & echo $! > "$FAKE_CHILD_PID"; wait ;;
  ignoreterm) trap '' TERM; printf '>a\n'; (trap '' TERM; exec sleep 300) & echo $! > "$FAKE_CHILD_PID"; wait ;;
esac
'''


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        self.prefix = self.root / 'prefix'
        (self.prefix / 'bin').mkdir(parents=True)
        (self.prefix / 'libexec' / 'mafft').mkdir(parents=True)
        self.mafft = self.prefix / 'bin' / 'mafft'
        self.mafft.write_text(FAKE)
        self.mafft.chmod(0o755)
        self.program('version 1')
        self.mode_file = self.root / 'mode'
        self.count_file = self.root / 'count'
        self.child_pid = self.root / 'child.pid'
        self.mode('good')
        self.data = self.root / 'data'
        self.golden = self.root / 'golden'
        self.data.mkdir()
        self.golden.mkdir()
        files = []
        for name in ('in1.fasta', 'in2.fasta'):
            (self.data / name).write_text('>a\nACGT\n>b\nACCGT\n')
            (self.golden / name).write_text(GOOD)
            files.append(dict(file=name, partition='development', sha256=caserun.file_digest(self.data / name),
                              production_options=['--localpair', '--maxiterate', '1000']))
        self.manifest = self.root / 'corpus.json'
        self.manifest.write_text(json.dumps(dict(files=files)))
        self.out = self.root / 'out'
        os.environ.update(FAKE_MODE_FILE=str(self.mode_file), FAKE_COUNT_FILE=str(self.count_file),
                          FAKE_CHILD_PID=str(self.child_pid), FAKE_GOOD=GOOD)

    def program(self, text):
        (self.prefix / 'libexec' / 'mafft' / 'disttbfast').write_text(text)

    def mode(self, mode):
        self.mode_file.write_text(mode)

    def runs(self):
        return len(self.count_file.read_text().split()) if self.count_file.exists() else 0

    def ordered(self, *extra):
        argv = ['ordered_checks.py', 'corpus', '--mafft', str(self.mafft), '--manifest', str(self.manifest),
                '--data', str(self.data), '--golden', str(self.golden), '--out', str(self.out), *extra]
        stdout = io.StringIO()
        with patch('sys.argv', argv), contextlib.redirect_stdout(stdout):
            status = ordered_checks.main()
        return status, stdout.getvalue(), json.loads((self.out / 'summary.json').read_text())


class OrderedChecksResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.f = Fixture(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_run_then_resume_rechecks_without_rerunning(self):
        status, _, summary = self.f.ordered()
        self.assertEqual((status, summary['total_failures'], self.f.runs()), (0, 0, 2))
        status, _, summary = self.f.ordered()
        self.assertEqual((status, summary['total_failures'], self.f.runs()), (0, 0, 2))
        self.assertEqual(summary['counts']['corpus t2']['from_manifest'], 2)

    def test_recorded_failure_still_counts_on_resume_and_after_retry(self):
        self.f.mode('other')
        status, _, summary = self.f.ordered()
        self.assertEqual((status, summary['total_failures']), (1, 2))
        self.f.mode('good')
        status, out, summary = self.f.ordered()
        self.assertEqual((status, summary['total_failures'], self.f.runs()), (1, 2, 2))
        self.assertIn('earlier attempt failed: output differs from expected', out)
        status, _, summary = self.f.ordered('--retry')
        self.assertEqual(self.f.runs(), 4)
        self.assertEqual((status, summary['total_failures']), (1, 2))

    def test_truncated_output_from_a_killed_run_is_not_skipped(self):
        # The old runner skipped any case whose output path existed.
        self.f.out.mkdir()
        (self.f.out / 'in1.fasta.t2.fa').write_text('>a\nAC')
        self.f.mode('half')
        status, _, summary = self.f.ordered()
        self.assertEqual(self.f.runs(), 2)
        self.assertEqual((status, summary['total_failures']), (1, 2))

    def test_output_truncated_after_a_recorded_pass_is_a_failure(self):
        self.f.ordered()
        (self.f.out / 'in1.fasta.t2.fa').write_text('>a\nAC')
        os.unlink(self.f.out / 'in2.fasta.t2.fa')
        status, out, summary = self.f.ordered()
        self.assertEqual((status, summary['total_failures'], self.f.runs()), (1, 2, 2))
        self.assertIn('cached output changed', out)
        self.assertIn('cached output is missing', out)

    def test_stale_output_from_a_different_binary_is_rerun(self):
        self.f.ordered()
        self.f.program('version 2')  # launcher unchanged; a libexec program was rebuilt
        self.f.mode('other')
        status, _, summary = self.f.ordered()
        self.assertEqual(self.f.runs(), 4)
        self.assertEqual((status, summary['total_failures']), (1, 2))

    def test_changed_golden_invalidates_a_cached_pass(self):
        self.f.ordered()
        (self.f.golden / 'in1.fasta').write_text(GOOD + '>c\nA\n')
        status, out, summary = self.f.ordered()
        self.assertEqual((status, summary['total_failures']), (1, 1))
        self.assertIn('expected hash changed', out)

    def test_nonzero_exit_with_complete_output_is_a_failure(self):
        self.f.mode('exit3')
        status, out, summary = self.f.ordered()
        self.assertEqual((status, summary['total_failures']), (1, 2))
        self.assertIn('exit status 3', out)
        self.assertFalse((self.f.out / 'in1.fasta.t2.fa').exists())
        self.assertEqual((self.f.out / 'in1.fasta.t2.fa.failed').read_text(), GOOD)
        status, _, summary = self.f.ordered()
        self.assertEqual((status, summary['total_failures'], self.f.runs()), (1, 2, 2))

    def test_empty_output_is_a_failure(self):
        self.f.mode('empty')
        status, _, summary = self.f.ordered()
        self.assertEqual((status, summary['total_failures']), (1, 2))

    def test_timeout_kills_the_process_group_and_counts(self):
        self.f.mode('sleep')
        started = time.monotonic()
        status, out, summary = self.f.ordered('--timeout', '1')
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual((status, summary['total_failures']), (1, 2))
        self.assertIn('timeout after 1s', out)
        child = int(self.f.child_pid.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)

    def test_manifest_record_cut_off_mid_write_is_ignored(self):
        self.f.ordered()
        path = self.f.out / 'manifest.jsonl'
        lines = path.read_text().splitlines()
        path.write_text(lines[0] + '\n' + lines[1][:40])
        status, _, summary = self.f.ordered()
        self.assertEqual((status, summary['total_failures'], self.f.runs()), (0, 0, 3))
        caserun.Manifest(path).close()  # the repaired file parses

    def test_corrupt_record_in_the_middle_stops_the_run(self):
        self.f.ordered()
        path = self.f.out / 'manifest.jsonl'
        lines = path.read_text().splitlines()
        path.write_text(lines[0][:40] + '\n' + lines[1] + '\n')
        with self.assertRaisesRegex(SystemExit, 'corrupt manifest'):
            self.f.ordered()


class RunCaseTests(unittest.TestCase):
    def test_term_ignoring_descendants_are_killed(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp)
            f.mode('ignoreterm')
            out = Path(tmp) / 'x.fa'
            result = caserun.run_case([str(f.mafft), 'in'], out, env=dict(os.environ), timeout=1, grace=1)
            self.assertTrue(result['timed_out'])
            self.assertFalse(out.exists())
            with self.assertRaises(ProcessLookupError):
                os.kill(int(f.child_pid.read_text()), 0)

    def test_launch_failure_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = caserun.run_case([str(Path(tmp) / 'missing')], Path(tmp) / 'x.fa', env={}, timeout=5)
            self.assertEqual(caserun.verdict(result, None), 'launch error')


class StockReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.f = Fixture(self.tmp.name)
        items = [dict(job='j%d' % i, input=str(self.f.data / n), sha256=caserun.file_digest(self.f.data / n),
                      opts=['--localpair']) for i, n in enumerate(('in1.fasta', 'in2.fasta'))]
        self.set = Path(self.tmp.name) / 'set.json'
        self.set.write_text(json.dumps(items))
        other = Fixture.__new__(Fixture)
        other.root = Path(self.tmp.name) / 'second'
        other.prefix = other.root / 'prefix'
        (other.prefix / 'bin').mkdir(parents=True)
        other.mafft = other.prefix / 'bin' / 'mafft'
        other.mafft.write_text(FAKE.replace('FAKE_MODE_FILE', 'FAKE_MODE2_FILE'))
        other.mafft.chmod(0o755)
        self.mode2 = other.root / 'mode2'
        self.mode2.write_text('good')
        os.environ['FAKE_MODE2_FILE'] = str(self.mode2)
        self.second = other.mafft

    def tearDown(self):
        self.tmp.cleanup()

    def replay(self, *extra):
        argv = ['stock_replay.py', 'replay', '--set', str(self.set), '--out', str(self.f.out), *extra,
                'stock=%s' % self.f.mafft, 'new=%s' % self.second]
        stdout = io.StringIO()
        with patch('sys.argv', argv), contextlib.redirect_stdout(stdout):
            status = stock_replay.main()
        return status, stdout.getvalue()

    def test_identical_builds_pass_and_resume(self):
        self.assertEqual(self.replay()[0], 0)
        self.assertEqual(self.f.runs(), 4)
        self.assertEqual(self.replay()[0], 0)
        self.assertEqual(self.f.runs(), 4)

    def test_nonzero_exit_is_not_renamed_into_place(self):
        self.mode2.write_text('exit3')
        status, out = self.replay()
        self.assertEqual(status, 1)
        self.assertIn('exit status 3', out)
        self.assertFalse((self.f.out / 'j0.new.fasta').exists())
        self.assertEqual(self.replay()[0], 1)

    def test_differing_bytes_fail(self):
        self.mode2.write_text('other')
        status, out = self.replay()
        self.assertEqual(status, 1)
        self.assertIn('differs from stock', out)

    def test_timeout_fails(self):
        self.mode2.write_text('sleep')
        status, out = self.replay('--timeout', '1')
        self.assertEqual(status, 1)
        self.assertIn('timeout', out)


class LatencyTests(unittest.TestCase):
    def test_timeout_and_mismatch_fail_but_config_b_may_differ(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp)
            inputs = Path(tmp) / 'inputs.json'
            inputs.write_text(json.dumps([dict(name='in1', path=str(f.data / 'in1.fasta'), options=[],
                                               golden=str(f.golden / 'in1.fasta'))]))
            argv = ['latency.py', '--mafft', str(f.mafft), '--inputs', str(inputs), '--out', str(f.out),
                    '--rounds', '1', '--timeout', '1']
            with patch('sys.argv', argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(latency.main(), 0)
            f.mode('sleep')
            with patch('sys.argv', argv + ['--rounds', '2']), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(latency.main(), 1)
            self.assertIn('total failures: 3', out.getvalue())  # A, F, O; B is out of scope


if __name__ == '__main__':
    unittest.main()
