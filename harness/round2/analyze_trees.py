#!/usr/bin/env python3
"""Compare replicate trees and fixed-alignment search controls, without likelihood ranking."""
import argparse
from collections import defaultdict
import itertools
import json
from pathlib import Path
import statistics
from analysis import make_mapping,write_json
from tree import read_tree,compare_trees,write_tsv
from throughput import summary


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--queues',nargs='+',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--quartets',type=int,default=1000);args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=False);groups=defaultdict(list);counts=[]
    for q in args.queues:
        queue=json.loads((q/'queue.json').read_text());records=[json.loads(x) for x in (q/'runs.jsonl').read_text().splitlines()]
        good=[r for r in records if r['completed']]
        counts.append({'queue':str(q.resolve()),'requested':len(records),'successful_requests':len(good),
                       'independent_executed_searches':sum(r['inference_counts_as_independent'] for r in good),
                       'unique_tree_hashes':len({r['tree_sha256'] for r in good}),
                       'fast':queue['metadata']['fast'],'support':queue['metadata']['support']})
        for r in good:groups[(r['input_sha256'],r['fast'],r['support'])].append(r)
    trees={};cache={};comparisons=[];datasets=[]
    with (args.output/'comparisons.jsonl').open('x',buffering=1) as full:
        for (sha,fast,support),rs in sorted(groups.items(),key=lambda x:str(x[0])):
            name=rs[0]['input'];mapping=make_mapping(args.data/name);by=defaultdict(list)
            for r in rs:by[(r['config'],r['seed'])].append(r)
            if not any(c=='A' for c,s in by):continue
            cases=[]
            for seed in sorted({s for c,s in by}):
                if ('A',seed) not in by:continue
                a=min(by['A',seed],key=lambda r:r['replicate'])
                for conf in ['B','C','F']:
                    for b in by.get((conf,seed),[]):cases.append(('A_vs_'+conf,a,b))
                for conf in ['B','C','F']:
                    for a1,b1 in itertools.combinations(by.get((conf,seed),[]),2):cases.append(('within_'+conf,a1,b1))
            # Same exact fixed A alignment, distinct tree-search random seeds.
            acontrols={}
            for (conf,seed),rr in by.items():
                if conf=='A':acontrols[seed]=min(rr,key=lambda r:r['replicate'])
            for a,b in itertools.combinations(acontrols.values(),2):
                if a['named_alignment_sha256']!=b['named_alignment_sha256']:raise ValueError('A search control alignment changed')
                cases.append(('search_control_A',a,b))
            dataset={'input':name,'input_sha256':sha,'taxa':len(mapping['records']),
                     'fast':fast,'support':support,'groups':{}}
            for kind,a,b in cases:
                key=(a['tree'],b['tree'])
                if key not in cache:
                    for r in [a,b]:
                        if r['tree'] not in trees:trees[r['tree']]=read_tree(r['tree'])
                    cache[key]=compare_trees(trees[a['tree']],trees[b['tree']],mapping=mapping,quartets=args.quartets,details=True)
                result=cache[key]
                info={'input':name,'input_sha256':sha,'kind':kind,'fast':fast,'support':support,
                      'reference_config':a['config'],'candidate_config':b['config'],
                      'reference_replicate':a['replicate'],'candidate_replicate':b['replicate'],
                      'reference_seed':a['seed'],'candidate_seed':b['seed'],
                      'same_alignment':a['named_alignment_sha256']==b['named_alignment_sha256'],
                      'comparison':result}
                full.write(json.dumps(info,sort_keys=True)+'\n')
                flat={k:v for k,v in info.items() if k!='comparison'}
                for view,v in result['views'].items():
                    item=dict(flat,view=view,rf=v['rf'],normalized_rf=v['normalized_rf'],split_normalized_rf=v['split_normalized_rf'],
                              shared_splits=v['shared_splits'],reference_splits=v['reference_splits'],candidate_splits=v['candidate_splits'],
                              incompatible_split_pairs=v['incompatible_split_pairs'],quartet_distance=v['quartets'].get('distance_fraction'))
                    comparisons.append(item)
            for kind in sorted({x[0] for x in cases}):
                views={}
                for view in ['raw','zero_collapsed','input_duplicate_representatives_zero_collapsed','strong_support','input_duplicate_representatives_strong_support']:
                    z=[x for x in comparisons if x['input_sha256']==sha and x['fast']==fast and x['support']==support and x['kind']==kind and x['view']==view]
                    if z:views[view]={'comparisons':len(z),'rf':summary(x['rf'] for x in z),'normalized_rf':summary(x['normalized_rf'] for x in z),
                                     'quartet_distance':summary(x['quartet_distance'] for x in z if x['quartet_distance'] is not None),
                                     'comparisons_with_incompatible_splits':sum(x['incompatible_split_pairs']>0 for x in z)}
                dataset['groups'][kind]=views
            datasets.append(dataset);print(name,'fast',fast,'support',support,'comparisons',len(cases),flush=True)
    aggregate=[]
    for fast,support,kind,view in sorted({(x['fast'],x['support'],x['kind'],x['view']) for x in comparisons}):
        z=[d['groups'][kind][view] for d in datasets if d['fast']==fast and d['support']==support and kind in d['groups'] and view in d['groups'][kind]]
        aggregate.append({'fast':fast,'support':support,'kind':kind,'view':view,'distinct_inputs':len(z),
                          'inputs_with_any_rf_difference':sum(t['rf']['max']>0 for t in z),
                          'median_of_input_median_nrf':statistics.median(t['normalized_rf']['median'] for t in z),
                          'max_nrf':max(t['normalized_rf']['max'] for t in z),
                          'median_of_input_max_nrf':statistics.median(t['normalized_rf']['max'] for t in z)})
    write_json(args.output/'summary.json',{'queue_counts':counts,'datasets':datasets,'aggregates':aggregate,
                 'independence':'Each distinct original input hash is one summary unit; replicates and seed searches are nested, and some datasets overlap in taxa. Different search methods/support settings are separate cohorts.',
                 'likelihood_policy':'No cross-alignment likelihood ranking is performed.'})
    write_tsv(args.output/'comparisons.tsv',comparisons)

if __name__=='__main__':main()
