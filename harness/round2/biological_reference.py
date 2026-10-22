#!/usr/bin/env python3
"""Audit exact voucher links to cached published non-ITS Cantharellales trees.

No sequence alignment, phylogenetic inference, approximate name matching, or
nominal-species monophyly scoring occurs here. Input sources are cached outside
Git with their provenance. A few explicit links are reviewed below; every link
must pass both a GenBank residue-identity check and an exact voucher check.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import re
import xml.etree.ElementTree as ET

from analysis import make_mapping, read_fasta, reverse_complement, ungap, write_json, sha256_file
from analyze_trees import inference_protocol
from tree import read_tree

LINKS = {
    "OR471183.1": ("Sistotrema_coronilla_RAS538", "isolate", "RAS538 SV2", "RAS538; SV2 is a genotype suffix within that isolate"),
    "OR471177.1": ("Sistotrema_coronilla_HHB10070", "isolate", "HHB10070", "exact isolate identifier"),
    "OR464478.1": ("Burgellopsis_nivea_ATCCMYA4209", "culture_collection", "ATCC:MYA-4209", "exact culture collection identifier"),
    "OR471312.1": ("Ceratobasidium_bulbillifaciens_CBS132236", "culture_collection", "CBS:132236", "exact culture collection identifier"),
    "DQ267125.1": ("Sistotrema_confluens_AFTOL_613", "isolate", "AFTOL-ID 613", "exact AFTOL identifier"),
    "DQ218305.1": ("Hydnum_albomagnum_AFTOL_471", "isolate", "AFTOL-ID 471", "exact AFTOL identifier"),
}


def resolve_accession(header, records):
    """Resolve a leading accession only against unambiguous cached versions.

    A versionless header is not evidence for the latest database version.  It
    may identify the single archived version, which must still pass the exact
    residue and reviewed voucher checks below.  Explicit versions never fall
    back to a different version.
    """
    match = re.match(r"([A-Z]{1,4}_?[0-9]{5,}(?:\.[0-9]+)?)(?=$|[\s_|:;])", header)
    if not match:
        return None, None, "No leading accession in original header; no independently verified voucher link"
    token = match[1]
    if "." in token:
        if token in records:
            return token, token, None
        return token, None, "Explicit accession version is absent from the archived GenBank records"
    versions = sorted(key for key in records if key.rsplit(".", 1)[0] == token)
    if len(versions) != 1:
        return token, None, ("No archived accession version" if not versions else
                             "Multiple archived accession versions; versionless identity is ambiguous")
    return token, versions[0], None


def normalize_identifier(value):
    # Two documented conventions, not fuzzy name matching.
    value = re.sub(r" SV[0-9]+$", "", value.upper())
    value = value.replace("AFTOL-ID", "AFTOL")
    return re.sub(r"[^A-Z0-9]", "", value)


def source_qualifiers(record):
    result = {}
    for feature in record.findall(".//GBFeature"):
        if feature.findtext("GBFeature_key") == "source":
            for q in feature.findall(".//GBQualifier"):
                result.setdefault(q.findtext("GBQualifier_name"), []).append(q.findtext("GBQualifier_value"))
    return result


def sequence_identity(sequence, accession_sequence):
    rev = reverse_complement(sequence)
    if sequence == accession_sequence:
        return "exact forward"
    if rev == accession_sequence:
        return "exact reverse complement"
    if len(sequence) >= 200 and sequence in accession_sequence:
        return "input is exact contiguous substring of accession, forward"
    if len(sequence) >= 200 and rev in accession_sequence:
        return "input is exact contiguous substring of accession, reverse complement"
    if len(accession_sequence) >= 200 and accession_sequence in sequence:
        return "accession is exact contiguous substring of input, forward"
    return "NO EXACT IDENTITY"

def validated_queue_records(sources, originalmaps, queue_configs=None):
    """Read actual completed jobs; never infer identity from a directory name."""
    target={mapping['input_sha256']:mapping for mapping in originalmaps.values()}
    results=[];provenance=[];runs={};tree_hashes={};named_hashes={};seen={}
    logs=list(dict.fromkeys((Path(source)/'runs.jsonl' if Path(source).is_dir() else Path(source)).resolve() for source in sources))
    for log in logs:
        queue_path=log.parent/'queue.json'
        queue=json.loads(queue_path.read_text()) if queue_path.exists() else None
        stats={'runs_log':str(log),'runs_log_sha256':sha256_file(log),
               'queue':str(queue_path) if queue else None,'queue_sha256':sha256_file(queue_path) if queue else None,
               'failed_requests':0,'outside_target_datasets':0,'excluded_config_requests':0,'accepted_requests':0,'duplicate_log_records':0}
        for line in log.read_text().splitlines():
            row=json.loads(line)
            if not row.get('completed',False):stats['failed_requests']+=1;continue
            if row['input_sha256'] not in target:stats['outside_target_datasets']+=1;continue
            if queue_configs and row['config'] not in queue_configs:stats['excluded_config_requests']+=1;continue
            run_path=Path(row['run_json'] if row.get('run_json') else Path(row['directory'])/'run.json').resolve()
            if run_path not in runs:runs[run_path]=json.loads(run_path.read_text())
            run=runs[run_path];plan=run['plan']
            if not run.get('completed',False):raise ValueError('Queue claims completion for an incomplete inference: '+str(run_path))
            if queue is not None:
                if queue['jobs'][row['job_key']]!=plan:raise ValueError('Executed plan differs from the prepared queue job')
                effective_queue=queue
            else:
                # A standalone copy of runs.jsonl remains auditable via each run.json.
                command=plan['command']
                mem_option='--mem' if '--mem' in command else '-mem'
                mem=command[command.index(mem_option)+1]
                meta={k:plan.get(k,False if k in ['fast','support'] else None)
                      for k in ['binary_sha256','iqtree_version','model','threads','nstop','fast','support']}
                meta.update(iqtree=command[0],mem=mem)
                effective_queue={'metadata':meta,'jobs':{row['job_key']:plan}}
            protocol_id,protocol=inference_protocol(effective_queue,row)
            tree_path=str(Path(row['tree']).resolve())
            if tree_path not in tree_hashes:tree_hashes[tree_path]=sha256_file(tree_path)
            if tree_hashes[tree_path]!=row['tree_sha256'] or row['tree_sha256']!=run['tree_sha256']:
                raise ValueError('Changed or misattributed inferred tree: '+tree_path)
            named=Path(plan['directory'])/'named.fasta'
            if named not in named_hashes:named_hashes[named]=sha256_file(named)
            if named_hashes[named]!=row['named_alignment_sha256']:
                raise ValueError('Changed named alignment for biological comparison: '+str(named))
            observation=(str(run_path),row['config'],row['replicate'],row['seed'],row.get('reason','alignment_replicate'))
            signature=(row['named_alignment_sha256'],row['tree_sha256'])
            if observation in seen:
                if seen[observation]!=signature:raise ValueError('Conflicting duplicate inference request')
                stats['duplicate_log_records']+=1;continue
            seen[observation]=signature;stats['accepted_requests']+=1
            results.append(dict(row,tree=tree_path,run_json=str(run_path),protocol_id=protocol_id,
                                fast=protocol['fast'],support=protocol['support'],
                                protocol=protocol,source_runs_log=str(log),source_stage=str(log.parent)))
        provenance.append(stats)
    return results,provenance


def score_queue_quartets(matched, originalmaps, reference_trees, records,
                         max_quartets=10000, quartet_seed=20261019):
    """Full inference taxon sets stay intact; only induced quartets are queried."""
    allrows=[];trees={};dataset_summaries={};protocols={}
    for dataset,links in matched.items():
        mapping=originalmaps[dataset];expected={row['id'] for row in mapping['records']}
        original_headers={record['id']:record['original_header'] for record in mapping['records']}
        requests=[row for row in records if row['input_sha256']==mapping['input_sha256']]
        dataset_summaries[dataset]={}
        for locus,reference in reference_trees.items():
            linked=sorted([row for row in links if row['accepted_reference_tip'] in reference.taxa],key=lambda row:row['id'])
            if len({row['id'] for row in linked})!=len(linked) or len({row['accepted_reference_tip'] for row in linked})!=len(linked):
                raise ValueError('Non-bijective specimen mapping; explicitly review duplicate records before reference scoring')
            if len(linked)<4:
                dataset_summaries[dataset][locus]={'matched_specimens':len(linked),'assessable':False,
                    'limitation':'Fewer than four exactly matched specimens; requests are not counted as congruent or discordant.'}
                continue
            total=math.comb(len(linked),4);exact=total<=max_quartets
            if exact:panels=list(itertools.combinations(linked,4))
            else:
                rng=random.Random(quartet_seed)
                panels=[tuple(sorted(rng.sample(linked,4),key=lambda row:row['id'])) for _ in range(max_quartets)]
            source_depth=reference.weighted_depth(reference.taxa,set(reference.splits()))
            strong_source_masks=[]
            for index,node in enumerate(reference.nodes[1:],1):
                try:support=float(node.label)
                except ValueError:continue
                if math.isfinite(support) and 95<=support<=100:strong_source_masks.append(reference.masks[index])
            reference_panel=[]
            for quartet in panels:
                ids=tuple(row['id'] for row in quartet);tips=tuple(row['accepted_reference_tip'] for row in quartet)
                mask=sum(1<<reference.position[t] for t in tips)
                resolution=reference.quartet(tips,source_depth)
                witnessed=any((source & mask).bit_count()==2 for source in strong_source_masks)
                reference_panel.append((ids,tips,resolution,witnessed))
            group_rows=defaultdict(list)
            for request in requests:
                path=request['tree'];protocols[request['protocol_id']]=request['protocol']
                if path not in trees:trees[path]=read_tree(path)
                candidate=trees[path]
                if set(candidate.taxa)!=expected:raise ValueError('Biological audit tree does not retain the full original taxon set')
                raw_depth=candidate.weighted_depth(candidate.taxa,set(candidate.splits()))
                supported=set(candidate.support_order)=={'shalrt','ufboot'}
                if request['support'] and not supported:raise ValueError('Support labels cannot be validated for a supported biological comparison')
                strong_depth=None
                if supported:
                    kept={mask for mask,edge in candidate.splits().items()
                          if (edge.length is None or edge.length>1e-6) and edge.support.get('shalrt',-1)>=80 and edge.support.get('ufboot',-1)>=95}
                    strong_depth=candidate.weighted_depth(candidate.taxa,kept)
                request_rows=[]
                for number,(ids,tips,ref_resolution,witnessed) in enumerate(reference_panel):
                    resolution=candidate.quartet(ids,raw_depth)
                    strong_resolution=candidate.quartet(ids,strong_depth) if strong_depth is not None else None
                    row={k:request.get(k) for k in ['input','input_sha256','config','replicate','seed','reason','named_alignment_sha256',
                         'tree_sha256','reused','inference_counts_as_independent','source_runs_log','source_stage','run_json','protocol_id']}
                    row.update(dataset=dataset,locus=locus,tree=path,full_original_taxa=len(expected),quartet_index=number,
                               original_ids=ids,original_headers=[original_headers[t] for t in ids],
                               reference_tip_labels=tips,reference_resolution=ref_resolution,
                               reference_classical_bootstrap_ge95_witness=witnessed,candidate_resolution=resolution,
                               raw_congruent=(resolution==ref_resolution if ref_resolution is not None else None),
                               candidate_support_available=supported,candidate_strong_resolution=strong_resolution,
                               strongly_supported_conflict=(witnessed and ref_resolution is not None and strong_resolution is not None and strong_resolution!=ref_resolution),
                               interpretation='Induced unrooted quartet congruence with a separate non-ITS gene tree; not a complete true species tree or a taxon-placement accuracy score.')
                    request_rows.append(row);allrows.append(row)
                group_rows[(request['source_stage'],request['protocol_id'],request['config'])].append((request,request_rows))
            groups=[]
            for (stage,protocol_id,config),items in sorted(group_rows.items()):
                rows=[row for _,rr in items for row in rr]
                # Requests and search jobs are different units; a job can be shared across configurations.
                groups.append({'source_stage':stage,'protocol_id':protocol_id,'config':config,
                    'biological_input_hashes':1,'logical_requests':len(items),
                    'alignment_replicates':len({r['replicate'] for r,_ in items}),
                    'unique_named_alignments':len({r['named_alignment_sha256'] for r,_ in items}),
                    'unique_search_jobs_used':len({r['run_json'] for r,_ in items}),
                    'search_jobs_owned_by_config':len({r['run_json'] for r,_ in items if r.get('inference_counts_as_independent') is True}),
                    'reused_logical_requests':sum(r.get('reused',False) for r,_ in items),
                    'request_quartet_observations':len(rows),
                    'congruent_observations':sum(r['raw_congruent'] is True for r in rows),
                    'resolved_discordant_observations':sum(r['reference_resolution'] is not None and r['candidate_resolution'] is not None and r['raw_congruent'] is False for r in rows),
                    'candidate_unresolved_observations':sum(r['candidate_resolution'] is None for r in rows),
                    'strongly_supported_conflicting_observations':sum(r['strongly_supported_conflict'] for r in rows),
                    'logical_requests_with_any_resolved_conflict':sum(any(r['reference_resolution'] is not None and r['candidate_resolution'] is not None and r['raw_congruent'] is False for r in rr) for _,rr in items),
                    'unique_quartet_states':sorted({str(r['candidate_resolution']) for r in rows})})
            dataset_summaries[dataset][locus]={'assessable':True,'matched_specimens':len(linked),
                'possible_reference_quartets':total,'evaluated_reference_quartets':len(reference_panel),'exact_quartet_panel':exact,
                'quartet_seed':quartet_seed,'sampling':'all quartets' if exact else 'uniform with replacement, fixed panel reused across requests',
                'reference_quartets_with_bootstrap_ge95_witness':sum(q[3] for q in reference_panel),'groups':groups,
                'units':'Repeated requests, search seeds, duplicate named alignments and quartet draws are nested within one original input hash; none is an additional biological dataset.'}
    return allrows,dataset_summaries,protocols


def audit(data, references, output, datasets, trees_root=None, configs=("A", "B", "C"), seed=73111,
          tree_queues=None,queue_configs=None,max_reference_quartets=10000,reference_quartet_seed=20261019):
    output.mkdir(parents=True, exist_ok=False)
    metadata = json.loads((references / "figshare-25691193-metadata.json").read_text())
    for source in metadata["files"]:
        path = references / source["name"]
        if path.exists() and hashlib.md5(path.read_bytes()).hexdigest() != source["computed_md5"]:
            raise ValueError(f"Published source MD5 mismatch: {path}")
    xml = ET.parse(references / "production-pilot-genbank.xml").getroot()
    records = {r.findtext("GBSeq_accession-version"): r for r in xml}
    its = {name: ungap(seq) for name, seq in read_fasta(references / "Cantharellales_ITS.phy")}
    trees = {locus: read_tree(references / f"RAxML_bipartitions.CANTH_{locus}.tre", support_order=["bootstrap"])
             for locus in ("rpb2", "tef1")}
    allrows, matched, originalmaps = [], {}, {}
    for dataset in datasets:
        original = make_mapping(data / f"{dataset}.fasta")
        originalmaps[dataset] = original
        selected = []
        for row in original["records"]:
            header_acc, acc, resolution_error = resolve_accession(row["original_header"], records)
            record = {"dataset": f"{dataset}.fasta", "input_sha256": original["input_sha256"],
                      "id": row["id"], "original_header": row["original_header"],
                      "header_accession": header_acc, "accession": acc, "accepted_reference_tip": None,
                      "reference_loci": []}
            if resolution_error:
                record["unmatched_reason"] = resolution_error
                allrows.append(record)
                continue
            gb = records[acc]
            qualifiers = source_qualifiers(gb)
            identity = sequence_identity(row["sequence"], gb.findtext("GBSeq_sequence").upper())
            record.update(genbank_organism=gb.findtext("GBSeq_organism"), source_qualifiers=qualifiers,
                          sequence_identity=identity)
            if acc in LINKS:
                tip, key, value, reason = LINKS[acc]
                if value not in qualifiers.get(key, []):
                    raise ValueError(f"Reviewed voucher qualifier changed: {acc}")
                if not normalize_identifier(tip).endswith(normalize_identifier(value)):
                    raise ValueError(f"Reviewed source voucher no longer matches tip: {acc}")
                if identity == "NO EXACT IDENTITY":
                    raise ValueError(f"Input residues do not validate accession identity: {acc}")
                if value.endswith(" SV2") and "genotype: SV2" not in qualifiers.get("note", []):
                    raise ValueError("Genotype-suffix interpretation lacks GenBank evidence")
                aseq = its.get(tip)
                archived_identity = sequence_identity(row["sequence"], aseq) if aseq else "tip absent from archived ITS; direct voucher link remains the evidence"
                record.update(accepted_reference_tip=tip, link_qualifier=key, link_value=value,
                              link_reason=reason, archived_ITS_identity=archived_identity,
                              reference_loci=[l for l, t in trees.items() if tip in t.taxa])
                selected.append(record)
            else:
                record["unmatched_reason"] = "No reviewed exact accession/voucher link to a non-ITS source tip; names/ITS similarity alone are insufficient"
            allrows.append(record)
        matched[dataset] = selected
    summary = {}
    for dataset, selected in matched.items():
        summary[dataset] = {}
        for locus, tree in trees.items():
            tips = sorted(r["accepted_reference_tip"] for r in selected if r["accepted_reference_tip"] in tree.taxa)
            info = {"matched_tips": tips, "matched_count": len(tips), "splits": [],
                    "bootstrap_rule": "A source full-tree branch with classical bootstrap >=95 implying the induced split is a conservative witness; no SH-aLRT/UFBoot equivalence is assumed."}
            if len(tips) >= 4:
                universe = tree.selected_mask(tips)
                for mask in tree.splits(tips):
                    witnesses = []
                    for i, node in enumerate(tree.nodes[1:], 1):
                        if tree.canonical(tree.masks[i], universe) == mask:
                            try:
                                bs = float(node.label)
                            except ValueError:
                                bs = None
                            witnesses.append({"source_node_label": node.label, "classical_bootstrap": bs,
                                              "edge_length": node.length})
                    side = tree.side_taxa(mask)
                    info["splits"].append({"side": side, "other_side": [x for x in tips if x not in side],
                                           "source_edge_witnesses": witnesses,
                                           "has_source_bootstrap_ge95": any(w["classical_bootstrap"] is not None and w["classical_bootstrap"] >= 95 for w in witnesses)})
                if trees_root is not None and len(tips) == 4:
                    # Fixed original ordinal order defines a common quartet orientation.
                    linked = sorted((r for r in selected if r["accepted_reference_tip"] in tips), key=lambda r: r["id"])
                    identifiers = tuple(r["id"] for r in linked)
                    source_tips = tuple(r["accepted_reference_tip"] for r in linked)
                    reference_resolution = tree.quartet(source_tips, tree.weighted_depth(tips, set(tree.splits(tips))))
                    info["quartet_original_ids"] = identifiers
                    info["reference_quartet_resolution"] = reference_resolution
                    info["production_tree_congruence"] = []
                    for config in configs:
                        path = trees_root / f"{dataset}.fasta" / config / f"seed{seed}" / "inference.treefile"
                        if not path.exists():
                            continue
                        candidate = read_tree(path)
                        resolution = candidate.quartet(identifiers, candidate.weighted_depth(identifiers, set(candidate.splits(identifiers))))
                        info["production_tree_congruence"].append({"config": config, "seed": seed,
                                                                  "tree": str(path.resolve()),
                                                                  "quartet_resolution": resolution,
                                                                  "agrees_with_nonITS_quartet": resolution == reference_resolution})
            else:
                info["limitation"] = "Fewer than four exact specimen matches; no informative unrooted quartet comparison"
            summary[dataset][locus] = info
    queue_provenance=[]
    if tree_queues:
        requests,queue_provenance=validated_queue_records(tree_queues,originalmaps,queue_configs)
        scores,queue_summary,protocols=score_queue_quartets(matched,originalmaps,trees,requests,
                                                          max_reference_quartets,reference_quartet_seed)
        write_json(output/'queue-quartet-congruence.json',scores)
        write_json(output/'queue-quartet-summary.json',{'datasets':queue_summary,'protocols':protocols,
             'unique_target_input_hashes':len({mapping['input_sha256'] for mapping in originalmaps.values()}),
             'assessable_input_hashes':len({row['input_sha256'] for row in scores}),
             'unique_search_jobs_used':len({row['run_json'] for row in scores}),
             'units':'Distinct input hashes are file-level units and can still overlap in taxa. Logical requests, alignments, seeds, inference jobs and loci are not independent biological inputs.'})
        if scores:
            with (output/'queue-quartet-congruence.tsv').open('x',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(scores[0]),delimiter='\t',lineterminator='\n')
                writer.writeheader()
                for row in scores:writer.writerow({k:json.dumps(v) if isinstance(v,(tuple,list,dict)) else v for k,v in row.items()})
        for dataset in queue_summary:
            for locus in queue_summary[dataset]:summary[dataset][locus]['tree_queue_congruence']=queue_summary[dataset][locus]
    write_json(output / "matched-records.json", allrows)
    write_json(output / "matched-nonITS-splits.json", summary)
    write_json(output / "provenance.json", {
        "analysis_code_sha256": sha256_file(__file__),
        "accession_resolution": "Leading explicit version, or unique archived version for a versionless token; exact residue and reviewed voucher checks remain mandatory.",
        "publication": "https://doi.org/10.1002/ajb2.70054",
        "archive": "https://doi.org/10.6084/m9.figshare.25691193",
        "source_downloads": json.loads((references / "download-provenance.json").read_text()),
        "source_directory": str(references.resolve()), "datasets": datasets,
        "tree_queue_sources":queue_provenance,
        "classical_bootstrap_threshold": 95,
        "excluded_near_matches": [
            {"accessions": ["NR_145334.1", "AB972781.1"], "reason": "CBS131.38 type culture differs from reference Burgoa_verzuoliana_CBS32075"},
            {"accessions": ["OR464476.1"], "reason": "GenBank culture ATCC MYA2933 differs from archived tip ATCCMYA2993; possible typo is not silently corrected"},
            {"accessions": ["KX423755.1", "OR471313.1", "OR471314.1"], "reason": "JL596-16/EZ4455 differ from archived protein-locus specimen EZLF1256; shared species name is insufficient"},
            {"accessions": ["LC642038.1", "LC642040.1"], "reason": "TUMH64401/64402 are not demonstrated identical to archived flavorhizomorphae TYPE tip"}],
        "interpretation": "These references provide sparse non-ITS congruence checks. They do not establish a complete true tree or independent biological identity for local query OV2602."
    })
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--references", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--datasets", nargs="+", default=["1msv", "ghy3"])
    parser.add_argument("--trees-root", type=Path)
    parser.add_argument("--tree-queues",nargs='+',type=Path,help='Completed queue directories or runs.jsonl logs; no searches are launched')
    parser.add_argument("--queue-configs",nargs='+',help='Optional configuration filter for queued trees; default all recorded configurations')
    parser.add_argument("--max-reference-quartets",type=int,default=10000)
    parser.add_argument("--reference-quartet-seed",type=int,default=20261019)
    parser.add_argument("--configs", nargs="+", default=["A", "B", "C"])
    parser.add_argument("--seed", type=int, default=73111)
    args = parser.parse_args()
    if args.max_reference_quartets<1:parser.error('--max-reference-quartets must be positive')
    result = audit(args.data, args.references, args.output, args.datasets,
                   args.trees_root, args.configs, args.seed,args.tree_queues,args.queue_configs,
                   args.max_reference_quartets,args.reference_quartet_seed)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
