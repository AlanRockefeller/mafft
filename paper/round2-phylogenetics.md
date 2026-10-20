# MAFFT threading and fungal ITS phylogenies: research in progress

This is an interim scientific report. The completed evidence comprises 610
alignment runs on all 46 original two-thread production inputs, an 11-input fast-search screen,
a four-input default-search study, a completed robust-support example, and accession/voucher
audits against primary non-ITS references. Broader tree screening, remaining support
analyses and production scheduling comparisons remain pending. This
report contains no new performance conclusion.

Two findings are already clear for this selected workload: ordinary two-thread
refinement can change inferred residue homologies across identical commands,
and disabling threaded refinement can give repeatable outputs that differ from
the one-thread baseline. The preliminary trees differ in topology, but the
current evidence does not show that any threading configuration is biologically
less accurate.

## Data and completed experiments

All 46 original two-thread inputs now have five B and five C alignments, at
least one verified A reference, and two F alignments. The 12 pilot inputs have
a second A run. All 58 A outputs match their frozen single-thread goldens.

| Configuration | Runs | Inputs variable across repeats | Inputs with any A difference | Minimum A-comparison homology F1 |
|---|---:|---:|---:|---:|
| A: `--thread 1` | 58 | 0/46 observed | 0/46 | 1 |
| B: `--thread 2` | 230 | 25/46 (54.3%) | 25/46 | 0.958981 |
| C: `--thread 2 --threadit 0` | 230 | 0/46 | 24/46 (52.2%) | 0.965136 |
| F: `--thread 2 --threadit 1` | 92 | 0/46 | 0/46 | 1 |

A has two actual runs on 12 inputs and one on the other 34, plus comparison to
the independently preserved golden; F has two runs per input. Neither received
five repeated runs. B produced some A-identical runs on 12 of the 25 variable
inputs; on the other 13, all five B outputs differed from A. No direction
adjustment changed in any comparison. The five-replicate study detects more
variable inputs than an earlier single two-thread pairing, as expected from the
additional opportunities to observe nondeterminism. These are finite observations
under the recorded build and execution conditions, not universal guarantees.

The full per-file table is `round2/all-alignment-analysis/datasets.tsv`, with
hashed inputs, complete commands, widths, gap summaries, exact residue-homology
metrics and within-configuration comparisons in the adjacent JSON files. The
median of the 46 input-level median B/A homology F1 values is 1.0, reflecting a
mixture of invariant inputs and variable cases. This median must not replace the
incidence counts or conceal the changed cases. C's corresponding median is
0.999989. The lowest C/A F1 occurs in `zdku` (141 taxa, seven duplicate rows),
followed by `vifv` at 0.970028. `zdku` also has the second-lowest B/A minimum,
0.974316, and minimum within-B F1 0.972211. It was selected for an additional
full-search and support follow-up based on these alignment results, before
inspecting its tree differences.

Descriptive Wilson intervals for 25/46 and 24/46 are 0.402–0.678 and
0.381–0.659. These count distinct input hashes; selected production jobs and
overlapping taxa are not a random sample of independent fungal lineages.
Scientific-repeat runtimes are descriptive and carry observed or unknown CPU
contention flags. Separate quiet-gated experiments determine performance.

The pilot was selected before examining these round-2 results. It includes
previously differing and matching two-thread cases, small and medium datasets,
divergent inputs, duplicate-rich inputs and a 1,042-taxon FFT-NS-2 control. Every
alignment retains its recorded production direction and strategy flags. Inputs
are fungal DNA from the supplied production/sample corpus; strategy is never
replaced with `--auto` for convenience.

The original `sample.tgz` archive was not located. The recovered extracted inputs
match all 100 hashes in the preserved production manifest, establishing continuity
with the previous real-data study.

Each original FASTA SHA-256 defines one file-level summary unit. There are 12
distinct hashes in the pilot, with 8–1,042 records per file. These are selected
files, not a random sample of fungal diversity. Some datasets share taxa and
sequence records, including the local query in `1msv` and `ghy3`; distinct input
hashes do not make their biological observations independent.

The completed alignment pilot used the private experimental `candidate2`
installation and two A, five B and five C runs per input:

| Configuration | MAFFT thread flags | Runs | Inputs with different outputs across repeats | Inputs with any output differing from A |
|---|---|---:|---:|---:|
| A | `--thread 1` | 24 | 0/12 | 0/12 |
| B | `--thread 2` | 60 | 7/12 | 7/12 |
| C | `--thread 2 --threadit 0` | 60 | 0/12 | 6/12 |

All 24 A outputs matched the frozen original single-thread goldens in every
byte. C was byte-repeatable in all five observed runs of each input. That is a
finite reproducibility test, not a proof for arbitrary future inputs. No pilot
run changed a sequence's direction relative to its A reference.

The executable at `../round2/install-candidate2/bin/mafft` still reports
`v7.526-opt8-dikarya2`; that version string alone cannot identify this
experimental build. Its source hash manifest is
`../round2/candidate2-source.sha256.json`, with compiler options in
`../round2/candidate2-build-flags.txt`. The authoritative run record is
`../round2/threading-pilot/experiment.json` plus `runs.jsonl`, including complete
MAFFT argument arrays and output hashes. The compile options select the new
score, profile-cache and six-mer prototypes; these should not be confused with
the separately frozen original dikarya2 installation.

## Alignment changes are measured by residue homology

Taxon identity is the original FASTA record ordinal. A reversible mapping keeps
the complete original header, sequence and hash. Reordered outputs and MAFFT
`_R_` direction markers are validated against the original residue sequence or
its reverse complement. Missing records, changed residue identities and
unresolvable duplicate-name matches fail validation. An aligned residue is
identified by its original record and original forward-strand coordinate.

A homology assertion is a pair of these residue identities occupying the same
alignment column. With reference assertions R and candidate assertions C,
homology F1 is `2*|R∩C|/(|R|+|C|)`. Precision and recall use the candidate and
reference pair totals, respectively. All-residue and A/C/G/T-only metrics are
separate. Neither denominator convention makes the reference alignment true.

Shared assertions are calculated exactly from a contingency table of reference
and candidate column assignments: `sum choose2(n[reference_column,candidate_column])`.
This avoids materializing a quadratic set of residue pairs. Exact column
signatures contain original residue identities and are compared without column
indices; all-gap columns are excluded, with singleton-only columns reported
separately. Width, row and column gap distributions, terminal/internal gaps and
changed-homology residues are retained in the machine-readable files.

| Input | Taxa | Exact duplicate rows | Distinct B outputs / 5 | B vs A F1, minimum–median | Lowest within-B F1 | C equals A? | C vs A F1 |
|---|---:|---:|---:|---:|---:|:---:|---:|
| `0uuv` | 143 | 6 | 5 | 0.996485–0.996857 | 0.995024 | No | 0.997719 |
| `1msv` | 37 | 0 | 5 | 0.990513–0.997496 | 0.989237 | No | 0.992966 |
| `26et` | 128 | 11 | 5 | 0.994507–0.997105 | 0.994083 | No | 0.994644 |
| `6y9j` | 8 | 0 | 1 | 1–1 | 1 | Yes | 1 |
| `7xqo` | 134 | 39 | 4 | 0.994014–0.997093 | 0.994016 | No | 0.983990 |
| `8q2l` | 135 | 31 | 5 | 0.995154–0.995158 | 0.995137 | No | 0.996886 |
| `buh4` | 1,042 | 228 | 1 | 1–1 | 1 | Yes | 1 |
| `c30k` | 134 | 30 | 1 | 1–1 | 1 | Yes | 1 |
| `fcx9` | 40 | 17 | 1 | 1–1 | 1 | Yes | 1 |
| `ghy3` | 21 | 0 | 5 | 0.958981–0.970119 | 0.945407 | No | 0.984524 |
| `gopv` | 159 | 15 | 1 | 1–1 | 1 | Yes | 1 |
| `khx6` | 42 | 8 | 2 | 0.998755–0.998755 | 0.998755 | Yes | 1 |

Duplicate rows are redundant original ungapped sequences after the first copy.
The within-B column summarizes all ten unordered pairs of the five runs per
input, including identical outputs with their observed multiplicity. Apart from
`buh4` (FFT-NS-2), these inputs use L-INS-i production settings.

The largest pilot difference occurs in the small divergent `ghy3` dataset:
its worst B-versus-A F1 is 0.958981 and its least similar pair of B runs has F1
0.945407. Those are changes in inferred residue relationships, rather than a
column-index comparison or an all-gap-column shift. They do **not** mean that
4.1% or 5.5% of residues moved, and they do not identify the more accurate
alignment. Across the 12 files, the median of file-level median B-versus-A F1
values is 0.998125; the corresponding C value is 0.998859. These summaries
conceal the heterogeneous cases, which is why the file-level table is retained.

The pilot frequencies 7/12 and 6/12 describe the selected workload. Descriptive
Wilson intervals in the raw summary do not correct for purposeful selection,
overlapping taxa or only five opportunities to observe B variability. They
should not be interpreted as population confidence intervals for fungal ITS
alignments generally.

The [MAFFT threading documentation](https://mafft.ddbj.nig.ac.jp/alignment/software/multithreading.html)
recommends `--threadit 0` for reproducible refinement. The observed A–C difference
is compatible with a source-level distinction: A enters a one-worker plus
coordinator refinement implementation, whereas C enters a separate serial
implementation. Configuration F (`--thread 2 --threadit 1`) tests retaining the
A-style refinement path while parallelizing earlier stages. Its one-run quick
checks matched A on three inputs. The completed broader F check now has **92/92
byte matches to the A golden**, comprising two runs on each of all **46** original
two-thread inputs. All F repeats match each other in this finite test. This is
the frozen candidate2 study binary; final dikarya3 checks and isolated throughput
measurements remain separate, pending evidence.

Frozen original opt8 controls ran A once and B five times on `0uuv` and `1msv`.
Each input produced **five different B outputs**, all different from its matching
A golden. These nested controls confirm longstanding MAFFT behavior; they are
not two additional independent biological datasets and do not imply equal
distributions of nondeterministic outcomes across builds.
The median B-versus-A homology F1 is 0.996996 for `0uuv` and 0.985317 for
`1msv`; the respective minima are 0.988897 and 0.976345. Within-B minima are
0.989205 and 0.975704. The separate `round2/original-threading-control-analysis`
preserves every comparison. This small control cannot establish whether the
fork increases or decreases the frequency or magnitude of nondeterminism.

## Broader topology screening

The completed 46-input fast-search screen has 138 logical A/B/C requests at
paired seed 73111 and 90 separately executed alignment/seed/method searches.
Repeated identical normalized alignments reuse a result within the queue. Each
input contributes its first B and first C alignment; the other B replicates
remain in the repeat-rich pilot and selected full-search cohorts.

| Comparison to A | Inputs with raw RF >0 | Inputs with RF >0 after exact-duplicate pruning and short-edge collapse | Maximum normalized RF in that sensitivity view |
|---|---:|---:|---:|
| First B alignment | 19/46 | 18/46 | 0.372807 |
| First C alignment | 24/46 | 23/46 | 0.381579 |

Every requested screen search completed. These are topology-screening frequencies
under IQ-TREE `--fast`, not estimates of biological error or a controlled causal
effect beyond search variability. They do not replace the three-seed A controls
and five-B-replicate full-search comparisons. `round2/broad-fast-tree-analysis`
retains all 46 file-level RF, incompatible-split and quartet comparisons.

## Search variability and a completed supported example

The 11-input fast-search pilot requested 154 trees, including additional A seeds,
and executed 68 distinct alignment/seed/method jobs. Identical stable-ID alignments
with identical seeds/settings reused the same calculation. With exact original
duplicate rows reduced to one representative and original edges <=1e-6 collapsed,
6/11 inputs had some A–B topology difference. The median of each file's median
normalized RF was 0.054348. Fixed-A search controls differed in 9/11 inputs,
with a corresponding median 0.163043. Fast search therefore introduces substantial
noise and is used for screening, rather than a causal estimate of alignment error.
Corrected evidence is in `round2/pilot-fast-tree-analysis-v2`.

The completed default-search cohort has **four** inputs, **56** logical requests
and **35** distinct inference jobs. Each input has A2/B5/C5 at seed 73111 plus A
at seeds 73129 and 73141. Inference retains every original taxon; the table uses
the sensitivity view that prunes exact duplicate rows and collapses original
short edges. Quartet values are exact for `ghy3` and a shared 10,000-draw fixed
panel for each larger input:

| Input | Original taxa | A–B nRF median / maximum | Fixed-A search-control nRF median / maximum | A–B median quartet disagreement | Fixed-A median quartet disagreement |
|---|---:|---:|---:|---:|---:|
| `ghy3` | 21 | 0.05556 / 0.16667 | 0 / 0 | 0.04211 | 0 |
| `1msv` | 37 | 0.10294 / 0.16176 | 0.01471 / 0.01471 | 0.03280 | 0.00040 |
| `26et` | 128 | 0.14474 / 0.18421 | 0.16228 / 0.19298 | 0.01580 | 0.00620 |
| `8q2l` | 135 | 0.51485 / 0.54455 | 0.45050 / 0.46535 | 0.06110 | 0.12630 |

Normalized RF divides by `2*(retained_taxa-3)` and can be odd in its numerator
when one tree has additional collapsed branches. RF disagreement is not itself
a count of incompatible strongly supported clades. In `ghy3` and `1msv`, the
observed alignment-associated variation exceeds these finite A search controls.
In `26et` and especially `8q2l`, search variability is substantial, and the
relative magnitude depends on the distance measure. A small quartet distance
can coexist with many changed splits in small tip groups. These deliberately
selected inputs do not estimate the prevalence of severe changes among all
production jobs. Detailed support for the three larger cases is still pending.
Full split/quartet records are in `round2/normal-extended-analysis`.

Edge collapse precedes duplicate-tip pruning. Collapsing after pruning could
incorrectly restore a path composed of multiple short edges, or discard a strong
projected split because an adjacent weak edge projects to the same split. Two
regression fixtures verify the corrected order. Historical analyses with the old
order are preserved but do not supply final duplicate-pruned distances.

The divergent 21-taxon `ghy3` case completed all A2/B5/C5 default searches plus
two additional seeds on the same A alignment: 14 logical requests and nine
distinct inference jobs. Here all three A search seeds gave the same topology.
A–B normalized RF ranged up to 1/6, with median 1/18; within-B maximum was also
1/6. C recovered the A topology despite its changed alignment. This finite
example shows alignment-associated variation beyond the observed A search
controls, without proving that the searches exhaust every plausible topology.

One A, one C and the B replicate with the largest prespecified normal-search
distance then received `-B 1000 --alrt 1000 --bnni` support analyses. These are
separate support/search jobs, all using IQ-TREE 2.0.7, `GTR+F+G4`, one inference
thread and seed 73111:

| `ghy3` support comparison | Raw RF | Normalized RF | Exact quartet disagreement | Strong split sets |
|---|---:|---:|---:|---|
| A–B r2 | 6 | 0.166667 | 842/5,985 = 0.140685 | All nine shared; zero strong conflicts |
| A–C | 0 | 0 | 0/5,985 | All nine shared; zero strong conflicts |

Strong means both SH-aLRT >=80 and UFBoot >=95, after original short-edge collapse.
Every one of these nine strong splits also occurs in all five B normal-search
trees. Changed raw branches in this case have weak support by the prespecified
joint criterion. This does not establish that every possible two-thread change
is trivial. Selecting a maximally different B replicate makes this a stress case,
not an estimate of the prevalence of strong conflicts. Support remains conditional
on each analyzed alignment and model.

Among the 15 nonzero splits shared by A and B in this example, median SH-aLRT
and UFBoot changes are both zero, but individual changes range from -12.5 to
+89.5 and -12 to +6 percentage points, respectively. None crosses the joint
strong-support threshold. The large SH-aLRT change therefore does not identify
a newly strong conflicting clade. `round2/ghy3-support-changes` contains the
signed per-split changes and original labels; branches within one tree are not
independent biological replicates.

Evidence includes `round2/ghy3-support-preview`, `round2/ghy3-clade-stability` and
SVG/PDF tanglegrams with support annotations in `round2/ghy3-support-figures`.
All 21 original records remain in the inference; the full original labels and
stable IDs are exported alongside the readable plots. Tree runtimes during
unrelated production load are descriptive and are not used for isolated latency
or throughput recommendations.

## Preliminary phylogenetic example: 1msv

Three earlier quick alignments, made with frozen dikarya2 rather than the
candidate2 pilot batch, each received one default IQ-TREE 2.0.7 search with
`GTR+F+G4`, one inference thread and seed 73111. These are three actual inferred
trees on one 37-taxon input, with no branch-support replicates. They are a
preliminary illustration and are not pooled as additional candidate2 pilot
replicates.

IQ-TREE input rows are in original record order with stable ordinal labels.
`--keep-ident` retains every original taxon and the output label set is checked
exactly. Complete commands, executable SHA-256, named-alignment hashes and
Newicks are in `../round2/tree-pilot-quick/1msv.fasta/{A,B,C}/seed73111/`.

| Comparison | Homology F1 | Alignment widths | Nonmatching exact columns, reference/candidate | Unrooted RF | Normalized RF | Sampled quartet disagreement |
|---|---:|---:|---:|---:|---:|---:|
| A–B | 0.997026 | 1938 / 1938 | 83 / 83 | 2 | 0.029412 | 74/10,000 = 0.0074 |
| A–C | 0.992966 | 1938 / 1937 | 180 / 179 | 6 | 0.088235 | 324/10,000 = 0.0324 |
| B–C | 0.991860 | 1938 / 1937 | 208 / 207 | 8 | 0.117647 | 398/10,000 = 0.0398 |

RF counts the symmetric difference between nontrivial unrooted splits;
normalization is `RF/[2*(37-3)]`. All 37 original records are retained and this
input has no exact duplicate rows. Root placements therefore do not contribute
to the reported RF. Quartet sampling uses 10,000 independent uniform draws with
replacement and seed 20261019, using the same panel across comparisons. The
Monte Carlo 95% Wilson intervals are 0.00590–0.00928, 0.02911–0.03605 and
0.03614–0.04381, respectively. These intervals quantify sampling error for the
fixed inferred trees, not uncertainty across alignments or fungal datasets.

Trees and tanglegrams show that alignment-associated differences can accompany
different ML topologies. A fixed random seed does not eliminate search
variability as a competing explanation: changing an alignment can change a
stochastic search trajectory even with the same seed. No fixed-alignment
multi-seed controls or robust support calculations have yet been completed in
this initial example. Consequently, these distances cannot establish that the
threading effect exceeds ordinary tree-search variability, nor whether the
changed branches are well supported.

The numerical summaries and SVG/PDF tanglegrams are in
`../round2/quick-phylo-analysis-v2/`. The figures are cladograms, with original
taxon labels and corresponding tips connected; their branch lengths are not to
scale. Full Newicks and split tables accompany them. Short-edge collapse
(`<=1e-6` substitutions/site) is a separately reported sensitivity, rather than
an assumed correction for topology differences.

## Independent biological evidence and its limits

[Swenie et al. (2025)](https://doi.org/10.1002/ajb2.70054) studied Cantharellales
using 18S, ITS, 28S, tef1 and rpb2. Its
[public archive](https://doi.org/10.6084/m9.figshare.25691193) includes separate
published rpb2 and tef1 RAxML trees. Those separate protein-coding loci provide
an independent evidence route; the ITS tree and five-locus concatenation that
includes ITS are not used as an independent truth set here.

We checked primary GenBank records for all 52 accession-bearing records across
`1msv` and `ghy3`. An accepted link requires the original input residues to match
the accession sequence exactly, in reverse complement, or by a documented exact
contiguous sequence overlap, and a reviewed exact isolate or culture identifier
to match the published non-ITS tip. Shared species names and similar ITS
sequences are insufficient. Archive files are checked against their published
MD5 values. GenBank XML, source downloads, source metadata and complete matching
tables are retained outside Git.

Four original `1msv` records have reviewed voucher links to the rpb2 tree:

| Original ID | ITS accession | GenBank isolate/culture identifier | Published rpb2 tip |
|---|---|---|---|
| `t000008` | `OR471183.1` | RAS538 SV2 | `Sistotrema_coronilla_RAS538` |
| `t000010` | `OR471177.1` | HHB10070 | `Sistotrema_coronilla_HHB10070` |
| `t000027` | `OR464478.1` | ATCC MYA-4209 | `Burgellopsis_nivea_ATCCMYA4209` |
| `t000035` | `OR471312.1` | CBS 132236 | `Ceratobasidium_bulbillifaciens_CBS132236` |

For RAS538, the primary accession explicitly marks SV2 as a genotype of the
same specimen. The mapping does not claim a phased multilocus haplotype. On
these four matched tips the published rpb2 topology separates the two
S. coronilla specimens from the Burgellopsis/Ceratobasidium pair. Full-tree
branches with bootstrap annotations 98 and 100 each imply this induced
quartet; a prespecified classical-bootstrap witness threshold of 95 is met.
Other full-tree edges along the pruned path have labels 51 and 17. All witness
values are retained; a fresh bootstrap frequency for the reduced four-tip tree
has not been estimated.

All three preliminary A/B/C ITS trees recover this same independently supported
quartet. This is one sparse congruence check. It does not distinguish the
configurations' biological accuracy, validate the remaining 33 taxa, or validate
the placement or identity of local query OV2602. Only three of these vouchers
occur in the tef1 tree, which is insufficient for an informative unrooted
quartet. `ghy3` has two reviewed rpb2 matches and none for tef1, also insufficient.

Several tempting matches were deliberately excluded from the evidence set:
the Burgoa type culture CBS 131.38 differs from the published CBS 320.75 sample;
Neoburgoa JL596-16/EZ4455 differs from the protein-locus tip EZLF1256; and the
Minimedusa GenBank culture ATCC MYA-2933 differs from the archived tip MYA2993.
The last may be a transcription error, but it was not silently corrected.
The `flavorhizomorphae` original vouchers TUMH 64401/64402 are not demonstrated
identical to the reference's type specimen. Nominal Sistotrema monophyly is
never scored as biological truth.

The completed audit is in `../round2/biological-audit-preview/` and is reproduced
by `harness/round2/biological_reference.py` from cached primary sources in
`../round2/biological-reference/`.

A second primary reference, [Zhou et al.'s Albatrellus study](https://doi.org/10.1080/21501203.2024.2386021),
was audited for the 135-taxon `8q2l` case. Its seven-locus concatenation includes
ITS and cannot itself provide independent validation. The accession/voucher audit
checks 142 table rows and 21 cached GenBank records. Only two original records
have verified matching specimens with protein-coding loci: Dai 21256 and Dai
21257. Both input sequences have exact contiguous matches to the primary ITS
records. Two specimens cannot define an unrooted quartet. Some rpb1 records use
the organism name *A. flettioides* where the ITS records and table say *A. fumosus*;
these conflicts remain explicit rather than silently corrected. Another table
row reuses an ITS-containing accession for LSU, requiring annotated non-ITS
extraction before independent use. TreeBASE downloads returned 404 and the
queried publisher Figshare record listed no downloadable files. No independent
non-ITS topology score is therefore claimed for this case. Cached sources,
download-attempt metadata and `round2/albatrellus-audit/audit.json` preserve the
scope and limitations; `harness/round2/audit_albatrellus.py` reproduces the audit.

## Prespecified next analyses

The prepared serial campaign separates an 11-input fast-search tree screen
(omitting the 1,042-taxon case initially), default-search analyses of three
informative inputs, and targeted branch-support analyses. It uses paired seed
73111 across alignments and additional seeds 73129/73141 on a fixed A alignment.
Reusing one computation for identical stable-ID alignments, seed and settings
is recorded explicitly and does not create independent search replicates.
Fast-search results and default-search results remain separate cohorts.

Tree comparisons report raw splits, short-edge collapse, a separately labeled
sensitivity retaining the first ordinal per exact original duplicate sequence,
shared versus incompatible splits, and quartet disagreement with unresolved
states represented explicitly. Full inference retains all original taxa. After
pruning duplicate representatives, surviving edges project onto the reduced
taxon set. Collapse filters are applied to original edges first; this is a
sensitivity view, not a re-estimation of bootstrap support on a new alignment.

Detailed support jobs use 1,000 ultrafast bootstrap replicates, 1,000 SH-aLRT
replicates and bootstrap NNI correction. A strong candidate edge must meet both
SH-aLRT >=80 and UFBoot >=95, with support-label order verified from the IQ-TREE
report. These thresholds follow the
[IQ-TREE single-gene guidance](https://iqtree.github.io/doc/Frequently-Asked-Questions).
Higher support alone does not establish greater accuracy. Selecting the B run
with the largest prior default-search distance for a detailed analysis makes
that subset informative-case sampling, not a prevalence estimate.

All repeated production-input alignments, F checks and original-opt8 controls
are complete. Broader feasible tree searches, the `zdku` follow-up and remaining
support analyses are queued as distinct stages. Their results must be added
before making workload-wide phylogenetic conclusions.
No raw likelihoods are used to rank different alignments, and no production
threading setting has been changed by this study.

The completed evidence supports controlling alignment reproducibility. It does
not yet support a claim that two-thread MAFFT is systematically less reliable
biologically, nor a production tradeoff between latency, throughput and tree
stability. The final recommendation requires the pending search controls,
branch supports, broader alignment repeats and separately measured scheduling
results.
