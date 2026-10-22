/* Small, bounded guest measurements. Report CPU and wall time separately under VM load.
   Pointer chasing exposes dependent-load cost; triad counts read+write traffic (24 B/element),
   excluding write allocation. This is not a measurement of the physical host's peak bandwidth. */
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static volatile uint64_t sink;
static double stamp(clockid_t c)
{
    struct timespec t;
    clock_gettime(c,&t);
    return t.tv_sec+t.tv_nsec*1e-9;
}
static uint32_t rng(uint32_t *s)
{
    *s ^= *s<<13; *s ^= *s>>17; *s ^= *s<<5;
    return *s;
}
int main(void)
{
    size_t bytes;
    for(bytes=4096;bytes<=67108864;bytes*=4)
    {
        size_t n=bytes/sizeof(uint32_t), i;
        uint32_t *p=malloc(bytes), *order=malloc(bytes), state=1234567, k=0;
        double cpu,wall;
        const size_t steps=4000000;
        if(!p || !order) return 1;
        for(i=0;i<n;i++) order[i]=(uint32_t)i;
        for(i=n-1;i>0;i--) { size_t j=rng(&state)%(i+1); uint32_t v=order[i]; order[i]=order[j]; order[j]=v; }
        for(i=0;i<n;i++) p[order[i]]=order[(i+1)%n];
        for(i=0;i<steps/4;i++) k=p[k];
        wall=stamp(CLOCK_MONOTONIC); cpu=stamp(CLOCK_THREAD_CPUTIME_ID);
        for(i=0;i<steps;i++) k=p[k];
        cpu=stamp(CLOCK_THREAD_CPUTIME_ID)-cpu; wall=stamp(CLOCK_MONOTONIC)-wall;
        sink+=k;
        printf("chase bytes=%zu ns/load_cpu=%.3f ns/load_wall=%.3f\n",bytes,cpu*1e9/steps,wall*1e9/steps);
        free(p);free(order);
    }
    for(bytes=16384;bytes<=67108864;bytes*=8)
    {
        size_t n=bytes/sizeof(double),i,rep, reps=134217728/n;
        double *a=malloc(bytes), *b=malloc(bytes), *c=malloc(bytes),cpu,wall;
        if(!a || !b || !c) return 1;
        for(i=0;i<n;i++) { a[i]=i*.25; b[i]=i*.5; c[i]=0; }
        wall=stamp(CLOCK_MONOTONIC); cpu=stamp(CLOCK_THREAD_CPUTIME_ID);
        for(rep=0;rep<reps;rep++)
        {
            for(i=0;i<n;i++) c[i]=a[i]+3.0*b[i];
            /* Keep every repetition observable, without blocking vectorization inside it. */
            __asm__ volatile("" : : "r"(c) : "memory");
        }
        cpu=stamp(CLOCK_THREAD_CPUTIME_ID)-cpu; wall=stamp(CLOCK_MONOTONIC)-wall;
        sink+=(uint64_t)c[n-1];
        printf("triad working_set=%zu GiB/s_cpu=%.3f GiB/s_wall=%.3f\n",bytes*3,24.0*n*reps/cpu/1073741824,24.0*n*reps/wall/1073741824);
        free(a);free(b);free(c);
    }
    printf("checksum=%llu\n",(unsigned long long)sink);
    return 0;
}
