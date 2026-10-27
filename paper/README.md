# Technical reports

Newest first. Paths beginning `round2/` inside the reports name files in the private
measurement archive on the Dikarya host (production inputs, alignments and trees); it is not
published. Terms are explained in the [README glossary](../README.md#glossary).

| report | version | what it covers |
|---|---|---|
| [round4-audit-fixes.md](round4-audit-fixes.md) | opt9 | harness resume fixes, public fixtures and `verify-against-stock.sh`, CI, ThreadSanitizer and stress audit of ordered refinement, the Phase 2 code fixes and their verification |
| [round3-ordered-refinement.md](round3-ordered-refinement.md) | dikarya5 | ordered multi-worker refinement: design, why it is exact, scope, latency results, evidence |
| [round2-performance.md](round2-performance.md) | dikarya4 | round-2 speedups on the Dikarya VM, the 100-input corpus, timing method and results |
| [round2-score-kernels.md](round2-score-kernels.md) | dikarya4 | the exact AVX2 score kernels: rolling score-only DP, identity shortcut, zero-extension specialization |
| [round2-refinement-reuse.md](round2-refinement-reuse.md) | dikarya4 | exact reuse of a terminal rejected-refinement suffix and its dependency argument |
| [round2-band-certificate.md](round2-band-certificate.md) | — | a candidate exact band certificate for the local DP (a derivation only: no implemented kernel or measured speedup) |
| [round2-phylogenetics.md](round2-phylogenetics.md) | dikarya4 study | threading reproducibility and its effect on fungal ITS trees (46 inputs) |
| [round2-methods.md](round2-methods.md) | dikarya4 study | experimental methods of round 2 |
| [broadwell-dikarya2.md](broadwell-dikarya2.md) | dikarya2 | exact speedups on the Dikarya AVX2 VM |
| [mafft-apple-silicon.pdf](mafft-apple-silicon.pdf) ([source](mafft-apple-silicon.tex)) | opt1–opt8 | methods, verification and what didn't work, including how opt7's per-architecture work was done by parallel agents and consolidated in opt8 |

Figures for round 2 are in [`figures/round2`](figures/round2). Release history is in the
[changelog](../CHANGELOG.md).
