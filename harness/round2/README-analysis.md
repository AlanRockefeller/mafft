# Alignment and phylogenetic analysis

`analysis.py` and `tree.py` use Python's standard library for all identity,
alignment and topology calculations. Figure commands additionally use
Matplotlib. Inference uses an external, recorded IQ-TREE executable. Importing
either module runs no experiments. Existing outputs are never overwritten.

## Identity, orientation and duplicate records

The identity of a taxon is its ordinal in the original FASTA file. `t000001`
means original record 1; it is never assigned from output order or tree order.
The mapping JSON retains the complete original header, original ungapped
sequence, ordinal, sequence SHA-256, input SHA-256 and exact duplicate groups.
Case is ignored for residue identity; `-` and `.` are treated as gaps. T and U
are not silently interchanged. Other changes to residue content cause an error.

```sh
python3 harness/round2/analysis.py prepare \
  --input original.fasta --out stable.fasta --mapping taxa.json
```

Use this name-only transformation *before* alignment when original labels and
sequence contents are both duplicated. A post-hoc analysis cannot establish
which of two identical, identically named input records produced which gapped
row. Ambiguous identities therefore cause an error. Distinct full headers,
stable identifiers, or repeated headers disambiguated by their exact residue
contents are accepted. Unique first-token matching is permitted when tools
discard the rest of a header. Matching never relies on FASTA row order.

MAFFT's `_R_` prefix is recognized. Every sequence is validated against its
original forward sequence or its IUPAC reverse complement. Palindromic
sequences use the explicit prefix to preserve the reported strand decision.
Original labels that happen to begin with `_R_` are tested both literally and
as marked labels; an unresolvable collision causes an error. Each aligned
residue receives the identity `(original taxon ordinal, original FORWARD
strand residue position)`, even if its aligned row was reverse-complemented.
The tree input strips direction prefixes from the stable taxon label, retains
the actual aligned bases, and writes the strand decisions to `direction.json`.

No inference drops duplicate sequences: `--keep-ident` is explicit and the
resulting tree must have exactly the original stable taxon set. Duplicate
sensitivity analyses are separately labeled and operate on copies of inferred
trees, retaining the first input ordinal in each exact original ungapped
forward-sequence group. They do not modify the alignments or original trees.
Reverse-complement-equivalent sequences are not combined in this sensitivity.

## Alignment comparisons

```sh
python3 harness/round2/analysis.py compare \
  --input original.fasta --reference A.fasta --candidate B.fasta \
  --json A-B.json --duplicate-sensitivity
```

The result records file hashes, output order, orientation changes, width,
per-row gap counts/runs and terminal/internal gaps, a column-gap histogram,
all-gap and singleton columns, and counts of variable and parsimony-informative
columns using unambiguous A/C/G/T bases. These summaries make no assumptions
about the original taxonomic labels.

A homology assertion is an unordered pair of original residue identities
placed in the same column. Let `R` and `C` be the assertion sets implied by
reference and candidate alignments. The primary metrics are:

- Precision = `|R ∩ C| / |C|`.
- Recall = `|R ∩ C| / |R|`.
- F1 = `2 |R ∩ C| / (|R| + |C|)`.
- Jaccard = `|R ∩ C| / |R ∪ C|`.
- Lost/gained assertions = `|R \ C|` and `|C \ R|`.

If both sets are empty, these similarities are one. If exactly one is empty,
the relevant similarities are zero. Neither alignment is treated as biological
truth: reference is only the denominator convention. All-residue metrics and
A/C/G/T-only metrics are reported separately. The latter exclude a residue
containing any ambiguity code before forming pairs. Per-taxon counts identify
where homology relationships changed.

No quadratic set of residue pairs is materialized. For each original residue,
record its reference column `r` and candidate column `c`. If `N(r,c)` is the
number of residues with those two assignments, then
`|R ∩ C| = sum_(r,c) N(r,c)*(N(r,c)-1)/2`. Reference and candidate counts use
their respective column occupancy counts in the same formula. This requires
time and memory proportional to residue count, rather than taxa squared times
width. Tiny randomized fixtures compare it against explicit pair sets.

A column's exact signature is its set of original residue identities.
Nonempty signatures are compared as multisets, with a separate count restricted
to signatures containing at least two residues. This gives an explicit
definition of unmatched columns without comparing column indices. All-gap
columns and equivalent shifts of an entire aligned block do not reduce
homology similarity. Singleton-only rearrangements can change exact signatures
without changing any pairwise homology assertion. A global reversal of all
rows can preserve homologies while changing recorded strand decisions; these
are reported as separate outcomes.

## Reproducible tree jobs

The installed baseline is `/usr/bin/iqtree2`, IQ-TREE multicore 2.0.7.
`/usr/local/bin/iqtree3` is IQ-TREE 3.1.4. A study must consistently use one
recorded executable for its principal comparisons. The runner records its
version, executable SHA-256 and complete argument array before starting.

```sh
python3 harness/round2/tree.py run \
  --input original.fasta --alignment A.fasta \
  --out-dir trees/dataset/A/seed73111 --seed 73111 --timeout 1200
```

This validates sequence identities, writes `named.fasta` in original taxon
order, `taxa.json`, `direction.json` and `command.json`, then runs:

```text
iqtree2 -s named.fasta --seqtype DNA -m GTR+F+G4 -T 1 \
  --seed 73111 --keep-ident --mem 6G --prefix inference
```

The fixed model uses empirical nucleotide frequencies and four Gamma rate
categories. Default full ML search effort is retained. `--nstop` is available
only as an explicitly recorded study-wide setting; changing it within a
comparison would confound interpretation. `--plan-only` prepares a job without
searching; `tree.py execute --plan command.json` executes a prepared job exactly
once. A started or completed job cannot silently be rerun or resumed. Use a new
directory for every intentional independent run. Failed/time-limited jobs keep
their log, checkpoint and partial results and are marked incomplete.

`--lock /absolute/path/compute.lock` takes an advisory exclusive flock before
searching. This coordinates processes using that same lock; the external
scheduler must also prevent unrelated benchmark or build processes from
running simultaneously. GNU time records elapsed/user/system seconds and
maximum resident KiB for each search. A timeout terminates the process group,
including inference workers. Environment thread counts are explicitly one.

Use `--support` for the detailed subset. This adds `-B 1000 --alrt 1000
--bnni`; the final option applies additional NNI optimization to bootstrap trees.
The support command must be identical across compared alignments. The support
order must be verified from the `.iqtree` report, rather than assumed from slash
positions in a Newick label. The run is flagged invalid if that declaration is
missing when support was requested.

Official option/model references, checked 2026-10-19:
[IQ-TREE command reference](https://iqtree.github.io/doc/Command-Reference),
[tutorial](https://iqtree.github.io/doc/Tutorial), and
[support interpretation](https://iqtree.github.io/doc/Frequently-Asked-Questions).
Local `iqtree2 -hh` additionally confirms `--keep-ident`, `-B`, `--alrt`,
`--bnni`, `-T`, `--seed` and an optimization lower branch bound of `0.000001`.

## Tree comparisons and prespecified sensitivities

```sh
python3 harness/round2/tree.py compare \
  --reference trees/dataset/A/seed73111/inference.treefile \
  --candidate trees/dataset/B/seed73111/inference.treefile \
  --mapping trees/dataset/A/seed73111/taxa.json \
  --json A-B-trees.json --quartets 10000 --details
```

Both Newick trees must contain precisely the same unique labels. With a mapping,
that set must also equal the complete original ordinal-ID set. Spaces and
apostrophes in quoted Newick labels, comments, multifurcations and alternative
root placements are handled. Negative/nonfinite lengths or multiple trees in a
single-tree input are rejected.

Internal unrooted edges induce bipartitions. Pendant/trivial splits are
excluded, split/complement are represented canonically, and degree-two paths
(including a degree-two artificial root) are suppressed. Their lengths are
summed. If duplicate path annotations conflict, support is conservatively
the minimum of the available annotations. Raw RF is the number of splits in
exactly one tree. `normalized_rf` divides by `2*(n-3)`, the maximum RF for two
fully resolved unrooted trees; `split_normalized_rf` divides by the number of
retained splits across the two trees, which is useful for polytomies. A missing
split compatible with every split of the other tree is identified as a
resolution difference. Incompatible split pairs are counted explicitly.

The following views are reported independently:

1. All taxa and all nontrivial edges, regardless of support.
2. Edges of length **<= 1e-6 substitutions/site** collapsed, a prespecified
   sensitivity at IQ-TREE2's default minimum branch length.
3. One representative per exact original duplicate group, with and without the
   same short-edge collapse.
4. When both reports verify support order, short edges collapsed and only edges
   with **SH-aLRT >= 80 AND UFBoot >= 95** retained, on the full and duplicate
   representative taxon sets. Missing support is not treated as strong support.

The strong-support threshold follows the IQ-TREE single-gene guidance linked
above. It is an interpretation rule conditional on the input alignment/model,
not an accuracy certificate. An incompatibility between two retained splits is
a strongly supported conflict; high support on an incompatible topology does
not by itself establish biological correctness.

Quartet resolution is computed using the four-point test on integer topological
distances. Removed internal edges have weight zero, retaining unresolved
quartets explicitly. All quartets are enumerated if `choose(n,4) <= 10000`;
otherwise 10,000 independent uniform quartets are sampled **with replacement**
with seed **20261019**. The same deterministic taxon panel is used across
matching views. Results distinguish resolved conflicts from a resolution in
only one tree, give per-taxon quartet participation and discordance, and retain
example discordant quartets. The Wilson interval describes Monte Carlo error
for a fixed pair of trees; it is not a confidence interval across fungal
datasets. Exact enumeration reports no Monte Carlo error.

## Experimental units and tree-search controls

Keep configuration identifiers generic. Principal configurations are A
(`--thread 1`), B (`--thread 2`), C (`--thread 2 --threadit 0`); F
(`--thread 2 --threadit 1`) is a separately tested stage-specific control.
No configuration is presumed byte-equivalent or deterministic merely from its
name. Actual MAFFT commands retain each input's production strategy/direction
options and are recorded by `alignments.py`.

Use at least five B and five C alignment replicates per unique input SHA-256.
Where feasible run multiple A references and F replicates. Pilot tree searches
use explicit paired seeds on selected A/B/C alignments; representative fixed
alignments receive several distinct seeds, for example 73111, 73129, 73147,
73217 and 73259. Default identical model/settings/order are required.

Separate the following distances before summarizing each independent input:

- Within one fixed alignment across different search seeds: search variability.
- Different alignment replicates from one configuration with matched seed:
  alignment-associated variability.
- Across configurations with matched seed: configuration-associated variability.

Matching seeds does not imply identical stochastic paths after the alignment
changes. Search controls remain essential. Identical normalized stable-ID
alignments with the same seed/model/executable/options can reuse a recorded
inference, with an explicit provenance link; this is computational deduplication,
not an additional independent tree replicate.

Compute dataset-level medians/ranges of repeated distances before averaging
across datasets or resampling confidence intervals. Incidence intervals use the
number of unique input hashes, not alignment runs, tree seeds, quartets or
pairwise comparisons. A small convenience pilot is not a prevalence estimate
for all fungal ITS data. Raw likelihoods from alignments with different column
homologies or lengths are never directly used to rank those alignments.

Independent biological validation must map exact accessions/vouchers to
non-ITS loci or independently established clades. Species/generic names and
nominal species monophyly alone are not a truth set. Published trees containing
the same ITS sequences can be useful congruence checks but are not independent
validation. Gene-tree disagreement with a species tree also has biological
causes; it cannot automatically be attributed to threading.

## Figures and machine-readable tables

```sh
/path/to/round2/venv/bin/python harness/round2/tree.py visualize \
  --reference A.treefile --candidate B.treefile --mapping taxa.json \
  --svg A-B.svg --pdf A-B.pdf --selection-json shown-taxa.json

/path/to/round2/venv/bin/python harness/round2/tree.py collect \
  A-B-trees.json A-C-trees.json --tsv distances.tsv \
  --svg distance-distributions.svg --pdf distance-distributions.pdf
```

The tanglegram uses cladograms with explicit stable IDs plus original labels;
lengths are not to scale. Red edges denote displayed splits absent from the
other displayed tree. Larger trees show at most 48 tips, chosen from fixed-seed
discordant-quartet examples and then evenly spaced ordinal taxa; the caption
and optional JSON document that selection. `--taxa` provides an explicit comma
separated subset. All numerical comparisons remain on the full specified taxon
set. These diagrams are not rooted biological claims. Original Newick files and
all splits (`--details`) remain available for inspecting omitted taxa.

Install plotting dependencies in an isolated environment only:

```sh
python3 -m venv /path/to/round2/venv
/path/to/round2/venv/bin/python -m pip install -r harness/round2/requirements-figures.txt
```

## Figures and interpretable exports

`figures.py` builds publication SVG/PDF/PNG figures from completed
`summarize_study.py` and `analyze_trees.py` outputs:

```sh
/path/to/round2/venv/bin/python harness/round2/figures.py \
  --alignment-analysis /path/to/round2/all-alignment-analysis \
  --tree-analysis /path/to/round2/pilot-normal-tree-analysis \
                  /path/to/round2/support-tree-analysis \
  --data /path/to/bench/data --output /path/to/round2/science-figures \
  --formats svg pdf png --tanglegrams 3
```

Use `--dry-run` for a figure plan with no rendering or output creation. Each
analysis stage and compatible inference protocol remains separate, including
when two stages share a protocol. Input-level medians/ranges distinguish
between-alignment distances from fixed-alignment search controls. Ranges are
observed ranges rather than confidence intervals. The F1-versus-RF join checks
the exact alignment hashes used for inference and requires that its A reference
is the exact frozen A used in the alignment metric. Identical reused tree jobs
are represented by one scatter point with all logical replicate IDs retained
in the corresponding table. Missing or incompatible joins are explicit.

The default conflict export uses `strong_support`; it does not turn unsupported
raw edges into strongly supported edges. Complete conflicting split pairs are
reconstructed from `split_details` and checked against recorded conflict counts.
The export includes stable tip sets, four incompatibility quadrants, support
values, source trees and example original headers. `taxa-labels.tsv` keeps the
complete reversible ordinal/header/hash mapping. Quartet-witness counts for
different conflicting split pairs overlap and must not be summed as a distance.
An explicit `--max-conflict-pairs` limit records truncation. Raw conflicts can be
requested with `--conflict-view raw` and are labeled accordingly. The table
describes unrooted splits; it does not infer nominal-species monophyly or assign
biological importance to a large tip count. Selected tanglegrams show raw trees
and document their display subset and distance-based selection.

For those selected comparisons, `taxon-quartet-stability.tsv` joins the already
recorded per-tip quartet participation to stable IDs, original ordinals, original
headers and sequence hashes. It records the exact or sampled quartet panel,
per-tip sample counts and the fraction of participating quartets with different
resolutions. Differences include resolved/unresolved changes. This is a stability
diagnostic, not a placement-error probability; four tips participate in each
quartet and repeated observations are nested within their input. No quartets are
resampled while exporting. `selected-detailed-cases.tsv` documents selection.
Use `--tables-only` to export these tables, alignment/tree joins and split
conflicts without importing Matplotlib or producing new figures. The
`--tanglegrams` limit also controls the number of detailed cases in this mode.

## Voucher-validated non-ITS checks on queued trees

The original `biological_reference.py --trees-root ...` quick-tree interface is
unchanged. Completed current-candidate queues can be audited directly:

```sh
python3 harness/round2/biological_reference.py \
  --data /path/to/bench/data \
  --references /path/to/round2/biological-reference \
  --tree-queues /path/to/round2/pilot-normal-trees \
                /path/to/round2/support-trees \
  --output /path/to/round2/biological-current-candidate
```

`--tree-queues` accepts queue directories or their `runs.jsonl` files. An optional
`--queue-configs A B C F` filter otherwise defaults to all recorded configurations.
The audit verifies executed plans, the complete inference-method cohort, original
input hashes, named-alignment hashes and tree hashes. Every scored tree must
retain the full original stable-ID taxon set. Exact accession, sequence and
voucher matching remains mandatory; names alone never establish a link.

`queue-quartet-congruence.tsv`/`.json` report each configuration, alignment
replicate and search seed against the same induced non-ITS quartet panel.
`queue-quartet-summary.json` keeps stages and method cohorts separate and counts
logical requests, unique named alignments and actually used inference jobs
separately from unique biological input hashes. Reused alignments or tree jobs do
not become extra biological datasets. All matched quartets are used up to
`--max-reference-quartets` (default 10000); a fixed-seed uniform panel with
replacement is recorded above that limit. Fewer than four exact specimen matches
is explicitly unassessable. Full-source classical bootstrap >=95 is a reference
witness; candidate strong conflicts require verified SH-aLRT >=80 and UFBoot >=95
with edges <=1e-6 collapsed. An unresolved candidate is not counted as a resolved
conflict. Separate gene-tree congruence does not establish a complete true
species tree or validate unmatched local queries.

`audit_albatrellus.py --article article.xml --genbank voucher-genbank.xml
--input 8q2l.fasta --output NEW_DIRECTORY` reproduces the separate accession and
voucher audit of the cached primary Albatrellus article. It checks actual input
residues against cached GenBank sequences, keeps organism-name conflicts and
marks loci sharing the ITS accession. Its two matching protein-locus specimens
are insufficient for a quartet. The seven-locus published concatenation includes
ITS and is not treated as independent validation.

## Verification

Export an inspectable collection without changing inference trees:

```sh
python3 harness/round2/export_trees.py \
  --queues /path/to/normal-trees /path/to/support-trees \
  --files 1msv.fasta ghy3.fasta --data /path/to/bench/data \
  --output /path/to/NEW_TREE_EXPORT
```

Each logical request has the original stable-ID Newick, a separate Newick with
the full original header plus stable ID, named alignment and execution provenance.
Readable labels are quoted, and a reversed relabeling must preserve all splits,
branch lengths and support labels. Original FASTA files and reversible taxon maps
are included. Reused requests remain distinguishable from executed searches.

`clade_stability.py` follows splits supported by SH-aLRT >=80 and UFBoot >=95
across the complete normal-search alignment replicates and fixed-alignment
search seeds. It validates full taxon sets, source hashes and inference methods.
The TSV joins stable tip IDs to original headers and counts logical requests,
distinct named alignments and executed jobs separately. Presence in another tree
does not transfer the source tree's bootstrap support to that tree.

```sh
python3 harness/round2/clade_stability.py \
  --support-queues /path/to/support-trees /path/to/support-ghy3 \
  --normal-queues /path/to/pilot-normal-trees /path/to/normal-extra-ghy3 \
  --data /path/to/bench/data --output /path/to/final-clade-stability
```

```sh
cd harness/round2
python3 -m unittest -v test_analysis
python3 -m unittest -v test_queue test_execution test_science_exports
```

Fixtures verify shifted all-gap columns, reordered taxa, original-strand
coordinates, ambiguous duplicate labels, altered residue rejection, ambiguity
filters, explicit versus contingency pair counts, root-invariant splits,
resolved/unresolved quartets, exact known RF distances, duplicate-taxonomy
pruning, short edges and the conjunction of support thresholds. They run no
MAFFT or IQ-TREE searches. Export fixtures additionally verify complete original
taxon retention, non-bijective specimen rejection, strong reference conflicts,
named-alignment hashes, reused-job accounting and original-label quartet tables
with rendering blocked.

## Campaign recovery and method compatibility

`queue_trees.py --workers 2` optionally executes two independent IQ-TREE
processes. Each keeps `-T 1`, its original alignment, seed and inference options.
The immutable prepared plans and method cohorts do not change. Disjoint job
directories prevent competing writes; the established serial writer adopts
completed `run.json` files into the canonical logical-request log. An
`execution-*.json` records the outer concurrency and runner hash. `OUTPUT/PAUSE`
prevents new jobs at the next boundary; at most two searches finish in progress.
Default-search controls already completed serially are retained. Wall times from
overlapping tree searches are descriptive and are not alignment benchmarks.

IQ-TREE jobs use the same tested process-group timeout helper as alignment jobs:
SIGKILL also covers descendants after a GNU-time leader exits on SIGTERM.

`alignments.py` starts each measured command in its own process group. A timeout
signals the whole group with SIGTERM, then SIGKILL for any remaining descendants,
including when the GNU-time leader has already exited. The failed alignment,
stderr and run metadata are preserved. Missing GNU-time data are explicitly
unavailable, including CPU time; they are never replaced with zero. A failed
study cannot silently resume over its failed artifacts in the same directory.

`run_plan.py` retains the first `step.log` and writes retries as
`step.attempt-002.log`, including after an interruption before the ledger was
written. Completed accepted steps are not rerun. The default policy accepts
only exit code zero. A plan can explicitly permit a partial-result stage using
`"accepted_returncodes": [0, 1]` on that step; the actual nonzero exit remains
visible in `steps.jsonl` and final `STATUS.json`. This does not change the tree
queue's own nonzero exit for incomplete searches. Plan hashes are immutable, so
changing this policy requires a new campaign output directory.

`analyze_trees.py` checks each successful request against its prepared command
and verifies its recorded tree and original-input hashes. Compatible inference
cohorts require the same executable path/hash/version, model, inference thread
count, memory/search settings and branch-support settings. The full argument
template is part of the cohort identity, so a future unrecognized search flag
cannot be silently ignored. Only alignment path, output prefix and the recorded
random seed vary within a cohort. Distinct cohorts receive separate dataset
and aggregate summaries, identified by `protocol_id`; they are never pooled
merely because both used normal search or both used fast search. Missing A
searches after partial failures are explicitly recorded as excluded groups.

`same_seed_trees.py` verifies actual separately executed IQ-TREE searches on
the same alignment, model, executable, options and seed. It reports Newick byte
identity and topological equality separately. It rejects reused search records;
these checks supplement the distinct-seed search controls.

`support_changes.py --analysis SUPPORT_ANALYSIS --data INPUTS --output NEW_DIR`
reads the validated detailed tree comparisons and exports SH-aLRT and UFBoot
deltas for shared splits, including reversible original taxon labels. It keeps
joint support-threshold crossings separate from missing splits and incompatible
strong splits. The primary view retains all original taxa and collapses original
edges <=1e-6. Support deltas on shared branches cannot establish accuracy and
are not independent observations across branches within an input.

`observed_starts.py` reads matching dated alignment commands from existing
production logs without changing jobs. It requires the current input SHA-256,
binary path, thread count and options to match the frozen manifest. It records
excluded or changed inputs. The selected stage execution starts are neither
user submission times nor the entire production arrival stream. Log clock
timestamps have no specified timezone; only relative intervals are used.

`performance_figures.py` combines the completed paired analyses and validated
scheduling summary into three standalone figures, each in SVG/PDF/PNG, plus six
plotting tables and a hash manifest. It checks matching input/hash cohorts,
frozen goldens, manifests and candidate installations before plotting. Matplotlib
is imported only during rendering; use the isolated figure environment and run
after native timing finishes:

```sh
python3 harness/round2/performance_figures.py \
  --dikarya2-analysis /private/dikarya4-r2-performance-analysis \
  --opt8-analysis /private/dikarya4-r2-opt8-analysis \
  --scheduling-analysis /private/dikarya4-r2-scheduling-analysis \
  --output /private/new-performance-figures
```

The speedup panels separate production filenames from distinct input hashes.
The scheduling figure uses the full backlog; isolated latency uses only the
selected subset's two-repeat file means. Contention and exact-output counts
remain visible, and no repeat range or workload-mix interval is displayed as a
timing-noise confidence interval. Existing output directories are refused.

After isolated and saturated native measurements finish, `trace_replay.py`
projects fixed worker policies onto those observed start intervals. The simple
queue model separates service, waiting and response time and reports its error
against the measured saturated schedule. It checks build and input identities
and refuses contended native throughput summaries. Projections omit other
pipeline stages and resource users and must remain separate from measured
throughput and latency. Recorded historical runtimes are never used as paired
benchmark denominators.

`rejected_branch_audit.py` reads the verified historical paired-corpus stderr
traces and counts repeat rejections at the same accepted-alignment epoch. It
merges adjacent preliminary/final messages and resets state for each new
refinement invocation. Counts are opportunities, never CPU fractions or speedups.
No branch is skipped. The included fixtures cover accepted-change invalidation,
repeated FFT-NS-i refiner invocations and rejection of multiple-worker traces.

`refinement_semantics.py` compares optional experimental `REFINE_SEMANTIC` and
`REFINE_MASTER` traces from actual memo-off/on processes built from the same
source. It compares post-attempt score and gain bits and checks complete
original-order intermediate rows at every acceptance. For rejected attempts,
the traced score follows restoration of `tscore = mscore`; it is not the
discarded candidate objective. Missing traces, malformed row blocks,
different commands/inputs and wholly inactive cache guards cannot pass as an
active-reuse validation. Traced executions are correctness experiments and are
excluded from timing claims. The isolated `experiment/rejected-branch-memo`
branch preserves the default-off development snapshots. The guarded code is
now selected in dikarya4; its semantic-trace switch remains independently off
in the normal release build.

`reject_memo_scores_check.py --source SNAPSHOT/core/tditeration.c --output NEW_DIR`
extracts the actual experimental symmetry guard and tests finite matrices,
asymmetric values/zero signs, nonfinite values, null rows and seeded mutations.
It retains the full source/helper hashes, generated driver, compiler commands
and strict GCC/ASAN/UBSAN results. This verifies guard behavior, not whole
refinement equivalence; see the separate traced and full-output checks.

`support_resolution_fig.py --analysis SUPPORT_ANALYSIS --output NEW_DIR`
plots stored strong-split RF alongside quartet disagreement separated into
resolved only in A, resolved only in B/C, and opposing resolved quartets.
It exports the underlying TSV and figure/source hashes without recomputing
quartets or inferring trees. Deep resolution changes can affect many quartets
even when normalized RF is small. These are conditional stability metrics,
not error probabilities or independent biological replicates.

`raxml_sensitivity.py --queue NORMAL_QUEUE --data INPUTS --input FILE.fasta
--b-replicate N --output NEW_DIR --pilot` performs one RAxML-NG cost pilot.
Resume without `--pilot` for seven separately executed searches: three A seeds,
two selected-B seeds and two C seeds, each with two parsimony and two random
starts, `GTR+F+G4`, one thread/worker and likelihood epsilon 0.1. It reuses
validated stable-ID alignments and requires every original taxon in each tree.
Failed/timed-out jobs remain visible and are not silently resumed or replaced.
Results form a separate inference-method cohort; no raw cross-alignment
likelihood ranking, extra support estimate or biological accuracy claim follows.
The limited starting-tree search is a sensitivity check, not exhaustive search.

`idle_background.py --output NEW_DIR [--wait-for COMPLETION_MARKER ...]`
records a prespecified two-minute idle-service calibration. It rejects windows
with a busy scientific process or excessive background activity and keeps
waiting. A successful window recommends a prospective quiet threshold from its
p95 plus headroom, bounded at 0.20 CPU cores; it never changes the default gate
or reclassifies earlier measurements. Review the raw window and apply an
explicit threshold only in a new measurement plan. Busy scientific jobs remain
a hard blocker at 0.05 cores even if a different global threshold is selected.

`summarize_scheduling.py` validates and summarizes explicit final measurement
selections after all native work. It checks complete workloads, seeded queue
orders, recorded commands, input/golden/output/stderr/resource hashes, native
installation and frozen source identity. A/F outputs must match frozen A bytes.
Selection uses the prespecified **policy-level** aggregate contention check;
transient serial-job flags remain counted and visible. Concurrent workers'
individual residuals include the other worker and cannot attribute external
contention. Unknown serial attribution is rejected. The tool never selects by
speed or discards original attempts.

```sh
python3 harness/round2/summarize_scheduling.py \
  --full-selection ../round2/dikarya4-r2-throughput-selected.jsonl \
  --isolated-selection ../round2/dikarya4-r2-isolated-selected.jsonl \
  --pilot-selection ../round2/dikarya4-r2-throughput-pilot-selected.jsonl \
  --manifest ../bench/production-corpus.json --output /private/new-scheduling-summary
```

The new output contains full-policy, isolated-per-input/configuration and
two-round pilot TSVs, plus JSON provenance. Latency averages the two isolated
runs per selected input before giving each filename one vote; this 12-input
subset is separate from the full 100-job distribution. Full throughput retains
all production filenames, including the repeated raw inputs. Queue delay,
service latency, response latency, per-job GNU-time peak RSS and sampled
concurrent process-group RSS remain separate. B/C timing can accompany changed
alignments. Two-repeat stability and two-round ranges do not establish universal
determinism, a timing-noise confidence interval or biological accuracy.
