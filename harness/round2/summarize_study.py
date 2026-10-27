#!/usr/bin/env python3
"""Summarize independent-input alignment experiments with residue-level metrics.

Repeated runs are observations nested within an input, never extra biological
replicates. Raw input hashes identify copied inputs. The original FASTA identities
must validate before any alignment score is calculated.
"""
import argparse
from collections import defaultdict
import itertools
import json
import math
from pathlib import Path
import statistics
from analysis import (make_mapping,read_alignment,compare_alignments,homology_metrics,
                      write_json,sha256_file)
from throughput import summary


def wilson(k,n):
    if not n:return None
    z=1.959963984540054;p=k/n;den=1+z*z/n
    mid=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0,mid-half),min(1,mid+half)]

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--runs',nargs='+',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True);ap.add_argument('--golden',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--manifest',type=Path,required=True)
    ap.add_argument('--pairwise',action='store_true');args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    runs=[];sources=[];builds=set();installed=set();legacy=0
    for p in args.runs:
        metadata_path=p.parent/'experiment.json'
        metadata=json.loads(metadata_path.read_text())
        build=(metadata['binary'],metadata['version'],metadata['launcher_sha256'])
        builds.add(build)
        # libexec programs can change while the launcher stays the same (alignments.py records them);
        # experiments recorded before that field existed have none to compare.
        programs=metadata.get('installed_file_sha256')
        if programs is not None:installed.add(json.dumps(programs,sort_keys=True))
        else:legacy+=1
        sources.append({'runs':str(p.resolve()),'runs_sha256':sha256_file(p),
                        'experiment':str(metadata_path.resolve()),'experiment_sha256':sha256_file(metadata_path),
                        'binary':build[0],'version':build[1],'launcher_sha256':build[2],
                        'installed_file_sha256':programs})
        for line in p.read_text().splitlines():
            r=json.loads(line)
            if r['returncode'] or r.get('completed') is False:raise ValueError('Failed run in study')
            if (r['binary'],r['version'])!=build[:2]:raise ValueError('Run differs from recorded MAFFT build')
            if sha256_file(r['alignment'])!=r['output_sha256']:raise ValueError('Recorded alignment hash changed')
            r['source_log']=str(p.resolve());runs.append(r)
    if len(builds)!=1 or len(installed)>1:raise ValueError('Analyze different MAFFT builds separately')
    # a legacy experiment's helper programs are unknown, so it cannot be shown to match a recorded set
    if legacy and installed:raise ValueError('Analyze experiments with and without installed program hashes separately')
    manifest={f['file']:f for f in json.loads(args.manifest.read_text())['files']}
    groups=defaultdict(list)
    for r in runs:groups[r['input_sha256']].append(r)
    rows=[];allmetrics=[]
    for digest,rs in sorted(groups.items(),key=lambda x:x[1][0]['input']):
        names=sorted({r['input'] for r in rs});f=names[0]
        mapping=make_mapping(args.data/f)
        if digest!=mapping['input_sha256']:raise ValueError('Input hash mismatch')
        reference=read_alignment(args.golden/f,mapping);cache={}
        row={'input':f,'raw_copies':names,'input_sha256':digest,'taxa':len(mapping['records']),
             'duplicate_rows':mapping['duplicate_rows'],'configs':{}}
        for conf in sorted({r['config'] for r in rs}):
            cs=[r for r in rs if r['config']==conf];unique={};metrics=[]
            for r in cs:
                h=r['output_sha256']
                if h not in cache:
                    alignment=read_alignment(r['alignment'],mapping)
                    m=compare_alignments(reference,alignment,duplicate_sensitivity=True)
                    cache[h]=(alignment,m)
                    write_json(args.output/(f+'.'+h[:16]+'.json'),m)
                unique[h]=cache[h][0];metrics.append(cache[h][1]);allmetrics.append({'input':f,'config':conf,'replicate':r['replicate'],'metrics_hash':h})
            row['configs'][conf]={'runs':len(cs),'distinct_outputs':len(unique),
                'matches_A_runs':sum(m['byte_identical'] for m in metrics),
                'reproducible_in_observed_repeats':len(unique)==1,
                'at_least_five_replicates':len(cs)>=5,
                'wall_seconds':summary(r['wall'] for r in cs),
                'cpu_seconds':summary(r['cpu'] for r in cs),
                'timing_contention':{'flagged_runs':sum(r.get('contention',{}).get('contention_flag') is True for r in cs),
                                     'unknown_runs':sum(r.get('contention',{}).get('contention_flag') is None for r in cs)},
                'max_rss_kib':max(r['resources']['rss_kib'] for r in cs),
                'width':summary(m['candidate']['width'] for m in metrics),
                'gap_fraction':summary(m['candidate']['gap_fraction'] for m in metrics),
                'homology_f1':summary(m['homology']['f1'] for m in metrics),
                'ACGT_homology_f1':summary(m['homology_unambiguous']['f1'] for m in metrics),
                'reference_nonmatching_homology_columns':summary(m['columns']['reference_nonmatching_homology_columns'] for m in metrics),
                'candidate_nonmatching_homology_columns':summary(m['columns']['candidate_nonmatching_homology_columns'] for m in metrics),
                'direction_changed_ids':sorted({i for m in metrics for i in m['direction_changed_ids']}),
                'output_hashes':list(unique)}
            if args.pairwise:
                pairs=[]
                # Include multiplicity when describing repeated-run distributions.
                for a,b in itertools.combinations(cs,2):
                    if a['output_sha256']==b['output_sha256']:pairs.append(1.0)
                    else:
                        hm=homology_metrics(cache[a['output_sha256']][0],cache[b['output_sha256']][0])
                        pairs.append(hm['f1'])
                row['configs'][conf]['within_configuration_homology_f1']=summary(pairs)
        rows.append(row);print(f, {c:(v['runs'],v['distinct_outputs'],v['matches_A_runs']) for c,v in row['configs'].items()},flush=True)
    aggregate={}
    for conf in sorted({r['config'] for r in runs}):
        vals=[r['configs'][conf] for r in rows if conf in r['configs']];n=len(vals)
        changes=sum(v['matches_A_runs']<v['runs'] for v in vals)
        variable=sum(v['distinct_outputs']>1 for v in vals)
        aggregate[conf]={'unique_inputs':n,'alignment_runs':sum(v['runs'] for v in vals),
                         'inputs_with_any_A_difference':changes,'A_difference_fraction':changes/n,
                         'descriptive_wilson_95_interval':wilson(changes,n),
                         'inputs_variable_across_repeats':variable,
                         'variable_fraction':variable/n,'variable_wilson_95_interval':wilson(variable,n),
                         'inputs_with_at_least_five_replicates':sum(v['at_least_five_replicates'] for v in vals),
                         'median_of_input_median_f1':statistics.median(v['homology_f1']['median'] for v in vals),
                         'minimum_f1':min(v['homology_f1']['min'] for v in vals)}
    result={'unique_raw_input_hashes':len(rows),'alignment_runs':len(runs),'aggregates':aggregate,'datasets':rows,
            'sources':sources,'timing_interpretation':'Scientific alignment repetitions can overlap production work. Their wall and CPU times are descriptive; clean quiet-gated measurements determine performance recommendations. Missing contention observations are not treated as quiet.',
            'interpretation':'Wilson intervals describe counts of distinct input files; taxon/sequence overlap may reduce biological independence. Repeated alignment runs are never independent datasets. Observed reproducibility is a finite test, not a proof for all inputs.'}
    write_json(args.output/'summary.json',result);write_json(args.output/'metric-index.json',allmetrics)
    with (args.output/'datasets.tsv').open('x') as out:
        out.write('input\tinput_sha256\ttaxa\tduplicate_rows\tconfig\truns\tdistinct_outputs\tmatches_A_runs\tmedian_homology_f1\tminimum_homology_f1\tmedian_wall_s\tmedian_cpu_s\tmax_rss_kib\n')
        for row in rows:
            for c,v in row['configs'].items():
                fields=[row['input'],row['input_sha256'],row['taxa'],row['duplicate_rows'],c,v['runs'],v['distinct_outputs'],v['matches_A_runs'],v['homology_f1']['median'],v['homology_f1']['min'],v['wall_seconds']['median'],v['cpu_seconds']['median'],v['max_rss_kib']]
                out.write('\t'.join(map(str,fields))+'\n')

if __name__=='__main__':main()
