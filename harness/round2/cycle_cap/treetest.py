"""Dikarya-style tree test of capped refinement: trimAl -gt 0.1, IQ-TREE GTR+F+G4 --alrt 1000.

usage: treetest.py run   ITER OUT JOB.fasta...   (2 parallel IQ-TREE workers)
       treetest.py score OUT
Alignments: ref = ITER/JOB.cap1000.fa (must match golden), cap2/cap3 = ITER/JOB.capN.fa when they differ.
OUT/JOB must not exist yet. The strong-clade contradiction counts add both directions: strong
clades of the first tree contradicted by the second, plus those of the second by the first.
"""
import hashlib, itertools, json, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tree import parse_newick, IndexedTree, incompatible, ZERO_THRESHOLD, SHALRT_THRESHOLD

TRIMAL = '/home/tree/miniforge3/bin/trimal'
IQTREE = '/opt/iqtree-3.1.4-dikarya1/bin/iqtree3'
SEEDS = (73111, 73129)
W = Path(__file__).resolve().parents[4]


def fasta(path):
    recs, name = [], None
    for line in open(path):
        line = line.rstrip('\n')
        if line.startswith('>'):
            recs.append([line[1:], []])
        elif line:
            recs[-1][1].append(line)
    return [(n, ''.join(s)) for n, s in recs]


def prepare(iterdir, out, job):
    """Write renamed (t0..tN, by alignment order) copies of each distinct alignment; return tasks."""
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    golden = {l.split()[1].lstrip('*').split('/')[-1]: l.split()[0] for l in open(W / 'bench/golden.sha256')}
    ref = iterdir / f'{job}.cap1000.fa'
    if sha(ref) != golden[job]:
        sys.exit(f'{ref}: not the golden alignment')
    names = [n for n, _ in fasta(ref)]
    variants = {'ref': ref}
    for cap in ('2', '3'):
        p = iterdir / f'{job}.cap{cap}.fa'
        if sha(p) != sha(ref) and all(sha(p) != sha(q) for q in variants.values()):
            variants['cap' + cap] = p
    tasks = []
    d = out / job; d.mkdir(parents=True)  # fails on a reused OUT: no stale trims or trees
    for tag, p in variants.items():
        recs = fasta(p)
        assert [n for n, _ in recs] == names, (job, tag)
        (d / f'{tag}.fa').write_text(''.join(f'>t{i}\n{s}\n' for i, (_, s) in enumerate(recs)))
        # trim here, once, before either seed's IQ-TREE run is scheduled
        subprocess.run(['nice', '-n', '19', TRIMAL, '-in', str(d / f'{tag}.fa'), '-out', str(d / f'{tag}.trim.fa'), '-gt', '0.1'],
                       check=True, capture_output=True)
        tasks += [(d, tag, seed) for seed in SEEDS]
    return tasks


def run(task):
    d, tag, seed = task
    prefix = d / f'{tag}.s{seed}'
    subprocess.run(['nice', '-n', '19', IQTREE, '-s', str(d / f'{tag}.trim.fa'), '-m', 'GTR+F+G4', '--alrt', '1000',
                    '-T', '1', '-seed', str(seed), '--prefix', str(prefix), '-redo', '-quiet'],
                   check=True, capture_output=True)
    return str(prefix)


def splits(path):
    t = IndexedTree(parse_newick(Path(path).read_text()), support_order=['shalrt'])
    keep = lambda e: e.length is None or e.length > ZERO_THRESHOLD
    s = t.splits(edge_filter=keep)
    strong = {m for m, e in s.items() if e.support.get('shalrt', 0) >= SHALRT_THRESHOLD}
    return t.taxa, t.universe, set(s), strong


def compare(a, b):
    ta, u, sa, stra = a; tb, _, sb, strb = b
    assert ta == tb
    n = len(ta)
    rf = len(sa ^ sb) / (2 * (n - 3))
    # strong splits of either tree contradicted (incompatible) by some split of the other
    contra = sum(any(incompatible(x, y, u) for y in sb) for x in stra) + \
             sum(any(incompatible(x, y, u) for y in sa) for x in strb)
    return rf, contra


def score(out):
    rows = []
    for d in sorted(p for p in out.iterdir() if p.is_dir()):
        tr = {(f.name.split('.s')[0], int(f.name.split('.s')[1].split('.')[0])): splits(f) for f in d.glob('*.treefile')}
        ref = [tr[('ref', s)] for s in SEEDS]
        seed_ref = compare(*ref)
        for tag in sorted({t for t, _ in tr} - {'ref'}):
            cap = [tr[(tag, s)] for s in SEEDS]
            seed_cap = compare(*cap)
            cross = [compare(r, c) for r, c in itertools.product(ref, cap)]
            paired = [compare(ref[i], cap[i]) for i in range(len(SEEDS))]
            seed_rf = max(seed_ref[0], seed_cap[0])
            mean_cross = sum(r for r, _ in cross) / len(cross)
            rows.append({'job': d.name, 'variant': tag, 'taxa': len(ref[0][0]),
                         'strong_ref': len(ref[0][3]),
                         'seed_rf_ref': round(seed_ref[0], 4), 'seed_rf_cap': round(seed_cap[0], 4),
                         'cross_rf_mean': round(mean_cross, 4), 'paired_rf': [round(r, 4) for r, _ in paired],
                         'seed_strong_contra': [seed_ref[1], seed_cap[1]],
                         'cross_strong_contra': [c for _, c in cross],
                         'moved_beyond_seed': mean_cross > seed_rf,
                         'strong_contra_beyond_seed': min(c for _, c in cross) > max(seed_ref[1], seed_cap[1])})
    for r in rows:
        print(json.dumps(r))
    (out / 'summary.json').write_text(json.dumps(rows, indent=1))


if __name__ == '__main__':
    if sys.argv[1] == 'run':
        iterdir, out = Path(sys.argv[2]), Path(sys.argv[3])
        tasks = [t for job in sys.argv[4:] for t in prepare(iterdir, out, job)]
        print(len(tasks), 'IQ-TREE runs', flush=True)
        with ThreadPoolExecutor(2) as ex:
            for i, p in enumerate(ex.map(run, tasks), 1):
                print(i, p, flush=True)
    else:
        score(Path(sys.argv[2]))
