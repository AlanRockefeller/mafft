/* Independent score-only matrices in eight AVX2 lanes, an experiment only.
 * Build from repository root:
 * gcc -O3 -march=native -ffp-contract=off -std=c99 -Denablemultithread \
 *   -ffunction-sections -fdata-sections harness/round2/batch_dp.c core/defs.o \
 *   -Wl,--gc-sections -lm -lpthread -o /private/batch_dp
 *
 * batch_dp check 2000
 * batch_dp bench 64 700 700 shared|independent|ragged
 * batch_dp fasta /private/input.fasta 8 shared|independent
 *
 * FASTA mode measures real sequences and shapes under the driver's explicitly
 * synthetic +600/-450 nucleotide matrix; it is not a whole-MAFFT benchmark.
 * No production call site is changed by this driver.
 */
#define _POSIX_C_SOURCE 200809L
#include "../../core/Lalign11.c"
#include <time.h>

#if !defined(MAFFT_AVX2) || defined(MAFFT_AVX512) || KB_LANES != 8
#error This experimental driver requires eight-lane AVX2
#endif

void ErrorExit(char *s) { fprintf(stderr,"%s\n",s); exit(1); }

static unsigned bseed=1234567;
static unsigned brand32(void)
{
    bseed^=bseed<<13; bseed^=bseed>>17; bseed^=bseed<<5; return bseed;
}
static double btime(clockid_t id)
{
    struct timespec t; clock_gettime(id,&t); return t.tv_sec+t.tv_nsec*1e-9;
}

/* Returns raw (pre-threshold) maxima, as Lscore_kb does. The public MAFFT caller
 * must keep its zero-result double fallback if this prototype is ever integrated.
 * All lanes pass the established arithmetic guard before any batch work begins.
 */
static int batch8(double **matrix, char **s1, char **s2, double *scores)
{
    static int *mem=NULL;
    static size_t cap=0;
    int n[8] __attribute__((aligned(32))), m[8] __attribute__((aligned(32)));
    int idx[8] __attribute__((aligned(32))), special[8] __attribute__((aligned(32)));
    int imax=0,jmax=0,l,i,j,c,k,ithr=0,nprof=0,rank[256], *row,*vm,*profiles;
    unsigned char used[256]={0}, guardused[256];
    size_t stride,need;
    kb_vec vmax=kb_set1(INT_MIN), vpen=kb_set1(penalty), vext=kb_set1(penalty_ex);
    kb_vec vthr,vn,vmn;
    for(l=0;l<8;l++)
    {
        n[l]=strlen(s1[l]); m[l]=strlen(s2[l]);
        if(!lfill_prepare(matrix,matrix,0,(unsigned char *)s1[l],(unsigned char *)s2[l],n[l],m[l],&ithr,guardused)) return 0;
        if(n[l]>imax) imax=n[l];
        if(m[l]>jmax) jmax=m[l];
        for(i=0;i<n[l];i++) used[(unsigned char)s1[l][i]]=1;
    }
    for(c=0;c<256;c++) rank[c]=used[c]?nprof++:-1;
    stride=(size_t)(jmax+1)*8;
    need=(size_t)(nprof+2)*stride;
    if(need>cap) { kb_free(mem); mem=kb_alloc(sizeof(int)*need); cap=need; }
    row=mem; vm=row+stride; profiles=vm+stride;
    for(c=0;c<256;c++) if(used[c])
        for(j=0;j<=jmax;j++) for(l=0;l<8;l++)
            profiles[(size_t)rank[c]*stride+(size_t)j*8+l]=(j<m[l])?(int)matrix[c][(unsigned char)s2[l][j]]:0;
    for(j=0;j<=jmax;j++) for(l=0;l<8;l++)
    {
        row[j*8+l]=(j<m[l])?(int)matrix[(unsigned char)s1[l][0]][(unsigned char)s2[l][j]]:0;
        vm[j*8+l]=(j>0 && j<=m[l])?(int)matrix[(unsigned char)s1[l][0]][(unsigned char)s2[l][j-1]]:0;
    }
    vthr=kb_set1(ithr); vn=kb_load(n); vmn=kb_load(m);
    for(i=1;i<=imax;i++)
    {
        kb_vec diag,mi,active_i=kb_gt(vn,kb_set1(i-1)), offsets;
        int same=1,which;
        for(l=0;l<8;l++)
        {
            int qi=i<n[l]?i:0;
            special[l]=i<=n[l]?(int)matrix[(unsigned char)s2[l][0]][(unsigned char)s1[l][i-1]]:0;
            idx[l]=(int)((size_t)rank[(unsigned char)s1[l][qi]]*stride)+l;
            if(l && idx[l]-l!=idx[0]) same=0;
        }
        which=idx[0]; offsets=kb_load(idx); diag=kb_load(special); mi=diag;
        for(j=1;j<=jmax;j++)
        {
            kb_vec nextdiag=kb_load(row+j*8), vj=kb_load(vm+j*8), sub,wm;
            kb_vec active=kb_and(active_i,kb_gt(vmn,kb_set1(j-1)));
            wm=kb_max(diag,kb_add(mi,vpen));
            mi=kb_add(kb_max(mi,diag),vext);
            wm=kb_max(wm,kb_add(vj,vpen));
            kb_store(vm+j*8,kb_add(kb_max(diag,vj),vext));
            vmax=kb_max(vmax,kb_select(kb_set1(INT_MIN),wm,active));
            if(same) sub=kb_load(profiles+which+j*8);
            else sub=_mm256_i32gather_epi32(profiles+j*8,offsets,4);
            kb_store(row+j*8,kb_add(kb_max(wm,vthr),sub));
            diag=nextdiag;
        }
    }
    kb_store(special,vmax);
    for(l=0;l<8;l++) scores[l]=(double)special[l];
    return 1;
}

/* Independent scalar transcription of the original double score recurrence.
 * Store only two needed states; substitution in the unused final row/column is
 * zero. Zero signs are compared numerically here because raw integer kernels
 * return +0; the public-call checker separately exercises signed-zero fallback.
 */
static double scalar_raw(double **matrix,char *s1,char *s2)
{
    int n=strlen(s1),m=strlen(s2),i,j;
    double *row=calloc(m+1,sizeof(double)), *v=calloc(m+1,sizeof(double));
    double best=-999999999.9;
    for(j=0;j<m;j++) row[j]=matrix[(unsigned char)s1[0]][(unsigned char)s2[j]];
    for(j=1;j<=m;j++) v[j]=row[j-1];
    for(i=1;i<=n;i++)
    {
        double d=matrix[(unsigned char)s2[0]][(unsigned char)s1[i-1]],h=d;
        for(j=1;j<=m;j++)
        {
            double next=row[j],w=d,g=h+penalty;
            if(g>w) w=g;
            if(d>h) h=d;
            h+=penalty_ex;
            g=v[j]+penalty;
            if(g>w) w=g;
            if(d>v[j]) v[j]=d;
            v[j]+=penalty_ex;
            if(w>best) best=w;
            if(w < -offset) w=-offset;
            row[j]=w+((i<n && j<m)?matrix[(unsigned char)s1[i]][(unsigned char)s2[j]]:0);
            d=next;
        }
    }
    free(row);free(v);return best;
}

static void setmatrix(double **matrix,int randomize)
{
    int a,b;
    for(a=0;a<4;a++) for(b=0;b<4;b++)
    {
        double score=randomize?(int)(brand32()%61)-30:(a==b?600:-450);
        matrix[(unsigned char)"ACGT"[a]][(unsigned char)"ACGT"[b]]=score;
        matrix[a][b]=score;
    }
}
static char *newseq(int n)
{
    int i; char *s=malloc(n+1);
    for(i=0;i<n;i++) s[i]="ACGT"[brand32()%4];
    s[n]=0;return s;
}
static long long bcheck(double **matrix,char **s1,char **s2,int scalar)
{
    int l;double bat[8];long long cells=0;
    if(!batch8(matrix,s1,s2,bat)) ErrorExit("Unexpected batch arithmetic fallback");
    for(l=0;l<8;l++)
    {
        double striped,ref;
        int n=strlen(s1[l]),m=strlen(s2[l]);
        if(!Lscore_kb(matrix,matrix,0,s1[l],s2[l],n,m,&striped)) ErrorExit("Unexpected striped arithmetic fallback");
        ref=scalar?scalar_raw(matrix,s1[l],s2[l]):striped;
        if(memcmp(&bat[l],&striped,sizeof(double)) || ref!=bat[l])
        {
            fprintf(stderr,"Mismatch lane=%d len=%d,%d batch=%.17g striped=%.17g scalar=%.17g\n",l,n,m,bat[l],striped,ref);abort();
        }
        cells+=(long long)n*m;
    }
    return cells;
}
static void randomized_checks(double **matrix,int cases)
{
    int r,l;long long cells=0;
    for(r=0;r<cases;r++)
    {
        char *s1[8],*s2[8];
        int common=1+brand32()%97;
        setmatrix(matrix,1);
        penalty=-(int)(brand32()%70);penalty_ex=-(int)(brand32()%5);offset=(int)(brand32()%41)-20;
        if(r%7==0) penalty=(int)(brand32()%11);
        if(r%13==0) penalty_ex=(int)(brand32()%5);
        for(l=0;l<8;l++)
        {
            int n=r%2?1+brand32()%97:common,m=1+brand32()%97;
            if(r%19==0)
            {
                const int edges[]={1,2,7,8,9,15,16,17,63,64,65,127,128,129,511,512,513};
                n=edges[brand32()%17];m=edges[brand32()%17];
            }
            s1[l]=newseq(n);s2[l]=newseq(m);
            if(r%2==0 && r%19!=0 && l) { free(s1[l]);s1[l]=strdup(s1[0]); }
        }
        cells+=bcheck(matrix,s1,s2,1);
        for(l=0;l<8;l++) { free(s1[l]);free(s2[l]); }
    }
    printf("check cases=%d matrices=%d cells=%lld all identical\n",cases,cases*8,cells);
}

static double run_kind(double **matrix,char **s1,char **s2,int batch,int repeats,long long *sum)
{
    int r,l;double scores[8],start=btime(CLOCK_THREAD_CPUTIME_ID);
    for(r=0;r<repeats;r++)
    {
        if(batch) { if(!batch8(matrix,s1,s2,scores)) ErrorExit("batch fallback"); }
        else for(l=0;l<8;l++)
            if(!Lscore_kb(matrix,matrix,0,s1[l],s2[l],strlen(s1[l]),strlen(s2[l]),scores+l)) ErrorExit("striped fallback");
        for(l=0;l<8;l++) *sum+=(long long)scores[l];
    }
    return btime(CLOCK_THREAD_CPUTIME_ID)-start;
}
static void measure(double **matrix,char **s1,char **s2,int repeats,int tag)
{
    int r,l,imax=0,jmax=0;long long cells=bcheck(matrix,s1,s2,0);
    for(l=0;l<8;l++) { if((int)strlen(s1[l])>imax) imax=strlen(s1[l]);if((int)strlen(s2[l])>jmax) jmax=strlen(s2[l]); }
    for(r=0;r<3;r++)
    {
        double times[2];long long sums[2]={0,0};
        for(l=0;l<2;l++) { int kind=(r+l)%2;times[kind]=run_kind(matrix,s1,s2,kind,repeats,sums+kind); }
        if(sums[0]!=sums[1]) ErrorExit("Timing checksum mismatch");
        printf("{\"tag\":%d,\"round\":%d,\"repeats\":%d,\"valid_cells\":%lld,\"padded_cells\":%lld,\"striped_cpu\":%.9f,\"batch_cpu\":%.9f,\"speedup\":%.6f,\"checksum\":%lld}\n",
          tag,r,repeats,cells,(long long)imax*jmax*8,times[0],times[1],times[0]/times[1],sums[0]);
        fflush(stdout);
    }
}

static char **readfasta(const char *path,int *nseq)
{
    FILE *f=fopen(path,"r");char *line=NULL,**seq=NULL;size_t linecap=0,cap=0,len=0;ssize_t got;
    int n=0;
    if(!f) ErrorExit("Cannot open FASTA");
    while((got=getline(&line,&linecap,f))>=0)
    {
        if(got && line[0]=='>')
        {
            if(n && len==0) ErrorExit("Empty FASTA record");
            seq=realloc(seq,sizeof(char *)*(n+1));cap=256;seq[n]=malloc(cap);seq[n++][0]=0;len=0;
        }
        else
        {
            ssize_t j;
            for(j=0;j<got;j++) if(!isspace((unsigned char)line[j]) && line[j]!='-' && line[j]!='.')
            {
                if(!n) ErrorExit("Sequence before FASTA header");
                if(len+2>cap) { cap*=2;seq[n-1]=realloc(seq[n-1],cap); }
                seq[n-1][len++]=toupper((unsigned char)line[j]);seq[n-1][len]=0;
            }
        }
    }
    if(n && len==0) ErrorExit("Empty FASTA record");
    free(line);fclose(f);*nseq=n;return seq;
}

int main(int argc,char **argv)
{
    int i,l;double **matrix=malloc(sizeof(double *)*256);
    for(i=0;i<256;i++) matrix[i]=calloc(256,sizeof(double));
    nalphabets=4;for(i=0;i<4;i++) amino[i]="ACGT"[i];
    setmatrix(matrix,0);penalty=-1530;penalty_ex=offset=0;
    if(argc>1 && !strcmp(argv[1],"check")) randomized_checks(matrix,argc>2?atoi(argv[2]):2000);
    else if(argc>1 && !strcmp(argv[1],"fasta"))
    {
        int count,batches=argc>3?atoi(argv[3]):8,shared=argc<=4 || !strcmp(argv[4],"shared");
        char **seq;
        if(argc<3) ErrorExit("Missing FASTA path");
        seq=readfasta(argv[2],&count);
        if(count<2) ErrorExit("Need at least two FASTA records");
        if(batches>count) batches=count;
        for(i=0;i<batches;i++)
        {
            char *s1[8],*s2[8];
            for(l=0;l<8;l++) { s1[l]=seq[(i*17+(shared?0:l*13))%count];s2[l]=seq[(i*17+l+1)%count]; }
            measure(matrix,s1,s2,16,i);
        }
        for(i=0;i<count;i++) free(seq[i]);free(seq);
    }
    else
    {
        char *s1[8],*s2[8];int repeats=argc>2?atoi(argv[2]):64,n=argc>3?atoi(argv[3]):700,m=argc>4?atoi(argv[4]):700;
        int shared=argc<=5 || !strcmp(argv[5],"shared"),ragged=argc>5 && !strcmp(argv[5],"ragged");
        for(l=0;l<8;l++)
        {
            s1[l]=(shared && l)?strdup(s1[0]):newseq(ragged?n*(l+1)/8+1:n);
            s2[l]=newseq(ragged?m*(8-l)/8+1:m);
        }
        measure(matrix,s1,s2,repeats,0);
        for(l=0;l<8;l++) { free(s1[l]);free(s2[l]); }
    }
    kb_release();for(i=0;i<256;i++) free(matrix[i]);free(matrix);return 0;
}
