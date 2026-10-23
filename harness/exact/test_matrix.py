#!/usr/bin/env python3
"""Comparison rules of matrix.py."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import matrix  # noqa: E402


def run(tmp, name, text, returncode=0, **extra):
    path = Path(tmp) / name
    path.write_bytes(text)
    import hashlib
    return dict(output=str(path), output_sha256=hashlib.sha256(text).hexdigest(), output_bytes=len(text),
                returncode=returncode, timed_out=False, timeout=5, launch_error=False,
                leaked_processes=False, invalid=None, **extra)


class CompareTests(unittest.TestCase):
    def test_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = run(tmp, 'a', b'>x\nAC\n')
            self.assertIsNone(matrix.compare(a, run(tmp, 'b', b'>x\nAC\n')))
            self.assertEqual(matrix.compare(a, run(tmp, 'c', b'>x\nA-\n')), 'stdout differs from stock')
            self.assertIsNone(matrix.compare(run(tmp, 'd', b'', 1), run(tmp, 'e', b'partial', 1)))
            self.assertEqual(matrix.compare(run(tmp, 'f', b'', 1), a), 'exit status 1, stock 0')
            self.assertIn('timed out', matrix.compare(dict(a, timed_out=True), a))
            self.assertIn('cached output', matrix.compare(dict(a, invalid='cached output is missing'), a))
            self.assertEqual(matrix.compare(run(tmp, 'g', b''), run(tmp, 'h', b'')), 'both outputs empty')

    def test_clustal_version_allowance_covers_line_one_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = b'\n\nx  AC\ny  A-\n'
            stock = run(tmp, 's', b'CLUSTAL format alignment by MAFFT L-INS-i (v7.526)\n' + body)
            ours = run(tmp, 'o', b'CLUSTAL format alignment by MAFFT L-INS-i (v7.526-opt8)\n' + body)
            other = run(tmp, 'x', b'CLUSTAL format alignment by MAFFT L-INS-i (v7.526-opt8)\n' + body + b'z  AC\n')
            self.assertEqual(matrix.compare(ours, stock), 'stdout differs from stock')
            self.assertIsNone(matrix.compare(ours, stock, allow_clustal_version=True))
            self.assertEqual(matrix.compare(other, stock, allow_clustal_version=True), 'stdout differs from stock')


if __name__ == '__main__':
    unittest.main()
