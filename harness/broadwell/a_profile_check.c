/* Ordinary A__align raw-row cache and stack scarr versus an actual frozen
 * match_calc extracted by a_match_reference.py. No real MAFFT execution needed.
 *
 * python3 harness/broadwell/a_match_reference.py FROZEN_CORE /private/a_reference.h
 * gcc -O3 -march=native -ffp-contract=off -std=c99 -Denablemultithread \
 *   -ffunction-sections -fdata-sections -DA_PROFILE_REFERENCE='"/private/a_reference.h"' \
 *   harness/broadwell/a_profile_check.c -Wl,--gc-sections -lm -lpthread \
 *   -o /private/a_profile_check
 *
 * Add -fsanitize=address,undefined -fno-omit-frame-pointer for memory checks.
 */
#define _POSIX_C_SOURCE 200809L
#ifndef MAFFT_A_MATCH_ROWCACHE
#define MAFFT_A_MATCH_ROWCACHE 1
#endif
#ifndef MAFFT_A_SCARR_STACK
#define MAFFT_A_SCARR_STACK 1
#endif
#ifndef A_PROFILE_SOURCE
#define A_PROFILE_SOURCE "../../core/Salignmm.c"
#endif
#ifndef A_PROFILE_REFERENCE
#error Extract and define A_PROFILE_REFERENCE from the frozen source
#endif
#include A_PROFILE_SOURCE
#include A_PROFILE_REFERENCE
#include <stdint.h>

int nalphabets;
int constants_generation;
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
static uint32_t random_state = 709309;
static uint32_t draw( void )
{
	random_state ^= random_state << 13; random_state ^= random_state >> 17; random_state ^= random_state << 5;
	return random_state;
}
static double value( void )
{
	static const double special[] = {0.0,-0.0,0x1p-1074,-0x1p-1074,0x1p-1022,-0x1p-1022,0x1p40,-0x1p40,1e50,-1e50};
	return draw()%3 ? ( (int)(draw()%2001)-1000 ) / 31.0 : special[draw()%10];
}
static void compare( double *expected, double *actual, int n, int row )
{
	int j;
	for( j=0; j<n; j++ ) if( memcmp( expected+j, actual+j, sizeof(double) ) )
	{
		fprintf( stderr, "A_PROFILE_CHECK: row %d col %d: %.17g vs %.17g\n", row, j, expected[j], actual[j] );
		abort();
	}
	if( actual[n] != 0x1.23456789abcp40 ) abort();
}

int main( int argc, char **argv )
{
	int run, l, j, i, passes = argc > 1 ? atoi(argv[1]) : 1000;
	double *p1[256], *p2[256], *matrix[256], *alternative[256], *work0[260], *work1[260];
	int *index0[260], *index1[260];
	double expected[260], actual[260];
	unsigned long long rows = 0, cells = 0, hits = 0, misses = 0;
	for( l=0; l<256; l++ )
	{
		p1[l] = malloc( sizeof(double)*260 ); p2[l] = malloc( sizeof(double)*260 );
		matrix[l] = malloc( sizeof(double)*256 ); alternative[l] = malloc( sizeof(double)*256 );
		for( j=0; j<256; j++ ) { matrix[l][j] = value(); alternative[l][j] = value(); }
	}
	for( j=0; j<260; j++ )
	{
		work0[j] = malloc( sizeof(double)*257 ); work1[j] = malloc( sizeof(double)*257 );
		index0[j] = malloc( sizeof(int)*257 ); index1[j] = malloc( sizeof(int)*257 );
	}
	for( run=0; run<passes; run++ )
	{
		static const int alphabets[] = {4,10,26,33,64,128,256};
		int n1 = run<4 ? run : 3+draw()%255, n2 = draw()%258, patterns = 1+draw()%20;
		nalphabets = run < 7 ? alphabets[run] : run%2 ? 4 : 26;
		constants_generation++;
		for( l=0; l<nalphabets; l++ )
		{
			for( j=0; j<=n2; j++ ) p2[l][j] = draw()%4 ? 0.0 : value();
			for( i=0; i<=n1; i++ ) p1[l][i] = i<patterns ? value() : p1[l][i%patterns];
		}
		/* Both initialization passes must clear old cache state and retain the
		 * exact original sparse-column setup and row arithmetic. */
		actual[n1] = 0x1.23456789abcp40;
		a_original_match_calc( matrix, expected, p2, p1, 0, n1, work0, index0, 1 );
		match_calc( matrix, actual, p2, p1, 0, n1, work1, index1, 1 );
		compare( expected, actual, n1, 0 );
		actual[n2] = 0x1.23456789abcp40;
		a_original_match_calc( matrix, expected, p1, p2, 0, n2, work0, index0, 1 );
		match_calc( matrix, actual, p1, p2, 0, n2, work1, index1, 1 );
		compare( expected, actual, n2, 0 );
#if MAFFT_A_MATCH_ROWCACHE
		mafft_rowcache_prepare( &amc_cache, p1, n1, n2, nalphabets, matrix, constants_generation, 8388608, "A_MATCH_ROWCACHE" );
#endif
		for( j=0; j<2; j++ ) for( i=0; i<=n1; i++ )
		{
			actual[n2] = 0x1.23456789abcp40;
			a_original_match_calc( matrix, expected, p1, p2, i, n2, work0, index0, 0 );
			match_calc( matrix, actual, p1, p2, i, n2, work1, index1, 0 );
			compare( expected, actual, n2, i );
			memset( actual, 0xa5, sizeof(double)*(size_t)n2 );
			rows++; cells += n2;
		}
#if MAFFT_A_MATCH_ROWCACHE
		hits += amc_cache.hits; misses += amc_cache.misses;
#endif
		/* A different dynamic matrix or generation must bypass entries. */
		constants_generation++;
		matrix[0][0] = value();
#if MAFFT_A_MATCH_ROWCACHE
		if( mafft_rowcache_slot( &amc_cache, p1, matrix, constants_generation, nalphabets, 1, n2 ) != -1 ) abort();
		if( mafft_rowcache_slot( &amc_cache, p1, alternative, amc_cache.generation, nalphabets, 1, n2 ) != -1 ) abort();
#endif
		actual[n2] = 0x1.23456789abcp40;
		i = n1 > 1 ? 1 : 0;
		a_original_match_calc( matrix, expected, p1, p2, i, n2, work0, index0, 0 );
		match_calc( matrix, actual, p1, p2, i, n2, work1, index1, 0 );
		compare( expected, actual, n2, i );
		actual[n2] = 0x1.23456789abcp40;
		a_original_match_calc( alternative, expected, p1, p2, i, n2, work0, index0, 0 );
		match_calc( alternative, actual, p1, p2, i, n2, work1, index1, 0 );
		compare( expected, actual, n2, i );
/* Reuse-key tests are separate from raw-score equality, which can hide a wrong
 * merge of +0.0 and -0.0 profile keys. */
#if MAFFT_A_MATCH_ROWCACHE
		mafft_rowcache_clear( &amc_cache );
#endif
	}
#if MAFFT_A_MATCH_ROWCACHE
	for( l=0; l<4; l++ ) for( i=0; i<5; i++ ) p1[l][i] = i%2 ? 0.0 : -0.0;
	mafft_rowcache_prepare( &amc_cache, p1, 5, 1, 4, matrix, constants_generation, 8388608, "A_MATCH_ROWCACHE" );
	if( !amc_cache.ids || amc_cache.ids[1] == amc_cache.ids[2] || amc_cache.ids[1] != amc_cache.ids[3] || amc_cache.ids[2] != amc_cache.ids[4] ) abort();
	mafft_rowcache_clear( &amc_cache );
#endif
	for( l=0; l<256; l++ ) { free(p1[l]); free(p2[l]); free(matrix[l]); free(alternative[l]); }
	for( j=0; j<260; j++ ) { free(work0[j]); free(work1[j]); free(index0[j]); free(index1[j]); }
	printf( "A_PROFILE_CHECK: %llu rows, %llu cells, %llu hits, %llu misses, all score bits identical\n", rows, cells, hits, misses );
	return 0;
}
