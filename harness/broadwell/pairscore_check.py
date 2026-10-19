#!/usr/bin/env python3
"""Patch a scratch mltaln9.c to check direct scorers against their buffered originals.

Run the checking build single-threaded.  It compares every returned score in all
64 bits, retaining floating-point addition order and gap-run semantics, including
dynamic matrices.  No input or skip table is changed by either implementation.
Use pairscore_check.c for independent exhaustive/randomized tests and ASAN/UBSAN.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
if path.is_dir():
    path /= "mltaln9.c"
source = path.read_text(encoding="latin-1")
specs = (
    ("naivepairscorefast", "char *seq1, char *seq2, int *skip1, int *skip2, int penal", "seq1,seq2,skip1,skip2,penal"),
    ("naivepairscore11_dynmtx", "double **mtx, char *seq1, char *seq2, int penal", "mtx,seq1,seq2,penal"),
    ("naivepairscore11", "char *seq1, char *seq2, int penal", "seq1,seq2,penal"),
)
source += "\nstatic unsigned long long pairscore_check_calls, pairscore_check_columns;\n"
source += r'''
static void pairscore_check_report(void)
{
    fprintf(stderr,"PAIRSCORE_CHECK: %llu calls, %llu columns, all identical\n",
            pairscore_check_calls,pairscore_check_columns);
}
'''
for name, parameters, arguments in specs:
    head = f"double {name}( {parameters} )\n{{\n"
    assert source.count(head) == 1, name
    start = source.index(head)
    gate = start + len(head)
    assert source[gate:].startswith("#if MAFFT_DIRECT_PAIRSCORE\n"), name
    middle = source.index("#else\n", gate)
    end = source.index("#endif\n}", middle) + len("#endif\n}")
    original = source[middle + len("#else\n"):end - len("#endif\n}")]
    implementation = source[start:end].replace(head, f"static double {name}_direct( {parameters} )\n{{\n", 1)
    reference = f"static double {name}_buffered( {parameters} )\n{{\n" + original + "}\n"
    source = source[:start] + reference + implementation + source[end:]
    source += f'''
double {name}({parameters})
{{
    double a={name}_buffered({arguments}), b={name}_direct({arguments});
    if(pairscore_check_calls++==0) atexit(pairscore_check_report);
    pairscore_check_columns+=strlen(seq1);
    if(memcmp(&a,&b,sizeof(double)))
    {{
        fprintf(stderr,"PAIRSCORE_CHECK {name}: %.17g vs %.17g, width %zu\\n",a,b,strlen(seq1));
        abort();
    }}
    return b;
}}
'''
path.write_text(source, encoding="latin-1")
print("patched direct vs buffered pair scorers (single-threaded checking only)")
