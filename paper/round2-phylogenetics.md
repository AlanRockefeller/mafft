# MAFFT threading and fungal ITS phylogenies

The completed study finds reproducibility and downstream topology effects, but
no established biological accuracy ranking among threading configurations.
Ordinary two-thread MAFFT varied across repeated commands on 25/46 production
inputs. Disabling threaded refinement was repeatable on all 46 inputs but
changed the one-thread result on 24. Keeping one refinement worker
(`--thread 2 --threadit 1`) matched every one-thread golden in 92/92 runs.

Five selected inputs received repeated default IQ-TREE searches and 15 support
analyses. Their ten supported A–B/A–C comparisons had **zero incompatible pairs
of strongly supported splits**. This does not imply that all changes were
small: some strongly supported splits disappeared from another raw tree, and
support filtering changed the resolution of deep relationships. In particular,
`zdku` lost a 100/100-supported 70|71 bipartition in the selected B and C trees.
The sparse independent protein-locus evidence does not determine which result
is more accurate. Isolated performance and scheduling recommendations remain
separate, pending measurements; this report does not nominate an eventual
release binary or change production settings.

## Workload, configurations and experimental units

All 46 inputs whose original production options used two threads were studied
with their recorded alignment strategy and direction flags. The original
`sample.tgz` archive was not found, but the recovered extracted inputs match all
100 hashes in the preserved production manifest. A 12-input pilot deliberately
included previously differing and matching cases, small and medium datasets,
divergent inputs, duplicate-rich inputs and a 1,042-taxon FFT-NS-2 control.
The remaining 34 inputs then completed the same five-repeat B/C experiment.

Each original FASTA SHA-256 is one file-level summary unit. These are selected
production inputs, not a random sample of fungal diversity. Files can share
sequences and taxa: `1msv`, `ghy3` and `zdku`, for example, are not independent
biological lineages. Alignment repeats, pairwise comparisons, search seeds,
bootstrap replicates and multiple loci are nested observations, not extra
biological datasets.

| Configuration | MAFFT flags | Candidate2 runs | Inputs variable across new repeats | Inputs with any golden-A difference | Minimum A-comparison homology F1 |
|---|---|---:|---|---:|---:|
| A | `--thread 1` | 58 | 0/12 repeated inputs; 34 single-run inputs | 0/46 | 1 |
| B | `--thread 2` | 230 | 25/46 | 25/46 (54.3%) | 0.958981 |
| C | `--thread 2 --threadit 0` | 230 | 0/46 | 24/46 (52.2%) | 0.965136 |
| F | `--thread 2 --threadit 1` | 92 | 0/46 | 0/46 | 1 |

These **610 alignment runs** comprise A2/B5/C5 on the 12 pilot inputs,
A1/B5/C5 on the remaining 34, and F2 on all 46. Every A output matched its
independently preserved single-thread golden in every byte. B produced some
A-identical runs on 12 of the 25 variable inputs; on the other 13 all five B
outputs differed from A. No direction adjustment changed in this experiment.
All C repeats matched one another within each input. F also matched both its
own repeats and the golden on every tested input. These are finite observations
under recorded conditions, not proofs for arbitrary inputs.

The median of the 46 file-level median B/A homology F1 values is 1.0; this
mixture of unchanged and changed inputs must not conceal the 25 changed cases.
C's corresponding median is 0.999989. The worst B/A comparison is `ghy3`
(21 taxa), whose least similar pair of B alignments has F1 0.945407. The lowest
C/A F1 is `zdku` (141 taxa, seven duplicate rows), followed by `vifv` at
0.970028. The broader study therefore contains meaningful homology differences,
even though many jobs are unchanged. Nominal Wilson intervals for 25/46 and
24/46 are 0.402–0.678 and 0.381–0.659; purposeful selection and taxon overlap
prevent interpreting these as calibrated population intervals for fungal ITS.

The [complete alignment summary](../../round2/all-alignment-analysis/summary.json)
and [per-file table](../../round2/all-alignment-analysis/datasets.tsv) retain
widths, gap summaries, hashes, homology metrics and within-configuration
comparisons. The five-replicate study detects more variable inputs than an
earlier single two-thread pairing, as expected from its additional opportunities
to observe nondeterminism.

The main biological experiment used the private `candidate2` build, whose
launcher still reports `v7.526-opt8-dikarya2`. Its
[source manifest](../../round2/candidate2-source.sha256.json),
[build flags](../../round2/candidate2-build-flags.txt) and immutable run records
identify it more precisely than that version string. It is distinct from the
frozen previous dikarya2 installation. Scientific-repeat runtimes have observed
or unknown contention flags and are descriptive; they do not establish clean
latency or throughput differences.

## Alignment and tree comparison methods

Taxon identity is the original FASTA record ordinal, with a reversible mapping
to the full original header, ungapped sequence and hash. Reordered alignment
outputs and MAFFT `_R_` markers are validated against original residues or their
reverse complement. Missing records, changed residue identities and ambiguous
duplicate-name matches fail validation. Residues retain their original record
and original forward-strand coordinate.

A homology assertion is a pair of these residue identities occupying the same
alignment column. For assertion sets R and C, F1 is
`2*|R∩C|/(|R|+|C|)`. Shared assertions are counted exactly from the contingency
of reference/candidate column assignments, `sum choose2(n[refcol,candcol])`,
without constructing a quadratic pair set. All-residue and A/C/G/T-only metrics
are separate. Exact column signatures use residue identities rather than column
indices; all-gap columns are excluded and singleton-only columns distinguished.
An F1 deficit is neither the fraction of residues moved nor an accuracy score.

Tree inference used **IQ-TREE 2.0.7**, executable SHA-256
`ce43dcccef855a109893a0ad1384a5c5926614e1e4d117f53c84d0a8b0931202`,
fixed nucleotide model `GTR+F+G4`, `--seqtype DNA`, one inference thread,
`--keep-ident`, `--mem 6G` and explicitly recorded seeds. Stable-ID alignments
use original record order; every inferred tree must retain exactly the full
original taxon set. Duplicate sequences are retained during inference.

Default searches use seed 73111 across alignments and seeds 73129/73141 on
one fixed A alignment per detailed input. Fast screening adds `--fast` and is
a separate method. Support searches add `-B 1000 --alrt 1000 --bnni` and are
another separate cohort, not annotations pasted onto the earlier default-search
trees. Their fresh searches can return different raw topologies. Complete
prepared commands, executed plans, executable hashes, alignments, resources
and Newicks remain available.

A logical request reuses an inference job only when original input hash,
normalized stable-ID alignment, seed, executable and method settings match.
Such reuse does not create an independent search. Full command templates,
including otherwise unrecognized options, distinguish incompatible inference
cohorts. Cross-normal/support split-presence checks normalize only the recorded
support-specific flags/metadata after validating each executed plan; all other
search settings must agree. Methods and recovery rules are documented in the
[analysis harness](../harness/round2/README-analysis.md).

Unrooted Robinson–Foulds distance counts the symmetric difference of nontrivial
splits. Normalized RF is `RF/[2*(retained_taxa-3)]`; it can have an odd numerator
when collapsed trees have different resolutions. Split incompatibility is
reported separately from a split merely missing in a less resolved tree.
Quartets include unresolved states explicitly. The 21-taxon `ghy3` comparison
uses all 5,985 quartets; detailed larger inputs use the same fixed-seed panel
of 10,000 uniform draws with replacement. Monte Carlo intervals concern this
sampling only, not biological replication or alignment uncertainty.

Views include raw trees, original edges of length <=1e-6 collapsed, and a
separately labeled sensitivity retaining the first ordinal per exact original
forward-strand duplicate sequence. Collapse filters apply to original edges
**before** pruning taxa and suppressing degree-two paths. Applying them after
pruning could incorrectly sum collapsed short edges or discard a surviving
strong projected split. Regression fixtures cover this ordering. Historical
analyses with the old order are preserved and do not supply the final distances.

A strong edge requires both SH-aLRT >=80 and UFBoot >=95, after original
short-edge collapse. Support-label order is checked against the IQ-TREE report.
These thresholds follow the
[IQ-TREE single-gene guidance](https://iqtree.github.io/doc/Frequently-Asked-Questions).
Support is conditional on each alignment and model. A threshold crossing,
especially near 94/95, is not independent evidence that a relationship became
true or false. No raw likelihoods from different alignments are ranked.

## Screening, repeated searches and topology variation

| Tree stage | Distinct input hashes | Logical requests | Separately executed jobs | Scope |
|---|---:|---:|---:|---|
| Earlier fast pilot | 11 | 154 | 68 | Repeated B/C alignments plus fixed-A seed controls |
| Broad fast screen | 46 | 138 | 90 | A, first B and first C at seed 73111 |
| Detailed default searches | 5 | 69 | 44 | Five B/C alignments, A references and three A search seeds |
| Detailed support searches | Same 5 | 15 | 15 | A, selected B and C for each input |
| Identical-alignment/same-seed reruns | 2 of those inputs | 2 additional reruns | 2 | Actual separate IQ-TREE processes |

Stages overlap in inputs and must not be added as independent datasets.
The detailed default cohort has A2/B5/C5 at the paired seed on four pilot
inputs, A1/B5/C5 on `zdku`, and two additional fixed-A seeds for each input.
The fifth input, `zdku`, was selected because it had the largest C/A alignment
change, before inspecting its detailed tree outcomes. For support, the selected
B replicates were `ghy3` r2, `1msv` r0, `26et` r4, `8q2l` r4 and `zdku` r1:
the largest duplicate-pruned, short-edge-collapsed B/A RF among the five earlier
default searches, with ties resolved by earliest replicate. This is informative
stress-case selection, not a prevalence estimate.

The corrected fast pilot found some A–B topology difference in 6/11 inputs
after duplicate pruning and original short-edge collapse. The median of each
file's median normalized RF was 0.054348. Fixed-A seed controls differed in
9/11 inputs, with a corresponding median 0.163043. Fast search therefore adds
substantial search variability. Its
[corrected results](../../round2/pilot-fast-tree-analysis-v2/summary.json)
are screening evidence rather than a causal estimate of alignment error.

The [completed broad screen](../../round2/broad-fast-tree-analysis/summary.json)
found raw A–B differences in 19/46 inputs and A–C differences in 24/46.
After original short-edge collapse and exact-duplicate pruning, those counts
were 18/46 and 23/46; maximum normalized RF was 0.372807 and 0.381579.
Every requested screen search completed. These are frequencies for the first
B/C alignment under `--fast`, not estimates of biological error or of the
frequency of all possible B outcomes.

The following **default-search** results use duplicate representatives after
original short-edge collapse. B ranges summarize five paired-seed comparisons;
C is repeatable and its five logical requests reuse one calculation.

| Input | Original / retained taxa | B/A nRF median / maximum | C/A nRF | Fixed-A seed nRF median / maximum | B/A median quartet disagreement | C/A quartet disagreement | Fixed-A median quartet disagreement |
|---|---:|---:|---:|---:|---:|---:|---:|
| `ghy3` | 21 / 21 | 0.05556 / 0.16667 | 0 | 0 / 0 | 0.04211 | 0 | 0 |
| `1msv` | 37 / 37 | 0.10294 / 0.16176 | 0.10294 | 0.01471 / 0.01471 | 0.03280 | 0.03280 | 0.00040 |
| `26et` | 128 / 117 | 0.14474 / 0.18421 | 0.16228 | 0.16228 / 0.19298 | 0.01580 | 0.00740 | 0.00620 |
| `8q2l` | 135 / 104 | 0.51485 / 0.54455 | 0.46535 | 0.45050 / 0.46535 | 0.06110 | 0.04590 | 0.12630 |
| `zdku` | 141 / 134 | 0.15267 / 0.26718 | 0.23664 | 0.11069 / 0.12595 | 0.02680 | 0.04820 | 0.01460 |

Within-B maximum normalized RF was respectively 0.16667, 0.13235, 0.21491,
0.54455 and 0.31679. These pairwise comparisons are dependent within each input.
The [full default-search summary](../../round2/normal-five-analysis/summary.json)
and [comparison table](../../round2/normal-five-analysis/comparisons.tsv)
retain all within- and between-configuration results.

`ghy3` and `1msv` show alignment-associated variation exceeding the observed
fixed-A search controls. `zdku` also exceeds those controls in the reported
B/C summaries, although its A searches vary. `26et` and especially `8q2l`
have substantial search variability, and the relative magnitudes depend on the
distance metric. Many changed small splits can produce high RF but limited
quartet disagreement. A paired random seed does not isolate alignment effects
completely: a changed alignment can alter a stochastic search trajectory.
Three seeds do not exhaust the possible tree-search variability.

To test actual inference repeatability separately, the same A alignment and
seed 73111 were run in new one-thread IQ-TREE processes for `1msv` and `8q2l`.
Both produced byte-identical Newicks and RF0 against their original searches.
These [two same-seed controls](../../round2/same-seed-tree-analysis/summary.json)
were genuinely rerun, not satisfied by queue reuse. They support finite
repeatability under identical conditions while leaving distinct-seed search
variability and untested inputs/models unresolved.

## Branch support: conflicts, threshold changes and lost resolution

All 15 support searches finished. The table uses all original taxa; each row
compares A with its selected B and C. Strong counts are numbers of retained
unrooted splits, not counts of independent biological relationships.

| Input | B replicate | Raw RF A–B / A–C | Strong splits A / B / C | Strong RF A–B / A–C | Incompatible strong split pairs A–B / A–C | Strong quartet disagreement A–B / A–C |
|---|---:|---:|---:|---:|---:|---:|
| `ghy3` | 2 | 6 / 0 | 9 / 9 / 9 | 0 / 0 | 0 / 0 | 0 / 0 |
| `1msv` | 0 | 10 / 6 | 13 / 13 / 14 | 0 / 1 | 0 / 0 | 0 / 0.0065 |
| `26et` | 4 | 68 / 60 | 25 / 24 / 26 | 3 / 1 | 0 / 0 | 0.0009 / 0.0005 |
| `8q2l` | 4 | 170 / 152 | 12 / 10 / 12 | 2 / 2 | 0 / 0 | 0.0042 / 0.2022 |
| `zdku` | 1 | 78 / 60 | 42 / 37 / 39 | 13 / 13 | 0 / 0 | 0.0488 / 0.2020 |

The [paired RF and quartet-resolution figure](../../round2/support-resolution-figures/support-resolution.svg)
places normalized RF beside the percentage of all evaluated quartets resolved
only in A, resolved only in B/C, or resolved incompatibly in both. It makes the
approximately 20% resolution differences visible despite small normalized RF.
The [underlying counts](../../round2/support-resolution-figures/support-resolution.tsv)
and [manifest](../../round2/support-resolution-figures/manifest.json) preserve
denominators, exact/sampled panels, thresholds and source hashes without
recomputing or resampling quartets.

There are zero resolved strong-quartet conflicts in all ten comparisons.
The nonzero strong quartet distances are differences between a resolved and
an unresolved quartet after filtering. They are not necessarily small: a
single deep split crossing the threshold can affect many quartets. Likewise,
a strongly supported A split can be absent from B's raw tree while B's
incompatible raw alternative fails the support threshold. **Zero strong–strong
conflicts does not imply identical topologies, identical certainty, or that
all missing strongly supported splits are trivial.**

The main examples make this distinction concrete:

* `ghy3` shares all nine strong splits across A/B/C. All nine also occur in
  each of its five B default-search trees. Its A–B raw RF6 and exact quartet
  disagreement 842/5,985 involve branches below the joint criterion.
* `1msv` A/B share all 13 strong splits. Their ten raw-only splits each have
  UFBoot <=51, so their differences are not strong–strong conflicts. C adds
  one strong classification on the raw-shared `t000030,t000035` bipartition:
  SH-aLRT/UFBoot changes from 98.1/90 in A to 97.6/95 in C.
* `26et` A/B strong RF3 consists entirely of three raw-shared bipartitions
  crossing the joint threshold. The Turbinellus `t000008,t000010` pair changes
  from 81.9/95 to 81.6/94; the `t000038,t000039,t000047` group from 81.2/97
  to 84.0/93; and the Ramaria `t000071,t000077,t000080,t000083,t000084`
  group from 95.7/94 to 98.8/98. The last contains exact duplicates
  `t000083,t000084`; pruning them leaves four unique sequences on that side
  but preserves RF3 and zero strong incompatibilities. Original labels and
  accessions remain in the machine-readable split records.
* `8q2l` A/B strong RF2 is also support classification on raw-shared splits.
  Its A/C comparison includes a much larger **54|81** bipartition, present in
  both raw trees, changing from 94.4/86 in A to 95.1/96 in C. This one
  resolution change contributes most of its 20.22% sampled strong-quartet
  disagreement; it is not evidence that 20.22% of taxa were misplaced.
* `zdku` contains both support-filtering differences and changed raw splits.
  Of 13 unique strong splits in A/B, six are raw-shared filtering differences
  and seven are absent from the other raw tree. For A/C the corresponding
  counts are eight and five; one raw-shared difference also crosses the
  original short-edge cutoff. A's **70|71 split, ID `370c41447962b7c6`, has
  100/100 support and is absent from the selected B and C raw support trees**.
  That is a substantive stability concern even without an opposing strong
  split. Independent evidence is insufficient to label A, B or C correct.

Tracking those source splits through the separate normal-search replicates
shows why a threshold-only explanation must not be applied to every case:

| Source split | Present in normal A seed trees | Present in five normal B trees | Present in normal C tree | Interpretation |
|---|---:|---:|---:|---|
| `zdku` A100/100 70\|71 split | 3/3 | 4/5 | 0/1 | Deep raw-topology loss in the selected B and in C, beyond its observed A-seed split stability |
| `8q2l` C95.1/96 54\|81 split | 3/3 | 5/5 | 1/1 | Raw topology retained; support-filtered resolution changes |

C's five identical alignment requests each reuse the one normal C inference;
0/5 or 5/5 logical requests would not represent five independent searches.
The [full split-stability table](../../round2/final-clade-stability/supported-split-stability.tsv)
includes stable-ID sides, complete original labels, source support, alignment
counts and unique-job counts; its
[summary](../../round2/final-clade-stability/summary.json) records the complete
cross-normal/support search signature and provenance.

Exact duplicate pruning leaves the nonzero strong RF values unchanged for
these cases; `zdku` remains RF13 for A/B and A/C at 134 retained taxa. For
`8q2l`, the strong A/C quartet sensitivity becomes 0.2521 at 104 retained
taxa. These changes are therefore not all explained by redundant duplicate-tip
resolutions. They remain model- and search-dependent ITS results.

Shared-split support deltas are retained in
[the support-change summary](../../round2/support-extended-changes/summary.json)
and [signed per-split table](../../round2/support-extended-changes/shared-split-support.tsv).
Across the ten comparisons, median SH-aLRT changes on shared nonzero splits
are all zero; median UFBoot changes are zero or +0.5. Individual changes can
be much larger. These medians do not describe missing splits, establish a
systematic support increase/decrease, or supply independent biological
replication. There is only one support-seed analysis per selected alignment;
bootstrap/search uncertainty near cutoffs has not been separately replicated.
Complete support comparisons are in
[the five-input support results](../../round2/support-extended-analysis/summary.json).

## Independent biological evidence

[Swenie et al. (2025)](https://doi.org/10.1002/ajb2.70054) studied
Cantharellales using 18S, ITS, 28S, tef1 and rpb2. The
[public archive](https://doi.org/10.6084/m9.figshare.25691193) includes separate
published rpb2 and tef1 RAxML trees. Those separate protein-coding loci are an
independent evidence route. Its ITS tree and ITS-containing concatenation are
not used as independent truth sets.

Accepted links require exact input-residue identity, reverse-complement
identity or documented exact contiguous overlap with a primary GenBank record,
plus a reviewed exact isolate/culture identifier matching a published tip.
Versionless accessions resolve only to a unique cached version; an explicit
version never silently falls back. Shared species names and similar ITS
sequences are insufficient. Archived files are checked against published MD5
values. Full taxon sets remain intact when querying induced quartets.

| Original `1msv` ID | ITS accession | Reviewed isolate/culture identifier | Published rpb2 tip |
|---|---|---|---|
| `t000008` | OR471183.1 | RAS538 SV2 | Sistotrema_coronilla_RAS538 |
| `t000010` | OR471177.1 | HHB10070 | Sistotrema_coronilla_HHB10070 |
| `t000027` | OR464478.1 | ATCC MYA-4209 | Burgellopsis_nivea_ATCCMYA4209 |
| `t000035` | OR471312.1 | CBS 132236 | Ceratobasidium_bulbillifaciens_CBS132236 |

RAS538's primary accession identifies SV2 as a genotype of the same specimen;
this does not establish a phased multilocus haplotype. The published rpb2
quartet separates the two S. coronilla specimens from the other pair.
Full-source branches labeled 98 and 100 imply that induced quartet and meet
the prespecified classical-bootstrap witness threshold >=95. Other path edges
have labels 51 and 17. All are retained in the audit; no fresh four-tip
bootstrap frequency is inferred by pruning.

The [completed current-candidate audit](../../round2/biological-current-trees-v3/queue-quartet-summary.json)
finds the same raw quartet in **all 20 logical requests using 15 actual search
jobs** for `1msv`: three fast-screen requests, 14 normal requests including
search controls, and three support requests. These are repeated checks of
**one matched quartet in one input**, not 20 biological confirmations. All
three support-filtered ITS trees leave that quartet unresolved, so agreement
of their raw topologies is not strong ITS support for the reference split.
The [per-request table](../../round2/biological-current-trees-v3/queue-quartet-congruence.tsv)
keeps inference methods, configurations, replicates, seeds and reuse separate.

The audited exact specimen counts are rpb2/tef1: `1msv` 4/3, `ghy3` 2/0 and
`zdku` 2/2. Only `1msv` supplies an assessable quartet. The two accepted
`zdku` links are OR471183 and OR471177; neither locus has enough matched tips
for an unrooted quartet. This sparse evidence does not validate the remaining
33 `1msv` taxa, local query OV2602, the deep `zdku` split or a complete species
tree. Nor does it favor one threading configuration.

Several tempting links remain excluded: Burgoa CBS131.38 is not the published
CBS320.75 specimen; Neoburgoa JL596-16/EZ4455 is not EZLF1256; Minimedusa
ATCC MYA-2933 differs from archived MYA2993; and the original
`flavorhizomorphae` TUMH64401/64402 vouchers are not demonstrated identical
to the reference type. Possible transcription errors are not silently fixed.
Nominal species or genus monophyly is never scored as truth, and ITS gene trees
need not recover every named species as monophyletic.

A second primary reference,
[Zhou et al.'s Albatrellus study](https://doi.org/10.1080/21501203.2024.2386021),
was audited for `8q2l`. Its seven-locus tree includes ITS and is not independent
validation. The audit of 142 table rows and 21 cached GenBank records verifies
only two matching specimens with protein-coding loci, Dai21256 and Dai21257;
two tips cannot define an unrooted quartet. Both original ITS sequences have
exact contiguous primary-record matches. Some rpb1 records use *A. flettioides*
where ITS metadata/table use *A. fumosus*; these conflicts remain explicit.
Another LSU candidate reuses an ITS-containing accession and would require
annotated non-ITS extraction. TreeBASE attempts returned 404 and the queried
publisher Figshare record had no downloadable files. No independent topology
score is claimed. The [Albatrellus audit](../../round2/albatrellus-audit/audit.json)
preserves exact scope, voucher evidence and limitations.

## Separate RAxML-NG sensitivity check on `zdku`

The deep split loss motivated a separate **RAxML-NG 2.0.2** cohort on the
same three prepared MAFFT alignments: A r0, selected B r1 and C r0. Seven
searches used `GTR+F+G4`, one thread and one worker, four starting trees per
search (`pars{2},rand{2}`), and likelihood convergence epsilon 0.1. A used
seeds 88311, 88329 and 88341; B and C each used 88311 and 88329. All 141
original taxa were retained during inference. The
[experiment record](../../round2/raxml-zdku/experiment.json) records the
executable/version/hash, source alignments and settings. These searches are
kept separate from IQ-TREE results rather than pooled into that cohort.

The comparison below first collapses original edges <=1e-6, then retains the
134 exact original duplicate representatives. Quartet disagreement uses the
stored fixed panel of 10,000 draws. Each A–B/A–C comparison pairs the same
RAxML-NG seed across alignments.

| Comparison | Seed | Normalized RF | Sampled quartet disagreement |
|---|---:|---:|---:|
| A–B r1 | 88311 | 0.17557 | 0.0344 |
| A–B r1 | 88329 | 0.25191 | 0.1184 |
| A–C r0 | 88311 | 0.17939 | 0.0563 |
| A–C r0 | 88329 | 0.17176 | 0.1119 |
| Same A alignment, three seed pairs | 88311/88329/88341 | 0.03817–0.04962 | 0.0024–0.0687 |

Direct inspection of the seven raw best trees confirms that the **70|71
bipartition `370c41447962b7c6` occurs in all three A trees and is absent
from both B trees and both C trees**. Its 100/100 support refers to
the earlier IQ-TREE A analysis, not to these RAxML-NG trees: no RAxML-NG
bootstrap or branch-support analysis was performed. Representative raw
[A](../../round2/raxml-zdku/A-seed88311/inference.raxml.bestTree),
[B](../../round2/raxml-zdku/B-seed88311/inference.raxml.bestTree) and
[C](../../round2/raxml-zdku/C-seed88311/inference.raxml.bestTree) best trees
and the [complete comparisons](../../round2/raxml-zdku/comparisons.json)
are available for inspection. The
[deep-split audit](../../round2/raxml-zdku/deep-split-sensitivity.json)
records presence, branch lengths and hashes for every search. A reversibly
quoted [A tree with original labels](../../round2/raxml-zdku/A-seed88311/original-labels.nwk)
is also available, with corresponding readable trees in every B/C search directory.

Thus the selected alignments' deep-split difference and larger RF variation
are not confined to IQ-TREE's search implementation. All four paired
between-alignment nRF values exceed the observed fixed-A nRF range. Quartet
variability is less cleanly separated: the lower A–B/A–C quartet distances
overlap the range produced by different searches on the fixed A alignment.
Neither the finite within-A controls nor four starts exhaust search
uncertainty. This is **one selected input and three MAFFT alignments**, not
seven biological observations or independent biological truth. Cross-program
congruence of a sensitivity pattern does not establish which ITS alignment or
tree is more accurate; the independent voucher-matched loci remain too sparse
to adjudicate this split.

## Build checks, visualizations and practical interpretation

Frozen original opt8 controls used A once and B five times on `0uuv` and
`1msv`. Both produced five different B outputs, all different from their
matching A golden. These
[original-binary controls](../../round2/original-threading-control-analysis/summary.json)
confirm longstanding MAFFT behavior but cannot determine whether the fork
changes the distribution of nondeterministic outcomes. They are not two
additional independent datasets. Earlier frozen-dikarya2 quick trees are
[preserved separately](../../round2/quick-phylo-analysis-v2/) and are not pooled
with candidate2 tree replicates.

A later `dikarya3` build was separately checked on `1msv`, `26et`, `8q2l`
and `ghy3`, with A1/B5/C5/F5 per input: **64 additional alignments**. All four
A outputs and all 20 F outputs matched the frozen A goldens. B varied on all
four inputs, producing respectively 5, 3, 4 and 4 distinct outputs; C was
repeatable within each input and differed from A on all four. These
[later-build results](../../round2/final-threading-check-analysis/summary.json)
are a separate build cohort and add no new biological datasets or inferred
trees. The recorded launcher reports `v7.526-opt8-dikarya3`; this checked build
is not assumed to be the eventual release while further exact optimization
and performance validation proceed.

The [MAFFT threading documentation](https://mafft.ddbj.nig.ac.jp/alignment/software/multithreading.html)
recommends `--threadit 0` for reproducible refinement. Our A–C differences are
consistent with the source distinction: A enters a one-worker plus coordinator
refinement path, whereas C enters a separate serial implementation. F retains
the A-style refinement path while allowing earlier stages to use two threads.
Its byte equivalence is well supported on this workload, but no universal
reproducibility guarantee or isolated speed advantage follows from these tests.

[The final figure manifest](../../round2/final-figures/figure-manifest.json)
records 51 standard plot files in SVG/PDF/PNG and nine selected SVG/PDF
tanglegram comparisons, with all source hashes. Useful entry points are:

* [Alignment variability and observed repeatability](../../round2/final-figures/alignment-variation.svg).
* [Five-input default-search variation versus fixed-A seed controls](../../round2/final-figures/normal-five-analysis-de64bd78a1-9e5541-input_duplicate_representatives_zero_collapsed-search-controls.svg).
* [Strong support-filtered topology variation](../../round2/final-figures/support-extended-analysis-5a32cbeca4-29bed8-strong_support-tree-variation.svg).
* [Selected `zdku` A/B support-tree tanglegram](../../round2/final-figures/support-extended-analysis-5a32cbeca4-29bed8-tanglegram-02-zdku.fasta-B-r1.svg).
* [Selected `8q2l` A/B support-tree tanglegram](../../round2/final-figures/support-extended-analysis-5a32cbeca4-29bed8-tanglegram-01-8q2l.fasta-B-r4.svg).

Tanglegrams display selected raw-tree subtrees for readability, with support
annotations and a documented tip subset; red absence marks are not necessarily
strong conflicts or biological errors. All numerical comparisons retain the
full specified taxon set. The
[per-taxon quartet participation table](../../round2/final-figures/taxon-quartet-stability.tsv)
joins 2,659 rows from nine selected comparisons/views to original labels.
It is a stability diagnostic, not a taxon-placement error probability; four
tips participate in every quartet and resolved/unresolved changes count.
The [alignment/tree join](../../round2/final-figures/alignment-versus-tree.tsv)
contains 274 stage/view-specific points with zero provenance exclusions, not
274 independent biological inputs.

The [tree inspection bundle](../../round2/inspect-study-trees/manifest.json)
contains 151 distinct executed job paths across the 46 inputs, including the
broad screen, normal/support cohorts and actual same-seed reruns. Each export
contains original stable-ID Newick, reversibly quoted original-label Newick,
the analyzed alignment, IQ-TREE report and provenance. For example, the
[`1msv` A tree with original labels](../../round2/inspect-study-trees/1msv/pilot-normal-trees-1msv-A-r0-seed73111/original-labels.nwk)
is directly inspectable. Artifact links assume the preserved workspace's
sibling `round2` directory; large data and immutable results remain outside Git.

The completed evidence supports controlling reproducibility when downstream
analyses must be repeatable or must reproduce the established one-thread
alignment. It does not establish that A is biologically more accurate, that B
is systematically worse, or that C is an A-equivalent replacement. Some effects
involve weak branches or threshold crossings; others include loss of a deep,
strongly supported split and substantial changes in support-filtered resolution.
Independent evidence is too sparse to adjudicate those cases.

Limitations include selected overlapping inputs, only five B/C alignment draws,
three fixed-A search seeds, one IQ-TREE support seed per selected alignment, a
single fixed substitution model, and second-program sensitivity on only one
selected input without RAxML-NG bootstrap support. Support-aware disagreement
can reflect alignment, search and
support-estimation sensitivity together. Broader taxonomic/biological accuracy
claims would require suitable additional independent evidence. Choosing among
A, F and concurrent independent one-thread jobs for production still requires
the separately controlled latency, throughput, CPU and memory measurements.
