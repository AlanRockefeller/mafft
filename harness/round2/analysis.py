#!/usr/bin/env python3
"""Exact, residue-indexed alignment comparisons for MAFFT threading experiments.

The core implementation uses only Python's standard library.  It never creates
the quadratic set of all aligned residue pairs: shared pairs are counted from
the contingency table of reference and candidate columns for each residue.
"""
from __future__ import annotations

import argparse
from array import array
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable

GAPS = frozenset("-.")
BASES = frozenset("ACGT")
COMPLEMENT = str.maketrans("ACGTURYSWKMBDHVNX?", "TGCAAYRSWMKVHDBNX?")
SCHEMA = "mafft-round2-taxa-v1"


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for data in iter(lambda: f.read(1 << 20), b""):
            h.update(data)
    return h.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_json(path: str | Path, value: Any) -> None:
    """Refuse to replace previous results, including incomplete result files."""
    encoded = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with open(path, "x", encoding="utf-8") as f:
        f.write(encoded)


def read_fasta(path: str | Path) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    label: str | None = None
    sequence: list[str] = []
    with open(path, encoding="utf-8-sig") as f:
        for number, line in enumerate(f, 1):
            line = line.rstrip("\r\n")
            if line.startswith(">"):
                if label is not None:
                    records.append((label, "".join(sequence)))
                label, sequence = line[1:], []
                if not label.strip():
                    raise ValueError(f"{path}:{number}: empty FASTA header")
            elif line.strip():
                if label is None:
                    raise ValueError(f"{path}:{number}: sequence precedes first header")
                sequence.append("".join(line.split()))
    if label is not None:
        records.append((label, "".join(sequence)))
    if not records:
        raise ValueError(f"{path}: no FASTA records")
    if any(not seq for _, seq in records):
        raise ValueError(f"{path}: empty FASTA record")
    return records


def ungap(sequence: str) -> str:
    return "".join(x for x in sequence.upper() if x not in GAPS)


def reverse_complement(sequence: str) -> str:
    invalid = set(sequence.upper()) - set("ACGTURYSWKMBDHVNX?")
    if invalid:
        raise ValueError(f"Cannot reverse-complement symbols: {sorted(invalid)}")
    return sequence.upper().translate(COMPLEMENT)[::-1]


def make_mapping(path: str | Path) -> dict[str, Any]:
    records = read_fasta(path)
    groups: dict[str, list[str]] = defaultdict(list)
    result = []
    for index, (label, raw) in enumerate(records, 1):
        sequence = ungap(raw)
        if not sequence:
            raise ValueError(f"Input record {index} has no residues")
        identifier = f"t{index:06d}"
        digest = sha256_text(sequence)
        groups[digest].append(identifier)
        result.append({"id": identifier, "ordinal": index,
                       "original_header": label, "sequence": sequence,
                       "sequence_sha256": digest, "length": len(sequence),
                       "input_gap_characters": sum(x in GAPS for x in raw)})
    duplicates = [v for v in groups.values() if len(v) > 1]
    return {"schema": SCHEMA, "input": str(Path(path).resolve()),
            "input_sha256": sha256_file(path), "records": result,
            "duplicate_groups": duplicates,
            "duplicate_rows": sum(len(v) - 1 for v in duplicates),
            "duplicate_headers": len(records) - len(set(label for label, _ in records)),
            "identity_rules": "Original record ordinal; case-insensitive residues; '-' and '.' are gaps; no T/U substitution."}


def load_mapping(path: str | Path | None, input_path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        if input_path is None:
            raise ValueError("An input FASTA or taxa mapping is required")
        return make_mapping(input_path)
    result = json.loads(Path(path).read_text())
    if result.get("schema") != SCHEMA:
        raise ValueError(f"Unsupported taxa mapping schema in {path}")
    ids = [r["id"] for r in result["records"]]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate stable identifiers in {path}")
    for record in result["records"]:
        if sha256_text(record["sequence"]) != record["sequence_sha256"]:
            raise ValueError(f"Corrupt residue identity for {record['id']} in {path}")
    if input_path is not None and sha256_file(input_path) != result["input_sha256"]:
        raise ValueError("Input FASTA does not match the mapping's SHA-256")
    return result


@dataclass
class Alignment:
    path: str
    mapping: dict[str, Any]
    sequences: list[str]  # Stable original-record order, regardless of output order.
    reverse: list[bool]
    headers: list[str]
    input_order: list[int]

    @property
    def width(self) -> int:
        return len(self.sequences[0])

    @property
    def ids(self) -> list[str]:
        return [r["id"] for r in self.mapping["records"]]

    def residue_columns(self) -> list[array]:
        """Column of each residue in ORIGINAL forward-strand coordinate order."""
        result = []
        for seq, rev, record in zip(self.sequences, self.reverse, self.mapping["records"]):
            row = array("i", [0]) * record["length"]
            k = 0
            for column, char in enumerate(seq):
                if char not in GAPS:
                    row[len(row) - 1 - k if rev else k] = column
                    k += 1
            result.append(row)
        return result


def read_alignment(path: str | Path, mapping: dict[str, Any]) -> Alignment:
    """Validate both record identity and strand; never match taxa by row order.

    Original labels may be repeated only if residue content disambiguates them.
    Ambiguous repeated label+sequence combinations require stable IDs prepared
    BEFORE alignment, because no post-hoc rule can recover their identities.
    """
    records = mapping["records"]
    stable: dict[str, set[int]] = defaultdict(set)
    full: dict[str, set[int]] = defaultdict(set)
    tokens: dict[str, set[int]] = defaultdict(set)
    for i, record in enumerate(records):
        stable[record["id"]].add(i)
        full[record["original_header"]].add(i)
        tokens[record["original_header"].split()[0]].add(i)
    sequences: list[str | None] = [None] * len(records)
    reverse: list[bool] = [False] * len(records)
    headers: list[str] = [""] * len(records)
    order = []
    widths: set[int] = set()
    output_records = read_fasta(path)
    if len(output_records) != len(records):
        raise ValueError(f"{path}: {len(output_records)} records; expected {len(records)}")
    for label, seq in output_records:
        seq = seq.upper()
        widths.add(len(seq))
        raw = ungap(seq)
        candidates: dict[int, set[bool]] = defaultdict(set)
        variants = [(label, False)]
        if label.startswith("_R_"):
            variants.append((label[3:], True))
        for variant, marker in variants:
            token = variant.split()[0]
            exact = stable.get(token, set()) | full.get(variant, set())
            # Token-only fallback is allowed only when it uniquely identifies a record.
            if not exact and len(tokens.get(token, set())) == 1:
                exact = tokens[token]
            for index in exact:
                original = records[index]["sequence"]
                forward_match = raw == original
                try:
                    reverse_match = raw == reverse_complement(original)
                except ValueError:
                    reverse_match = False
                if forward_match and reverse_match:
                    candidates[index].add(marker)  # Palindromic: honor explicit direction marker.
                elif forward_match:
                    candidates[index].add(False)
                elif reverse_match:
                    candidates[index].add(True)
        if len(candidates) != 1:
            if not candidates:
                raise ValueError(f"{path}: cannot match label/residues of {label!r}; sequence identity changed or label is unknown")
            raise ValueError(f"{path}: ambiguous record identity for {label!r}; prepare stable ordinal IDs before alignment")
        index, directions = next(iter(candidates.items()))
        if len(directions) != 1:
            raise ValueError(f"{path}: ambiguous strand identity for {label!r}")
        if sequences[index] is not None:
            raise ValueError(f"{path}: repeated output identity {records[index]['id']}")
        sequences[index], reverse[index], headers[index] = seq, directions.pop(), label
        order.append(index)
    if len(widths) != 1:
        raise ValueError(f"{path}: unequal alignment row widths {sorted(widths)}")
    if any(row is None for row in sequences):
        raise ValueError(f"{path}: missing record identities")
    return Alignment(str(Path(path).resolve()), mapping, list(sequences), reverse, headers, order)  # type: ignore[arg-type]


def write_named_alignment(alignment: Alignment, path: str | Path) -> None:
    """IQ-TREE identity is fixed to original ordinal, including reversed rows."""
    with open(path, "x", encoding="utf-8") as f:
        for identifier, sequence in zip(alignment.ids, alignment.sequences):
            f.write(f">{identifier}\n{sequence}\n")


def descriptive(values: Iterable[int | float]) -> dict[str, Any]:
    values = sorted(values)
    if not values:
        return {"n": 0}
    def quantile(p: float) -> float:
        position = p * (len(values) - 1)
        low = int(position)
        return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (position - low)
    return {"n": len(values), "min": values[0], "max": values[-1],
            "mean": statistics.fmean(values), "median": statistics.median(values),
            "p05": quantile(.05), "p95": quantile(.95)}


def alignment_summary(alignment: Alignment) -> dict[str, Any]:
    seqs, width = alignment.sequences, alignment.width
    occupancy = [0] * width
    canonical = [0] * width
    states = [Counter() for _ in range(width)]
    rows = []
    for identifier, seq in zip(alignment.ids, seqs):
        gaps = sum(x in GAPS for x in seq)
        leading = len(seq) - len(seq.lstrip("-."))
        trailing = len(seq) - len(seq.rstrip("-."))
        runs = sum(x in GAPS and (j == 0 or seq[j - 1] not in GAPS) for j, x in enumerate(seq))
        rows.append({"id": identifier, "gap_characters": gaps,
                     "gap_runs": runs, "leading_gaps": leading,
                     "trailing_gaps": trailing, "internal_gaps": gaps - leading - trailing})
        for j, char in enumerate(seq):
            if char not in GAPS:
                occupancy[j] += 1
            if char in BASES:
                canonical[j] += 1
                states[j][char] += 1
    gap_characters = sum(row["gap_characters"] for row in rows)
    return {"path": alignment.path, "sha256": sha256_file(alignment.path),
            "taxa": len(seqs), "width": width,
            "gap_characters": gap_characters,
            "gap_fraction": gap_characters / (width * len(seqs)),
            "gap_runs": sum(row["gap_runs"] for row in rows),
            "all_gap_columns": occupancy.count(0),
            "singleton_residue_columns": occupancy.count(1),
            "columns_with_two_or_more_residues": sum(x >= 2 for x in occupancy),
            "columns_with_two_or_more_unambiguous_bases": sum(x >= 2 for x in canonical),
            "variable_unambiguous_columns": sum(len(x) > 1 for x in states),
            "parsimony_informative_unambiguous_columns": sum(sum(count >= 2 for count in x.values()) >= 2 for x in states),
            "column_gap_count_histogram": dict(sorted(Counter(len(seqs) - x for x in occupancy).items())),
            "row_gap_count_distribution": descriptive(row["gap_characters"] for row in rows),
            "rows": rows,
            "reverse_complement_ids": [i for i, rev in zip(alignment.ids, alignment.reverse) if rev],
            "output_record_order": [alignment.ids[i] for i in alignment.input_order],
            "duplicate_input_rows": alignment.mapping["duplicate_rows"]}


def choose2(n: int) -> int:
    return n * (n - 1) // 2


def homology_metrics(reference: Alignment, candidate: Alignment, *, canonical_only: bool = False,
                     selected: list[int] | None = None) -> dict[str, Any]:
    """Exact sum-of-pairs overlap in O(residues) time/memory, not O(taxa²).

    A homology assertion is an unordered pair of (original_record, original
    forward-strand_residue_index) identities assigned to the same column.
    Shared assertions are choose2(contingency[reference_column,candidate_column]).
    """
    if reference.mapping["input_sha256"] != candidate.mapping["input_sha256"]:
        raise ValueError("Cannot compare alignments from different original input bytes")
    refcols, candcols = reference.residue_columns(), candidate.residue_columns()
    rn, cn = [0] * reference.width, [0] * candidate.width
    contingency: dict[tuple[int, int], int] = defaultdict(int)
    selected = list(range(len(refcols))) if selected is None else selected
    for i in selected:
        original = reference.mapping["records"][i]["sequence"]
        for p, (r, c) in enumerate(zip(refcols[i], candcols[i])):
            if canonical_only and original[p] not in BASES:
                continue
            rn[r] += 1
            cn[c] += 1
            contingency[r, c] += 1
    refpairs, candpairs = sum(map(choose2, rn)), sum(map(choose2, cn))
    shared = sum(map(choose2, contingency.values()))
    precision = shared / candpairs if candpairs else float(refpairs == 0)
    recall = shared / refpairs if refpairs else float(candpairs == 0)
    f1 = 2 * shared / (refpairs + candpairs) if refpairs + candpairs else 1.0
    by_taxon = []
    for i in selected:
        original = reference.mapping["records"][i]["sequence"]
        lost = gained = changed = eligible = 0
        for p, (r, c) in enumerate(zip(refcols[i], candcols[i])):
            if canonical_only and original[p] not in BASES:
                continue
            eligible += 1
            common = contingency[r, c]
            loss, gain = rn[r] - common, cn[c] - common
            lost += loss
            gained += gain
            changed += bool(loss or gain)
        by_taxon.append({"id": reference.ids[i], "eligible_residues": eligible,
                         "residues_with_changed_homologies": changed,
                         "lost_partner_relationships": lost, "gained_partner_relationships": gained})
    return {"residue_filter": "ACGT only" if canonical_only else "all non-gap residues including ambiguity codes",
            "selected_taxa": len(selected), "reference_pairs": refpairs,
            "candidate_pairs": candpairs, "shared_pairs": shared,
            "lost_pairs": refpairs - shared, "gained_pairs": candpairs - shared,
            "precision": precision, "recall": recall, "f1": f1,
            "jaccard": shared / (refpairs + candpairs - shared) if refpairs + candpairs - shared else 1.0,
            "symmetric_pair_difference_fraction": 1.0 - f1,
            "residues_with_changed_homologies": sum(x["residues_with_changed_homologies"] for x in by_taxon),
            "by_taxon": by_taxon}


def column_signatures(alignment: Alignment) -> Counter[tuple[tuple[int, int], ...]]:
    columns: list[list[tuple[int, int]]] = [[] for _ in range(alignment.width)]
    for i, row in enumerate(alignment.residue_columns()):
        for residue, column in enumerate(row):
            columns[column].append((i, residue))
    return Counter(tuple(column) for column in columns if column)


def compare_alignments(reference: Alignment, candidate: Alignment, *, duplicate_sensitivity: bool = False) -> dict[str, Any]:
    refsig, candsig = column_signatures(reference), column_signatures(candidate)
    common = refsig & candsig
    ref_multi = Counter({k: v for k, v in refsig.items() if len(k) >= 2})
    cand_multi = Counter({k: v for k, v in candsig.items() if len(k) >= 2})
    common_multi = ref_multi & cand_multi
    result = {"schema": "mafft-round2-alignment-comparison-v1",
              "input_sha256": reference.mapping["input_sha256"],
              "reference": alignment_summary(reference), "candidate": alignment_summary(candidate),
              "byte_identical": sha256_file(reference.path) == sha256_file(candidate.path),
              "same_rows_and_strands_after_identity_mapping": reference.sequences == candidate.sequences and reference.reverse == candidate.reverse,
              "direction_changed_ids": [identifier for identifier, r, c in zip(reference.ids, reference.reverse, candidate.reverse) if r != c],
              "columns": {"definition": "Multiset overlap of nonempty column signatures, each containing (original_record_ordinal, original_forward_residue_index). Column order and all-gap columns do not contribute.",
                          "reference_nonempty": sum(refsig.values()), "candidate_nonempty": sum(candsig.values()),
                          "shared_exact": sum(common.values()),
                          "reference_nonmatching": sum(refsig.values()) - sum(common.values()),
                          "candidate_nonmatching": sum(candsig.values()) - sum(common.values()),
                          "reference_homology_columns": sum(ref_multi.values()),
                          "candidate_homology_columns": sum(cand_multi.values()),
                          "shared_exact_homology_columns": sum(common_multi.values()),
                          "reference_nonmatching_homology_columns": sum(ref_multi.values()) - sum(common_multi.values()),
                          "candidate_nonmatching_homology_columns": sum(cand_multi.values()) - sum(common_multi.values())},
              "homology": homology_metrics(reference, candidate),
              "homology_unambiguous": homology_metrics(reference, candidate, canonical_only=True)}
    if duplicate_sensitivity:
        seen: set[str] = set()
        selected = []
        for i, record in enumerate(reference.mapping["records"]):
            if record["sequence_sha256"] not in seen:
                selected.append(i)
                seen.add(record["sequence_sha256"])
        result["homology_input_duplicate_representatives"] = homology_metrics(reference, candidate, selected=selected)
    return result


def prepare(input_path: str | Path, out: str | Path, mapping_path: str | Path) -> dict[str, Any]:
    mapping = make_mapping(input_path)
    # Keep any input gaps: identifier preparation changes names only.
    with open(out, "x", encoding="utf-8") as f:
        for record, (_, sequence) in zip(mapping["records"], read_fasta(input_path)):
            f.write(f">{record['id']}\n{sequence}\n")
    write_json(mapping_path, mapping)
    return {"input_sha256": mapping["input_sha256"], "taxa": len(mapping["records"]),
            "fasta": str(Path(out).resolve()), "mapping": str(Path(mapping_path).resolve())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare", help="Assign stable identifiers before alignment, preserving input gaps")
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--mapping", required=True)
    p = sub.add_parser("compare", help="Compare two aligned FASTA files against original sequence identities")
    p.add_argument("--input")
    p.add_argument("--mapping")
    p.add_argument("--reference", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--json", required=True)
    p.add_argument("--duplicate-sensitivity", action="store_true")
    p = sub.add_parser("summarize")
    p.add_argument("--input")
    p.add_argument("--mapping")
    p.add_argument("--alignment", required=True)
    p.add_argument("--json", required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare(args.input, args.out, args.mapping)
    else:
        mapping = load_mapping(args.mapping, args.input)
        if args.action == "compare":
            result = compare_alignments(read_alignment(args.reference, mapping), read_alignment(args.candidate, mapping),
                                        duplicate_sensitivity=args.duplicate_sensitivity)
        else:
            result = alignment_summary(read_alignment(args.alignment, mapping))
        write_json(args.json, result)
        result = {"output": str(Path(args.json).resolve())}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        sys.exit(str(exc))
