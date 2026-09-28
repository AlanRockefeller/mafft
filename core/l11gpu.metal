/*
 * Integer L__align11 fill + traceback for many pairs at once (see l11gpu.h / l11gpu.m).
 * One pair per 32-lane SIMD group; CMAX = columns held per lane, instantiated below.
 * Compiled offline into an embedded .metallib when the Metal compiler is available at build
 * time, otherwise from this source at run time.
 */
#include <metal_stdlib>
using namespace metal;
struct PairDesc { uint s1off; uint s2off; int l1; int l2; uint ijpoff; uint outoff; int pad0; int pad1; };
struct Params { int ncode; int pen; int ext; int thr; int gapc; int npairs; };
struct Result { int maxwm; int endi; int endj; int off1; int off2; int start; int status; int pad; };
static inline void comb( thread int &av, thread int &ak, int bv, int bk ) { if( bv > av ) { av = bv; ak = bk; } }
template <int CMAX>
kernel void l11( device const uchar *codes [[buffer(0)]], device const char *chars [[buffer(1)]],
                 device const int *mtx [[buffer(2)]], device const PairDesc *pd [[buffer(3)]],
                 constant Params &P [[buffer(4)]], device short *ijpbuf [[buffer(5)]],
                 device char *outbuf [[buffer(6)]], device Result *res [[buffer(7)]],
                 uint gid [[threadgroup_position_in_grid]], uint lane [[thread_index_in_simdgroup]] )
{
  PairDesc d = pd[gid];
  const int l1 = d.l1, l2 = d.l2, nc1 = P.ncode + 1;
  const int pen = P.pen, ext = P.ext, thr = P.thr;
  const int lstop = l1 + l2 + 1;
  const int C = ( l2 + 31 ) / 32;
  device const uchar *c1 = codes + d.s1off;
  device const uchar *c2 = codes + d.s2off;
  device short *ijp = ijpbuf + d.ijpoff;
  const int W = 32 * C;
  const int j0 = (int)lane * C + 1;
  int Pv[CMAX], VM[CMAX], PK[CMAX];
  int u10 = c1[0];
  for( int t=0; t<CMAX; t++ )
  {
    int j = j0 + t;
    int cj = ( t < C && j < l2 ) ? (int)c2[j] : P.ncode;
    int cjm = ( t < C && j <= l2 ) ? (int)c2[j-1] : P.ncode;
    Pv[t] = mtx[u10*nc1 + cj];
    VM[t] = mtx[u10*nc1 + cjm];
    PK[t] = cj;
  }
  int maxwm = INT_MIN, endi = 0, endj = 0;
  for( int i=1; i<=l1; i++ )
  {
    int prev0 = mtx[ (int)c2[0]*nc1 + (int)c1[i-1] ];
    int c1i = ( i < l1 ) ? (int)c1[i] : (int)c1[0];
    int last = 0, av = INT_MIN, ak = -1, a2v = INT_MIN, a2k = -1;
    for( int t=0; t<CMAX; t++ )
    {
      int j = j0 + t;
      if( t < C && j <= l2 )
      {
        comb( av, ak, Pv[t] - j*ext, j );
        if( t <= C-2 ) { a2v = av; a2k = ak; }
      }
      if( t == C-1 ) last = Pv[t];
    }
    /* inclusive scan of lane aggregates (earlier lanes first) */
    int sv = av, sk = ak;
    for( uint dd=1; dd<32; dd<<=1 )
    {
      int ov = simd_shuffle_up( sv, dd ), ok = simd_shuffle_up( sk, dd );
      if( lane >= dd ) { int nv = ov, nk = ok; comb( nv, nk, sv, sk ); sv = nv; sk = nk; }
    }
    int ev = simd_shuffle_up( sv, 1 ), ek = simd_shuffle_up( sk, 1 );
    { int tv = prev0, tk = 0; if( lane > 0 ) comb( tv, tk, ev, ek ); ev = tv; ek = tk; }
    /* incl up to this lane's second-to-last column, passed to the next lane */
    int s2v = ev, s2k = ek; comb( s2v, s2k, a2v, a2k );
    int Sv = simd_shuffle_up( s2v, 1 ), Sk = simd_shuffle_up( s2k, 1 );
    if( lane == 0 ) { Sv = prev0; Sk = 0; }
    int pl = simd_shuffle_up( last, 1 );
    if( lane == 0 ) pl = prev0;
    int Rv = ev, Rk = ek, o1 = pl, o2 = 0;
    int lmax = INT_MIN, lj = INT_MAX;
    device short *ijrow = ijp + i*W + lane;
    for( int t=0; t<CMAX; t++ )
    {
      int j = j0 + t;
      if( t < C && j <= l2 )
      {
        int hv, hk;
        if( t == 0 ) { hv = Sv; hk = Sk; }
        else if( t == 1 ) { hv = Rv; hk = Rk; }
        else { comb( Rv, Rk, o2 - ( j-2 )*ext, j-2 ); hv = Rv; hk = Rk; }
        int p = o1;
        int wm = p, ij = 0, g;
        g = hv + ( j-1 )*ext + pen;
        if( g > wm ) { wm = g; ij = -( j - hk ); }
        int vm = VM[t], vmp = PK[t] >> 8;
        g = vm + pen;
        if( g > wm ) { wm = g; ij = i - vmp; }
        if( p > vm ) { vm = p; vmp = i-1; }
        VM[t] = vm + ext;
        PK[t] = ( vmp << 8 ) | ( PK[t] & 255 );
        if( wm > lmax ) { lmax = wm; lj = j; }
        if( wm < thr ) { ij = lstop; wm = thr; }
        ijrow[t*32] = (short)ij;
        int old = Pv[t];
        Pv[t] = wm + mtx[ c1i*nc1 + ( PK[t] & 255 ) ];
        o2 = o1; o1 = old;
      }
    }
    int rmax = simd_max( lmax );
    int rj = simd_min( lmax == rmax ? lj : INT_MAX );
    if( rmax > maxwm ) { maxwm = rmax; endi = i; endj = rj; }
  }
  threadgroup_barrier( mem_flags::mem_device );
  if( lane != 0 ) return;
  device char *m1 = outbuf + d.outoff;
  device char *m2 = m1 + ( l1 + l2 + 1 );
  device const char *s1 = chars + d.s1off;
  device const char *s2 = chars + d.s2off;
  char gap = (char)P.gapc;
  int pos = l1 + l2;
  m1[pos] = 0; m2[pos] = 0;
  Result r; r.maxwm = maxwm; r.endi = endi; r.endj = endj; r.off1 = 0; r.off2 = 0; r.pad = 0;
  if( ijp[endi*W + ((endj-1)%C)*32 + (endj-1)/C] == lstop ) { r.status = 1; r.start = pos; res[gid] = r; return; }
  int iin = endi, jin = endj, ifi = 0, jfi = 0, limk = l1 + l2, status = 0;
  for( int k=0; k<=limk; k++ )
  {
    int v = ( iin <= 0 || jin <= 0 ) ? lstop : (int)ijp[iin*W + ((jin-1)%C)*32 + (jin-1)/C];
    if( v >= l1 + l2 ) { status = 2; break; }
    else if( v < 0 ) { ifi = iin-1; jfi = jin+v; }
    else if( v > 0 ) { ifi = iin-v; jfi = jin-1; }
    else { ifi = iin-1; jfi = jin-1; }
    int l = iin - ifi;
    while( --l > 0 ) { --pos; m1[pos] = s1[ifi+l]; m2[pos] = gap; k++; }
    l = jin - jfi;
    while( --l > 0 ) { --pos; m1[pos] = gap; m2[pos] = s2[jfi+l]; k++; }
    if( iin <= 0 || jin <= 0 ) break;
    --pos; m1[pos] = s1[ifi]; m2[pos] = s2[jfi];
    int nv = ( ifi <= 0 || jfi <= 0 ) ? lstop : (int)ijp[ifi*W + ((jfi-1)%C)*32 + (jfi-1)/C];
    if( nv == lstop ) break;
    k++;
    iin = ifi; jin = jfi;
  }
  r.off1 = ( ifi == -1 ) ? 0 : ifi;
  r.off2 = ( jfi == -1 ) ? 0 : jfi;
  r.start = pos; r.status = status;
  res[gid] = r;
}

template [[host_name("l11_8")]] kernel void l11<8>( device const uchar *, device const char *, device const int *, device const PairDesc *, constant Params &, device short *, device char *, device Result *, uint, uint );
template [[host_name("l11_12")]] kernel void l11<12>( device const uchar *, device const char *, device const int *, device const PairDesc *, constant Params &, device short *, device char *, device Result *, uint, uint );
template [[host_name("l11_16")]] kernel void l11<16>( device const uchar *, device const char *, device const int *, device const PairDesc *, constant Params &, device short *, device char *, device Result *, uint, uint );
template [[host_name("l11_20")]] kernel void l11<20>( device const uchar *, device const char *, device const int *, device const PairDesc *, constant Params &, device short *, device char *, device Result *, uint, uint );
template [[host_name("l11_24")]] kernel void l11<24>( device const uchar *, device const char *, device const int *, device const PairDesc *, constant Params &, device short *, device char *, device Result *, uint, uint );
template [[host_name("l11_28")]] kernel void l11<28>( device const uchar *, device const char *, device const int *, device const PairDesc *, constant Params &, device short *, device char *, device Result *, uint, uint );
template [[host_name("l11_32")]] kernel void l11<32>( device const uchar *, device const char *, device const int *, device const PairDesc *, constant Params &, device short *, device char *, device Result *, uint, uint );
template [[host_name("l11_40")]] kernel void l11<40>( device const uchar *, device const char *, device const int *, device const PairDesc *, constant Params &, device short *, device char *, device Result *, uint, uint );
template [[host_name("l11_48")]] kernel void l11<48>( device const uchar *, device const char *, device const int *, device const PairDesc *, constant Params &, device short *, device char *, device Result *, uint, uint );
template [[host_name("l11_64")]] kernel void l11<64>( device const uchar *, device const char *, device const int *, device const PairDesc *, constant Params &, device short *, device char *, device Result *, uint, uint );
