#!/usr/bin/env python3
"""Interleaved exact-output benchmarks. All data/results stay outside the source tree.

Builds are NAME=/absolute/prefix/bin/mafft. Commands, hashes, wall and child CPU
times, faults, block I/O and context switches are recorded per run. Every output
is retained. An existing golden is never overwritten; mismatches fail the run.
Use --rounds 1 --freeze with a designated reference to create read-only goldens.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "exact"))
from caserun import run_group  # noqa: E402  (kills the whole process group on timeout)

MODES = {
    "production": [],  # per-input options from the corpus manifest; --threads still explicit
    "auto": ["--auto"],
    "direction-auto": ["--adjustdirection", "--auto"],
    "linsi": ["--localpair", "--maxiterate", "1000"],
    "direction-linsi": ["--adjustdirection", "--localpair", "--maxiterate", "1000"],
    "ginsi": ["--globalpair", "--maxiterate", "1000"],
    "einsi": ["--genafpair", "--maxiterate", "1000"],
    "fftnsi": ["--retree", "2", "--maxiterate", "2"],
    "fftns2": ["--retree", "2", "--maxiterate", "0"],
}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--partition", choices=["all", "development", "holdout", "difficult"], default="all")
    p.add_argument("--files", nargs="*")
    p.add_argument("--build", action="append", required=True)
    p.add_argument("--mode", choices=MODES, default="direction-auto")
    p.add_argument("--threads", type=int, default=1)
    p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--cpu", type=int)
    p.add_argument("--nice", type=int, default=10)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--golden", type=Path)
    p.add_argument("--freeze", action="store_true")
    p.add_argument("--timeout", type=float, default=1800)
    a = p.parse_args()
    if a.freeze and (len(a.build) != 1 or a.rounds != 1 or not a.golden or a.threads != 1):
        p.error("freeze requires one build, one round, one thread and --golden")
    a.output.mkdir(parents=True, exist_ok=True)
    tmp = (a.output / "tmp").resolve()
    tmp.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.pop("MAFFT_BINARIES", None)
    env.update(LC_ALL="C", LANG="C", TMPDIR=str(tmp))
    files = sorted(a.data.glob("*.fasta"))
    manifest = json.loads(a.manifest.read_text()) if a.manifest else None
    options = {s["file"]: s["production_options"] for s in manifest["files"]
               if "production_options" in s} if manifest else {}
    if a.partition != "all":
        if manifest is None:
            p.error("partition selection requires --manifest")
        allowed = {s["file"] for s in manifest["files"] if s["partition"] == a.partition}
        files = [f for f in files if f.name in allowed]
    if a.files is not None:
        files = [f for f in files if f.name in set(a.files)]
    if not files:
        p.error("no selected inputs")
    builds = [x.split("=", 1) for x in a.build]
    # A separate command wrapper controls only benchmark descendants.
    prefix = ["nice", "-n", str(a.nice)]
    if a.cpu is not None:
        prefix += ["taskset", "-c", str(a.cpu)]
    with (a.output / "runs.jsonl").open("x", buffering=1) as log:
        for r in range(a.rounds):
            for n, source in enumerate(files):
                order = builds if (r + n) % 2 == 0 else builds[::-1]
                if len(builds) > 2:
                    # Rotate as well as reverse, so a third build is not always
                    # in the middle. Every build occupies every position.
                    start = (r + n) % len(builds)
                    order = builds[start:] + builds[:start]
                    if ((r + n) // len(builds)) % 2:
                        order = order[::-1]
                for name, binary in order:
                    dest = a.output / name / f"r{r}" / source.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    flags = options[source.name] if a.mode == "production" else MODES[a.mode]
                    argv = prefix + [binary, "--thread", str(a.threads), *flags, str(source.resolve())]
                    before = resource.getrusage(resource.RUSAGE_CHILDREN)
                    started = time.monotonic()
                    with dest.open("xb") as out, dest.with_suffix(".stderr").open("xb") as err:
                        result = run_group(argv, env=env, stdout=out, stderr=err, timeout=a.timeout)
                    elapsed = time.monotonic() - started
                    after = resource.getrusage(resource.RUSAGE_CHILDREN)
                    record = dict(build=name, input=source.name, input_sha256=digest(source),
                                  round=r, mode=a.mode, threads=a.threads, argv=argv,
                                  environment={k: env[k] for k in ("LC_ALL", "LANG", "TMPDIR")},
                                  wall=elapsed, user=after.ru_utime - before.ru_utime,
                                  system=after.ru_stime - before.ru_stime,
                                  children_maxrss_highwater_kib=after.ru_maxrss,
                                  minor_faults=after.ru_minflt - before.ru_minflt,
                                  major_faults=after.ru_majflt - before.ru_majflt,
                                  inblocks=after.ru_inblock - before.ru_inblock,
                                  outblocks=after.ru_oublock - before.ru_oublock,
                                  voluntary_switches=after.ru_nvcsw - before.ru_nvcsw,
                                  involuntary_switches=after.ru_nivcsw - before.ru_nivcsw,
                                  returncode=result["returncode"], timed_out=result["timed_out"],
                                  launch_error=result["launch_error"], output_sha256=digest(dest),
                                  stderr_sha256=digest(dest.with_suffix(".stderr")))
                    record["cpu"] = record["user"] + record["system"]
                    text = dest.with_suffix(".stderr").read_text(errors="replace")
                    record["strategy"] = next((s for s in ("L-INS-i", "FFT-NS-i", "FFT-NS-2", "G-INS-i", "E-INS-i") if s in text), "unknown")
                    ok = result["returncode"] == 0 and not result["timed_out"] and dest.stat().st_size > 0
                    if a.golden:
                        gold = a.golden / f"{a.mode}-t{a.threads}" / source.name
                        if a.freeze:
                            gold.parent.mkdir(parents=True, exist_ok=True)
                            with gold.open("xb") as f:
                                f.write(dest.read_bytes())
                            gold.chmod(0o444)
                        record["golden_sha256"] = digest(gold)
                        record["identical"] = dest.read_bytes() == gold.read_bytes()
                        ok = ok and record["identical"]
                    log.write(json.dumps(record, sort_keys=True) + "\n")
                    print(f"{name:16} r{r} {source.name:14} {elapsed:8.3f}s CPU {record['cpu']:8.3f}s "
                          f"{record['strategy']:10} {'OK' if ok else 'FAIL'}", flush=True)
                    if not ok:
                        raise SystemExit(f"failed: {argv}; retained {dest}")


if __name__ == "__main__":
    main()
