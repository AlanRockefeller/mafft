"""Resumable byte-identity case runner shared by the verification scripts.

A case is one MAFFT invocation whose stdout is compared with an expected hash
(or only required to finish cleanly when there is none).  Every finished
attempt is appended to a completion manifest (JSON lines) under a key that
covers the input path and hash, the installed binary hash, the options and the
MAFFT-relevant environment, so a rebuilt binary, an edited input or a changed
option never reuses an old result.

Rules, which the tests in test_caserun.py pin down:

* stdout and stderr go to temporary files that are renamed into place only
  after the process exits 0; any other result is kept as OUT.failed;
* every process runs in its own session and is killed as a group on timeout;
* a timeout, a nonzero exit, an empty output or a hash mismatch is a failure;
* on resume a cached pass counts only if its output still exists with the
  recorded hash and the recorded expected hash is still the expected hash;
  anything else counts as a failure ("invalid"), never as a skip;
* failures are sticky: a case that failed in any recorded attempt is counted
  as failed even if a later --retry attempt passes.
"""
import hashlib
import json
import os
import signal
import threading
import time
from pathlib import Path

#: Environment variables that can change what MAFFT does and so belong in the key.
ENV_PREFIXES = ('MAFFT_',)
ENV_NAMES = ('LC_ALL', 'LANG', 'OMP_NUM_THREADS')


def file_digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def install_digest(binary):
    """Hash of the launcher and, for an installed prefix, every program it can run.

    The mafft launcher is a shell script; the programs that produce the
    alignment live in PREFIX/libexec/mafft, so hashing the launcher alone would
    let a rebuilt binary reuse results from the previous one.
    """
    binary = Path(binary).resolve()
    parts = [('launcher', file_digest(binary))]
    libexec = binary.parent.parent / 'libexec' / 'mafft'
    if libexec.is_dir():
        parts += sorted((str(p.relative_to(libexec)), file_digest(p))
                        for p in libexec.rglob('*') if p.is_file())
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def write_json(path, value):
    """Write JSON atomically: a reader never sees a half-written file."""
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp-%d' % os.getpid())
    tmp.write_text(json.dumps(value, indent=1, sort_keys=True) + '\n')
    os.replace(tmp, path)


def relevant_env(env):
    return {k: v for k, v in sorted(env.items())
            if k.startswith(ENV_PREFIXES) or k in ENV_NAMES}


def case_key(**fields):
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def _kill_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _group_alive(pgid):
    """True while any non-zombie process is in group PGID (Linux /proc; else kill -0)."""
    if not os.path.isdir('/proc/self'):
        return _kill_group(pgid, 0)
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        try:
            with open('/proc/%s/stat' % entry) as stream:
                fields = stream.read().rsplit(')', 1)[1].split()
        except OSError:
            continue
        if int(fields[2]) == pgid and fields[0] != 'Z':
            return True
    return False


def run_group(argv, *, stdout, stderr, env, timeout, nice=None, grace=5.0, cwd=None, stdin=None):
    """Run argv in its own session; on timeout kill the whole process group.

    SIGTERM first, SIGKILL after GRACE seconds for anything still in the
    group.  Group members left behind by a leader that exited are killed and
    reported as leaked.  Returns returncode, timed_out, rusage (of the child,
    including the descendants it waited for), launch_error and leaked.
    """
    import subprocess
    preexec = (lambda: os.nice(nice)) if nice else None
    timed_out = threading.Event()
    try:
        proc = subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=stderr, env=env, cwd=cwd,
                                start_new_session=True, preexec_fn=preexec)
    except OSError as error:
        return dict(returncode=127, timed_out=False, usage=None, launch_error=str(error), leaked=False)

    def expire():
        timed_out.set()
        _kill_group(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not _kill_group(proc.pid, 0):
                return
            time.sleep(0.05)
        _kill_group(proc.pid, signal.SIGKILL)

    timer = threading.Timer(timeout, expire)
    timer.daemon = True
    timer.start()
    try:
        _, status, usage = os.wait4(proc.pid, 0)
    except BaseException:
        _kill_group(proc.pid, signal.SIGKILL)
        raise
    finally:
        timer.cancel()
    proc.returncode = os.waitstatus_to_exitcode(status)
    # A leader that exited may leave group members behind (for example a
    # killed launcher whose C program ignored SIGTERM).  Sweep them.
    leaked = _kill_group(proc.pid, 0)
    if leaked:
        _kill_group(proc.pid, signal.SIGKILL)
    # Do not return while killed members still run: the next case would overlap them.
    deadline = time.monotonic() + 30
    while _group_alive(proc.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return dict(returncode=proc.returncode, timed_out=timed_out.is_set(), usage=usage,
                launch_error=None, leaked=leaked)


def run_case(argv, out, *, env, timeout, nice=None, err=None, grace=5.0, cwd=None):
    """Run argv with stdout to OUT, atomically.  Returns a result dict.

    The child gets its own session, so a timeout kills MAFFT's whole process
    tree (the launcher script and the C programs it starts), not just the
    shell.  CPU time is the child's rusage, which includes the descendants it
    waited for.
    """
    out = Path(out)
    err = Path(err) if err else Path(str(out) + '.err')
    tag = '.tmp-%d-%d' % (os.getpid(), threading.get_ident())
    tmp_out, tmp_err = Path(str(out) + tag), Path(str(err) + tag)
    for stale in (out, err, Path(str(out) + '.failed'), Path(str(err) + '.failed')):
        if stale.exists():
            stale.unlink()
    started = time.monotonic()
    with open(tmp_out, 'wb') as stdout, open(tmp_err, 'wb') as stderr:
        g = run_group(argv, stdout=stdout, stderr=stderr, env=env, timeout=timeout, nice=nice,
                      grace=grace, cwd=cwd)
        if g['launch_error']:
            stderr.write(('launch failed: %s\n' % g['launch_error']).encode())
    usage = g['usage']
    result = dict(argv=[str(x) for x in argv], returncode=g['returncode'],
                  timed_out=g['timed_out'], timeout=timeout, wall=time.monotonic() - started,
                  cpu=(usage.ru_utime + usage.ru_stime) if usage else None,
                  maxrss_kib=usage.ru_maxrss if usage else None,
                  launch_error=bool(g['launch_error']), leaked_processes=g['leaked'])
    clean = result['returncode'] == 0 and not result['timed_out'] and not result['leaked_processes']
    os.replace(tmp_out, out if clean else Path(str(out) + '.failed'))
    os.replace(tmp_err, err if clean else Path(str(err) + '.failed'))
    result['output'] = str(out if clean else Path(str(out) + '.failed'))
    result['stderr'] = str(err if clean else Path(str(err) + '.failed'))
    result['output_sha256'] = file_digest(result['output'])
    result['output_bytes'] = os.path.getsize(result['output'])
    return result


class Manifest:
    """Append-only JSON-lines completion manifest, one record per attempt."""

    def __init__(self, path):
        self.path = Path(path)
        self.records = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = self.path.read_bytes() if self.path.exists() else b''
        complete, _, tail = data.rpartition(b'\n')
        if tail:
            # A run killed while appending leaves a partial last record; that attempt
            # never finished.  Keep the bytes for inspection and cut the file back.
            side = self.path.with_name(self.path.name + '.partial-%d' % time.time_ns())
            side.write_bytes(tail)
            with open(self.path, 'r+b') as stream:
                stream.truncate(len(complete) + 1 if complete else 0)
        for number, line in enumerate(complete.split(b'\n') if complete else [], 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                raise SystemExit('%s:%d: corrupt manifest record; preserve it and use a new output'
                                 % (self.path, number))
            self.records.setdefault(record['key'], []).append(record)
        self._stream = open(self.path, 'ab')

    def attempts(self, key):
        return self.records.get(key, [])

    def append(self, record):
        self._stream.write(json.dumps(record, sort_keys=True).encode() + b'\n')
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self.records.setdefault(record['key'], []).append(record)

    def close(self):
        self._stream.close()


def verdict(result, expected):
    """Failure reason for a fresh result, or None for a pass."""
    if result['launch_error']:
        return 'launch error'
    if result['timed_out']:
        return 'timeout after %gs' % result['timeout']
    if result['returncode'] != 0:
        return 'exit status %d' % result['returncode']
    if result['leaked_processes']:
        return 'processes left running after exit'
    if not result['output_bytes']:
        return 'empty output'
    if expected is not None and result['output_sha256'] != expected:
        return 'output differs from expected'
    return None


def recheck(record, expected):
    """Failure reason for a cached passing record, or None if it still holds."""
    output = Path(record['output'])
    if not output.exists():
        return 'cached output is missing'
    if file_digest(output) != record['output_sha256']:
        return 'cached output changed since it was recorded'
    if record.get('expected_sha256') != expected:
        return 'expected hash changed since the case was recorded'
    return None


class Tally:
    """Pass/fail counts per category, with every failure listed."""

    def __init__(self):
        self.counts = {}
        self.failures = []

    def add(self, category, name, reason, cached=False):
        c = self.counts.setdefault(category, dict(pass_=0, fail=0, cached=0))
        c['cached'] += cached
        if reason is None:
            c['pass_'] += 1
        else:
            c['fail'] += 1
            self.failures.append(dict(category=category, case=name, reason=reason))

    @property
    def failed(self):
        return sum(c['fail'] for c in self.counts.values())

    def report(self, stream=None):
        import sys
        stream = stream or sys.stdout
        for category in sorted(self.counts):
            c = self.counts[category]
            print('%-40s pass %5d  fail %4d  (from manifest %d)' % (category, c['pass_'], c['fail'], c['cached']),
                  file=stream)
        for f in self.failures:
            print('FAIL %s %s: %s' % (f['category'], f['case'], f['reason']), file=stream)
        print('total failures: %d' % self.failed, file=stream)

    def as_dict(self):
        return dict(counts={k: dict(passed=v['pass_'], failed=v['fail'], from_manifest=v['cached'])
                            for k, v in self.counts.items()},
                    failures=self.failures, total_failures=self.failed)


def execute(case, manifest, tally, *, timeout, retry=False, nice=None, log=None):
    """Run or re-verify one case and add it to the tally.  Returns the reason or None.

    case: dict with name, category, argv, env, out, key, expected (sha256 or None)
    and optional in_scope (False: recorded, never counted as a failure) and
    record (extra fields stored with the attempt).
    """
    attempts = manifest.attempts(case['key'])
    earlier = [a for a in attempts if a['failure'] is not None]
    passed = [a for a in attempts if a['failure'] is None]
    in_scope = case.get('in_scope', True)
    category = case['category'] if in_scope else case['category'] + ' (out of scope)'
    if attempts and not (retry and (earlier or not passed)):
        if earlier:
            reason = 'earlier attempt failed: ' + earlier[0]['failure']
        else:
            reason = recheck(passed[-1], case['expected'])
        tally.add(category, case['name'], reason if in_scope else None, cached=True)
        return reason
    result = run_case(case['argv'], case['out'], env=case['env'], timeout=timeout, nice=nice)
    reason = verdict(result, case['expected'])
    if case.get('stderr_markers'):
        text = Path(result['stderr']).read_bytes()
        result['markers'] = {name: marker in text for name, marker in case['stderr_markers'].items()}
    record = dict(key=case['key'], case=case['name'], category=case['category'],
                  expected_sha256=case['expected'], failure=reason, in_scope=in_scope,
                  finished_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                  **case.get('record', {}), **result)
    manifest.append(record)
    if log:
        log(record)
    if reason is None and earlier:
        reason = 'earlier attempt failed: ' + earlier[0]['failure']
    tally.add(category, case['name'], reason if in_scope else None)
    return reason
