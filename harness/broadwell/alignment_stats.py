#!/usr/bin/env python3
"""Summarize golden alignment widths and gaps without exporting residues or headers."""
import argparse
import collections
import json
from pathlib import Path
from corpus import fasta, quantiles

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("input", type=Path)
p.add_argument("output", type=Path)
a = p.parse_args()
rows = []
for path in sorted(a.input.glob("*.fasta")):
    _, seq = fasta(path)
    widths = {len(s) for s in seq}
    if len(widths) != 1:
        raise ValueError(f"{path}: nonrectangular alignment")
    width = widths.pop()
    gaps = sum(s.count("-") for s in seq)
    terminal = sum(len(s)-len(s.lstrip("-"))+len(s)-len(s.rstrip("-")) for s in seq)
    run_lengths = []
    for s in seq:
        start = None
        for i, c in enumerate(s + "X"):
            if c == "-" and start is None:
                start = i
            elif c != "-" and start is not None:
                run_lengths.append(i - start)
                start = None
    rows.append(dict(file=path.name, sequences=len(seq), width=width, gaps=gaps,
                     cells=width*len(seq), gap_fraction=gaps/(width*len(seq)),
                     terminal_gaps=terminal, run_lengths=quantiles(run_lengths) if run_lengths else {}))
summary = dict(widths=quantiles([r["width"] for r in rows]),
               gap_fraction=sum(r["gaps"] for r in rows)/sum(r["cells"] for r in rows),
               per_input_gap_fraction=quantiles([r["gap_fraction"] for r in rows]),
               terminal_fraction_of_gaps=sum(r["terminal_gaps"] for r in rows)/sum(r["gaps"] for r in rows))
with a.output.open("x") as f:
    json.dump(dict(summary=summary, files=rows), f, indent=2)
    f.write("\n")
print(json.dumps(summary, indent=2))
