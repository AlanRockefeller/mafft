#!/usr/bin/env python3
"""Audit exact voucher links to cached published non-ITS Cantharellales trees.

No sequence alignment, phylogenetic inference, approximate name matching, or
nominal-species monophyly scoring occurs here. Input sources are cached outside
Git with their provenance. A few explicit links are reviewed below; every link
must pass both a GenBank residue-identity check and an exact voucher check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from analysis import make_mapping, read_fasta, reverse_complement, ungap, write_json
from tree import read_tree

LINKS = {
    "OR471183.1": ("Sistotrema_coronilla_RAS538", "isolate", "RAS538 SV2", "RAS538; SV2 is a genotype suffix within that isolate"),
    "OR471177.1": ("Sistotrema_coronilla_HHB10070", "isolate", "HHB10070", "exact isolate identifier"),
    "OR464478.1": ("Burgellopsis_nivea_ATCCMYA4209", "culture_collection", "ATCC:MYA-4209", "exact culture collection identifier"),
    "OR471312.1": ("Ceratobasidium_bulbillifaciens_CBS132236", "culture_collection", "CBS:132236", "exact culture collection identifier"),
    "DQ267125.1": ("Sistotrema_confluens_AFTOL_613", "isolate", "AFTOL-ID 613", "exact AFTOL identifier"),
    "DQ218305.1": ("Hydnum_albomagnum_AFTOL_471", "isolate", "AFTOL-ID 471", "exact AFTOL identifier"),
}


def normalize_identifier(value):
    # Two documented conventions, not fuzzy name matching.
    value = re.sub(r" SV[0-9]+$", "", value.upper())
    value = value.replace("AFTOL-ID", "AFTOL")
    return re.sub(r"[^A-Z0-9]", "", value)


def source_qualifiers(record):
    result = {}
    for feature in record.findall(".//GBFeature"):
        if feature.findtext("GBFeature_key") == "source":
            for q in feature.findall(".//GBQualifier"):
                result.setdefault(q.findtext("GBQualifier_name"), []).append(q.findtext("GBQualifier_value"))
    return result


def sequence_identity(sequence, accession_sequence):
    rev = reverse_complement(sequence)
    if sequence == accession_sequence:
        return "exact forward"
    if rev == accession_sequence:
        return "exact reverse complement"
    if len(sequence) >= 200 and sequence in accession_sequence:
        return "input is exact contiguous substring of accession, forward"
    if len(sequence) >= 200 and rev in accession_sequence:
        return "input is exact contiguous substring of accession, reverse complement"
    if len(accession_sequence) >= 200 and accession_sequence in sequence:
        return "accession is exact contiguous substring of input, forward"
    return "NO EXACT IDENTITY"


def audit(data, references, output, datasets, trees_root=None, configs=("A", "B", "C"), seed=73111):
    output.mkdir(parents=True, exist_ok=False)
    metadata = json.loads((references / "figshare-25691193-metadata.json").read_text())
    for source in metadata["files"]:
        path = references / source["name"]
        if path.exists() and hashlib.md5(path.read_bytes()).hexdigest() != source["computed_md5"]:
            raise ValueError(f"Published source MD5 mismatch: {path}")
    xml = ET.parse(references / "production-pilot-genbank.xml").getroot()
    records = {r.findtext("GBSeq_accession-version"): r for r in xml}
    its = {name: ungap(seq) for name, seq in read_fasta(references / "Cantharellales_ITS.phy")}
    trees = {locus: read_tree(references / f"RAxML_bipartitions.CANTH_{locus}.tre", support_order=["bootstrap"])
             for locus in ("rpb2", "tef1")}
    allrows, matched, originalmaps = [], {}, {}
    for dataset in datasets:
        original = make_mapping(data / f"{dataset}.fasta")
        originalmaps[dataset] = original
        selected = []
        for row in original["records"]:
            match = re.match(r"([A-Z]{1,3}_[0-9]+\.[0-9]+|[A-Z]{1,3}[0-9]{5,}\.[0-9]+)", row["original_header"])
            record = {"dataset": f"{dataset}.fasta", "input_sha256": original["input_sha256"],
                      "id": row["id"], "original_header": row["original_header"],
                      "accession": match[1] if match else None, "accepted_reference_tip": None,
                      "reference_loci": []}
            if not match:
                record["unmatched_reason"] = "No accession in original header; no independently verified voucher link"
                allrows.append(record)
                continue
            acc = match[1]
            gb = records[acc]
            qualifiers = source_qualifiers(gb)
            identity = sequence_identity(row["sequence"], gb.findtext("GBSeq_sequence").upper())
            record.update(genbank_organism=gb.findtext("GBSeq_organism"), source_qualifiers=qualifiers,
                          sequence_identity=identity)
            if acc in LINKS:
                tip, key, value, reason = LINKS[acc]
                if value not in qualifiers.get(key, []):
                    raise ValueError(f"Reviewed voucher qualifier changed: {acc}")
                if not normalize_identifier(tip).endswith(normalize_identifier(value)):
                    raise ValueError(f"Reviewed source voucher no longer matches tip: {acc}")
                if identity == "NO EXACT IDENTITY":
                    raise ValueError(f"Input residues do not validate accession identity: {acc}")
                if value.endswith(" SV2") and "genotype: SV2" not in qualifiers.get("note", []):
                    raise ValueError("Genotype-suffix interpretation lacks GenBank evidence")
                aseq = its.get(tip)
                archived_identity = sequence_identity(row["sequence"], aseq) if aseq else "tip absent from archived ITS; direct voucher link remains the evidence"
                record.update(accepted_reference_tip=tip, link_qualifier=key, link_value=value,
                              link_reason=reason, archived_ITS_identity=archived_identity,
                              reference_loci=[l for l, t in trees.items() if tip in t.taxa])
                selected.append(record)
            else:
                record["unmatched_reason"] = "No reviewed exact accession/voucher link to a non-ITS source tip; names/ITS similarity alone are insufficient"
            allrows.append(record)
        matched[dataset] = selected
    summary = {}
    for dataset, selected in matched.items():
        summary[dataset] = {}
        for locus, tree in trees.items():
            tips = sorted(r["accepted_reference_tip"] for r in selected if r["accepted_reference_tip"] in tree.taxa)
            info = {"matched_tips": tips, "matched_count": len(tips), "splits": [],
                    "bootstrap_rule": "A source full-tree branch with classical bootstrap >=95 implying the induced split is a conservative witness; no SH-aLRT/UFBoot equivalence is assumed."}
            if len(tips) >= 4:
                universe = tree.selected_mask(tips)
                for mask in tree.splits(tips):
                    witnesses = []
                    for i, node in enumerate(tree.nodes[1:], 1):
                        if tree.canonical(tree.masks[i], universe) == mask:
                            try:
                                bs = float(node.label)
                            except ValueError:
                                bs = None
                            witnesses.append({"source_node_label": node.label, "classical_bootstrap": bs,
                                              "edge_length": node.length})
                    side = tree.side_taxa(mask)
                    info["splits"].append({"side": side, "other_side": [x for x in tips if x not in side],
                                           "source_edge_witnesses": witnesses,
                                           "has_source_bootstrap_ge95": any(w["classical_bootstrap"] is not None and w["classical_bootstrap"] >= 95 for w in witnesses)})
                if trees_root is not None and len(tips) == 4:
                    # Fixed original ordinal order defines a common quartet orientation.
                    linked = sorted((r for r in selected if r["accepted_reference_tip"] in tips), key=lambda r: r["id"])
                    identifiers = tuple(r["id"] for r in linked)
                    source_tips = tuple(r["accepted_reference_tip"] for r in linked)
                    reference_resolution = tree.quartet(source_tips, tree.weighted_depth(tips, set(tree.splits(tips))))
                    info["quartet_original_ids"] = identifiers
                    info["reference_quartet_resolution"] = reference_resolution
                    info["production_tree_congruence"] = []
                    for config in configs:
                        path = trees_root / f"{dataset}.fasta" / config / f"seed{seed}" / "inference.treefile"
                        if not path.exists():
                            continue
                        candidate = read_tree(path)
                        resolution = candidate.quartet(identifiers, candidate.weighted_depth(identifiers, set(candidate.splits(identifiers))))
                        info["production_tree_congruence"].append({"config": config, "seed": seed,
                                                                  "tree": str(path.resolve()),
                                                                  "quartet_resolution": resolution,
                                                                  "agrees_with_nonITS_quartet": resolution == reference_resolution})
            else:
                info["limitation"] = "Fewer than four exact specimen matches; no informative unrooted quartet comparison"
            summary[dataset][locus] = info
    write_json(output / "matched-records.json", allrows)
    write_json(output / "matched-nonITS-splits.json", summary)
    write_json(output / "provenance.json", {
        "publication": "https://doi.org/10.1002/ajb2.70054",
        "archive": "https://doi.org/10.6084/m9.figshare.25691193",
        "source_downloads": json.loads((references / "download-provenance.json").read_text()),
        "source_directory": str(references.resolve()), "datasets": datasets,
        "classical_bootstrap_threshold": 95,
        "excluded_near_matches": [
            {"accessions": ["NR_145334.1", "AB972781.1"], "reason": "CBS131.38 type culture differs from reference Burgoa_verzuoliana_CBS32075"},
            {"accessions": ["OR464476.1"], "reason": "GenBank culture ATCC MYA2933 differs from archived tip ATCCMYA2993; possible typo is not silently corrected"},
            {"accessions": ["KX423755.1", "OR471313.1", "OR471314.1"], "reason": "JL596-16/EZ4455 differ from archived protein-locus specimen EZLF1256; shared species name is insufficient"},
            {"accessions": ["LC642038.1", "LC642040.1"], "reason": "TUMH64401/64402 are not demonstrated identical to archived flavorhizomorphae TYPE tip"}],
        "interpretation": "These references provide sparse non-ITS congruence checks. They do not establish a complete true tree or independent biological identity for local query OV2602."
    })
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--references", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--datasets", nargs="+", default=["1msv", "ghy3"])
    parser.add_argument("--trees-root", type=Path)
    parser.add_argument("--configs", nargs="+", default=["A", "B", "C"])
    parser.add_argument("--seed", type=int, default=73111)
    args = parser.parse_args()
    result = audit(args.data, args.references, args.output, args.datasets,
                   args.trees_root, args.configs, args.seed)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
