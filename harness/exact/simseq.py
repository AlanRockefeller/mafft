#!/usr/bin/env python3
"""simseq.py SEED NSEQ ROOTLEN dna|aa [dup=F rc=F n=F lower=1 iupac=F rate=F frag=F indel=F outlier=F] > out.fa
Random-tree sequence family with substitutions and indels."""
import random, sys
a = sys.argv[1:]; seed, nseq, rootlen, typ = int(a[0]), int(a[1]), int(a[2]), a[3]
opt = dict(x.split('=') for x in a[4:]); f = lambda k, d: float(opt.get(k, d))
rng = random.Random(seed); alpha = 'ACGT' if typ == 'dna' else 'ARNDCQEGHILKMFPSTWYV'
rate, indel = f('rate', 0.08), f('indel', 0.15)
def mutate(s, r):
    out, i = [], 0
    while i < len(s):
        x = rng.random()
        if x < r * indel / 2: i += rng.randint(1, 6); continue
        if x < r * indel: out.extend(rng.choice(alpha) for _ in range(rng.randint(1, 6)))
        out.append(rng.choice(alpha) if rng.random() < r else s[i]); i += 1
    return ''.join(out)
seqs = [''.join(rng.choice(alpha) for _ in range(rootlen))]
while len(seqs) < nseq:
    k = rng.randrange(len(seqs)); p = seqs[k]
    seqs[k] = mutate(p, rate * rng.uniform(0.3, 1.5)); seqs.append(mutate(p, rate * rng.uniform(0.3, 1.5)))
comp = str.maketrans('ACGTacgtNn', 'TGCAtgcaNn')
for i, s in enumerate(seqs):
    if rng.random() < f('outlier', 0): s = ''.join(rng.choice(alpha) for _ in range(len(s)))
    if rng.random() < f('frag', 0) and len(s) > 40:
        b = rng.randrange(len(s) // 2); s = s[b:b + rng.randint(20, len(s) - b)]
    if rng.random() < f('n', 0):
        b = rng.randrange(len(s)); s = s[:b] + ('N' if typ == 'dna' else 'X') * rng.randint(1, 30) + s[b:]
    if typ == 'dna' and rng.random() < f('iupac', 0):
        s = ''.join(c if rng.random() > 0.03 else rng.choice('RYSWKMBDHVN') for c in s)
    if typ == 'dna' and rng.random() < f('rc', 0): s = s.translate(comp)[::-1]
    if opt.get('lower') == '1' and rng.random() < 0.3: s = s.lower()
    seqs[i] = s
for _ in range(int(round(f('dup', 0) * nseq))): seqs[rng.randrange(nseq)] = seqs[rng.randrange(nseq)]
rng.shuffle(seqs)
for i, s in enumerate(seqs):
    print('>s%d_%d' % (i, rng.randrange(10**6)))
    for j in range(0, len(s), 60): print(s[j:j + 60])
