"""Run the corpus L-INS-i jobs with --maxiterate 1000/3/2 (--thread 1); record child CPU, wall, output hash.

usage: itercap.py OUT short|long   (reads cycles.json beside this script; writes OUT/{short,long}.jsonl and outputs)

Stops on a MAFFT failure or an uncapped output that differs from the golden. The committed
records predate the sha256 field and those checks; every record has rc 0, and every cap-1000
run matched its golden.
"""
import hashlib, json, os, resource, subprocess, sys, time
from pathlib import Path
W = Path(__file__).resolve().parents[4]; OUT = Path(sys.argv[1]); which = sys.argv[2]  # 'short' or 'long'
MAFFT = '/opt/mafft-releases/mafft-7.526-opt9-c8ade5a0b948/bin/mafft'  # the opt9 release, not the switchable entry point
rows = json.load(open(Path(__file__).with_name('cycles.json')))  # (file, longest, nseq, opts, cycles run, converged, partition)
golden = {l.split()[1].lstrip('*').split('/')[-1]: l.split()[0] for l in open(W / 'bench/golden.sha256')}
sel = [r for r in rows if '--localpair' in r[3] and ((r[1] < 1000) == (which == 'short'))]
log = open(OUT / f'{which}.jsonl', 'x')  # a rerun needs a new OUT
for k, (name, longest, nseq, opts, cycles, conv, part) in enumerate(sel):
    caps = ['1000', '3', '2']
    if k % 2: caps.reverse()  # alternate order against drift
    for cap in caps:
        argv = [MAFFT, '--thread', '1'] + opts.split()
        argv[argv.index('--maxiterate') + 1] = cap
        argv.append(str(W / 'bench/data' / name))
        r0 = resource.getrusage(resource.RUSAGE_CHILDREN); t0 = time.monotonic()
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ, LC_ALL='C'))
        wall = time.monotonic() - t0; r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        if p.returncode:
            sys.exit(f'{name} cap {cap}: mafft exited {p.returncode}\n{p.stderr.decode(errors="replace")[-2000:]}')
        h = hashlib.sha256(p.stdout).hexdigest()
        if cap == '1000' and h != golden[name]:
            sys.exit(f'{name}: uncapped output differs from the golden')
        (OUT / f'{name}.cap{cap}.fa').write_bytes(p.stdout)
        log.write(json.dumps({'file': name, 'cap': int(cap), 'rc': p.returncode, 'cpu': (r1.ru_utime + r1.ru_stime) - (r0.ru_utime + r0.ru_stime),
                              'wall': wall, 'sha256': h, 'golden': h == golden[name], 'cycles': cycles, 'longest': longest, 'nseq': nseq, 'part': part}) + '\n'); log.flush()
