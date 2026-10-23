#!/usr/bin/env python3
"""Compare reference/candidate stdout and exit status across strategies and score settings.

Matching rejections are distinguished from successful nonempty alignments. All commands,
outputs, stderr and SHA-256 hashes are retained outside Git. Thread 2 refinement is not a
determinism test; use lins1 for concurrent pair-stage checks with no iterative refinement.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "exact"))
from caserun import run_group  # noqa: E402  (kills the whole process group on timeout)

MODES = {
    "auto": ["--auto"],
    "direction": ["--adjustdirection", "--auto"],
    "accurate-direction": ["--adjustdirectionaccurately", "--retree", "1", "--maxiterate", "0"],
    "linsi": ["--localpair", "--maxiterate", "1000"],
    "lins1": ["--localpair", "--maxiterate", "0"],
    "direction-lins1": ["--adjustdirection", "--localpair", "--maxiterate", "0"],
    "gap": ["--localpair", "--maxiterate", "1000", "--lop", "-1.1", "--lexp", "-0.1"],
    "fractional": ["--localpair", "--maxiterate", "1000", "--weighti", "0.37"],
    "ginsi": ["--globalpair", "--maxiterate", "1000"],
    "shift": ["--globalpair", "--maxiterate", "1000", "--allowshift"],
    "einsi": ["--genafpair", "--maxiterate", "1000"],
    "fftnsi": ["--retree", "2", "--maxiterate", "2"],
    "fftns2": ["--retree", "2", "--maxiterate", "0"],
    "reorder": ["--localpair", "--maxiterate", "1000", "--reorder"],
    "clustal": ["--localpair", "--maxiterate", "1000", "--clustalout"],
    "clustal-reorder": ["--localpair", "--maxiterate", "1000", "--clustalout", "--reorder"],
    "phylip": ["--localpair", "--maxiterate", "1000", "--phylipout"],
}

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--reference", required=True, type=Path)
p.add_argument("--candidate", required=True, type=Path)
p.add_argument("--data", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--files", nargs="*")
p.add_argument("--modes", nargs="+", choices=MODES, default=["auto", "linsi", "gap", "fractional", "ginsi", "fftnsi"])
p.add_argument("--threads", type=int, default=1)
p.add_argument("--cpu", default="1", help="taskset CPU list, e.g. 1 or 0,1")
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=False)
tmp = (a.output / "tmp").resolve()
tmp.mkdir()
env = {**os.environ, "LC_ALL": "C", "LANG": "C", "TMPDIR": str(tmp)}
env.pop("MAFFT_BINARIES", None)
files = sorted(a.data.glob("*.fasta"))
if a.files is not None:
    files = [f for f in files if f.name in a.files]
if not files:
    p.error("no inputs")
counts = {"aligned": 0, "matching-rejection": 0}
with (a.output / "runs.jsonl").open("x", buffering=1) as log:
    for mode in a.modes:
        for source in files:
            dest = a.output / mode / source.name
            dest.mkdir(parents=True)
            results = []
            for name, binary in (("ref", a.reference), ("new", a.candidate)):
                argv = ["nice", "-n", "15", "taskset", "-c", str(a.cpu), str(binary.resolve()),
                        "--thread", str(a.threads), *MODES[mode], str(source.resolve())]
                with (dest / name).open("xb") as out, (dest / (name + ".stderr")).open("xb") as err:
                    r = run_group(argv, stdout=out, stderr=err, env=env, timeout=600)
                value = (dest / name).read_bytes()
                if r["timed_out"] or r["launch_error"]:
                    raise SystemExit(f"timeout or launch failure: {argv}; retained {dest}")
                results.append(dict(argv=argv, returncode=r["returncode"], sha256=hashlib.sha256(value).hexdigest(), bytes=len(value)))
            ok = results[0]["returncode"] == results[1]["returncode"] and (dest / "ref").read_bytes() == (dest / "new").read_bytes()
            status = "aligned" if results[0]["returncode"] == 0 and results[0]["bytes"] else "matching-rejection"
            log.write(json.dumps(dict(input=source.name, mode=mode, threads=a.threads, identical=ok, status=status, builds=results)) + "\n")
            print(f"{mode:18} {source.name:22} {'OK' if ok else 'FAIL'} {status}", flush=True)
            if not ok:
                raise SystemExit(f"mismatch: {dest}")
            counts[status] += 1
print(json.dumps(counts))
