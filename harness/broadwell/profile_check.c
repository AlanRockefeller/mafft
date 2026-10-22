/* Include a scratch partSalignmm.c patched by row_check.py and mcmatch_check.py.
   Exercises exact rows and grouped profiles with ties, signed zeros, subnormals,
   fractional weights, ragged vector widths and varying alphabets. */
#define _POSIX_C_SOURCE 200809L
#ifndef LPROFILE_SOURCE
#error Define LPROFILE_SOURCE to the patched scratch partSalignmm.c
#endif
#include LPROFILE_SOURCE

void ErrorExit(char *s) { fprintf(stderr,"%s\n",s); exit(1); }
/* Isolate mc_match's arithmetic from scarr_fill, which is unchanged by this experiment. */
void scarr_fill(double *out,double **mtx,double **profile,int column)
{
    int l,k;
    for(l=0;l<nalphabets;l++)
    {
        out[l]=0.0;
        for(k=0;k<nalphabets;k++) out[l]=MULADD(mtx[k][l],profile[k][column],out[l]);
    }
}
static uint32_t prng=173915;
static uint32_t next(void) { prng^=prng<<13; prng^=prng>>17; prng^=prng<<5; return prng; }
static double number(void)
{
    static const double special[]={0.0,-0.0,1.0,-1.0,0x1p-1022,-0x1p-1022,0x1p-1074,-0x1p-1074,0x1p45,-0x1p45};
    return next()%3 ? ((int)(next()%101)-50)/13.0 : special[next()%10];
}
int main(int argc,char **argv)
{
    int r,j,k,l,passes=argc>1?atoi(argv[1]):3000;
    double a[9][260], **c1=malloc(26*sizeof(double *)), **c2=malloc(26*sizeof(double *));
    double result[260];
    int ia[3][260];
    n_dis_consweight_multi=malloc(26*sizeof(double *));
    for(l=0;l<26;l++)
    {
        c1[l]=malloc(sizeof(double)); c2[l]=malloc(260*sizeof(double));
        n_dis_consweight_multi[l]=malloc(26*sizeof(double));
    }
    for(r=0;r<passes;r++)
    {
        int n=1+next()%257;
        for(j=0;j<n+2;j++)
        {
            for(k=0;k<9;k++) a[k][j]=number();
            for(k=0;k<3;k++) ia[k][j]=next()%31;
        }
        partA_row(1+next()%100,n,a[0],a[1],a[2],ia[0],ia[1],a[3],a[4],a[5],
                  number(),number(),number(),number(),number(),a[6],ia[2]);
        nalphabets=r%2?4:26;
        for(l=0;l<nalphabets;l++)
        {
            c1[l][0]=number();
            for(k=0;k<nalphabets;k++) n_dis_consweight_multi[l][k]=(int)(next()%1201)-600;
            for(j=0;j<n;j++) c2[l][j]=next()%8?0.0:number();
        }
        mc_build(c2,n);
        mc_match(result,c1,0,n);
    }
    printf("PROFILE_CHECK: %d adversarial rows and grouped profiles identical\n",passes);
    return 0;
}
