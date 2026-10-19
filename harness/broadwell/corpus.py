#!/usr/bin/env python3
"""Characterize a private FASTA corpus and make reproducible development/holdout splits.

No sequence text or headers are included in the report. Pair cells are the sum of
ungapped length products over unordered pairs, a proxy for all-pairs DP cost.
K-mer Jaccard is an orientation-insensitive similarity proxy, not alignment identity.
"""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import shlex
import statistics


def fasta(path):
    records, headers, row = [], [], []
    for line in path.read_text().splitlines():
        if line.startswith(">"):
            if headers:
                records.append("".join(row).upper())
            headers.append(line)
            row = []
        elif line.strip():
            if not headers:
                raise ValueError(f"{path}: sequence before first header")
            row.append("".join(line.split()))
    if headers:
        records.append("".join(row).upper())
    if not records or any(not s for s in records):
        raise ValueError(f"{path}: empty FASTA or record")
    return headers, records


def quantiles(values):
    ordered = sorted(values)
    return {str(p): ordered[round((len(ordered) - 1) * p / 100)]
            for p in (0, 10, 25, 50, 75, 90, 99, 100)}


def kmers(row):
    table = str.maketrans("ACGT", "TGCA")
    words = (row[i:i + 11] for i in range(max(0, len(row) - 10)))
    return {min(w, w.translate(table)[::-1]) for w in words if set(w) <= set("ACGT")}


def characterize(path):
    headers, records = fasta(path)
    clean = [s.replace("-", "").replace(".", "") for s in records]
    lengths = [len(s) for s in clean]
    counts = collections.Counter("".join(records))
    total = sum(lengths)
    # Fixed sampling of pairs avoids quadratic corpus analysis.
    sketches = [kmers(s) for s in clean]
    sims = []
    for i in range(1, len(sketches)):
        a, b = sketches[i], sketches[(i * 29 + 7) % i]
        if a | b:
            sims.append(len(a & b) / len(a | b))
    return dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                sequences=len(records), lengths=quantiles(lengths),
                length_cv=statistics.pstdev(lengths) / statistics.mean(lengths),
                residues=total, pair_cells=(total * total - sum(x * x for x in lengths)) // 2,
                duplicate_sequences=len(records) - len(set(clean)),
                duplicate_headers=len(headers) - len(set(headers)),
                composition=dict(sorted(counts.items())),
                ambiguous=sum(v for c, v in counts.items() if c not in "ACGTU-."),
                input_gaps=counts["-"] + counts["."],
                gap_runs=sum(sum(s[i] in "-." and (i == 0 or s[i - 1] not in "-.")
                                 for i in range(len(s))) for s in records),
                sampled_kmer_jaccard=quantiles(sims) if sims else {},
                auto_hint="localpair" if len(records) < 200 and max(lengths) < 1000
                          else "FFT (verify from stderr)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--job-logs", type=Path,
                        help="optional application jobs directory; read only each matching alignment.log first line")
    args = parser.parse_args()
    files = sorted(args.input.glob("*.fasta"))
    stats = [characterize(p) for p in files]
    if args.job_logs:
        for s in stats:
            log = args.job_logs / Path(s["file"]).stem / "logs" / "alignment.log"
            if log.is_file():
                with log.open() as f:
                    line = f.readline()
                if line.startswith("CMD: "):
                    cmd = shlex.split(line[5:])
                    ti = cmd.index("--thread")
                    s["production_binary"] = cmd[0]
                    s["production_threads"] = int(cmd[ti + 1])
                    s["production_options"] = cmd[1:ti] + cmd[ti + 2:-1]
    if not stats:
        parser.error("no *.fasta inputs")
    # Deliberately reserve expensive, long, variable and ambiguous examples.
    hard = set()
    for key in (lambda s: s["pair_cells"], lambda s: s["lengths"]["100"],
                lambda s: s["length_cv"], lambda s: s["ambiguous"]):
        hard.update(s["file"] for s in sorted(stats, key=key, reverse=True)[:3])
    ordered = sorted((s for s in stats if s["file"] not in hard), key=lambda s: s["pair_cells"])
    dev = set()
    # One hash-selected member per cost stratum; the rest stay in the holdout.
    for start in range(0, len(ordered), 8):
        group = ordered[start:start + 8]
        dev.add(min(group, key=lambda s: s["sha256"])["file"])
    for s in stats:
        s["partition"] = "difficult" if s["file"] in hard else "development" if s["file"] in dev else "holdout"
    counts = collections.Counter()
    for s in stats:
        counts.update(s["composition"])
    report = dict(files=stats, summary=dict(
        files=len(stats), sequences=sum(s["sequences"] for s in stats),
        residues=sum(s["residues"] for s in stats),
        sequence_count_quantiles=quantiles([s["sequences"] for s in stats]),
        longest_sequence_quantiles=quantiles([s["lengths"]["100"] for s in stats]),
        pair_cells=sum(s["pair_cells"] for s in stats), composition=dict(counts),
        partitions=dict(collections.Counter(s["partition"] for s in stats)),
        production_invocations=dict(collections.Counter(
            " ".join(s.get("production_options", ["unknown"])) for s in stats)),
        production_threads=dict(collections.Counter(s.get("production_threads", "unknown") for s in stats)),
        production_binaries=dict(collections.Counter(s.get("production_binary", "unknown") for s in stats)),
        duplicate_input_files=len(stats) - len(set(s["sha256"] for s in stats))))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps(report["summary"], indent=2))
    print("development:", " ".join(sorted(dev)))
    print("difficult:", " ".join(sorted(hard)))


if __name__ == "__main__":
    main()
