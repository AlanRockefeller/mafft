#!/usr/bin/env python3
"""Extract the actual original Salignmm.c match_calc into a private test header.

Usage: a_match_reference.py FROZEN_CORE OUTPUT_HEADER
The input must be a frozen source without the new ordinary row-cache wrapper.
No source file is patched. The only code change in the extraction is its name.
"""
from pathlib import Path
import hashlib
import re
import sys

source = Path(sys.argv[1])
if source.is_dir():
    source /= "Salignmm.c"
output = Path(sys.argv[2])
text = source.read_text(encoding="latin-1")
signature = "static void match_calc( double **n_dynamicmtx, double *match, double **cpmx1, double **cpmx2, int i1, int lgth2, double **doublework, int **intwork, int initialize )"
assert text.count(signature) == 1
assert "MAFFT_A_MATCH_ROWCACHE" not in text, "reference must precede the ordinary cache"
start = text.index(signature)
# Replace comments/literals with equal-length spaces before balancing braces.
clean = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
               lambda match: " " * len(match[0]), text, flags=re.S)
at = clean.index("{", start)
depth = 1
end = at + 1
while depth:
    depth += (clean[end] == "{") - (clean[end] == "}")
    end += 1
body = text[start:end].replace("match_calc(", "a_original_match_calc(", 1)
digest = hashlib.sha256(source.read_bytes()).hexdigest()
with output.open("x", encoding="latin-1") as handle:
    handle.write(f"/* Frozen original: {source}\n * SHA-256: {digest}\n */\n" + body + "\n")
print(f"extracted actual original match_calc from {source} ({digest})")
