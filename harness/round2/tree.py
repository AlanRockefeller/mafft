#!/usr/bin/env python3
"""Recorded IQ-TREE searches and unrooted tree comparisons for MAFFT round 2.

All inference is explicit and serial.  Importing this module runs no analyses.
Newick, RF, quartet and identity checks require only Python's standard library.
Matplotlib is optional and used only by the visualisation commands.
"""
from __future__ import annotations

import argparse
from array import array
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from typing import Any, Iterable

from analysis import (Alignment, load_mapping, read_alignment, sha256_file,
                      write_json, write_named_alignment)

ZERO_THRESHOLD = 1e-6
SHALRT_THRESHOLD = 80.0
UFBOOT_THRESHOLD = 95.0
QUARTET_SEED = 20261019


@dataclass(eq=False)
class Node:
    label: str = ""
    length: float | None = None  # Edge from parent, never the root's edge.
    children: list["Node"] = field(default_factory=list)


def parse_newick(text: str) -> Node:
    """Single-tree Newick with quoted labels, comments and multifurcations."""
    position = 0
    # Long ladder trees must not fail merely because Python's limit is 1000.
    sys.setrecursionlimit(max(sys.getrecursionlimit(), min(len(text) + 100, 100000)))

    def skip() -> None:
        nonlocal position
        while position < len(text):
            if text[position].isspace():
                position += 1
            elif text[position] == "[":
                depth = 1
                position += 1
                while position < len(text) and depth:
                    depth += (text[position] == "[") - (text[position] == "]")
                    position += 1
                if depth:
                    raise ValueError("Unterminated Newick comment")
            else:
                return

    def label() -> str:
        nonlocal position
        skip()
        if position >= len(text):
            return ""
        if text[position] == "'":
            position += 1
            value = []
            while position < len(text):
                if text[position] == "'":
                    position += 1
                    if position < len(text) and text[position] == "'":
                        position += 1
                        value.append("'")
                    else:
                        return "".join(value)
                else:
                    value.append(text[position])
                    position += 1
            raise ValueError("Unterminated quoted Newick label")
        start = position
        while position < len(text) and text[position] not in "(),:;[]" and not text[position].isspace():
            position += 1
        return text[start:position]

    def subtree() -> Node:
        nonlocal position
        skip()
        node = Node()
        if position < len(text) and text[position] == "(":
            position += 1
            while True:
                node.children.append(subtree())
                skip()
                if position < len(text) and text[position] == ",":
                    position += 1
                    continue
                if position >= len(text) or text[position] != ")":
                    raise ValueError(f"Expected ')' at Newick position {position}")
                position += 1
                break
            if len(node.children) < 2:
                raise ValueError("Unary Newick node is unsupported; suppress it explicitly")
        node.label = label()
        skip()
        if position < len(text) and text[position] == ":":
            position += 1
            skip()
            start = position
            while position < len(text) and text[position] not in ",);[" and not text[position].isspace():
                position += 1
            try:
                node.length = float(text[start:position])
            except ValueError as exc:
                raise ValueError(f"Invalid Newick branch length at {start}") from exc
            if not math.isfinite(node.length) or node.length < 0:
                raise ValueError("Newick branch lengths must be finite and nonnegative")
        if not node.children and not node.label:
            raise ValueError(f"Empty Newick tip at {position}")
        return node

    root = subtree()
    skip()
    if position >= len(text) or text[position] != ";":
        raise ValueError("Newick tree must end with a semicolon")
    position += 1
    skip()
    if position != len(text):
        raise ValueError("Expected exactly one Newick tree")
    return root


def detect_support_order(report: str | Path | None) -> list[str]:
    """Use the IQ-TREE report declaration, rather than guessing slash order."""
    if report is None or not Path(report).exists():
        return []
    text = Path(report).read_text(errors="replace")
    # IQ-TREE 2/3 place this declaration immediately before the displayed tree.
    for line in text.splitlines():
        line = line.lower()
        if "sh-alrt" in line and "ultrafast bootstrap" in line and "/" in line:
            return sorted(["shalrt", "ufboot"],
                          key=lambda x: line.index("sh-alrt" if x == "shalrt" else "ultrafast bootstrap"))
    return []


def parse_support(label: str, order: list[str]) -> dict[str, float]:
    if not order:
        return {}
    try:
        values = [float(x) for x in label.split("/")]
    except ValueError:
        return {}
    if len(values) != len(order) or any(not 0 <= x <= 100 for x in values):
        return {}
    return dict(zip(order, values))


@dataclass
class Edge:
    length: float | None
    support: dict[str, float]
    labels: list[str]


class IndexedTree:
    def __init__(self, root: Node, *, path: str = "", support_order: list[str] | None = None):
        self.root, self.path = root, path
        self.support_order = support_order or []
        self.nodes: list[Node] = []
        self.parent: list[int] = []
        self.depth: list[int] = []
        stack = [(root, 0, 0)]
        while stack:
            node, parent, depth = stack.pop()
            index = len(self.nodes)
            self.nodes.append(node)
            self.parent.append(parent if index else 0)
            self.depth.append(depth)
            stack.extend((child, index, depth + 1) for child in reversed(node.children))
        leaves = [node.label for node in self.nodes if not node.children]
        if len(leaves) != len(set(leaves)):
            raise ValueError(f"{path}: duplicate taxon labels in tree")
        self.taxa = sorted(leaves)
        self.position = {taxon: i for i, taxon in enumerate(self.taxa)}
        self.leafnode = {node.label: i for i, node in enumerate(self.nodes) if not node.children}
        self.children: list[list[int]] = [[] for _ in self.nodes]
        for i in range(1, len(self.nodes)):
            self.children[self.parent[i]].append(i)
        self.masks = [0] * len(self.nodes)
        for i in range(len(self.nodes) - 1, -1, -1):
            node = self.nodes[i]
            if not node.children:
                self.masks[i] = 1 << self.position[node.label]
            if i:
                self.masks[self.parent[i]] |= self.masks[i]
        self.universe = (1 << len(self.taxa)) - 1
        self.up: list[list[int]] = [self.parent]
        for _ in range(max(self.depth).bit_length()):
            previous = self.up[-1]
            self.up.append([previous[previous[i]] for i in range(len(self.nodes))])

    def lca(self, a: int, b: int) -> int:
        if self.depth[a] < self.depth[b]:
            a, b = b, a
        diff = self.depth[a] - self.depth[b]
        for k in range(diff.bit_length()):
            if diff & (1 << k):
                a = self.up[k][a]
        if a == b:
            return a
        for k in range(len(self.up) - 1, -1, -1):
            if self.up[k][a] != self.up[k][b]:
                a, b = self.up[k][a], self.up[k][b]
        return self.parent[a]

    def selected_mask(self, selected: Iterable[str] | None = None) -> int:
        if selected is None:
            return self.universe
        result = 0
        for taxon in selected:
            if taxon not in self.position:
                raise ValueError(f"Unknown taxon in subset: {taxon}")
            result |= 1 << self.position[taxon]
        return result

    @staticmethod
    def canonical(mask: int, universe: int) -> int:
        mask &= universe
        anchor = universe & -universe
        return universe ^ mask if mask & anchor else mask

    def splits(self, selected: Iterable[str] | None = None) -> dict[int, Edge]:
        universe = self.selected_mask(selected)
        n = universe.bit_count()
        result: dict[int, Edge] = {}
        for i, node in enumerate(self.nodes[1:], 1):
            mask = self.canonical(self.masks[i], universe)
            if mask.bit_count() < 2 or n - mask.bit_count() < 2:
                continue
            support = parse_support(node.label, self.support_order)
            if mask not in result:
                result[mask] = Edge(node.length, support, [node.label] if node.label else [])
            else:
                # Suppress degree-two root / degree-two paths after taxon pruning.
                old = result[mask]
                old.length = old.length + node.length if old.length is not None and node.length is not None else None
                for name, value in support.items():
                    old.support[name] = min(old.support.get(name, value), value)
                if node.label:
                    old.labels.append(node.label)
        return result

    def weighted_depth(self, selected: Iterable[str], retained_splits: set[int]) -> list[int]:
        universe = self.selected_mask(selected)
        n = universe.bit_count()
        depth = [0] * len(self.nodes)
        for i in range(1, len(self.nodes)):
            mask = self.canonical(self.masks[i], universe)
            # Any pendant path cancels in the quartet four-point comparison.
            trivial = mask.bit_count() < 2 or n - mask.bit_count() < 2
            depth[i] = depth[self.parent[i]] + int(trivial or mask in retained_splits)
        return depth

    def quartet(self, taxa: tuple[str, str, str, str], depth: list[int]) -> int | None:
        nodes = [self.leafnode[t] for t in taxa]
        def d(i: int, j: int) -> int:
            a, b = nodes[i], nodes[j]
            return depth[a] + depth[b] - 2 * depth[self.lca(a, b)]
        sums = [d(0, 1) + d(2, 3), d(0, 2) + d(1, 3), d(0, 3) + d(1, 2)]
        minimum = min(sums)
        winners = [i for i, value in enumerate(sums) if value == minimum]
        return winners[0] if len(winners) == 1 else None

    def side_taxa(self, mask: int) -> list[str]:
        return [taxon for i, taxon in enumerate(self.taxa) if mask & (1 << i)]


def read_tree(path: str | Path, *, report: str | Path | None = None,
              support_order: list[str] | None = None) -> IndexedTree:
    path = Path(path)
    if report is None:
        sibling = path.with_suffix(".iqtree")
        if sibling.exists():
            report = sibling
    order = detect_support_order(report) if support_order is None else support_order
    return IndexedTree(parse_newick(path.read_text()), path=str(path.resolve()), support_order=order)


def incompatible(a: int, b: int, universe: int) -> bool:
    return bool((a & b) and (a & (universe ^ b)) and ((universe ^ a) & b) and ((universe ^ a) & (universe ^ b)))


def wilson(successes: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    if not total:
        return None
    p = successes / total
    center = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [max(0., center - half), min(1., center + half)]


def compare_quartets(reference: IndexedTree, candidate: IndexedTree, selected: list[str],
                     reference_splits: set[int], candidate_splits: set[int], *,
                     samples: int = 10000, seed: int = QUARTET_SEED) -> dict[str, Any]:
    total = math.comb(len(selected), 4) if len(selected) >= 4 else 0
    if not samples or not total:
        return {"evaluated": 0, "possible_quartets": total}
    exact = total <= samples
    if exact:
        quartets = itertools.combinations(selected, 4)
    else:
        rng = random.Random(seed)
        # Independent uniform draws WITH replacement permit a binomial Monte Carlo CI.
        quartets = (tuple(sorted(rng.sample(selected, 4))) for _ in range(samples))
    rd = reference.weighted_depth(selected, reference_splits)
    cd = candidate.weighted_depth(selected, candidate_splits)
    counts: dict[str, int] = defaultdict(int)
    taxon_counts: dict[str, list[int]] = {t: [0, 0] for t in selected}
    examples = []
    for quartet in quartets:
        a, b = reference.quartet(quartet, rd), candidate.quartet(quartet, cd)
        counts["evaluated"] += 1
        counts["different"] += a != b
        counts["both_resolved"] += a is not None and b is not None
        counts["resolved_conflict"] += a is not None and b is not None and a != b
        counts["both_unresolved"] += a is None and b is None
        counts["reference_only_resolved"] += a is not None and b is None
        counts["candidate_only_resolved"] += a is None and b is not None
        for taxon in quartet:
            taxon_counts[taxon][0] += 1
            taxon_counts[taxon][1] += a != b
        if a != b and len(examples) < 12:
            examples.append({"taxa": quartet, "reference_resolution": a, "candidate_resolution": b})
    n, d = counts["evaluated"], counts["different"]
    return {**counts, "possible_quartets": total, "exact": exact,
            "sampling": "all quartets" if exact else "uniform independent quartets with replacement",
            "seed": seed, "distance_fraction": d / n,
            "resolved_conflict_fraction": counts["resolved_conflict"] / n,
            "distance_monte_carlo_95ci": [d / n, d / n] if exact else wilson(d, n),
            "ci_interpretation": "Monte Carlo sampling uncertainty only; not biological replication",
            "resolution_codes": {"0": "(a,b)|(c,d)", "1": "(a,c)|(b,d)", "2": "(a,d)|(b,c)", "null": "unresolved"},
            "discordant_examples": examples,
            "taxon_participation": [{"id": t, "sampled_quartets": value[0],
                                     "discordant_quartets": value[1],
                                     "discordance_fraction": value[1] / value[0] if value[0] else None}
                                    for t, value in taxon_counts.items()]}


def keep_edge(edge: Edge, *, zero: bool, support: bool,
              zero_threshold: float, shalrt: float, ufboot: float) -> bool:
    if zero and edge.length is not None and edge.length <= zero_threshold:
        return False
    return not support or (edge.support.get("shalrt", -1) >= shalrt and edge.support.get("ufboot", -1) >= ufboot)


def split_record(tree: IndexedTree, mask: int, edge: Edge) -> dict[str, Any]:
    taxa = tree.side_taxa(mask)
    return {"id": hashlib.sha256("\n".join(taxa).encode()).hexdigest()[:16],
            "side": taxa, "size": len(taxa), "length": edge.length, "support": edge.support}


def compare_view(reference: IndexedTree, candidate: IndexedTree, selected: list[str], *,
                 zero: bool = False, support: bool = False,
                 zero_threshold: float = ZERO_THRESHOLD, shalrt: float = SHALRT_THRESHOLD,
                 ufboot: float = UFBOOT_THRESHOLD, quartets: int = 10000,
                 quartet_seed: int = QUARTET_SEED, details: bool = False) -> dict[str, Any]:
    ra, ca = reference.splits(selected), candidate.splits(selected)
    r = {mask: edge for mask, edge in ra.items() if keep_edge(edge, zero=zero, support=support,
                                                           zero_threshold=zero_threshold, shalrt=shalrt, ufboot=ufboot)}
    c = {mask: edge for mask, edge in ca.items() if keep_edge(edge, zero=zero, support=support,
                                                           zero_threshold=zero_threshold, shalrt=shalrt, ufboot=ufboot)}
    shared, onlyr, onlyc = set(r) & set(c), set(r) - set(c), set(c) - set(r)
    universe = reference.selected_mask(selected)
    pairs = [(a, b) for a in sorted(onlyr) for b in sorted(onlyc) if incompatible(a, b, universe)]
    conflicts_r = {a for a, _ in pairs}
    conflicts_c = {b for _, b in pairs}
    rf, n = len(onlyr) + len(onlyc), len(selected)
    result = {"taxa": n, "reference_splits": len(r), "candidate_splits": len(c),
              "shared_splits": len(shared), "reference_only_splits": len(onlyr),
              "candidate_only_splits": len(onlyc), "rf": rf,
              "normalized_rf": rf / (2 * (n - 3)) if n > 3 else 0.0,
              "normalized_rf_definition": "RF / (2*(taxa-3)); upper bound for fully resolved unrooted trees",
              "split_normalized_rf": rf / (len(r) + len(c)) if len(r) + len(c) else 0.0,
              "reference_incompatible_splits": len(conflicts_r),
              "candidate_incompatible_splits": len(conflicts_c),
              "incompatible_split_pairs": len(pairs),
              "reference_compatible_resolution_only_splits": len(onlyr) - len(conflicts_r),
              "candidate_compatible_resolution_only_splits": len(onlyc) - len(conflicts_c),
              "zero_edges_collapsed": zero, "zero_threshold": zero_threshold if zero else None,
              "support_filter": {"shalrt_min": shalrt, "ufboot_min": ufboot} if support else None,
              "quartets": compare_quartets(reference, candidate, selected, set(r), set(c), samples=quartets, seed=quartet_seed)}
    def show_pair(a: int, b: int) -> dict[str, Any]:
        return {"reference": split_record(reference, a, r[a]), "candidate": split_record(candidate, b, c[b])}
    result["conflict_examples"] = [show_pair(a, b) for a, b in pairs[:20]]
    if details:
        result["split_details"] = [
            {"status": "shared" if mask in shared else "reference_only",
             "reference": split_record(reference, mask, edge),
             "candidate": split_record(candidate, mask, c[mask]) if mask in shared else None}
            for mask, edge in sorted(r.items())]
        result["split_details"].extend({"status": "candidate_only", "reference": None,
                                         "candidate": split_record(candidate, mask, c[mask])} for mask in sorted(onlyc))
    return result


def compare_trees(reference: IndexedTree, candidate: IndexedTree, *, mapping: dict[str, Any] | None = None,
                  quartets: int = 10000, quartet_seed: int = QUARTET_SEED,
                  zero_threshold: float = ZERO_THRESHOLD, shalrt: float = SHALRT_THRESHOLD,
                  ufboot: float = UFBOOT_THRESHOLD, details: bool = False) -> dict[str, Any]:
    if reference.taxa != candidate.taxa:
        missing_r, missing_c = set(candidate.taxa) - set(reference.taxa), set(reference.taxa) - set(candidate.taxa)
        raise ValueError(f"Tree taxon sets differ: reference missing {sorted(missing_r)}; candidate missing {sorted(missing_c)}")
    if mapping is not None and set(reference.taxa) != {r["id"] for r in mapping["records"]}:
        raise ValueError("Tree taxon set does not equal the complete stable-ID mapping")
    support_available = all(set(t.support_order) == {"shalrt", "ufboot"} for t in (reference, candidate))
    kwargs = dict(quartets=quartets, quartet_seed=quartet_seed, zero_threshold=zero_threshold,
                  shalrt=shalrt, ufboot=ufboot, details=details)
    views = {"raw": compare_view(reference, candidate, reference.taxa, **kwargs),
             "zero_collapsed": compare_view(reference, candidate, reference.taxa, zero=True, **kwargs)}
    if support_available:
        views["strong_support"] = compare_view(reference, candidate, reference.taxa, zero=True, support=True, **kwargs)
    selected = reference.taxa
    removed = []
    if mapping is not None:
        seen = set()
        selected = []
        for record in mapping["records"]:
            if record["sequence_sha256"] not in seen:
                selected.append(record["id"])
                seen.add(record["sequence_sha256"])
            else:
                removed.append(record["id"])
        selected.sort()
        views["input_duplicate_representatives"] = compare_view(reference, candidate, selected, **kwargs)
        views["input_duplicate_representatives_zero_collapsed"] = compare_view(reference, candidate, selected, zero=True, **kwargs)
        if support_available:
            views["input_duplicate_representatives_strong_support"] = compare_view(reference, candidate, selected, zero=True, support=True, **kwargs)
    return {"schema": "mafft-round2-tree-comparison-v1",
            "reference": {"path": reference.path, "sha256": sha256_file(reference.path) if reference.path else None,
                          "support_order": reference.support_order},
            "candidate": {"path": candidate.path, "sha256": sha256_file(candidate.path) if candidate.path else None,
                          "support_order": candidate.support_order},
            "input_sha256": mapping["input_sha256"] if mapping else None,
            "taxa": len(reference.taxa), "support_available": support_available,
            "duplicate_sensitivity": {"definition": "Keep first original ordinal per EXACT original forward-strand ungapped sequence; original inferred trees always retain all taxa.",
                                      "removed_ids": removed, "retained_ids": selected} if mapping else None,
            "views": views,
            "interpretation": "Unrooted split and quartet congruence measures stability, not independent biological accuracy. Raw likelihoods from different alignments are not ranked."}


def inference_version(binary: str) -> str:
    result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15, check=True)
    return (result.stdout + result.stderr).strip()


def prepare_tree_job(*, alignment: str | Path, out_dir: str | Path, seed: int,
                     mapping: dict[str, Any], iqtree: str = "/usr/bin/iqtree2",
                     support: bool = False, mem: str = "6G", nstop: int | None = None,
                     fast: bool = False) -> dict[str, Any]:
    """Create a reviewable immutable job; no inference runs in this function."""
    binary = shutil.which(iqtree) if not Path(iqtree).is_absolute() else iqtree
    if binary is None or not Path(binary).is_file():
        raise ValueError(f"IQ-TREE executable is unavailable: {iqtree}")
    validated = read_alignment(alignment, mapping)
    directory = Path(out_dir).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    write_named_alignment(validated, directory / "named.fasta")
    write_json(directory / "taxa.json", mapping)
    orientation = [{"id": identifier, "reverse_complement": rev, "alignment_header": label}
                   for identifier, rev, label in zip(validated.ids, validated.reverse, validated.headers)]
    write_json(directory / "direction.json", orientation)
    if seed < 1:
        raise ValueError("Tree seed must be a positive integer")
    command = [str(Path(binary).resolve()), "-s", str(directory / "named.fasta"),
               "--seqtype", "DNA", "-m", "GTR+F+G4", "-T", "1", "--seed", str(seed),
               "--keep-ident", "--mem", mem, "--prefix", str(directory / "inference")]
    if nstop is not None:
        command.extend(["--nstop", str(nstop)])
    if fast:
        command.append("--fast")
    if support:
        command.extend(["-B", "1000", "--alrt", "1000", "--bnni"])
    plan = {"schema": "mafft-round2-tree-job-v1", "command": command,
            "shell_display_only": shlex.join(command), "directory": str(directory),
            "iqtree_version": inference_version(command[0]), "binary_sha256": sha256_file(command[0]),
            "source_alignment": str(Path(alignment).resolve()), "alignment_sha256": sha256_file(alignment),
            "named_alignment_sha256": sha256_file(directory / "named.fasta"),
            "input_sha256": mapping["input_sha256"], "taxa": len(validated.ids),
            "seed": seed, "threads": 1, "model": "GTR+F+G4", "support": support,
            "fast": fast, "search_method": "IQ-TREE --fast screening" if fast else "IQ-TREE default full search",
            "support_replicates": {"ufboot": 1000, "shalrt": 1000, "bootstrap_nni": True} if support else None,
            "nstop": nstop, "direction_ids": [x["id"] for x in orientation if x["reverse_complement"]],
            "created_utc": datetime.now(timezone.utc).isoformat()}
    write_json(directory / "command.json", plan)
    return plan


def run_tree_job(plan: dict[str, Any], *, timeout: float | None = None,
                 lock: str | Path | None = None) -> dict[str, Any]:
    """Run one prepared search; optional flock coordinates external schedulers."""
    directory = Path(plan["directory"])
    if (directory / "run.json").exists() or (directory / "started.json").exists():
        raise FileExistsError(f"Tree job has already started: {directory}")
    if sha256_file(directory / "named.fasta") != plan["named_alignment_sha256"]:
        raise ValueError("Prepared alignment changed after job creation")
    if sha256_file(plan["command"][0]) != plan["binary_sha256"]:
        raise ValueError("IQ-TREE binary changed after job creation")
    lockfile = open(lock, "a+") if lock else None
    try:
        if lockfile:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
        write_json(directory / "started.json", {"started_utc": datetime.now(timezone.utc).isoformat(),
                                               "pid": os.getpid()})
        measured = ["/usr/bin/time", "-f", "%e\t%U\t%S\t%M", "-o", str(directory / "resource.tsv"), *plan["command"]]
        begin = time.monotonic()
        timed_out = False
        with open(directory / "stdout.txt", "xb") as stdout, open(directory / "stderr.txt", "xb") as stderr:
            env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
            process = subprocess.Popen(measured, stdout=stdout, stderr=stderr, env=env, start_new_session=True,
                                       cwd=directory)
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    exit_code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    exit_code = process.wait()
        elapsed = time.monotonic() - begin
        resource_values = None
        resource_file = directory / "resource.tsv"
        if resource_file.exists():
            for line in resource_file.read_text().splitlines():
                fields = line.split("\t")
                if len(fields) == 4:
                    try:
                        resource_values = {"wall_seconds": float(fields[0]), "user_seconds": float(fields[1]),
                                           "system_seconds": float(fields[2]), "max_rss_kib": int(fields[3])}
                    except ValueError:
                        pass
        tree_path = directory / "inference.treefile"
        validation_error = None
        order = []
        if exit_code == 0 and tree_path.exists():
            try:
                tree = read_tree(tree_path)
                order = tree.support_order
                expected = {r["id"] for r in json.loads((directory / "taxa.json").read_text())["records"]}
                if set(tree.taxa) != expected:
                    raise ValueError("IQ-TREE changed the taxon set")
                if plan["support"] and set(order) != {"shalrt", "ufboot"}:
                    raise ValueError("Cannot verify SH-aLRT/UFBoot label order from IQ-TREE report")
            except ValueError as exc:
                validation_error = str(exc)
        result = {"schema": "mafft-round2-tree-run-v1", "plan": plan,
                  "exit_code": exit_code, "timed_out": timed_out,
                  "elapsed_seconds": elapsed, "resources": resource_values,
                  "tree": str(tree_path) if tree_path.exists() else None,
                  "tree_sha256": sha256_file(tree_path) if tree_path.exists() else None,
                  "support_order": order, "validation_error": validation_error,
                  "completed": exit_code == 0 and tree_path.exists() and validation_error is None,
                  "finished_utc": datetime.now(timezone.utc).isoformat()}
        write_json(directory / "run.json", result)
        return result
    finally:
        if lockfile:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)
            lockfile.close()


def comparison_rows(comparisons: Iterable[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for source, comparison in comparisons:
        for view, values in comparison["views"].items():
            quartet = values["quartets"]
            rows.append({"source": source, "input_sha256": comparison.get("input_sha256"),
                         "reference": comparison["reference"]["path"], "candidate": comparison["candidate"]["path"],
                         "view": view, "taxa": values["taxa"], "rf": values["rf"],
                         "normalized_rf": values["normalized_rf"], "shared_splits": values["shared_splits"],
                         "incompatible_split_pairs": values["incompatible_split_pairs"],
                         "quartet_distance": quartet.get("distance_fraction"),
                         "quartet_resolved_conflict": quartet.get("resolved_conflict_fraction")})
    return rows


def write_tsv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    import csv
    if not rows:
        raise ValueError("No result rows")
    with open(path, "x", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _plot_import() -> Any:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except ImportError as exc:
        raise ValueError("Visualization needs matplotlib; use the isolated round2/venv Python") from exc


def _pruned_display(tree: IndexedTree, selected: set[str]) -> tuple[Node, dict[int, int]]:
    """Clone/prune only for visualization; topology metrics use full input trees."""
    masks: dict[int, int] = {}
    def prune(index: int) -> Node | None:
        source = tree.nodes[index]
        if not source.children:
            if source.label not in selected:
                return None
            result = Node(source.label, source.length)
        else:
            children = [node for child in tree.children[index] if (node := prune(child)) is not None]
            if not children:
                return None
            if len(children) == 1:
                result = children[0]
                result.length = ((result.length or 0) + (source.length or 0))
            else:
                result = Node(source.label, source.length, children)
        masks[id(result)] = tree.masks[index]
        return result
    root = prune(0)
    if root is None:
        raise ValueError("No taxa selected for visualization")
    return root, masks


def visualize_trees(reference: IndexedTree, candidate: IndexedTree, *, svg: str | Path,
                    pdf: str | Path | None = None, mapping: dict[str, Any] | None = None,
                    selected: list[str] | None = None, max_tips: int = 48,
                    title: str = "Alignment-sensitive tree comparison") -> dict[str, Any]:
    if reference.taxa != candidate.taxa:
        raise ValueError("Tree taxon sets differ")
    if max_tips < 4:
        raise ValueError("At least four tips are needed")
    selection = "all taxa"
    if selected is None:
        if len(reference.taxa) <= max_tips:
            selected = reference.taxa
        else:
            selection = "fixed-seed discordant-quartet exemplars, then evenly spaced ordinal taxa"
            ra, ca = set(reference.splits()), set(candidate.splits())
            panel = compare_quartets(reference, candidate, reference.taxa, ra, ca, samples=2000)
            chosen: set[str] = set()
            for example in panel.get("discordant_examples", []):
                if len(chosen | set(example["taxa"])) <= max_tips:
                    chosen.update(example["taxa"])
            for i in range(max_tips):
                if len(chosen) < max_tips:
                    chosen.add(reference.taxa[int(i * (len(reference.taxa) - 1) / (max_tips - 1))])
            selected = sorted(chosen)
    else:
        selection = "explicit user subset"
        if len(set(selected)) != len(selected) or not set(selected) <= set(reference.taxa):
            raise ValueError("Visualization subset contains duplicate or unknown taxa")
        if len(selected) > max_tips:
            raise ValueError("Explicit subset exceeds --max-tips")
    selected = sorted(selected)
    plt = _plot_import()
    fig, ax = plt.subplots(figsize=(17, max(5, len(selected) * .23 + 1.9)))
    roots = [_pruned_display(tree, set(selected)) for tree in (reference, candidate)]
    def leaves(node: Node) -> list[str]:
        return [x for child in node.children for x in leaves(child)] if node.children else [node.label]
    def reorder(node: Node, order: dict[str, int]) -> None:
        for child in node.children:
            reorder(child, order)
        node.children.sort(key=lambda x: sum(order[t] for t in leaves(x)) / len(leaves(x)))
    order = {taxon: i for i, taxon in enumerate(selected)}
    for _ in range(4):
        for root, _masks in roots:
            reorder(root, order)
            order = {taxon: i for i, taxon in enumerate(leaves(root))}
    labels = {r["id"]: r["original_header"] for r in mapping["records"]} if mapping else {}
    tips = []
    all_splits = [set(tree.splits(selected)) for tree in (reference, candidate)]
    for panel, ((root, masks), tree) in enumerate(zip(roots, (reference, candidate))):
        tip_order = {taxon: i for i, taxon in enumerate(leaves(root))}
        positions: dict[int, tuple[float, float]] = {}
        depths: dict[int, int] = {}
        def layout(node: Node, depth: int = 0) -> float:
            depths[id(node)] = depth
            ys = [layout(child, depth + 1) for child in node.children]
            y = sum(ys) / len(ys) if ys else tip_order[node.label]
            positions[id(node)] = (float(depth), y)
            return y
        layout(root)
        maximum = max(depths.values()) or 1
        # Trees occupy [0,.3] and [.7,1]; labels face the central tanglegram.
        def px(depth: float) -> float:
            return .29 * depth / maximum if panel == 0 else 1 - .29 * depth / maximum
        universe = tree.selected_mask(selected)
        def draw(node: Node) -> None:
            x, y = positions[id(node)]
            if node.children:
                ys = [positions[id(child)][1] for child in node.children]
                ax.plot([px(x), px(x)], [min(ys), max(ys)], color="#444444", lw=.7)
                for child in node.children:
                    cx, cy = positions[id(child)]
                    mask = tree.canonical(masks[id(child)], universe)
                    nontrivial = 2 <= mask.bit_count() <= len(selected) - 2
                    conflict = nontrivial and mask not in all_splits[1 - panel]
                    color = "#b2182b" if conflict else "#444444"
                    ax.plot([px(x), px(cx)], [cy, cy], color=color, lw=1.6 if conflict else .7)
                    if child.children and "/" in child.label:
                        ax.text((px(x) + px(cx)) / 2, cy + .12, child.label, fontsize=5.5, color=color,
                                ha="center", va="bottom")
                    draw(child)
            else:
                end = .30 if panel == 0 else .70
                ax.plot([px(x), end], [y, y], color="#999999", lw=.35, ls=":")
                text = labels.get(node.label, node.label)
                if len(text) > 42:
                    text = text[:39] + "…"
                text = f"{node.label} {text}" if node.label in labels else text
                ax.text(end + (.005 if panel == 0 else -.005), y, text, fontsize=6.2,
                        ha="left" if panel == 0 else "right", va="center")
        draw(root)
        tips.append(tip_order)
    for taxon in selected:
        a, b = tips[0][taxon], tips[1][taxon]
        ax.plot([.48, .52], [a, b], color="#4c78a8", alpha=.32, lw=.6, zorder=-1)
    ax.set_xlim(-.015, 1.015)
    ax.set_ylim(len(selected) + 1.5, -2.5)
    ax.text(.14, -1.5, "Reference", ha="center", fontsize=12)
    ax.text(.86, -1.5, "Candidate", ha="center", fontsize=12)
    ax.set_title(title, fontsize=14, pad=12)
    ax.axis("off")
    fig.text(.5, .018, f"{len(selected)} of {len(reference.taxa)} taxa shown. Cladogram; branch lengths are not to scale. Red: split absent from other displayed tree.\nSelection: {selection}. Root placement is for display; all distances use unrooted splits.",
             ha="center", fontsize=8)
    fig.tight_layout(rect=[0, .045, 1, 1])
    for target in [svg, pdf]:
        if target is not None:
            if Path(target).exists():
                raise FileExistsError(target)
            fig.savefig(target, bbox_inches="tight")
    plt.close(fig)
    return {"svg": str(Path(svg).resolve()), "pdf": str(Path(pdf).resolve()) if pdf else None,
            "selected_ids": selected, "selection": selection, "total_taxa": len(reference.taxa)}


def plot_comparisons(comparisons: list[tuple[str, dict[str, Any]]], *, svg: str | Path,
                     pdf: str | Path | None = None) -> None:
    plt = _plot_import()
    views = [v for v in ("raw", "input_duplicate_representatives_zero_collapsed", "strong_support")
             if any(v in data["views"] for _, data in comparisons)]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for i, view in enumerate(views):
        values = [data["views"][view]["normalized_rf"] for _, data in comparisons if view in data["views"]]
        axes[0].plot(sorted(values), [(j + 1) / len(values) for j in range(len(values))], label=view, drawstyle="steps-post")
        x, y = [], []
        for _, data in comparisons:
            if view in data["views"] and "distance_fraction" in data["views"][view]["quartets"]:
                x.append(data["views"][view]["normalized_rf"])
                y.append(data["views"][view]["quartets"]["distance_fraction"])
        axes[1].scatter(x, y, label=view, s=18, alpha=.7)
    axes[0].set(xlabel="Normalized unrooted RF distance", ylabel="Fraction of tree comparisons", ylim=(0, 1.02))
    axes[1].set(xlabel="Normalized unrooted RF distance", ylabel="Quartet distance fraction")
    axes[0].legend(fontsize=6)
    axes[1].legend(fontsize=6)
    fig.suptitle("Tree congruence: comparisons are repeated observations, not independent datasets", fontsize=10)
    fig.tight_layout()
    for target in [svg, pdf]:
        if target is not None:
            if Path(target).exists():
                raise FileExistsError(target)
            fig.savefig(target, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("run", help="Prepare and run one fixed-model one-thread IQ-TREE search")
    p.add_argument("--alignment", required=True)
    p.add_argument("--input")
    p.add_argument("--mapping")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--seed", required=True, type=int)
    p.add_argument("--iqtree", default="/usr/bin/iqtree2")
    p.add_argument("--support", action="store_true")
    p.add_argument("--fast", action="store_true", help="Explicitly distinct reduced-search screening method")
    p.add_argument("--mem", default="6G")
    p.add_argument("--nstop", type=int)
    p.add_argument("--plan-only", action="store_true")
    p.add_argument("--timeout", type=float)
    p.add_argument("--lock")
    p = sub.add_parser("execute", help="Execute a prepared command.json exactly once")
    p.add_argument("--plan", required=True)
    p.add_argument("--timeout", type=float)
    p.add_argument("--lock")
    p = sub.add_parser("compare")
    p.add_argument("--reference", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--reference-report")
    p.add_argument("--candidate-report")
    p.add_argument("--support-order", help="Explicit verified slash-label order, e.g. shalrt,ufboot")
    p.add_argument("--mapping")
    p.add_argument("--json", required=True)
    p.add_argument("--quartets", type=int, default=10000)
    p.add_argument("--quartet-seed", type=int, default=QUARTET_SEED)
    p.add_argument("--zero-threshold", type=float, default=ZERO_THRESHOLD)
    p.add_argument("--shalrt", type=float, default=SHALRT_THRESHOLD)
    p.add_argument("--ufboot", type=float, default=UFBOOT_THRESHOLD)
    p.add_argument("--details", action="store_true")
    p = sub.add_parser("visualize")
    p.add_argument("--reference", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--mapping")
    p.add_argument("--svg", required=True)
    p.add_argument("--pdf")
    p.add_argument("--selection-json")
    p.add_argument("--taxa", help="Comma-separated stable IDs")
    p.add_argument("--max-tips", type=int, default=48)
    p.add_argument("--title", default="Alignment-sensitive tree comparison")
    p = sub.add_parser("collect")
    p.add_argument("comparisons", nargs="+")
    p.add_argument("--tsv", required=True)
    p.add_argument("--svg")
    p.add_argument("--pdf")
    args = parser.parse_args()
    if args.action == "run":
        plan = prepare_tree_job(alignment=args.alignment, out_dir=args.out_dir, seed=args.seed,
                                mapping=load_mapping(args.mapping, args.input), iqtree=args.iqtree,
                                support=args.support, mem=args.mem, nstop=args.nstop, fast=args.fast)
        result = plan if args.plan_only else run_tree_job(plan, timeout=args.timeout, lock=args.lock)
        print(json.dumps(result, sort_keys=True))
        if not args.plan_only and not result["completed"]:
            sys.exit(1)
    elif args.action == "execute":
        result = run_tree_job(json.loads(Path(args.plan).read_text()), timeout=args.timeout, lock=args.lock)
        print(json.dumps(result, sort_keys=True))
        if not result["completed"]:
            sys.exit(1)
    elif args.action == "compare":
        order = args.support_order.split(",") if args.support_order else None
        reference = read_tree(args.reference, report=args.reference_report, support_order=order)
        candidate = read_tree(args.candidate, report=args.candidate_report, support_order=order)
        mapping = load_mapping(args.mapping) if args.mapping else None
        result = compare_trees(reference, candidate, mapping=mapping, quartets=args.quartets,
                               quartet_seed=args.quartet_seed, zero_threshold=args.zero_threshold,
                               shalrt=args.shalrt, ufboot=args.ufboot, details=args.details)
        write_json(args.json, result)
        print(json.dumps({"output": str(Path(args.json).resolve()), "raw_rf": result["views"]["raw"]["rf"]}))
    elif args.action == "visualize":
        reference, candidate = read_tree(args.reference), read_tree(args.candidate)
        result = visualize_trees(reference, candidate, svg=args.svg, pdf=args.pdf,
                                  mapping=load_mapping(args.mapping) if args.mapping else None,
                                  selected=args.taxa.split(",") if args.taxa else None,
                                  max_tips=args.max_tips, title=args.title)
        if args.selection_json:
            write_json(args.selection_json, result)
        print(json.dumps(result, sort_keys=True))
    else:
        comparisons = [(p, json.loads(Path(p).read_text())) for p in args.comparisons]
        rows = comparison_rows(comparisons)
        write_tsv(args.tsv, rows)
        if args.svg:
            plot_comparisons(comparisons, svg=args.svg, pdf=args.pdf)
        print(json.dumps({"tsv": str(Path(args.tsv).resolve()), "rows": len(rows)}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
