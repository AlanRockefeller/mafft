#!/usr/bin/env python3
"""Compare additions to a frozen reference alignment. Keep fixtures/results outside Git."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "exact"))
from caserun import run_group  # noqa: E402  (kills the whole process group on timeout)

from corpus import fasta

MODES = {
    "add": ["--auto", "--add"],
    "add-local": ["--localpair", "--maxiterate", "0", "--add"],
    "fragments": ["--auto", "--addfragments"],
    "long": ["--auto", "--addlong"],
    "full": ["--auto", "--addfull"],
    "add-keeplength": ["--auto", "--keeplength", "--add"],
    "fragments-keeplength": ["--auto", "--keeplength", "--addfragments"],
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--files", nargs="+", required=True)
    p.add_argument("--cpu", default="1")
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    tmp = (a.output / "tmp").resolve()
    tmp.mkdir()
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "TMPDIR": str(tmp)}
    env.pop("MAFFT_BINARIES", None)

    def run(binary, flags, dest):
        argv = ["nice", "-n", "15", "taskset", "-c", a.cpu, str(binary.resolve()),
                "--thread", "1", *map(str, flags)]
        with dest.open("xb") as out, dest.with_suffix(".stderr").open("xb") as err:
            r = run_group(argv, stdout=out, stderr=err, env=env, timeout=600)
        if r["timed_out"] or r["launch_error"]:
            raise SystemExit(f"timeout or launch failure: {argv}; retained {dest}")
        value = dest.read_bytes()
        return dict(argv=argv, returncode=r["returncode"], bytes=len(value),
                    sha256=hashlib.sha256(value).hexdigest())

    with (a.output / "runs.jsonl").open("x", buffering=1) as log:
        for name in a.files:
            headers, seqs = fasta(a.data / name)
            if len(seqs) < 4:
                p.error(f"need at least four sequences: {name}")
            dest = (a.output / name).resolve()
            dest.mkdir()
            seed, query = dest / "seed.fasta", dest / "query.fasta"
            for path, indices in ((seed, range(3)), (query, range(3, len(seqs)))):
                with path.open("x") as f:
                    for i in indices:
                        f.write(headers[i] + "\n" + seqs[i].replace("-", "").replace(".", "") + "\n")
            aligned = dest / "seed-aligned.fasta"
            seed_ref = run(a.reference, ["--auto", seed], aligned)
            seed_new = run(a.candidate, ["--auto", seed], dest / "seed-new.fasta")
            if (seed_ref["returncode"] or seed_new["returncode"] or not aligned.stat().st_size
                    or aligned.read_bytes() != (dest / "seed-new.fasta").read_bytes()):
                raise SystemExit(f"seed mismatch: {dest}")
            for mode, flags in MODES.items():
                ref = dest / (mode + "-ref.fasta")
                new = dest / (mode + "-new.fasta")
                results = [run(binary, [*flags, query, aligned], out)
                           for binary, out in ((a.reference, ref), (a.candidate, new))]
                ok = (all(r["returncode"] == 0 and r["bytes"] for r in results)
                      and ref.read_bytes() == new.read_bytes())
                log.write(json.dumps(dict(input=name, mode=mode, identical=ok, builds=results)) + "\n")
                print(f"{mode:24} {name:28} {'OK' if ok else 'FAIL'}", flush=True)
                if not ok:
                    raise SystemExit(f"placement mismatch/rejection: {dest}, {mode}")


if __name__ == "__main__":
    main()
