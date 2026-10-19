#!/usr/bin/env python3
"""Saturated mixed-workload replay on two vCPUs; per-job service and queue latency.

Serial policies use both CPUs; concurrent deterministic workers are each pinned
to one CPU. Queue order is seeded and shared across policies within a round.
Only descendants of the measured process groups are included in sampled RSS.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import queue
import random
import statistics
import subprocess
import threading
import time
from alignments import CONFIGS, FORMAT, digest, write_json

POLICIES={'two_threads':('B',1), 'two_jobs':('A',2), 'selective':('C',1),
          'refine_one':('F',1),'one_job':('A',1)}

def proc_rss(groups):
    total=0
    for d in Path('/proc').iterdir():
        if not d.name.isdigit():continue
        try:
            st=(d/'stat').read_text(); f=st[st.rfind(')')+2:].split()
            if int(f[2]) in groups:total+=int((d/'statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
        except (OSError,ValueError,IndexError):pass
    return total

def summary(values):
    v=sorted(values)
    if not v:return {}
    def pct(p):
        x=(len(v)-1)*p;i=int(x);return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(x-i)
    return {'n':len(v),'median':statistics.median(v),'p95':pct(.95),'sum':sum(v),'min':v[0],'max':v[-1]}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest',type=Path,required=True);ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--binary',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--golden',type=Path,required=True);ap.add_argument('--files',nargs='+')
    ap.add_argument('--policies',nargs='+',choices=POLICIES,default=['two_threads','two_jobs','selective'])
    ap.add_argument('--all-inputs',action='store_true');ap.add_argument('--rounds',type=int,default=2);ap.add_argument('--seed',type=int,default=40919)
    args=ap.parse_args();args.output=args.output.resolve();args.output.mkdir(parents=True,exist_ok=False)
    files=[f for f in json.loads(args.manifest.read_text())['files'] if (args.all_inputs or f['production_threads']==2) and (not args.files or f['file'] in args.files)]
    env=os.environ.copy();env.pop('MAFFT_BINARIES',None);tmp=args.output/'tmp';tmp.mkdir()
    env.update(LC_ALL='C',LANG='C',TMPDIR=str(tmp))
    write_json(args.output/'experiment.json',{'argv':os.sys.argv,'binary':str(args.binary.resolve()),'version':subprocess.run([str(args.binary),'--version'],capture_output=True,text=True).stderr,'manifest_sha256':digest(args.manifest),'policies':POLICIES,'input_count':len(files),'arrival_model':'All jobs arrive at t=0, saturated backlog; queue latency and service time reported separately. Not a replay of observed arrival timestamps.','seed':args.seed})
    for rnd in range(args.rounds):
        jobs=files.copy();random.Random(args.seed+rnd).shuffle(jobs)
        policies=args.policies[rnd%len(args.policies):]+args.policies[:rnd%len(args.policies)]
        for pol in policies:
            conf,concurrency=POLICIES[pol];outdir=args.output/f'r{rnd}'/pol;outdir.mkdir(parents=True)
            tasks=queue.Queue()
            for f in jobs:tasks.put(f)
            results=[];lock=threading.Lock();groups=set();stop=threading.Event();peaks=[]
            def monitor():
                peak=0
                while not stop.is_set():
                    with lock:current=groups.copy()
                    peak=max(peak,proc_rss(current));stop.wait(.2)
                peaks.append(peak)
            clock=time.monotonic();mon=threading.Thread(target=monitor);mon.start()
            def worker(slot):
                while True:
                    try:f=tasks.get_nowait()
                    except queue.Empty:return
                    d=outdir/f['file'];d.mkdir();source=(args.data/f['file']).resolve()
                    if digest(source)!=f['sha256']:raise RuntimeError('input changed')
                    cpu=str(slot) if concurrency==2 else '0,1'
                    argv=['nice','-n','10','taskset','-c',cpu,str(args.binary.resolve()),*CONFIGS[conf],*f['production_options'],str(source)]
                    started=time.monotonic();resources=d/'resources.json'
                    with (d/'alignment.fasta').open('xb') as out,(d/'stderr.txt').open('xb') as err:
                        p=subprocess.Popen(['/usr/bin/time','-f',FORMAT,'-o',str(resources),*argv],stdout=out,stderr=err,env=env,start_new_session=True)
                        with lock:groups.add(p.pid)
                        rc=p.wait(timeout=1800)
                        with lock:groups.discard(p.pid)
                    finish=time.monotonic();r={'input':f['file'],'input_sha256':f['sha256'],'argv':argv,'slot':slot,'config':conf,'policy':pol,'round':rnd,'queue_seconds':started-clock,'service_seconds':finish-started,'response_seconds':finish-clock,'returncode':rc,'resources':json.loads(resources.read_text()),'output_sha256':digest(d/'alignment.fasta'),'alignment':str(d/'alignment.fasta'),'golden_sha256':digest(args.golden/f['file'])}
                    r['matches_A']=r['output_sha256']==r['golden_sha256'];r['cpu']=r['resources']['user']+r['resources']['system']
                    with lock:
                        results.append(r)
                        with (outdir/'runs.jsonl').open('a') as log:log.write(json.dumps(r,sort_keys=True)+'\n')
                        print(rnd,pol,len(results),len(files),f['file'],round(r['service_seconds'],3),r['matches_A'],flush=True)
                    if rc or (conf in ['A','F'] and not r['matches_A']):raise RuntimeError('failed job or deterministic output mismatch')
            try:
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    futures=[pool.submit(worker,i) for i in range(concurrency)]
                    for future in futures:future.result()
            finally:stop.set();mon.join()
            elapsed=time.monotonic()-clock
            totals={'policy':pol,'round':rnd,'config':conf,'concurrency':concurrency,'jobs':len(results),'makespan_seconds':elapsed,'jobs_per_hour':len(results)*3600/elapsed,'cpu_per_job_seconds':sum(x['cpu'] for x in results)/len(results),'cpu_total_seconds':sum(x['cpu'] for x in results),'service_latency_seconds':summary(x['service_seconds'] for x in results),'response_latency_seconds':summary(x['response_seconds'] for x in results),'queue_seconds':summary(x['queue_seconds'] for x in results),'peak_sampled_process_tree_rss_mib':max(peaks)/2**20,'max_per_job_rss_mib':max(x['resources']['rss_kib'] for x in results)/1024,'matches_A':sum(x['matches_A'] for x in results),'queue_order':[f['file'] for f in jobs]}
            write_json(outdir/'summary.json',totals)
            print(json.dumps(totals),flush=True)

if __name__=='__main__':main()
