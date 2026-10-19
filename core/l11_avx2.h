/*
 * AVX2 version of Lfill_kb, included only by Lalign11.c. The layout and integer recurrence
 * are those of its AVX-512 sibling: all predecessor scores are from the previous row;
 * lane maxima from that row supply the horizontal carry once per row. Padding can only
 * propagate towards higher columns, so cannot affect a real cell. Strict comparisons keep
 * diagonal > horizontal > vertical on ties; marker replay keeps the first gap origin.
 *
 * MAFFT_AVX2_STRIPED=4/8 chooses the vector width for measurement and differential tests.
 */
#if MAFFT_AVX2_STRIPED == 8
#define KB_LANES 8
typedef __m256i kb_vec;
#define kb_set1 _mm256_set1_epi32
#define kb_zero _mm256_setzero_si256
#define kb_add _mm256_add_epi32
#define kb_sub _mm256_sub_epi32
#define kb_mul _mm256_mullo_epi32
#define kb_max _mm256_max_epi32
#define kb_gt _mm256_cmpgt_epi32
#define kb_eq _mm256_cmpeq_epi32
#define kb_and _mm256_and_si256
#define kb_or _mm256_or_si256
#define kb_load(p) _mm256_load_si256( (const __m256i *)(p) )
#define kb_store(p,v) _mm256_store_si256( (__m256i *)(p), (v) )
#define kb_maskbits(v) ((unsigned)_mm256_movemask_ps( _mm256_castsi256_ps(v) ))
static inline kb_vec kb_select( kb_vec a, kb_vec b, kb_vec m )
{
	return _mm256_xor_si256( a, _mm256_and_si256( _mm256_xor_si256( a, b ), m ) );
}
static inline kb_vec kb_first( kb_vec a, int x )
{
	return _mm256_blend_epi32( a, kb_set1( x ), 1 );
}
static inline kb_vec kb_shift1( kb_vec v, kb_vec fill )
{
	return _mm256_blend_epi32( _mm256_permutevar8x32_epi32( v, _mm256_setr_epi32( 0,0,1,2,3,4,5,6 ) ), fill, 1 );
}
static inline kb_vec kb_shift2( kb_vec v, kb_vec fill )
{
	return _mm256_blend_epi32( _mm256_permutevar8x32_epi32( v, _mm256_setr_epi32( 0,0,0,1,2,3,4,5 ) ), fill, 3 );
}
static inline kb_vec kb_shift4( kb_vec v, kb_vec fill )
{
	return _mm256_permute2x128_si256( fill, v, 0x20 );
}
static inline int kb_reduce_max( kb_vec v )
{
	__m128i h = _mm_max_epi32( _mm256_castsi256_si128(v), _mm256_extracti128_si256(v,1) );
	h = _mm_max_epi32( h, _mm_shuffle_epi32(h,0x4e) );
	h = _mm_max_epi32( h, _mm_shuffle_epi32(h,0xb1) );
	return _mm_cvtsi128_si32(h);
}
static inline void kb_store_kind( unsigned char *p, kb_vec v )
{
	__m128i h = _mm_packus_epi32( _mm256_castsi256_si128(v), _mm256_extracti128_si256(v,1) );
	h = _mm_packus_epi16( h, _mm_setzero_si128() );
	_mm_storel_epi64( (__m128i *)p, h );
}
#elif MAFFT_AVX2_STRIPED == 4
#define KB_LANES 4
typedef __m128i kb_vec;
#define kb_set1 _mm_set1_epi32
#define kb_zero _mm_setzero_si128
#define kb_add _mm_add_epi32
#define kb_sub _mm_sub_epi32
#define kb_mul _mm_mullo_epi32
#define kb_max _mm_max_epi32
#define kb_gt _mm_cmpgt_epi32
#define kb_eq _mm_cmpeq_epi32
#define kb_and _mm_and_si128
#define kb_or _mm_or_si128
#define kb_load(p) _mm_load_si128( (const __m128i *)(p) )
#define kb_store(p,v) _mm_store_si128( (__m128i *)(p), (v) )
#define kb_maskbits(v) ((unsigned)_mm_movemask_ps( _mm_castsi128_ps(v) ))
static inline kb_vec kb_select( kb_vec a, kb_vec b, kb_vec m ) { return _mm_blendv_epi8(a,b,m); }
static inline kb_vec kb_first( kb_vec a, int x ) { return _mm_blend_epi16(a,kb_set1(x),3); }
static inline kb_vec kb_shift1( kb_vec v, kb_vec fill ) { return _mm_alignr_epi8(v,fill,12); }
static inline kb_vec kb_shift2( kb_vec v, kb_vec fill ) { return _mm_alignr_epi8(v,fill,8); }
static inline int kb_reduce_max( kb_vec v )
{
	v = kb_max( v, _mm_shuffle_epi32(v,0x4e) );
	v = kb_max( v, _mm_shuffle_epi32(v,0xb1) );
	return _mm_cvtsi128_si32(v);
}
static inline void kb_store_kind( unsigned char *p, kb_vec v )
{
	__m128i h = _mm_packus_epi32(v,v);
	int x = _mm_cvtsi128_si32( _mm_packus_epi16(h,h) );
	memcpy(p,&x,sizeof(x));
}
#else
#error MAFFT_AVX2_STRIPED must be 0, 4 or 8
#endif

#ifndef MAFFT_KB_EMBED
#define MAFFT_KB_EMBED 1
#endif
/* lfill_prepare bounds every stored score by +/-5e8, so multiplying by four
   leaves two tag bits without losing any integer information or overflowing.
   Arithmetic right shifts recover negative scores too. Keep byte/bitplane
   storage as an explicit experiment with MAFFT_KB_EMBED=0. */
#if MAFFT_KB_EMBED
#define KB_SCALE 4
#define KB_SCALAR(v) ((v) >> 2)
#if MAFFT_AVX2_STRIPED == 8
#define kb_encode(v) _mm256_slli_epi32((v),2)
#define kb_decode(v) _mm256_srai_epi32((v),2)
#else
#define kb_encode(v) _mm_slli_epi32((v),2)
#define kb_decode(v) _mm_srai_epi32((v),2)
#endif
#else
#define KB_SCALE 1
#define KB_SCALAR(v) (v)
#define kb_encode(v) (v)
#define kb_decode(v) (v)
#endif
static void *kb_alloc( size_t n )
{
	char *raw = malloc(n + 64 + sizeof(void *)), *p;
	if( !raw ) ErrorExit("Cannot allocate local alignment memory.");
	p = (char *)(((uintptr_t)(raw + sizeof(void *)) + 63) & ~(uintptr_t)63);
	((void **)p)[-1] = raw;
	return p;
}
static void kb_free( void *p ) { if(p) free(((void **)p)[-1]); }
#ifndef MAFFT_KB_DEFER_END
#define MAFFT_KB_DEFER_END 1
#endif
#define KB_H 1
#define KB_V 2
#define KB_STOP 3
#ifndef MAFFT_KB_PACKED
#define MAFFT_KB_PACKED 0
#endif
#if MAFFT_KB_PACKED && MAFFT_KB_EMBED
#error Packed and embedded traceback are separate experiments
#endif
#if MAFFT_KB_PACKED
#define KB_KIND_BYTES ((3*KB_LANES+7)/8)
#else
#define KB_KIND_BYTES KB_LANES
#endif
static TLS unsigned char *kb_raw = NULL;
static TLS int *kb_rows = NULL;
static TLS size_t kb_cap = 0, kb_rcap = 0;
static TLS int kb_S, kb_ext, kb_v0;
#define KB_RSTRIDE ((size_t)(kb_S + 1) * KB_LANES)
#define KB_RV(r,c) KB_SCALAR(kb_rows[(size_t)(r)*KB_RSTRIDE + (size_t)((c)%kb_S)*KB_LANES + (c)/kb_S])
#if MAFFT_KB_EMBED
#define KB_KIND(i,c) (kb_rows[(size_t)(i)*KB_RSTRIDE + (size_t)((c)%kb_S+1)*KB_LANES + (c)/kb_S] & 3)
#elif MAFFT_KB_PACKED
static inline int kb_kind( int i, int c )
{
	unsigned flags=0;
	memcpy(&flags,kb_raw+((size_t)i*kb_S+c%kb_S)*KB_KIND_BYTES,KB_KIND_BYTES);
	flags >>= c/kb_S;
	return (flags & (1u<<(2*KB_LANES))) ? KB_STOP : (flags & (1u<<KB_LANES)) ? KB_V : (flags & 1u) ? KB_H : 0;
}
#define KB_KIND(i,c) kb_kind((i),(c))
#else
#define KB_KIND(i,c) (kb_raw[(size_t)(i)*kb_S*KB_LANES + (size_t)((c)%kb_S)*KB_LANES + (c)/kb_S])
#endif

#ifndef MAFFT_KB_ZERO_EXT
#define MAFFT_KB_ZERO_EXT 0
#endif
typedef struct
{
	int S, lgth2, ithr, pen, needwm;
	int *prev, *cur, *vm, *wmrow, *profrow;
#if !MAFFT_KB_EMBED
	unsigned char *kbrow;
#endif
	kb_vec lanebase, carry, ltn, vmax;
} kb_fill_row_state;

/* Inlining an explicit zero extension argument specializes the same recurrence:
   its affine column offsets, offset updates and score normalizations all vanish.
   The common body keeps strict traceback comparisons and storage identical. */
static inline __attribute__((always_inline)) void kb_fill_row( kb_fill_row_state *r, int ext )
{
	int s,S=r->S;
	const kb_vec ninf=kb_set1(INT_MIN), one=kb_set1(1), vpen=kb_set1(r->pen);
	const kb_vec vext=kb_set1(ext), vthr=kb_set1(r->ithr), vn=kb_set1(r->lgth2);
	kb_vec cext=kb_mul(r->lanebase,vext), e=ninf, carry=r->carry;
	kb_vec cextpen=kb_add(cext,vpen), vmax=ninf, pos=r->lanebase, ltn=ninf;
	for(s=0;s<S;s++)
	{
		kb_vec a=kb_decode(kb_load(r->prev+s*KB_LANES)), vmj=kb_load(r->vm+s*KB_LANES);
		kb_vec t=kb_sub(a,cext), m=kb_max(e,carry), g, wm, v, ch, cvt, clamp;
		e=kb_max(e,t);
		g=kb_add(m,cextpen);
		ch=kb_gt(g,a); wm=kb_max(a,g);
		g=kb_add(vmj,vpen); cvt=kb_gt(g,wm); wm=kb_max(wm,g);
		kb_store(r->vm+s*KB_LANES,kb_add(kb_max(a,vmj),vext));
		if(r->needwm) kb_store(r->wmrow+s*KB_LANES,wm);
		vmax=kb_max(vmax,kb_select(ninf,wm,kb_gt(vn,pos)));
		clamp=kb_gt(vthr,wm);
#if MAFFT_KB_PACKED
		{
			unsigned flags=kb_maskbits(ch) | (kb_maskbits(cvt)<<KB_LANES) | (kb_maskbits(clamp)<<(2*KB_LANES));
			memcpy(r->kbrow+s*KB_KIND_BYTES,&flags,KB_KIND_BYTES);
		}
#else
		kb_vec ij=kb_and(ch,kb_set1(KB_H));
		ij=kb_select(ij,kb_set1(KB_V),cvt);
		ij=kb_select(ij,kb_set1(KB_STOP),clamp);
#if !MAFFT_KB_EMBED
		kb_store_kind(r->kbrow+s*KB_LANES,ij);
#endif
#endif
		wm=kb_max(wm,vthr);
		v=kb_add(wm,kb_load(r->profrow+s*KB_LANES));
#if MAFFT_KB_EMBED
		kb_store(r->cur+(size_t)(s+1)*KB_LANES,kb_or(kb_encode(v),ij));
#else
		kb_store(r->cur+(size_t)(s+1)*KB_LANES,v);
#endif
		cext=kb_add(cext,vext); cextpen=kb_add(cextpen,vext);
		if(s<S-1) ltn=kb_max(ltn,kb_sub(v,cext));
		pos=kb_add(pos,one);
	}
	r->ltn=ltn; r->vmax=vmax;
}

static int Lfill_kb( double **amino_dynamicmtx, double **n_dynamicmtx, double scoreoffset,
                     char *s1, char *s2, int lgth1, int lgth2,
                     double *maxwmpt, int *endalipt, int *endaljpt )
{
	int i,c,k,l,s,S,ithr,nprof=0, *prof[256], *profbuf, *vm, *wmrow;
	int maxwm=INT_MIN, endali=0, endalj=0, endpend=0;
	int pen=penalty, ext=penalty_ex;
	unsigned char *u1=(unsigned char *)s1, *u2=(unsigned char *)s2, used[256];
	size_t rs, need, needkb;
	kb_vec lanebase, ltn;
	const kb_vec ninf=kb_set1(INT_MIN), one=kb_set1(1);
	const kb_vec vext=kb_set1(ext), vn=kb_set1(lgth2);
	if( !lfill_prepare(amino_dynamicmtx,n_dynamicmtx,scoreoffset,u1,u2,lgth1,lgth2,&ithr,used) ) return 0;
	S=kb_S=(lgth2+KB_LANES-1)/KB_LANES;
	rs=KB_RSTRIDE;
	need=(size_t)(lgth1+1)*rs;
	needkb=MAFFT_KB_EMBED?0:(size_t)(lgth1+1)*S*KB_KIND_BYTES;
	if( need > ((size_t)1<<26) ) return 0;
	if( need > kb_rcap ) { kb_free(kb_rows); kb_rows=kb_alloc(sizeof(int)*need); kb_rcap=need; }
	if( needkb > kb_cap ) { kb_free(kb_raw); kb_raw=kb_alloc(needkb); kb_cap=needkb; }
#ifdef MAFFT_POISON_TEST
	memset(kb_rows,0xa5,sizeof(int)*need);
#if !MAFFT_KB_EMBED
	memset(kb_raw,0xa5,needkb);
#endif
#endif
	for(c=0;c<256;c++) { prof[c]=NULL; if(used[c]) nprof++; }
	profbuf=kb_alloc(sizeof(int)*nprof*S*KB_LANES);
	k=0;
	for(c=0;c<256;c++) if(used[c])
	{
		prof[c]=profbuf+(size_t)k*S*KB_LANES;
		for(s=0;s<S;s++) for(l=0;l<KB_LANES;l++)
		{
			int j=l*S+s+1;
			prof[c][s*KB_LANES+l]=(j<lgth2)?(int)amino_dynamicmtx[c][u2[j]]:0;
		}
		k++;
	}
	vm=kb_alloc(sizeof(int)*S*KB_LANES*2);
	wmrow=vm+S*KB_LANES;
	for(s=0;s<S;s++) for(l=0;l<KB_LANES;l++)
	{
		int cc=l*S+s;
		kb_rows[s*KB_LANES+l]=(cc<lgth2)?KB_SCALE*(int)amino_dynamicmtx[u1[0]][u2[cc]]:0;
	}
	for(s=0;s<S;s++) kb_store(vm+s*KB_LANES,kb_decode(kb_load(kb_rows+s*KB_LANES)));
	kb_v0=KB_SCALAR(kb_rows[0]); kb_ext=ext;
	{
		int lanes[KB_LANES] __attribute__((aligned(32)));
		for(l=0;l<KB_LANES;l++) lanes[l]=l*S;
		lanebase=kb_load(lanes);
	}
	{
		kb_vec cext=kb_mul(lanebase,vext);
		ltn=ninf;
		for(s=0;s<S;s++)
		{
			if(s>0) ltn=kb_max(ltn,kb_sub(kb_decode(kb_load(kb_rows+s*KB_LANES)),cext));
			cext=kb_add(cext,vext);
		}
	}
	for(i=1;i<=lgth1;i++)
	{
		int needwm = !MAFFT_KB_DEFER_END || maxwm <= ithr;
		int *prev=kb_rows+(size_t)(i-1)*rs, *cur=kb_rows+(size_t)i*rs;
		int *profrow=(i<lgth1)?prof[u1[i]]:prof[u1[0]];
#if !MAFFT_KB_EMBED
		unsigned char *kbrow=kb_raw+(size_t)i*S*KB_KIND_BYTES;
#endif
		int special=(int)amino_dynamicmtx[u2[0]][u1[i-1]], rowmax;
		kb_vec r0, cext, lt, carry, vmax, pos;
		r0=(i==1)?kb_first(kb_load(prev),special*KB_SCALE):kb_shift1(kb_load(prev+(size_t)S*KB_LANES),kb_set1(special*KB_SCALE));
		kb_store(prev,r0); r0=kb_decode(r0);
		cext=kb_mul(lanebase,vext);
		lt=kb_max(ltn,kb_sub(r0,cext));
		carry=kb_shift1(lt,ninf);
		carry=kb_max(carry,kb_shift1(carry,ninf));
		carry=kb_max(carry,kb_shift2(carry,ninf));
#if KB_LANES == 8
		carry=kb_max(carry,kb_shift4(carry,ninf));
#endif
		carry=kb_first(carry,special);
		{
			kb_fill_row_state state;
			state.S=S; state.lgth2=lgth2; state.ithr=ithr; state.pen=pen; state.needwm=needwm;
			state.prev=prev; state.cur=cur; state.vm=vm; state.wmrow=wmrow; state.profrow=profrow;
#if !MAFFT_KB_EMBED
			state.kbrow=kbrow;
#endif
			state.lanebase=lanebase; state.carry=carry;
#if MAFFT_KB_ZERO_EXT
			if( ext==0 ) kb_fill_row(&state,0);
			else
#endif
			kb_fill_row(&state,ext);
			ltn=state.ltn; vmax=state.vmax;
		}
		rowmax=kb_reduce_max(vmax);
		if(rowmax>maxwm)
		{
			unsigned any=0;
			int ln;
			maxwm=rowmax; endali=i;
			if(!needwm) { endpend=1; continue; }
			endpend=0;
			pos=lanebase;
			for(s=0;s<S;s++)
			{
				any |= kb_maskbits(kb_and(kb_gt(vn,pos),kb_eq(kb_load(wmrow+s*KB_LANES),kb_set1(rowmax))));
				pos=kb_add(pos,one);
			}
			ln=__builtin_ctz(any);
			for(s=0;wmrow[s*KB_LANES+ln]!=rowmax;s++) ;
			endalj=ln*S+s+1;
		}
	}
	if(endpend)
	{
		/* Once maxwm > ithr, clamped cells cannot equal it. Subtracting the same
		   profile from the stored shifted row recovers every unclamped wm exactly.
		   Search only the row that finally set the maximum, preserving the first
		   column in the lowest lane. Use shifted slots: vector 0 of the final row
		   has no successor to perform its boundary fixup. */
		int *r=kb_rows+(size_t)endali*rs;
		int *pr=(endali<lgth1)?prof[u1[endali]]:prof[u1[0]], ln;
		unsigned any=0;
		kb_vec pos=lanebase;
		for(s=0;s<S;s++)
		{
			kb_vec wm=kb_sub(kb_decode(kb_load(r+(size_t)(s+1)*KB_LANES)),kb_load(pr+s*KB_LANES));
			any |= kb_maskbits(kb_and(kb_gt(vn,pos),kb_eq(wm,kb_set1(maxwm))));
			pos=kb_add(pos,one);
		}
		ln=__builtin_ctz(any);
		for(s=0;KB_SCALAR(r[(s+1)*KB_LANES+ln])-pr[s*KB_LANES+ln]!=maxwm;s++) ;
		endalj=ln*S+s+1;
	}
	kb_free(profbuf); kb_free(vm);
	*maxwmpt=(double)maxwm; *endalipt=endali; *endaljpt=endalj;
	return 1;
}

#ifndef MAFFT_KB_SCORE_ONLY
#define MAFFT_KB_SCORE_ONLY 1
#endif
#ifndef MAFFT_KB_SELF_SCORE
#define MAFFT_KB_SELF_SCORE 0
#endif
#if MAFFT_KB_SCORE_ONLY
/*
 * The score-only caller needs neither traceback markers nor an endpoint.  In
 * particular it need not retain any completed row, encode two marker bits in
 * every score, or search a row for the first occurrence of its maximum.
 *
 * row[s,l] holds D(i,l*S+s+1).  The next row reads D(i,c), so its first vector
 * comes from a lane shift of row[S-1] (with the asymmetric column-zero score in
 * lane zero), and its next vector comes from the previous row[s].  Loading that
 * vector before overwriting row[s] permits an in-place, single-row fill.
 * ltn contains max(D(i,c)-c*ext) for each lane's columns except its first one;
 * the shifted first vector completes these maxima before the lane prefix scan.
 * This is exactly the horizontal state used by Lfill_kb, without replay state.
 *
 * A vector maximum is accumulated across every row and reduced only at the end.
 * Its mask excludes padded columns before they can enter the maximum.  Padding
 * can propagate only towards higher columns, as in the traceback fill.
 * All storage is per-thread and O(lgth2), including the substitution profiles.
 */
static TLS int *kb_score_mem = NULL;
static TLS size_t kb_score_cap = 0;
static int Lscore_kb( double **amino_dynamicmtx, double **n_dynamicmtx, double scoreoffset,
                      char *s1, char *s2, int lgth1, int lgth2, double *maxwmpt )
{
	int i,c,k,l,s,S,ithr,nprof=0, *prof[256], *row, *vm;
	int pen=penalty, ext=penalty_ex;
	unsigned char *u1=(unsigned char *)s1, *u2=(unsigned char *)s2, used[256];
	size_t ns, need;
	kb_vec lanebase, ltn, vmax;
	const kb_vec ninf=kb_set1(INT_MIN), one=kb_set1(1);
	const kb_vec vpen=kb_set1(pen), vext=kb_set1(ext), vn=kb_set1(lgth2);
	if( !lfill_prepare(amino_dynamicmtx,n_dynamicmtx,scoreoffset,u1,u2,lgth1,lgth2,&ithr,used) ) return 0;
#if MAFFT_KB_SELF_SCORE
	/* With a common nonnegative diagonal M dominating every used substitution,
	   nonpositive gaps and threshold <= M, no path scores above min(n,m)*M.
	   For identical strings the ungapped identity path attains n*M exactly.
	   The public caller still falls back for zero, preserving signed-zero bits. */
	if( lgth1==lgth2 && pen<=0 && ext<=0 && !memcmp(u1,u2,lgth1) )
	{
		int self=(int)amino_dynamicmtx[u1[0]][u1[0]], valid=self>=0 && ithr<=self;
		for(c=0;c<256 && valid;c++) if(used[c])
		{
			if( amino_dynamicmtx[c][c] != self ) { valid=0; break; }
			for(k=0;k<256;k++) if(used[k] && amino_dynamicmtx[c][k]>self) { valid=0; break; }
		}
		if( valid ) { *maxwmpt=(double)((int64_t)self*lgth1); return 1; }
	}
#endif
	S=(lgth2+KB_LANES-1)/KB_LANES;
	ns=(size_t)S*KB_LANES;
	for(c=0;c<256;c++) { prof[c]=NULL; if(used[c]) nprof++; }
	need=(size_t)(nprof+2)*ns;
	if( need > kb_score_cap )
	{
		kb_free(kb_score_mem); kb_score_mem=kb_alloc(sizeof(int)*need); kb_score_cap=need;
	}
#ifdef MAFFT_POISON_TEST
	memset(kb_score_mem,0xa5,sizeof(int)*need);
#endif
	row=kb_score_mem; vm=row+ns;
	k=0;
	for(c=0;c<256;c++) if(used[c])
	{
		prof[c]=vm+(size_t)(k+1)*ns;
		for(s=0;s<S;s++) for(l=0;l<KB_LANES;l++)
		{
			int j=l*S+s+1;
			prof[c][s*KB_LANES+l]=(j<lgth2)?(int)amino_dynamicmtx[c][u2[j]]:0;
		}
		k++;
	}
	for(s=0;s<S;s++) for(l=0;l<KB_LANES;l++)
	{
		int cc=l*S+s;
		row[s*KB_LANES+l]=prof[u1[0]][s*KB_LANES+l];
		vm[s*KB_LANES+l]=(cc<lgth2)?(int)amino_dynamicmtx[u1[0]][u2[cc]]:0;
	}
	{
		int lanes[KB_LANES] __attribute__((aligned(32)));
		for(l=0;l<KB_LANES;l++) lanes[l]=l*S;
		lanebase=kb_load(lanes);
	}
	{
		kb_vec cext=kb_add(kb_mul(lanebase,vext),vext);
		ltn=ninf;
		for(s=0;s<S-1;s++)
		{
			ltn=kb_max(ltn,kb_sub(kb_load(row+s*KB_LANES),cext));
			cext=kb_add(cext,vext);
		}
	}
	vmax=ninf;
	for(i=1;i<=lgth1;i++)
	{
		int *profrow=(i<lgth1)?prof[u1[i]]:prof[u1[0]];
		int special=(int)amino_dynamicmtx[u2[0]][u1[i-1]];
		kb_vec a=kb_shift1(kb_load(row+(size_t)(S-1)*KB_LANES),kb_set1(special));
		kb_vec cext=kb_mul(lanebase,vext), e=ninf, lt, carry, cextpen, pos=lanebase;
		const kb_vec vthr=kb_set1(ithr);
		lt=kb_max(ltn,kb_sub(a,cext));
		carry=kb_shift1(lt,ninf);
		carry=kb_max(carry,kb_shift1(carry,ninf));
		carry=kb_max(carry,kb_shift2(carry,ninf));
#if KB_LANES == 8
		carry=kb_max(carry,kb_shift4(carry,ninf));
#endif
		carry=kb_first(carry,special);
		cextpen=kb_add(cext,vpen); ltn=ninf;
		for(s=0;s<S;s++)
		{
			kb_vec nexta=kb_load(row+s*KB_LANES), vmj=kb_load(vm+s*KB_LANES);
			kb_vec t=kb_sub(a,cext), m=kb_max(e,carry), wm, v;
			e=kb_max(e,t);
			wm=kb_max(a,kb_add(m,cextpen));
			wm=kb_max(wm,kb_add(vmj,vpen));
			kb_store(vm+s*KB_LANES,kb_add(kb_max(a,vmj),vext));
			vmax=kb_max(vmax,kb_select(ninf,wm,kb_gt(vn,pos)));
			v=kb_add(kb_max(wm,vthr),kb_load(profrow+s*KB_LANES));
			kb_store(row+s*KB_LANES,v);
			cext=kb_add(cext,vext); cextpen=kb_add(cextpen,vext);
			if(s<S-1) ltn=kb_max(ltn,kb_sub(v,cext));
			pos=kb_add(pos,one); a=nexta;
		}
	}
	*maxwmpt=(double)kb_reduce_max(vmax);
	return 1;
}
#endif
