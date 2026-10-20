#!/usr/bin/env python3
"""Small mathematical correctness fixtures; no MAFFT or phylogeny runs."""
from __future__ import annotations

from collections import defaultdict
import itertools
import json
from pathlib import Path
import random
import tempfile
import unittest

from analysis import (compare_alignments, make_mapping, prepare, read_alignment,
                      reverse_complement, write_json)
from tree import (IndexedTree, compare_trees, compare_view, detect_support_order,
                  parse_newick)


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fasta(self, name, records):
        path = self.directory / name
        path.write_text("".join(f">{label}\n{seq}\n" for label, seq in records))
        return path

    def alignments(self, originals, reference, candidate):
        mapping = make_mapping(self.fasta("input.fa", originals))
        return (read_alignment(self.fasta("a.fa", reference), mapping),
                read_alignment(self.fasta("b.fa", candidate), mapping))

    def test_gap_column_shift_is_homologically_identical(self):
        a, b = self.alignments([("a", "AC"), ("b", "AC")],
                               [("a", "A-C"), ("b", "A-C")],
                               [("b", "AC--"), ("a", "AC--")])
        result = compare_alignments(a, b)
        self.assertFalse(result["byte_identical"])
        self.assertEqual(result["homology"]["f1"], 1)
        self.assertEqual(result["columns"]["reference_nonmatching"], 0)
        self.assertEqual(result["candidate"]["all_gap_columns"], 2)

    def test_known_homology_change(self):
        a, b = self.alignments([("a", "AC"), ("b", "AC")],
                               [("a", "AC-"), ("b", "AC-")],
                               [("a", "AC-"), ("b", "-AC")])
        result = compare_alignments(a, b)
        self.assertEqual(result["homology"]["reference_pairs"], 2)
        self.assertEqual(result["homology"]["candidate_pairs"], 1)
        self.assertEqual(result["homology"]["shared_pairs"], 0)
        self.assertEqual(result["homology"]["lost_pairs"], 2)
        self.assertEqual(result["homology"]["gained_pairs"], 1)

    def test_reverse_complement_original_coordinates(self):
        a, b = self.alignments([("a", "ACT"), ("b", "ACT")],
                               [("a", "ACT"), ("b", "ACT")],
                               [("_R_b", "AGT"), ("_R_a", "AGT")])
        result = compare_alignments(a, b)
        self.assertEqual(result["homology"]["f1"], 1)
        self.assertEqual(result["direction_changed_ids"], ["t000001", "t000002"])
        self.assertEqual(result["columns"]["shared_exact"], 3)

    def test_one_reversed_row_changes_homologies(self):
        a, b = self.alignments([("a", "ACT"), ("b", "ACT")],
                               [("a", "ACT"), ("b", "ACT")],
                               [("a", "ACT"), ("_R_b", "AGT")])
        result = compare_alignments(a, b)
        self.assertEqual(result["homology"]["shared_pairs"], 1)
        self.assertEqual(result["homology"]["reference_pairs"], 3)

    def test_identity_change_is_rejected(self):
        mapping = make_mapping(self.fasta("input.fa", [("a", "AC")]))
        with self.assertRaisesRegex(ValueError, "identity changed"):
            read_alignment(self.fasta("a.fa", [("a", "AT")]), mapping)

    def test_duplicate_name_and_content_requires_prelabeling(self):
        mapping = make_mapping(self.fasta("input.fa", [("same", "AC"), ("same", "AC")]))
        with self.assertRaisesRegex(ValueError, "ambiguous record"):
            read_alignment(self.fasta("a.fa", [("same", "AC"), ("same", "AC")]), mapping)
        named, mapfile = self.directory / "named.fa", self.directory / "map.json"
        prepare(self.directory / "input.fa", named, mapfile)
        aligned = read_alignment(named, mapping)
        self.assertEqual(aligned.ids, ["t000001", "t000002"])

    def test_duplicate_names_distinct_sequences_are_disambiguated(self):
        mapping = make_mapping(self.fasta("input.fa", [("same", "AC"), ("same", "AG")]))
        aligned = read_alignment(self.fasta("a.fa", [("same", "AG"), ("same", "AC")]), mapping)
        self.assertEqual(aligned.sequences, ["AC", "AG"])

    def test_ambiguity_filter(self):
        a, b = self.alignments([("a", "AN"), ("b", "AN")],
                               [("a", "AN"), ("b", "AN")],
                               [("a", "AN"), ("b", "AN")])
        result = compare_alignments(a, b)
        self.assertEqual(result["homology"]["shared_pairs"], 2)
        self.assertEqual(result["homology_unambiguous"]["shared_pairs"], 1)

    def test_contingency_matches_explicit_pairs_random_fixtures(self):
        rng = random.Random(1923)
        for iteration in range(30):
            originals = [(f"s{i}", "ACGTN") for i in range(5)]
            outputs = []
            for _ in range(2):
                rows = []
                for label, seq in originals:
                    columns = sorted(rng.sample(range(9), len(seq)))
                    row = ["-"] * 9
                    for column, char in zip(columns, seq):
                        row[column] = char
                    rows.append((label, "".join(row)))
                outputs.append(rows)
            # Each iteration gets unique names to avoid accidental result reuse.
            mapping = make_mapping(self.fasta(f"input{iteration}.fa", originals))
            a = read_alignment(self.fasta(f"a{iteration}.fa", outputs[0]), mapping)
            b = read_alignment(self.fasta(f"b{iteration}.fa", outputs[1]), mapping)
            def pairs(aln):
                columns = defaultdict(list)
                for taxon, row in enumerate(aln.residue_columns()):
                    for residue, column in enumerate(row):
                        columns[column].append((taxon, residue))
                return {pair for column in columns.values() for pair in itertools.combinations(column, 2)}
            x, y = pairs(a), pairs(b)
            result = compare_alignments(a, b)["homology"]
            self.assertEqual(result["reference_pairs"], len(x))
            self.assertEqual(result["candidate_pairs"], len(y))
            self.assertEqual(result["shared_pairs"], len(x & y))


class TreeTests(unittest.TestCase):
    def tree(self, text, order=None):
        return IndexedTree(parse_newick(text), support_order=order)

    def test_root_invariant_rf_and_quartets(self):
        a = self.tree("((a:1,b:1):2,(c:1,d:1):3);")
        b = self.tree("(a:1,b:1,(c:1,d:1):5);")
        result = compare_trees(a, b)
        self.assertEqual(result["views"]["raw"]["rf"], 0)
        self.assertEqual(result["views"]["raw"]["quartets"]["distance_fraction"], 0)
        self.assertEqual(list(a.splits().values())[0].length, 5)

    def test_incompatible_quartets(self):
        a, b = self.tree("((a,b),(c,d));"), self.tree("((a,c),(b,d));")
        result = compare_trees(a, b)["views"]["raw"]
        self.assertEqual(result["rf"], 2)
        self.assertEqual(result["normalized_rf"], 1)
        self.assertEqual(result["incompatible_split_pairs"], 1)
        self.assertEqual(result["quartets"]["distance_fraction"], 1)

    def test_polytomy_differs_only_in_resolution(self):
        a, b = self.tree("((a,b),(c,d));"), self.tree("(a,b,c,d);")
        result = compare_trees(a, b)["views"]["raw"]
        self.assertEqual(result["rf"], 1)
        self.assertEqual(result["incompatible_split_pairs"], 0)
        self.assertEqual(result["quartets"]["resolved_conflict"], 0)
        self.assertEqual(result["quartets"]["reference_only_resolved"], 1)

    def test_floor_length_edges_are_collapsed(self):
        a = self.tree("(a:1,b:1,(c:1,d:1):0.000001);")
        b = self.tree("(a:1,b:1,c:1,d:1);")
        result = compare_trees(a, b)
        self.assertEqual(result["views"]["raw"]["rf"], 1)
        self.assertEqual(result["views"]["zero_collapsed"]["rf"], 0)

    def test_support_filter_is_conjunction(self):
        a = self.tree("(a,b,(c,d)99/94:0.01);", ["shalrt", "ufboot"])
        b = self.tree("(a,b,(c,d)79/100:0.01);", ["shalrt", "ufboot"])
        result = compare_trees(a, b)["views"]["strong_support"]
        self.assertEqual(result["reference_splits"], 0)
        self.assertEqual(result["candidate_splits"], 0)

    def test_short_edges_collapse_before_pruned_paths_are_summed(self):
        a = self.tree("(((a:1,b:1):0.0000005,x:1):0.0000005,(c:1,d:1):0.0000005,y:1);")
        b = self.tree("(a:1,b:1,c:1,d:1,x:1,y:1);")
        selected = ["a", "b", "c", "d"]
        self.assertGreater(next(iter(a.splits(selected).values())).length, 1e-6)
        result = compare_view(a, b, selected, zero=True)
        self.assertEqual(result["rf"], 0)
        self.assertEqual(result["quartets"]["both_unresolved"], 1)

    def test_strong_projected_edge_survives_weak_neighboring_path_edges(self):
        a = self.tree("(((a:1,b:1)90/99:0.1,x:1)50/50:0.1,(c:1,d:1)60/60:0.1,y:1);",
                      ["shalrt", "ufboot"])
        b = self.tree("((a:1,c:1)95/99:0.1,b:1,d:1,x:1,y:1);", ["shalrt", "ufboot"])
        result = compare_view(a, b, ["a", "b", "c", "d"], support=True, zero=True)
        self.assertEqual(result["reference_splits"], 1)
        self.assertEqual(result["candidate_splits"], 1)
        self.assertEqual(result["rf"], 2)
        self.assertEqual(result["incompatible_split_pairs"], 1)
        self.assertEqual(result["quartets"]["resolved_conflict"], 1)

    def test_strong_support_conflicts(self):
        a = self.tree("(a,b,(c,d)80/95:0.01);", ["shalrt", "ufboot"])
        b = self.tree("(a,c,(b,d)100/100:0.01);", ["shalrt", "ufboot"])
        result = compare_trees(a, b)["views"]["strong_support"]
        self.assertEqual(result["incompatible_split_pairs"], 1)

    def test_no_support_assumption_without_report(self):
        a = self.tree("(a,b,(c,d)99/100:0.01);")
        result = compare_trees(a, a)
        self.assertFalse(result["support_available"])
        self.assertNotIn("strong_support", result["views"])

    def test_quoted_tip_labels(self):
        tree = self.tree("('a with space':1,'b''s tip':1,(c:1,d:1):1)[comment];")
        self.assertEqual(tree.taxa, ["a with space", "b's tip", "c", "d"])

    def test_duplicate_and_taxon_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate taxon"):
            self.tree("(a,a,b);")
        with self.assertRaisesRegex(ValueError, "taxon sets differ"):
            compare_trees(self.tree("(a,b,c);"), self.tree("(a,b,d);"))

    def test_pruning_restricts_splits(self):
        a = self.tree("((a,b),(c,(d,e)));")
        b = self.tree("((a,b),(d,(c,e)));")
        result = compare_view(a, b, ["a", "b", "c", "d"])
        self.assertEqual(result["rf"], 0)
        self.assertEqual(result["quartets"]["distance_fraction"], 0)

    def test_report_support_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inference.iqtree"
            path.write_text("Numbers in parentheses are SH-aLRT support (%) / ultrafast bootstrap support (%).\n")
            self.assertEqual(detect_support_order(path), ["shalrt", "ufboot"])

    def test_complete_comparison_serializes_as_strict_sorted_json(self):
        a, b = self.tree("((a,b),(c,d));"), self.tree("((a,c),(b,d));")
        result = compare_trees(a, b, details=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "comparison.json"
            write_json(path, result)
            self.assertEqual(json.loads(path.read_text())["views"]["raw"]["rf"], 2)
            with self.assertRaises(FileExistsError):
                write_json(path, result)

    def test_lca_quartets_match_inducing_splits_after_collapse_and_pruning(self):
        a = self.tree("((a:1,b:1):0.01,((c:1,d:1):0.000001,(e:1,(f:1,(g:1,h:1):0.2):0.1):0.000001):0.05);")
        for selected in [a.taxa, ["a", "c", "d", "f", "g", "h"]]:
            for threshold in [-1, .000001, .02]:
                retained = {mask for mask, edge in a.splits(selected).items()
                            if edge.length is None or edge.length > threshold}
                depths = a.weighted_depth(selected, retained)
                for quartet in itertools.combinations(selected, 4):
                    answers = set()
                    for mask in retained:
                        side = {i for i, taxon in enumerate(quartet)
                                if mask & (1 << a.position[taxon])}
                        if len(side) == 2:
                            if 0 not in side:
                                side = set(range(4)) - side
                            answers.add({frozenset([0, 1]): 0, frozenset([0, 2]): 1,
                                         frozenset([0, 3]): 2}[frozenset(side)])
                    self.assertLessEqual(len(answers), 1)
                    self.assertEqual(a.quartet(quartet, depths), next(iter(answers)) if answers else None)

    def test_nonfinite_result_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            with self.assertRaises(ValueError):
                write_json(path, {"invalid": float("nan")})
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
