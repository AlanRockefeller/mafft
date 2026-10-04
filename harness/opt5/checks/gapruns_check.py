#!/usr/bin/env python3
"""Per-call check of gapruns (mltaln9.c): vector loop off vs on, n and every st/en bitwise."""
import os, sys
p = os.path.join(sys.argv[1], 'mltaln9.c')
s = open(p, encoding='latin-1').read()
sig = 'static int gapruns( char *s, int len, int *st, int *en )\n{'
assert s.count(sig) == 1
s = s.replace(sig, 'static int gr_vec = 1;\nstatic int gapruns_impl( char *s, int len, int *st, int *en )\n{')
pat = 'for( ; i+64<=len; i+=64 )'
assert s.count(pat) == 1
s = s.replace(pat, 'for( ; gr_vec && i+64<=len; i+=64 )')
wrapper = r'''
static int gapruns( char *s, int len, int *st, int *en )
{
	static long calls = 0;
	int *a = malloc( sizeof( int ) * ( len + 2 ) * 2 ), n0, n1, k;
	gr_vec = 0; n0 = gapruns_impl( s, len, a, a + len + 2 );
	gr_vec = 1; n1 = gapruns_impl( s, len, st, en );
	if( n0 != n1 ) { fprintf( stderr, "GAPRUNS_CHECK: n %d vs %d\n", n0, n1 ); abort(); }
	for( k=0; k<n1; k++ ) if( a[k] != st[k] || a[len+2+k] != en[k] ) { fprintf( stderr, "GAPRUNS_CHECK: run %d differs\n", k ); abort(); }
	free( a );
	if( ( ++calls & ( calls - 1 ) ) == 0 ) fprintf( stderr, "GAPRUNS_CHECK: %ld calls, all identical\n", calls );
	return( n1 );
}
'''
anchor = 'static int *gapruns_buf( int len )'
assert s.count(anchor) == 1
s = s.replace(anchor, wrapper + '\n' + anchor)
open(p, 'w', encoding='latin-1').write(s)
print('patched gapruns')
