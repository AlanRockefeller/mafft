#!/usr/bin/env python3
"""Stage the audited Dikarya5 build, verify it, then atomically activate MAFFT.

Dry-run by default. Requires Python 3.10+, Linux, and no third-party modules.
The public entrypoint is explicit; pipeline arguments are passed through.
"""

import argparse
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid


SOURCE_COMMIT = '9f3a58408f2036593e4907263f2fbcaa5241ea65'
VERSION = 'v7.526-opt8-dikarya5 (2024/Apr/26)'
BUILD_SHA = '680743b3060648b6c573b425bcef57607ecf9b631b1660efb90c0aa130fa9cbe'
INSTALL_SHA = 'c81eb9794e1476a986ddfc6a21823b27a542c2f1d565fc873e56df7c04a94066'
CORPUS_SHA = '65cb78000f3d7315bdef8906ba273317242a102b1e3c958b1f0e18bb1f1999b7'
GOLDEN_SHA = 'c1313b0d4696313bbefc1a6b453654be32e6f73221557cd5b658fd11053ba444'
RELEASE_NAME = 'mafft-7.526-opt8-dikarya5-' + INSTALL_SHA[:12]
SCHEMA = 'mafft-production-deployment-v1'
SMOKE_INPUTS = ('1msv.fasta', 'buh4.fasta', 't4lr.fasta', 'icv1.fasta')
THREADS = {'A': ['--thread', '1'], 'F': ['--thread', '2', '--threadit', '1'], 'O': ['--thread', '2']}
STRATEGY_ALIASES = ('einsi', 'fftns', 'fftnsi', 'ginsi', 'linsi', 'nwns', 'nwnsi',
                    'mafft-einsi', 'mafft-fftns', 'mafft-fftnsi', 'mafft-ginsi',
                    'mafft-linsi', 'mafft-nwns', 'mafft-nwnsi', 'mafft-qinsi', 'mafft-xinsi')
# Ordered two-thread refinement reproduces one-thread output only for L-INS-i-style runs.
ORDERED_OPTION = '--localpair'
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
WORKSPACE = Path(__file__).resolve().parents[3]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_pinned(path, expected):
    if sha(path) != expected:
        raise ValueError('Audited checksum mismatch: ' + str(path))
    return Path(path).read_bytes()


def relative(name):
    if not isinstance(name, str):
        raise ValueError('Unsafe manifest path: ' + repr(name))
    p = PurePosixPath(name)
    if not name or p.is_absolute() or any(x in ('', '.', '..') for x in name.split('/')):
        raise ValueError('Unsafe manifest path: ' + repr(name))
    return Path(*p.parts)


def write_json(path, record):
    with path.open('x') as stream:
        json.dump(record, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def entry_path(value):
    # Resolve parents only: resolving the final symlink would replace its target.
    value = Path(os.path.abspath(Path(value).expanduser()))
    if value.name != 'mafft':
        raise ValueError('--entrypoint must name the mafft command, not a strategy alias')
    return value.parent.resolve() / value.name


def snapshot(path):
    try:
        s = path.lstat()
    except FileNotFoundError:
        return {'kind': 'absent'}
    result = {'mode': stat.S_IMODE(s.st_mode), 'uid': s.st_uid, 'gid': s.st_gid}
    if stat.S_ISLNK(s.st_mode):
        return dict(result, kind='symlink', target=os.readlink(path))
    if stat.S_ISREG(s.st_mode):
        return dict(result, kind='file', sha256=sha(path), size=s.st_size)
    raise ValueError('Entrypoint must be a regular file, symlink, or absent: ' + str(path))


@contextmanager
def locked(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another deployment/rollback holds ' + str(path)) from None
        yield
    finally:
        os.close(fd)


def entry_lock(path):
    return path.parent / ('.mafft-deploy-' + hashlib.sha256(str(path).encode()).hexdigest()[:16] + '.lock')


@contextmanager
def interruptions():
    def stop(signum, frame):
        raise InterruptedError('Deployment interrupted by signal ' + str(signum))
    # Avoid Popen.wait's special KeyboardInterrupt grace wait/reaping behavior.
    old = {s: signal.signal(s, stop) for s in STOP_SIGNALS}
    try:
        yield
    finally:
        for s, handler in old.items():
            signal.signal(s, handler)


@contextmanager
def blocked_stop_signals():
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def wait_owned(child, timeout):
    deadline = time.monotonic() + timeout
    while True:
        # A Python signal can otherwise land between waitpid reaping the child
        # and Popen recording returncode. Keep that state update indivisible,
        # but unmask between short waits so interruption remains responsive.
        with blocked_stop_signals():
            try:
                return child.wait(timeout=max(0, min(.05, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(child.args, timeout)


def package_file(install, name, original_prefix):
    """Dereference internal aliases, including original absolute install links."""
    p = install / relative(name)
    for _ in range(32):
        p = Path(os.path.normpath(p))
        if not p.is_relative_to(install):
            raise ValueError('Package link escapes installation: ' + name)
        if not p.is_symlink():
            if not p.is_file():
                raise ValueError('Missing package file: ' + str(p))
            return p
        target = Path(os.readlink(p))
        if target.is_absolute():
            if target.is_relative_to(original_prefix):
                p = install / target.relative_to(original_prefix)
            elif target.is_relative_to(install):
                p = target
            else:
                raise ValueError('External package link: ' + name)
        else:
            p = p.parent / target
    raise ValueError('Package symlink cycle: ' + name)


def inventory(root):
    files = set()
    for p in root.rglob('*'):
        if p.is_symlink() and p.is_dir():
            raise ValueError('Directory symlinks are unsupported: ' + str(p))
        if p.is_symlink() or p.is_file():
            files.add(p.relative_to(root).as_posix())
    return files


def read_package(build):
    metadata = json.loads(load_pinned(build / 'build.json', BUILD_SHA))
    manifest = json.loads(load_pinned(build / 'binary-sha256.json', INSTALL_SHA))
    install = build / 'install'
    if inventory(install) != set(manifest):
        raise ValueError('Installation inventory differs from the audited manifest')
    sources = {}
    for name, digest in manifest.items():
        p = package_file(install, name, Path(metadata['prefix']))
        if sha(p) != digest:
            raise ValueError('Changed validated binary/data: ' + name)
        sources[name] = p
    for name, digest in metadata['source_sha256'].items():
        p = build / 'source' / relative(name)
        if p.is_symlink() or sha(p) != digest:
            raise ValueError('Changed validated native source: ' + name)
    return metadata, manifest, sources


def read_corpus(args):
    corpus = json.loads(load_pinned(args.corpus, CORPUS_SHA))
    golden = {}
    for line in load_pinned(args.golden_manifest, GOLDEN_SHA).decode().splitlines():
        digest, name = line.split(None, 1)
        name = Path(name.lstrip('*')).name
        if name in golden:
            raise ValueError('Repeated golden filename: ' + name)
        golden[name] = digest
    files = {r['file']: r for r in corpus['files']}
    if len(files) != len(corpus['files']) or set(files) != set(golden):
        raise ValueError('Corpus/golden membership differs')
    for name, r in files.items():
        if relative(name).name != name:
            raise ValueError('FASTA filenames must be flat')
        if sha(args.data / name) != r['sha256'] or sha(args.golden / name) != golden[name]:
            raise ValueError('Changed input or frozen golden: ' + name)
    selected = sorted(files) if args.validation == 'full' else list(SMOKE_INPUTS)
    if not all(n in files for n in selected):
        raise ValueError('Validation fixtures are missing')
    return files, golden, selected


def wrapper(final, name):
    # Pin helpers to this release even if a service inherited MAFFT_BINARIES.
    text = ('#!/bin/sh\nMAFFT_BINARIES=' + shlex.quote(str(final / 'payload/libexec/mafft')) +
            '\nexport MAFFT_BINARIES\n')
    if name == 'mafft':
        # Existing adjacent aliases can point to the public mafft command.
        # Preserve argv[0] so the upstream launcher retains their defaults.
        text += ('case ${0##*/} in\n  ' + '|'.join(STRATEGY_ALIASES) + ')\n'
                 '    exec ' + shlex.quote(str(final / 'payload/bin')) + '/"${0##*/}" "$@" ;;\nesac\n')
    # These two upstream Ruby drivers otherwise invoke /usr/local/bin/mafft.
    # They concatenate a shell command, so pass an already shell-quoted command.
    command = shlex.quote(str(final / 'bin/mafft'))
    if name == 'mafft-homologs.rb':
        text += 'MAFFT_HOMOLOGS_MAFFT=' + shlex.quote(command) + '\nexport MAFFT_HOMOLOGS_MAFFT\n'
    text += 'exec ' + shlex.quote(str(final / 'payload/bin' / name))
    if name == 'mafft-sparsecore.rb':
        text += ' -m ' + shlex.quote(command)
    return text + ' "$@"\n'


def verify_release(final, manifest, sources):
    load_pinned(final / 'build.json', BUILD_SHA)
    load_pinned(final / 'binary-sha256.json', INSTALL_SHA)
    record = json.loads((final / 'release.json').read_text())
    if record.get('schema') != SCHEMA or record.get('install_manifest_sha256') != INSTALL_SHA or record.get('source_commit') != SOURCE_COMMIT:
        raise ValueError('Existing release has different provenance: ' + str(final))
    if inventory(final / 'payload') != set(manifest):
        raise ValueError('Existing release inventory changed')
    for name, digest in manifest.items():
        p = final / 'payload' / relative(name)
        expected_mode = 0o555 if sources[name].stat().st_mode & 0o111 else 0o444
        if p.is_symlink() or sha(p) != digest or stat.S_IMODE(p.stat().st_mode) != expected_mode:
            raise ValueError('Existing release payload changed: ' + name)
    names = sorted(PurePosixPath(n).name for n in manifest if PurePosixPath(n).parent == PurePosixPath('bin'))
    if inventory(final / 'bin') != set(names):
        raise ValueError('Existing release wrappers changed')
    for name in names:
        p = final / 'bin' / name
        if p.is_symlink() or p.read_text() != wrapper(final, name) or not os.access(p, os.X_OK):
            raise ValueError('Existing release wrapper changed: ' + name)


def stage_release(args, metadata, manifest, sources):
    final = args.releases_dir / RELEASE_NAME
    if final.exists() or final.is_symlink():
        if final.is_symlink():
            raise ValueError('Release directory cannot be a symlink')
        verify_release(final, manifest, sources)
        return final
    stage = Path(tempfile.mkdtemp(prefix='.staging-', dir=args.releases_dir))
    for name, source in sources.items():
        target = stage / 'payload' / relative(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha(target) != manifest[name]:
            raise ValueError('Package changed during staging: ' + name)
        target.chmod(0o555 if source.stat().st_mode & 0o111 else 0o444)
        with target.open('rb') as f:
            os.fsync(f.fileno())
    (stage / 'bin').mkdir()
    for name in manifest:
        if PurePosixPath(name).parent == PurePosixPath('bin'):
            p = stage / 'bin' / PurePosixPath(name).name
            p.write_text(wrapper(final, p.name))
            p.chmod(0o555)
            with p.open('rb') as f:
                os.fsync(f.fileno())
    shutil.copyfile(args.build / 'build.json', stage / 'build.json')
    shutil.copyfile(args.build / 'binary-sha256.json', stage / 'binary-sha256.json')
    write_json(stage / 'release.json', {'schema': SCHEMA, 'source_commit': SOURCE_COMMIT,
               'install_manifest_sha256': INSTALL_SHA, 'build_metadata_sha256': BUILD_SHA,
               'version': VERSION, 'created_utc': utc(), 'final_path': str(final)})
    for p in stage.iterdir():
        if p.is_file():
            p.chmod(0o444)
            with p.open('rb') as f:
                os.fsync(f.fileno())
    for p in sorted([p for p in stage.rglob('*') if p.is_dir()], key=lambda p: len(p.parts), reverse=True):
        p.chmod(0o555)
        sync_dir(p)
    stage.chmod(0o555)
    sync_dir(stage)
    os.rename(stage, final)
    sync_dir(args.releases_dir)
    verify_release(final, manifest, sources)
    return final


def run(command, directory, timeout):
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / 'command.json', {'argv': command, 'utc': utc(), 'timeout_seconds': timeout})
    environment = dict(os.environ, LC_ALL='C', LANG='C', TMPDIR=str(directory))
    environment.pop('MAFFT_BINARIES', None)
    with (directory / 'stdout').open('xb') as stdout, (directory / 'stderr').open('xb') as stderr:
        child = subprocess.Popen(command, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                                 stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            code = wait_owned(child, timeout)
        except BaseException as error:
            # Kill before reaping the leader, preventing PID/group ID reuse.
            # Only this command's new process group, including helpers, is owned.
            with blocked_stop_signals():
                if child.returncode is None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
            write_json(directory / 'result.json', {'returncode': child.returncode, 'aborted': True,
                       'error': str(error), 'utc': utc(), 'stdout_sha256': sha(directory / 'stdout'),
                       'stderr_sha256': sha(directory / 'stderr')})
            raise
    write_json(directory / 'result.json', {'returncode': code, 'utc': utc(),
               'stdout_sha256': sha(directory / 'stdout'), 'stderr_sha256': sha(directory / 'stderr')})
    if code:
        raise RuntimeError('Validation exited ' + str(code) + '; see ' + str(directory / 'stderr'))


def check_version(binary, directory, timeout):
    run([str(binary), '--version'], directory, timeout)
    text = (directory / 'stdout').read_text() + (directory / 'stderr').read_text()
    if text.strip() != VERSION:
        raise ValueError('Unexpected deployed version: ' + repr(text.strip()))


def configurations(record):
    return [c for c in THREADS if c != 'O' or ORDERED_OPTION in record['production_options']]


def check_alignment(binary, args, files, golden, name, config, directory):
    directory.mkdir()
    local = directory / 'input.fasta'
    shutil.copyfile(args.data / name, local)
    if sha(local) != files[name]['sha256']:
        raise ValueError('Input changed while copying: ' + name)
    command = [str(binary)] + THREADS[config] + files[name]['production_options'] + [str(local)]
    run(command, directory / 'run', args.timeout)
    if sha(directory / 'run/stdout') != golden[name]:
        raise ValueError('Golden mismatch: ' + name + ' configuration ' + config)


def active(path, target, release_command):
    return (path.is_symlink() and os.readlink(path) == str(target) and
            target.is_symlink() and os.readlink(target) == str(release_command))


def atomic_link(path, target):
    temporary = path.parent / ('.mafft-activate-' + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def backup_entry(path, before, transaction):
    if before['kind'] == 'file':
        shutil.copy2(path, transaction / 'previous-file')
        if sha(transaction / 'previous-file') != before['sha256']:
            raise ValueError('Entrypoint changed during backup')
        with (transaction / 'previous-file').open('rb') as f:
            os.fsync(f.fileno())
    if snapshot(path) != before:
        raise ValueError('Entrypoint changed during backup')


def restoration_image(destination, before, transaction):
    if before['kind'] == 'symlink':
        destination.symlink_to(before['target'])
    else:
        saved = transaction / 'previous-file'
        if sha(saved) != before['sha256']:
            raise ValueError('Rollback backup checksum mismatch')
        shutil.copy2(saved, destination)
    os.chown(destination, before['uid'], before['gid'], follow_symlinks=False)
    if before['kind'] == 'file':
        destination.chmod(before['mode'])
        with destination.open('rb') as f:
            os.fsync(f.fileno())
    if snapshot(destination) != before:
        raise ValueError('Cannot reconstruct original ownership, mode and content')


def check_restoration(path, before, transaction):
    if before['kind'] == 'absent':
        return
    temporary = path.parent / ('.mafft-restore-check-' + uuid.uuid4().hex)
    try:
        restoration_image(temporary, before, transaction)
    finally:
        temporary.unlink(missing_ok=True)


def restore(path, before, transaction, expected_target, release_command):
    if snapshot(path) == before:
        return 'already-restored'
    if not active(path, expected_target, release_command):
        raise RuntimeError('Entrypoint changed since activation; refusing to overwrite it')
    temporary = path.parent / ('.mafft-rollback-' + uuid.uuid4().hex)
    try:
        if before['kind'] == 'absent':
            path.unlink()
        else:
            restoration_image(temporary, before, transaction)
            os.replace(temporary, path)
        sync_dir(path.parent)
        if snapshot(path) != before:
            raise RuntimeError('Restored entrypoint metadata/content differs')
    finally:
        temporary.unlink(missing_ok=True)
    return 'restored'


def deploy(args):
    args.build = args.build.resolve()
    args.releases_dir = args.releases_dir.resolve()
    args.entrypoint = entry_path(args.entrypoint)
    for protected in (args.build, args.data.resolve(), args.golden.resolve()):
        if args.releases_dir.is_relative_to(protected) or args.entrypoint.is_relative_to(protected):
            raise ValueError('Deployment destination overlaps protected inputs/build: ' + str(protected))
    metadata, manifest, sources = read_package(args.build)
    files, golden, selected = read_corpus(args)
    final = args.releases_dir / RELEASE_NAME
    before = snapshot(args.entrypoint)
    plan = {'action': 'deploy', 'apply': args.apply, 'entrypoint': str(args.entrypoint),
            'previous_entrypoint': before, 'release': str(final), 'source_commit': SOURCE_COMMIT,
            'version': VERSION, 'payload_files': len(manifest), 'input_files': selected,
            'validation_configurations': list(THREADS), 'validation': args.validation,
            'pipeline_arguments': 'Passed through; configure thread policy in the caller.',
            'install_manifest_sha256': INSTALL_SHA, 'build_metadata_sha256': BUILD_SHA}
    if not args.apply:
        return dict(plan, result='dry-run; no files written and no jobs launched')
    args.releases_dir.mkdir(parents=True, exist_ok=True)
    args.entrypoint.parent.mkdir(parents=True, exist_ok=True)
    with locked(args.releases_dir / '.deploy.lock'), locked(entry_lock(args.entrypoint)), interruptions():
        if snapshot(args.entrypoint) != before:
            raise RuntimeError('Entrypoint changed since planning; rerun')
        final = stage_release(args, metadata, manifest, sources)
        release_command = final / 'bin/mafft'
        if args.entrypoint.is_symlink() and args.entrypoint.resolve() == release_command:
            return dict(plan, result='already active; payload/wrappers verified, no switch performed')
        transactions = args.releases_dir / 'deployments'
        transactions.mkdir(mode=0o700, exist_ok=True)
        transaction = Path(tempfile.mkdtemp(prefix=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=transactions))
        # A unique, retained anchor distinguishes two activations of this same
        # release. An older rollback cannot overwrite a newer transaction.
        target = args.entrypoint.parent / ('.mafft-release-' + transaction.name)
        backup_entry(args.entrypoint, before, transaction)
        record = dict(plan, schema=SCHEMA, transaction=str(transaction), utc=utc(), target=str(target),
                      release_command=str(release_command), script_sha256=sha(Path(__file__)))
        write_json(transaction / 'deployment.json', record)
        sync_dir(transaction)
        sync_dir(transactions)
        print('Deployment record: ' + str(transaction / 'deployment.json'), file=sys.stderr, flush=True)
        activation_attempted = False
        try:
            check_restoration(args.entrypoint, before, transaction)
            check_version(release_command, transaction / 'pre-version', args.timeout)
            for name in selected:
                for config in configurations(files[name]):
                    check_alignment(release_command, args, files, golden, name, config,
                                    transaction / ('pre-' + name + '-' + config))
            # Recheck before activation, after potentially long validation.
            verify_release(final, manifest, sources)
            if snapshot(args.entrypoint) != before:
                raise RuntimeError('Entrypoint changed during validation; refusing activation')
            activation_attempted = True
            target.symlink_to(release_command)
            sync_dir(args.entrypoint.parent)
            atomic_link(args.entrypoint, target)
            check_version(args.entrypoint, transaction / 'post-version', args.timeout)
            check_alignment(args.entrypoint, args, files, golden, selected[0], 'A', transaction / 'post-alignment')
        except BaseException as error:
            reverted = 'not-activated'
            rollback_error = None
            if activation_attempted:
                try:
                    reverted = restore(args.entrypoint, before, transaction, target, release_command)
                except (OSError, ValueError, RuntimeError) as failure:
                    reverted, rollback_error = 'refused-or-failed', str(failure)
            write_json(transaction / 'result.json', {'ok': False, 'utc': utc(), 'rollback': reverted,
                       'error': str(error), 'rollback_error': rollback_error})
            if rollback_error:
                raise RuntimeError(str(error) + '; automatic rollback: ' + rollback_error) from error
            raise
        write_json(transaction / 'result.json', {'ok': True, 'utc': utc(), 'golden_checks': len(selected) * 2 + 1})
        return dict(plan, result='activated and verified', deployment=str(transaction / 'deployment.json'),
                    rollback_command=[sys.executable, str(Path(__file__).resolve()), 'rollback', '--deployment',
                                      str(transaction / 'deployment.json'), '--apply'])


def rollback(args):
    transaction = args.deployment.resolve().parent
    record = json.loads(args.deployment.read_text())
    if record.get('schema') != SCHEMA or transaction != Path(record['transaction']):
        raise ValueError('Not a matching deployment record')
    path = entry_path(record['entrypoint'])
    before, target = record['previous_entrypoint'], Path(record['target'])
    release_command = Path(record['release_command'])
    if before['kind'] == 'file' and sha(transaction / 'previous-file') != before['sha256']:
        raise ValueError('Rollback backup checksum mismatch')
    if snapshot(path) != before and not active(path, target, release_command):
        raise RuntimeError('A different deployment/manual change is active; refusing rollback')
    result = {'action': 'rollback', 'entrypoint': str(path), 'deployment': str(args.deployment), 'apply': args.apply}
    if not args.apply:
        return dict(result, result='dry-run; no files written')
    with locked(entry_lock(path)), interruptions():
        outcome = restore(path, before, transaction, target, release_command)
        write_json(transaction / ('rollback-' + uuid.uuid4().hex + '.json'), {'utc': utc(), 'result': outcome})
        return dict(result, result=outcome)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    d = sub.add_parser('deploy', help='Verify/stage the audited build and activate an explicit command')
    d.add_argument('--entrypoint', type=Path, required=True, help='The production mafft command to replace atomically')
    d.add_argument('--build', type=Path, default=WORKSPACE / 'round2/dikarya5-build')
    d.add_argument('--releases-dir', type=Path, default=Path('/opt/mafft-releases'))
    d.add_argument('--corpus', type=Path, default=WORKSPACE / 'bench/production-corpus.json')
    d.add_argument('--data', type=Path, default=WORKSPACE / 'bench/data')
    d.add_argument('--golden', type=Path, default=WORKSPACE / 'bench/golden/production-t1')
    d.add_argument('--golden-manifest', type=Path, default=WORKSPACE / 'bench/golden.sha256')
    d.add_argument('--validation', choices=['smoke', 'full'], default='smoke', help='Four representative inputs or all 100; both A and F')
    d.add_argument('--timeout', type=float, default=600, help='Seconds per validation command')
    b = sub.add_parser('rollback', help='Restore the exact prior file/symlink/absence from a deployment record')
    b.add_argument('--deployment', type=Path, required=True)
    for command in (d, b):
        mode = command.add_mutually_exclusive_group()
        mode.add_argument('--apply', action='store_true', help='Perform writes; omitted means dry-run')
        mode.add_argument('--dry-run', action='store_true', help='Explicitly select the default read-only mode')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if sys.platform != 'linux':
            raise ValueError('This script targets the validated Linux VM')
        if args.command == 'deploy':
            if not 0 < args.timeout < float('inf'):
                raise ValueError('--timeout must be positive and finite')
            if platform.machine() != 'x86_64' or 'avx2' not in Path('/proc/cpuinfo').read_text().split():
                raise ValueError('The audited build requires the studied x86-64 AVX2 arithmetic class')
            result = deploy(args)
        else:
            result = rollback(args)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        print('Deployment error: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
