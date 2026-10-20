/* Direct integer-fast-path guard checks, including conversion/abs overflow.
 * Unsupported values must reject before any conversion with undefined behavior;
 * this driver deliberately does not execute the original scalar DP on them.
 * Build with -fsanitize=undefined,float-cast-overflow,address and section GC.
 */
#include "../../core/Lalign11.c"
#include <float.h>

void ErrorExit(char *s) { fprintf(stderr,"%s\n",s);exit(1); }
static int cases;
static void guard_expect(double **matrix,double **dynamic,double scoreoffset,int n,int m,int expected)
{
    unsigned char used[256];int ithr;
    unsigned char s1[]="ACGTACGT",s2[]="AACCGGTT";
    int got=lfill_prepare(matrix,dynamic,scoreoffset,s1,s2,n,m,&ithr,used);
    cases++;
    if(got!=expected) { fprintf(stderr,"guard case %d: expected %d, got %d\n",cases,expected,got);abort(); }
}
int main(void)
{
    const double invalid[]={NAN,INFINITY,-INFINITY,DBL_MAX,-DBL_MAX,
        (double)INT_MAX+1.0,(double)INT_MIN-1.0,(double)INT_MIN,(double)INT_MAX,0.25};
    double **matrix=malloc(256*sizeof(double *)),**dynamic=malloc(4*sizeof(double *));
    int a,b,i;
    for(a=0;a<256;a++) matrix[a]=calloc(256,sizeof(double));
    for(a=0;a<4;a++) dynamic[a]=calloc(4,sizeof(double));
    nalphabets=4;
    for(a=0;a<4;a++)
    {
        amino[a]="ACGT"[a];
        for(b=0;b<4;b++) matrix[(unsigned char)"ACGT"[a]][(unsigned char)"ACGT"[b]]=dynamic[a][b]=a==b?600:-450;
    }
    penalty=-1530;penalty_ex=offset=0;
    guard_expect(matrix,dynamic,0,8,8,1);
    guard_expect(matrix,dynamic,0.5,8,8,1);
    guard_expect(matrix,dynamic,-0.0,8,8,1);
    guard_expect(matrix,dynamic,0,0,8,0);
    guard_expect(matrix,dynamic,0,8,0,0);
    for(i=0;i<(int)(sizeof(invalid)/sizeof(invalid[0]));i++)
    {
        dynamic[0][0]=invalid[i];guard_expect(matrix,dynamic,0,8,8,0);dynamic[0][0]=600;
        matrix['A']['C']=invalid[i];guard_expect(matrix,dynamic,0,8,8,0);matrix['A']['C']=-450;
    }
    matrix['A']['C']=-0.0;guard_expect(matrix,dynamic,0,8,8,1);matrix['A']['C']=-450;
    for(i=0;i<2;i++)
    {
        int extreme=i?INT_MAX:INT_MIN;
        penalty=extreme;guard_expect(matrix,dynamic,0,8,8,0);penalty=-1530;
        penalty_ex=extreme;guard_expect(matrix,dynamic,0,8,8,0);penalty_ex=0;
        offset=extreme;guard_expect(matrix,dynamic,0,8,8,0);offset=0;
    }
    guard_expect(matrix,dynamic,NAN,8,8,0);
    guard_expect(matrix,dynamic,INFINITY,8,8,0);
    guard_expect(matrix,dynamic,-INFINITY,8,8,0);
    guard_expect(matrix,dynamic,DBL_MAX,8,8,0);
    guard_expect(matrix,dynamic,0.001,8,8,0);
    guard_expect(matrix,dynamic,0,INT_MAX,8,0);
    guard_expect(matrix,dynamic,0,8,INT_MAX,0);
    for(a=0;a<256;a++) free(matrix[a]);free(matrix);
    for(a=0;a<4;a++) free(dynamic[a]);free(dynamic);
    printf("LFILL_GUARD: %d direct helper cases passed\n",cases);return 0;
}
