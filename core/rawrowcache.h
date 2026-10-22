/* Call-scoped exact reuse of raw profile score rows.  Hashes find candidates;
 * equality requires every first-profile double's bits.  Neither importance nor
 * gap/DP state belongs in these immutable rows.  Callers own separate TLS state.
 */
#ifndef MAFFT_RAW_ROWCACHE_H
#define MAFFT_RAW_ROWCACHE_H
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>

typedef struct {
	double **source, **matrix;
	double *rows;
	unsigned char *valid;
	int *ids;
	int nrow, width, slots, generation, alphabet;
	unsigned long long hits, misses, calls;
	const char *label;
} mafft_rowcache;

static void mafft_rowcache_clear( mafft_rowcache *cache )
{
#ifdef MAFFT_MATCH_ROWCACHE_STATS
	if( cache->ids ) fprintf( stderr, "%s: %d x %d, %d slots, %zu bytes, %llu hits, %llu misses, %llu rows\n",
	                         cache->label, cache->nrow, cache->width, cache->slots,
	                         sizeof(double)*(size_t)cache->slots*cache->width + (size_t)cache->slots + sizeof(int)*(size_t)cache->nrow,
	                         cache->hits, cache->misses, cache->calls );
#endif
	free( cache->ids ); free( cache->rows ); free( cache->valid );
	memset( cache, 0, sizeof(*cache) );
}

static void mafft_rowcache_prepare( mafft_rowcache *cache, double **profile,
                                   int nrow, int width, int alphabet,
                                   double **matrix, int generation,
                                   size_t budget, const char *label )
{
	int *ids = NULL, *representative = NULL, *counts = NULL, *slots = NULL, *table = NULL;
	uint64_t *hashes = NULL;
	int row, letter, group, ngroup = 0, nslot = 0, ntab = 1, maxslots;
	mafft_rowcache_clear( cache );
	/* Interior rows only: initialization and terminal boundary rows keep their
	 * original paths.  The metadata guard also prevents indexing overflow. */
	if( sizeof(double) != sizeof(uint64_t) || nrow < 3 || nrow > INT_MAX/4 || width < 1 || alphabet < 1 ||
	    (size_t)nrow > budget/(12*sizeof(int)) ||
	    (size_t)width > budget/sizeof(double) ) return;
	maxslots = budget/sizeof(double)/(size_t)width > (size_t)nrow ? nrow : (int)(budget/sizeof(double)/(size_t)width);
	while( ntab < 2*nrow ) ntab *= 2;
	ids = malloc( sizeof(int)*(size_t)nrow );
	representative = malloc( sizeof(int)*(size_t)nrow );
	counts = calloc( nrow, sizeof(int) );
	slots = malloc( sizeof(int)*(size_t)nrow );
	table = malloc( sizeof(int)*(size_t)ntab );
	hashes = malloc( sizeof(uint64_t)*(size_t)nrow );
	if( !ids || !representative || !counts || !slots || !table || !hashes ) goto done;
	for( row=0; row<ntab; row++ ) table[row] = -1;
	ids[0] = -1;
	for( row=1; row<nrow; row++ )
	{
		uint64_t h = UINT64_C(14695981039346656037);
		int at;
		for( letter=0; letter<alphabet; letter++ )
		{
			uint64_t bits;
			memcpy( &bits, &profile[letter][row], sizeof(bits) );
			h = (h^bits)*UINT64_C(1099511628211);
		}
		/* Binary profile weights often differ only in high mantissa/exponent bits. */
		h ^= h >> 33; h *= UINT64_C(0xff51afd7ed558ccd);
		h ^= h >> 33; h *= UINT64_C(0xc4ceb9fe1a85ec53);
		h ^= h >> 33;
		at = (int)( h & (uint64_t)(ntab-1) );
		for( ;; at=(at+1)&(ntab-1) )
		{
			group = table[at];
			if( group < 0 )
			{
				group = ngroup++;
				table[at] = group; representative[group] = row; hashes[group] = h;
				break;
			}
			if( hashes[group] != h ) continue;
			for( letter=0; letter<alphabet; letter++ )
				if( memcmp( &profile[letter][row], &profile[letter][representative[group]], sizeof(double) ) ) break;
			if( letter == alphabet ) break;
		}
		ids[row] = group; counts[group]++;
	}
	for( group=0; group<ngroup; group++ )
		slots[group] = counts[group] > 1 && nslot < maxslots ? nslot++ : -1;
#ifdef MAFFT_MATCH_ROWCACHE_STATS
	fprintf( stderr, "%s_PREPARE: %d x %d, %d distinct profile columns, %d slots\n", label, nrow, width, ngroup, nslot );
#endif
	if( !nslot ) goto done;
	cache->rows = malloc( sizeof(double)*(size_t)nslot*width );
	cache->valid = calloc( nslot, 1 );
	if( !cache->rows || !cache->valid ) { mafft_rowcache_clear( cache ); goto done; }
	for( row=1; row<nrow; row++ ) ids[row] = slots[ids[row]];
	cache->ids = ids; ids = NULL;
	cache->source = profile; cache->matrix = matrix;
	cache->nrow = nrow; cache->width = width; cache->slots = nslot;
	cache->generation = generation; cache->alphabet = alphabet; cache->label = label;
done:
	free( ids ); free( representative ); free( counts ); free( slots ); free( table ); free( hashes );
}

static int mafft_rowcache_slot( mafft_rowcache *cache, double **profile, double **matrix,
                               int generation, int alphabet, int row, int width )
{
	if( !cache->ids || profile != cache->source || matrix != cache->matrix ||
	    generation != cache->generation || alphabet != cache->alphabet ||
	    width != cache->width || row <= 0 || row >= cache->nrow ) return -1;
	cache->calls++;
	return cache->ids[row];
}

static int mafft_rowcache_load( mafft_rowcache *cache, int slot, double *out )
{
	if( slot < 0 || !cache->valid[slot] ) return 0;
	memcpy( out, cache->rows+(size_t)slot*cache->width, sizeof(double)*(size_t)cache->width );
	cache->hits++;
	return 1;
}

static void mafft_rowcache_store( mafft_rowcache *cache, int slot, double *row )
{
	if( slot < 0 ) return;
	memcpy( cache->rows+(size_t)slot*cache->width, row, sizeof(double)*(size_t)cache->width );
	cache->valid[slot] = 1; cache->misses++;
}
#endif
