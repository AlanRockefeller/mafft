#!/usr/bin/env python3
"""Generate small, deterministic adversarial FASTAs outside the repository."""
import argparse
from pathlib import Path
import random


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--large-only", action="store_true",
                   help="three two-sequence cases around the 256 MiB integer-row fallback; run separately")
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    rng = random.Random(731140)

    def save(name, rows):
        with (a.output / (name + ".fasta")).open("x") as f:
            for i, row in enumerate(rows):
                f.write(f">record_{i} preserved header text {i%3}\n{row}\n")

    if a.large_only:
        for length in (8180, 8190, 8200):
            s = "".join(rng.choice("ACGT") for _ in range(length))
            t = "".join(c if rng.random() > .05 else rng.choice("ACGT") for c in s)
            save(f"rowlimit{length:05}", [s, t])
        return

    for length in (1, 2, 3, 4, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 65,
                   127, 128, 129, 255, 256, 257, 511, 512, 513, 1023, 1800, 1906):
        s = "".join(rng.choice("ACGT") for _ in range(length))
        t = "".join(c if rng.random() > .15 else rng.choice("ACGT") for c in s)
        save(f"edge{length:04}", [s, t, s, t[1:] or t, s, t + "ACGT"])
    save("ordered-ties", ["AC"*90, "CA"*90, "CA"*90, "AC"*90, "AC"*89,
                           "AC"*90, "CA"*90, "AC"*89])
    save("homopolymers", ["A"*n for n in (255, 256, 257, 256, 255, 511)])
    save("ambiguous", ["ACGT" + "NRYWSKMBDHV"*23 + "ACGT",
                        "ACGT" + "N"*253 + "ACGT",
                        "ACGT" + "NRYWSKMBDHV"*23 + "ACGT",
                        "ACGT"*70, "ACGT" + "N"*253 + "ACGT"])
    s = "ACGT"*80
    save("pregapped-duplicates", [s, "-".join(s), s, "ACGT"*79, "ACGT"*79])
    save("opposite-strands", ["AACCG"*80, "CGGTT"*80, "AACCG"*80,
                              "CGGTT"*80, "AACCG"*79 + "TTTTT"])
    save("rna", ["ACGU"*80, "ACGU"*79 + "NNNN", "ACGU"*80, "ACGU"*78])
    save("protein", ["ARNDCQEGHILKMFPSTWYV"*12, "ARNDCQEGHILKMFPSTWYV"*11,
                      "ARNDCQEGHILKMFPSTWYV"*12, "ARNDCQEGHILKMFPSTWYV"*10 + "XXXX"])
    save("empty-local-matches", ["A"*100, "C"*100, "A"*100, "C"*100, "G"*100, "T"*100])


if __name__ == "__main__":
    main()
