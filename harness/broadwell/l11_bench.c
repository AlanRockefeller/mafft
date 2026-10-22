/* Compile with -ffunction-sections -Wl,--gc-sections and core/defs.o; unused MAFFT
   entry points are discarded. L11_SOURCE can name a source patched by kb_check.py
   for a differential fuzzer (L11_CHECK). Production FASTAs remain the speed benchmark. */
#define _POSIX_C_SOURCE 200809L
#ifndef L11_SOURCE
#define L11_SOURCE "../../core/Lalign11.c"
#endif
#include L11_SOURCE
#include <time.h>

void ErrorExit(char *s) { fprintf(stderr,"%s\n",s); exit(1); }
#ifdef L11_SCORE
double *AllocateFloatVec(int n) { return calloc(n,sizeof(double)); }
void FreeFloatVec(double *p) { free(p); }
double **AllocateDoubleMtx(int n,int m)
{
    int i; double **p=calloc(n+1,sizeof(double *));
    for(i=0;i<n;i++) p[i]=calloc(m,sizeof(double));
    return p;
}
void FreeDoubleMtx(double **p) { int i; for(i=0;p[i];i++) free(p[i]); free(p); }
#endif
static unsigned seed=1234567;
static unsigned random32(void) { seed^=seed<<13; seed^=seed>>17; seed^=seed<<5; return seed; }
static double now(clockid_t id)
{
    struct timespec t; clock_gettime(id,&t); return t.tv_sec+t.tv_nsec*1e-9;
}
int main(int argc,char **argv)
{
    int a,b,r, passes=argc>1?atoi(argv[1]):512;
    int n=argc>2?atoi(argv[2]):700, m=argc>3?atoi(argv[3]):700;
    double **matrix=malloc(256*sizeof(double *)), maximum, cpu, wall;
    long long checksum=0, cells=0;
#ifndef L11_CHECK
    int **shared_ijp=malloc((n+1)*sizeof(int *));
    char *shared_s1=malloc(n+1), *shared_s2=malloc(m+1);
    for(a=0;a<=n;a++) shared_ijp[a]=calloc(m+1,sizeof(int));
#endif
    for(a=0;a<256;a++) matrix[a]=calloc(256,sizeof(double));
    nalphabets=4;
    for(a=0;a<4;a++) amino[a]="ACGT"[a];
    for(a=0;a<256;a++) for(b=0;b<256;b++) matrix[a][b]=(a==b)?600:-450;
    penalty=-1530; penalty_ex=0; offset=0;
    cpu=now(CLOCK_THREAD_CPUTIME_ID); wall=now(CLOCK_MONOTONIC);
    for(r=0;r<passes;r++)
    {
        int i, ei,ej,ok, **ijp;
        char *s1,*s2;
#ifdef L11_CHECK
        n=1+random32()%97; m=1+random32()%97;
        if(r%19==0) { const int edges[]={1,2,3,4,7,8,9,15,16,17,31,32,33,63,64,65,127,128,129,255,256,257,511,512,513,1023};
            n=edges[random32()%26]; m=edges[random32()%26]; }
        penalty=-(int)(random32()%70); penalty_ex=-(int)(random32()%5); offset=(int)(random32()%41)-20;
        if(r%7==0) penalty=(int)(random32()%11); /* allowed nonstandard positive penalties */
#ifdef L11_WIDE
        if(r%43==0 && n+m<500) { penalty=r%2?100000:-100000; penalty_ex=r%5?1000:-1000; offset=r%3?100000:-100000; }
        for(a=0;a<256;a++) for(b=0;b<256;b++) matrix[a][b]=r%11==0?0:(int)(random32()%200001)-100000;
#else
        for(a=0;a<256;a++) for(b=0;b<256;b++) matrix[a][b]=r%11==0?0:(int)(random32()%61)-30;
#endif
#ifdef L11_SCORE
        if(r%31==0) matrix[0][0]+=.25; /* nonintegral fallback */
        if(r%37==0) matrix[0][0]=1e7;  /* range fallback */
        if(r%29==0)
        {
            for(a=0;a<4;a++) for(b=0;b<4;b++) matrix[a][b]=(a+b)%2?-0.0:0.0;
            penalty=penalty_ex=offset=0;
        }
#endif
#endif
#ifdef L11_CHECK
        s1=malloc(n+1); s2=malloc(m+1);
        ijp=malloc((n+1)*sizeof(int *));
        for(i=0;i<=n;i++) ijp[i]=calloc(m+1,sizeof(int));
#else
        s1=shared_s1; s2=shared_s2; ijp=shared_ijp;
#endif
        for(i=0;i<n;i++) s1[i]="ACGT"[random32()%4];
        for(i=0;i<m;i++) s2[i]=(i<n && random32()%10)?s1[i]:"ACGT"[random32()%4];
        s1[n]=s2[m]=0;
#ifdef L11_SCORE
        { char *ss1[1]={s1}, *ss2[1]={s2}; maximum=L__align11_noalign(matrix,ss1,ss2); ei=ej=0; ok=1; }
#elif defined(MAFFT_LSTRIPED)
        ok=Lfill_kb(matrix,matrix,0,s1,s2,n,m,&maximum,&ei,&ej);
#else
        ok=Lfill_int(matrix,matrix,0,s1,s2,n,m,ijp,n+m+1,&maximum,&ei,&ej);
#endif
        if(!ok) { fprintf(stderr,"unexpected fallback %d,%d\n",n,m); return 1; }
        cells+=(long long)n*m; checksum+=(long long)maximum+ei+ej;
#ifdef L11_CHECK
        for(i=0;i<=n;i++) free(ijp[i]);
        free(ijp);free(s1);free(s2);
#ifdef L11_SCORE
        if(r%97==0) L__align11_noalign(NULL,NULL,NULL);
#endif
#endif
    }
    cpu=now(CLOCK_THREAD_CPUTIME_ID)-cpu; wall=now(CLOCK_MONOTONIC)-wall;
    printf("passes=%d cells=%lld checksum=%lld cpu=%.6f wall=%.6f ns/cell=%.4f\n",passes,cells,checksum,cpu,wall,cpu*1e9/cells);
#ifndef L11_CHECK
    for(a=0;a<=n;a++) free(shared_ijp[a]);
    free(shared_ijp);free(shared_s1);free(shared_s2);
#endif
    for(a=0;a<256;a++) free(matrix[a]);
    free(matrix);
    return 0;
}
