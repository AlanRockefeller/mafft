#!/usr/bin/env python3
"""Saturated mixed-workload replay on two vCPUs; per-job service and queue latency.

Serial policies use both CPUs; concurrent deterministic workers are each pinned
to one CPU. Queue order is seeded and shared across policies within a round.
Only descendants of the measured process groups are included in sampled RSS.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import json
import math
import os
from pathlib import Path
import queue
import random
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import alignments as alignment_harness
import quiet as quiet_harness
from alignments import CONFIGS, FORMAT, digest, write_json, run_process_group, read_resources
from quiet import add_arguments as add_quiet_arguments, settings as quiet_settings, cpu_snapshot, busy_delta, wait_for_quiet

POLICIES={'two_threads':('B',1), 'two_jobs':('A',2), 'selective':('C',1),
          'refine_one':('F',1),'one_job':('A',1),'production':('production',1)}

def job_config(policy_config, record):
    if policy_config != 'production':return policy_config
    threads=record['production_threads']
    if threads not in (1,2):raise ValueError('production policy requires recorded thread counts of 1 or 2')
    return 'A' if threads==1 else 'B'

def run_tracked_process(argv, *, stdout, stderr, env, timeout, groups, lock, cancelled, grace_seconds=5):
    """Keep the child group visible to RSS sampling until cleanup completes."""
    process=subprocess.Popen(argv,stdout=stdout,stderr=stderr,env=env,start_new_session=True)
    result={'process_group_pid':process.pid,'timed_out':False,'signals_sent':[]}
    try:
        with lock:groups.add(process.pid)
        if cancelled.is_set():raise RuntimeError('throughput campaign cancelled')
        try:
            result['returncode']=process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            result['timed_out']=True
            try:
                os.killpg(process.pid,signal.SIGTERM);result['signals_sent'].append('SIGTERM')
            except ProcessLookupError:pass
            try:process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:pass
            # The leader can exit while a descendant continues ignoring TERM.
            try:
                os.killpg(process.pid,signal.SIGKILL);result['signals_sent'].append('SIGKILL')
            except ProcessLookupError:pass
            result['returncode']=process.wait()
    except BaseException:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.wait()
        raise
    finally:
        with lock:groups.discard(process.pid)
    return result

def installed_hashes(binary):
    prefix=binary.parent.parent
    return {str(path.relative_to(prefix)):digest(path)
            for directory in ['bin','libexec/mafft']
            for path in (prefix/directory).rglob('*') if path.is_file()}

def source_hashes(source):
    if source is None:return None
    source=source.resolve();core=source/'core'
    if not core.is_dir():raise ValueError('source must be the frozen source root containing core/')
    extensions={'.c','.h','.tmpl','.cu','.m','.metal'}
    return {'root':str(source),'core_file_sha256':{
        str(path.relative_to(source)):digest(path) for path in core.rglob('*')
        if path.is_file() and (path.suffix in extensions or path.name=='Makefile')}}

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
    ap.add_argument('--source',type=Path,help='Frozen source snapshot used for the binary; never inferred from current checkout')
    ap.add_argument('--golden',type=Path,required=True);ap.add_argument('--files',nargs='+')
    ap.add_argument('--policies',nargs='+',choices=POLICIES,default=['two_threads','two_jobs','selective'])
    ap.add_argument('--all-inputs',action='store_true');ap.add_argument('--rounds',type=int,default=2);ap.add_argument('--seed',type=int,default=40919)
    ap.add_argument('--timeout',type=float,default=1800)
    add_quiet_arguments(ap)
    args=ap.parse_args()
    if not math.isfinite(args.timeout) or args.timeout<=0:ap.error('timeout must be finite and positive')
    if args.rounds<1:ap.error('rounds must be positive')
    if len(set(args.policies))!=len(args.policies):ap.error('policies must be unique')
    try:quiet_config=quiet_settings(args)
    except ValueError as error:ap.error(str(error))
    args.binary=args.binary.resolve();args.output=args.output.resolve()
    files=[f for f in json.loads(args.manifest.read_text())['files'] if (args.all_inputs or f['production_threads']==2) and (not args.files or f['file'] in args.files)]
    if not files:ap.error('select at least one input')
    if 'production' in args.policies:
        try:
            for record in files:job_config('production',record)
        except ValueError as error:ap.error(str(error))
    try:supplied_source=source_hashes(args.source)
    except ValueError as error:ap.error(str(error))
    args.output.mkdir(parents=True,exist_ok=False)
    env=os.environ.copy();env.pop('MAFFT_BINARIES',None);tmp=args.output/'tmp';tmp.mkdir()
    env.update(LC_ALL='C',LANG='C',TMPDIR=str(tmp))
    with tempfile.TemporaryFile(dir=tmp) as version_out,tempfile.TemporaryFile(dir=tmp) as version_err:
        execution=run_process_group([str(args.binary),'--version'],stdout=version_out,stderr=version_err,env=env,timeout=30)
        if execution['timed_out'] or execution['returncode']:ap.error('binary --version failed or timed out')
        version_out.seek(0);version_err.seek(0)
        version=(version_out.read()+version_err.read()).decode('utf-8',errors='replace')
    binary_hashes=installed_hashes(args.binary)
    golden_hashes={f['file']:digest(args.golden/f['file']) for f in files}
    harness_source={name:{'path':str(path.resolve()),'sha256':digest(path)}
                    for name,path in [('throughput.py',Path(__file__)),
                                      ('quiet.py',Path(quiet_harness.__file__)),
                                      ('alignments.py',Path(alignment_harness.__file__))]}
    launcher_prefix_lines=[]
    with args.binary.open('rb') as launcher:
        if launcher.read(2)==b'#!':
            launcher.seek(0)
            launcher_prefix_lines=[line for line in launcher.read().decode('utf-8',errors='replace').splitlines()
                                   if line.startswith('prefix=') or line.startswith('defaultprefix=')]
    write_json(args.output/'experiment.json',{
        'argv':os.sys.argv,'binary':str(args.binary),'launcher_sha256':digest(args.binary),'version':version,
        'installed_file_sha256':binary_hashes,'launcher_prefix_assignments':launcher_prefix_lines,
        'supplied_source_snapshot':supplied_source,
        'harness_source_at_startup':harness_source,
        'python_executable':sys.executable,'python_version':sys.version,
        'source_note':'The supplied frozen source is hashed; compiler flags and binary/source correspondence require retained build provenance.',
        'manifest':str(args.manifest.resolve()),'manifest_sha256':digest(args.manifest),'files':files,
        'data':str(args.data.resolve()),'golden':str(args.golden.resolve()),'golden_sha256':golden_hashes,
        'policies':POLICIES,'selected_policies':args.policies,'rounds':args.rounds,'timeout':args.timeout,
        'version_timeout':30,'input_count':len(files),
        'quiet_window':quiet_config,
        'arrival_model':'All jobs arrive at t=0, saturated backlog; queue latency and service time reported separately. Not a replay of observed arrival timestamps.',
        'rss_definition':'Sampled sum of RSS for active child process groups; sample interval 0.2 seconds. GNU time RSS is a separate per-job maximum.',
        'seed':args.seed})
    for rnd in range(args.rounds):
        jobs=files.copy();random.Random(args.seed+rnd).shuffle(jobs)
        policies=args.policies[rnd%len(args.policies):]+args.policies[:rnd%len(args.policies)]
        for pol in policies:
            conf,concurrency=POLICIES[pol];outdir=args.output/f'r{rnd}'/pol;outdir.mkdir(parents=True)
            quiet_check=wait_for_quiet(quiet_config,status_path=args.output/'STATUS.json',context={'round':rnd,'policy':pol})
            write_json(outdir/'quiet-window.json',quiet_check)
            if not quiet_check['ready']:raise RuntimeError('quiet window unavailable; no policy jobs launched')
            write_json(args.output/'STATUS.json',{'phase':'running_policy','round':rnd,'policy':pol,'complete':0,'total':len(files),'quiet_ready':True})
            tasks=queue.Queue()
            for f in jobs:tasks.put(f)
            results=[];lock=threading.Lock();groups=set();stop=threading.Event();cancelled=threading.Event();peaks=[]
            def monitor():
                peak=0
                while not stop.is_set():
                    with lock:current=groups.copy()
                    peak=max(peak,proc_rss(current));stop.wait(.2)
                peaks.append(peak)
            observer_cpu_before=time.process_time()
            policy_cpu_before=cpu_snapshot();clock=time.monotonic();mon=threading.Thread(target=monitor);mon.start()
            def worker(slot):
                while not cancelled.is_set():
                    try:f=tasks.get_nowait()
                    except queue.Empty:return
                    d=outdir/f['file'];d.mkdir();source=(args.data/f['file']).resolve()
                    if digest(source)!=f['sha256']:raise RuntimeError('input changed')
                    if digest(args.golden/f['file'])!=golden_hashes[f['file']]:raise RuntimeError('golden changed')
                    cpu=str(slot) if concurrency==2 else '0,1'
                    actual_config=job_config(conf,f)
                    argv=['nice','-n','10','taskset','-c',cpu,str(args.binary),*CONFIGS[actual_config],*f['production_options'],str(source)]
                    cpu_before=cpu_snapshot();started=time.monotonic();resources=d/'resources.json'
                    utc=datetime.datetime.now(datetime.timezone.utc).isoformat()
                    with (d/'alignment.fasta').open('xb') as out,(d/'stderr.txt').open('xb') as err:
                        execution=run_tracked_process(['/usr/bin/time','-f',FORMAT,'-o',str(resources),*argv],stdout=out,stderr=err,env=env,timeout=args.timeout,groups=groups,lock=lock,cancelled=cancelled)
                    finish=time.monotonic();cpu_after=cpu_snapshot();usage,resource_error=read_resources(resources)
                    r={'input':f['file'],'input_sha256':f['sha256'],'argv':argv,'slot':slot,'config':actual_config,
                       'threads':int(CONFIGS[actual_config][1]),'recorded_production_threads':f['production_threads'],
                       'policy':pol,'round':rnd,'start_utc':utc,'queue_seconds':started-clock,
                       'service_seconds':finish-started,'response_seconds':finish-clock,**execution,
                       'resources':usage,'resources_error':resource_error,
                       'cpu_stat_before':cpu_before,'cpu_stat_after':cpu_after,
                       'output_sha256':digest(d/'alignment.fasta'),'alignment':str(d/'alignment.fasta'),
                       'stderr_sha256':digest(d/'stderr.txt'),'golden_sha256':golden_hashes[f['file']]}
                    r['matches_A']=r['output_sha256']==r['golden_sha256'];r['cpu']=usage['user']+usage['system'] if usage else None
                    r['contention']=busy_delta(cpu_before,cpu_after,r['cpu'],max_busy_cores=quiet_config['max_busy_cores'],other_measured_jobs_possible=concurrency>1)
                    with lock:
                        results.append(r)
                        with (outdir/'runs.jsonl').open('a') as log:log.write(json.dumps(r,sort_keys=True)+'\n')
                        write_json(args.output/'STATUS.json',{'phase':'running_policy','round':rnd,'policy':pol,'complete':len(results),'total':len(files),'quiet_ready':True})
                        print(rnd,pol,len(results),len(files),f['file'],round(r['service_seconds'],3),r['matches_A'],flush=True)
                    if execution['returncode'] or execution['timed_out'] or resource_error or not (d/'alignment.fasta').stat().st_size or (actual_config in ['A','F'] and not r['matches_A']):raise RuntimeError('failed job or deterministic output mismatch')
            try:
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    futures=[]
                    try:
                        for slot in range(concurrency):futures.append(pool.submit(worker,slot))
                        for future in as_completed(futures):future.result()
                    except BaseException:
                        cancelled.set()
                        with lock:active=groups.copy()
                        for group in active:
                            try:os.killpg(group,signal.SIGKILL)
                            except ProcessLookupError:pass
                        for future in futures:future.cancel()
                        raise
            finally:stop.set();mon.join()
            elapsed=time.monotonic()-clock
            policy_cpu_after=cpu_snapshot()
            observer_cpu_seconds=time.process_time()-observer_cpu_before
            totals={'policy':pol,'round':rnd,'config':conf,'concurrency':concurrency,'jobs':len(results),'makespan_seconds':elapsed,'jobs_per_hour':len(results)*3600/elapsed,'cpu_per_job_seconds':sum(x['cpu'] for x in results)/len(results),'cpu_total_seconds':sum(x['cpu'] for x in results),'service_latency_seconds':summary(x['service_seconds'] for x in results),'response_latency_seconds':summary(x['response_seconds'] for x in results),'queue_seconds':summary(x['queue_seconds'] for x in results),'peak_sampled_process_tree_rss_mib':max(peaks)/2**20,'max_per_job_rss_mib':max(x['resources']['rss_kib'] for x in results)/1024,'matches_A':sum(x['matches_A'] for x in results),'queue_order':[f['file'] for f in jobs]}
            totals.update(quiet_window=quiet_check,cpu_stat_before=policy_cpu_before,cpu_stat_after=policy_cpu_after,
                          contention=busy_delta(policy_cpu_before,policy_cpu_after,totals['cpu_total_seconds'],max_busy_cores=quiet_config['max_busy_cores']))
            totals.update(observer_cpu_seconds=observer_cpu_seconds,
                          observer_cpu_cores=observer_cpu_seconds/elapsed,
                          observer_cpu_definition='Policy-wide Python process CPU, including all runner threads and excluding child jobs, bracketing the global CPU snapshots. Cores use the policy makespan as denominator. Diagnostic only; never subtracted from contention or used for quiet-window acceptance.')
            write_json(outdir/'summary.json',totals)
            write_json(args.output/'STATUS.json',{'phase':'policy_complete','round':rnd,'policy':pol,'complete':len(results),'total':len(files),'contention':totals['contention']})
            print(json.dumps(totals),flush=True)
    if installed_hashes(args.binary)!=binary_hashes:raise RuntimeError('installed binaries changed during campaign')
    write_json(args.output/'STATUS.json',{'finished':True,'rounds':args.rounds,'policies':args.policies})

if __name__=='__main__':main()
