#!/usr/bin/env python3
"""Resumable, interleaved single-thread benchmarks against immutable goldens.

GNU time resources belong to one completed job, rather than the Python
process's cumulative child high-water mark. No production file is modified.
"""
import argparse
import datetime
import json
import math
import os
from pathlib import Path
import tempfile
import time
from alignments import FORMAT, digest, write_json, run_process_group, read_resources
from quiet import add_arguments as add_quiet_arguments, settings as quiet_settings, cpu_snapshot, busy_delta, wait_for_quiet


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--golden', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--build', action='append', required=True, help='NAME=/private/prefix/bin/mafft')
    p.add_argument('--partition', choices=['all', 'development', 'holdout', 'difficult'], default='all')
    p.add_argument('--files', nargs='+')
    p.add_argument('--rounds', type=int, default=1)
    p.add_argument('--cpu', default='0')
    p.add_argument('--timeout', type=float, default=1800)
    add_quiet_arguments(p)
    a = p.parse_args()
    if not math.isfinite(a.timeout) or a.timeout <= 0:
        p.error('timeout must be finite and positive')
    try:
        quiet_config = quiet_settings(a)
    except ValueError as error:
        p.error(str(error))
    a.output = a.output.resolve()
    a.output.mkdir(parents=True, exist_ok=True)
    tmp = a.output / 'tmp'
    tmp.mkdir(exist_ok=True)
    env = dict(os.environ, LC_ALL='C', LANG='C', TMPDIR=str(tmp))
    env.pop('MAFFT_BINARIES', None)
    files = [f for f in json.loads(a.manifest.read_text())['files']
             if (a.partition == 'all' or f['partition'] == a.partition)
             and (not a.files or f['file'] in a.files)]
    if not files or a.rounds < 1:
        p.error('select at least one input and round')
    builds = []
    version_timeout = 30.0
    for item in a.build:
        if '=' not in item:
            p.error('each build must be NAME=/private/prefix/bin/mafft')
        name, executable = item.split('=', 1)
        binary = Path(executable).resolve()
        if not name or name in {'.', '..'} or Path(name).name != name or name in {b['name'] for b in builds}:
            p.error('build names must be unique simple path components')
        with tempfile.TemporaryFile(dir=tmp) as version_out, tempfile.TemporaryFile(dir=tmp) as version_err:
            try:
                version_execution = run_process_group([str(binary), '--version'], stdout=version_out,
                                                      stderr=version_err, env=env, timeout=version_timeout)
            except OSError as error:
                p.error(f'cannot run --version for {binary}: {error}')
            if version_execution['timed_out']:
                p.error(f'--version timed out after {version_timeout:g} seconds: {binary}')
            if version_execution['returncode']:
                p.error(f'--version exited with status {version_execution["returncode"]}: {binary}')
            version_out.seek(0)
            version_err.seek(0)
            version = (version_out.read() + version_err.read()).decode('utf-8', errors='replace')
        prefix = binary.parent.parent
        binaries = {str(x.relative_to(prefix)): digest(x) for directory in ['bin', 'libexec/mafft']
                    for x in (prefix / directory).rglob('*') if x.is_file()}
        builds.append({'name': name, 'binary': str(binary), 'launcher_sha256': digest(binary),
                       'version': version,
                       'installed_file_sha256': binaries})
    golden_hashes = {f['file']: digest(a.golden / f['file']) for f in files}
    plan = {'manifest_sha256': digest(a.manifest), 'files': files, 'builds': builds,
            'golden_sha256': golden_hashes,
            'golden': str(a.golden.resolve()), 'data': str(a.data.resolve()),
            'rounds': a.rounds, 'cpu': a.cpu, 'timeout': a.timeout,
            'version_timeout': version_timeout,
            'order_policy': 'two builds alternate by round/input parity; otherwise rotate/reverse cycles',
            'quiet_window': quiet_config,
            'arithmetic': 'Compiler flags/source provenance are recorded in each private build directory.',
            'purpose': 'Paired performance: no competing research computation during measurements.',
            'rss_definition': 'GNU time maximum resident size for this job; not summed concurrent process RSS.'}
    meta = a.output / 'experiment.json'
    if meta.exists():
        if json.loads(meta.read_text()) != plan:
            raise SystemExit('Resume plan differs; preserve this output and choose a new directory')
    else:
        write_json(meta, plan)
    logpath = a.output / 'runs.jsonl'
    done = {}
    if logpath.exists():
        for line in logpath.read_text().splitlines():
            r = json.loads(line)
            if (r['returncode'] or not r['identical'] or digest(r['alignment']) != r['output_sha256']
                    or r['golden_sha256'] != golden_hashes[r['input']]
                    or digest(Path(r['alignment']).parent / 'stderr.txt') != r['stderr_sha256']):
                raise SystemExit('Prior failed/changed record; preserve it and choose a new directory')
            done[(r['round'], r['input'], r['build'])] = r
    total = a.rounds * len(files) * len(builds)
    with logpath.open('a', buffering=1) as log:
        for rnd in range(a.rounds):
            for index, f in enumerate(files):
                shift = (rnd + index) % len(builds)
                order = builds[shift:] + builds[:shift]
                if len(builds) != 2 and ((rnd + index) // len(builds)) % 2:
                    order = order[::-1]
                for b in order:
                    key = (rnd, f['file'], b['name'])
                    if key in done:
                        continue
                    while (a.output / 'PAUSE').exists():
                        write_json(a.output / 'STATUS.json', {'paused': True, 'complete': len(done), 'total': total})
                        time.sleep(.5)
                    quiet_check = wait_for_quiet(quiet_config, status_path=a.output / 'STATUS.json',
                                                context={'running': key, 'complete': len(done), 'total': total})
                    if quiet_config['enabled']:
                        with (a.output / 'quiet-checks.jsonl').open('a') as quiet_log:
                            quiet_log.write(json.dumps({'job': key, **quiet_check}) + '\n')
                    if not quiet_check['ready']:
                        raise SystemExit('Quiet window unavailable; no job launched. Preserve this output and resume when idle.')
                    # A controller may have requested a pause during the quiet wait.
                    # Recheck before launching a native process; revalidate quiet on resume.
                    if (a.output / 'PAUSE').exists():
                        raise SystemExit('Pause requested during quiet wait; no job launched. Resume this output after removing PAUSE.')
                    write_json(a.output / 'STATUS.json', {'paused': False, 'running': key, 'complete': len(done), 'total': total})
                    source = (a.data / f['file']).resolve()
                    if digest(source) != f['sha256']:
                        raise SystemExit('Input hash changed: ' + str(source))
                    gold = a.golden / f['file']
                    dest = a.output / b['name'] / ('r' + str(rnd)) / f['file']
                    dest.mkdir(parents=True, exist_ok=False)
                    output = dest / 'alignment.fasta'
                    command = ['nice', '-n', '10', 'taskset', '-c', a.cpu, b['binary'], '--thread', '1',
                               *f['production_options'], str(source)]
                    resources = dest / 'resources.json'
                    cpu_before = cpu_snapshot()
                    start = time.monotonic()
                    utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    with output.open('xb') as out, (dest / 'stderr.txt').open('xb') as err:
                        execution = run_process_group(['/usr/bin/time', '-f', FORMAT, '-o', str(resources), *command],
                                                      stdout=out, stderr=err, env=env, timeout=a.timeout)
                    rc = execution['returncode']
                    elapsed = time.monotonic() - start
                    cpu_after = cpu_snapshot()
                    usage, resource_error = read_resources(resources)
                    text = (dest / 'stderr.txt').read_text(errors='replace')
                    r = {'build': b['name'], 'input': f['file'], 'input_sha256': f['sha256'],
                         'partition': f['partition'], 'round': rnd, 'threads': 1, 'argv': command,
                         'start_utc': utc, 'wall': elapsed, 'returncode': rc, **execution,
                         'resources_error': resource_error,
                         'resources': usage, 'cpu': usage['user'] + usage['system'] if usage else None,
                         'alignment': str(output), 'output_sha256': digest(output), 'golden_sha256': golden_hashes[f['file']],
                         'stderr_sha256': digest(dest / 'stderr.txt'),
                         'quiet_window': quiet_check, 'cpu_stat_before': cpu_before, 'cpu_stat_after': cpu_after,
                         'strategy': next((s for s in ['L-INS-i', 'FFT-NS-i', 'FFT-NS-2', 'G-INS-i', 'E-INS-i'] if s in text), 'unknown')}
                    r['contention'] = busy_delta(cpu_before, cpu_after, r['cpu'], max_busy_cores=quiet_config['max_busy_cores'])
                    r['identical'] = r['output_sha256'] == r['golden_sha256']
                    log.write(json.dumps(r, sort_keys=True) + '\n')
                    done[key] = r
                    print(f"{len(done)}/{total} {b['name']} r{rnd} {f['file']} wall={elapsed:.3f} cpu={r['cpu']} exact={r['identical']} contention={r['contention']['contention_flag']}", flush=True)
                    if rc or execution['timed_out'] or resource_error or not r['identical'] or not output.stat().st_size:
                        raise SystemExit('Failed run preserved: ' + str(dest))
    write_json(a.output / 'STATUS.json', {'finished': True, 'complete': len(done), 'total': total})


if __name__ == '__main__':
    main()
