"""Reject inconsistent diagnostic versions before compiling a private release."""
from pathlib import Path
import tempfile
import unittest

from build import check_version_stamps


class BuildVersionTests(unittest.TestCase):
    def test_diagnostic_stamps_agree_while_alignment_stamp_stays_frozen(self):
        with tempfile.TemporaryDirectory() as temporary:
            core = Path(temporary)
            (core / 'mltaln.h').write_text('#define VERSION "7.526-opt8-dikarya4"\n'
                                         '#define ALIGNMENT_VERSION "7.526"\n')
            (core / 'mafft.tmpl').write_text('version="v7.526-opt8-dikarya4 (2024/Apr/26)"; export version\n')
            self.assertEqual(check_version_stamps(core)['native'], '7.526-opt8-dikarya4')
            (core / 'mafft.tmpl').write_text('version="v7.526-opt8-dikarya3 (2024/Apr/26)"; export version\n')
            with self.assertRaisesRegex(ValueError, 'must agree'):
                check_version_stamps(core)

    def test_missing_stamps_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            core = Path(temporary)
            (core / 'mltaln.h').write_text('#define ALIGNMENT_VERSION "7.526"\n')
            (core / 'mafft.tmpl').write_text('version="unrecognized"\n')
            with self.assertRaisesRegex(ValueError, 'must agree'):
                check_version_stamps(core)


if __name__ == '__main__':
    unittest.main()
