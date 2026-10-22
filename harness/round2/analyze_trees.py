#!/usr/bin/env python3
"""Compare replicate trees and fixed-alignment search controls, without likelihood ranking."""
import argparse
from collections import defaultdict
import itertools
import hashlib
import json
from pathlib import Path
import statistics
from analysis import make_mapping,write_json,sha256_file
from tree import read_tree,compare_trees,write_tsv
from throughput import summary

def inference_protocol(queue,row):
    """Validate a request against its prepared job and identify its full method.

    Paths for the alignment/prefix and the random seed vary across legitimate
    comparisons. Every other command argument, including the executable, is
    part of the compatibility signature. Additional future search flags cannot
    silently disappear merely because this collector does not yet know them.
    """
    meta=queue['metadata'];plan=queue['jobs'][row['job_key']]
    command=plan.get('command')
    if not isinstance(command,list) or not command:
        raise ValueError('Prepared tree job lacks a complete IQ-TREE command')
    def option(*names):
        found=[i for i,x in enumerate(command) if x in names]
        if len(found)>1:raise ValueError('Duplicate method option in IQ-TREE command: '+str(names))
        if not found:return None
        if found[0]+1>=len(command):raise ValueError('Missing value for IQ-TREE option: '+str(names))
        return command[found[0]+1]
    for key in ['binary_sha256','iqtree_version','model','threads','nstop','fast','support']:
        default=False if key in ['fast','support'] else None
        if plan.get(key,default)!=meta.get(key,default):
            raise ValueError('Queue/job inference provenance mismatch: '+key)
    if command[0]!=meta['iqtree']:
        raise ValueError('Queue/job IQ-TREE executable path mismatch')
    for key in ['input_sha256','named_alignment_sha256','seed']:
        if plan[key]!=row[key]:raise ValueError('Tree request/job identity mismatch: '+key)
    for key in ['fast','support']:
        if row.get(key,meta[key])!=meta[key]:raise ValueError('Tree request inference setting mismatch: '+key)
    if option('-m')!=plan['model'] or option('-T','-nt')!=str(plan['threads']):
        raise ValueError('IQ-TREE model/thread command disagrees with recorded settings')
    if option('--seed','-seed')!=str(plan['seed']):
        raise ValueError('IQ-TREE command seed disagrees with its paired-search label')
    if option('--mem','-mem')!=meta['mem'] or option('--nstop','-nstop')!=(str(plan['nstop']) if plan['nstop'] is not None else None):
        raise ValueError('IQ-TREE memory/search command disagrees with recorded settings')
    if ('--fast' in command or '-fast' in command)!=plan.get('fast',False):
        raise ValueError('IQ-TREE fast-search flag disagrees with recorded settings')
    support_options=plan.get('support_replicates')
    if plan['support']:
        if not support_options or option('-B','--ufboot')!=str(support_options['ufboot']) or option('--alrt','-alrt')!=str(support_options['shalrt']):
            raise ValueError('IQ-TREE support replicates disagree with recorded settings')
        if ('--bnni' in command or '-bnni' in command)!=support_options['bootstrap_nni']:
            raise ValueError('IQ-TREE bootstrap NNI setting disagrees with recorded settings')
    elif option('-B','--ufboot') is not None or option('--alrt','-alrt') is not None:
        raise ValueError('Unexpected support calculation in an unsupported-search cohort')
    template=list(command)
    for i,arg in enumerate(command):
        if arg in ['-s','--prefix','-pre','--seed','-seed']:
            if i+1>=len(command):raise ValueError('Incomplete IQ-TREE command')
            template[i+1]={'-s':'<alignment>','--prefix':'<prefix>','-pre':'<prefix>',
                           '--seed':'<seed>','-seed':'<seed>'}[arg]
    protocol={'iqtree_executable':command[0],'binary_sha256':plan['binary_sha256'],
              'iqtree_version':plan['iqtree_version'],'model':plan['model'],'threads':plan['threads'],
              'fast':plan.get('fast',False),'support':plan['support'],'support_replicates':support_options,
              'nstop':plan['nstop'],'mem':meta['mem'],'command_template':template}
    key=hashlib.sha256(json.dumps(protocol,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return key,protocol


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--queues',nargs='+',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--quartets',type=int,default=1000);args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=False);groups=defaultdict(list);counts=[];protocols={};seen={};tree_hashes={};excluded=[]
    for q in args.queues:
        queue=json.loads((q/'queue.json').read_text());records=[json.loads(x) for x in (q/'runs.jsonl').read_text().splitlines()]
        good=[r for r in records if r['completed']]
        counts.append({'queue':str(q.resolve()),'requested':len(records),'successful_requests':len(good),
                       'independent_executed_searches':sum(r['inference_counts_as_independent'] for r in good),
                       'unique_tree_hashes':len({r['tree_sha256'] for r in good}),
                       'fast':queue['metadata']['fast'],'support':queue['metadata']['support']})
        queue_protocols=set()
        for r in good:
            cohort,protocol=inference_protocol(queue,r);protocols[cohort]=protocol;queue_protocols.add(cohort)
            if r['tree'] not in tree_hashes:tree_hashes[r['tree']]=sha256_file(r['tree'])
            if tree_hashes[r['tree']]!=r['tree_sha256']:raise ValueError('Recorded tree hash changed: '+r['tree'])
            identity=(r['input_sha256'],cohort,r['config'],r['replicate'],r['seed'])
            if identity in seen:
                old=seen[identity]
                if old['tree_sha256']!=r['tree_sha256'] or old['named_alignment_sha256']!=r['named_alignment_sha256']:
                    raise ValueError('Conflicting tree/alignment for the same input/config/replicate/seed/method')
                continue
            seen[identity]=r;groups[(r['input_sha256'],cohort)].append(r)
        counts[-1]['protocol_ids']=sorted(queue_protocols)
    trees={};cache={};comparisons=[];datasets=[]
    with (args.output/'comparisons.jsonl').open('x',buffering=1) as full:
        for (sha,cohort),rs in sorted(groups.items(),key=lambda x:str(x[0])):
            protocol=protocols[cohort];fast=protocol['fast'];support=protocol['support']
            name=rs[0]['input'];mapping=make_mapping(args.data/name);by=defaultdict(list)
            if mapping['input_sha256']!=sha:raise ValueError('Original FASTA hash changed: '+name)
            for r in rs:by[(r['config'],r['seed'])].append(r)
            if not any(c=='A' for c,s in by):
                excluded.append({'input':name,'input_sha256':sha,'protocol_id':cohort,'reason':'No completed A search in this compatible-method cohort'})
                continue
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
                     'fast':fast,'support':support,'protocol_id':cohort,'groups':{}}
            for kind,a,b in cases:
                key=(a['tree'],b['tree'])
                if key not in cache:
                    for r in [a,b]:
                        if r['tree'] not in trees:trees[r['tree']]=read_tree(r['tree'])
                    cache[key]=compare_trees(trees[a['tree']],trees[b['tree']],mapping=mapping,quartets=args.quartets,details=True)
                result=cache[key]
                info={'input':name,'input_sha256':sha,'kind':kind,'fast':fast,'support':support,'protocol_id':cohort,
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
                available_views=sorted({x['view'] for x in comparisons if x['input_sha256']==sha and x['protocol_id']==cohort and x['kind']==kind})
                for view in available_views:
                    z=[x for x in comparisons if x['input_sha256']==sha and x['protocol_id']==cohort and x['kind']==kind and x['view']==view]
                    if z:views[view]={'comparisons':len(z),'rf':summary(x['rf'] for x in z),'normalized_rf':summary(x['normalized_rf'] for x in z),
                                     'quartet_distance':summary(x['quartet_distance'] for x in z if x['quartet_distance'] is not None),
                                     'comparisons_with_incompatible_splits':sum(x['incompatible_split_pairs']>0 for x in z)}
                dataset['groups'][kind]=views
            datasets.append(dataset);print(name,'fast',fast,'support',support,'comparisons',len(cases),flush=True)
    aggregate=[]
    for cohort,kind,view in sorted({(x['protocol_id'],x['kind'],x['view']) for x in comparisons}):
        protocol=protocols[cohort]
        z=[d['groups'][kind][view] for d in datasets if d['protocol_id']==cohort and kind in d['groups'] and view in d['groups'][kind]]
        aggregate.append({'fast':protocol['fast'],'support':protocol['support'],'protocol_id':cohort,'kind':kind,'view':view,'distinct_inputs':len(z),
                          'inputs_with_any_rf_difference':sum(t['rf']['max']>0 for t in z),
                          'median_of_input_median_nrf':statistics.median(t['normalized_rf']['median'] for t in z),
                          'max_nrf':max(t['normalized_rf']['max'] for t in z),
                          'median_of_input_max_nrf':statistics.median(t['normalized_rf']['max'] for t in z)})
    write_json(args.output/'summary.json',{'queue_counts':counts,'datasets':datasets,'aggregates':aggregate,'protocols':protocols,'excluded_groups':excluded,
                 'independence':'Each distinct original input hash is one summary unit; replicates and seed searches are nested, and some datasets overlap in taxa. Different search methods/support settings are separate cohorts.',
                 'likelihood_policy':'No cross-alignment likelihood ranking is performed.'})
    if comparisons:write_tsv(args.output/'comparisons.tsv',comparisons)
    else:
        with (args.output/'comparisons.tsv').open('x') as empty:empty.write('input\tinput_sha256\tprotocol_id\tkind\tview\trf\tnormalized_rf\n')

if __name__=='__main__':main()
