#!/usr/bin/env python3
"""Sequential, resumable threading experiment; exclusive outputs and per-run GNU time.

A is --thread 1; B is --thread 2; C is --thread 2 --threadit 0;
D additionally sets --threadtb 1; E sets --threadtb 0. Production strategy
and direction options are read from the frozen manifest. No golden is overwritten.
Create OUTPUT/PAUSE to pause at a job boundary (STATUS.json indicates paused).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import datetime

CONFIGS = {'A':['--thread','1'], 'B':['--thread','2'],
           'C':['--thread','2','--threadit','0'],
           'D':['--thread','2','--threadit','0','--threadtb','1'],
           'E':['--thread','2','--threadit','0','--threadtb','0'],
           'F':['--thread','2','--threadit','1']}
FORMAT = '{"user":%U,"system":%S,"rss_kib":%M,"major_faults":%F,"minor_faults":%R,"inblocks":%I,"outblocks":%O,"voluntary_switches":%w,"involuntary_switches":%c,"exit":%x}'

def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def write_json(p,d):
    t=p.with_suffix('.tmp'); t.write_text(json.dumps(d,indent=2)+'\n');t.replace(p)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest',required=True,type=Path)
    ap.add_argument('--data',required=True,type=Path)
    ap.add_argument('--binary',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path)
    ap.add_argument('--files',nargs='+')
    ap.add_argument('--all-inputs',action='store_true')
    ap.add_argument('--configs',nargs='+',choices=CONFIGS,default=['A','B','C'])
    ap.add_argument('--replicates',type=int,default=5)
    ap.add_argument('--a-replicates',type=int,default=2)
    ap.add_argument('--golden',type=Path)
    ap.add_argument('--timeout',type=int,default=1800)
    args=ap.parse_args()
    args.output=args.output.resolve();args.binary=args.binary.resolve()
    args.output.mkdir(exist_ok=True,parents=True)
    env=os.environ.copy();env.pop('MAFFT_BINARIES',None)
    tmp=args.output/'tmp';tmp.mkdir(exist_ok=True)
    env.update(LC_ALL='C',LANG='C',TMPDIR=str(tmp))
    files=json.loads(args.manifest.read_text())['files']
    files=[f for f in files if (args.all_inputs or f['production_threads']==2) and (not args.files or f['file'] in args.files)]
    seen={};independent=[]
    for f in files:
        if f['sha256'] not in seen:
            independent.append(f);seen[f['sha256']]=f['file']
    version=subprocess.run([str(args.binary),'--version'],capture_output=True,text=True,env=env)
    metadata={'binary':str(args.binary),'launcher_sha256':digest(args.binary),
              'version':version.stdout+version.stderr,'manifest_sha256':digest(args.manifest),
              'configs':CONFIGS,'selected_configs':args.configs,'selected_files':files,'independent_files':[f['file'] for f in independent],
              'replicates':args.replicates,'a_replicates':args.a_replicates,
              'argv':list(os.sys.argv),'cpu_affinity':sorted(os.sched_getaffinity(0)),
              'purpose':'alignment variability and descriptive isolated latency; no competing research computation'}
    mp=args.output/'experiment.json'
    if mp.exists():
        old=json.loads(mp.read_text())
        for k in ['binary','launcher_sha256','manifest_sha256','selected_files','selected_configs','replicates','a_replicates']:
            if old[k]!=metadata[k]:raise SystemExit('resume metadata mismatch: '+k)
    else:mp.write_text(json.dumps(metadata,indent=2)+'\n')
    logpath=args.output/'runs.jsonl';done={}
    if logpath.exists():
        for l in logpath.read_text().splitlines():
            r=json.loads(l); key=(r['input'],r['config'],r['replicate'])
            if r['returncode'] or digest(r['alignment'])!=r['output_sha256']:raise SystemExit('invalid prior record '+str(key))
            done[key]=r
    total=sum((args.a_replicates if c=='A' else args.replicates)*len(files) for c in args.configs)
    with logpath.open('a',buffering=1) as log:
        for rep in range(max(args.replicates,args.a_replicates)):
            for fi,f in enumerate(files):
                configs=args.configs[(fi+rep)%len(args.configs):]+args.configs[:(fi+rep)%len(args.configs)]
                for c in configs:
                    if rep>=(args.a_replicates if c=='A' else args.replicates):continue
                    key=(f['file'],c,rep)
                    if key in done:continue
                    while (args.output/'PAUSE').exists():
                        write_json(args.output/'STATUS.json',{'paused':True,'complete':len(done),'total':total,'next':key})
                        time.sleep(.5)
                    write_json(args.output/'STATUS.json',{'paused':False,'complete':len(done),'total':total,'running':key})
                    source=(args.data/f['file']).resolve()
                    if digest(source)!=f['sha256']:raise SystemExit('input hash changed')
                    dest=args.output/f['file']/c/f'r{rep}'
                    dest.mkdir(parents=True,exist_ok=False)
                    output=dest/'alignment.fasta';err=dest/'stderr.txt';resources=dest/'resources.json'
                    command=['nice','-n','10','taskset','-c','0,1',str(args.binary),*CONFIGS[c],*f['production_options'],str(source)]
                    argv=['/usr/bin/time','-f',FORMAT,'-o',str(resources),*command]
                    start=time.monotonic();utc=datetime.datetime.now(datetime.timezone.utc).isoformat()
                    with output.open('xb') as out,err.open('xb') as stderr:
                        run=subprocess.run(argv,stdout=out,stderr=stderr,env=env,timeout=args.timeout)
                    elapsed=time.monotonic()-start
                    r={'input':f['file'],'input_sha256':f['sha256'],'config':c,'replicate':rep,
                       'argv':command,'version':metadata['version'],'binary':str(args.binary),
                       'alignment':str(output),'output_sha256':digest(output),'returncode':run.returncode,
                       'wall':elapsed,'start_utc':utc,'resources':json.loads(resources.read_text()),
                       'environment':{k:env[k] for k in ['LC_ALL','LANG','TMPDIR']},
                       'strategy':next((s for s in ['L-INS-i','FFT-NS-i','FFT-NS-2','G-INS-i','E-INS-i'] if s in err.read_text(errors='replace')),'unknown')}
                    r['cpu']=r['resources']['user']+r['resources']['system']
                    if args.golden:
                        golden=args.golden/f['file'];r['golden_sha256']=digest(golden);r['matches_A_golden']=r['output_sha256']==r['golden_sha256']
                    log.write(json.dumps(r,sort_keys=True)+'\n');done[key]=r
                    print(f'{len(done)}/{total} {f["file"]} {c} r{rep} wall={elapsed:.3f} cpu={r["cpu"]:.3f} A={r.get("matches_A_golden")} {r["output_sha256"][:12]}',flush=True)
                    if run.returncode or not output.stat().st_size:raise SystemExit('alignment failed')
                    if c=='A' and args.golden and not r['matches_A_golden']:raise SystemExit('A differs from frozen golden')
    write_json(args.output/'STATUS.json',{'paused':False,'complete':len(done),'total':total,'finished':True})

if __name__=='__main__':main()
