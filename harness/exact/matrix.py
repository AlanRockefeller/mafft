#!/usr/bin/env python3
"""Byte-for-byte regression matrix: a candidate MAFFT build against stock.

Both builds must come from the same compiler and flags (see
harness/verify-against-stock.sh).  Every case runs the candidate and the
stock build with the same options and compares stdout bytes and exit status:

  identical stdout, both exit 0              pass
  both exit with the same nonzero status     pass (a matching rejection)
  anything else, or a timeout                fail

Threaded cases (DNA L-INS-i, G-INS-i and E-INS-i, the ordered-refinement
scope) run the candidate with --thread N and compare it with stock --thread 1.
With --test-build (a -DMAFFT_ORDERED_TEST build of the candidate) they also run
under random delays and with MAFFT_ORDERED_TEST_UNSAFE forcing the one-worker
rerun at several commit positions.

Runs are recorded in OUT/manifest.jsonl (caserun.py): a restart reuses a run
only if its record checks out, and a run is never repeated within one OUT, so
use a new OUT to try again.  OUT/summary.json has pass/fail counts per
category.  Exit status: 0 if every case passed, 1 otherwise.

  matrix.py --stock S/bin/mafft --candidate C/bin/mafft --out OUT [--full]
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import caserun  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / 'test' / 'exact'

STRATEGIES = {
    'fftns2': [],
    'retree1': ['--retree', '1'],
    'fftnsi': ['--maxiterate', '1000'],
    'nwns2': ['--retree', '2', '--nofft'],
    'nwnsi': ['--retree', '2', '--nofft', '--maxiterate', '1000'],
    'linsi1': ['--localpair'],
    'linsi': ['--localpair', '--maxiterate', '1000'],
    'ginsi': ['--globalpair', '--maxiterate', '1000'],
    'einsi': ['--genafpair', '--maxiterate', '1000', '--ep', '0'],
    'auto': ['--auto'],
    'linsi-op': ['--localpair', '--maxiterate', '1000', '--op', '2.5', '--ep', '0.4'],
    'linsi-lop': ['--localpair', '--maxiterate', '200', '--lop', '-1.2', '--lexp', '-0.3'],
}

# name: (options, input kinds it applies to).  {add}, {frag}, {long}, {base} are auxiliary files.
OPTIONS = {
    'adjustdirection': (['--adjustdirection'], 'dna'),
    'adjustdirection-linsi': (['--adjustdirection', '--localpair', '--maxiterate', '1000'], 'dna'),
    'adjustdirectionaccurately': (['--adjustdirectionaccurately'], 'dna'),
    'parttree': (['--parttree'], 'any'),
    'dpparttree': (['--dpparttree'], 'any'),
    '6merpair': (['--6merpair'], 'any'),
    'clustalout': (['--localpair', '--maxiterate', '1000', '--clustalout'], 'any'),
    'reorder': (['--localpair', '--maxiterate', '1000', '--reorder'], 'any'),
    'kimura1': (['--kimura', '1'], 'dna'),
    'amino': (['--amino', '--localpair', '--maxiterate', '1000'], 'aa'),
    'bl30': (['--bl', '30', '--localpair', '--maxiterate', '1000'], 'aa'),
    'jtt200': (['--jtt', '200', '--maxiterate', '1000'], 'aa'),
    'anysymbol': (['--anysymbol', '--localpair', '--maxiterate', '1000'], 'any'),
    'fmodel': (['--fmodel', '--localpair', '--maxiterate', '1000'], 'any'),
    'weighti2': (['--weighti', '2', '--localpair', '--maxiterate', '1000'], 'any'),
    'bestfirst': (['--bestfirst', '--localpair', '--maxiterate', '1000'], 'any'),
    'oldgenafpair': (['--oldgenafpair', '--maxiterate', '1000'], 'any'),
    'add': (['--add', '{add}', '{base}'], 'aux'),
    'add-linsi': (['--localpair', '--maxiterate', '1000', '--add', '{add}', '{base}'], 'aux'),
    'addfragments': (['--addfragments', '{frag}', '{base}'], 'aux'),
    'addfragments-keeplength': (['--addfragments', '{frag}', '--keeplength', '{base}'], 'aux'),
    'addlong': (['--addlong', '{long}', '{base}'], 'aux'),
    'addfull': (['--addfull', '{add}', '{base}'], 'aux'),
    'seed': (['--seed', '{base}', '--localpair', '--maxiterate', '1000', '{add}'], 'aux'),
}

ORDERED = ['linsi', 'ginsi', 'einsi']

FAST = dict(
    strategy_inputs=['dna12small', 'aa12small', 'tiny', 'one'],
    strategies=list(STRATEGIES),
    # Mid-sized inputs: the small ones above can miss a wrong rounding class (a
    # clang FMA build without -DMAFFT_STOCK_FMA=1 matches them but not dna40).
    rounding_inputs=['dna40'],
    rounding_strategies=['fftns2', 'fftnsi', 'linsi'],
    option_inputs=['dna12small', 'aa12small'],
    options=list(OPTIONS),
    default_thread_inputs=['dna12small', 'aa12small'],
    thread_inputs=['dna12small', 'dna20alldup'],
    threads=[2, 3, 4],
    jitter_repeats=2,
    unsafe=['1', '5', '40'],
)
FULL = dict(
    strategy_inputs=['dna40', 'dna80dup', 'dna60rc', 'dna50amb', 'dna120frag', 'dna40outlier', 'dna20alldup',
                     'dna30near', 'aa60', 'aa40div', 'dna6x5k', 'dna12small', 'aa12small', 'tiny', 'one',
                     'gappedu', 'sample'],
    strategies=list(STRATEGIES),
    rounding_inputs=[],
    rounding_strategies=[],
    option_inputs=['dna40', 'dna60rc', 'dna50amb', 'aa60', 'aa40div', 'dna12small', 'aa12small'],
    options=list(OPTIONS),
    default_thread_inputs=['dna40', 'dna80dup', 'aa60', 'sample'],
    thread_inputs=['dna40', 'dna80dup', 'dna60rc', 'dna120frag', 'dna20alldup', 'dna30near', 'dna40outlier'],
    threads=[2, 3, 4],
    jitter_repeats=3,
    unsafe=['1', '2', '5', '40', '400'],
)


def kind(name):
    return 'aa' if name.startswith('aa') else 'dna'


def input_path(name):
    return ROOT / 'test' / 'sample' if name == 'sample' else FIX / (name + '.fa')


class Runner:
    """Runs (binary, argv, env) once per OUT and remembers the result."""

    def __init__(self, out, timeout, nice):
        self.out, self.timeout, self.nice = out, timeout, nice
        self.manifest = caserun.Manifest(out / 'manifest.jsonl')
        self.lock = threading.Lock()
        self.busy = {}
        self.digests = {}
        self.inputs = {}
        (out / 'runs').mkdir(parents=True, exist_ok=True)

    def digest(self, binary):
        if binary not in self.digests:
            self.digests[binary] = caserun.install_digest(binary)
        return self.digests[binary]

    def key(self, binary, args, env):
        files = {a: self.input_digest(a) for a in args if os.path.isfile(a)}
        # the timeout too: a case that timed out may pass under a longer limit, and the reverse
        return caserun.case_key(binary=self.digest(binary), args=args, files=files,
                                env=caserun.relevant_env(env), timeout=self.timeout)

    def input_digest(self, path):
        if path not in self.inputs:
            self.inputs[path] = caserun.file_digest(path)
        return self.inputs[path]

    def run(self, binary, args, env, label):
        """Return a run record: fresh, or a manifest record whose output still checks out.

        Cases share stock references, so concurrent requests for one key wait
        for the first instead of running it twice.
        """
        key = self.key(binary, args, env)
        with self.lock:
            busy = self.busy.setdefault(key, threading.Lock())
        with busy:
            return self._run(key, binary, args, env, label)

    def _run(self, key, binary, args, env, label):
        with self.lock:
            old = self.manifest.attempts(key)
        if old:
            record = dict(old[-1])
            problem = caserun.recheck(record, record.get('expected_sha256'))
            record['invalid'] = problem
            record['cached'] = True
            return record
        out = self.out / 'runs' / (key[:16] + '.out')
        result = caserun.run_case([binary, *args], out, env=env, timeout=self.timeout, nice=self.nice)
        record = dict(key=key, label=label, expected_sha256=None, failure=None, **result)
        with self.lock:
            self.manifest.append(record)
        record['invalid'] = None
        record['cached'] = False
        return record


CLUSTAL_HEADER = re.compile(rb'CLUSTAL format alignment by MAFFT [^\n]*\(v7\.526[^)\n]*\)\n')


def clustal_header_only(cand, ref):
    """True if the outputs differ only in the version on the CLUSTAL header line."""
    a, b = Path(cand['output']).read_bytes(), Path(ref['output']).read_bytes()
    return (a.split(b'\n', 1)[1:] == b.split(b'\n', 1)[1:]
            and bool(CLUSTAL_HEADER.match(a)) and bool(CLUSTAL_HEADER.match(b)))


# ASAN_OPTIONS=exitcode=86 and UBSAN_OPTIONS=exitcode=87 (verify-against-stock.sh, CI).
SANITIZER_EXIT = (86, 87)


def outcome(cand, ref):
    """How a passing comparison passed: identical output, or both builds exiting alike."""
    if cand['returncode'] == 0:
        return 'identical'
    if cand['returncode'] in SANITIZER_EXIT:
        return 'matching sanitizer abort'
    return 'matching rejection'


def compare(cand, ref, allow_clustal_version=False):
    """Failure reason for a candidate run against its stock reference, or None."""
    for who, r in (('candidate', cand), ('stock', ref)):
        if r.get('invalid'):
            return '%s: %s' % (who, r['invalid'])
        if r['timed_out']:
            return '%s timed out after %gs' % (who, r['timeout'])
        if r['launch_error']:
            return '%s failed to launch' % who
        if r['leaked_processes']:
            return '%s left processes running' % who
    if cand['returncode'] != ref['returncode']:
        return 'exit status %d, stock %d' % (cand['returncode'], ref['returncode'])
    if cand['returncode'] == 0 and cand['output_sha256'] != ref['output_sha256']:
        if allow_clustal_version and clustal_header_only(cand, ref):
            return None
        return 'stdout differs from stock'
    if cand['returncode'] == 0 and not cand['output_bytes']:
        return 'both outputs empty'
    return None


# Auxiliary --add/--seed inputs (committed; see test/exact/README.md).
AUX = dict(base=str(FIX / 'add-base.aln'), add=str(FIX / 'add-new.fa'),
           frag=str(FIX / 'add-frag.fa'), long=str(FIX / 'add-long.fa'))


def build_cases(plan, stock, cand, test_build, corpus):
    """Yield (category, name, candidate binary, candidate args, env extra, stock args)."""
    for s in plan['strategies']:
        for name in plan['strategy_inputs']:
            opts = STRATEGIES[s] + [str(input_path(name))]
            yield ('strategy %s t1' % s, name, cand, ['--thread', '1', *opts], {}, ['--thread', '1', *opts])
    for s in plan['rounding_strategies']:
        for name in plan['rounding_inputs']:
            opts = STRATEGIES[s] + [str(input_path(name))]
            yield ('rounding %s t1' % s, name, cand, ['--thread', '1', *opts], {}, ['--thread', '1', *opts])
    for name in plan['default_thread_inputs']:
        for s in ('fftns2', 'fftnsi', 'linsi'):
            opts = STRATEGIES[s] + [str(input_path(name))]
            yield ('default threads %s' % s, name, cand, opts, {}, opts)
    for o in plan['options']:
        opts, applies = OPTIONS[o]
        names = ['aux'] if applies == 'aux' else [n for n in plan['option_inputs']
                                                  if applies == 'any' or kind(n) == applies]
        for name in names:
            args = [x.format(**AUX) for x in opts] + ([] if applies == 'aux' else [str(input_path(name))])
            yield ('option %s' % o, name, cand, ['--thread', '1', *args], {}, ['--thread', '1', *args])
    for s in ORDERED:
        for name in plan['thread_inputs']:
            opts = STRATEGIES[s] + [str(input_path(name))]
            for t in plan['threads']:
                yield ('ordered %s t%d' % (s, t), name, cand, ['--thread', str(t), *opts], {},
                       ['--thread', '1', *opts])
            if test_build:
                for t in plan['threads']:
                    for rep in range(plan['jitter_repeats']):
                        # The repeat number is in the environment so each repeat is its own run.
                        yield ('jitter %s t%d' % (s, t), '%s.r%d' % (name, rep), test_build,
                               ['--thread', str(t), *opts], {'MAFFT_MATRIX_REPEAT': str(rep)},
                               ['--thread', '1', *opts])
                for n in plan['unsafe']:
                    yield ('unsafe-fallback %s' % s, '%s.u%s' % (name, n), test_build,
                           ['--thread', '2', *opts], {'MAFFT_ORDERED_TEST_UNSAFE': n},
                           ['--thread', '1', *opts])
    for f in corpus:
        opts = [*f['production_options'], f['path']]
        yield ('corpus t1', f['file'], cand, ['--thread', '1', *opts], {}, ['--thread', '1', *opts])
        if '--localpair' in f['production_options']:
            yield ('corpus ordered t2', f['file'], cand, ['--thread', '2', *opts], {}, ['--thread', '1', *opts])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--stock', required=True, help='stock mafft launcher (PREFIX/bin/mafft)')
    p.add_argument('--candidate', required=True, help='candidate mafft launcher, same compiler and flags')
    p.add_argument('--test-build', help='candidate built with -DMAFFT_ORDERED_TEST (jitter, forced fallback)')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--full', action='store_true', help='the full matrix (hours) instead of the fast subset')
    p.add_argument('--corpus', type=Path, help='production-corpus.json; adds its inputs (with --data)')
    p.add_argument('--data', type=Path)
    p.add_argument('--only', help='regular expression on "category input"')
    p.add_argument('--jobs', type=int, default=1, help='runs at once (threaded cases are not split)')
    p.add_argument('--timeout', type=float, default=1800, help='seconds per MAFFT run')
    p.add_argument('--nice', type=int, default=10)
    p.add_argument('--allow-clustal-version', action='store_true',
                   help='accept CLUSTAL output whose only difference is the version on line 1')
    a = p.parse_args()
    plan = FULL if a.full else FAST
    stock, cand = str(Path(a.stock).resolve()), str(Path(a.candidate).resolve())
    test_build = str(Path(a.test_build).resolve()) if a.test_build else None
    corpus = []
    if a.corpus:
        if not a.data:
            p.error('--corpus needs --data')
        corpus = [dict(f, path=str((a.data / f['file']).resolve()))
                  for f in json.loads(a.corpus.read_text())['files']]
    env = {k: v for k, v in os.environ.items() if k != 'MAFFT_BINARIES'
           and not k.startswith(('MAFFT_ORDERED', 'MAFFT_NO'))}
    env.update(LC_ALL='C', LANG='C')
    a.out.mkdir(parents=True, exist_ok=True)
    runner = Runner(a.out, a.timeout, a.nice)
    tally = caserun.Tally()
    cases = [c for c in build_cases(plan, stock, cand, test_build, corpus)
             if not a.only or re.search(a.only, c[0] + ' ' + c[1])]
    print('%d cases' % len(cases), flush=True)
    results = []

    def one(case):
        category, name, binary, args, extra, stock_args = case
        ref = runner.run(stock, stock_args, env, 'stock ' + category + ' ' + name)
        got = runner.run(binary, args, dict(env, **extra), category + ' ' + name)
        return case, got, ref

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        for n, (case, got, ref) in enumerate(pool.map(one, cases), 1):
            category, name = case[0], case[1]
            reason = compare(got, ref, a.allow_clustal_version)
            tally.add(category, name, reason, cached=got['cached'] and ref['cached'])
            results.append(dict(category=category, input=name, candidate=got['key'], stock=ref['key'],
                                returncode=got['returncode'], failure=reason,
                                outcome=None if reason else outcome(got, ref),
                                candidate_cpu=got['cpu'], stock_cpu=ref['cpu'],
                                candidate_wall=got['wall'], stock_wall=ref['wall']))
            print('%5d/%d %-36s %-22s %s' % (n, len(cases), category, name,
                                             'ok' if reason is None else 'FAIL ' + reason), flush=True)
    runner.manifest.close()
    # Matching aborts are stock parity, not clean runs: listed apart so a summary never hides them.
    aborts = ['%s %s' % (r['category'], r['input']) for r in results if r['outcome'] == 'matching sanitizer abort']
    rejections = ['%s %s' % (r['category'], r['input']) for r in results if r['outcome'] == 'matching rejection']
    caserun.write_json(a.out / 'summary.json', dict(tally.as_dict(), mode='full' if a.full else 'fast',
                                                     matching_sanitizer_aborts=aborts, matching_rejections=rejections,
                                                     allow_clustal_version=a.allow_clustal_version,
                                                     stock=stock, candidate=cand, test_build=test_build,
                                                     stock_sha256=runner.digest(stock),
                                                     candidate_sha256=runner.digest(cand)))
    caserun.write_json(a.out / 'comparisons.json', results)
    tally.report()
    for title, group in (('matching rejections (same nonzero exit in both builds)', rejections),
                         ('matching sanitizer aborts (stock parity, not a clean run)', aborts)):
        if group:
            print('%s: %d: %s' % (title, len(group), ', '.join(group)))
    return 1 if tally.failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
