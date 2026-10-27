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
import signal
import subprocess
import time
from quiet import cpu_snapshot, busy_delta
import datetime

CONFIGS = {'A':['--thread','1'], 'B':['--thread','2'],
           'C':['--thread','2','--threadit','0'],
           'D':['--thread','2','--threadit','0','--threadtb','1'],
           'E':['--thread','2','--threadit','0','--threadtb','0'],
           'F':['--thread','2','--threadit','1']}
FORMAT = '{"user":%U,"system":%S,"rss_kib":%M,"major_faults":%F,"minor_faults":%R,"inblocks":%I,"outblocks":%O,"voluntary_switches":%w,"involuntary_switches":%c,"exit":%x}'

def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def installed_hashes(binary):
    """Every installed program: the launcher is a script, the alignment comes from libexec.
    A release from deploy.py keeps its programs under payload/, behind wrappers in bin/."""
    prefix=Path(binary).resolve().parent.parent
    if (prefix/'payload'/'libexec'/'mafft').is_dir():dirs=['bin','payload/bin','payload/libexec/mafft']
    elif (prefix/'libexec'/'mafft').is_dir():dirs=['bin','libexec/mafft']
    else:return {Path(binary).name:digest(binary)}
    return {str(x.relative_to(prefix)):digest(x) for d in dirs for x in sorted((prefix/d).rglob('*')) if x.is_file()}

def write_json(p,d):
    t=p.with_suffix('.tmp'); t.write_text(json.dumps(d,indent=2)+'\n');t.replace(p)

def run_process_group(argv, *, stdout, stderr, env, timeout, grace_seconds=5, cwd=None):
    """Terminate descendants as well as GNU time after a timeout.

    A final SIGKILL covers descendants ignoring SIGTERM even if the leader dies.
    No shell is involved; the new session makes this process the group leader.
    """
    process=subprocess.Popen(argv,stdout=stdout,stderr=stderr,env=env,start_new_session=True,cwd=cwd)
    result={'process_group_pid':process.pid,'timed_out':False,'signals_sent':[]}
    try:
        result['returncode']=process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        result['timed_out']=True
        try:
            os.killpg(process.pid,signal.SIGTERM);result['signals_sent'].append('SIGTERM')
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid,signal.SIGKILL);result['signals_sent'].append('SIGKILL')
        except ProcessLookupError:
            pass
        result['returncode']=process.wait()
    except BaseException:
        try:
            os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise
    return result

def read_resources(path):
    """GNU time may prefix JSON with exit diagnostics, or die before writing."""
    if not path.exists():return None,'resource file is missing'
    for line in reversed(path.read_text(errors='replace').splitlines()):
        try:
            value=json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value,dict) and all(isinstance(value.get(k),(int,float)) for k in ['user','system','rss_kib']):
            return value,None
    return None,'no complete GNU time JSON record'

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
    metadata={'binary':str(args.binary),'launcher_sha256':digest(args.binary),'installed_file_sha256':installed_hashes(args.binary),
              'version':version.stdout+version.stderr,'manifest_sha256':digest(args.manifest),
              'configs':CONFIGS,'selected_configs':args.configs,'selected_files':files,'independent_files':[f['file'] for f in independent],
              'replicates':args.replicates,'a_replicates':args.a_replicates,
              'argv':list(os.sys.argv),'cpu_affinity':sorted(os.sched_getaffinity(0)),
              'purpose':'alignment variability; runtime is descriptive and external guest contention is observationally flagged where counters are available. Isolated performance claims use separate quiet-gated measurements.'}
    mp=args.output/'experiment.json'
    if mp.exists():
        old=json.loads(mp.read_text())
        for k in ['binary','launcher_sha256','installed_file_sha256','manifest_sha256','selected_files','selected_configs','replicates','a_replicates']:
            if old.get(k)!=metadata[k]:raise SystemExit('resume metadata mismatch: '+k)
    else:mp.write_text(json.dumps(metadata,indent=2)+'\n')
    logpath=args.output/'runs.jsonl';done={}
    if logpath.exists():
        for l in logpath.read_text().splitlines():
            r=json.loads(l); key=(r['input'],r['config'],r['replicate'])
            if r['returncode'] or not r.get('completed',True) or digest(r['alignment'])!=r['output_sha256']:raise SystemExit('invalid prior record '+str(key)+'; preserve failed artifacts and use a new output directory')
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
                    cpu_before=cpu_snapshot()
                    start=time.monotonic();utc=datetime.datetime.now(datetime.timezone.utc).isoformat()
                    launch_error=None
                    with output.open('xb') as out,err.open('xb') as stderr:
                        try:
                            execution=run_process_group(argv,stdout=out,stderr=stderr,env=env,timeout=args.timeout)
                        except OSError as exc:
                            launch_error=str(exc)
                            execution={'returncode':127,'timed_out':False,'signals_sent':[],'process_group_pid':None}
                    elapsed=time.monotonic()-start;cpu_after=cpu_snapshot()
                    resource_values,resource_error=read_resources(resources)
                    r={'input':f['file'],'input_sha256':f['sha256'],'config':c,'replicate':rep,
                       'argv':command,'version':metadata['version'],'binary':str(args.binary),
                       'alignment':str(output),'output_sha256':digest(output),**execution,
                       'timeout_seconds':args.timeout,'launch_error':launch_error,
                       'wall':elapsed,'start_utc':utc,'resources':resource_values,
                       'resources_complete':resource_values is not None,'resource_error':resource_error,
                       'environment':{k:env[k] for k in ['LC_ALL','LANG','TMPDIR']},
                       'strategy':next((s for s in ['L-INS-i','FFT-NS-i','FFT-NS-2','G-INS-i','E-INS-i'] if s in err.read_text(errors='replace')),'unknown')}
                    r['cpu']=resource_values['user']+resource_values['system'] if resource_values is not None else None
                    r['cpu_stat_before']=cpu_before;r['cpu_stat_after']=cpu_after
                    r['contention']=busy_delta(cpu_before,cpu_after,r['cpu'])
                    if args.golden:
                        golden=args.golden/f['file'];r['golden_sha256']=digest(golden);r['matches_A_golden']=r['output_sha256']==r['golden_sha256']
                    failure=('launch error: '+launch_error if launch_error else
                             'alignment timeout' if r['timed_out'] else
                             'alignment process exited '+str(r['returncode']) if r['returncode'] else
                             'empty alignment output' if not output.stat().st_size else
                             'incomplete resource metadata: '+resource_error if resource_error else
                             'A differs from frozen golden' if c=='A' and args.golden and not r['matches_A_golden'] else None)
                    r['completed']=failure is None;r['failure_reason']=failure
                    log.write(json.dumps(r,sort_keys=True)+'\n');done[key]=r
                    cpu_text=f'{r["cpu"]:.3f}' if r['cpu'] is not None else 'unavailable'
                    print(f'{len(done)}/{total} {f["file"]} {c} r{rep} wall={elapsed:.3f} cpu={cpu_text} A={r.get("matches_A_golden")} {r["output_sha256"][:12]} completed={r["completed"]}',flush=True)
                    if failure:
                        write_json(args.output/'STATUS.json',{'failed':True,'finished':False,'complete':len(done)-1,'total':total,'failed_run':key,'failure_reason':failure,'timed_out':r['timed_out']})
                        raise SystemExit(failure)
    write_json(args.output/'STATUS.json',{'paused':False,'complete':len(done),'total':total,'finished':True})

if __name__=='__main__':main()
