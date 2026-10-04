#!/usr/bin/env python3
"""Per-call check of commongappick_record (mltaln9.c) against the original column loop: every
byte of every row (0..len) and map[0..count) must match."""
import os, sys
p = os.path.join(sys.argv[1], 'mltaln9.c')
s = open(p, encoding='latin-1').read()
sig = 'void commongappick_record( int nseq, char **seq, int *map )\n{'
assert s.count(sig) == 1
s = s.replace(sig, 'static void commongappick_record_impl( int nseq, char **seq, int *map )\n{')
wrapper = r'''
void commongappick_record( int nseq, char **seq, int *map )
{
	static long calls = 0, dropped = 0;
	int len = strlen( seq[0] ), i, j, count;
	char **c = malloc( sizeof( char * ) * nseq ), *keep = calloc( len + 1, 1 );
	int *m0 = malloc( sizeof( int ) * ( len + 1 ) );
	for( j=0; j<nseq; j++ ) { c[j] = malloc( len + 1 ); memcpy( c[j], seq[j], len + 1 ); }
	/* the original */
	for( i=0; i<len; i++ ) { for( j=0; j<nseq; j++ ) if( c[j][i] != '-' ) break; keep[i] = ( j < nseq ); }
	keep[len] = 1;
	for( i=0, count=0; i<=len; i++ ) if( keep[i] ) m0[count++] = i;
	for( j=0; j<nseq; j++ ) for( i=0; i<count; i++ ) c[j][i] = c[j][m0[i]];
	commongappick_record_impl( nseq, seq, map );
	for( i=0; i<count; i++ ) if( map[i] != m0[i] ) { fprintf( stderr, "CGP_CHECK: map[%d]\n", i ); abort(); }
	for( j=0; j<nseq; j++ ) if( memcmp( c[j], seq[j], len + 1 ) ) { fprintf( stderr, "CGP_CHECK: row %d\n", j ); abort(); }
	if( count < len + 1 ) dropped++;
	for( j=0; j<nseq; j++ ) free( c[j] );
	free( c ); free( keep ); free( m0 );
	if( ( ++calls & ( calls - 1 ) ) == 0 ) fprintf( stderr, "CGP_CHECK: %ld calls (%ld dropping columns), all identical\n", calls, dropped );
}
'''
anchor = '#if 0\nvoid commongaprecord('
assert s.count(anchor) == 1
s = s.replace(anchor, wrapper + '\n' + anchor)
open(p, 'w', encoding='latin-1').write(s)
print('patched commongappick_record')
