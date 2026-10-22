#!/usr/bin/env python3
"""Summarize paired real-input timings, checking completeness and exactness first."""
import argparse
import collections
import json
import math
from pathlib import Path
import statistics


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("runs", type=Path)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--reference", default="reference")
    p.add_argument("--candidate", default="candidate")
    p.add_argument("--partition", choices=["all", "development", "holdout", "difficult"], default="all")
    p.add_argument("--allow-partial", action="store_true")
    a = p.parse_args()
    manifest = {s["file"]: s for s in json.loads(a.manifest.read_text())["files"]}
    corpus = {name: s for name, s in manifest.items()
              if a.partition == "all" or s["partition"] == a.partition}
    runs = collections.defaultdict(dict)
    for line in a.runs.read_text().splitlines():
        r = json.loads(line)
        if r["build"] not in (a.reference, a.candidate):
            continue
        if r["input"] not in manifest:
            raise SystemExit(f"input not in manifest: {r['input']}")
        if r["input"] not in corpus:
            continue
        if r["returncode"] or r.get("identical") is not True:
            raise SystemExit(f"failed or not checked against golden: {r['input']}, {r['build']}")
        if r["input_sha256"] != corpus[r["input"]]["sha256"]:
            raise SystemExit(f"input hash changed: {r['input']}")
        key = (r["input"], r["round"])
        if r["build"] in runs[key]:
            raise SystemExit(f"duplicate run: {key}, {r['build']}")
        runs[key][r["build"]] = r
    complete = {k: v for k, v in runs.items() if set(v) == {a.reference, a.candidate}}
    if not a.allow_partial:
        rounds = {key[1] for key in runs}
        expected = {(name, i) for name in corpus for i in rounds}
        if not rounds or set(complete) != expected:
            raise SystemExit(f"incomplete corpus: {len(complete)} complete pairs, expected {len(expected)}")
    groups = collections.defaultdict(list)
    for (name, _), pair in sorted(complete.items()):
        ref, new = pair[a.reference], pair[a.candidate]
        if ref["output_sha256"] != new["output_sha256"]:
            raise SystemExit(f"pair output mismatch: {name}")
        for field in ("mode", "threads", "environment"):
            if ref[field] != new[field]:
                raise SystemExit(f"incomparable {field}: {name}")
        def without_binary(argv):
            index = argv.index("--thread")
            return argv[:index - 1] + argv[index:]
        if without_binary(ref["argv"]) != without_binary(new["argv"]):
            raise SystemExit(f"incomparable command flags: {name}")
        s = corpus[name]
        for group in ("all", "partition:" + s["partition"],
                      "options:" + " ".join(s.get("production_options", []))):
            groups[group].append((ref, new))
    result = {"complete_pairs": len(complete), "groups": {}}
    for group, pairs in sorted(groups.items()):
        record = {"pairs": len(pairs)}
        for metric in ("wall", "cpu"):
            ref = sum(r[metric] for r, n in pairs)
            new = sum(n[metric] for r, n in pairs)
            ratios = [r[metric] / n[metric] for r, n in pairs]
            record[metric] = dict(reference=ref, candidate=new, speedup=ref / new,
                                  reduction_percent=100 * (1 - new / ref),
                                  median_speedup=statistics.median(ratios),
                                  geometric_mean_speedup=math.exp(statistics.mean(map(math.log, ratios))),
                                  faster=sum(x > 1 for x in ratios),
                                  minimum_speedup=min(ratios), maximum_speedup=max(ratios))
        for metric in ("inblocks", "outblocks", "major_faults", "minor_faults",
                       "voluntary_switches", "involuntary_switches"):
            record[metric] = {a.reference: sum(r[metric] for r, n in pairs),
                              a.candidate: sum(n[metric] for r, n in pairs)}
        result["groups"][group] = record
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
