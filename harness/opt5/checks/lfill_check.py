#!/usr/bin/env python3
"""Turn Lalign11.c into a per-call check of the vector Lfill_int against its scalar path.

Every call runs Lfill_int twice: vector loops off (scalar tail does every cell) into a scratch
ijp, then vector loops on into the real ijp.  Return value, maxwm, end point and every ijp cell
written (rows 1..lgth1, cols 1..lgth2) must match bit for bit, else abort.
Usage: lfill_check.py Lalign11.c  (rewrites in place)
"""
import re, sys

p = sys.argv[1]
import os
if os.path.isdir(p): p = os.path.join(p, 'Lalign11.c')
s = open(p, encoding='latin-1').read()

s = s.replace('static int Lfill_int( double **amino_dynamicmtx,',
              'static int Lfill_vec = 1;\nstatic int Lfill_int_impl( double **amino_dynamicmtx,', 1)
n = 0
for pat in ('for( ; j+3<=lgth2; j+=4 )', 'for( ; j+15<=lgth2; j+=16 )'):
    c = s.count(pat)
    s = s.replace(pat, pat.replace('for( ; ', 'for( ; Lfill_vec && '))
    n += c
assert n >= 2, n

wrapper = r'''
static long Lfill_calls = 0, Lfill_cells = 0;
static void Lfill_report( void ) { fprintf( stderr, "LFILL_CHECK: %ld calls, %ld cells, all identical\n", Lfill_calls, Lfill_cells ); }
static int Lfill_int( double **amino_dynamicmtx, double **n_dynamicmtx, double scoreoffset,
                      char *s1, char *s2, int lgth1, int lgth2, int **ijp, int lstop,
                      double *maxwmpt, int *endalipt, int *endaljpt )
{
	int i, r0, r1, ei0, ej0, ei1, ej1;
	double m0, m1;
	int **sc = malloc( sizeof( int * ) * ( lgth1 + 1 ) );
	if( Lfill_calls++ == 0 ) atexit( Lfill_report );
	for( i=0; i<=lgth1; i++ ) sc[i] = calloc( lgth2 + 1, sizeof( int ) );
	Lfill_vec = 0;
	r0 = Lfill_int_impl( amino_dynamicmtx, n_dynamicmtx, scoreoffset, s1, s2, lgth1, lgth2, sc, lstop, &m0, &ei0, &ej0 );
	Lfill_vec = 1;
	r1 = Lfill_int_impl( amino_dynamicmtx, n_dynamicmtx, scoreoffset, s1, s2, lgth1, lgth2, ijp, lstop, &m1, &ei1, &ej1 );
	if( r0 != r1 ) { fprintf( stderr, "LFILL_CHECK: return %d vs %d\n", r0, r1 ); abort(); }
	if( r1 )
	{
		int j;
		if( memcmp( &m0, &m1, sizeof( double ) ) || ei0 != ei1 || ej0 != ej1 )
		{ fprintf( stderr, "LFILL_CHECK: max %f/%f end %d,%d vs %d,%d\n", m0, m1, ei0, ej0, ei1, ej1 ); abort(); }
		for( i=1; i<=lgth1; i++ ) for( j=1; j<=lgth2; j++ )
			if( sc[i][j] != ijp[i][j] )
			{ fprintf( stderr, "LFILL_CHECK: ijp[%d][%d] %d vs %d (lgth %d,%d)\n", i, j, sc[i][j], ijp[i][j], lgth1, lgth2 ); abort(); }
		*maxwmpt = m1; *endalipt = ei1; *endaljpt = ej1;
		Lfill_cells += (long)lgth1 * lgth2;
	}
	for( i=0; i<=lgth1; i++ ) free( sc[i] );
	free( sc );
	return( r1 );
}
'''
# insert the wrapper just before L__align11's definition
anchor = 'double L__align11( double **n_dynamicmtx, double scoreoffset,'
assert s.count(anchor) == 1
s = s.replace(anchor, wrapper + '\n' + anchor)
open(p, 'w', encoding='latin-1').write(s)
print('patched %d vector loops' % n)
