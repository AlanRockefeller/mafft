/* Differential test for direct pair scoring against an independently buffered
 * reference.  Compile without fast math or contraction; link-time garbage
 * collection drops unrelated mltaln9.c functions.  See pairscore_check.py.
 *
 * gcc -O3 -march=native -ffp-contract=off -std=c99 -Denablemultithread \
 *   -ffunction-sections -fdata-sections harness/broadwell/pairscore_check.c \
 *   -Wl,--gc-sections -lm -lpthread -o /private/path/pairscore_check
 */
#ifndef PAIRSCORE_SOURCE
#define PAIRSCORE_SOURCE "../../core/mltaln9.c"
#endif
#include PAIRSCORE_SOURCE
#include <stdint.h>
#include <limits.h>

int **amino_dis;
int amino_n[256];
static uint32_t state = 269729;
static unsigned long long calls, columns;

static uint32_t random32( void )
{
	state ^= state << 13; state ^= state >> 17; state ^= state << 5;
	return state;
}

/* The old algorithm materializes common-gap-free strings, then skips whole
 * gap runs.  This deliberately has a different control structure from the
 * direct scorer and does not consult its skip-table traversal or gap state. */
static double buffered( const char *seq1, const char *seq2, int penal, double **matrix )
{
	size_t len = strlen( seq1 ), n = 0, j;
	unsigned char *s1 = malloc( len+1 ), *s2 = malloc( len+1 );
	double score = 0.0;
	for( j=0; j<len; j++ )
		if( seq1[j] != '-' || seq2[j] != '-' )
		{
			s1[n] = (unsigned char)seq1[j]; s2[n++] = (unsigned char)seq2[j];
		}
	s1[n] = s2[n] = 0;
	for( j=0; j<n; )
	{
		if( s1[j] == '-' )
		{
			score += (double)penal;
			do { j++; } while( s1[j] == '-' );
		}
		else if( s2[j] == '-' )
		{
			score += (double)penal;
			do { j++; } while( s2[j] == '-' );
		}
		else
		{
			if( matrix ) score += matrix[amino_n[s1[j]]][amino_n[s2[j]]];
			else score += (double)amino_dis[s1[j]][s2[j]];
			j++;
		}
	}
	free( s1 ); free( s2 );
	return score;
}

static void checkbits( double a, double b, const char *which, const char *s1, const char *s2 )
{
	if( memcmp( &a, &b, sizeof( a ) ) )
	{
		uint64_t aa, bb;
		memcpy( &aa, &a, sizeof( aa ) ); memcpy( &bb, &b, sizeof( bb ) );
		fprintf( stderr, "%s: %.17g vs %.17g (%016llx vs %016llx), length %zu\n",
		         which, a, b, (unsigned long long)aa, (unsigned long long)bb, strlen( s1 ) );
		fprintf( stderr, "%s\n%s\n", s1, s2 );
		abort();
	}
}

static void check_pair( char *s1, char *s2, int penal, double **matrix )
{
	size_t i, n1 = 0, n2 = 0, len = strlen( s1 );
	int *skip1 = calloc( len+1, sizeof( int ) ), *skip2 = calloc( len+1, sizeof( int ) );
	double expected;
	for( i=0; i<len; i++ )
	{
		if( s1[i] == '-' ) skip1[n1]++; else n1++;
		if( s2[i] == '-' ) skip2[n2]++; else n2++;
	}
	expected = buffered( s1, s2, penal, NULL );
	checkbits( expected, naivepairscorefast( s1, s2, skip1, skip2, penal ), "skip-table", s1, s2 );
	checkbits( expected, naivepairscore11( s1, s2, penal ), "plain", s1, s2 );
	expected = buffered( s1, s2, penal, matrix );
	checkbits( expected, naivepairscore11_dynmtx( matrix, s1, s2, penal ), "dynamic matrix", s1, s2 );
	free( skip1 ); free( skip2 );
	calls++; columns += len;
}

int main( int argc, char **argv )
{
	static const unsigned char alphabet[] = "ACGTNRYacgtnryBZX*?";
	static const size_t boundaries[] = {0,1,2,3,4,7,8,9,15,16,17,31,32,33,63,64,65,127,128,129,255,256,257,511,512,513,1023,1024,1025,2048,2049};
	static const double special[] = {0.0,-0.0,0x1p-1074,-0x1p-1074,0x1p-1022,-0x1p-1022,0x1p53,-0x1p53,1e200,-1e200};
	int r, i, j, passes = argc > 1 ? atoi( argv[1] ) : 30000;
	double **matrix = malloc( 256 * sizeof( double * ) );
	char s1[2050], s2[2050];
	amino_dis = malloc( 256 * sizeof( int * ) );
	for( i=0; i<256; i++ )
	{
		amino_n[i] = ( i * 73 ) & 255; /* Nontrivial reversible character mapping. */
		amino_dis[i] = malloc( 256 * sizeof( int ) );
		matrix[i] = malloc( 256 * sizeof( double ) );
		for( j=0; j<256; j++ )
		{
			amino_dis[i][j] = (int)( random32()%2000000001 ) - 1000000000;
			matrix[i][j] = random32()%3 ? ( (int)(random32()%2001)-1000 ) / 17.0 : special[random32()%10];
		}
	}
	/* Exhaust all four pair-column types through length eight, including gap
 * switches, common gaps within runs, all-gap and empty alignments. */
	for( i=0; i<=8; i++ )
	{
		unsigned int pattern, count = 1u << ( 2*i );
		for( pattern=0; pattern<count; pattern++ )
		{
			unsigned int bits = pattern;
			for( j=0; j<i; j++, bits>>=2 )
			{
				s1[j] = (bits&1) ? '-' : 'A'; s2[j] = (bits&2) ? '-' : 'C';
			}
			s1[i] = s2[i] = 0;
			check_pair( s1, s2, (pattern&1) ? INT_MIN : INT_MAX, matrix );
		}
	}
	for( r=0; r<passes; r++ )
	{
		size_t len = r < (int)(sizeof(boundaries)/sizeof(*boundaries)) ? boundaries[r] : random32()%2050;
		unsigned int gaprate = random32()%101;
		for( j=0; j<(int)len; j++ )
		{
			/* Independent and correlated gap blocks, with sparse ambiguity and
			 * arbitrary nonzero byte alphabets in every fifth replicate. */
			s1[j] = random32()%100 < gaprate ? '-' : r%5 ? alphabet[random32()%(sizeof(alphabet)-1)] : 1+random32()%255;
			s2[j] = random32()%100 < gaprate ? '-' : r%5 ? alphabet[random32()%(sizeof(alphabet)-1)] : 1+random32()%255;
			if( r%7 == 0 && j%53 < 29 ) s1[j] = s2[j] = '-';
		}
		s1[len] = s2[len] = 0;
		check_pair( s1, s2, (int)(random32()%2000000001)-1000000000, matrix );
		check_pair( s2, s1, 0, matrix );
	}
	printf( "PAIRSCORE_CHECK: %llu pairs, %llu columns, all three scorers identical in every return-value bit\n", calls, columns );
	for( i=0; i<256; i++ ) { free( amino_dis[i] ); free( matrix[i] ); }
	free( amino_dis ); free( matrix );
	return 0;
}
