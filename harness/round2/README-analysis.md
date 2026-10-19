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

## Verification

```sh
cd harness/round2
python3 -m unittest -v test_analysis
```

Fixtures verify shifted all-gap columns, reordered taxa, original-strand
coordinates, ambiguous duplicate labels, altered residue rejection, ambiguity
filters, explicit versus contingency pair counts, root-invariant splits,
resolved/unresolved quartets, exact known RF distances, duplicate-taxonomy
pruning, short edges and the conjunction of support thresholds. They run no
MAFFT or IQ-TREE searches.
