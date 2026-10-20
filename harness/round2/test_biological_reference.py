import unittest

from biological_reference import resolve_accession


class AccessionResolutionTests(unittest.TestCase):
    def test_versionless_header_requires_one_archived_version(self):
        records = {"OR471183.1": object(), "NR_145334.1": object()}
        self.assertEqual(resolve_accession("OR471183 Sistotrema coronilla", records),
                         ("OR471183", "OR471183.1", None))
        self.assertEqual(resolve_accession("NR_145334.1_Burgoa", records),
                         ("NR_145334.1", "NR_145334.1", None))
        records["OR471183.2"] = object()
        token, resolved, error = resolve_accession("OR471183 Sistotrema", records)
        self.assertEqual(token, "OR471183")
        self.assertIsNone(resolved)
        self.assertIn("ambiguous", error)
        self.assertEqual(resolve_accession("OR471183.1_Sistotrema", records),
                         ("OR471183.1", "OR471183.1", None))

    def test_missing_version_and_partial_identifiers_do_not_fall_back(self):
        records = {"OR471183.1": object()}
        self.assertIsNone(resolve_accession("OR471183.2", records)[1])
        self.assertIsNone(resolve_accession("OR471184", records)[1])
        for header in ["local OR471183.1", "OR471183.1extra", "OR471183extra", "OR471183.12.3"]:
            self.assertIsNone(resolve_accession(header, records)[1])


if __name__ == "__main__":
    unittest.main()
