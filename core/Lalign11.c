#include "mltaln.h"
#include "dp.h"
#include <limits.h>
#include <stdint.h>
/* Exact banded all-pairs fill (Lfill_band, paper/round2-band-certificate.md).  On by default
   only where it was measured: AVX2 builds without AVX-512 (the Dikarya host). */
#ifndef MAFFT_LBAND
#if defined(MAFFT_AVX2) && !defined(MAFFT_AVX512) && MAFFT_AVX2_STRIPED
#define MAFFT_LBAND 1
#else
#define MAFFT_LBAND 0
#endif
#endif
#define LBAND_NEG ( INT_MIN / 2 ) /* an empty state: below every filled value, never overflows */
#if MAFFT_LBAND
/* MAFFT_NOLBAND (any value) turns the band off; lband_force overrides it for the check mode. */
static TLS int lband_force = -1;
static int lband_on( void )
{
	static int env = -1;
	if( lband_force >= 0 ) return( lband_force );
	if( env < 0 ) env = getenv( "MAFFT_NOLBAND" ) ? 0 : 1;
	return( env );
}
static int lband_check_on( void )
{
	static int env = -1;
	if( env < 0 ) env = getenv( "MAFFT_LBAND_CHECK" ) ? 1 : 0;
	return( env );
}
#else
#define lband_on() 0
#endif
#if defined(MAFFT_SVE)
#ifndef LSVE_MINLANES
#define LSVE_MINLANES 8 /* the SVE fill is used for vectors of at least this many int32 lanes */
#endif
#endif

#define DEBUG 0
#define DEBUG2 0
#define XXXXXXX    0
#define USE_PENALTY_EX  1


static TLS int localstop; // 060910

#if 1
static void match_calc_mtx( double **mtx, double *match, char **s1, char **s2, int i1, int lgth2 ) 
{
	char *seq2 = s2[0];
	double *doubleptr = mtx[(unsigned char)s1[0][i1]];

	while( lgth2-- )
		*match++ = doubleptr[(unsigned char)*seq2++];
}
#else
static void match_calc( double *match, char **s1, char **s2, int i1, int lgth2 )
{
	int j;

	for( j=0; j<lgth2; j++ )
		match[j] = amino_dis[(*s1)[i1]][(*s2)[j]];
}
#endif

#if 0
static void match_calc_bk( double *match, double **cpmx1, double **cpmx2, int i1, int lgth2, double **doublework, int **intwork, int initialize )
{
	int j, k, l;
	double scarr[nalphabets];
	double **cpmxpd = doublework;
	int **cpmxpdn = intwork;
	int count = 0;

	if( initialize )
	{
		for( j=0; j<lgth2; j++ )
		{
			count = 0;
			for( l=0; l<nalphabets; l++ )
			{
				if( cpmx2[l][j] )
				{
					cpmxpd[count][j] = cpmx2[l][j];
					cpmxpdn[count][j] = l;
					count++;
				}
			}
			cpmxpdn[count][j] = -1;
		}
	}

	for( l=0; l<nalphabets; l++ )
	{
		scarr[l] = 0.0;
		for( k=0; k<nalphabets; k++ )
			scarr[l] += n_dis[k][l] * cpmx1[k][i1];
	}
#if 0 /* これを使うときはdoubleworkのアロケートを逆にする */
	{
		double *fpt, **fptpt, *fpt2;
		int *ipt, **iptpt;
		fpt2 = match;
		iptpt = cpmxpdn;
		fptpt = cpmxpd;
		while( lgth2-- )
		{
			*fpt2 = 0.0;
			ipt=*iptpt,fpt=*fptpt;
			while( *ipt > -1 )
				*fpt2 += scarr[*ipt++] * *fpt++;
			fpt2++,iptpt++,fptpt++;
		} 
	}
#else
	for( j=0; j<lgth2; j++ )
	{
		match[j] = 0.0;
		for( k=0; cpmxpdn[k][j]>-1; k++ )
			match[j] += scarr[cpmxpdn[k][j]] * cpmxpd[k][j];
	} 
#endif
}
#endif

#if defined(MAFFT_AVX2) || defined(MAFFT_A64)
/*
 * Used by every AVX2 build, AVX-512 builds included (on Zen 4 it made L-INS-i about 7% faster
 * than the AVX-512 prefix-scan fill of opt5), and by every arm64 build, with a NEON or SVE loop
 * (on the Apple build it serves the pairs the GPU does not take).  The integer DP is the same
 * under every rounding class, so MAFFT_STOCK_FMA does not matter here.
 *
 * The AVX2 integer fill (Lfill_int below) does not compute the traceback offsets themselves.  A
 * cell whose best move is a horizontal gap stores LMARK_H, a vertical gap LMARK_V, and every DP
 * row is kept (lr_*) instead of two alternating ones.  Ltracking only reads ijp along the one
 * path it follows, and for a marked cell asks lres_hk()/lres_vmp() for the offset, which replay
 * the original scalar rules over the stored rows -- integer arithmetic, so the offset is exactly
 * the one the full fill would have stored.  The markers lie below every real value (offsets are
 * at least -lgth2, localstop and warpbase are positive).
 */
#define LFILL_MARKS 1
#define LMARK_H INT_MIN
#define LMARK_V ( INT_MIN + 1 )
static TLS int *lr_raw = NULL;
static TLS size_t lr_cap = 0;
static TLS int *lr_base, lr_stride, lr_ext, lr_v0;
#define LROW( r ) ( lr_base + (size_t)( r ) * lr_stride )
#if MAFFT_LBAND
/* Set by Lfill_band: only cells with lr_blo <= i-j <= lr_bhi were filled (see Lfill_band). */
static TLS int lr_banded = 0, lr_blo, lr_bhi;
#endif

/* hk[j] of row i: first k in [0, j-2] maximising prev[k] - k*ext (the first k of a tie), k = 0 for j < 2 */
static int lres_hk( int i, int j )
{
#if MAFFT_LBAND
	/* banded: the k of row i-1 below its first filled column are out of the band */
	int *prev = LROW( i-1 ), jj, k0 = ( lr_banded && i-1-lr_bhi > 0 ) ? i-1-lr_bhi : 0, tbest = prev[k0], tbestk = k0;
	for( jj=k0+2; jj<=j; jj++ )
#else
	int *prev = LROW( i-1 ), jj, tbest = prev[0], tbestk = 0;
	for( jj=2; jj<=j; jj++ )
#endif
	{
		int q = prev[jj-2] - ( jj-2 ) * lr_ext;
		if( q > tbest ) { tbest = q; tbestk = jj-2; }
	}
	return( tbestk );
}

/* vmp[j] as row i reads it: the vertical state started from row 0 and updated by rows 1 .. i-1 */
static int lres_vmp( int i, int j )
{
#if MAFFT_LBAND
	/* banded: column j-1 is filled from row q0 = j-1+lr_blo down; above it the state is empty */
	int q0 = ( lr_banded && j-1+lr_blo > 0 ) ? j-1+lr_blo : 0, r;
	int vm = q0 ? LBAND_NEG : ( j == 1 ) ? lr_v0 : LROW( 0 )[j-1], vmp = q0;
	for( r=q0+1; r<i; r++ )
#else
	int r, vm = ( j == 1 ) ? lr_v0 : LROW( 0 )[j-1], vmp = 0;
	for( r=1; r<i; r++ )
#endif
	{
		int p = LROW( r-1 )[j-1];
		if( p > vm ) { vm = p; vmp = r-1; }
		vm += lr_ext;
	}
	return( vmp );
}
#endif

#if defined(MAFFT_LSTRIPED)
static int kb_ijv( int i, int j, int lstop );
static int kb_isstop( int i, int j );
#if MAFFT_LBAND
static int lband_ijv( int i, int j, int lstop );
static int lband_isstop( int i, int j );
#define LKB_ISSTOP( kb, i, j ) ( (kb) == 2 ? lband_isstop( (i), (j) ) : kb_isstop( (i), (j) ) )
#else
#define LKB_ISSTOP( kb, i, j ) kb_isstop( (i), (j) )
#endif
#endif
/* kb: the fill was Lfill_kb, whose traceback is read through kb_ijv() instead of ijp */
static double Ltracking( double *lasthorizontalw, double *lastverticalw, 
						char **seq1, char **seq2, 
                        char **mseq1, char **mseq2, 
                        int **ijp, int *off1pt, int *off2pt, int endi, int endj,
						int *warpis, int *warpjs, int warpbase, int kb )
{
	int i, j, l, iin, jin, lgth1, lgth2, k, limk;
	int ifi=0, jfi=0; // by D.Mathog, a guess
//	char gap[] = "-";
	char *gap;
	gap = newgapstr;
	lgth1 = strlen( seq1[0] );
	lgth2 = strlen( seq2[0] );

#if 0
	for( i=0; i<lgth1; i++ ) 
	{
		fprintf( stderr, "lastverticalw[%d] = %f\n", i, lastverticalw[i] );
	}
#endif
 
#if defined(MAFFT_LSTRIPED) && MAFFT_KB_NOIJP
	if( !kb )
#endif
    for( i=0; i<lgth1+1; i++ )
    {
        ijp[i][0] = localstop;
    }
#if defined(MAFFT_LSTRIPED) && MAFFT_KB_NOIJP
	if( !kb )
#endif
    for( j=0; j<lgth2+1; j++ )
    {
        ijp[0][j] = localstop;
    }

	mseq1[0] += lgth1+lgth2;
	*mseq1[0] = 0;
	mseq2[0] += lgth1+lgth2;
	*mseq2[0] = 0;
	iin = endi; jin = endj;
	limk = lgth1+lgth2;
	for( k=0; k<=limk; k++ ) 
	{
		int ijv;
#if defined(MAFFT_LSTRIPED) && MAFFT_KB_NOIJP
		if( !kb ) ijv = ijp[iin][jin];
#else
		ijv = ijp[iin][jin];
#endif
#if defined(MAFFT_LSTRIPED)
#if MAFFT_LBAND
		if( kb == 2 ) ijv = lband_ijv( iin, jin, localstop );
		else
#endif
		if( kb ) ijv = kb_ijv( iin, jin, localstop );
		else
#endif
#ifdef LFILL_MARKS
		if( ijv == LMARK_H ) ijv = -( jin - lres_hk( iin, jin ) );
		else if( ijv == LMARK_V ) ijv = iin - lres_vmp( iin, jin );
#endif
		(void)kb;
		if( ijv >= warpbase )
		{
//			fprintf( stderr, "WARP!\n" );
			ifi = warpis[ijv-warpbase];
			jfi = warpjs[ijv-warpbase];
		}
		else if( ijv < 0 ) 
		{
			ifi = iin-1; jfi = jin+ijv;
		}
		else if( ijv > 0 )
		{
			ifi = iin-ijv; jfi = jin-1;
		}
		else
		{
			ifi = iin-1; jfi = jin-1;
		}


#if 1 // sentou de warp?
		if( ifi == -warpbase && jfi == -warpbase )
		{
			l = iin;
			while( --l >= 0 ) 
			{
				*--mseq1[0] = seq1[0][l];
				*--mseq2[0] = *gap;
				k++;
			}
			l= jin;
			while( --l >= 0 )
			{
				*--mseq1[0] = *gap;
				*--mseq2[0] = seq2[0][l];
				k++;
			}
			break;
		}
		else
#endif
		{
			l = iin - ifi;
			while( --l > 0 ) 
			{
				*--mseq1[0] = seq1[0][ifi+l];
				*--mseq2[0] = *gap;
				k++;
			}
			l= jin - jfi;
			while( --l > 0 )
			{
				*--mseq1[0] = *gap;
				*--mseq2[0] = seq2[0][jfi+l];
				k++;
			}
		}


		if( iin <= 0 || jin <= 0 ) break;
		*--mseq1[0] = seq1[0][ifi];
		*--mseq2[0] = seq2[0][jfi];
#if defined(MAFFT_LSTRIPED)
		if( kb ? LKB_ISSTOP( kb, ifi, jfi ) : ijp[ifi][jfi] == localstop ) break;
#else
		if( ijp[ifi][jfi] == localstop ) break;
#endif
		k++;
		iin = ifi; jin = jfi;
	}
	if( ifi == -1 ) *off1pt = 0; else *off1pt = ifi;
	if( jfi == -1 ) *off2pt = 0; else *off2pt = jfi;

//	fprintf( stderr, "ifn = %d, jfn = %d\n", ifi, jfi );
//	fprintf( stderr, "\n" );
//	fprintf( stderr, "%s\n", mseq1[0] );
//	fprintf( stderr, "%s\n", mseq2[0] );


	return( 0.0 );
}


/*
 * The preconditions of the integer fills (Lfill_int, Lfill_kb), shared by all of them: every score
 * and penalty integral and small enough that no intermediate comes near int32 overflow.  Sets
 * *ithrpt and used[c] (character c occurs in s1 or s2).  Returns 0 when the double-precision fill
 * must be used instead.
 */
static int lfill_prepare( double **amino_dynamicmtx, double **n_dynamicmtx, double scoreoffset,
                          unsigned char *u1, unsigned char *u2, int lgth1, int lgth2, int *ithrpt, unsigned char *used )
{
	int i, j, c, k, ithr;
	int pen = penalty, ext = penalty_ex;
	double maxabs = 0.0, thr, span, basebound;
	if( lgth1 < 1 || lgth2 < 1 ) return( 0 );
	if( !isfinite(scoreoffset) ) return( 0 );
	/* Conversion must follow finite/range checks. Widen before negating the
	   integer offset too: -INT_MIN and abs(INT_MIN) overflow in integer C. */
	thr = ( offset ? -(double)offset : 0.0 ) + scoreoffset * 600;
	if( !isfinite(thr) || thr < (double)INT_MIN || thr > (double)INT_MAX ) return( 0 );
	if( thr != (double)(int)thr ) return( 0 );
	ithr = (int)thr;
	span = (double)lgth1 + (double)lgth2 + 4.0;
	basebound = fabs((double)pen) + fabs((double)ext) + fabs((double)ithr) + 1.0;
	/* This lower bound also rejects impossible lengths before scanning them. */
	if( basebound * span * 2.0 > 5.0e8 ) return( 0 );
	for( i=0; i<nalphabets; i++ ) for( j=0; j<nalphabets; j++ )
	{
		double v = n_dynamicmtx[i][j];
		if( !isfinite(v) || v < (double)INT_MIN || v > (double)INT_MAX ) return( 0 );
		if( v != (double)(int)v ) return( 0 );
		if( fabs(v) > maxabs ) maxabs = fabs(v);
	}
	/* Characters outside the alphabet read entries that were never set from n_dynamicmtx. */
	memset( used, 0, 0x100 );
	for( i=0; i<lgth1; i++ ) used[u1[i]] = 1;
	for( j=0; j<lgth2; j++ ) used[u2[j]] = 1;
	for( c=0; c<0x100; c++ ) if( used[c] )
	{
		for( k=0; k<0x100; k++ ) if( used[k] )
		{
			double v = amino_dynamicmtx[c][k];
			if( !isfinite(v) || fabs(v) > 1e6 || v != (double)(int)v ) return( 0 );
			if( fabs(v) > maxabs ) maxabs = fabs(v);
		}
	}
	/* Keep every intermediate (including prev[k] - k*ext) far from int32 overflow. */
	{
		double bound = (maxabs + basebound) * span * 2.0;
		if( bound > 5.0e8 ) return( 0 );
	}
	*ithrpt = ithr;
	return( 1 );
}

/*
 * Integer version of the L__align11 fill (trywarp == 0 only).
 *
 * When every score and penalty is integral (the default: n_dis * consweight_multi with
 * consweight_multi == 1.0, integer penalties, scoreoffset == 0), the double DP below only ever
 * holds integers well inside 2^53, so an int32 DP makes exactly the same comparisons and yields
 * the same ijp[][], maxwm and end point.  Every candidate for cell (i,j) is derived from row i-1,
 * so a whole row can be computed several cells at a time.
 *
 * Returns 0 (and does nothing) when the integral/range preconditions do not hold.
 */
#ifdef LFILL_MARKS
/*
 * Lfill_int for AVX2 (see LFILL_MARKS above): the same integer fill, 8 cells at a time, storing
 * LMARK_H / LMARK_V instead of gap offsets, so neither the first-index scan for the horizontal
 * state nor the vmp[] rows are needed.  Values, wm, the threshold clamp, ijp's 0 / lstop cells,
 * maxwm and the end point are computed exactly as in the original.  On arm64 the same loop
 * runs 4 cells at a time with NEON, or svcntw() at a time with SVE when the vectors are at
 * least 256 bits wide (Graviton3); 128-bit SVE (Graviton4) uses the NEON loop.
 */
#if defined(MAFFT_A64)
#define LPROF_STRIDE ( ( lgth2 + 4 + 3 ) & ~3 ) /* each profile row 16-byte aligned */
#else
#define LPROF_STRIDE ( lgth2 + 4 )
#endif
static int Lfill_int( double **amino_dynamicmtx, double **n_dynamicmtx, double scoreoffset,
                      char *s1, char *s2, int lgth1, int lgth2, int **ijp, int lstop,
                      double *maxwmpt, int *endalipt, int *endaljpt )
{
	int i, j, c, k;
	int pen = penalty, ext = penalty_ex;
	int ithr, *prof[0x100], *profbuf, nprof = 0;
	int *prev, *cur, *vm, *hq, *wmrow, *vmraw;
	int needwm, endpend = 0;
#if defined(MAFFT_A64)
	int *kx, *kp;
#endif
#if defined(MAFFT_SVE)
	/* lsve_nstep: 0 (use NEON), else the log2 of the lanes per SVE vector (2..4 handled) */
	int vl = (int)svcntw(), lsve_nstep = ( vl >= LSVE_MINLANES && vl <= 16 ) ? ( vl >= 16 ? 4 : vl >= 8 ? 3 : 2 ) : 0;
#endif
	int tbest, jj;
	int maxwm, endali = 0, endalj = 0, rowmax;
	unsigned char *u1 = (unsigned char *)s1, *u2 = (unsigned char *)s2;
	unsigned char used[0x100];
	size_t need;

	if( !lfill_prepare( amino_dynamicmtx, n_dynamicmtx, scoreoffset, u1, u2, lgth1, lgth2, &ithr, used ) ) return( 0 );
#if MAFFT_LBAND
	lr_banded = 0; /* a full fill: lres_hk()/lres_vmp() replay whole rows and columns */
#endif

	for( c=0; c<0x100; c++ ) prof[c] = NULL;
	for( c=0; c<0x100; c++ ) if( used[c] ) nprof++;
	profbuf = malloc( sizeof( int ) * nprof * LPROF_STRIDE );
	if( profbuf == NULL ) return( 0 ); /* out of memory: the double-precision fill */
	k = 0;
	for( c=0; c<0x100; c++ ) if( used[c] )
	{
		double *row = amino_dynamicmtx[c];
		prof[c] = profbuf + k * LPROF_STRIDE;
		for( j=0; j<lgth2; j++ ) prof[c][j] = (int)row[u2[j]];
		prof[c][lgth2] = 0;
		k++;
	}

	/* All rows, each with one slot in front (prev[-1] seeds the vector scan); rows start so that
	   cell 1 of every row is 32-byte aligned.  Kept between calls: a fresh 3 MB mapping per pair
	   costs more in page faults than the fill. */
	lr_stride = ( ( lgth2 + 9 ) + 7 ) & ~7;
	need = (size_t)( lgth1 + 1 ) * lr_stride + 16;
	/* The rows double the memory of ijp[][]; past 256 MB leave it to the double-precision fill. */
	if( need > ( (size_t)1 << 26 ) ) { free( profbuf ); return( 0 ); }
	if( need > lr_cap )
	{
		free( lr_raw );
		lr_raw = malloc( sizeof( int ) * need );
		lr_cap = lr_raw ? need : 0;
		if( lr_raw == NULL ) { free( profbuf ); return( 0 ); }
	}
#ifdef MAFFT_POISON_TEST
	memset( lr_raw, 0xa5, sizeof( int ) * need );
#endif
#if defined(MAFFT_A64)
	/* the NEON loop starts at cell 4 (ijp rows come from malloc, 16-byte aligned): row cell 4 aligned too */
	lr_base = (int *)( ( (uintptr_t)( lr_raw + 8 ) & ~(uintptr_t)31 ) );
#else
	lr_base = (int *)( ( (uintptr_t)( lr_raw + 8 ) & ~(uintptr_t)31 ) ) + 7; /* lr_base + 1 is 32-byte aligned */
#endif
	lr_ext = ext;
	vmraw = malloc( sizeof( int ) * ( lgth2 + 16 ) );
	if( vmraw == NULL ) { free( profbuf ); return( 0 ); }
#if defined(MAFFT_A64)
	vm    = (int *)( ( (uintptr_t)( vmraw + 8 ) & ~(uintptr_t)31 ) );
	/* kx[k] = k*ext, kp[k] = k*ext + ext + pen (horizontal gap scan offsets, read by the NEON loop) */
	kx = malloc( sizeof( int ) * 2 * ( lgth2 + 8 ) );
	if( kx == NULL ) { free( profbuf ); free( vmraw ); return( 0 ); }
	kp = kx + lgth2 + 8;
	for( k=0; k<lgth2+8; k++ ) { kx[k] = k * ext; kp[k] = k * ext + ext + pen; }
#else
	vm    = (int *)( ( (uintptr_t)( vmraw + 8 ) & ~(uintptr_t)31 ) ) + 7;
#endif
	hq    = malloc( sizeof( int ) * ( lgth2 + 4 ) );
	wmrow = malloc( sizeof( int ) * ( lgth2 + 4 ) );
	if( !hq || !wmrow )
	{
		free( profbuf ); free( vmraw ); free( hq ); free( wmrow );
#if defined(MAFFT_A64)
		free( kx );
#endif
		return( 0 );
	}
#ifdef MAFFT_POISON_TEST
	memset( vmraw, 0xa5, sizeof( int ) * ( lgth2 + 16 ) ); memset( hq, 0xa5, sizeof( int ) * ( lgth2 + 4 ) ); memset( wmrow, 0xa5, sizeof( int ) * ( lgth2 + 4 ) );
#endif

	/* row 0: currentw[j] = mtx[s1[0]][s2[j]];  m[j] = currentw[j-1] */
	cur = LROW( 0 );
	for( j=0; j<lgth2; j++ ) cur[j] = prof[u1[0]][j];
	cur[lgth2] = 0;
	for( j=1; j<=lgth2; j++ ) vm[j] = cur[j-1];
	lr_v0 = cur[0]; /* m[1] before row 1 overwrites cur[0] */

	maxwm = INT_MIN;
	for( i=1; i<=lgth1; i++ )
	{
		int *profrow = ( i < lgth1 ) ? prof[u1[i]] : NULL;
		int *ijrow = ijp[i];
		prev = LROW( i-1 ); cur = LROW( i );
		/* previousw[0] = initverticalw[i-1] = mtx[s2[0]][s1[i-1]] */
		prev[0] = (int)amino_dynamicmtx[u2[0]][u1[i-1]];
		/* H_j = (j-1)*ext + max_{k<=max(j-2,0)} (prev[k] - k*ext); prev[-1] stands for the k=0 term of H_1 */
		prev[-1] = prev[0] - ext;
		tbest = prev[0];

		rowmax = INT_MIN;
		if( !profrow ) profrow = prof[u1[0]]; /* last row: cur[] is never read again */
		j = 1;
		/* Once maxwm > ithr, a row whose maximum beats it has rowmax > ithr, and its first cell
		   holding rowmax is the first j with cur[j] - profrow[j] == rowmax (a clamped cell gives
		   ithr there, below rowmax; any other gives its wm).  The vector loops then skip wmrow,
		   and the search is done once, after the last row, on the row that set maxwm. */
		needwm = ( maxwm <= ithr );
#if defined(MAFFT_A64)
		if( lgth2 >= 7 )
		{
			/* Cells 1..3 in scalar code (the same rules as the remaining cells below), so that the
			   vector loop's stores into ijp, cur, vm and wmrow are 16-byte aligned. */
			for( ; j<4; j++ )
			{
				int p = prev[j-1], wm = p, ij = 0, g;
				if( j >= 2 )
				{
					int q = prev[j-2] - ( j-2 ) * ext;
					if( q > tbest ) tbest = q;
				}
				g = tbest + ( j-1 ) * ext + pen;
				if( g > wm ) { wm = g; ij = LMARK_H; }
				g = vm[j] + pen;
				if( g > wm ) { wm = g; ij = LMARK_V; }
				if( p > vm[j] ) vm[j] = p;
				vm[j] += ext;
				wmrow[j] = wm;
				rowmax = ( wm > rowmax ) ? wm : rowmax;
				if( wm < ithr ) { ij = lstop; wm = ithr; }
				ijrow[j] = ij;
				cur[j] = wm + profrow[j];
			}
		}
#if defined(MAFFT_SVE)
		if( j == 4 && lsve_nstep )
		{
			/* SVE, vector-length agnostic: svcntw() cells at a time (chosen only for vectors of at
			   least LSVE_MINLANES lanes; 128-bit SVE has the NEON loop's width and more work per
			   step).  The same operations as the NEON loop: the in-block prefix max shifts lanes up
			   with tbl (an out-of-range index reads 0) and a max predicated on the lanes that
			   received a value. */
			const svbool_t pt = svptrue_b32();
			const svint32_t vpen = svdup_n_s32( pen ), vext = svdup_n_s32( ext ), vthr = svdup_n_s32( ithr );
			const svint32_t vstop = svdup_n_s32( lstop ), vmarkh = svdup_n_s32( LMARK_H ), vmarkv = svdup_n_s32( LMARK_V );
			const svint32_t zero = svdup_n_s32( 0 );
			const svuint32_t lane = svindex_u32( 0, 1 ), last = svdup_n_u32( (unsigned)vl - 1 );
			const svuint32_t ix1 = svsub_n_u32_x( pt, lane, 1 ), ix2 = svsub_n_u32_x( pt, lane, 2 ), ix4 = svsub_n_u32_x( pt, lane, 4 ), ix8 = svsub_n_u32_x( pt, lane, 8 );
			const svbool_t ge1 = svcmpge_n_u32( pt, lane, 1 ), ge2 = svcmpge_n_u32( pt, lane, 2 ), ge4 = svcmpge_n_u32( pt, lane, 4 ), ge8 = svcmpge_n_u32( pt, lane, 8 );
			svint32_t vmax = svdup_n_s32( rowmax ), cv = svdup_n_s32( tbest );
#define LSVE_BLOCK( J, W ) \
			{ \
				svint32_t p = svld1_s32( pt, prev + (J) - 1 ); \
				svint32_t v = svsub_s32_x( pt, svld1_s32( pt, prev + (J) - 2 ), svld1_s32( pt, kx + (J) - 2 ) ); \
				svint32_t x, g, wm, ij, vmj; \
				svbool_t ch, cvt, clamp; \
				x = svmax_s32_m( ge1, v, svtbl_s32( v, ix1 ) ); \
				x = svmax_s32_m( ge2, x, svtbl_s32( x, ix2 ) ); \
				if( lsve_nstep > 2 ) x = svmax_s32_m( ge4, x, svtbl_s32( x, ix4 ) ); \
				if( lsve_nstep > 3 ) x = svmax_s32_m( ge8, x, svtbl_s32( x, ix8 ) ); \
				x = svmax_s32_x( pt, x, cv ); \
				cv = svtbl_s32( x, last ); \
				wm = p; \
				g = svadd_s32_x( pt, x, svld1_s32( pt, kp + (J) - 2 ) );   /* hq + pen */ \
				ch = svcmpgt_s32( pt, g, wm ); \
				wm = svmax_s32_x( pt, wm, g ); \
				vmj = svld1_s32( pt, vm + (J) ); \
				g = svadd_s32_x( pt, vmj, vpen ); \
				cvt = svcmpgt_s32( pt, g, wm ); \
				wm = svmax_s32_x( pt, wm, g ); \
				svst1_s32( pt, vm + (J), svadd_s32_x( pt, svmax_s32_x( pt, p, vmj ), vext ) ); \
				if( W ) svst1_s32( pt, wmrow + (J), wm ); \
				vmax = svmax_s32_x( pt, vmax, wm ); \
				clamp = svcmpgt_s32( pt, vthr, wm ); \
				ij = svsel_s32( ch, vmarkh, zero ); \
				ij = svsel_s32( cvt, vmarkv, ij ); \
				ij = svsel_s32( clamp, vstop, ij ); \
				wm = svmax_s32_x( pt, wm, vthr ); \
				svst1_s32( pt, ijrow + (J), ij ); \
				svst1_s32( pt, cur + (J), svadd_s32_x( pt, wm, svld1_s32( pt, profrow + (J) ) ) ); \
			}
			if( needwm )
				for( ; j+2*vl-1<=lgth2; j+=2*vl )
				{
					LSVE_BLOCK( j, 1 )
					LSVE_BLOCK( j+vl, 1 )
				}
			else
				for( ; j+2*vl-1<=lgth2; j+=2*vl )
				{
					LSVE_BLOCK( j, 0 )
					LSVE_BLOCK( j+vl, 0 )
				}
			for( ; j+vl-1<=lgth2; j+=vl )
				LSVE_BLOCK( j, needwm )
#undef LSVE_BLOCK
			rowmax = svmaxv_s32( pt, vmax );
			tbest = svlastb_s32( pt, cv );
		}
		else
#endif
		if( j == 4 )
		{
			/* 4 cells at a time, two blocks per iteration.  k*ext and k*ext+ext+pen come from tables
			   (loads, not vector adds).  ij goes through an empty asm so that the compiler keeps
			   the two selects instead of rebuilding them from and/or. */
			const int32x4_t vpen = vdupq_n_s32( pen ), vext = vdupq_n_s32( ext ), vthr = vdupq_n_s32( ithr );
			const int32x4_t vstop = vdupq_n_s32( lstop ), vmarkh = vdupq_n_s32( LMARK_H ), vmarkv = vdupq_n_s32( LMARK_V );
			const int32x4_t ninf = vdupq_n_s32( INT_MIN );
			int32x4_t vmax = vdupq_n_s32( rowmax ), cv = vdupq_n_s32( tbest );
#define LNEON_BLOCK( J, W ) \
			{ \
				int32x4_t p = vld1q_s32( prev + (J) - 1 ); \
				int32x4_t v = vsubq_s32( vld1q_s32( prev + (J) - 2 ), vld1q_s32( kx + (J) - 2 ) ); \
				int32x4_t x, g, wm, ij, vmj; \
				uint32x4_t ch, cvt, clamp; \
				x = vmaxq_s32( v, vextq_s32( ninf, v, 3 ) ); \
				x = vmaxq_s32( x, vextq_s32( ninf, x, 2 ) ); \
				x = vmaxq_s32( x, cv ); \
				cv = vdupq_laneq_s32( x, 3 ); \
				wm = p; \
				g = vaddq_s32( x, vld1q_s32( kp + (J) - 2 ) );   /* hq + pen */ \
				ch = vcgtq_s32( g, wm ); \
				wm = vmaxq_s32( wm, g ); \
				vmj = vld1q_s32( vm + (J) ); \
				g = vaddq_s32( vmj, vpen ); \
				cvt = vcgtq_s32( g, wm ); \
				wm = vmaxq_s32( wm, g ); \
				vst1q_s32( vm + (J), vaddq_s32( vmaxq_s32( p, vmj ), vext ) ); \
				if( W ) vst1q_s32( wmrow + (J), wm ); \
				vmax = vmaxq_s32( vmax, wm ); \
				clamp = vcgtq_s32( vthr, wm ); \
				ij = vandq_s32( vreinterpretq_s32_u32( ch ), vmarkh ); \
				LNEON_OPAQUE( ij ); \
				ij = vbslq_s32( cvt, vmarkv, ij ); \
				LNEON_OPAQUE( ij ); \
				ij = vbslq_s32( clamp, vstop, ij ); \
				wm = vmaxq_s32( wm, vthr ); \
				vst1q_s32( ijrow + (J), ij ); \
				vst1q_s32( cur + (J), vaddq_s32( wm, vld1q_s32( profrow + (J) ) ) ); \
			}
#define LNEON_OPAQUE( r ) __asm__( "" : "+w"( r ) )
			if( needwm )
				for( ; j+7<=lgth2; j+=8 )
				{
					LNEON_BLOCK( j, 1 )
					LNEON_BLOCK( j+4, 1 )
				}
			else
				for( ; j+7<=lgth2; j+=8 )
				{
					LNEON_BLOCK( j, 0 )
					LNEON_BLOCK( j+4, 0 )
				}
			for( ; j+3<=lgth2; j+=4 )
				LNEON_BLOCK( j, needwm )
#undef LNEON_BLOCK
#undef LNEON_OPAQUE
			rowmax = vmaxvq_s32( vmax );
			tbest = vgetq_lane_s32( cv, 0 );
		}
#else
		{
			/* prefix max with in-lane byte shifts (vpslldq) and the fill OR-ed in, one vpermd for the
			   half carry; blends are and/xor (vpblendvb is two port-5 uops on Haswell/Broadwell). */
#define AVX2_BLEND( a, b, m ) _mm256_xor_si256( (a), _mm256_and_si256( _mm256_xor_si256( (a), (b) ), (m) ) )
			const __m256i vpen = _mm256_set1_epi32( pen ), vext = _mm256_set1_epi32( ext ), vthr = _mm256_set1_epi32( ithr );
			const __m256i vstop = _mm256_set1_epi32( lstop ), vmarkh = _mm256_set1_epi32( LMARK_H ), vmarkv = _mm256_set1_epi32( LMARK_V );
			const __m256i ninf = _mm256_set1_epi32( INT_MIN );
			const __m256i fill1 = _mm256_setr_epi32( INT_MIN, 0, 0, 0, INT_MIN, 0, 0, 0 );
			const __m256i fill2 = _mm256_setr_epi32( INT_MIN, INT_MIN, 0, 0, INT_MIN, INT_MIN, 0, 0 );
			const __m256i idx3 = _mm256_set1_epi32( 3 ), last = _mm256_set1_epi32( 7 );
			const __m256i ext8 = _mm256_set1_epi32( 8 * ext );
			__m256i vmax = ninf, cv = ninf;
			/* lane l of the block at j stands for k = j-2+l */
			__m256i kext = _mm256_mullo_epi32( _mm256_setr_epi32( -1, 0, 1, 2, 3, 4, 5, 6 ), vext );
			__m256i kext1pen = _mm256_add_epi32( kext, _mm256_set1_epi32( ext + pen ) );
			for( ; j+7<=lgth2; j+=8 )
			{
				__m256i p = _mm256_loadu_si256( (__m256i *)( prev + j - 1 ) );
				__m256i v = _mm256_sub_epi32( _mm256_loadu_si256( (__m256i *)( prev + j - 2 ) ), kext );
				__m256i x, g, wm, ch, cvt, clamp, ij, vmj;
				x = _mm256_max_epi32( v, _mm256_or_si256( _mm256_bslli_epi128( v, 4 ), fill1 ) );
				x = _mm256_max_epi32( x, _mm256_or_si256( _mm256_bslli_epi128( x, 8 ), fill2 ) );
				x = _mm256_max_epi32( x, _mm256_blend_epi32( ninf, _mm256_permutevar8x32_epi32( x, idx3 ), 0xf0 ) );
				x = _mm256_max_epi32( x, cv );
				cv = _mm256_permutevar8x32_epi32( x, last );

				wm = p;
				g = _mm256_add_epi32( x, kext1pen );   /* hq + pen */
				ch = _mm256_cmpgt_epi32( g, wm );
				wm = _mm256_max_epi32( wm, g );
				vmj = _mm256_load_si256( (__m256i *)( vm + j ) );
				g = _mm256_add_epi32( vmj, vpen );
				cvt = _mm256_cmpgt_epi32( g, wm );
				wm = _mm256_max_epi32( wm, g );
				_mm256_store_si256( (__m256i *)( vm + j ), _mm256_add_epi32( _mm256_max_epi32( p, vmj ), vext ) );
				if( needwm ) _mm256_storeu_si256( (__m256i *)( wmrow + j ), wm );
				vmax = _mm256_max_epi32( vmax, wm );
				clamp = _mm256_cmpgt_epi32( vthr, wm );
				ij = _mm256_and_si256( ch, vmarkh );
				ij = AVX2_BLEND( ij, vmarkv, cvt );
				ij = AVX2_BLEND( ij, vstop, clamp );
				wm = _mm256_max_epi32( wm, vthr );
				_mm256_storeu_si256( (__m256i *)( ijrow + j ), ij );
				_mm256_store_si256( (__m256i *)( cur + j ), _mm256_add_epi32( wm, _mm256_loadu_si256( (__m256i *)( profrow + j ) ) ) );
				kext = _mm256_add_epi32( kext, ext8 );
				kext1pen = _mm256_add_epi32( kext1pen, ext8 );
			}
#undef AVX2_BLEND
			{
				__m128i h = _mm_max_epi32( _mm256_castsi256_si128( vmax ), _mm256_extracti128_si256( vmax, 1 ) );
				h = _mm_max_epi32( h, _mm_shuffle_epi32( h, 0x4e ) );
				h = _mm_max_epi32( h, _mm_shuffle_epi32( h, 0xb1 ) );
				rowmax = _mm_cvtsi128_si32( h );
			}
			if( j > 1 ) tbest = _mm256_cvtsi256_si32( cv );
		}
#endif
		/* the remaining cells */
		for( jj=j; jj<=lgth2; jj++ )
		{
			if( jj >= 2 )
			{
				int q = prev[jj-2] - ( jj-2 ) * ext;
				if( q > tbest ) tbest = q;
			}
			hq[jj] = tbest + ( jj-1 ) * ext;
		}
		for( ; j<=lgth2; j++ )
		{
			int p = prev[j-1];
			int wm = p, ij = 0, g;
			g = hq[j] + pen;
			if( g > wm ) { wm = g; ij = LMARK_H; }
			g = vm[j] + pen;
			if( g > wm ) { wm = g; ij = LMARK_V; }
			if( p > vm[j] ) vm[j] = p;
			vm[j] += ext;
			wmrow[j] = wm;
			rowmax = ( wm > rowmax ) ? wm : rowmax;
			if( wm < ithr ) { ij = lstop; wm = ithr; }
			ijrow[j] = ij;
			cur[j] = wm + profrow[j];
		}
		if( rowmax > maxwm && !needwm )
		{
			maxwm = rowmax; endali = i; endpend = 1;
		}
		else if( rowmax > maxwm )
		{
			endpend = 0;
#if defined(MAFFT_A64)
			/* first cell holding rowmax (it is always present): 16 cells per test, then 4 */
			int32x4_t vr = vdupq_n_s32( rowmax );
			j = 1;
			for( ; j+15<=lgth2; j+=16 )
				if( vmaxvq_u32( vorrq_u32( vorrq_u32( vceqq_s32( vld1q_s32( wmrow + j ), vr ), vceqq_s32( vld1q_s32( wmrow + j + 4 ), vr ) ),
				                           vorrq_u32( vceqq_s32( vld1q_s32( wmrow + j + 8 ), vr ), vceqq_s32( vld1q_s32( wmrow + j + 12 ), vr ) ) ) ) ) break;
			for( ; j+3<=lgth2; j+=4 )
				if( vmaxvq_u32( vceqq_s32( vld1q_s32( wmrow + j ), vr ) ) ) break;
#else
			/* first cell holding rowmax, 8 at a time (it is always present) */
			__m256i vr = _mm256_set1_epi32( rowmax );
			j = 1;
			for( ; j+7<=lgth2; j+=8 )
			{
				int km = _mm256_movemask_ps( _mm256_castsi256_ps( _mm256_cmpeq_epi32( _mm256_loadu_si256( (__m256i *)( wmrow + j ) ), vr ) ) );
				if( km ) { j += __builtin_ctz( (unsigned)km ); break; }
			}
#endif
			for( ; wmrow[j] != rowmax; j++ )
				;
			maxwm = rowmax; endali = i; endalj = j;
		}
		if( i < lgth1 ) cur[0] = (int)amino_dynamicmtx[u2[0]][u1[i]]; /* currentw[0] = initverticalw[i] */
	}

	if( endpend )
	{
		int *r = LROW( endali ), *pr = ( endali < lgth1 ) ? prof[u1[endali]] : prof[u1[0]];
		for( j=1; r[j] - pr[j] != maxwm; j++ )
			;
		endalj = j;
	}
	free( profbuf ); free( vmraw ); free( hq ); free( wmrow );
#if defined(MAFFT_A64)
	free( kx );
#endif
	*maxwmpt = (double)maxwm;
	*endalipt = endali;
	*endaljpt = endalj;
	return( 1 );
}
#if MAFFT_LBAND
/*
 * Lfill_band: Lfill_int restricted to the cells with lo <= i-j <= hi.  Row i fills
 * j in [ja, jb] = [max(1,i-hi), min(lgth2,i-lo)] with Lfill_int's rules; a state built only
 * from cells outside the band is LBAND_NEG.  Every cell the window reads is in the band: the
 * diagonal prev[j-1] and the horizontal terms prev[k] (k >= ja-1, or column 0 when ja == 1)
 * lie in row i-1's window, and column j enters the window exactly when cell (i-1,j-1) does,
 * so its vertical state starts empty there (row 1 starts from row 0, as Lfill_int does).
 * lres_hk()/lres_vmp() replay the same restricted rules.  The result is the band-restricted
 * recurrence; Lband_try() below uses it only where the certificate makes it the full one.
 */
static int Lfill_band( double **amino_dynamicmtx, double **n_dynamicmtx, double scoreoffset,
                       char *s1, char *s2, int lgth1, int lgth2, int **ijp, int lstop, int lo, int hi,
                       double *maxwmpt, int *endalipt, int *endaljpt )
{
	int i, j, c, k, ja, jb, jbprev;
	int pen = penalty, ext = penalty_ex;
	int ithr, *prof[0x100], *profbuf, nprof = 0;
	int *prev, *cur, *vm, *wmrow;
	int needwm, endpend = 0, tbest;
	int maxwm, endali = 0, endalj = 0, rowmax;
	unsigned char *u1 = (unsigned char *)s1, *u2 = (unsigned char *)s2;
	unsigned char used[0x100];
	size_t need;

	if( !lfill_prepare( amino_dynamicmtx, n_dynamicmtx, scoreoffset, u1, u2, lgth1, lgth2, &ithr, used ) ) return( 0 );
	for( c=0; c<0x100; c++ ) prof[c] = NULL;
	for( c=0; c<0x100; c++ ) if( used[c] ) nprof++;
	profbuf = malloc( sizeof( int ) * nprof * LPROF_STRIDE );
	if( profbuf == NULL ) return( 0 );
	k = 0;
	for( c=0; c<0x100; c++ ) if( used[c] )
	{
		double *row = amino_dynamicmtx[c];
		prof[c] = profbuf + k * LPROF_STRIDE;
		for( j=0; j<lgth2; j++ ) prof[c][j] = (int)row[u2[j]];
		prof[c][lgth2] = 0;
		k++;
	}
	lr_stride = ( ( lgth2 + 9 ) + 7 ) & ~7;
	need = (size_t)( lgth1 + 1 ) * lr_stride + 16;
	if( need > ( (size_t)1 << 26 ) ) { free( profbuf ); return( 0 ); }
	if( need > lr_cap )
	{
		free( lr_raw );
		lr_raw = malloc( sizeof( int ) * need );
		lr_cap = lr_raw ? need : 0;
		if( lr_raw == NULL ) { free( profbuf ); return( 0 ); }
	}
	lr_base = (int *)( ( (uintptr_t)( lr_raw + 8 ) & ~(uintptr_t)31 ) ) + 7;
	lr_ext = ext;
	vm = malloc( sizeof( int ) * ( lgth2 + 16 ) );
	wmrow = malloc( sizeof( int ) * ( lgth2 + 16 ) );
	if( !vm || !wmrow ) { free( profbuf ); free( vm ); free( wmrow ); return( 0 ); }

	cur = LROW( 0 );
	for( j=0; j<lgth2; j++ ) cur[j] = prof[u1[0]][j];
	cur[lgth2] = 0;
	lr_v0 = cur[0];
	jbprev = ( 1-lo < lgth2 ) ? 1-lo : lgth2;
	for( j=1; j<=jbprev; j++ ) vm[j] = cur[j-1];

	maxwm = INT_MIN;
	for( i=1; i<=lgth1; i++ )
	{
		int *profrow = ( i < lgth1 ) ? prof[u1[i]] : prof[u1[0]]; /* last row: as Lfill_int */
		int *ijrow = ijp[i];
		prev = LROW( i-1 ); cur = LROW( i );
		prev[0] = (int)amino_dynamicmtx[u2[0]][u1[i-1]];
		ja = ( i-hi > 1 ) ? i-hi : 1;
		jb = ( i-lo < lgth2 ) ? i-lo : lgth2;
		for( j=jbprev+1; j<=jb; j++ ) vm[j] = LBAND_NEG; /* columns entering the band */
		if( jb > jbprev ) jbprev = jb;
		if( i < lgth1 ) cur[0] = (int)amino_dynamicmtx[u2[0]][u1[i]];
		if( ja > jb ) continue;
		if( ja == 1 ) { prev[-1] = prev[0] - ext; tbest = prev[0]; }
		else { prev[ja-2] = LBAND_NEG; tbest = LBAND_NEG; } /* column ja-2 of row i-1 is outside */
		rowmax = INT_MIN;
		needwm = ( maxwm <= ithr );
		j = ja;
		{
#define AVX2_BLEND( a, b, m ) _mm256_xor_si256( (a), _mm256_and_si256( _mm256_xor_si256( (a), (b) ), (m) ) )
			const __m256i vpen = _mm256_set1_epi32( pen ), vext = _mm256_set1_epi32( ext ), vthr = _mm256_set1_epi32( ithr );
			const __m256i vstop = _mm256_set1_epi32( lstop ), vmarkh = _mm256_set1_epi32( LMARK_H ), vmarkv = _mm256_set1_epi32( LMARK_V );
			const __m256i ninf = _mm256_set1_epi32( INT_MIN );
			const __m256i fill1 = _mm256_setr_epi32( INT_MIN, 0, 0, 0, INT_MIN, 0, 0, 0 );
			const __m256i fill2 = _mm256_setr_epi32( INT_MIN, INT_MIN, 0, 0, INT_MIN, INT_MIN, 0, 0 );
			const __m256i idx3 = _mm256_set1_epi32( 3 ), last = _mm256_set1_epi32( 7 );
			const __m256i ext8 = _mm256_set1_epi32( 8 * ext );
			__m256i vmax = ninf, cv = ninf;
			/* lane l of the block at j stands for k = j-2+l */
			__m256i kext = _mm256_mullo_epi32( _mm256_add_epi32( _mm256_set1_epi32( ja-2 ), _mm256_setr_epi32( 0, 1, 2, 3, 4, 5, 6, 7 ) ), vext );
			__m256i kext1pen = _mm256_add_epi32( kext, _mm256_set1_epi32( ext + pen ) );
			for( ; j+7<=jb; j+=8 )
			{
				__m256i p = _mm256_loadu_si256( (__m256i *)( prev + j - 1 ) );
				__m256i v = _mm256_sub_epi32( _mm256_loadu_si256( (__m256i *)( prev + j - 2 ) ), kext );
				__m256i x, g, wm, ch, cvt, clamp, ij, vmj;
				x = _mm256_max_epi32( v, _mm256_or_si256( _mm256_bslli_epi128( v, 4 ), fill1 ) );
				x = _mm256_max_epi32( x, _mm256_or_si256( _mm256_bslli_epi128( x, 8 ), fill2 ) );
				x = _mm256_max_epi32( x, _mm256_blend_epi32( ninf, _mm256_permutevar8x32_epi32( x, idx3 ), 0xf0 ) );
				x = _mm256_max_epi32( x, cv );
				cv = _mm256_permutevar8x32_epi32( x, last );
				wm = p;
				g = _mm256_add_epi32( x, kext1pen );
				ch = _mm256_cmpgt_epi32( g, wm );
				wm = _mm256_max_epi32( wm, g );
				vmj = _mm256_loadu_si256( (__m256i *)( vm + j ) );
				g = _mm256_add_epi32( vmj, vpen );
				cvt = _mm256_cmpgt_epi32( g, wm );
				wm = _mm256_max_epi32( wm, g );
				_mm256_storeu_si256( (__m256i *)( vm + j ), _mm256_add_epi32( _mm256_max_epi32( p, vmj ), vext ) );
				if( needwm ) _mm256_storeu_si256( (__m256i *)( wmrow + j ), wm );
				vmax = _mm256_max_epi32( vmax, wm );
				clamp = _mm256_cmpgt_epi32( vthr, wm );
				ij = _mm256_and_si256( ch, vmarkh );
				ij = AVX2_BLEND( ij, vmarkv, cvt );
				ij = AVX2_BLEND( ij, vstop, clamp );
				wm = _mm256_max_epi32( wm, vthr );
				_mm256_storeu_si256( (__m256i *)( ijrow + j ), ij );
				_mm256_storeu_si256( (__m256i *)( cur + j ), _mm256_add_epi32( wm, _mm256_loadu_si256( (__m256i *)( profrow + j ) ) ) );
				kext = _mm256_add_epi32( kext, ext8 );
				kext1pen = _mm256_add_epi32( kext1pen, ext8 );
			}
#undef AVX2_BLEND
			if( j > ja )
			{
				__m128i h = _mm_max_epi32( _mm256_castsi256_si128( vmax ), _mm256_extracti128_si256( vmax, 1 ) );
				h = _mm_max_epi32( h, _mm_shuffle_epi32( h, 0x4e ) );
				h = _mm_max_epi32( h, _mm_shuffle_epi32( h, 0xb1 ) );
				rowmax = _mm_cvtsi128_si32( h );
				tbest = _mm256_cvtsi256_si32( cv );
			}
		}
		for( ; j<=jb; j++ )
		{
			int p = prev[j-1], wm = p, ij = 0, g;
			if( j >= 2 )
			{
				int q = prev[j-2] - ( j-2 ) * ext;
				if( q > tbest ) tbest = q;
			}
			g = tbest + ( j-1 ) * ext + pen;
			if( g > wm ) { wm = g; ij = LMARK_H; }
			g = vm[j] + pen;
			if( g > wm ) { wm = g; ij = LMARK_V; }
			if( p > vm[j] ) vm[j] = p;
			vm[j] += ext;
			wmrow[j] = wm;
			rowmax = ( wm > rowmax ) ? wm : rowmax;
			if( wm < ithr ) { ij = lstop; wm = ithr; }
			ijrow[j] = ij;
			cur[j] = wm + profrow[j];
		}
		if( rowmax > maxwm && !needwm )
		{
			maxwm = rowmax; endali = i; endpend = 1;
		}
		else if( rowmax > maxwm )
		{
			endpend = 0;
			for( j=ja; wmrow[j] != rowmax; j++ )
				;
			maxwm = rowmax; endali = i; endalj = j;
		}
	}
	if( endpend )
	{
		int *r = LROW( endali ), *pr = ( endali < lgth1 ) ? prof[u1[endali]] : prof[u1[0]];
		j = ( endali-hi > 1 ) ? endali-hi : 1;
		for( ; r[j] - pr[j] != maxwm; j++ )
			;
		endalj = j;
	}
	free( profbuf ); free( vm ); free( wmrow );
	lr_banded = 1; lr_blo = lo; lr_bhi = hi;
	*maxwmpt = (double)maxwm;
	*endalipt = endali;
	*endaljpt = endalj;
	return( 1 );
}

/*
 * The striped band fill: Lfill_band's recurrence over lo <= i-j <= hi in diagonal coordinates.
 * Slot d = l*S + s (lane l, position s, S = ceil(W/8), W = hi-lo+1) of row i is column
 * j = i-hi+d.  The diagonal predecessor (i-1, j-1) is slot d of the previous row; the
 * horizontal term max_{k<=j-2} prev[k] + (j-1-k)*ext is max_{d'<=d-1} P[d'] + (d-d')*ext over
 * the previous row, a lane-local running maximum plus a carry from the lower lanes, as in
 * Lfill_kb (for j = 1, where Lfill_int also allows k = 0, that candidate is the diagonal plus
 * pen <= 0 and never wins); column j's vertical state moves from slot d to d-1 at the next row.
 * Column 0 (the reversed-access mtx[s2[0]][s1[r]] that Lfill_int puts in prev[0]) sits at slot
 * hi-r, and a slot with j < 1, j > lgth2 or d >= W holds LBAND_NEG.  Substitution scores are a
 * two-table lookup on seq2's letter codes (at most 15 letters; code ncode is the zero past the
 * last column), pre-striped once per call.
 *
 * store == 0: only the raw maximum (for L).  store == 1: every row is kept as Lfill_kb keeps it,
 * value*4 | kind (KB_H, KB_V, KB_STOP), and the first row-major cell of the maximum is found
 * as in Lfill_kb; lband_ijv()/lband_isstop() below then serve Ltracking.
 */
#define LB_NEGENC INT_MIN                 /* an empty stored slot: decodes below every value */
#define LB_H 1                            /* the kinds Lfill_kb stores (KB_H, KB_V, KB_STOP) */
#define LB_V 2
#define LB_STOP 3
static TLS int *lb_rows = NULL;
static TLS size_t lb_cap = 0;
static TLS int lb_S, lb_W, lb_hi, lb_lo, lb_ext, lb_v0;
#define LB_SLOT( d ) ( (size_t)( (d) % lb_S ) * 8 + (d) / lb_S )
/* Lfill_int's LROW( r )[col], or LBAND_NEG outside the band */
static inline int lb_rv( int r, int col )
{
	int d = col - r + lb_hi;
	if( d < 0 || d >= lb_W ) return( LBAND_NEG );
	return( lb_rows[(size_t)r * 8 * lb_S + LB_SLOT( d )] >> 2 );
}
/* a ? b : c by lanes of an all-ones/all-zeros mask m, without vpblendvb (two port-5 uops on Haswell/Broadwell) */
#define LB_SEL( m, b, c ) _mm256_xor_si256( (c), _mm256_and_si256( _mm256_xor_si256( (c), (b) ), (m) ) )
static TLS int *lb_buf = NULL;
static TLS size_t lb_bufcap = 0;
static inline __attribute__((always_inline)) int lband_striped( double **amino_dynamicmtx, int lgth1, int lgth2,
                        unsigned char *u1, unsigned char *u2, int ithr, int lo, int hi, int store, int onetab,
                        long long *maxwmpt, int *endalipt, int *endaljpt )
{
	int W = hi - lo + 1, S = W / 8, i, s, l, c, code[0x100], letter[16], qcode[0x100], nq = 0, ncode, inv;
	int pen = penalty, ext = penalty_ex, tmin, tmax, t, maxwm = INT_MIN, endali = 0, endalj = 0, endpend = 0;
	int *P, *V, *Vn, *CS, *wmrow, *tabs;
	__m256i vmaxall, LM, lanebase;
	const __m256i NEGV = _mm256_set1_epi32( LBAND_NEG ), one = _mm256_set1_epi32( 1 );
	const __m256i vpen = _mm256_set1_epi32( pen ), vext = _mm256_set1_epi32( ext ), vthr = _mm256_set1_epi32( ithr );
	const __m256i up1 = _mm256_setr_epi32( 0, 0, 1, 2, 3, 4, 5, 6 ), up2 = _mm256_setr_epi32( 0, 0, 0, 1, 2, 3, 4, 5 );
	const __m256i dn1 = _mm256_setr_epi32( 1, 2, 3, 4, 5, 6, 7, 7 ), seven = _mm256_set1_epi32( 7 );
	const __m256i kh = _mm256_set1_epi32( LB_H ), kv = _mm256_set1_epi32( LB_V ), kst = _mm256_set1_epi32( LB_STOP );
	size_t rs = (size_t)8 * S, need;
	/* the caller makes W a multiple of 8: no padding slots */
	for( c=0; c<0x100; c++ ) { code[c] = -1; qcode[c] = -1; }
	ncode = 0;
	for( i=0; i<lgth2; i++ ) if( code[u2[i]] < 0 )
	{
		if( ncode == ( onetab ? 6 : 14 ) ) return( 0 );
		letter[ncode] = u2[i]; code[u2[i]] = ncode++;
	}
	inv = onetab ? 7 : 15; /* code ncode: the zero past the last column; code inv: no cell (LBAND_NEG) */
	for( i=0; i<lgth1; i++ ) if( qcode[u1[i]] < 0 ) qcode[u1[i]] = nq++;
	/* CS[t] lane l = code of column t + l*S, t = 1-hi .. lgth1-hi+S-1; column 0 and anything
	   outside 1..lgth2 is not a cell (column lgth2 is the zero profile entry, code ncode) */
	tmin = 1 - hi; tmax = lgth1 - hi + S - 1;
	need = (size_t)8 * ( tmax - tmin + 1 ) + rs * 4 + (size_t)16 * nq + 16;
	if( need > lb_bufcap )
	{
		free( lb_buf ); lb_buf = malloc( sizeof( int ) * need + 64 );
		lb_bufcap = lb_buf ? need : 0;
		if( !lb_buf ) return( 0 );
	}
	P = (int *)( ( (uintptr_t)lb_buf + 31 ) & ~(uintptr_t)31 ); V = P + rs; Vn = V + rs; wmrow = Vn + rs; tabs = wmrow + rs;
	CS = tabs + (size_t)16 * nq;
	for( c=0; c<0x100; c++ ) if( qcode[c] >= 0 )
		for( l=0; l<16; l++ ) tabs[qcode[c]*16+l] = ( l < ncode ) ? (int)amino_dynamicmtx[c][letter[l]] : ( l == inv ) ? LBAND_NEG : 0;
	for( t=tmin; t<=tmax; t++ ) for( l=0; l<8; l++ )
	{
		int x = t + l * S;
		CS[(size_t)( t - tmin ) * 8 + l] = ( x >= 1 && x < lgth2 ) ? code[u2[x]] : ( x == lgth2 ) ? ncode : inv;
	}
	if( store )
	{
		size_t nr = (size_t)( lgth1 + 1 ) * rs;
		if( nr > lb_cap )
		{
			free( lb_rows ); lb_rows = malloc( sizeof( int ) * nr + 32 );
			lb_cap = lb_rows ? nr : 0;
			if( !lb_rows ) return( 0 );
		}
	}
	/* row 0: slot d is column j' = d-hi; column 0 holds the value row 1 reads as prev[0];
	   row 1's vertical state of column j = 1-hi+d is row 0's value at j-1 (Lfill_int's vm init) */
	for( s=0; s<S; s++ ) for( l=0; l<8; l++ )
	{
		int d = l * S + s, jj = d - hi, v = LBAND_NEG, vv = LBAND_NEG;
		if( jj >= 0 && jj <= lgth2 )
			v = ( jj == 0 ) ? (int)amino_dynamicmtx[u2[0]][u1[0]] : ( jj < lgth2 ) ? (int)amino_dynamicmtx[u1[0]][u2[jj]] : 0;
		jj = 1 - hi + d;
		if( jj >= 1 && jj <= lgth2 ) vv = (int)amino_dynamicmtx[u1[0]][u2[jj-1]];
		P[s*8+l] = v; V[s*8+l] = vv;
		if( store ) lb_rows[s*8+l] = ( v == LBAND_NEG ) ? LBAND_NEG : v * 4; /* only filled cells and column 0 are ever read; LBAND_NEG*4 would overflow */
	}
	{
		int lb[8] __attribute__((aligned(32)));
		for( l=0; l<8; l++ ) lb[l] = l * S;
		lanebase = _mm256_load_si256( (__m256i *)lb );
	}
	LM = NEGV;
	{
		__m256i cext = _mm256_mullo_epi32( lanebase, vext );
		for( s=0; s<S; s++ ) { LM = _mm256_max_epi32( LM, _mm256_sub_epi32( _mm256_load_si256( (__m256i *)( P + s*8 ) ), cext ) ); cext = _mm256_add_epi32( cext, vext ); }
	}
	vmaxall = NEGV;
	for( i=1; i<=lgth1; i++ )
	{
		unsigned char rc = ( i < lgth1 ) ? u1[i] : u1[0]; /* last row: as Lfill_int */
		int dmax = hi + lgth2 - i, d0 = hi - i, rowmax, needwm = ( maxwm <= ithr );
		__m256i T0, T1, carry, e = NEGV, cext, cextpen, pos = lanebase, LMn = NEGV, res0 = NEGV, vmax = NEGV;
		__m256i vdmax = _mm256_set1_epi32( dmax + 1 );
		const int *Ci = CS + (size_t)( i - hi - tmin ) * 8;
		int *row = store ? lb_rows + (size_t)i * rs : NULL, edge = ( dmax < W - 1 );
		T0 = _mm256_loadu_si256( (__m256i *)( tabs + qcode[rc]*16 ) );
		T1 = onetab ? T0 : _mm256_loadu_si256( (__m256i *)( tabs + qcode[rc]*16 + 8 ) );
		/* exclusive prefix maximum of the previous row's lane maxima: the carry from lower lanes */
		carry = _mm256_blend_epi32( _mm256_permutevar8x32_epi32( LM, up1 ), NEGV, 1 );
		carry = _mm256_max_epi32( carry, _mm256_blend_epi32( _mm256_permutevar8x32_epi32( carry, up1 ), NEGV, 1 ) );
		carry = _mm256_max_epi32( carry, _mm256_blend_epi32( _mm256_permutevar8x32_epi32( carry, up2 ), NEGV, 3 ) );
		carry = _mm256_max_epi32( carry, _mm256_permute2x128_si256( NEGV, carry, 0x20 ) );
		cext = _mm256_mullo_epi32( lanebase, vext ); cextpen = _mm256_add_epi32( cext, vpen );
		for( s=0; s<S; s++ )
		{
			__m256i a = _mm256_load_si256( (__m256i *)( P + s*8 ) ), vmj = _mm256_load_si256( (__m256i *)( V + s*8 ) );
			__m256i cd = _mm256_loadu_si256( (const __m256i *)( Ci + (size_t)s * 8 ) );
			__m256i m = _mm256_max_epi32( e, carry ), g, wm, nv, sc, v, ch, cvt, wv;
			e = _mm256_max_epi32( e, _mm256_sub_epi32( a, cext ) );
			g = _mm256_add_epi32( m, cextpen );
			ch = _mm256_cmpgt_epi32( g, a ); wm = _mm256_max_epi32( a, g );
			g = _mm256_add_epi32( vmj, vpen );
			cvt = _mm256_cmpgt_epi32( g, wm ); wm = _mm256_max_epi32( wm, g );
			nv = _mm256_add_epi32( _mm256_max_epi32( a, vmj ), vext );
			if( s ) _mm256_store_si256( (__m256i *)( Vn + (s-1)*8 ), nv ); else res0 = nv;
			/* only cells past column lgth2 can hold a real wm without being cells (left of column 1
			   every predecessor is empty) */
			wv = edge ? LB_SEL( _mm256_cmpgt_epi32( vdmax, pos ), wm, NEGV ) : wm;
			vmax = _mm256_max_epi32( vmax, wv );
			if( store && needwm ) _mm256_store_si256( (__m256i *)( wmrow + s*8 ), wv );
			sc = _mm256_permutevar8x32_epi32( T0, cd );
			if( !onetab ) sc = LB_SEL( _mm256_cmpgt_epi32( cd, seven ), _mm256_permutevar8x32_epi32( T1, cd ), sc );
			v = _mm256_add_epi32( _mm256_max_epi32( wm, vthr ), sc ); /* code inv: about LBAND_NEG */
			_mm256_store_si256( (__m256i *)( P + s*8 ), v );
			if( store )
			{
				__m256i ij = _mm256_and_si256( ch, kh );
				ij = LB_SEL( cvt, kv, ij );
				ij = LB_SEL( _mm256_cmpgt_epi32( vthr, wm ), kst, ij );
				_mm256_storeu_si256( (__m256i *)( row + s*8 ), _mm256_or_si256( _mm256_slli_epi32( v, 2 ), ij ) ); /* lb_rows: malloc alignment */
			}
			LMn = _mm256_max_epi32( LMn, _mm256_sub_epi32( v, cext ) );
			cext = _mm256_add_epi32( cext, vext ); cextpen = _mm256_add_epi32( cextpen, vext );
			pos = _mm256_add_epi32( pos, one );
		}
		/* slot l*S-1 of the next row's vertical state is lane l-1, position S-1 */
		_mm256_store_si256( (__m256i *)( Vn + (S-1)*8 ), _mm256_blend_epi32( _mm256_permutevar8x32_epi32( res0, dn1 ), NEGV, 0x80 ) );
		{ int *tp = V; V = Vn; Vn = tp; }
		/* column 0 of this row (slot d0): the value the next row reads as prev[0] */
		if( d0 >= 0 )
		{
			int seed = ( i < lgth1 ) ? (int)amino_dynamicmtx[u2[0]][u1[i]] : 0, sl = ( d0 % S ) * 8 + d0 / S;
			int q[8] __attribute__((aligned(32)));
			P[sl] = seed;
			if( store ) row[sl] = seed * 4;
			for( l=0; l<8; l++ ) q[l] = LBAND_NEG;
			q[d0 / S] = seed - d0 * ext;
			LMn = _mm256_max_epi32( LMn, _mm256_load_si256( (__m256i *)q ) );
		}
		LM = LMn;
		vmaxall = _mm256_max_epi32( vmaxall, vmax );
		if( !store ) continue;
		{
			__m128i h = _mm_max_epi32( _mm256_castsi256_si128( vmax ), _mm256_extracti128_si256( vmax, 1 ) );
			h = _mm_max_epi32( h, _mm_shuffle_epi32( h, 0x4e ) );
			h = _mm_max_epi32( h, _mm_shuffle_epi32( h, 0xb1 ) );
			rowmax = _mm_cvtsi128_si32( h );
		}
		if( rowmax > maxwm )
		{
			maxwm = rowmax; endali = i;
			if( !needwm ) { endpend = 1; continue; }
			endpend = 0;
			{
				/* the first column holding rowmax: the lowest lane with one, then its lowest position */
				unsigned any = 0; int ln;
				__m256i vr = _mm256_set1_epi32( rowmax );
				for( s=0; s<S; s++ ) any |= (unsigned)_mm256_movemask_ps( _mm256_castsi256_ps( _mm256_cmpeq_epi32( _mm256_load_si256( (__m256i *)( wmrow + s*8 ) ), vr ) ) );
				ln = __builtin_ctz( any );
				for( s=0; wmrow[s*8+ln] != rowmax; s++ ) ;
				endalj = i - hi + ln * S + s;
			}
		}
	}
	if( store && endpend )
	{
		/* Once maxwm > ithr a clamped cell cannot hold it; wm = stored value - score elsewhere. */
		unsigned char rc = ( endali < lgth1 ) ? u1[endali] : u1[0];
		const int *tb = tabs + qcode[rc]*16, *Ci = CS + (size_t)( endali - hi - tmin ) * 8;
		int *row = lb_rows + (size_t)endali * rs, dmin = hi + 1 - endali, dmx = ( hi + lgth2 - endali < W - 1 ) ? hi + lgth2 - endali : W - 1, d;
		for( d=( dmin > 0 ? dmin : 0 ); d<=dmx; d++ )
		{
			int sl = ( d % S ) * 8 + d / S;
			if( ( row[sl] >> 2 ) - tb[Ci[sl]] == maxwm ) break;
		}
		endalj = endali - hi + d;
	}
	if( !store )
	{
		__m128i h = _mm_max_epi32( _mm256_castsi256_si128( vmaxall ), _mm256_extracti128_si256( vmaxall, 1 ) );
		h = _mm_max_epi32( h, _mm_shuffle_epi32( h, 0x4e ) );
		h = _mm_max_epi32( h, _mm_shuffle_epi32( h, 0xb1 ) );
		maxwm = _mm_cvtsi128_si32( h );
	}
	else
	{
		lb_S = S; lb_W = W; lb_hi = hi; lb_lo = lo; lb_ext = ext;
		lb_v0 = (int)amino_dynamicmtx[u1[0]][u2[0]]; /* Lfill_int's lr_v0: row 0's column 0 before row 1 */
		*endalipt = endali; *endaljpt = endalj;
	}
	*maxwmpt = maxwm;
	return( 1 );
}
#undef LB_SEL
/* the letters of seq2 decide the table width; at most 14 */
static int lband_onetab( unsigned char *u2, int lgth2 )
{
	unsigned char seen[0x100] = { 0 };
	int i, n = 0;
	for( i=0; i<lgth2; i++ ) if( !seen[u2[i]] ) { seen[u2[i]] = 1; n++; }
	return( n <= 6 );
}
static int Lscore_band( double **amino_dynamicmtx, int lgth1, int lgth2, unsigned char *u1, unsigned char *u2,
                        int ithr, int lo, int hi, long long *maxwmpt )
{
	if( lband_onetab( u2, lgth2 ) ) return( lband_striped( amino_dynamicmtx, lgth1, lgth2, u1, u2, ithr, lo, hi, 0, 1, maxwmpt, NULL, NULL ) );
	return( lband_striped( amino_dynamicmtx, lgth1, lgth2, u1, u2, ithr, lo, hi, 0, 0, maxwmpt, NULL, NULL ) );
}
static int Lfill_bandkb( double **amino_dynamicmtx, int lgth1, int lgth2, unsigned char *u1, unsigned char *u2,
                         int ithr, int lo, int hi, long long *maxwmpt, int *endalipt, int *endaljpt )
{
	if( lband_onetab( u2, lgth2 ) ) return( lband_striped( amino_dynamicmtx, lgth1, lgth2, u1, u2, ithr, lo, hi, 1, 1, maxwmpt, endalipt, endaljpt ) );
	return( lband_striped( amino_dynamicmtx, lgth1, lgth2, u1, u2, ithr, lo, hi, 1, 0, maxwmpt, endalipt, endaljpt ) );
}
/* lres_hk()/lres_vmp() and kb_ijv()/kb_isstop() over the striped band rows */
static int lb_hk( int i, int j )
{
	int k0 = ( i-1-lb_hi > 0 ) ? i-1-lb_hi : 0, jj, tbest = lb_rv( i-1, k0 ), tbestk = k0;
	for( jj=k0+2; jj<=j; jj++ )
	{
		int q = lb_rv( i-1, jj-2 ) - ( jj-2 ) * lb_ext;
		if( q > tbest ) { tbest = q; tbestk = jj-2; }
	}
	return( tbestk );
}
static int lb_vmp( int i, int j )
{
	int q0 = ( j-1+lb_lo > 0 ) ? j-1+lb_lo : 0, r;
	int vm = q0 ? LBAND_NEG : ( j == 1 ) ? lb_v0 : lb_rv( 0, j-1 ), vmp = q0;
	for( r=q0+1; r<i; r++ )
	{
		int p = lb_rv( r-1, j-1 );
		if( p > vm ) { vm = p; vmp = r-1; }
		vm += lb_ext;
	}
	return( vmp );
}
static int lb_kind( int i, int j )
{
	int d = j - i + lb_hi;
	if( d < 0 || d >= lb_W ) return( LB_STOP ); /* not reached on the path: the certificate */
	return( lb_rows[(size_t)i * 8 * lb_S + LB_SLOT( d )] & 3 );
}
static int lband_ijv( int i, int j, int lstop )
{
	if( i <= 0 || j <= 0 ) return( lstop );
	switch( lb_kind( i, j ) )
	{
		case LB_H: return( -( j - lb_hk( i, j ) ) );
		case LB_V: return( i - lb_vmp( i, j ) );
		case LB_STOP: return( lstop );
		default: return( 0 );
	}
}
static int lband_isstop( int i, int j ) { return( i <= 0 || j <= 0 || lb_kind( i, j ) == LB_STOP ); }

#ifndef LBAND_W
#define LBAND_W 32       /* half-width of the first band */
#endif
#ifndef LBAND_REFILL
#define LBAND_REFILL 0.6 /* refill with the certificate's band only below this fraction of the cells */
#endif
/* Cost of a cell relative to the full striped fill (Dikarya VM, round 4): the score-only first
   band and the striped band with traceback.  On inputs where most pairs end with the full fill
   (divergent or very unequal lengths) the first band is wasted, so each thread keeps a running
   estimate of the cells saved and, once it is negative after LBAND_LEARN attempts, tries only
   every LBAND_PROBE-th pair.  Either way the result is the full fill's; only time changes. */
#define LBAND_COST1 0.8
#define LBAND_COST2 1.3
#define LBAND_LEARN 64
#define LBAND_PROBE 32
static TLS double lband_saved = 0.0;
static TLS long lband_tries = 0, lband_skipped = 0;
/* Cells of an lgth1 x lgth2 fill with lo <= i-j <= hi (i in 1..lgth1, j in 1..lgth2). */
static double lband_cells( int lgth1, int lgth2, long long lo, long long hi )
{
	double n = 0; int i;
	for( i=1; i<=lgth1; i++ )
	{
		long long a = i - hi, b = i - lo;
		if( a < 1 ) a = 1;
		if( b > lgth2 ) b = lgth2;
		if( b >= a ) n += (double)( b - a + 1 );
	}
	return( n );
}
/*
 * The band certificate (paper/round2-band-certificate.md): with M >= every substitution score
 * that can be read (0 included), T <= M, nonpositive gap costs and a feasible raw score L >
 * max(T,0), every history using a cell outside min(0,D)-r <= i-j <= max(0,D)+r, D = lgth1-lgth2,
 * r = min(lgth1,lgth2) - ceil(L/M), scores below L.  Within such a band the restricted fill gives
 * the full fill's maximum, end point, path and markers.  L is the first band's own maximum (a
 * real history of the full recurrence).  Returns 1 with the exact fill done, or 0 having left
 * nothing the caller uses (it then runs the full fill).
 */
static int Lband_try( double **amino_dynamicmtx, double **n_dynamicmtx, double scoreoffset,
                      char *s1, char *s2, int lgth1, int lgth2, int **ijp, int lstop,
                      double *maxwmpt, int *endalipt, int *endaljpt )
{
	int c, k, n = lgth1 < lgth2 ? lgth1 : lgth2, d = lgth1 - lgth2, mx = 0, ithr;
	long long lo, hi, w = LBAND_W, kk, r, L;
	unsigned char *u1 = (unsigned char *)s1, *u2 = (unsigned char *)s2, used[0x100];
	double full = (double)lgth1 * lgth2;
	lr_banded = 0;
	if( penalty > 0 || penalty_ex > 0 || n < 4 * LBAND_W ) return( 0 );
	/* the check mode tries every eligible pair, so that each one is compared */
	if( lband_tries >= LBAND_LEARN && lband_saved < 0.0 && !lband_check_on() && ++lband_skipped % LBAND_PROBE ) return( 0 );
	if( !lfill_prepare( amino_dynamicmtx, n_dynamicmtx, scoreoffset, u1, u2, lgth1, lgth2, &ithr, used ) ) return( 0 );
	for( c=0; c<0x100; c++ ) if( used[c] ) for( k=0; k<0x100; k++ ) if( used[k] )
		if( (int)amino_dynamicmtx[c][k] > mx ) mx = (int)amino_dynamicmtx[c][k];
	if( mx <= 0 || ithr > mx ) return( 0 );
	/* the first band only supplies L: its raw maximum is the score of a real history */
	/* the striped kernel takes a band of a multiple of 8 diagonals (a wider band is still exact) */
	lo = ( d < 0 ? d : 0 ) - w; hi = ( d > 0 ? d : 0 ) + w; hi += ( 8 - ( hi - lo + 1 ) % 8 ) % 8;
	if( lband_cells( lgth1, lgth2, lo, hi ) > LBAND_REFILL * full ) return( 0 );
	if( !Lscore_band( amino_dynamicmtx, lgth1, lgth2, u1, u2, ithr, (int)lo, (int)hi, &L ) ) return( 0 );
	lband_tries++;
	lband_saved -= LBAND_COST1 * lband_cells( lgth1, lgth2, lo, hi ); /* until the band is used */
	if( lband_check_on() )
	{
		double m; int ei, ej;
		if( !Lfill_band( amino_dynamicmtx, n_dynamicmtx, scoreoffset, s1, s2, lgth1, lgth2, ijp, lstop, (int)lo, (int)hi, &m, &ei, &ej ) || (long long)m != L )
		{
			fprintf( stderr, "\nLBAND_CHECK: striped band score %lld, row-major %.17g\n", L, m );
			exit( 1 );
		}
		lr_banded = 0;
	}
	if( L <= ( ithr > 0 ? ithr : 0 ) ) return( 0 );
	kk = ( L + mx - 1 ) / mx;
	if( kk < 1 || kk > n ) return( 0 );
	r = n - kk;
	lo = ( d < 0 ? d : 0 ) - r; hi = ( d > 0 ? d : 0 ) + r; hi += ( 8 - ( hi - lo + 1 ) % 8 ) % 8;
	if( lband_cells( lgth1, lgth2, lo, hi ) > LBAND_REFILL * full ) return( 0 );
	lband_saved += full - LBAND_COST2 * lband_cells( lgth1, lgth2, lo, hi );
	{
		long long m;
		if( !Lfill_bandkb( amino_dynamicmtx, lgth1, lgth2, u1, u2, ithr, (int)lo, (int)hi, &m, endalipt, endaljpt ) ) return( 0 );
		*maxwmpt = (double)m;
	}
	return( 1 );
}
#endif
#if defined(MAFFT_LSTRIPED)
#if defined(MAFFT_AVX512)
/*
 * Lfill_kb: the marker fill above in a striped layout (Farrar's), with ijp kept as one byte per
 * cell (0 diagonal, KB_H for LMARK_H, KB_V for LMARK_V, KB_STOP for lstop) instead of an int.
 *
 * Column c = j-1 of a row (c = 0 .. lgth2-1, padded to 16*S) lives in lane c / S, position c % S,
 * S = ceil( lgth2 / 16 ).  Cell j reads prev[j-1] (its own position in the previous row) and the
 * horizontal state H_j = (j-1)*ext + max_{k <= max(j-2,0)} T_k, T_k = prev[k] - k*ext.  In this
 * layout that maximum is the larger of a running maximum along the cell's own lane (positions
 * before it) and a carry: the maxima of the whole lanes below it (for lane 0, T_0 itself, which is
 * the k = 0 term cell 1 uses).  The lane maxima come from the previous row's pass, so a row is one
 * pass of plain vertical max/add operations with no prefix scan inside the loop.  Padding columns
 * (c >= lgth2) sit above every real column, and the scan only carries to higher columns, so they
 * never reach a real cell; they are kept out of the row maximum by a mask.
 *
 * The rest is Lfill_int's arithmetic per cell, unchanged.  Every row is kept (striped, row i's
 * value for column c, i.e. prev[c] as row i+1 reads it, at KB_RV( i, c )) for the marker replay,
 * which kb_hk() and kb_vmp() do exactly as lres_hk() and lres_vmp() do over Lfill_int's rows.
 * The first cell holding a row's maximum is the first position holding it in the lowest lane
 * that has it (lanes are in column order).  Ltracking() reads the traceback through kb_ijv().
 */
/* 64-byte aligned allocation, released with aligned_free_64() */
static void *aligned_alloc_64( size_t n )
{
	char *raw = malloc( n + 64 + sizeof( void * ) ), *p;
	if( raw == NULL ) ErrorExit( "Cannot allocate memory." );
	p = (char *)( ( (uintptr_t)( raw + sizeof( void * ) ) + 63 ) & ~(uintptr_t)63 );
	( (void **)p )[-1] = raw;
	return( p );
}
static void aligned_free_64( void *p ) { if( p ) free( ( (void **)p )[-1] ); }
#define KB_H 1
#define KB_V 2
#define KB_STOP 3
static TLS unsigned char *kb_raw = NULL;
static TLS int *kb_rows = NULL;
static TLS size_t kb_cap = 0, kb_rcap = 0;
static TLS int kb_S, kb_ext, kb_v0;
#define KB_RSTRIDE ( (size_t)( kb_S + 1 ) * 16 )
/* row r's value at column c (striped vector c % S, lane c / S; vector 0 holds the shifted values) */
#define KB_RV( r, c ) ( kb_rows[(size_t)( r ) * KB_RSTRIDE + (size_t)( ( c ) % kb_S ) * 16 + ( c ) / kb_S] )
#define KB_KIND( i, c ) ( kb_raw[(size_t)( i ) * kb_S * 16 + (size_t)( ( c ) % kb_S ) * 16 + ( c ) / kb_S] )

static int Lfill_kb( double **amino_dynamicmtx, double **n_dynamicmtx, double scoreoffset,
                     char *s1, char *s2, int lgth1, int lgth2,
                     double *maxwmpt, int *endalipt, int *endaljpt )
{
	int i, c, k, l, s, S;
	int pen = penalty, ext = penalty_ex;
	int ithr, *prof[0x100], *profbuf, nprof = 0, *vmraw, *vm, *wmrow;
	int maxwm, endali = 0, endalj = 0, rowmax;
	unsigned char *u1 = (unsigned char *)s1, *u2 = (unsigned char *)s2;
	unsigned char used[0x100];
	size_t need, needkb, rs;
	__m512i lanebase, ltn;

	if( !lfill_prepare( amino_dynamicmtx, n_dynamicmtx, scoreoffset, u1, u2, lgth1, lgth2, &ithr, used ) ) return( 0 );

	S = kb_S = ( lgth2 + 15 ) / 16;
	rs = KB_RSTRIDE;
	need = (size_t)( lgth1 + 1 ) * rs;
	needkb = (size_t)( lgth1 + 1 ) * S * 16;
	if( need > ( (size_t)1 << 26 ) ) return( 0 );
	if( need > kb_rcap )
	{
		aligned_free_64( kb_rows );
		kb_rows = aligned_alloc_64( sizeof( int ) * need );
		kb_rcap = need;
	}
	if( needkb > kb_cap )
	{
		free( kb_raw );
		kb_raw = malloc( needkb );
		if( kb_raw == NULL ) ErrorExit( "Cannot allocate the local alignment traceback." );
		kb_cap = needkb;
	}
#ifdef MAFFT_POISON_TEST
	memset( kb_rows, 0xa5, sizeof( int ) * need ); memset( kb_raw, 0xa5, needkb );
#endif

	/* striped profiles: cell j = c+1 adds prof[.][j] (prof[.][lgth2] = 0; padding 0) */
	for( c=0; c<0x100; c++ ) prof[c] = NULL;
	for( c=0; c<0x100; c++ ) if( used[c] ) nprof++;
	profbuf = aligned_alloc_64( sizeof( int ) * nprof * S * 16 );
	k = 0;
	for( c=0; c<0x100; c++ ) if( used[c] )
	{
		double *row = amino_dynamicmtx[c];
		prof[c] = profbuf + (size_t)k * S * 16;
		for( s=0; s<S; s++ ) for( l=0; l<16; l++ )
		{
			int jj = l * S + s + 1;
			prof[c][s*16+l] = ( jj < lgth2 ) ? (int)row[u2[jj]] : 0;
		}
		k++;
	}
	vmraw = aligned_alloc_64( sizeof( int ) * S * 16 * 2 );
	vm = vmraw; wmrow = vmraw + S * 16;

	/* row 0: R_0[c] = mtx[s1[0]][s2[c]] for c < lgth2 (0 in the padding), in the shifted layout
	   (vector s holds columns l*S+s); vm[j] = R_0[j-1] */
	{
		int *r0 = kb_rows;
		for( s=0; s<S; s++ ) for( l=0; l<16; l++ )
		{
			int cc = l * S + s;
			r0[s*16+l] = ( cc < lgth2 ) ? (int)amino_dynamicmtx[u1[0]][u2[cc]] : 0;
		}
		memcpy( vm, r0, sizeof( int ) * S * 16 );
		kb_v0 = r0[0]; /* m[1] before row 1 overwrites R_0[0] */
	}
	kb_ext = ext;

	lanebase = _mm512_mullo_epi32( _mm512_setr_epi32( 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 ), _mm512_set1_epi32( S ) );
	/* lane maxima of row 0's T (padding included: it only reaches padding) */
	{
		__m512i cext = _mm512_mullo_epi32( lanebase, _mm512_set1_epi32( ext ) );
		ltn = _mm512_set1_epi32( INT_MIN );
		for( s=0; s<S; s++ )
		{
			if( s > 0 ) ltn = _mm512_max_epi32( ltn, _mm512_sub_epi32( _mm512_load_si512( (void *)( kb_rows + s*16 ) ), cext ) );
			cext = _mm512_add_epi32( cext, _mm512_set1_epi32( ext ) );
		}
	}

	maxwm = INT_MIN;
	for( i=1; i<=lgth1; i++ )
	{
		int *prev = kb_rows + (size_t)( i-1 ) * rs, *cur = kb_rows + (size_t)i * rs;
		int *profrow = ( i < lgth1 ) ? prof[u1[i]] : prof[u1[0]]; /* last row: cur[] is never read again */
		unsigned char *kbrow = kb_raw + (size_t)i * S * 16;
		int special = (int)amino_dynamicmtx[u2[0]][u1[i-1]]; /* prev[0] = initverticalw[i-1] */
		const __m512i vpen = _mm512_set1_epi32( pen ), vext = _mm512_set1_epi32( ext ), vthr = _mm512_set1_epi32( ithr );
		const __m512i vkh = _mm512_set1_epi32( KB_H ), vkv = _mm512_set1_epi32( KB_V ), vks = _mm512_set1_epi32( KB_STOP );
		const __m512i ninf = _mm512_set1_epi32( INT_MIN ), one = _mm512_set1_epi32( 1 ), vn = _mm512_set1_epi32( lgth2 );
		__m512i r0, t0, lt, carry, e, cext, cextpen, vmax, pos;
		/* vector 0 of the previous row: its columns l*S, i.e. the previous lane's last cell, and
		   prev[0] in lane 0 */
		if( i == 1 ) r0 = _mm512_mask_mov_epi32( _mm512_load_si512( (void *)prev ), 1, _mm512_set1_epi32( special ) );
		else r0 = _mm512_alignr_epi32( _mm512_load_si512( (void *)( prev + (size_t)S * 16 ) ), _mm512_set1_epi32( special ), 15 );
		_mm512_store_si512( (void *)prev, r0 );
		/* lane maxima of T over the previous row, then the carry: the maximum over the lanes below,
		   and T_0 for lane 0 */
		cext = _mm512_mullo_epi32( lanebase, vext );
		t0 = _mm512_sub_epi32( r0, cext );
		lt = _mm512_max_epi32( ltn, t0 );
		carry = _mm512_alignr_epi32( lt, ninf, 15 );
		carry = _mm512_max_epi32( carry, _mm512_alignr_epi32( carry, ninf, 15 ) );
		carry = _mm512_max_epi32( carry, _mm512_alignr_epi32( carry, ninf, 14 ) );
		carry = _mm512_max_epi32( carry, _mm512_alignr_epi32( carry, ninf, 12 ) );
		carry = _mm512_max_epi32( carry, _mm512_alignr_epi32( carry, ninf, 8 ) );
		carry = _mm512_mask_mov_epi32( carry, 1, _mm512_set1_epi32( special ) ); /* T_0 = prev[0] */

		e = ninf;
		cextpen = _mm512_add_epi32( cext, vpen );
		vmax = ninf;
		pos = lanebase;
		ltn = ninf;
		for( s=0; s<S; s++ )
		{
			__m512i a = _mm512_load_si512( (void *)( prev + s*16 ) ), vmj = _mm512_load_si512( (void *)( vm + s*16 ) );
			__m512i t = _mm512_sub_epi32( a, cext ), m, g, wm, ij, v;
			__mmask16 ch, cvt, clamp;
			m = _mm512_max_epi32( e, carry );
			e = _mm512_max_epi32( e, t );
			g = _mm512_add_epi32( m, cextpen );    /* H + pen */
			ch = _mm512_cmpgt_epi32_mask( g, a );
			wm = _mm512_max_epi32( a, g );
			g = _mm512_add_epi32( vmj, vpen );
			cvt = _mm512_cmpgt_epi32_mask( g, wm );
			wm = _mm512_max_epi32( wm, g );
			_mm512_store_si512( (void *)( vm + s*16 ), _mm512_add_epi32( _mm512_max_epi32( a, vmj ), vext ) );
			_mm512_store_si512( (void *)( wmrow + s*16 ), wm );
			vmax = _mm512_mask_max_epi32( vmax, _mm512_cmplt_epi32_mask( pos, vn ), vmax, wm ); /* real columns only */
			clamp = _mm512_cmpgt_epi32_mask( vthr, wm );
			ij = _mm512_maskz_mov_epi32( ch, vkh );
			ij = _mm512_mask_mov_epi32( ij, cvt, vkv );
			ij = _mm512_mask_mov_epi32( ij, clamp, vks );
			_mm_store_si128( (__m128i *)( kbrow + s*16 ), _mm512_cvtepi32_epi8( ij ) );
			wm = _mm512_max_epi32( wm, vthr );
			v = _mm512_add_epi32( wm, _mm512_load_si512( (void *)( profrow + s*16 ) ) );
			_mm512_store_si512( (void *)( cur + (size_t)( s+1 ) * 16 ), v );
			cext = _mm512_add_epi32( cext, vext );
			cextpen = _mm512_add_epi32( cextpen, vext );
			/* next row's T at column c+1, same lane except the last position (lane above, added
			   with vector 0 at the next row's start) */
			if( s < S-1 ) ltn = _mm512_max_epi32( ltn, _mm512_sub_epi32( v, cext ) );
			pos = _mm512_add_epi32( pos, one );
		}
		rowmax = _mm512_reduce_max_epi32( vmax );
		if( rowmax > maxwm )
		{
			/* the first column holding it: the lowest lane that has it (lanes are in column order),
			   then its first position there */
			__m512i vr = _mm512_set1_epi32( rowmax );
			unsigned any = 0;
			int ln, c0;
			pos = lanebase;
			for( s=0; s<S; s++ )
			{
				any |= _mm512_mask_cmpeq_epi32_mask( _mm512_cmplt_epi32_mask( pos, vn ), _mm512_load_si512( (void *)( wmrow + s*16 ) ), vr );
				pos = _mm512_add_epi32( pos, one );
			}
			ln = __builtin_ctz( any );
			for( s=0; wmrow[s*16+ln] != rowmax; s++ )
				;
			c0 = ln * S + s;
			maxwm = rowmax; endali = i; endalj = c0 + 1;
		}
	}

	aligned_free_64( profbuf ); aligned_free_64( vmraw );
	*maxwmpt = (double)maxwm;
	*endalipt = endali;
	*endaljpt = endalj;
	return( 1 );
}
#else
#include "l11_avx2.h"
#endif

#if defined(MAFFT_AVX512) || !MAFFT_KB_SCORE_ONLY
static int Lscore_kb( double **amino_dynamicmtx, double **n_dynamicmtx, double scoreoffset,
                      char *s1, char *s2, int lgth1, int lgth2, double *maxwmpt )
{
	int ei, ej;
	return Lfill_kb(amino_dynamicmtx,n_dynamicmtx,scoreoffset,s1,s2,lgth1,lgth2,maxwmpt,&ei,&ej);
}
#endif

/* lres_hk() and lres_vmp() over the striped rows */
static int kb_hk( int i, int j )
{
	int jj, tbest = KB_RV( i-1, 0 ), tbestk = 0;
	for( jj=2; jj<=j; jj++ )
	{
		int q = KB_RV( i-1, jj-2 ) - ( jj-2 ) * kb_ext;
		if( q > tbest ) { tbest = q; tbestk = jj-2; }
	}
	return( tbestk );
}
static int kb_vmp( int i, int j )
{
	int r, vm = ( j == 1 ) ? kb_v0 : KB_RV( 0, j-1 ), vmp = 0;
	for( r=1; r<i; r++ )
	{
		int p = KB_RV( r-1, j-1 );
		if( p > vm ) { vm = p; vmp = r-1; }
		vm += kb_ext;
	}
	return( vmp );
}

/* ijp[i][j] as Ltracking resolves it after Lfill_int (row 0 and column 0 hold localstop) */
static int kb_ijv( int i, int j, int lstop )
{
	if( i <= 0 || j <= 0 ) return( lstop );
	switch( KB_KIND( i, j-1 ) )
	{
		case KB_H: return( -( j - kb_hk( i, j ) ) );
		case KB_V: return( i - kb_vmp( i, j ) );
		case KB_STOP: return( lstop );
		default: return( 0 );
	}
}
static int kb_isstop( int i, int j ) { return( i <= 0 || j <= 0 || KB_KIND( i, j-1 ) == KB_STOP ); }
/* Both public entry points share this per-thread workspace. Their NULL-input
   cleanup is called before worker exit (direction checking can create many workers). */
static void kb_release( void )
{
#if defined(MAFFT_AVX512)
	aligned_free_64(kb_rows); free(kb_raw);
#else
	kb_free(kb_rows); kb_free(kb_raw);
#if MAFFT_KB_SCORE_ONLY
	kb_free(kb_score_mem); kb_score_mem=NULL; kb_score_cap=0;
#endif
#endif
	kb_rows=NULL; kb_raw=NULL; kb_rcap=kb_cap=0;
}
#endif
#else
static int Lfill_int( double **amino_dynamicmtx, double **n_dynamicmtx, double scoreoffset,
                      char *s1, char *s2, int lgth1, int lgth2, int **ijp, int lstop,
                      double *maxwmpt, int *endalipt, int *endaljpt )
{
	int i, j, c, k;
	int pen = penalty, ext = penalty_ex;
	int ithr, *prof[0x100], *profbuf, nprof = 0;
	int *prev, *cur, *vm, *vmp, *hq, *hk, *wmrow, *pbuf1, *pbuf2;
	int tbest, tbestk, jj;
	int maxwm, endali = 0, endalj = 0, rowmax;
	unsigned char *u1 = (unsigned char *)s1, *u2 = (unsigned char *)s2;
	unsigned char used[0x100];

	if( !lfill_prepare( amino_dynamicmtx, n_dynamicmtx, scoreoffset, u1, u2, lgth1, lgth2, &ithr, used ) ) return( 0 );

	for( c=0; c<0x100; c++ ) prof[c] = NULL;
	for( c=0; c<0x100; c++ ) if( used[c] ) nprof++;
	profbuf = malloc( sizeof( int ) * nprof * ( lgth2 + 4 ) );
	if( profbuf == NULL ) return( 0 ); /* out of memory: the double-precision fill */
	k = 0;
	for( c=0; c<0x100; c++ ) if( used[c] )
	{
		double *row = amino_dynamicmtx[c];
		prof[c] = profbuf + k * ( lgth2 + 4 );
		for( j=0; j<lgth2; j++ ) prof[c][j] = (int)row[u2[j]];
		prof[c][lgth2] = 0;
		k++;
	}

	/* one slot in front of prev[]/cur[]: prev[-1] seeds the vector scan */
	pbuf1 = calloc( lgth2 + 5, sizeof( int ) );
	pbuf2 = calloc( lgth2 + 5, sizeof( int ) );
	vm    = malloc( sizeof( int ) * ( lgth2 + 4 ) );
	vmp   = malloc( sizeof( int ) * ( lgth2 + 4 ) );
	hq    = malloc( sizeof( int ) * ( lgth2 + 4 ) );
	hk    = malloc( sizeof( int ) * ( lgth2 + 4 ) );
	wmrow = malloc( sizeof( int ) * ( lgth2 + 4 ) );
	if( !pbuf1 || !pbuf2 || !vm || !vmp || !hq || !hk || !wmrow )
	{
		free( profbuf ); free( pbuf1 ); free( pbuf2 ); free( vm ); free( vmp ); free( hq ); free( hk ); free( wmrow );
		return( 0 );
	}
	prev = pbuf1 + 1; cur = pbuf2 + 1;

	/* row 0: currentw[j] = mtx[s1[0]][s2[j]];  m[j] = currentw[j-1], mp[j] = 0 */
	for( j=0; j<lgth2; j++ ) cur[j] = prof[u1[0]][j];
	cur[lgth2] = 0;
	for( j=1; j<=lgth2; j++ ) { vm[j] = cur[j-1]; vmp[j] = 0; }

	maxwm = INT_MIN;
	for( i=1; i<=lgth1; i++ )
	{
		int *t = prev; prev = cur; cur = t;
		int *profrow = ( i < lgth1 ) ? prof[u1[i]] : NULL;
		int *ijrow = ijp[i];
		int negi1 = i - 1;
		/* previousw[0] = initverticalw[i-1] = mtx[s2[0]][s1[i-1]] */
		prev[0] = (int)amino_dynamicmtx[u2[0]][u1[i-1]];

		/* Horizontal state before cell j:  H_j = (j-1)*ext + max_{k<=max(j-2,0)} (prev[k] - k*ext),
		   mpi_j = first k attaining it (the original updates only on strict '>').  Computed
		   inside the loop below: the first index attaining a running max is the last strict
		   record, so both are prefix maxima, taken 4 lanes at a time with a carry.  prev[-1] is
		   set so that lane k=-1 of the first block stands for the k=0 term of H_1. */
		prev[-1] = prev[0] - ext;
		tbest = prev[0]; tbestk = 0;

		rowmax = INT_MIN;
		if( !profrow ) profrow = prof[u1[0]]; /* last row: cur[] is never read again */
		j = 1;
#if defined(MAFFT_SSE41)
		/* The NEON block above, lane for lane: palignr for vextq, pblendvb for vbslq. */
		{
			__m128i vpen = _mm_set1_epi32( pen ), vext = _mm_set1_epi32( ext ), vthr = _mm_set1_epi32( ithr );
			__m128i vstop = _mm_set1_epi32( lstop ), vi = _mm_set1_epi32( i ), vi1 = _mm_set1_epi32( negi1 );
			__m128i vmax = _mm_set1_epi32( INT_MIN ), vfour = _mm_set1_epi32( 4 );
			__m128i vj = _mm_setr_epi32( 1, 2, 3, 4 );
			__m128i ninf = _mm_set1_epi32( INT_MIN ), none = _mm_set1_epi32( -1 );
			__m128i cv = ninf, ck = _mm_setzero_si128();
			__m128i kv = _mm_setr_epi32( -1, 0, 1, 2 );
			__m128i kext = _mm_mullo_epi32( kv, vext ), kext1 = _mm_add_epi32( kext, vext ), ext4 = _mm_mullo_epi32( vfour, vext );
			for( ; j+3<=lgth2; j+=4 )
			{
				__m128i p = _mm_loadu_si128( (__m128i *)( prev + j - 1 ) );
				__m128i hqv, hkv;
				{
					__m128i v = _mm_sub_epi32( _mm_loadu_si128( (__m128i *)( prev + j - 2 ) ), kext );
					__m128i x = _mm_max_epi32( v, _mm_alignr_epi8( v, ninf, 12 ) );
					__m128i e, r, t;
					x = _mm_max_epi32( x, _mm_alignr_epi8( x, ninf, 8 ) );
					x = _mm_max_epi32( x, cv );
					e = _mm_alignr_epi8( x, cv, 12 );
					r = _mm_blendv_epi8( none, kv, _mm_cmpgt_epi32( v, e ) );
					t = _mm_max_epi32( r, _mm_alignr_epi8( r, none, 12 ) );
					t = _mm_max_epi32( t, _mm_alignr_epi8( t, none, 8 ) );
					t = _mm_max_epi32( t, ck );
					hqv = _mm_add_epi32( x, kext1 );
					hkv = t;
					cv = _mm_shuffle_epi32( x, 0xff );
					ck = _mm_shuffle_epi32( t, 0xff );
					kv = _mm_add_epi32( kv, vfour );
					kext = _mm_add_epi32( kext, ext4 );
					kext1 = _mm_add_epi32( kext1, ext4 );
				}
				__m128i g, wm, ij, m, vmj, vmpj, c;
				wm = p;
				ij = _mm_setzero_si128();
				g = _mm_add_epi32( hqv, vpen );
				c = _mm_cmpgt_epi32( g, wm );
				wm = _mm_max_epi32( wm, g );
				ij = _mm_blendv_epi8( ij, _mm_sub_epi32( hkv, vj ), c );
				vmj = _mm_loadu_si128( (__m128i *)( vm + j ) );
				vmpj = _mm_loadu_si128( (__m128i *)( vmp + j ) );
				g = _mm_add_epi32( vmj, vpen );
				c = _mm_cmpgt_epi32( g, wm );
				wm = _mm_max_epi32( wm, g );
				ij = _mm_blendv_epi8( ij, _mm_sub_epi32( vi, vmpj ), c );
				c = _mm_cmpgt_epi32( p, vmj );
				m = _mm_max_epi32( p, vmj );
				_mm_storeu_si128( (__m128i *)( vm + j ), _mm_add_epi32( m, vext ) );
				_mm_storeu_si128( (__m128i *)( vmp + j ), _mm_blendv_epi8( vmpj, vi1, c ) );
				_mm_storeu_si128( (__m128i *)( wmrow + j ), wm );
				vmax = _mm_max_epi32( vmax, wm );
				c = _mm_cmpgt_epi32( vthr, wm );
				ij = _mm_blendv_epi8( ij, vstop, c );
				wm = _mm_max_epi32( wm, vthr );
				_mm_storeu_si128( (__m128i *)( ijrow + j ), ij );
				_mm_storeu_si128( (__m128i *)( cur + j ), _mm_add_epi32( wm, _mm_loadu_si128( (__m128i *)( profrow + j ) ) ) );
				vj = _mm_add_epi32( vj, vfour );
			}
			vmax = _mm_max_epi32( vmax, _mm_shuffle_epi32( vmax, 0x4e ) );
			vmax = _mm_max_epi32( vmax, _mm_shuffle_epi32( vmax, 0xb1 ) );
			rowmax = _mm_cvtsi128_si32( vmax );
			if( j > 1 ) { tbest = _mm_cvtsi128_si32( cv ); tbestk = _mm_cvtsi128_si32( ck ); }
		}
#endif
		/* scan for the remaining cells */
		for( jj=j; jj<=lgth2; jj++ )
		{
			if( jj >= 2 )
			{
				int q = prev[jj-2] - ( jj-2 ) * ext;
				if( q > tbest ) { tbest = q; tbestk = jj-2; }
			}
			hq[jj] = tbest + ( jj-1 ) * ext;
			hk[jj] = tbestk;
		}
		for( ; j<=lgth2; j++ )
		{
			int p = prev[j-1];
			int wm = p, ij = 0, g;
			g = hq[j] + pen;
			if( g > wm ) { wm = g; ij = -( j - hk[j] ); }
			g = vm[j] + pen;
			if( g > wm ) { wm = g; ij = i - vmp[j]; }
			if( p > vm[j] ) { vm[j] = p; vmp[j] = negi1; }
			vm[j] += ext;
			wmrow[j] = wm;
			rowmax = ( wm > rowmax ) ? wm : rowmax;
			if( wm < ithr ) { ij = lstop; wm = ithr; }
			ijrow[j] = ij;
			cur[j] = wm + profrow[j];
		}
		if( rowmax > maxwm )
		{
			j = 1;
#if defined(MAFFT_SSE41)
			{
				__m128i vr = _mm_set1_epi32( rowmax );
				for( ; j+3<=lgth2; j+=4 )
				{
					int k = _mm_movemask_ps( _mm_castsi128_ps( _mm_cmpeq_epi32( _mm_loadu_si128( (__m128i *)( wmrow + j ) ), vr ) ) );
					if( k ) { j += __builtin_ctz( (unsigned)k ); break; }
				}
			}
#endif
			for( ; wmrow[j] != rowmax; j++ )
				;
			maxwm = rowmax; endali = i; endalj = j;
		}
		if( i < lgth1 ) cur[0] = (int)amino_dynamicmtx[u2[0]][u1[i]]; /* currentw[0] = initverticalw[i] */
	}

	free( profbuf ); free( pbuf1 ); free( pbuf2 ); free( vm ); free( vmp ); free( hq ); free( hk ); free( wmrow );
	*maxwmpt = (double)maxwm;
	*endalipt = endali;
	*endaljpt = endalj;
	return( 1 );
}
#endif

#if MAFFT_LBAND
static double L__align11_body( double **n_dynamicmtx, double scoreoffset, char **seq1, char **seq2, int alloclen, int *off1pt, int *off2pt )
#else
double L__align11( double **n_dynamicmtx, double scoreoffset, char **seq1, char **seq2, int alloclen, int *off1pt, int *off2pt )
#endif
/* score no keisan no sai motokaraaru gap no atukai ni mondai ga aru */
{
//	int k;
	int i, j;
	int lasti, lastj;                      /* outgap == 0 -> lgth1, outgap == 1 -> lgth1+1 */
	int lgth1, lgth2;
	int resultlen;
	double wm = 0.0;   /* int ?????? */
	double g;
	double *currentw, *previousw;
#if 1
	double *wtmp;
	int *ijppt;
	double *mjpt, *prept, *curpt;
	int *mpjpt;
#endif
	static TLS double mi, *m;
	static TLS int **ijp;
	static TLS int mpi, *mp;
	static TLS double *w1, *w2;
	static TLS double *match;
	static TLS double *initverticalw;    /* kufuu sureba iranai */
	static TLS double *lastverticalw;    /* kufuu sureba iranai */
	static TLS char **mseq1;
	static TLS char **mseq2;
	static TLS char **mseq;
//	static TLS int **intwork;
//	static TLS double **doublework;
	static TLS int orlgth1 = 0, orlgth2 = 0;
	static TLS double **amino_dynamicmtx = NULL; // ??
	double maxwm;
	int endali = 0, endalj = 0; // by D.Mathog, a guess
//	int endali, endalj;
	double localthr = -offset + scoreoffset * 600; // 2013/12/13
	double localthr2 = -offset + scoreoffset * 600; // 2013/12/13
//	double localthr = -offset;
//	double localthr2 = -offset;
	double fpenalty = (double)penalty;
	double fpenalty_ex = (double)penalty_ex;
	double fpenalty_shift = (double)penalty_shift;
	double fpenalty_tmp; // atode kesu

	int *warpis = NULL;
	int *warpjs = NULL;
	int *warpi = NULL;
	int *warpj = NULL;
	int *prevwarpi = NULL;
	int *prevwarpj = NULL;
	double *wmrecords = NULL;
	double *prevwmrecords = NULL;
	int warpn = 0;
	int warpbase;
#if defined(MAFFT_LSTRIPED)
	int kbfill = 0;
#endif
	double curm = 0.0;
	double *wmrecordspt, *wmrecords1pt, *prevwmrecordspt;
	int *warpipt, *warpjpt;



	if( seq1 == NULL )
	{
#if defined(MAFFT_LSTRIPED)
		kb_release();
#endif
#if MAFFT_LBAND
		free( lb_rows ); lb_rows = NULL; lb_cap = 0;
		free( lb_buf ); lb_buf = NULL; lb_bufcap = 0;
#endif
		if( orlgth1 > 0 && orlgth2 > 0 )
		{
			orlgth1 = 0;
			orlgth2 = 0;
			free( mseq1 );
			free( mseq2 );
			FreeFloatVec( w1 );
			FreeFloatVec( w2 );
			FreeFloatVec( match );
			FreeFloatVec( initverticalw );
			FreeFloatVec( lastverticalw );

			FreeFloatVec( m );
			FreeIntVec( mp );

			FreeCharMtx( mseq );
			if( amino_dynamicmtx ) FreeDoubleMtx( amino_dynamicmtx ); amino_dynamicmtx = NULL;

		}
		return( 0.0 );
	}


	if( orlgth1 == 0 )
	{
		mseq1 = AllocateCharMtx( njob, 0 );
		mseq2 = AllocateCharMtx( njob, 0 );
	}


	lgth1 = strlen( seq1[0] );
	lgth2 = strlen( seq2[0] );


	warpbase = lgth1 + lgth2;
	warpis = NULL;
	warpjs = NULL;
	warpn = 0;
	if( trywarp )
	{
		wmrecords = AllocateFloatVec( lgth2+1 );
		warpi = AllocateIntVec( lgth2+1 );
		warpj = AllocateIntVec( lgth2+1 );
		prevwmrecords = AllocateFloatVec( lgth2+1 );
		prevwarpi = AllocateIntVec( lgth2+1 );
		prevwarpj = AllocateIntVec( lgth2+1 );
		for( i=0; i<lgth2+1; i++ ) prevwmrecords[i] = 0.0;
		for( i=0; i<lgth2+1; i++ ) wmrecords[i] = 0.0;
		for( i=0; i<lgth2+1; i++ ) prevwarpi[i] = -warpbase;
		for( i=0; i<lgth2+1; i++ ) prevwarpj[i] = -warpbase;
		for( i=0; i<lgth2+1; i++ ) warpi[i] = -warpbase;
		for( i=0; i<lgth2+1; i++ ) warpj[i] = -warpbase;
	}


	if( lgth1 > orlgth1 || lgth2 > orlgth2 )
	{
		int ll1, ll2;

		if( orlgth1 > 0 && orlgth2 > 0 )
		{
			FreeFloatVec( w1 );
			FreeFloatVec( w2 );
			FreeFloatVec( match );
			FreeFloatVec( initverticalw );
			FreeFloatVec( lastverticalw );

			FreeFloatVec( m );
			FreeIntVec( mp );

			FreeCharMtx( mseq );
			if( amino_dynamicmtx ) FreeDoubleMtx( amino_dynamicmtx ); amino_dynamicmtx = NULL;


//			FreeFloatMtx( doublework );
//			FreeIntMtx( intwork );
		}

		ll1 = MAX( (int)(1.3*lgth1), orlgth1 ) + 100;
		ll2 = MAX( (int)(1.3*lgth2), orlgth2 ) + 100;

#if DEBUG
		fprintf( stderr, "\ntrying to allocate (%d+%d)xn matrices ... ", ll1, ll2 );
#endif

		w1 = AllocateFloatVec( ll2+2 );
		w2 = AllocateFloatVec( ll2+2 );
		match = AllocateFloatVec( ll2+2 );

		initverticalw = AllocateFloatVec( ll1+2 );
		lastverticalw = AllocateFloatVec( ll1+2 );

		m = AllocateFloatVec( ll2+2 );
		mp = AllocateIntVec( ll2+2 );

		mseq = AllocateCharMtx( njob, ll1+ll2 );


//		doublework = AllocateFloatMtx( nalphabets, MAX( ll1, ll2 )+2 ); 
//		intwork = AllocateIntMtx( nalphabets, MAX( ll1, ll2 )+2 ); 

#if DEBUG
		fprintf( stderr, "succeeded\n" );
#endif
		amino_dynamicmtx = AllocateDoubleMtx( 0x100, 0x100 );
		orlgth1 = ll1 - 100;
		orlgth2 = ll2 - 100;
	}

    for( i=0; i<nalphabets; i++) for( j=0; j<nalphabets; j++ )
		amino_dynamicmtx[(int)amino[i]][(int)amino[j]] = (double)n_dynamicmtx[i][j];


	mseq1[0] = mseq[0];
	mseq2[0] = mseq[1];
	currentw = w1; /* also a valid (unused) argument to Ltracking after an integer fill */

#if defined(MAFFT_LSTRIPED) && MAFFT_KB_NOIJP
	/* The byte/bit traceback is self-contained. Only allocate the full int matrix if
	   the striped fill declines (fractional scoring, range limits or warped alignment). */
	if( !trywarp && !lband_on() )
	{
		localstop = lgth1+lgth2+1;
		if( Lfill_kb( amino_dynamicmtx, n_dynamicmtx, scoreoffset, seq1[0], seq2[0], lgth1, lgth2, &maxwm, &endali, &endalj ) )
		{
			kbfill = 1;
			goto Lint_done;
		}
	}
#endif

	if( orlgth1 > commonAlloc1 || orlgth2 > commonAlloc2 )
	{
		int ll1, ll2;

		if( commonAlloc1 && commonAlloc2 )
		{
			FreeIntMtx( commonIP );
		}

		ll1 = MAX( orlgth1, commonAlloc1 );
		ll2 = MAX( orlgth2, commonAlloc2 );

#if DEBUG
		fprintf( stderr, "\n\ntrying to allocate %dx%d matrices ... ", ll1+1, ll2+1 );
#endif

		commonIP = AllocateIntMtx( ll1+10, ll2+10 );

#if DEBUG
		fprintf( stderr, "succeeded\n\n" );
#endif

		commonAlloc1 = ll1;
		commonAlloc2 = ll2;
	}
	ijp = commonIP;

#if MAFFT_LBAND
	/* The exact band first (it needs ijp); if it declines, the striped fill as before. */
	if( !trywarp && lband_on() )
	{
		localstop = lgth1+lgth2+1;
		if( Lband_try( amino_dynamicmtx, n_dynamicmtx, scoreoffset, seq1[0], seq2[0], lgth1, lgth2, ijp, localstop, &maxwm, &endali, &endalj ) )
		{
			kbfill = 2; /* the striped band rows, read through lband_ijv() */
			goto Lint_done;
		}
		if( Lfill_kb( amino_dynamicmtx, n_dynamicmtx, scoreoffset, seq1[0], seq2[0], lgth1, lgth2, &maxwm, &endali, &endalj ) )
		{
			kbfill = 1;
			goto Lint_done;
		}
	}
#endif
#if defined(MAFFT_LSTRIPED) && !MAFFT_KB_NOIJP
	if( !trywarp )
	{
		localstop = lgth1+lgth2+1;
		if( Lfill_kb( amino_dynamicmtx, n_dynamicmtx, scoreoffset, seq1[0], seq2[0], lgth1, lgth2, &maxwm, &endali, &endalj ) )
		{
			kbfill = 1;
			goto Lint_done;
		}
	}
#endif
	if( !trywarp )
	{
		localstop = lgth1+lgth2+1;
		if( Lfill_int( amino_dynamicmtx, n_dynamicmtx, scoreoffset, seq1[0], seq2[0], lgth1, lgth2, ijp, localstop, &maxwm, &endali, &endalj ) )
			goto Lint_done;
	}


#if 0
	for( i=0; i<lgth1; i++ ) 
		fprintf( stderr, "ogcp1[%d]=%f\n", i, ogcp1[i] );
#endif

	currentw = w1;
	previousw = w2;

	match_calc_mtx( amino_dynamicmtx, initverticalw, seq2, seq1, 0, lgth1 );

	match_calc_mtx( amino_dynamicmtx, currentw, seq1, seq2, 0, lgth2 );


	lasti = lgth2+1;
	for( j=1; j<lasti; ++j ) 
	{
		m[j] = currentw[j-1]; mp[j] = 0;
#if 0
		if( m[j] < localthr ) m[j] = localthr2;
#endif
	}

	lastverticalw[0] = currentw[lgth2-1];

	lasti = lgth1+1;

#if 0
fprintf( stderr, "currentw = \n" );
for( i=0; i<lgth1+1; i++ )
{
	fprintf( stderr, "%5.2f ", currentw[i] );
}
fprintf( stderr, "\n" );
fprintf( stderr, "initverticalw = \n" );
for( i=0; i<lgth2+1; i++ )
{
	fprintf( stderr, "%5.2f ", initverticalw[i] );
}
fprintf( stderr, "\n" );
#endif
#if DEBUG2
	fprintf( stderr, "\n" );
	fprintf( stderr, "       " );
	for( j=0; j<lgth2; j++ )
		fprintf( stderr, "%c     ", seq2[0][j] );
	fprintf( stderr, "\n" );
#endif

	localstop = lgth1+lgth2+1;
	maxwm = -999999999.9;
#if DEBUG2
	fprintf( stderr, "\n" );
	fprintf( stderr, "%c   ", seq1[0][0] );

	for( j=0; j<lgth2+1; j++ )
		fprintf( stderr, "%5.0f ", currentw[j] );
	fprintf( stderr, "\n" );
#endif

	for( i=1; i<lasti; i++ )
	{
		wtmp = previousw; 
		previousw = currentw;
		currentw = wtmp;

		previousw[0] = initverticalw[i-1];

		match_calc_mtx( amino_dynamicmtx, currentw, seq1, seq2, i, lgth2 );
#if DEBUG2
		fprintf( stderr, "%c   ", seq1[0][i] );
		fprintf( stderr, "%5.0f ", currentw[0] );
#endif

#if XXXXXXX
fprintf( stderr, "\n" );
fprintf( stderr, "i=%d\n", i );
fprintf( stderr, "currentw = \n" );
for( j=0; j<lgth2; j++ )
{
	fprintf( stderr, "%5.2f ", currentw[j] );
}
fprintf( stderr, "\n" );
#endif
#if XXXXXXX
fprintf( stderr, "\n" );
fprintf( stderr, "i=%d\n", i );
fprintf( stderr, "currentw = \n" );
for( j=0; j<lgth2; j++ )
{
	fprintf( stderr, "%5.2f ", currentw[j] );
}
fprintf( stderr, "\n" );
#endif
		currentw[0] = initverticalw[i];

		mi = previousw[0]; mpi = 0;

#if 0
		if( mi < localthr ) mi = localthr2;
#endif

		ijppt = ijp[i] + 1;
		mjpt = m + 1;
		prept = previousw;
		curpt = currentw + 1;
		mpjpt = mp + 1;
		lastj = lgth2+1;

		if( trywarp )
		{
			prevwmrecordspt = prevwmrecords;
			wmrecordspt = wmrecords+1;
			wmrecords1pt = wmrecords;
			warpipt = warpi + 1;
			warpjpt = warpj + 1;
		}
		for( j=1; j<lastj; j++ )
		{
			wm = *prept;
			*ijppt = 0;

#if 0
			fprintf( stderr, "%5.0f->", wm );
#endif
#if 0
			fprintf( stderr, "%5.0f?", g );
#endif
			if( (g=mi+fpenalty) > wm )
			{
				wm = g;
				*ijppt = -( j - mpi );
			}
			if( *prept > mi )
			{
				mi = *prept;
				mpi = j-1;
			}

#if USE_PENALTY_EX
			mi += fpenalty_ex;
#endif

#if 0 
			fprintf( stderr, "%5.0f?", g );
#endif
			if( (g=*mjpt+fpenalty) > wm )
			{
				wm = g;
				*ijppt = +( i - *mpjpt );
			}
			if( *prept > *mjpt )
			{
				*mjpt = *prept;
				*mpjpt = i-1;
			}
#if USE_PENALTY_EX
			*mjpt += fpenalty_ex;
#endif

			if( maxwm < wm )
			{
				maxwm = wm;
				endali = i;
				endalj = j;
			}
#if 1
			if( wm < localthr )
			{
//				fprintf( stderr, "stop i=%d, j=%d, curpt=%f, localthr = %f\n", i, j, *curpt, localthr );
				*ijppt = localstop;
				wm = localthr2;
			}
#endif
#if 0
			fprintf( stderr, "%5.0f ", *curpt );
#endif
#if 0
			fprintf( stderr, "wm (%d,%d) = %5.0f\n", i, j, wm );
//			fprintf( stderr, "%c-%c *ijppt = %d, localstop = %d\n", seq1[0][i], seq2[0][j], *ijppt, localstop );
#endif
			if( trywarp )
			{
				fpenalty_tmp = fpenalty_shift + fpenalty_ex * ( i - prevwarpi[j-1] + j - prevwarpj[j-1] );
//				fprintf( stderr, "fpenalty_shift = %f\n", fpenalty_tmp );

//				fprintf( stderr, "\n\n\nwarp to %c-%c (%d-%d) from %c-%c (%d-%d) ? prevwmrecords[%d] = %f + %f <- wm = %f\n", seq1[0][prevwarpi[j-1]], seq2[0][prevwarpj[j-1]], prevwarpi[j-1], prevwarpj[j-1], seq1[0][i], seq2[0][j], i, j, j, prevwmrecords[j-1], fpenalty_tmp, wm );
//				if( (g=prevwmrecords[j-1] + fpenalty_shift )> wm )
				if( ( g=*prevwmrecordspt++ + fpenalty_tmp )> wm ) // naka ha osokute kamawanai
				{
//					fprintf( stderr, "Yes! Warp!! from %d-%d (%c-%c) to  %d-%d (%c-%c) fpenalty_tmp = %f! warpn = %d\n", i, j, seq1[0][i], seq2[0][j-1], prevwarpi[j-1], prevwarpj[j-1],seq1[0][prevwarpi[j-1]], seq2[0][prevwarpj[j-1]], fpenalty_tmp, warpn );
					if( warpn && prevwarpi[j-1] == warpis[warpn-1] && prevwarpj[j-1] == warpjs[warpn-1] )
					{
						*ijppt = warpbase + warpn - 1;
					}
					else
					{
						*ijppt = warpbase + warpn;
						warpis = realloc( warpis, sizeof(int) * ( warpn+1 ) );
						warpjs = realloc( warpjs, sizeof(int) * ( warpn+1 ) );
						warpis[warpn] = prevwarpi[j-1];
						warpjs[warpn] = prevwarpj[j-1];
						warpn++;
					}
					wm = g;
				}
				else
				{
				}

				curm = *curpt + wm;
	
//				fprintf( stderr, "###### curm = %f at %c-%c, i=%d, j=%d\n", curm, seq1[0][i], seq2[0][j], i, j ); 
	
//				fprintf( stderr, "copy from i, j-1? %f > %f?\n", wmrecords[j-1], curm );
//				if( wmrecords[j-1] > wmrecords[j] )
				if( *wmrecords1pt > *wmrecordspt )
				{
//					fprintf( stderr, "yes\n" );
//					wmrecords[j] = wmrecords[j-1];
					*wmrecordspt = *wmrecords1pt;
//					warpi[j] = warpi[j-1];
//					warpj[j] = warpj[j-1];
					*warpipt  = *(warpipt-1);
					*warpjpt  = *(warpjpt-1);
//					fprintf( stderr, "warpi[j]=%d, warpj[j]=%d wmrecords[j] = %f\n", warpi[j], warpj[j], wmrecords[j] );
				}
//				else
//				{
//					fprintf( stderr, "no\n" );
//				}
	
//				fprintf( stderr, " curm = %f at %c-%c\n", curm, seq1[0][i], seq2[0][j] ); 
//				fprintf( stderr, " wmrecords[%d] = %f\n", j, wmrecords[j] ); 
//				fprintf( stderr, "replace?\n" );
	
//				if( curm > wmrecords[j] )
				if( curm > *wmrecordspt )
				{
//					fprintf( stderr, "yes at %d-%d (%c-%c), replaced warp: warpi[j]=%d, warpj[j]=%d warpn=%d, wmrecords[j] = %f -> %f\n", i, j, seq1[0][i], seq2[0][j], i, j, warpn, wmrecords[j], curm );
//					wmrecords[j] = curm;
					*wmrecordspt = curm;
//					warpi[j] = i;
//					warpj[j] = j;
					*warpipt = i;
					*warpjpt = j;
				}
//				else
//				{
//					fprintf( stderr, "No! warpi[j]=%d, warpj[j]=%d wmrecords[j] = %f\n", warpi[j], warpj[j], wmrecords[j] );
//				}
//				fprintf( stderr, "%d-%d (%c-%c) curm = %5.0f, wmrecords[j]=%f\n", i, j, seq1[0][i], seq2[0][j], curm, wmrecords[j] );
				wmrecordspt++;
				wmrecords1pt++;
				warpipt++;
				warpjpt++;
			}

			*curpt++ += wm;
			ijppt++;
			mjpt++;
			prept++;
			mpjpt++;
		}
#if DEBUG2
		fprintf( stderr, "\n" );
#endif

		lastverticalw[i] = currentw[lgth2-1];
		if( trywarp )
		{
			fltncpy( prevwmrecords, wmrecords, lastj );
			intncpy( prevwarpi, warpi, lastj );
			intncpy( prevwarpj, warpj, lastj );
		}

	}
//	fprintf( stderr, "\nwm = %f\n", wm );
	if( trywarp )
	{
//		if( warpn ) fprintf( stderr, "warpn = %d\n", warpn );
		free( wmrecords );
		free( prevwmrecords );
		free( warpi );
		free( warpj );
		free( prevwarpi );
		free( prevwarpj );
	}

#if 0
	fprintf( stderr, "maxwm = %f\n", maxwm );
	fprintf( stderr, "endali = %d\n", endali );
	fprintf( stderr, "endalj = %d\n", endalj );
#endif

Lint_done:
#if defined(MAFFT_LSTRIPED)
	if( kbfill ? LKB_ISSTOP( kbfill, endali, endalj ) : ( ijp[endali][endalj] == localstop ) )
#else
	if( ijp[endali][endalj] == localstop )
#endif
	{
		strcpy( seq1[0], "" );
		strcpy( seq2[0], "" );
		*off1pt = *off2pt = 0;
		fprintf( stderr, "maxwm <- 0.0 \n" );
		return( 0.0 );
	}
		
#if defined(MAFFT_LSTRIPED)
	Ltracking( currentw, lastverticalw, seq1, seq2, mseq1, mseq2, ijp, off1pt, off2pt, endali, endalj, warpis, warpjs, warpbase, kbfill );
#else
	Ltracking( currentw, lastverticalw, seq1, seq2, mseq1, mseq2, ijp, off1pt, off2pt, endali, endalj, warpis, warpjs, warpbase, 0 );
#endif
	if( warpis ) free( warpis );
	if( warpjs ) free( warpjs );


	resultlen = strlen( mseq1[0] );
	if( alloclen < resultlen || resultlen > N )
	{
		fprintf( stderr, "alloclen=%d, resultlen=%d, N=%d\n", alloclen, resultlen, N );
		ErrorExit( "LENGTH OVER!\n" );
	}


	strcpy( seq1[0], mseq1[0] );
	strcpy( seq2[0], mseq2[0] );

#if 0
	fprintf( stderr, "wm=%f\n", wm );
	fprintf( stderr, ">\n%s\n", mseq1[0] );
	fprintf( stderr, ">\n%s\n", mseq2[0] );

	fprintf( stderr, "*off1pt = %d, *off2pt = %d\n", *off1pt, *off2pt );

	fprintf( stderr, "maxwm = %f\n", maxwm );
	fprintf( stderr, "   wm = %f\n",    wm );
#endif

	return( maxwm );
}

#if MAFFT_LBAND
/*
 * MAFFT_LBAND_CHECK (any value): every pair is aligned twice, without and with the band, and
 * the two results (score, offsets, aligned strings) must be identical, else the run stops.
 * For tests only: it does the all-pairs work twice.
 */
static char *lband_copy( const char *t )
{
	char *c = malloc( strlen( t ) + 1 );
	if( !c ) ErrorExit( "Cannot allocate memory." );
	return( strcpy( c, t ) );
}
double L__align11( double **n_dynamicmtx, double scoreoffset, char **seq1, char **seq2, int alloclen, int *off1pt, int *off2pt )
{
	if( lband_check_on() && seq1 && lband_on() )
	{
		char *a1 = lband_copy( seq1[0] ), *a2 = lband_copy( seq2[0] ), *r1, *r2;
		int o1, o2, p1, p2;
		double v0, v1;
		lband_force = 0;
		v0 = L__align11_body( n_dynamicmtx, scoreoffset, seq1, seq2, alloclen, &o1, &o2 );
		r1 = lband_copy( seq1[0] ); r2 = lband_copy( seq2[0] );
		strcpy( seq1[0], a1 ); strcpy( seq2[0], a2 );
		lband_force = 1;
		v1 = L__align11_body( n_dynamicmtx, scoreoffset, seq1, seq2, alloclen, &p1, &p2 );
		lband_force = -1;
		if( memcmp( &v0, &v1, sizeof( double ) ) || o1 != p1 || o2 != p2 || strcmp( r1, seq1[0] ) || strcmp( r2, seq2[0] ) )
		{
			fprintf( stderr, "\nLBAND_CHECK: banded fill differs (score %.17g vs %.17g, offsets %d,%d vs %d,%d)\n", v0, v1, o1, o2, p1, p2 );
			exit( 1 );
		}
		free( a1 ); free( a2 ); free( r1 ); free( r2 );
		*off1pt = p1; *off2pt = p2;
		return( v1 );
	}
	return( L__align11_body( n_dynamicmtx, scoreoffset, seq1, seq2, alloclen, off1pt, off2pt ) );
}
#endif


double L__align11_noalign( double **n_dynamicmtx, char **seq1, char **seq2 )
// warp mitaiou
{
//	int k;
	int i, j;
	int lasti, lastj;                      /* outgap == 0 -> lgth1, outgap == 1 -> lgth1+1 */
	int lgth1, lgth2;
//	int resultlen;
	double wm = 0.0;   /* int ?????? */
	double g;
	double *currentw, *previousw;
#if 1
	double *wtmp;
//	int *ijppt;
	double *mjpt, *prept, *curpt;
//	int *mpjpt;
#endif
	static TLS double mi, *m;
//	static TLS int **ijp;
//	static TLS int mpi, *mp;
	static TLS double *w1, *w2;
	static TLS double *match;
	static TLS double *initverticalw;    /* kufuu sureba iranai */
	static TLS double *lastverticalw;    /* kufuu sureba iranai */
//	static TLS char **mseq1;
//	static TLS char **mseq2;
//	static TLS char **mseq;
//	static TLS int **intwork;
//	static TLS double **doublework;
	static TLS int orlgth1 = 0, orlgth2 = 0;
	static TLS double **amino_dynamicmtx = NULL; // ??
	double maxwm;
//	int endali = 0, endalj = 0; // by D.Mathog, a guess
//	int endali, endalj;
	double localthr = -offset;
	double localthr2 = -offset;
//	double localthr = 100;
//	double localthr2 = 100;
	double fpenalty = (double)penalty;
	double fpenalty_ex = (double)penalty_ex;

	if( seq1 == NULL )
	{
#if defined(MAFFT_LSTRIPED) && MAFFT_FAST_NOALIGN
		kb_release();
#endif
		if( orlgth1 > 0 && orlgth2 > 0 )
		{
			orlgth1 = 0;
			orlgth2 = 0;
//			free( mseq1 );
//			free( mseq2 );
			FreeFloatVec( w1 );
			FreeFloatVec( w2 );
			FreeFloatVec( match );
			FreeFloatVec( initverticalw );
			FreeFloatVec( lastverticalw );

			FreeFloatVec( m );
//			FreeIntVec( mp );

//			FreeCharMtx( mseq );
			if( amino_dynamicmtx ) FreeDoubleMtx( amino_dynamicmtx ); amino_dynamicmtx = NULL;

		}
		return( 0.0 );
	}


//	if( orlgth1 == 0 )
//	{
//		mseq1 = AllocateCharMtx( njob, 0 );
//		mseq2 = AllocateCharMtx( njob, 0 );
//	}


	lgth1 = strlen( seq1[0] );
	lgth2 = strlen( seq2[0] );

	if( lgth1 > orlgth1 || lgth2 > orlgth2 )
	{
		int ll1, ll2;

		if( orlgth1 > 0 && orlgth2 > 0 )
		{
			FreeFloatVec( w1 );
			FreeFloatVec( w2 );
			FreeFloatVec( match );
			FreeFloatVec( initverticalw );
			FreeFloatVec( lastverticalw );

			FreeFloatVec( m );
//			FreeIntVec( mp );

//			FreeCharMtx( mseq );



//			FreeFloatMtx( doublework );
//			FreeIntMtx( intwork );
			if( amino_dynamicmtx ) FreeDoubleMtx( amino_dynamicmtx ); amino_dynamicmtx = NULL;
		}

		ll1 = MAX( (int)(1.3*lgth1), orlgth1 ) + 100;
		ll2 = MAX( (int)(1.3*lgth2), orlgth2 ) + 100;

#if DEBUG
		fprintf( stderr, "\ntrying to allocate (%d+%d)xn matrices ... ", ll1, ll2 );
#endif

		w1 = AllocateFloatVec( ll2+2 );
		w2 = AllocateFloatVec( ll2+2 );
		match = AllocateFloatVec( ll2+2 );

		initverticalw = AllocateFloatVec( ll1+2 );
		lastverticalw = AllocateFloatVec( ll1+2 );

		m = AllocateFloatVec( ll2+2 );
//		mp = AllocateIntVec( ll2+2 );

//		mseq = AllocateCharMtx( njob, ll1+ll2 );


//		doublework = AllocateFloatMtx( nalphabets, MAX( ll1, ll2 )+2 ); 
//		intwork = AllocateIntMtx( nalphabets, MAX( ll1, ll2 )+2 ); 

#if DEBUG
		fprintf( stderr, "succeeded\n" );
#endif
//		amino_dynamicmtx = AllocateDoubleMtx( 0x80, 0x80 ); 
		amino_dynamicmtx = AllocateDoubleMtx( 0x100, 0x100 );  // 2017/Nov.  constants.c no 'charsize' wo global hensuu nishita houga yoi?
		orlgth1 = ll1 - 100;
		orlgth2 = ll2 - 100;
	}

    for( i=0; i<nalphabets; i++) for( j=0; j<nalphabets; j++ )
		amino_dynamicmtx[(int)amino[i]][(int)amino[j]] = (double)n_dynamicmtx[i][j];



#if defined(MAFFT_LSTRIPED) && MAFFT_FAST_NOALIGN
	/* Identical recurrence and raw maximum to the alignment fill. Its range guard
	   also puts every value above the scalar maximum's -999999999.9 initializer.
	   Keep zero results on the scalar path: integral matrices may contain -0.0,
	   whose sign can survive the original score-only return value. */
	{
		double fastscore;
		if( Lscore_kb( amino_dynamicmtx, n_dynamicmtx, 0.0, seq1[0], seq2[0], lgth1, lgth2, &fastscore ) && fastscore != 0.0 )
		{
			maxwm = fastscore;
			goto Lnoalign_done;
		}
	}
#endif

//	mseq1[0] = mseq[0];
//	mseq2[0] = mseq[1];


//	if( orlgth1 > commonAlloc1 || orlgth2 > commonAlloc2 )
//	{
//		int ll1, ll2;
//
//		if( commonAlloc1 && commonAlloc2 )
//		{
//			FreeIntMtx( commonIP );
//		}
//
//		ll1 = MAX( orlgth1, commonAlloc1 );
//		ll2 = MAX( orlgth2, commonAlloc2 );

#if DEBUG
//		fprintf( stderr, "\n\ntrying to allocate %dx%d matrices ... ", ll1+1, ll2+1 );
#endif

//		commonIP = AllocateIntMtx( ll1+10, ll2+10 );

#if DEBUG
//		fprintf( stderr, "succeeded\n\n" );
#endif

//		commonAlloc1 = ll1;
//		commonAlloc2 = ll2;
//	}
//	ijp = commonIP;


#if 0
	for( i=0; i<lgth1; i++ ) 
		fprintf( stderr, "ogcp1[%d]=%f\n", i, ogcp1[i] );
#endif

	currentw = w1;
	previousw = w2;

	match_calc_mtx( amino_dynamicmtx, initverticalw, seq2, seq1, 0, lgth1 );

	match_calc_mtx( amino_dynamicmtx, currentw, seq1, seq2, 0, lgth2 );


	lasti = lgth2+1;
	for( j=1; j<lasti; ++j ) 
	{
		m[j] = currentw[j-1]; 
//		mp[j] = 0;
#if 0
		if( m[j] < localthr ) m[j] = localthr2;
#endif
	}

	lastverticalw[0] = currentw[lgth2-1];

	lasti = lgth1+1;

#if 0
fprintf( stderr, "currentw = \n" );
for( i=0; i<lgth1+1; i++ )
{
	fprintf( stderr, "%5.2f ", currentw[i] );
}
fprintf( stderr, "\n" );
fprintf( stderr, "initverticalw = \n" );
for( i=0; i<lgth2+1; i++ )
{
	fprintf( stderr, "%5.2f ", initverticalw[i] );
}
fprintf( stderr, "\n" );
#endif
#if DEBUG2
	fprintf( stderr, "\n" );
	fprintf( stderr, "       " );
	for( j=0; j<lgth2; j++ )
		fprintf( stderr, "%c     ", seq2[0][j] );
	fprintf( stderr, "\n" );
#endif

	localstop = lgth1+lgth2+1;
	maxwm = -999999999.9;
#if DEBUG2
	fprintf( stderr, "\n" );
	fprintf( stderr, "%c   ", seq1[0][0] );

	for( j=0; j<lgth2+1; j++ )
		fprintf( stderr, "%5.0f ", currentw[j] );
	fprintf( stderr, "\n" );
#endif

	for( i=1; i<lasti; i++ )
	{
		wtmp = previousw; 
		previousw = currentw;
		currentw = wtmp;

		previousw[0] = initverticalw[i-1];

		match_calc_mtx( amino_dynamicmtx, currentw, seq1, seq2, i, lgth2 );
#if DEBUG2
		fprintf( stderr, "%c   ", seq1[0][i] );
		fprintf( stderr, "%5.0f ", currentw[0] );
#endif

#if XXXXXXX
fprintf( stderr, "\n" );
fprintf( stderr, "i=%d\n", i );
fprintf( stderr, "currentw = \n" );
for( j=0; j<lgth2; j++ )
{
	fprintf( stderr, "%5.2f ", currentw[j] );
}
fprintf( stderr, "\n" );
#endif
#if XXXXXXX
fprintf( stderr, "\n" );
fprintf( stderr, "i=%d\n", i );
fprintf( stderr, "currentw = \n" );
for( j=0; j<lgth2; j++ )
{
	fprintf( stderr, "%5.2f ", currentw[j] );
}
fprintf( stderr, "\n" );
#endif
		currentw[0] = initverticalw[i];

		mi = previousw[0]; 
//		mpi = 0;

#if 0
		if( mi < localthr ) mi = localthr2;
#endif

//		ijppt = ijp[i] + 1;
		mjpt = m + 1;
		prept = previousw;
		curpt = currentw + 1;
//		mpjpt = mp + 1;
		lastj = lgth2+1;
		for( j=1; j<lastj; j++ )
		{
			wm = *prept;
//			*ijppt = 0;

#if 0
			fprintf( stderr, "%5.0f->", wm );
#endif
#if 0
			fprintf( stderr, "%5.0f?", g );
#endif
			if( (g=mi+fpenalty) > wm )
			{
				wm = g;
//				*ijppt = -( j - mpi );
			}
			if( *prept > mi )
			{
				mi = *prept;
//				mpi = j-1;
			}

#if USE_PENALTY_EX
			mi += fpenalty_ex;
#endif

#if 0 
			fprintf( stderr, "%5.0f?", g );
#endif
			if( (g=*mjpt+fpenalty) > wm )
			{
				wm = g;
//				*ijppt = +( i - *mpjpt );
			}
			if( *prept > *mjpt )
			{
				*mjpt = *prept;
//				*mpjpt = i-1;
			}
#if USE_PENALTY_EX
			*mjpt += fpenalty_ex;
#endif

			if( maxwm < wm )
			{
				maxwm = wm;
//				endali = i;
//				endalj = j;
			}
#if 1
			if( wm < localthr )
			{
//				fprintf( stderr, "stop i=%d, j=%d, curpt=%f\n", i, j, *curpt );
//				*ijppt = localstop;
				wm = localthr2;
			}
#endif
#if 0
			fprintf( stderr, "%5.0f ", *curpt );
#endif
#if DEBUG2
			fprintf( stderr, "%5.0f ", wm );
//			fprintf( stderr, "%c-%c *ijppt = %d, localstop = %d\n", seq1[0][i], seq2[0][j], *ijppt, localstop );
#endif

			*curpt++ += wm;
//			ijppt++;
			mjpt++;
			prept++;
//			mpjpt++;
		}
#if DEBUG2
		fprintf( stderr, "\n" );
#endif

		lastverticalw[i] = currentw[lgth2-1];
	}


#if 0
	fprintf( stderr, "maxwm = %f\n", maxwm );
	fprintf( stderr, "endali = %d\n", endali );
	fprintf( stderr, "endalj = %d\n", endalj );
#endif


#if defined(MAFFT_LSTRIPED) && MAFFT_FAST_NOALIGN
Lnoalign_done:
#endif
#if 0 // IRUKAMO!!!!
	if( ijp[endali][endalj] == localstop )
	{
		strcpy( seq1[0], "" );
		strcpy( seq2[0], "" );
		*off1pt = *off2pt = 0;
		fprintf( stderr, "maxwm <- 0.0 \n" );
		return( 0.0 );
	}
#else
	if( maxwm < localthr )
	{
		fprintf( stderr, "maxwm <- 0.0 \n" );
		return( 0.0 );
	}
#endif
		
//	Ltracking( currentw, lastverticalw, seq1, seq2, mseq1, mseq2, ijp, off1pt, off2pt, endali, endalj );


//	resultlen = strlen( mseq1[0] );
//	if( alloclen < resultlen || resultlen > N )
//	{
//		fprintf( stderr, "alloclen=%d, resultlen=%d, N=%d\n", alloclen, resultlen, N );
//		ErrorExit( "LENGTH OVER!\n" );
//	}


//	strcpy( seq1[0], mseq1[0] );
//	strcpy( seq2[0], mseq2[0] );

#if 0
	fprintf( stderr, "wm=%f\n", wm );
	fprintf( stderr, ">\n%s\n", mseq1[0] );
	fprintf( stderr, ">\n%s\n", mseq2[0] );

	fprintf( stderr, "maxwm = %f\n", maxwm );
	fprintf( stderr, "   wm = %f\n",    wm );
#endif

	return( maxwm );
}
