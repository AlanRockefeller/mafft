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

#include "l11gpu_src.h"   /* l11gpu_source[]: l11gpu.metal as text */
#include "l11gpu_lib.h"   /* l11gpu_metallib[]: the same, compiled offline (if a Metal compiler was found) */

typedef struct { uint32_t s1off, s2off; int32_t l1, l2; uint32_t ijpoff, outoff; int32_t pad0, pad1; } PairDesc;
typedef struct { int32_t ncode, pen, ext, thr, gapc, npairs; } Params;
typedef struct { int32_t maxwm, endi, endj, off1, off2, start, status, pad; } Result;

static const int cmaxes[] = { 8, 12, 16, 20, 24, 28, 32, 40, 48, 64 };
#define NCMAX ( sizeof( cmaxes ) / sizeof( cmaxes[0] ) )

static id<MTLDevice> dev = nil;
static id<MTLCommandQueue> queue = nil;
static id<MTLComputePipelineState> pipes[NCMAX];
static int gpu_failed = 0;

static id<MTLLibrary> lib = nil;

/* The embedded .metallib when there is one and it loads; otherwise the embedded source,
   compiled now.  MAFFT_GPU_SOURCE=1 forces the source path. */
static id<MTLLibrary> getlib( void )
{
	NSError *err = nil;
	if( lib ) return( lib );
#ifdef L11GPU_HAVE_METALLIB
	if( !getenv( "MAFFT_GPU_SOURCE" ) )
	{
		dispatch_data_t dd = dispatch_data_create( l11gpu_metallib, sizeof( l11gpu_metallib ), NULL, DISPATCH_DATA_DESTRUCTOR_DEFAULT );
		lib = [dev newLibraryWithData:dd error:&err];
		if( lib ) { if( getenv( "L11GPU_VERBOSE" ) ) fprintf( stderr, "l11gpu: using the embedded metallib\n" ); return( lib ); }
		fprintf( stderr, "l11gpu: embedded metallib did not load (%s); compiling the source\n", [[err description] UTF8String] );
	}
#endif
	{
		NSString *src = [[NSString alloc] initWithBytes:l11gpu_source length:sizeof( l11gpu_source ) encoding:NSUTF8StringEncoding];
		lib = [dev newLibraryWithSource:src options:[MTLCompileOptions new] error:&err];
		if( !lib ) fprintf( stderr, "l11gpu: compile failed: %s\n", [[err description] UTF8String] );
		else if( getenv( "L11GPU_VERBOSE" ) ) fprintf( stderr, "l11gpu: compiled the shader source at run time\n" );
	}
	return( lib );
}

static id<MTLComputePipelineState> getpipe( int v )
{
	if( pipes[v] ) return( pipes[v] );
	@autoreleasepool
	{
		NSError *err = nil;
		id<MTLLibrary> l = getlib();
		id<MTLFunction> fn;
		if( !l ) return( nil );
		fn = [l newFunctionWithName:[NSString stringWithFormat:@"l11_%d", cmaxes[v]]];
		if( !fn ) { fprintf( stderr, "l11gpu: no function l11_%d\n", cmaxes[v] ); return( nil ); }
		pipes[v] = [dev newComputePipelineStateWithFunction:fn error:&err];
		if( !pipes[v] ) fprintf( stderr, "l11gpu: pipeline failed: %s\n", [[err description] UTF8String] );
		else if( [pipes[v] threadExecutionWidth] != 32 ) { fprintf( stderr, "l11gpu: simd width %d\n", (int)[pipes[v] threadExecutionWidth] ); pipes[v] = nil; }
		else if( getenv( "L11GPU_VERBOSE" ) ) fprintf( stderr, "l11gpu: CMAX=%d maxthreads/tg=%d\n", cmaxes[v], (int)[pipes[v] maxTotalThreadsPerThreadgroup] );
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
