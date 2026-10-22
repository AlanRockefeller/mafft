/* Exact raw-row reuse versus the actual uncached grouped-profile scorer.
 *
 * gcc -O3 -march=native -ffp-contract=off -std=c99 -Denablemultithread \
 *   -ffunction-sections -fdata-sections harness/broadwell/profile_rowcache_check.c \
 *   -Wl,--gc-sections -lm -lpthread -o /private/path/profile_rowcache_check
 *
 * Add ASAN/UBSAN for memory checks.  PAIRSCORE_SOURCE's analogue here can select
 * a scratch copy; this fixture does not require source patching.
 */
#define _POSIX_C_SOURCE 200809L
#define MAFFT_MATCH_ROWCACHE 1
#ifndef PROFILE_ROWCACHE_SOURCE
#define PROFILE_ROWCACHE_SOURCE "../../core/partSalignmm.c"
#endif
#include PROFILE_ROWCACHE_SOURCE
#include <stdint.h>

int nalphabets;
int constants_generation;
double **n_dis_consweight_multi;

void ErrorExit( char *message ) { fprintf( stderr, "%s\n", message ); exit(1); }
void scarr_fill( double *out, double **matrix, double **profile, int column )
{
	int l, k;
	for( l=0; l<nalphabets; l++ )
	{
		out[l] = 0.0;
		for( k=0; k<nalphabets; k++ ) out[l] = MULADD( matrix[k][l], profile[k][column], out[l] );
	}
}
static uint32_t random_state = 3093127;
static uint32_t draw( void )
{
	random_state ^= random_state << 13; random_state ^= random_state >> 17; random_state ^= random_state << 5;
	return random_state;
}
static double value( void )
{
	static const double special[] = {0.0,-0.0,0x1p-1074,-0x1p-1074,0x1p-1022,-0x1p-1022,0x1p40,-0x1p40};
	return draw()%3 ? ( (int)(draw()%2001)-1000 ) / 31.0 : special[draw()%8];
}
static void compare( double *expected, double *actual, int n, int row )
{
	int j;
	for( j=0; j<n; j++ ) if( memcmp( expected+j, actual+j, sizeof(double) ) )
	{
		fprintf( stderr, "ROWCACHE_CHECK: row %d col %d: %.17g vs %.17g\n", row, j, expected[j], actual[j] );
		abort();
	}
}

int main( int argc, char **argv )
{
	int run, l, j, i, passes = argc > 1 ? atoi(argv[1]) : 3000;
	double *p1[26], *p2[26], *matrix[26], *alternative[26], expected[260], actual[260];
	unsigned long long rows = 0, cells = 0, hits = 0, misses = 0;
	for( l=0; l<26; l++ )
	{
		p1[l] = malloc( sizeof(double)*260 ); p2[l] = malloc( sizeof(double)*260 );
		matrix[l] = malloc( sizeof(double)*26 ); alternative[l] = malloc( sizeof(double)*26 );
	}
	for( run=0; run<passes; run++ )
	{
		int n1 = 3+draw()%255, n2 = 1+draw()%257, patterns = 1+draw()%20;
		nalphabets = run%2 ? 4 : 26;
		n_dis_consweight_multi = matrix; constants_generation++;
		for( l=0; l<nalphabets; l++ )
		{
			for( j=0; j<nalphabets; j++ ) { matrix[l][j] = value(); alternative[l][j] = value(); }
			for( j=0; j<n2; j++ ) p2[l][j] = draw()%4 ? 0.0 : value();
			for( i=0; i<n1+1; i++ ) p1[l][i] = i<patterns ? value() : p1[l][i%patterns];
		}
		mc_build( p2, n2 );
		mc_rowcache_prepare( p1, n1, n2 );
		/* Revisit every column, and poison the caller's output after each call.
		 * This models the later per-row importance/DP writes: cached raw rows must
		 * remain immutable and independent of the caller's scratch buffer. */
		for( j=0; j<2; j++ ) for( i=0; i<=n1; i++ )
		{
			mc_match( expected, p1, i, n2 ); mc_match_cached( actual, p1, i, n2 );
			compare( expected, actual, n2, i );
			memset( actual, 0xa5, sizeof(double)*(size_t)n2 );
			rows++; cells += n2;
		}
		hits += mcr_hits; misses += mcr_misses;
		/* The scoring generation, matrix pointer and boundary rows must bypass
		 * the cache when their call context differs. */
		constants_generation++;
		matrix[0][0] = value();
		mc_match( expected, p1, 1, n2 ); mc_match_cached( actual, p1, 1, n2 );
		compare( expected, actual, n2, 1 );
		n_dis_consweight_multi = alternative;
		mc_match( expected, p1, 2, n2 ); mc_match_cached( actual, p1, 2, n2 );
		compare( expected, actual, n2, 2 );
		mc_rowcache_clear();
	}
	/* Signed-zero-distinct keys must remain distinct even when their resulting
	 * raw score happens to be identical. */
	nalphabets = 4; n_dis_consweight_multi = matrix;
	for( l=0; l<4; l++ ) for( i=0; i<5; i++ ) p1[l][i] = i%2 ? 0.0 : -0.0;
	mc_rowcache_prepare( p1, 5, 1 );
	if( !mcr_ids || mcr_ids[1] == mcr_ids[2] || mcr_ids[1] != mcr_ids[3] || mcr_ids[2] != mcr_ids[4] ) abort();
	mc_rowcache_clear();
	for( l=0; l<26; l++ ) { free(p1[l]); free(p2[l]); free(matrix[l]); free(alternative[l]); }
	printf( "ROWCACHE_CHECK: %llu rows, %llu cells, %llu cached hits, %llu misses, all score bits identical\n", rows, cells, hits, misses );
	return 0;
}
