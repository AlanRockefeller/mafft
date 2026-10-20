"""Check reversible, quoted tree labels without running inference software."""
import unittest
from export_trees import readable_newick
from tree import IndexedTree, parse_newick


class TreeExportTests(unittest.TestCase):
    def test_complete_headers_and_unique_ids_preserve_supports_and_lengths(self):
        mapping = {'records': [{'id': f't{i:06d}', 'original_header': label} for i, label in enumerate(
                   ["same name: a,b[foo]", "same name: a,b[foo]", "voucher O'Brien", 'fungus α'], 1)]}
        text = '((t000001:0.001,t000002:0)99/100:0.002,(t000003:1,t000004:2)80/95:0.003);'
        result = readable_newick(text, mapping)
        self.assertIn("O''Brien", result)
        tree = IndexedTree(parse_newick(result))
        self.assertEqual(len(tree.taxa), 4)
        self.assertTrue(all(' | ' in label for label in tree.taxa))
        self.assertIn('99/100:0.002', result)

    def test_incomplete_taxon_sets_fail(self):
        mapping = {'records': [{'id': 't000001', 'original_header': 'one'},
                               {'id': 't000002', 'original_header': 'two'},
                               {'id': 't000003', 'original_header': 'three'}]}
        with self.assertRaisesRegex(ValueError, 'complete original taxon set'):
            readable_newick('(t000001,t000002);', mapping)


if __name__ == '__main__':
    unittest.main()
