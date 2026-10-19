#!/usr/bin/env python3
"""Choose the largest duplicate-pruned normal-search B/A RF per dataset for targeted support.

This is an informative-case selection, not a population frequency estimate.
All alignment replicates remain in screening and normal-search summaries.
"""
import argparse,json
from pathlib import Path
from tree import read_tree
from analysis import make_mapping,write_json

p=argparse.ArgumentParser(description=__doc__);p.add_argument('--trees',type=Path,required=True)
p.add_argument('--alignments',type=Path,required=True);p.add_argument('--data',type=Path,required=True)
p.add_argument('--output',type=Path,required=True);a=p.parse_args()
rs=[json.loads(x) for x in a.trees.read_text().splitlines()];source=[json.loads(x) for x in a.alignments.read_text().splitlines()]
selected=[];details=[]
for name in sorted({r['input'] for r in rs}):
 rr=[r for r in rs if r['input']==name and r['seed']==73111 and r['completed']]
 baseline=next(r for r in rr if r['config']=='A' and r['replicate']==0)
 mapping=make_mapping(a.data/name);seen=set();ids=[]
 for r in mapping['records']:
  if r['sequence_sha256'] not in seen:seen.add(r['sequence_sha256']);ids.append(r['id'])
 def informative(tree):
  return {k for k,e in tree.splits(ids).items() if e.length is None or e.length>1e-6}
 ref=informative(read_tree(baseline['tree']));options=[]
 for r in rr:
  if r['config']=='B':options.append((len(ref^informative(read_tree(r['tree']))),-r['replicate'],r))
 best=max(options,key=lambda x:x[:2]);rep=best[2]['replicate']
 for c,r in [('A',0),('B',rep),('C',0)]:selected.append(next(z for z in source if z['input']==name and z['config']==c and z['replicate']==r))
 details.append({'input':name,'selected_B_replicate':rep,'duplicate_pruned_RF':best[0],'original_taxa':len(mapping['records']),'retained_taxa_for_comparison':len(ids),'selection':'Maximum B/A RF among normal-search seed73111 B replicates after pruning exact original duplicate sequences and collapsing edges <=1e-6; ties choose earliest replicate. Support results are conditional on this informative-case selection.'})
with a.output.open('x') as f:
 for r in selected:f.write(json.dumps(r,sort_keys=True)+'\n')
write_json(a.output.with_suffix('.selection.json'),details)
