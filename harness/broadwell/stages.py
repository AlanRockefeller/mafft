#!/usr/bin/env python3
"""Capture/replay real dvtditr inputs to measure refinement independently of all-pairs DP.

setup REAL_LIBEXEC WRAPPERS creates symlinks, replacing only dvtditr with this script.
Run MAFFT with MAFFT_BINARIES=WRAPPERS and MAFFT_CAPTURE_CASE=/absolute/new/case.
bench --case CASE --build name=PREFIX ... --output NEW_DIR [--rounds 3] replays
the captured argv and compares the rewritten pre file in every byte (stdout is discarded
by the MAFFT launcher). Include the reference among the builds; the frozen full-corpus
goldens remain the authoritative output oracle. Case files are private/untracked.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time


def capture():
    root = Path(os.environ["MAFFT_CAPTURE_CASE"])
    root.mkdir(parents=True, exist_ok=False)
    shutil.copytree(Path.cwd(), root / "input")
    binary = Path(os.environ["MAFFT_CAPTURE_REAL"]) / "dvtditr"
    (root / "argv.json").write_text(json.dumps(sys.argv[1:]) + "\n")
    os.execv(binary, [str(binary), *sys.argv[1:]])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="op", required=True)
    setup = sub.add_parser("setup")
    setup.add_argument("real", type=Path)
    setup.add_argument("wrappers", type=Path)
    bench = sub.add_parser("bench")
    bench.add_argument("--case", action="append", type=Path, required=True)
    bench.add_argument("--build", action="append", required=True)
    bench.add_argument("--output", type=Path, required=True)
    bench.add_argument("--rounds", type=int, default=3)
    bench.add_argument("--cpu", type=int, default=1)
    args = p.parse_args()
    if args.op == "setup":
        args.wrappers.mkdir(parents=True, exist_ok=False)
        for path in args.real.iterdir():
            target = Path(__file__).absolute() if path.name == "dvtditr" else path.resolve()
            (args.wrappers / path.name).symlink_to(target)
        return
    args.output.mkdir(parents=True, exist_ok=False)
    builds = [v.split("=", 1) for v in args.build]
    goldens = {}
    with (args.output / "runs.jsonl").open("x", buffering=1) as log:
        for r in range(args.rounds):
            for i, case in enumerate(args.case):
                options = json.loads((case / "argv.json").read_text())
                order = builds if (r + i) % 2 == 0 else builds[::-1]
                for name, prefix in order:
                    dest = args.output / f"{case.name}-{name}-r{r}"
                    work = dest / "input"
                    shutil.copytree(case / "input", work)
                    argv = ["nice", "-n", "10", "taskset", "-c", str(args.cpu),
                            str(Path(prefix) / "libexec/mafft/dvtditr"), *options]
                    before = resource.getrusage(resource.RUSAGE_CHILDREN)
                    start = time.monotonic()
                    # MAFFT feeds pre on stdin; dvtditr writes the refined alignment back
                    # to pre itself (stdout is normally /dev/null in mafft.tmpl).
                    with (dest / "out").open("xb") as out, (dest / "err").open("xb") as err, (work / "pre").open("rb") as inp:
                        result = subprocess.run(argv, cwd=work, stdin=inp, stdout=out, stderr=err,
                                                env={**os.environ, "LC_ALL": "C"}, timeout=1200)
                    wall = time.monotonic() - start
                    after = resource.getrusage(resource.RUSAGE_CHILDREN)
                    output = (work / "pre").read_bytes()
                    goldens.setdefault(case.name, output)
                    ok = result.returncode == 0 and bool(output) and output == goldens[case.name]
                    record = dict(case=case.name, build=name, round=r, argv=argv,
                                  wall=wall, cpu=after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
                                  identical=ok, sha256=hashlib.sha256(output).hexdigest())
                    log.write(json.dumps(record) + "\n")
                    print(f"{case.name:12} {name:16} r{r} CPU={record['cpu']:.4f} wall={wall:.4f} {'OK' if ok else 'FAIL'}", flush=True)
                    if not ok:
                        raise SystemExit(f"stage output mismatch: {dest}")


if __name__ == "__main__":
    if Path(sys.argv[0]).name == "dvtditr":
        capture()
    else:
        main()
