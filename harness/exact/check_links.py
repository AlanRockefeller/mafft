#!/usr/bin/env python3
"""Check the relative links and anchors of every tracked Markdown file.

  check_links.py [FILE.md ...]      (default: git ls-files '*.md')

Reports links to missing files, links to '#anchors' that no heading of the
target produces (GitHub's slug rules), and '[' ... '](' constructs that do
not close.  External http(s) and mailto links are not fetched.  Exit status
1 if anything is reported.
"""
import re
import subprocess
import sys
from pathlib import Path

LINK = re.compile(r'(?<!!)\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|!\[[^\]]*\]\(([^)\s]+)\)')
HEADING = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
FENCE = re.compile(r'^\s*(```|~~~)')


def slug(text):
    text = re.sub(r'`([^`]*)`', r'\1', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = text.strip().lower()
    text = re.sub(r'[^\w\- ]', '', text)
    return text.replace(' ', '-')


def lines_outside_code(path):
    inside = False
    for n, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
        if FENCE.match(line):
            inside = not inside
            continue
        if not inside:
            yield n, re.sub(r'`[^`]*`', '', line)


def anchors(path, cache={}):
    if path not in cache:
        seen, out = {}, set()
        for _, line in lines_outside_code(path):
            m = HEADING.match(line)
            if m:
                s = slug(m.group(2))
                k = seen.get(s, 0)
                out.add(s if k == 0 else '%s-%d' % (s, k))
                seen[s] = k + 1
        for m in re.finditer(r'<a\s+(?:name|id)="([^"]+)"', path.read_text(errors='replace')):
            out.add(m.group(1))
        cache[path] = out
    return cache[path]


def check(path):
    problems = []
    for n, line in lines_outside_code(path):
        for m in LINK.finditer(line):
            target = m.group(2) or m.group(3)
            if re.match(r'^[a-z][a-z0-9+.-]*:', target, re.I):
                continue
            file_part, _, frag = target.partition('#')
            dest = (path.parent / file_part).resolve() if file_part else path.resolve()
            if file_part and not dest.exists():
                problems.append('%s:%d: missing target %s' % (path, n, target))
                continue
            if frag and dest.suffix == '.md' and frag not in anchors(dest):
                problems.append('%s:%d: no heading for #%s in %s' % (path, n, frag, file_part or path.name))
        stripped = LINK.sub('', line)
        if re.search(r'\[[^\]]*$', stripped) and '](' not in stripped and re.search(r'\[[^\]]*\(', stripped):
            problems.append('%s:%d: unclosed link: %s' % (path, n, line.strip()[:80]))
    return problems


def main():
    files = [Path(f) for f in sys.argv[1:]] or [
        Path(f) for f in subprocess.check_output(['git', 'ls-files', '*.md'], text=True).split()]
    problems = [p for f in files for p in check(f)]
    for p in problems:
        print(p)
    print('%d files, %d problems' % (len(files), len(problems)))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
