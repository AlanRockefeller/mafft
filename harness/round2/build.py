#!/usr/bin/env python3
"""Build a source snapshot into a new private directory; never install system-wide."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--cc',default='gcc');ap.add_argument('--flags',default='-O3 -march=native -ffp-contract=off -g')
    ap.add_argument('--jobs',type=int,default=2);args=ap.parse_args()
    dst=args.output.resolve();dst.mkdir(parents=True,exist_ok=False)
    source=dst/'source';prefix=dst/'install'
    shutil.copytree(args.source.resolve(),source,ignore=shutil.ignore_patterns('.git','*.o','*.gcda','*.gcno','__pycache__','bin','binaries','scripts'))
    (source/'scripts').mkdir(exist_ok=True);(source/'binaries').mkdir(exist_ok=True)
    core=source/'core';cc=shutil.which(args.cc) if not Path(args.cc).is_absolute() else args.cc
    if not cc:raise SystemExit('compiler unavailable')
    commands=[['make','-C',str(core),'clean'],
              ['make','-C',str(core),'-j'+str(args.jobs),'CC='+cc,'CFLAGS='+args.flags],
              ['make','-C',str(core),'-W','mafft.tmpl','mafft','PREFIX='+str(prefix)],
              ['make','-C',str(core),'install','CC='+cc,'CFLAGS='+args.flags,'PREFIX='+str(prefix),'STRIP=true']]
    hashes={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in core.iterdir() if p.is_file() and (p.suffix in ['.c','.h','.tmpl'] or p.name=='Makefile')}
    metadata={'source':str(args.source.resolve()),'source_sha256':hashes,'compiler':subprocess.check_output([cc,'--version'],text=True),
              'flags':args.flags,'commands':commands,'prefix':str(prefix),'argv':os.sys.argv}
    (dst/'build.json').write_text(json.dumps(metadata,indent=2)+'\n')
    with (dst/'build.log').open('x') as log:
        start=time.monotonic()
        for command in commands:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    manifest={str(p.relative_to(prefix)):hashlib.sha256(p.read_bytes()).hexdigest() for p in prefix.rglob('*') if p.is_file()}
    (dst/'binary-sha256.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'prefix':str(prefix),'elapsed':time.monotonic()-start,'files':len(manifest)}))

if __name__=='__main__':main()
