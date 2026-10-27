#!/usr/bin/env python3
"""Regenerate test/exact/*.fa from test/exact/recipes.txt and the edge cases below.

Simulated families come from simseq.py (seed-fixed).  The edge cases are
written literally.  Run with --check to verify the committed files instead.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parents[1] / 'test' / 'exact'

EDGE = {
    # one sequence
    'one.fa': '>only\nACGTACGTTAGCTAGCTAGGATCCGATCGATCGTAGCTAGCTAGCATCGAT\n',
    # one- and two-residue sequences among short ones
    'tiny.fa': '>t1\nA\n>t2\nAC\n>t3\nG\n>t4\nTT\n>t5\nACG\n>t6\nCA\n',
    # pre-gapped RNA-style input with U and gap columns
    'gappedu.fa': ('>g1\nAUGC-UAGCUAGG--CUAGCUAGCAUCG-AUCGAUGCUAGCUA\n'
                   '>g2\nAUGCAUAGCU-GGAUCUAGCUAGCAUCGGAUCGAUGCUAG-UA\n'
                   '>g3\nAUGC-UAGCUAGGAUCUAG--AGCAUCGGAUCGAUGCUAGCUA\n'
                   '>g4\n--GCAUAGCUAGGAUCUAGCUAGCAUCGGAUC-AUGCUAGCUA\n'
                   '>g5\nAUGCAUAGCUAGGAUCUAGCUAGCAUCGGAUCGAUGCUAGC--\n'),
}


def generate():
    out = {}
    for line in (FIXTURES / 'recipes.txt').read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        name, *args = line.split()
        out[name + '.fa'] = subprocess.run([sys.executable, str(HERE / 'simseq.py'), *args],
                                           check=True, capture_output=True, text=True).stdout
    out.update(EDGE)
    return out


def main():
    check = '--check' in sys.argv[1:]
    bad = 0
    for name, text in generate().items():
        path = FIXTURES / name
        if check:
            if not path.exists() or path.read_text() != text:
                print('differs:', path)
                bad += 1
        else:
            path.write_text(text)
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
