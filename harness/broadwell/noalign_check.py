#!/usr/bin/env python3
"""Patch a scratch Lalign11.c: compare fast score-only calls against the original
double recurrence, in every return-value bit. Run single-threaded; zero/fractional
and out-of-range cases exercise the original fallback. Inputs are not modified.
"""
from pathlib import Path
import sys

p = Path(sys.argv[1])
if p.is_dir():
    p /= "Lalign11.c"
s = p.read_text(encoding="latin-1")
head = "double L__align11_noalign( double **n_dynamicmtx, char **seq1, char **seq2 )"
assert s.count(head) == 1
s = s.replace(head, "static int lns_vec=1;\nstatic long lns_taken=0;\nstatic double Lscore_impl( double **n_dynamicmtx, char **seq1, char **seq2 )")
gate = "if( Lfill_kb( amino_dynamicmtx, n_dynamicmtx, 0.0, seq1[0], seq2[0], lgth1, lgth2, &fastscore, &ei, &ej ) && fastscore != 0.0 )"
assert s.count(gate) == 1
s = s.replace(gate, gate.replace("if( ", "if( lns_vec && ", 1))
s = s.replace("maxwm = fastscore;", "lns_taken++; maxwm = fastscore;")
s += r'''
static long lns_calls=0;
static void lns_report(void) { fprintf(stderr,"NOALIGN_CHECK: %ld calls, %ld fast, all identical\n",lns_calls,lns_taken); }
double L__align11_noalign(double **matrix,char **s1,char **s2)
{
    double a,b;
    if(!s1) return Lscore_impl(matrix,s1,s2);
    if(lns_calls++==0) atexit(lns_report);
    lns_vec=0; a=Lscore_impl(matrix,s1,s2);
    lns_vec=1; b=Lscore_impl(matrix,s1,s2);
    if(memcmp(&a,&b,sizeof(double)))
    { fprintf(stderr,"NOALIGN_CHECK: %.17g vs %.17g (lengths %zu,%zu)\n",a,b,strlen(s1[0]),strlen(s2[0])); abort(); }
    return b;
}
'''
p.write_text(s, encoding="latin-1")
print("patched original-double vs fast score-only calls")
