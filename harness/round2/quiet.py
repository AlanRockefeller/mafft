"""Read-only shared-VM quiet checks and global CPU accounting.

No process is stopped or signalled. CPU estimates include harness/kernel work;
they are observational flags, never a reason to silently drop a timed result.
"""
import datetime
import json
import math
import os
from pathlib import Path
import time


def add_arguments(parser):
    enabled=os.environ.get('MAFFT_BENCH_QUIET','1').lower() not in ('0','false','no','off')
    parser.add_argument('--quiet',action='store_true',default=enabled,
                        help='wait before a job/policy; default on, also controlled by MAFFT_BENCH_QUIET')
    parser.add_argument('--no-quiet',action='store_false',dest='quiet',help='explicitly disable the default quiet gate')
    parser.add_argument('--quiet-timeout',type=float,default=300,help='maximum quiet-gate wait seconds')
    parser.add_argument('--quiet-interval',type=float,default=1,help='quiet CPU sample interval in seconds')
    parser.add_argument('--quiet-samples',type=int,default=2,help='consecutive quiet samples required')
    parser.add_argument('--quiet-max-cpu',type=float,default=.10,
                        help='allowed global busy CPU in cores, not percent; default 0.10 of one vCPU')


def settings(args):
    for name in ('quiet_timeout','quiet_interval','quiet_max_cpu'):
        value=getattr(args,name)
        if not math.isfinite(value) or value<0 or (name!='quiet_max_cpu' and value==0):
            raise ValueError(name.replace('_','-')+' must be finite and '+('nonnegative' if name=='quiet_max_cpu' else 'positive'))
    if args.quiet_samples<1:raise ValueError('quiet-samples must be positive')
    return {'enabled':args.quiet,'environment_value':os.environ.get('MAFFT_BENCH_QUIET'),
            'timeout_seconds':args.quiet_timeout,
            'interval_seconds':args.quiet_interval,'consecutive_samples':args.quiet_samples,
            'max_busy_cores':args.quiet_max_cpu,'max_scientific_cores':min(.05,args.quiet_max_cpu)}


def cpu_snapshot():
    try:
        lines=Path('/proc/stat').read_text().splitlines()
        fields=lines[0].split()
        if fields[0]!='cpu':raise ValueError('missing aggregate cpu line')
        values=[int(value) for value in fields[1:]]+[0]*10
        count=sum(line.startswith('cpu') and line[3:4].isdigit() for line in lines)
        return {'available':True,'monotonic':time.monotonic(),
                'busy_ticks':sum(values[index] for index in (0,1,2,5,6)),
                'steal_ticks':values[7],'tick_hz':os.sysconf('SC_CLK_TCK'),
                'cpu_count':count or os.cpu_count() or 1,
                'definition':'user+nice+system+irq+softirq; excludes idle, iowait and steal; guest fields not double counted'}
    except (OSError,ValueError,IndexError) as error:
        return {'available':False,'monotonic':time.monotonic(),'error':str(error)}


def busy_delta(before,after,job_cpu_seconds,*,max_busy_cores=.10,other_measured_jobs_possible=False):
    if not before.get('available') or not after.get('available'):
        return {'available':False,'contention_flag':None,'reason':'global CPU counters unavailable'}
    elapsed=after['monotonic']-before['monotonic']
    ticks=after['busy_ticks']-before['busy_ticks']
    steal=after['steal_ticks']-before['steal_ticks']
    if elapsed<=0 or ticks<0 or steal<0 or before['tick_hz']!=after['tick_hz']:
        return {'available':False,'contention_flag':None,'reason':'inconsistent global CPU snapshots'}
    busy=ticks/after['tick_hz'];steal_seconds=steal/after['tick_hz']
    accounted=job_cpu_seconds if job_cpu_seconds is not None and math.isfinite(job_cpu_seconds) and job_cpu_seconds>=0 else None
    residual=busy-accounted if accounted is not None else None
    estimate=max(0.0,residual) if residual is not None else None
    threshold=max(.05,elapsed*max_busy_cores)
    flag=estimate>threshold if estimate is not None else None
    return {'available':True,'elapsed_seconds':elapsed,'global_busy_seconds':busy,
            'accounted_job_cpu_seconds':accounted,'global_busy_minus_job_cpu_seconds_raw':residual,
            'external_busy_seconds_estimate':estimate,
            'external_busy_cores_estimate':estimate/elapsed if estimate is not None else None,
            'external_capacity_fraction_estimate':estimate/elapsed/after['cpu_count'] if estimate is not None else None,
            'external_attribution_valid':not other_measured_jobs_possible,
            'global_unaccounted_busy_flag':flag,
            'contention_flag':flag if not other_measured_jobs_possible else None,
            'flag_threshold_seconds':threshold,'steal_seconds':steal_seconds,
            'note':('Includes other measured workers; use the policy-level sum-of-job-CPU estimate for external contention.'
                    if other_measured_jobs_possible else
                    'Global busy minus GNU job CPU; includes harness/kernel work and counter/time rounding, not process attribution.')}


def scientific_snapshot():
    exact={'mb','dvtditr','tbfast','disttbfast','pairlocalalign','splittbfast','muscle','clustalo'}
    prefixes=('mrbayes','mb-mpi','iqtree','raxml','mafft')
    processes={}
    try:entries=list(Path('/proc').iterdir())
    except OSError:return processes
    for entry in entries:
        if not entry.name.isdigit():continue
        try:
            stat=(entry/'stat').read_text();end=stat.rfind(')')
            name=stat[stat.find('(')+1:end];fields=stat[end+2:].split()
            if name.lower() not in exact and not name.lower().startswith(prefixes):continue
            key=(int(entry.name),int(fields[19]))
            processes[key]={'pid':key[0],'name':name,'cpu_ticks':int(fields[11])+int(fields[12])}
        except (OSError,ValueError,IndexError):pass
    return processes


def _status(path,context,record):
    if path is None:return
    data=dict(context or {})
    phase='quiet_ready' if record['ready'] else 'quiet_unavailable' if record.get('reason') else 'waiting_for_quiet'
    data.update(phase=phase,quiet={key:value for key,value in record.items() if key!='samples'})
    data['quiet']['latest_sample']=record['samples'][-1] if record['samples'] else None
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(data,indent=2)+'\n');temporary.replace(path)


def wait_for_quiet(config,*,status_path=None,context=None):
    if not config['enabled']:return {'enabled':False,'ready':True,'wait_seconds':0.0}
    started=time.monotonic();deadline=started+config['timeout_seconds'];streak=0
    result={'enabled':True,'ready':False,'timed_out':False,'wait_seconds':0.0,'samples':[],
            'start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'settings':config}
    previous=cpu_snapshot();processes=scientific_snapshot()
    _status(status_path,context,result)
    if not previous.get('available'):
        result['reason']='global CPU counters unavailable';_status(status_path,context,result);return result
    while time.monotonic()<deadline:
        time.sleep(min(config['interval_seconds'],max(0,deadline-time.monotonic())))
        current=cpu_snapshot();now_processes=scientific_snapshot()
        delta=busy_delta(previous,current,0.0,max_busy_cores=config['max_busy_cores'])
        science=[]
        if delta.get('available'):
            for key,process in now_processes.items():
                prior=processes.get(key)
                ticks=process['cpu_ticks']-(prior['cpu_ticks'] if prior else 0)
                cores=max(0,ticks)/current['tick_hz']/delta['elapsed_seconds']
                if cores>config['max_scientific_cores']:
                    science.append({'pid':process['pid'],'name':process['name'],'cpu_cores':cores,'newly_observed':prior is None})
        quiet=bool(delta.get('available') and delta['external_busy_cores_estimate']<=config['max_busy_cores'] and not science)
        streak=streak+1 if quiet else 0
        result['samples'].append({'global_cpu':delta,'busy_scientific_processes':science,'quiet':quiet,'quiet_streak':streak})
        result['wait_seconds']=time.monotonic()-started
        result['ready']=streak>=config['consecutive_samples']
        _status(status_path,context,result)
        if result['ready']:return result
        previous=current;processes=now_processes
    result['timed_out']=True;result['reason']='quiet deadline reached without the required consecutive samples'
    result['wait_seconds']=time.monotonic()-started;_status(status_path,context,result);return result
