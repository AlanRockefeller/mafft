/* Exact all-pairs six-mer multiset intersection for the finite DNA alphabet.
 * This replaces a write-heavy per-pair histogram with immutable byte counts.
 * Counts are checked before narrowing. Any unsupported shape, overflow, memory
 * cap or allocation failure keeps the original scalar point-list algorithm.
 * Only integer intersection counts are replaced; distance arithmetic and taxon
 * order stay with the caller. Storage is local to one distance-matrix stage.
 */
#ifndef MAFFT_DENSE_KMER
#define MAFFT_DENSE_KMER 0
#endif
#include <stdint.h>
#if MAFFT_DENSE_KMER && defined(__AVX2__)
#include <immintrin.h>
#define KD_WIDTH 4096
struct kd_dense { void *allocation; unsigned char *rows; };
static struct kd_dense kd_prepare(int **points, int n, int table_size, int tuple_size, int dna)
{
    struct kd_dense result = {NULL,NULL};
    unsigned char *rows;
    size_t bytes;
    int i;
    if(getenv("MAFFT_NODENSEKMER")) return result;
    if(!dna || tuple_size!=6 || table_size!=KD_WIDTH || n<2 || n>4096) return result;
    bytes=(size_t)n*KD_WIDTH;
    result.allocation=calloc(bytes+31,1);
    if(!result.allocation) return result;
    rows=(unsigned char *)(((uintptr_t)result.allocation+31)&~(uintptr_t)31);
    for(i=0;i<n;i++)
    {
        int *p=points[i];
        unsigned char *row=rows+(size_t)i*KD_WIDTH;
        for(;*p>=0;p++)
        {
            if(*p>=KD_WIDTH || row[*p]==255)
            { free(result.allocation);result.allocation=NULL;return result; }
            row[*p]++;
        }
    }
    result.rows=rows;
#ifdef MAFFT_DENSE_KMER_STATS
    fprintf(stderr,"DENSE_KMER: %d rows, %zu bytes\n",n,bytes);
#endif
    return result;
}
static void kd_destroy(struct kd_dense *dense)
{ free(dense->allocation);dense->allocation=NULL;dense->rows=NULL; }
static int kd_intersection(const unsigned char *a,const unsigned char *b)
{
    int k;
    __m256i accum=_mm256_setzero_si256(),zero=_mm256_setzero_si256();
    uint64_t sums[4];
    for(k=0;k<KD_WIDTH;k+=32)
    {
        __m256i av=_mm256_load_si256((const __m256i *)(a+k));
        __m256i bv=_mm256_load_si256((const __m256i *)(b+k));
        accum=_mm256_add_epi64(accum,_mm256_sad_epu8(_mm256_min_epu8(av,bv),zero));
    }
    _mm256_storeu_si256((__m256i *)sums,accum);
    /* Total <=4096*255, so both the sum and conversion to int are exact. */
    return (int)(sums[0]+sums[1]+sums[2]+sums[3]);
}
#else
struct kd_dense { void *allocation; unsigned char *rows; };
static struct kd_dense kd_prepare(int **points,int n,int t,int k,int dna)
{ struct kd_dense result={NULL,NULL};(void)points;(void)n;(void)t;(void)k;(void)dna;return result; }
static void kd_destroy(struct kd_dense *dense) { (void)dense; }
#endif
