/* Build against a scratch Lalign11.c patched by broadwell/noalign_check.py.
 * Enable -DMAFFT_KB_SELF_SCORE=1. The wrapper checks every return bit against
 * the original double DP; model zero also checks the analytic expected value.
 */
#ifndef L11_SOURCE
#define L11_SOURCE "../../core/Lalign11.c"
#endif
#include L11_SOURCE

void ErrorExit(char *s) { fprintf(stderr,"%s\n",s);exit(1); }
double *AllocateFloatVec(int n) { return calloc(n,sizeof(double)); }
void FreeFloatVec(double *p) { free(p); }
double **AllocateDoubleMtx(int n,int m)
{
    int i;double **p=calloc(n+1,sizeof(double *));
    for(i=0;i<n;i++) p[i]=calloc(m,sizeof(double));return p;
}
void FreeDoubleMtx(double **p) { int i;for(i=0;p[i];i++) free(p[i]);free(p); }

static unsigned sseed=987654321;
static unsigned srand32(void) { sseed^=sseed<<13;sseed^=sseed>>17;sseed^=sseed<<5;return sseed; }
int main(int argc,char **argv)
{
    int r,a,b,i,cases=argc>1?atoi(argv[1]):10000,eligible=0;
    long long cells=0;
    double **matrix=AllocateDoubleMtx(256,256);
    for(r=0;r<cases;r++)
    {
        const char *alphabet=r%2?"ACGTRYSWKMBDHVN":"ARNDCQEGHILKMFPSTWYV";
        int n=1+srand32()%257,m=n,model=r%8,selfvalue=1+srand32()%100;
        char *s1,*s2;double actual;
        if(r%17==0) { const int edges[]={1,2,7,8,9,15,16,17,63,64,65,127,128,129,511,512,513,1023};n=m=edges[srand32()%18]; }
        if(r%3==0 && model!=0) m=1+srand32()%257;
        nalphabets=strlen(alphabet);
        for(a=0;a<nalphabets;a++) amino[a]=alphabet[a];
        penalty=-(int)(srand32()%100);penalty_ex=-(int)(srand32()%4);
        offset=(int)(srand32()%201);
        for(a=0;a<nalphabets;a++) for(b=0;b<nalphabets;b++)
            matrix[a][b]=a==b?selfvalue:selfvalue-(int)(srand32()%201);
        if(model==1) for(a=0;a<nalphabets;a++) matrix[a][a]+=a;
        if(model==2) matrix[0][1]=selfvalue+1;
        if(model==3) penalty=1+srand32()%30;
        if(model==4) penalty_ex=1+srand32()%5;
        if(model==5) offset=-selfvalue-1-(int)(srand32()%100);
        if(model==6)
        {
            for(a=0;a<nalphabets;a++) for(b=0;b<nalphabets;b++) matrix[a][b]=(a+b)%2?-0.0:0.0;
            penalty=penalty_ex=offset=0;
        }
        if(model==7) matrix[0][0]+=.25;
        s1=malloc(n+1);s2=malloc(m+1);
        for(i=0;i<n;i++) s1[i]=alphabet[srand32()%nalphabets];s1[n]=0;
        if(n==m) memcpy(s2,s1,n+1);
        else { for(i=0;i<m;i++) s2[i]=alphabet[srand32()%nalphabets];s2[m]=0; }
        actual=L__align11_noalign(matrix,&s1,&s2);
        if(model==0)
        {
            double expected=(double)selfvalue*n;
            if(memcmp(&actual,&expected,sizeof(double))) { fprintf(stderr,"analytic self mismatch %.17g vs %.17g\n",actual,expected);abort(); }
            eligible++;
        }
        cells+=(long long)n*m;free(s1);free(s2);
        if(r%101==0) L__align11_noalign(NULL,NULL,NULL);
    }
    L__align11_noalign(NULL,NULL,NULL);FreeDoubleMtx(matrix);
    printf("SELF_SCORE cases=%d eligible_uniform=%d cells=%lld\n",cases,eligible,cells);return 0;
}
