#!/usr/bin/env python3
"""Check the actual experimental symmetry guard, without building MAFFT.

Example (the output directory must not already exist):
  python3 harness/round2/reject_memo_scores_check.py \
    --source /absolute/frozen/core/tditeration.c --output /absolute/new-check

The helper is extracted unchanged and its source/hash retained. GCC runs a
strict O3 build and then an ASAN/UBSAN build, sequentially. --prepare-only writes
the sources and provenance without invoking the compiler or either executable.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def extract_helper(source):
    matches = list(re.finditer(
        r"(?m)^static\s+int\s+reject_memo_symmetric_scores\s*\(\s*void\s*\)\s*\{",
        source))
    if len(matches) != 1:
        raise ValueError("Expected exactly one reject_memo_symmetric_scores definition")
    start = matches[0].start()
    opening = matches[0].end() - 1
    # Ignore braces inside C comments and literals while retaining exact bytes.
    tokens = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|'
                        r"'(?:\\.|[^'\\])*'|[{}]", re.S)
    depth = 0
    for token in tokens.finditer(source, opening):
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return source[start:token.end()], source.count("\n", 0, start) + 1
    raise ValueError("Unterminated helper definition")


C_DRIVER = r'''
#include <float.h>
#include <inttypes.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

_Static_assert(sizeof(double) == sizeof(uint64_t) && FLT_RADIX == 2 &&
               DBL_MANT_DIG == 53 && DBL_MAX_EXP == 1024, "binary64 required");
enum { N = 128 };
static double storage[N][N];
static double *rows[N];
static double **amino_dis_consweight_multi;
#include "extracted_helper.c"

static uint64_t checks, rng, seed;
static unsigned random_index;

static void expect(const char *name, int expected)
{
    int actual = reject_memo_symmetric_scores();
    ++checks;
    if (actual != expected) {
        fprintf(stderr, "%s: expected %d, got %d (check=%" PRIu64
                ", seed=%" PRIu64 ", random_index=%u)\n",
                name, expected, actual, checks, seed, random_index);
        exit(1);
    }
}

static uint64_t word(void)
{
    uint64_t z = (rng += UINT64_C(0x9e3779b97f4a7c15));
    z = (z ^ (z >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
    z = (z ^ (z >> 27)) * UINT64_C(0x94d049bb133111eb);
    return z ^ (z >> 31);
}

static double finite_value(void)
{
    uint64_t bits = word();
    uint64_t exponent = word() % 2047;
    double value;
    bits = (bits & UINT64_C(0x800fffffffffffff)) | (exponent << 52);
    memcpy(&value, &bits, sizeof(value));
    return value;
}

int main(int argc, char **argv)
{
    int i, j;
    unsigned matrices;
    double saved, specials[] = { INFINITY, -INFINITY, NAN };
    uint64_t bits, changed;
    if (argc != 3) return 2;
    seed = rng = strtoull(argv[1], NULL, 10);
    matrices = (unsigned)strtoul(argv[2], NULL, 10);
    for (i = 0; i < N; ++i) rows[i] = storage[i];
    amino_dis_consweight_multi = rows;
    for (i = 0; i < N; ++i)
        for (j = 0; j <= i; ++j)
            storage[i][j] = storage[j][i] = ((i * 131 + j * 17) % 97 - 48) / 16.0;
    expect("finite symmetric matrix", 1);

    amino_dis_consweight_multi = NULL;
    expect("null matrix", 0);
    amino_dis_consweight_multi = rows;
    expect("matrix restored", 1);
    for (i = 0; i < N; ++i) {
        rows[i] = NULL;
        expect("null row", 0);
        rows[i] = storage[i];
        expect("row restored", 1);
    }

    saved = storage[127][0];
    storage[127][0] = saved + 0.125;
    expect("one-sided finite value", 0);
    storage[127][0] = saved;
    expect("finite value restored", 1);

    saved = storage[9][42];
    storage[9][42] = 0.0;
    storage[42][9] = -0.0;
    expect("opposite zero signs", 0);
    storage[9][42] = -0.0;
    expect("symmetric negative zero", 1);
    storage[9][42] = storage[42][9] = 0.0;
    expect("symmetric positive zero", 1);
    storage[9][42] = storage[42][9] = saved;
    expect("zero pair restored", 1);

    for (i = 0; i < 3; ++i) {
        saved = storage[11][77];
        storage[11][77] = storage[77][11] = specials[i];
        expect("symmetric nonfinite pair", 0);
        storage[11][77] = storage[77][11] = saved;
        expect("nonfinite pair restored", 1);
        saved = storage[39][39];
        storage[39][39] = specials[i];
        expect("nonfinite diagonal", 0);
        storage[39][39] = saved;
        expect("diagonal restored", 1);
    }

    for (random_index = 0; random_index < matrices; ++random_index) {
        for (i = 0; i < N; ++i)
            for (j = 0; j <= i; ++j)
                storage[i][j] = storage[j][i] = finite_value();
        expect("seeded symmetric finite matrix", 1);
        i = (int)(word() % N);
        j = (int)(word() % (N - 1));
        if (j >= i) ++j;
        memcpy(&bits, &storage[i][j], sizeof(bits));
        changed = bits ^ UINT64_C(1); /* Different finite value, unchanged exponent. */
        memcpy(&storage[i][j], &changed, sizeof(changed));
        expect("seeded one-sided mutation", 0);
        memcpy(&storage[i][j], &bits, sizeof(bits));
        expect("seeded value restored", 1);
    }
    printf("{\"status\":\"passed\",\"checks\":%" PRIu64 ",\"seed\":%" PRIu64
           ",\"random_matrices\":%u,\"null_rows_tested\":128}\n", checks, seed, matrices);
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--seed", default=20261020, type=lambda s: int(s, 0))
    parser.add_argument("--random-matrices", default=32, type=int)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.seed < 2**64 or not 0 <= args.random_matrices <= 10000:
        parser.error("seed must fit uint64; random-matrices must be 0..10000")
    source_path = args.source.resolve()
    source = source_path.read_bytes()
    source_sha = hashlib.sha256(source).hexdigest()
    if args.expected_source_sha256 and source_sha != args.expected_source_sha256:
        parser.error("Source SHA-256 does not match --expected-source-sha256")
    # Latin-1 provides an exact one-byte mapping even for non-UTF8 C comments.
    helper, first_line = extract_helper(source.decode("latin-1"))
    helper_bytes = helper.encode("latin-1")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "extracted_helper.c").write_bytes(helper_bytes)
    (output / "check.c").write_text(C_DRIVER, encoding="ascii")
    manifest = {
        "status": "prepared", "source": str(source_path), "source_sha256": source_sha,
        "helper_sha256": hashlib.sha256(helper_bytes).hexdigest(),
        "helper_first_line": first_line, "driver_sha256": hashlib.sha256(
            C_DRIVER.encode("ascii")).hexdigest(),
        "seed": args.seed, "random_matrices": args.random_matrices,
        "command": sys.argv, "builds": [],
    }
    manifest_path = output / "manifest.json"

    def save():
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    save()
    if args.prepare_only:
        print(json.dumps({"status": "prepared", "manifest": str(manifest_path)}))
        return 0
    strict = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-fno-fast-math",
              "-ffp-contract=off", "-fexcess-precision=standard"]
    modes = [
        ("strict", ["-O3", "-g"]),
        ("sanitized", ["-O1", "-g", "-fno-omit-frame-pointer",
                       "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]),
    ]
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=1:halt_on_error=1:abort_on_error=1",
               UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
    try:
        version = subprocess.run([args.cc, "--version"], check=True,
                                 capture_output=True, text=True)
        (output / "compiler-version.txt").write_text(version.stdout + version.stderr)
        for name, flags in modes:
            build = [args.cc, *strict, *flags, str(output / "check.c"),
                     "-lm", "-o", str(output / name)]
            run = [str(output / name), str(args.seed), str(args.random_matrices)]
            record = {"name": name, "build": build, "run": run,
                      "ASAN_OPTIONS": env["ASAN_OPTIONS"],
                      "UBSAN_OPTIONS": env["UBSAN_OPTIONS"]}
            manifest["builds"].append(record)
            save()
            for phase, command in (("build", build), ("run", run)):
                with (output / f"{name}-{phase}.stdout").open("w") as stdout, \
                     (output / f"{name}-{phase}.stderr").open("w") as stderr:
                    subprocess.run(command, check=True, stdout=stdout, stderr=stderr, env=env)
            record["result"] = json.loads((output / f"{name}-run.stdout").read_text())
            if record["result"].get("status") != "passed":
                raise ValueError(f"{name} did not report success")
            save()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        manifest.update(status="failed", error=str(error))
        save()
        print(f"Check failed; inspect {manifest_path}: {error}", file=sys.stderr)
        return 1
    manifest["status"] = "passed"
    save()
    print(json.dumps({"status": "passed", "manifest": str(manifest_path),
                      "results": [build["result"] for build in manifest["builds"]]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
