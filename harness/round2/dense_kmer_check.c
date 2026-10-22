/* Exact byte-histogram kernel versus original ordered point-list counting. */
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#define MAFFT_DENSE_KMER 1
#include "../../core/dense_kmer.h"
static unsigned state=928384;
static unsigned random32(void) { state^=state<<13;state^=state>>17;state^=state<<5;return state; }
static int scalar(const int *a,const int *b)
{
    int count[4096]={0},memo[4096]={0},score=0;
    for(;*a>=0;a++)count[*a]++;
    for(;*b>=0;b++)if(memo[*b]++<count[*b])score++;
    return score;
}
int main(void)
{
    int rounds,i,j,r,n=32,**points=calloc(n,sizeof(*points));
    unsigned long long tests=0;
    for(i=0;i<n;i++)points[i]=calloc(2101,sizeof(int));
    for(rounds=0;rounds<150;rounds++)
    {
        struct kd_dense dense;
        for(i=0;i<n;i++)
        {
            int len=random32()%2000,types=rounds%7?4096:257;
            for(j=0;j<len;j++)points[i][j]=random32()%types;
            points[i][len]=-1;
        }
        dense=kd_prepare(points,n,4096,6,1);assert(dense.rows);
        for(i=0;i<n;i++)for(j=0;j<n;j++)
        {
            int ref=scalar(points[i],points[j]);
            int actual=kd_intersection(dense.rows+(size_t)i*4096,dense.rows+(size_t)j*4096);
            if(ref!=actual) { fprintf(stderr,"Mismatch %d %d %d %d\n",i,j,ref,actual);return 1; }
            tests++;
        }
        kd_destroy(&dense);
    }
    for(i=0;i<256;i++)points[0][i]=4;points[0][256]=-1;
    {struct kd_dense d=kd_prepare(points,n,4096,6,1);assert(!d.rows && !d.allocation);}
    points[0][255]=-1;
    {struct kd_dense d=kd_prepare(points,n,4096,6,1);assert(d.rows);assert(kd_intersection(d.rows,d.rows)==255);kd_destroy(&d);}
    {struct kd_dense d=kd_prepare(points,n,4096,10,1);assert(!d.rows);}
    {struct kd_dense d=kd_prepare(points,n,4096,6,0);assert(!d.rows);}
    {struct kd_dense d=kd_prepare(points,4097,4096,6,1);assert(!d.rows);}
    {struct kd_dense d=kd_prepare(points,n,46656,6,1);assert(!d.rows);}
    for(i=0;i<n;i++)free(points[i]);free(points);
    printf("PASS %llu exact intersections; overflow/shape/cap fallback checks\n",tests);
    return 0;
}
