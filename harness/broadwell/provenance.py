#!/usr/bin/env python3
"""Record source/binary provenance and verify the rebuilt production rounding class.

Read-only except for the explicitly named new JSON report; no environment secrets collected.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def sections(raw):
    if raw[:6] != b"\x7fELF\x02\x01":
        raise ValueError("expected little-endian ELF64")
    header = struct.unpack_from("<HHIQQQIHHHHHH", raw, 16)
    offset, stride, count, names = header[5], header[10], header[11], header[12]
    table = [struct.unpack_from("<IIQQQQIIQQ", raw, offset + i * stride) for i in range(count)]
    string_section = table[names]
    strings = raw[string_section[4]:string_section[4] + string_section[5]]
    result = {}
    for section in table:
        start = section[0]
        name = strings[start:strings.index(b"\0", start)].decode()
        if name in (".text", ".rodata", ".data"):
            result[name] = digest(raw[section[4]:section[4] + section[5]])
    if set(result) != {".text", ".rodata", ".data"}:
        raise ValueError("missing required ELF sections")
    return result


def binaries(prefix):
    result = {}
    launcher = prefix / "bin" / "mafft"
    result["bin/mafft"] = {"sha256": digest(launcher.read_bytes())}
    for path in sorted((prefix / "libexec" / "mafft").iterdir()):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        record = {"sha256": digest(raw)}
        if raw[:4] == b"\x7fELF":
            record["sections"] = sections(raw)
        result[str(path.relative_to(prefix))] = record
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--reference-prefix", type=Path, required=True)
    p.add_argument("--rebuilt-prefix", type=Path, required=True)
    p.add_argument("--candidate-prefix", type=Path, required=True)
    p.add_argument("--candidate-source", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--golden-manifest", type=Path, required=True)
    p.add_argument("--cflags", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = dict(cflags=a.cflags, manifest_sha256=digest(a.manifest.read_bytes()),
                  golden_manifest_sha256=digest(a.golden_manifest.read_bytes()))
    result["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=a.repo, text=True).strip()
    result["git_status"] = subprocess.check_output(["git", "status", "--short"], cwd=a.repo, text=True)
    result["tracked_diff_sha256"] = digest(subprocess.check_output(["git", "diff", "--binary"], cwd=a.repo))
    result["compiler"] = subprocess.check_output(["gcc", "--version"], text=True).splitlines()[0]
    result["linker"] = subprocess.check_output(["ld", "--version"], text=True).splitlines()[0]
    result["source"] = {}
    for source in sorted((a.repo / "core").iterdir()):
        if not source.is_file() or (source.suffix not in (".c", ".h", ".tmpl") and source.name != "Makefile"):
            continue
        raw = source.read_bytes()
        snapshot = a.candidate_source / "core" / source.name
        if raw != snapshot.read_bytes():
            raise SystemExit(f"candidate source snapshot differs: {source}")
        result["source"][str(source.relative_to(a.repo))] = digest(raw)
    result["builds"] = {name: {"prefix": str(prefix.resolve()), "files": binaries(prefix)}
                        for name, prefix in (("reference", a.reference_prefix),
                                             ("rebuilt", a.rebuilt_prefix),
                                             ("candidate", a.candidate_prefix))}
    reference = result["builds"]["reference"]["files"]
    rebuilt = result["builds"]["rebuilt"]["files"]
    checked = 0
    for name, record in reference.items():
        if "sections" in record:
            if record["sections"] != rebuilt[name]["sections"]:
                raise SystemExit(f"reference and rebuilt executable/data sections differ: {name}")
            checked += 1
    result["reference_rebuild_matching_elfs"] = checked
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open("x") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"{checked} reference/rebuild ELF section sets identical; "
          f"{len(result['source'])} core source files match candidate build snapshot")


if __name__ == "__main__":
    main()
