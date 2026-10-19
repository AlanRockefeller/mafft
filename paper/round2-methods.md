# Round 2 experimental methods

All raw sequences, alignments, Newicks, profiles, downloaded reference data and run logs
are private sibling-directory artifacts under `../round2/`, never replacements for `../bench/`.
No production setting, installed production executable or system package is changed.
The frozen checkpoint is git `8894774` (dikarya2); original opt8 is
`322527b8f0b4b6b030cd4325a8aafe7c970a5764`.

## Exact optimization

The invariant is complete single-thread alignment stdout in every byte against original
production-compatible opt8 goldens, using the original per-file strategy, direction,
scoring and output-format options. Diagnostics can identify the new version; the CLUSTAL
alignment version remains opt8. GCC arithmetic is noncontracting, with no fast math,
reassociation or reduced precision. Internal differential tests compare public score return
bits, every DP score, gap offsets, endpoints, traceback strings and profile score cells as
applicable. Standalone differential tests are supplemented by original-double checks,
full alignments, alternate options, held-out inputs, boundary lengths and sanitizers.

Performance decisions require sequential interleaved measurements with alternating order.
Builds and correctness tests do not overlap timed measurements. Software perf samples
locate work but do not measure hardware stalls; hardware counters are unavailable on this VM.
CPU seconds corroborate wall times. Prior-corpus timing totals are historical context and
are never the denominator of a new incremental speedup. Per-file regressions remain visible.

## Threading and alignment comparison

A is `--thread 1`; B is `--thread 2`; C is `--thread 2 --threadit 0`.
F additionally tests `--thread 2 --threadit 1`. All keep the recorded production options.
The [official MAFFT threading documentation](https://mafft.ddbj.nig.ac.jp/alignment/software/multithreading.html)
recommends disabling threaded iterative refinement for reproducibility. This recommendation
does not imply byte identity with a one-worker refinement run. The fork passes A to a
one-worker/coordinator refinement implementation and C to a separate serial implementation.
F tests a single refinement worker while retaining parallel earlier stages.

Every original FASTA record receives a stable ordinal identifier, with a reversible mapping
to the complete original header and sequence SHA-256. Direction markers and actual reverse
complemented residues are validated; changed sequence identity, missing records and ambiguous
name matches fail. Original MAFFT commands retain original labels and order. IQ-TREE input
uses stable IDs. All inferred trees retain the complete original taxon set, including exact
duplicate sequences (`--keep-ident`).

A homology assertion is an unordered pair of original-record/residue-index identities placed
in the same alignment column. Shared assertions are counted exactly by a contingency table
of reference/candidate column assignments, with shared-pair count
`sum choose2(n[reference_column,candidate_column])`. This is linear in residue count and avoids
a quadratic set of residue pairs. Precision, recall and F1 use candidate and reference
assertion totals. Canonical A/C/G/T-only sensitivity and one original ordinal per exact
sequence sensitivity are reported separately. Direction changes are explicit.

An exact column signature is its set of (record ordinal, original forward residue index).
Nonmatching-column counts use multiset signature overlap, excluding all-gap columns;
column indices themselves are never compared. Homology columns contain at least two
residues. Singleton and all-gap shifts are distinguished from changed pairwise homology.
Alignment width, gap count/fraction/runs, terminal/internal gaps, per-row distributions,
variable and parsimony-informative sites are retained.

## Phylogenetics

The primary fixed model is GTR+F+G4, IQ-TREE 2.0.7, one inference thread, explicitly
recorded random seeds and complete taxon validation. Broad screening can use the documented
`--fast` ML search; normal search and support analyses are separate stages and are not pooled
as identical methods. Every comparison holds inference settings and seed fixed within stage.
Identical stable-ID alignments with identical settings and seed share a recorded computation;
different seeds on one fixed alignment are independent search controls, never silently reused.

Tree comparisons are unrooted. RF is the symmetric difference of nontrivial splits.
The primary normalization is `RF/[2(n-3)]`; a separate normalization by the total observed
split count handles polytomies transparently. Shared splits, incompatible splits and compatible
resolution differences are separate. Quartet states use unrooted four-point distances;
sampling has a fixed seed, with resolved/resolved conflicts distinguished from unresolved
quartets. Exact quartet enumeration is used when practical.

Raw trees, trees with edges <=1e-6 substitutions/site collapsed, and trees restricted to the
first ordinal per exact original ungapped sequence are compared. This duplicate sensitivity
prunes comparison views only; no taxon is removed from original inference. When support is
available, a strong edge must satisfy both SH-aLRT>=80 and UFBoot>=95, prespecified before
examining supported trees. Support label order is verified from the inference report.
Support jobs use 1,000 SH-aLRT replicates, 1,000 ultrafast bootstrap replicates and bootstrap
NNI correction. Support conditional on an alignment/model does not establish biological truth.

Raw likelihoods from different alignments are never ranked. Search controls use several
recorded seeds on the exact same alignment; between-alignment and within-alignment topology
variation are kept distinct. Distinct raw input hashes are the summary units; repeated runs
are nested observations. Inputs may overlap in taxa and fungal groups, so distinct files
are not automatically independent biological samples. Descriptive confidence intervals on
file-level frequencies do not remove this limitation.

Independent evidence requires exact accession/voucher matching and relationships supported
outside the tested ITS alignment. Sequence-header taxonomy or a published tree using the
same ITS evidence is insufficient. Reference matching, uncertainty, non-ITS gene provenance
and support limitations are documented explicitly.

## Scheduling

Isolated latency measurements allow both guest CPUs but run one alignment at a time.
Saturated queue replays compare one two-thread job, two simultaneous one-thread jobs (one
per pinned CPU), and selective stage threading, in the same seeded mixed-input order with
rotated policy order between rounds. All jobs arrive at time zero; this is a backlog replay,
not a claim to reproduce observed arrival timestamps. Service latency, queue delay and total
response latency are separate. Throughput is completed jobs divided by queue makespan.
GNU time records CPU and per-job peak RSS; process-group RSS is sampled for concurrent
aggregate memory and labelled as a sampled maximum. External shared-VM activity cannot be
fully controlled and no physical-host counter/frequency claim is made.
