/*
 * Metal implementation of l11gpu_align() (see l11gpu.h).
 *
 * One pair per 32-thread SIMD group.  Row i of the DP depends only on row i-1, so each lane owns
 * a contiguous run of C columns and keeps their state (previous-row value, vertical-gap state,
 * seq2 code) in registers.  The horizontal-gap state H_j = (j-1)*ext + max_{k<=j-2} (prev[k] -
 * k*ext), with the first k attaining it, is a prefix maximum: each lane scans its own columns and
 * one SIMD-wide scan carries the maximum across lanes.  Traceback (int16 ijp in device memory) is
 * then done by lane 0, writing the two aligned strings back to front as Ltracking() does.
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include "l11gpu.h"

static NSString *kernelsrc = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct PairDesc { uint s1off; uint s2off; int l1; int l2; uint ijpoff; uint outoff; int pad0; int pad1; };\n"
"struct Params { int ncode; int pen; int ext; int thr; int gapc; int npairs; };\n"
"struct Result { int maxwm; int endi; int endj; int off1; int off2; int start; int status; int pad; };\n"
"static inline void comb( thread int &av, thread int &ak, int bv, int bk ) { if( bv > av ) { av = bv; ak = bk; } }\n"
"kernel void l11( device const uchar *codes [[buffer(0)]], device const char *chars [[buffer(1)]],\n"
"                 device const int *mtx [[buffer(2)]], device const PairDesc *pd [[buffer(3)]],\n"
"                 constant Params &P [[buffer(4)]], device short *ijpbuf [[buffer(5)]],\n"
"                 device char *outbuf [[buffer(6)]], device Result *res [[buffer(7)]],\n"
"                 uint gid [[threadgroup_position_in_grid]], uint lane [[thread_index_in_simdgroup]] )\n"
"{\n"
"  PairDesc d = pd[gid];\n"
"  const int l1 = d.l1, l2 = d.l2, nc1 = P.ncode + 1;\n"
"  const int pen = P.pen, ext = P.ext, thr = P.thr;\n"
"  const int lstop = l1 + l2 + 1;\n"
"  const int C = ( l2 + 31 ) / 32;\n"
"  device const uchar *c1 = codes + d.s1off;\n"
"  device const uchar *c2 = codes + d.s2off;\n"
"  device short *ijp = ijpbuf + d.ijpoff;\n"
"  const int W = 32 * C;\n"
"  const int j0 = (int)lane * C + 1;\n"
"  int Pv[CMAX], VM[CMAX], PK[CMAX];\n"
"  int u10 = c1[0];\n"
"  for( int t=0; t<CMAX; t++ )\n"
"  {\n"
"    int j = j0 + t;\n"
"    int cj = ( t < C && j < l2 ) ? (int)c2[j] : P.ncode;\n"
"    int cjm = ( t < C && j <= l2 ) ? (int)c2[j-1] : P.ncode;\n"
"    Pv[t] = mtx[u10*nc1 + cj];\n"
"    VM[t] = mtx[u10*nc1 + cjm];\n"
"    PK[t] = cj;\n"
"  }\n"
"  int maxwm = INT_MIN, endi = 0, endj = 0;\n"
"  for( int i=1; i<=l1; i++ )\n"
"  {\n"
"    int prev0 = mtx[ (int)c2[0]*nc1 + (int)c1[i-1] ];\n"
"    int c1i = ( i < l1 ) ? (int)c1[i] : (int)c1[0];\n"
"    int last = 0, av = INT_MIN, ak = -1, a2v = INT_MIN, a2k = -1;\n"
"    for( int t=0; t<CMAX; t++ )\n"
"    {\n"
"      int j = j0 + t;\n"
"      if( t < C && j <= l2 )\n"
"      {\n"
"        comb( av, ak, Pv[t] - j*ext, j );\n"
"        if( t <= C-2 ) { a2v = av; a2k = ak; }\n"
"      }\n"
"      if( t == C-1 ) last = Pv[t];\n"
"    }\n"
"    /* inclusive scan of lane aggregates (earlier lanes first) */\n"
"    int sv = av, sk = ak;\n"
"    for( uint dd=1; dd<32; dd<<=1 )\n"
"    {\n"
"      int ov = simd_shuffle_up( sv, dd ), ok = simd_shuffle_up( sk, dd );\n"
"      if( lane >= dd ) { int nv = ov, nk = ok; comb( nv, nk, sv, sk ); sv = nv; sk = nk; }\n"
"    }\n"
"    int ev = simd_shuffle_up( sv, 1 ), ek = simd_shuffle_up( sk, 1 );\n"
"    { int tv = prev0, tk = 0; if( lane > 0 ) comb( tv, tk, ev, ek ); ev = tv; ek = tk; }\n"
"    /* incl up to this lane's second-to-last column, passed to the next lane */\n"
"    int s2v = ev, s2k = ek; comb( s2v, s2k, a2v, a2k );\n"
"    int Sv = simd_shuffle_up( s2v, 1 ), Sk = simd_shuffle_up( s2k, 1 );\n"
"    if( lane == 0 ) { Sv = prev0; Sk = 0; }\n"
"    int pl = simd_shuffle_up( last, 1 );\n"
"    if( lane == 0 ) pl = prev0;\n"
"    int Rv = ev, Rk = ek, o1 = pl, o2 = 0;\n"
"    int lmax = INT_MIN, lj = INT_MAX;\n"
"    device short *ijrow = ijp + i*W + lane;\n"
"    for( int t=0; t<CMAX; t++ )\n"
"    {\n"
"      int j = j0 + t;\n"
"      if( t < C && j <= l2 )\n"
"      {\n"
"        int hv, hk;\n"
"        if( t == 0 ) { hv = Sv; hk = Sk; }\n"
"        else if( t == 1 ) { hv = Rv; hk = Rk; }\n"
"        else { comb( Rv, Rk, o2 - ( j-2 )*ext, j-2 ); hv = Rv; hk = Rk; }\n"
"        int p = o1;\n"
"        int wm = p, ij = 0, g;\n"
"        g = hv + ( j-1 )*ext + pen;\n"
"        if( g > wm ) { wm = g; ij = -( j - hk ); }\n"
"        int vm = VM[t], vmp = PK[t] >> 8;\n"
"        g = vm + pen;\n"
"        if( g > wm ) { wm = g; ij = i - vmp; }\n"
"        if( p > vm ) { vm = p; vmp = i-1; }\n"
"        VM[t] = vm + ext;\n"
"        PK[t] = ( vmp << 8 ) | ( PK[t] & 255 );\n"
"        if( wm > lmax ) { lmax = wm; lj = j; }\n"
"        if( wm < thr ) { ij = lstop; wm = thr; }\n"
"        ijrow[t*32] = (short)ij;\n"
"        int old = Pv[t];\n"
"        Pv[t] = wm + mtx[ c1i*nc1 + ( PK[t] & 255 ) ];\n"
"        o2 = o1; o1 = old;\n"
"      }\n"
"    }\n"
"    int rmax = simd_max( lmax );\n"
"    int rj = simd_min( lmax == rmax ? lj : INT_MAX );\n"
"    if( rmax > maxwm ) { maxwm = rmax; endi = i; endj = rj; }\n"
"  }\n"
"  threadgroup_barrier( mem_flags::mem_device );\n"
"  if( lane != 0 ) return;\n"
"  device char *m1 = outbuf + d.outoff;\n"
"  device char *m2 = m1 + ( l1 + l2 + 1 );\n"
"  device const char *s1 = chars + d.s1off;\n"
"  device const char *s2 = chars + d.s2off;\n"
"  char gap = (char)P.gapc;\n"
"  int pos = l1 + l2;\n"
"  m1[pos] = 0; m2[pos] = 0;\n"
"  Result r; r.maxwm = maxwm; r.endi = endi; r.endj = endj; r.off1 = 0; r.off2 = 0; r.pad = 0;\n"
"  if( ijp[endi*W + ((endj-1)%C)*32 + (endj-1)/C] == lstop ) { r.status = 1; r.start = pos; res[gid] = r; return; }\n"
"  int iin = endi, jin = endj, ifi = 0, jfi = 0, limk = l1 + l2, status = 0;\n"
"  for( int k=0; k<=limk; k++ )\n"
"  {\n"
"    int v = ( iin <= 0 || jin <= 0 ) ? lstop : (int)ijp[iin*W + ((jin-1)%C)*32 + (jin-1)/C];\n"
"    if( v >= l1 + l2 ) { status = 2; break; }\n"
"    else if( v < 0 ) { ifi = iin-1; jfi = jin+v; }\n"
"    else if( v > 0 ) { ifi = iin-v; jfi = jin-1; }\n"
"    else { ifi = iin-1; jfi = jin-1; }\n"
"    int l = iin - ifi;\n"
"    while( --l > 0 ) { --pos; m1[pos] = s1[ifi+l]; m2[pos] = gap; k++; }\n"
"    l = jin - jfi;\n"
"    while( --l > 0 ) { --pos; m1[pos] = gap; m2[pos] = s2[jfi+l]; k++; }\n"
"    if( iin <= 0 || jin <= 0 ) break;\n"
"    --pos; m1[pos] = s1[ifi]; m2[pos] = s2[jfi];\n"
"    int nv = ( ifi <= 0 || jfi <= 0 ) ? lstop : (int)ijp[ifi*W + ((jfi-1)%C)*32 + (jfi-1)/C];\n"
"    if( nv == lstop ) break;\n"
"    k++;\n"
"    iin = ifi; jin = jfi;\n"
"  }\n"
"  r.off1 = ( ifi == -1 ) ? 0 : ifi;\n"
"  r.off2 = ( jfi == -1 ) ? 0 : jfi;\n"
"  r.start = pos; r.status = status;\n"
"  res[gid] = r;\n"
"}\n";

typedef struct { uint32_t s1off, s2off; int32_t l1, l2; uint32_t ijpoff, outoff; int32_t pad0, pad1; } PairDesc;
typedef struct { int32_t ncode, pen, ext, thr, gapc, npairs; } Params;
typedef struct { int32_t maxwm, endi, endj, off1, off2, start, status, pad; } Result;

static const int cmaxes[] = { 8, 12, 16, 20, 24, 28, 32, 40, 48, 64 };
#define NCMAX ( sizeof( cmaxes ) / sizeof( cmaxes[0] ) )

static id<MTLDevice> dev = nil;
static id<MTLCommandQueue> queue = nil;
static id<MTLComputePipelineState> pipes[NCMAX];
static int gpu_failed = 0;

static id<MTLComputePipelineState> getpipe( int v )
{
	if( pipes[v] ) return( pipes[v] );
	@autoreleasepool
	{
		NSError *err = nil;
		NSString *src = [NSString stringWithFormat:@"#define CMAX %d\n%@", cmaxes[v], kernelsrc];
		MTLCompileOptions *opt = [MTLCompileOptions new];
		id<MTLLibrary> lib = [dev newLibraryWithSource:src options:opt error:&err];
		if( !lib ) { fprintf( stderr, "l11gpu: compile failed: %s\n", [[err description] UTF8String] ); return( nil ); }
		id<MTLFunction> fn = [lib newFunctionWithName:@"l11"];
		pipes[v] = [dev newComputePipelineStateWithFunction:fn error:&err];
		if( !pipes[v] ) fprintf( stderr, "l11gpu: pipeline failed: %s\n", [[err description] UTF8String] );
		else if( getenv( "L11GPU_VERBOSE" ) ) fprintf( stderr, "l11gpu: CMAX=%d maxthreads/tg=%d\n", cmaxes[v], (int)[pipes[v] maxTotalThreadsPerThreadgroup] );
		else if( [pipes[v] threadExecutionWidth] != 32 ) { fprintf( stderr, "l11gpu: simd width %d\n", (int)[pipes[v] threadExecutionWidth] ); pipes[v] = nil; }
	}
	return( pipes[v] );
}

static int init_gpu( void )
{
	if( gpu_failed ) return( 0 );
	if( dev ) return( 1 );
	if( getenv( "MAFFT_NOGPU" ) ) { gpu_failed = 1; return( 0 ); }
	dev = MTLCreateSystemDefaultDevice();
	if( !dev ) { gpu_failed = 1; return( 0 ); }
	queue = [dev newCommandQueue];
	if( !queue ) { gpu_failed = 1; return( 0 ); }
	return( 1 );
}

#ifndef IJPMB
#define IJPMB 96
#endif
#define IJPBUDGET ( (size_t)IJPMB << 20 )   /* bytes of traceback matrix per batch */
#define MAXBATCH 4096

typedef struct
{
	id<MTLBuffer> pd, ijp, out, res;
	id<MTLCommandBuffer> cb;
	int first, n;         /* pairs [first, first+n) of the job list */
	size_t ijpcap, outcap;
} batch_t;

int l11gpu_align( int nseq, char **seqs, const int *lens, const int *code, int ncode, const int *mtx,
                  int pen, int ext, int thr, char gapc,
                  int npairs, const int *pi, const int *pj, l11res *out )
{
	int k, p;
	int *job = NULL, njob = 0;
	size_t total = 0;
	uint32_t *soff;
	id<MTLBuffer> codesb, charsb, mtxb, parb;
	batch_t bt[2];
	int b, next;

	for( p=0; p<npairs; p++ ) out[p].status = -1;
	if( !init_gpu() ) return( 0 );

	@autoreleasepool
	{
		/* sequence storage: codes and characters, one offset per sequence */
		soff = malloc( sizeof( uint32_t ) * nseq );
		for( k=0; k<nseq; k++ ) { soff[k] = (uint32_t)total; total += lens[k] + 1; }
		codesb = [dev newBufferWithLength:total+16 options:MTLResourceStorageModeShared];
		charsb = [dev newBufferWithLength:total+16 options:MTLResourceStorageModeShared];
		if( !codesb || !charsb ) { free( soff ); return( 0 ); }
		{
			unsigned char *cb = [codesb contents];
			char *ch = [charsb contents];
			for( k=0; k<nseq; k++ )
			{
				int i;
				for( i=0; i<lens[k]; i++ ) cb[soff[k]+i] = (unsigned char)code[(unsigned char)seqs[k][i]];
				cb[soff[k]+lens[k]] = 0;
				memcpy( ch + soff[k], seqs[k], lens[k] + 1 );
			}
		}
		mtxb = [dev newBufferWithBytes:mtx length:sizeof( int ) * ( ncode+1 ) * ( ncode+1 ) options:MTLResourceStorageModeShared];

		/* pairs the kernel can take: 64 <= l2 <= 32*CMAX, traceback values fit in int16 */
		job = malloc( sizeof( int ) * ( npairs + 1 ) );
		for( p=0; p<npairs; p++ )
		{
			int l1 = lens[pi[p]], l2 = lens[pj[p]];
			if( l1 < 1 || l2 < 64 || l2 > 32 * cmaxes[NCMAX-1] || l1 + l2 + 1 > 32000 ) continue;
			if( (size_t)( l1 + 1 ) * ( l2 + 1 ) * 2 > IJPBUDGET ) continue;
			job[njob++] = p;
		}

		/* batches are per kernel variant: order jobs by width class (stable) */
		{
			int *tmp = malloc( sizeof( int ) * ( njob + 1 ) ), m = 0, v2;
			for( v2=0; v2<(int)NCMAX; v2++ ) for( k=0; k<njob; k++ )
			{
				int c = ( lens[pj[job[k]]] + 31 ) / 32, vv;
				for( vv=0; vv<(int)NCMAX && cmaxes[vv] < c; vv++ ) ;
				if( vv == v2 ) tmp[m++] = job[k];
			}
			free( job ); job = tmp;
		}
		for( b=0; b<2; b++ ) { bt[b].cb = nil; bt[b].n = 0; bt[b].ijpcap = bt[b].outcap = 0; bt[b].pd = bt[b].ijp = bt[b].out = bt[b].res = nil; }
		next = 0;
		b = 0;
		while( 1 )
		{
			batch_t *cur = bt + b, *oth = bt + ( 1 - b );
			/* encode the next batch into cur (after collecting whatever cur held) */
			if( cur->cb )
			{
				[cur->cb waitUntilCompleted];
				if( getenv( "L11GPU_VERBOSE" ) ) fprintf( stderr, "l11gpu: batch %d pairs, gpu %.3f s\n", cur->n, [cur->cb GPUEndTime] - [cur->cb GPUStartTime] );
				if( [cur->cb status] != MTLCommandBufferStatusCompleted ) { fprintf( stderr, "l11gpu: command buffer failed\n" ); gpu_failed = 1; }
				else
				{
					Result *rs = [cur->res contents];
					char *ob = [cur->out contents];
					PairDesc *pd = [cur->pd contents];
					for( k=0; k<cur->n; k++ )
					{
						int q = job[cur->first+k];
						Result *r = rs + k;
						if( r->status == 0 )
						{
							int len = ( pd[k].l1 + pd[k].l2 ) - r->start;
							char *m1 = ob + pd[k].outoff + r->start;
							char *m2 = ob + pd[k].outoff + ( pd[k].l1 + pd[k].l2 + 1 ) + r->start;
							out[q].s1 = malloc( len + 1 ); memcpy( out[q].s1, m1, len + 1 );
							out[q].s2 = malloc( len + 1 ); memcpy( out[q].s2, m2, len + 1 );
							out[q].status = 0;
						}
						else if( r->status == 1 ) { out[q].s1 = out[q].s2 = NULL; out[q].status = 1; }
						else continue; /* leave -1 */
						out[q].maxwm = r->maxwm; out[q].off1 = r->off1; out[q].off2 = r->off2;
					}
				}
				cur->cb = nil;
			}
			if( gpu_failed ) break;
			if( next >= njob )
			{
				if( !oth->cb ) break;
				b = 1 - b;
				continue;
			}
			{
				size_t ijpsz = 0, outsz = 0;
				int n = 0, v = 0;
				while( next + n < njob && n < MAXBATCH )
				{
					int q = job[next+n], l1 = lens[pi[q]], l2 = lens[pj[q]], c = ( l2 + 31 ) / 32, vv;
					size_t a = (size_t)( l1 + 1 ) * ( 32 * ( ( l2 + 31 ) / 32 ) );
					for( vv=0; vv<(int)NCMAX && cmaxes[vv] < c; vv++ ) ;
					if( n && ( ijpsz + a ) * 2 > IJPBUDGET ) break;
					if( n && vv != v ) break;
					if( !n ) v = vv;
					ijpsz += a; outsz += 2 * (size_t)( l1 + l2 + 1 );
					n++;
				}
				id<MTLComputePipelineState> ps = getpipe( v );
				if( !ps ) { gpu_failed = 1; break; }
				if( cur->ijpcap < ijpsz * 2 ) { cur->ijp = [dev newBufferWithLength:ijpsz*2 options:MTLResourceStorageModePrivate]; cur->ijpcap = ijpsz * 2; }
				if( cur->outcap < outsz ) { cur->out = [dev newBufferWithLength:outsz options:MTLResourceStorageModeShared]; cur->outcap = outsz; }
				cur->pd = [dev newBufferWithLength:sizeof( PairDesc ) * n options:MTLResourceStorageModeShared];
				cur->res = [dev newBufferWithLength:sizeof( Result ) * n options:MTLResourceStorageModeShared];
				if( !cur->ijp || !cur->out || !cur->pd || !cur->res ) { gpu_failed = 1; break; }
				{
					PairDesc *pd = [cur->pd contents];
					size_t io = 0, oo = 0;
					for( k=0; k<n; k++ )
					{
						int q = job[next+k];
						pd[k].s1off = soff[pi[q]]; pd[k].s2off = soff[pj[q]];
						pd[k].l1 = lens[pi[q]]; pd[k].l2 = lens[pj[q]];
						pd[k].ijpoff = (uint32_t)io; pd[k].outoff = (uint32_t)oo;
						pd[k].pad0 = pd[k].pad1 = 0;
						io += (size_t)( pd[k].l1 + 1 ) * ( 32 * ( ( pd[k].l2 + 31 ) / 32 ) );
						oo += 2 * (size_t)( pd[k].l1 + pd[k].l2 + 1 );
					}
				}
				{
					Params par = { ncode, pen, ext, thr, (unsigned char)gapc, n };
					parb = [dev newBufferWithBytes:&par length:sizeof( par ) options:MTLResourceStorageModeShared];
				}
				cur->first = next; cur->n = n;
				cur->cb = [queue commandBuffer];
				id<MTLComputeCommandEncoder> enc = [cur->cb computeCommandEncoder];
				[enc setComputePipelineState:ps];
				[enc setBuffer:codesb offset:0 atIndex:0];
				[enc setBuffer:charsb offset:0 atIndex:1];
				[enc setBuffer:mtxb offset:0 atIndex:2];
				[enc setBuffer:cur->pd offset:0 atIndex:3];
				[enc setBuffer:parb offset:0 atIndex:4];
				[enc setBuffer:cur->ijp offset:0 atIndex:5];
				[enc setBuffer:cur->out offset:0 atIndex:6];
				[enc setBuffer:cur->res offset:0 atIndex:7];
				[enc dispatchThreadgroups:MTLSizeMake( n, 1, 1 ) threadsPerThreadgroup:MTLSizeMake( 32, 1, 1 )];
				[enc endEncoding];
				[cur->cb commit];
				next += n;
			}
			b = 1 - b;
		}
		free( soff );
		free( job );
	}
	return( !gpu_failed );
}
