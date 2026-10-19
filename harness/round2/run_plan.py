#!/usr/bin/env python3
"""Run an explicit command plan sequentially, preserving logs and successful steps."""
import argparse,hashlib,json,os
from pathlib import Path
import subprocess,time
from alignments import write_json

p=argparse.ArgumentParser(description=__doc__);p.add_argument('plan',type=Path);a=p.parse_args()
plan=json.loads(a.plan.read_text());out=Path(plan['output']).resolve();out.mkdir(exist_ok=True,parents=True)
digest=hashlib.sha256(a.plan.read_bytes()).hexdigest();meta=out/'plan-sha256.txt'
if meta.exists() and meta.read_text().strip()!=digest:raise SystemExit('Plan changed; preserve campaign and use another output')
meta.write_text(digest+'\n');done={}
log=out/'steps.jsonl'
if log.exists():
 for l in log.read_text().splitlines():
  r=json.loads(l)
  if r['returncode']==0:done[r['label']]=r
if plan.get('wait_for'):
 status=Path(plan['wait_for'])
 while not status.exists() or not json.loads(status.read_text()).get('finished',False):
  write_json(out/'STATUS.json',{'waiting_for':str(status)});time.sleep(2)
with log.open('a',buffering=1) as records:
 for step in plan['steps']:
  if step['label'] in done:continue
  while (out/'PAUSE').exists():
   write_json(out/'STATUS.json',{'paused':True,'next':step['label']});time.sleep(.5)
  write_json(out/'STATUS.json',{'paused':False,'running':step['label'],'command':step['command']})
  start=time.monotonic()
  with (out/(step['label']+'.log')).open('x') as stdout:
   result=subprocess.run(step['command'],cwd=plan['cwd'],stdout=stdout,stderr=subprocess.STDOUT,env=dict(os.environ,LC_ALL='C',LANG='C'))
  r={'label':step['label'],'command':step['command'],'returncode':result.returncode,'elapsed_seconds':time.monotonic()-start};records.write(json.dumps(r,sort_keys=True)+'\n');print(json.dumps(r),flush=True)
  if result.returncode:
   write_json(out/'STATUS.json',{'failed':step['label'],'returncode':result.returncode});raise SystemExit(result.returncode)
write_json(out/'STATUS.json',{'finished':True,'steps':len(plan['steps'])})
