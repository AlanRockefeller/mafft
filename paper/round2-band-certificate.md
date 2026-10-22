# A candidate exact band certificate for MAFFT's local DP

This is a source-based mathematical derivation for a possible future kernel.
It has received two independent static reviews, but has no implemented banded
kernel, differential-test result or measured speedup. Dikarya4 does not use it.
The indexing below follows `L__align11` in `core/Lalign11.c` and the guarded
integer fills in `core/l11_avx2.h`.

## Preconditions and objective

Use actual character lengths $m,n$ and the existing finite, integral scoring
and intermediate-range guard. Require no warp, nonpositive gap-opening and
extension costs, and an integer $M>0$ bounding every substitution that can be
read. This includes the reversed matrix access in column-zero initialization;
assuming a symmetric matrix without checking it is insufficient.

Let $T$ be the local restart threshold, with $T\le M$. The existing recurrence
uses the same value as the strict-stop threshold and restart replacement. Obtain
a verified feasible lower bound $L>\max(T,0)$ on the **raw `maxwm` objective**.
A stored post-substitution cell value, an approximate score or a public score
clamped to zero is insufficient. Finding this bound cheaply is an unresolved
engineering requirement.

At interior loop coordinates $(i,j)$, raw `wm` represents consumed prefixes
$(i,j)$. The stored value

$$v(i,j)=\max(wm(i,j),T)+S(a_i,b_j)$$

represents prefixes $(i+1,j+1)$. Both have offset $i-j$. The terminal raw row and
column can supply endpoints; their fictitious post-substitution values must
never feed a new real endpoint.

## Pair-budget bound

Set $N=\min(m,n)$ and $D=m-n$. A monotone history passing through consumed-prefix
point $(x,y)$ can contain at most

$$Q(x,y)=\min(x,y)+\min(m-x,n-y)
 =N-\operatorname{dist}(x-y,[\min(0,D),\max(0,D)])$$

residue pairs, including its remaining possible continuation. Nonpositive gap
costs cannot increase its score beyond $M Q(x,y)$.

A positive restart needs care: $T+S(a_i,b_j)$ seems to include an extra score
without consuming a pair. Restarts occur only at interior $i,j\ge1$. Charge
$T\le M$ to a virtual pair at $(i-1,j-1)$ before the real restart pair. That pair
fits within the available consumed-prefix budget. Only the history after the
last strict restart contributes. Boundary seeds need no extra pair and must
retain their original substitution values. No additional axis restarts are
allowed by this argument.

Consequently the same bound covers **every completed active history** through
a point, including a currently low state that might later recover after
reentering a band.

## Strict exclusion and ties

Define

$$K=\lceil L/M\rceil,\qquad r=N-K.$$

Require $1\le K\le N$ and retain the closed offset interval

$$\boxed{\min(0,D)-r\ \le i-j\le\ \max(0,D)+r}.$$

Outside it, $Q\le K-1$, so every completed history using an omitted scored state
has score at most

$$M(K-1)<L.$$

The inequality is strict even when $L$ is divisible by $M$. Since a feasible
history achieves at least $L$, no winning or tied winning history, endpoint or
gap origin can depend on an omitted scored state. This addresses traceback
identity as well as the optimal score, provided the implementation preserves
all original comparison priorities.

In particular, retain diagonal before horizontal before vertical ties, the first
gap origin and the earliest row-major maximum. The stop test is `wm < T`;
`wm == T` continues. Boundary seeds and stop markers must retain their original
meaning. Changes wholly before a strict restart cannot influence its retained
history, but a band implementation cannot silently change where that restart
occurs.

## Implementation requirements and practical uncertainty

The derivation does not make ordinary SIMD masking safe. Omitted affine states
need valid negative-infinity semantics: adding a negative cost to `INT_MIN` can
overflow even when the existing finite-score range guard passes. Use guarded
or widened arithmetic and preserve all necessary gap carries. Compute ceiling
and band limits without integer overflow. Unsupported parameters must use the
existing complete recurrence; extending this argument to floating-point scores
would require rounding-safe bounds.

Striped SIMD layout also complicates realizing a saving. Masking inactive lanes
does not eliminate an instruction when other lanes remain live; repacking or a
different diagonal layout can introduce setup, carry and traceback costs.
Measuring omitted scalar cells alone would therefore overstate an attainable
speedup. A prototype needs an inexpensive feasible bound, actual scoring
matrices, adversarial ties/restarts, asymmetric boundary cases, all cell/offset
and full-traceback comparisons, sanitizers and paired whole-alignment tests.
No fraction of runtime saved is inferred here.
