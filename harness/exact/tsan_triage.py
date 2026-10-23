#!/usr/bin/env python3
"""Group ThreadSanitizer reports and list the races a branch adds over stock.

  tsan_triage.py --stock DIR... --branch DIR... [--json OUT]

Each DIR holds TSAN_OPTIONS=log_path=DIR/tsan output (tsan.<pid> files).  A
race is identified by its kind and, for each of the two accesses, the
function and file of the innermost frame plus the access type (read/write).
Line numbers are left out so that stock and branch reports of the same
upstream race compare equal.  Races present only in branch reports are
listed first, with one full example report each.
"""
import argparse
import collections
import json
import re
from pathlib import Path

HEADER = re.compile(r'WARNING: ThreadSanitizer: (.+?) \(pid=')
ACCESS = re.compile(r'^\s+(Previous )?(atomic )?(read|write|Read|Write)\b.*?by (thread T\d+|main thread)', re.I)
FRAME = re.compile(r'^\s+#(\d+) (\S+) (\S+?):(\d+)')


def reports(directory):
    for path in sorted(Path(directory).glob('tsan*')):
        text = path.read_text(errors='replace')
        for block in text.split('==================\n'):
            m = HEADER.search(block)
            if m:
                yield m.group(1), block


def signature(kind, block):
    accesses = []
    lines = block.splitlines()
    for i, line in enumerate(lines):
        m = ACCESS.match(line)
        if not m:
            continue
        what = ('atomic ' if m.group(2) else '') + m.group(3).lower()
        for nxt in lines[i + 1:i + 4]:
            f = FRAME.match(nxt)
            if f:
                accesses.append('%s %s (%s)' % (what, f.group(2), Path(f.group(3)).name))
                break
    if not accesses:
        for line in lines:
            f = FRAME.match(line)
            if f:
                accesses.append('%s (%s)' % (f.group(2), Path(f.group(3)).name))
                break
    return (kind, tuple(sorted(accesses)))


def collect(directories):
    counts, examples = collections.Counter(), {}
    for d in directories:
        for kind, block in reports(d):
            sig = signature(kind, block)
            counts[sig] += 1
            examples.setdefault(sig, block)
    return counts, examples


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--stock', nargs='+', required=True)
    p.add_argument('--branch', nargs='+', required=True)
    p.add_argument('--json', type=Path)
    a = p.parse_args()
    stock, _ = collect(a.stock)
    branch, examples = collect(a.branch)
    new = [s for s in branch if s not in stock]
    shared = [s for s in branch if s in stock]
    print('stock: %d reports, %d distinct; branch: %d reports, %d distinct; new on branch: %d'
          % (sum(stock.values()), len(stock), sum(branch.values()), len(branch), len(new)))
    for title, group in (('NEW ON BRANCH', new), ('ALSO IN STOCK', shared)):
        print('\n== %s ==' % title)
        for s in sorted(group, key=lambda s: -branch[s]):
            print('%5d  %s: %s' % (branch[s], s[0], ' | '.join(s[1])))
    for s in new:
        print('\n---- example: %s: %s\n%s' % (s[0], ' | '.join(s[1]), examples[s][:4000]))
    if a.json:
        a.json.write_text(json.dumps(dict(
            new=[dict(kind=s[0], accesses=s[1], count=branch[s]) for s in new],
            shared=[dict(kind=s[0], accesses=s[1], count=branch[s], stock_count=stock[s]) for s in shared],
            stock_only=[dict(kind=s[0], accesses=s[1], count=stock[s]) for s in stock if s not in branch]),
            indent=1) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
