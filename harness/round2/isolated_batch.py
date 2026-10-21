#!/usr/bin/env python3
"""Prospective isolated short-input batches; never a sustained-throughput test.

One frozen-corpus A mean wall < 1 s input, four policies, two rotated rounds,
20 fresh sequential invocations per policy/round. Every original GNU record and
flag remains unchanged. Selection uses a separately named policy-wide waited
child CPU delta, including GNU time itself. No observer CPU is subtracted.
"""
import argparse
import datetime
import json
import math
import os
from pathlib import Path
import resource
import shutil
import sys
import tempfile
import threading
import time

import alignments
import quiet
import throughput
from alignments import CONFIGS, FORMAT, digest, read_resources, write_json

POLICIES = ('one_job', 'two_threads', 'selective', 'refine_one')
SCHEMA = 'mafft-round2-isolated-batch-v1'
SELECTION_FIELD = 'contention_rusage_children_v1'
REPLICATES, ROUNDS, SHORT_SECONDS, CEILING = 20, 2, 1.0, .2
PRIMARY_SOURCES = {
    'python_resource': 'https://docs.python.org/3/library/resource.html',
    'linux_getrusage': 'https://www.man7.org/linux/man-pages/man2/getrusage.2.html',
    'gnu_time_format': 'https://www.gnu.org/software/time/manual/html_node/Format-String.html',
    'gnu_source_centisecond_formatting': 'https://sources.debian.org/src/time/1.7-21/time.c',
}
LIMITATIONS = [
    'RUSAGE_CHILDREN is a process-wide CPU accumulator for terminated, waited-for children; '
    'normal waited-for descendant chains and GNU time CPU are included. It is not a per-worker counter.',
    'Microsecond representation avoids GNU display truncation; it does not establish microsecond accuracy.',
    'Global CPU ticks, VM accounting, host interference and transient contention remain imperfectly observed.',
    'A clean batch flag concerns the aggregate window. All individual GNU flags remain visible.',
    'Twenty repeated invocations of one FASTA are technical replicates, not independent biological inputs.',
    'Fresh processes still use potentially warm executable/input page caches. All 20 trials are retained.',
    'Isolated batch jobs/hour is descriptive overhead accounting, not a production throughput recommendation.',
]


def finite(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (positive and not value):
        raise ValueError('invalid finite nonnegative measurement')
    return value


def policy_order(rnd):
    if type(rnd) is not int or rnd not in range(ROUNDS):
        raise ValueError('exactly two paired rounds required')
    return list(POLICIES[rnd:] + POLICIES[:rnd])


def record_id(name, policy, rnd, replicate):
    return f'r{rnd}:{policy}:replicate-{replicate:03d}:{name}'


def driver_priority():
    """Match the existing coordinator without changing any other process."""
    before = os.getpriority(os.PRIO_PROCESS, 0)
    if before > 10:
        raise ValueError('driver inherited niceness >10; launch a new coordinator at niceness 10')
    if before < 10:
        os.setpriority(os.PRIO_PROCESS, 0, 10)
    current = os.getpriority(os.PRIO_PROCESS, 0)
    if current != 10:
        raise ValueError('driver niceness must be exactly 10')
    return {'driver_before': before, 'driver_at_measurement': current,
            'gnu_time_inherits_driver_nice': current, 'native_nice_increment': 10,
            'expected_native_nice': 19,
            'native_priority_note': 'Expected Linux capped result of inherited nice 10 plus explicit nice -n 10; not a per-descendant observation.'}


def children_snapshot():
    try:
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        return {'available': True, 'pid': os.getpid(), 'user': usage.ru_utime,
                'system': usage.ru_stime, 'monotonic': time.monotonic()}
    except OSError as error:
        return {'available': False, 'pid': os.getpid(), 'error': str(error)}


def children_delta(before, after, gnu_cpu):
    try:
        if before.get('available') is not True or after.get('available') is not True or before['pid'] != after['pid']:
            raise ValueError('unavailable or different parent process')
        if finite(after['monotonic']) <= finite(before['monotonic']):
            raise ValueError('nonpositive child observation interval')
        user = finite(after['user']) - finite(before['user'])
        system = finite(after['system']) - finite(before['system'])
        total = finite(user) + finite(system)
        if total + .00001 < finite(gnu_cpu):
            raise ValueError('child CPU is below summed truncated GNU CPU')
        return {'available': True, 'user': user, 'system': system, 'cpu_seconds': total,
                'definition': 'RUSAGE_CHILDREN user+system delta; includes GNU time and waited-for descendants'}
    except (KeyError, TypeError, ValueError) as error:
        return {'available': False, 'reason': str(error), 'cpu_seconds': None}


def selection_contention(before, after, child_delta):
    result = quiet.busy_delta(before, after, child_delta.get('cpu_seconds'), max_busy_cores=CEILING)
    if child_delta.get('available') is not True:
        result.update(available=False, contention_flag=None, reason=child_delta.get('reason'))
    result.update(accounting_source='RUSAGE_CHILDREN policy delta',
                  note='Global busy minus waited child-tree CPU, including GNU time; Python observer CPU remains in residual. No observer subtraction.')
    return result


def eligibility(manifest, plan, rows, build_name):
    """Resolve the short cohort solely from every frozen full-corpus A row."""
    files = {f['file']: f for f in manifest['files']}
    if (len(files) != len(manifest['files']) or not files or
            len(plan['files']) != len(files) or {f['file']: f for f in plan['files']} != files):
        raise ValueError('eligibility plan must contain the entire identical production manifest')
    builds = [b for b in plan['builds'] if b['name'] == build_name]
    if len(builds) != 1:
        raise ValueError('select exactly one frozen candidate build')
    if set(plan['golden_sha256']) != set(files):
        raise ValueError('eligibility plan has incomplete golden hashes')
    rounds = plan['rounds']
    if type(rounds) is not int or rounds < 1:
        raise ValueError('invalid frozen corpus round count')
    candidate = [r for r in rows if r['build'] == build_name]
    expected = {(rnd, name) for rnd in range(rounds) for name in files}
    keys = [(r['round'], r['input']) for r in candidate]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError('eligibility corpus has missing or duplicate candidate rows')
    walls = {name: [] for name in files}
    for r in candidate:
        if (type(r['round']) is not int or type(r['threads']) is not int or
                r['input_sha256'] != files[r['input']]['sha256'] or r['threads'] != 1 or
                r['returncode'] != 0 or r.get('timed_out') is not False or
                r.get('resources_error') or r.get('identical') is not True or
                r['golden_sha256'] != plan['golden_sha256'][r['input']] or
                r['output_sha256'] != r['golden_sha256']):
            raise ValueError('eligibility requires successful exact single-thread full-corpus rows')
        expected = ['nice', '-n', '10', 'taskset', '-c', plan['cpu'], builds[0]['binary'],
                    '--thread', '1', *files[r['input']]['production_options'],
                    str((Path(plan['data'])/r['input']).resolve())]
        if r['argv'] != expected:
            raise ValueError('eligibility row differs from the frozen production command')
        walls[r['input']].append(finite(r['wall'], positive=True))
    means = {name: sum(values) / len(values) for name, values in walls.items()}
    return builds[0], means, sorted(name for name, wall in means.items() if wall < SHORT_SECONDS)


def validate_batch_summary(summary, runs, name, policy, rnd):
    """Pure structural/arithmetic checks; file provenance is checked separately."""
    config, concurrency = throughput.POLICIES[policy]
    if type(summary['round']) is not int or type(summary['jobs']) is not int:
        raise ValueError('batch round/count must be integers')
    if (summary['schema'], summary['policy'], summary['round'], summary['config'], summary['concurrency']) != (SCHEMA, policy, rnd, config, 1):
        raise ValueError('batch policy/round/schema mismatch')
    if concurrency != 1 or len(runs) != REPLICATES or summary['jobs'] != REPLICATES:
        raise ValueError('batch requires exactly 20 sequential jobs')
    if (summary['native_repeats_per_round'] != REPLICATES or summary['paired_rounds'] != ROUNDS or
            summary['native_repeats_per_input'] != REPLICATES*ROUNDS or
            summary['selection_contention_source'] != SELECTION_FIELD):
        raise ValueError('batch replicate/accounting protocol changed')
    expected = {record_id(name, policy, rnd, rep) for rep in range(REPLICATES)}
    if {r['record_id'] for r in runs} != expected or len({r['replicate'] for r in runs}) != REPLICATES:
        raise ValueError('missing or duplicate stable replicate IDs')
    for rep, r in enumerate(runs):
        if type(r['round']) is not int or type(r['replicate']) is not int:
            raise ValueError('replicate round/index must be integers')
        if (r['input'], r['policy'], r['round'], r['replicate'], r['config'], r['slot']) != (name, policy, rnd, rep, config, 0):
            raise ValueError('replicate ordering/configuration mismatch')
        if r['returncode'] or r['timed_out'] is not False or r.get('resources_error') or r['resources'].get('exit') != 0:
            raise ValueError('failed native replicate')
        if config in ('A', 'F') and r['matches_A'] is not True:
            raise ValueError('deterministic output mismatch')
        expected_cpu = finite(r['resources']['user']) + finite(r['resources']['system'])
        if not math.isclose(r['cpu'], expected_cpu, abs_tol=1e-9):
            raise ValueError('original GNU CPU changed')
        finite(r['service_seconds'], positive=True)
        if r['queue_seconds'] != 0 or r['response_seconds'] != r['service_seconds']:
            raise ValueError('isolated trials must not become a saturated queue')
        original = quiet.busy_delta(r['cpu_stat_before'], r['cpu_stat_after'], r['cpu'], max_busy_cores=CEILING)
        if (original != r['contention'] or original.get('available') is not True or
                type(original.get('contention_flag')) is not bool or original.get('external_attribution_valid') is not True):
            raise ValueError('original individual GNU contention changed or is unknown')
    total = sum(r['cpu'] for r in runs)
    if not math.isclose(summary['cpu_total_seconds'], total, abs_tol=1e-9):
        raise ValueError('GNU CPU summary mismatch')
    delta = children_delta(summary['rusage_children_before'], summary['rusage_children_after'], total)
    if delta != summary['rusage_children_delta']:
        raise ValueError('child CPU summary mismatch')
    if summary['child_cpu_total_seconds'] != delta['cpu_seconds'] or summary['child_cpu_per_job_seconds'] != (delta['cpu_seconds']/REPLICATES if delta['available'] else None):
        raise ValueError('child CPU diagnostic changed')
    expected_old = quiet.busy_delta(summary['cpu_stat_before'], summary['cpu_stat_after'], total, max_busy_cores=CEILING)
    expected_new = selection_contention(summary['cpu_stat_before'], summary['cpu_stat_after'], delta)
    if summary['contention'] != expected_old or summary[SELECTION_FIELD] != expected_new:
        raise ValueError('original/new contention fields changed')
    if summary['all_children_reaped'] is not True or summary['queue_order'] != [name] * REPLICATES:
        raise ValueError('incomplete sequential batch')
    elapsed = finite(summary['makespan_seconds'], positive=True)
    if elapsed + 1e-6 < sum(r['service_seconds'] for r in runs):
        raise ValueError('sequential batch is shorter than its native trials')
    completed = [finite(r['completed_since_policy_seconds'], positive=True) for r in runs]
    if any(completed[rep] + 1e-6 < sum(r['service_seconds'] for r in runs[:rep+1]) for rep in range(REPLICATES)) or completed != sorted(completed) or completed[-1] > elapsed + 1e-6:
        raise ValueError('native completion order or duration changed')


def clean_batch_set(summaries):
    keys = [(s['round'], s['policy']) for s in summaries]
    if len(keys) != ROUNDS * len(POLICIES) or set(keys) != {(r, p) for r in range(ROUNDS) for p in POLICIES}:
        return False
    return all(s['quiet_window'].get('ready') is True and
               s[SELECTION_FIELD].get('available') is True and
               s[SELECTION_FIELD].get('contention_flag') is False and
               s[SELECTION_FIELD].get('external_attribution_valid') is True for s in summaries)


def run_policy(args, record, policy, rnd, outdir, quiet_check, env, golden_hash):
    outdir.mkdir(parents=True, exist_ok=False)
    write_json(outdir / 'quiet-window.json', quiet_check)
    config, _ = throughput.POLICIES[policy]
    groups, lock, cancelled, stop = set(), threading.Lock(), threading.Event(), threading.Event()
    peaks, observer_errors = [], []
    def monitor():
        peak = 0
        try:
            while not stop.is_set():
                with lock:
                    current = groups.copy()
                peak = max(peak, throughput.proc_rss(current))
                stop.wait(.2)
        except Exception as error:
            observer_errors.append(str(error))
        finally:
            peaks.append(peak)
    observer_before = time.process_time()
    global_before = quiet.cpu_snapshot()
    child_before = children_snapshot()
    clock = time.monotonic()
    thread = threading.Thread(target=monitor)
    thread.start()
    runs = []
    try:
        for rep in range(REPLICATES):
            source = (args.data / record['file']).resolve()
            if digest(source) != record['sha256'] or digest(args.golden / record['file']) != golden_hash:
                raise RuntimeError('input or frozen golden changed')
            dest = outdir / f'replicate-{rep:03d}' / record['file']
            dest.mkdir(parents=True, exist_ok=False)
            command = ['nice', '-n', '10', 'taskset', '-c', '0,1', str(args.binary),
                       *CONFIGS[config], *record['production_options'], str(source)]
            cpu_before = quiet.cpu_snapshot()
            utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
            started = time.monotonic()
            with (dest / 'alignment.fasta').open('xb') as out, (dest / 'stderr.txt').open('xb') as err:
                execution = throughput.run_tracked_process(
                    ['/usr/bin/time', '-f', FORMAT, '-o', str(dest / 'resources.json'), *command],
                    stdout=out, stderr=err, env=env, timeout=args.timeout,
                    groups=groups, lock=lock, cancelled=cancelled)
            finished = time.monotonic()
            cpu_after = quiet.cpu_snapshot()
            usage, resource_error = read_resources(dest / 'resources.json')
            output_hash = digest(dest / 'alignment.fasta')
            cpu = usage['user'] + usage['system'] if usage else None
            r = dict(input=record['file'], input_sha256=record['sha256'], policy=policy, round=rnd,
                     replicate=rep, record_id=record_id(record['file'], policy, rnd, rep),
                     config=config, slot=0, threads=int(CONFIGS[config][1]),
                     recorded_production_threads=record['production_threads'], argv=command,
                     start_utc=utc, queue_seconds=0.0, service_seconds=finished-started,
                     response_seconds=finished-started, completed_since_policy_seconds=finished-clock,
                     cpu=cpu, resources=usage, resources_error=resource_error, **execution,
                     cpu_stat_before=cpu_before, cpu_stat_after=cpu_after,
                     contention=quiet.busy_delta(cpu_before, cpu_after, cpu, max_busy_cores=CEILING),
                     alignment=str(dest / 'alignment.fasta'), output_sha256=output_hash,
                     stderr_sha256=digest(dest / 'stderr.txt'), golden_sha256=golden_hash,
                     matches_A=output_hash == golden_hash)
            runs.append(r)
            with (outdir / 'runs.jsonl').open('a') as stream:
                stream.write(json.dumps(r, sort_keys=True) + '\n')
            if (execution['returncode'] or execution['timed_out'] or resource_error or
                    not (dest / 'alignment.fasta').stat().st_size or
                    config in ('A', 'F') and not r['matches_A']):
                raise RuntimeError('failed replicate preserved at ' + str(dest))
    finally:
        stop.set()
        thread.join()
    elapsed = time.monotonic() - clock
    child_after = children_snapshot()  # All GNU time children have already been waited for.
    global_after = quiet.cpu_snapshot()
    observer_cpu = time.process_time() - observer_before
    if groups or observer_errors:
        raise RuntimeError('incomplete child-group cleanup or RSS observer failure: ' + str(observer_errors))
    gnu_cpu = sum(r['cpu'] for r in runs)
    delta = children_delta(child_before, child_after, gnu_cpu)
    s = dict(schema=SCHEMA, policy=policy, round=rnd, config=config, concurrency=1,
             input=record['file'], jobs=len(runs), native_repeats_per_round=REPLICATES,
             native_repeats_per_input=REPLICATES*ROUNDS, paired_rounds=ROUNDS,
             selection_contention_source=SELECTION_FIELD,
             makespan_seconds=elapsed, jobs_per_hour=len(runs)*3600/elapsed,
             cpu_total_seconds=gnu_cpu, cpu_per_job_seconds=gnu_cpu/len(runs),
             child_cpu_total_seconds=delta['cpu_seconds'],
             child_cpu_per_job_seconds=delta['cpu_seconds']/len(runs) if delta['available'] else None,
             rusage_children_before=child_before, rusage_children_after=child_after, rusage_children_delta=delta,
             cpu_stat_before=global_before, cpu_stat_after=global_after,
             contention=quiet.busy_delta(global_before, global_after, gnu_cpu, max_busy_cores=CEILING),
             quiet_window=quiet_check, all_children_reaped=True,
             child_reaping_definition='All 20 direct GNU time Popen children returned from wait; descendant propagation assumes the normal waited MAFFT process chain.',
             observer_cpu_seconds=observer_cpu, observer_cpu_cores=observer_cpu/elapsed,
             observer_cpu_definition='Python process CPU including all observer/runner threads; diagnostic only, never subtracted',
             max_per_job_rss_mib=max(r['resources']['rss_kib'] for r in runs)/1024,
             peak_sampled_process_tree_rss_mib=max(peaks)/2**20,
             matches_A=sum(r['matches_A'] for r in runs), queue_order=[record['file']]*len(runs))
    s[SELECTION_FIELD] = selection_contention(global_before, global_after, delta)
    for key in ('service_seconds', 'response_seconds', 'queue_seconds'):
        target = {'service_seconds': 'service_latency_seconds', 'response_seconds': 'response_latency_seconds'}.get(key, key)
        s[target] = throughput.summary([r[key] for r in runs])
    validate_batch_summary(s, runs, record['file'], policy, rnd)
    write_json(outdir / 'summary.json', s)
    return s


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('manifest', 'data', 'golden', 'binary', 'source', 'output', 'eligibility-runs'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--eligibility-build', required=True)
    parser.add_argument('--file', required=True)
    parser.add_argument('--seed', type=int, default=40919)
    parser.add_argument('--timeout', type=float, default=1800)
    quiet.add_arguments(parser)
    parser.set_defaults(quiet_max_cpu=CEILING)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('timeout must be finite and positive')
    try:
        priority = driver_priority()
    except (OSError, ValueError) as error:
        parser.error(str(error))
    config = quiet.settings(args)
    if not config['enabled'] or config['max_busy_cores'] != CEILING or config['max_scientific_cores'] != .05:
        parser.error('this protocol requires enabled .2 global/.05 scientific quiet ceilings')
    for key in ('manifest', 'data', 'golden', 'binary', 'source', 'output', 'eligibility_runs'):
        setattr(args, key, getattr(args, key).resolve())
    manifest = json.loads(args.manifest.read_text())
    plan_path = args.eligibility_runs.parent / 'experiment.json'
    plan = json.loads(plan_path.read_text())
    if plan['manifest_sha256'] != digest(args.manifest):
        parser.error('frozen eligibility manifest differs')
    frozen_rows = [json.loads(line) for line in args.eligibility_runs.read_text().splitlines() if line.strip()]
    build, means, cohort = eligibility(manifest, plan, frozen_rows, args.eligibility_build)
    if args.file not in cohort or Path(args.file).name != args.file:
        parser.error('input is not prospectively eligible under frozen A wall <1s')
    record = next(f for f in manifest['files'] if f['file'] == args.file)
    binaries = throughput.installed_hashes(args.binary)
    if digest(args.binary) != build['launcher_sha256'] or binaries != build['installed_file_sha256']:
        parser.error('binary differs from frozen full-corpus candidate')
    frozen_source = throughput.source_hashes(args.source)
    args.output.mkdir(parents=True, exist_ok=False)
    tmp = args.output / 'tmp'; tmp.mkdir()
    env = dict(os.environ, LC_ALL='C', LANG='C', TMPDIR=str(tmp)); env.pop('MAFFT_BINARIES', None)
    with tempfile.TemporaryFile(dir=tmp) as out, tempfile.TemporaryFile(dir=tmp) as err:
        execution = alignments.run_process_group([str(args.binary), '--version'], stdout=out, stderr=err, env=env, timeout=30)
        if execution['returncode'] or execution['timed_out']:
            raise RuntimeError('binary version probe failed')
        out.seek(0); err.seek(0); version = (out.read()+err.read()).decode('utf-8', errors='replace')
    if version != build['version']:
        raise RuntimeError('version differs from frozen full-corpus candidate')
    golden_hash = digest(args.golden / args.file)
    if golden_hash != plan['golden_sha256'][args.file]:
        raise RuntimeError('golden differs from frozen full-corpus golden')
    sources = {m.__name__: Path(m.__file__).resolve() for m in (alignments, quiet, throughput)}
    sources['isolated_batch'] = Path(__file__).resolve()
    harness = args.output / 'harness'; harness.mkdir()
    harness_hashes = {}
    for name, path in sources.items():
        shutil.copyfile(path, harness / path.name)
        harness_hashes[name] = {'path': str(path), 'sha256': digest(path), 'retained_copy': str(harness / path.name)}
    metadata = dict(schema=SCHEMA, protocol=SCHEMA, argv=sys.argv, binary=str(args.binary),
                    launcher_sha256=digest(args.binary), version=version, installed_file_sha256=binaries,
                    supplied_source_snapshot=frozen_source, manifest_sha256=digest(args.manifest),
                    files=[record], input_count=1, data=str(args.data), golden=str(args.golden),
                    golden_sha256={args.file: golden_hash}, selected_policies=list(POLICIES),
                    rounds=ROUNDS, paired_rounds=ROUNDS, native_repeats_per_input=REPLICATES*ROUNDS,
                    native_repeats_per_round=REPLICATES, seed=args.seed, quiet_window=config,
                    seed_note='No randomization within a singleton-input batch; the policy order is explicitly rotated by round.',
                    policy_order_by_round=[policy_order(rnd) for rnd in range(ROUNDS)], priority=priority,
                    timeout=args.timeout, harness_source_at_startup=harness_hashes,
                    python_executable=sys.executable, python_version=sys.version,
                    gnu_time_sha256=digest(Path('/usr/bin/time')),
                    selection_contention_source=SELECTION_FIELD,
                    selection_rule='first complete clean two-round batch set; all eight policy/round windows required',
                    eligibility={'runs': str(args.eligibility_runs), 'full_corpus_runs_sha256': digest(args.eligibility_runs),
                                 'experiment': str(plan_path), 'full_corpus_experiment_sha256': digest(plan_path),
                                 'build': args.eligibility_build, 'metric': 'mean A wall across all frozen corpus rounds',
                                 'strict_threshold_seconds': SHORT_SECONDS, 'input_mean_A_wall_seconds': means[args.file],
                                 'eligible_short_inputs': cohort},
                    arrival_model='One fresh isolated invocation at a time; queue_seconds=0, response=service. No independent-job concurrency.',
                    cpu_definitions={'cpu': 'Original GNU user+system, unchanged',
                                     'child_cpu': 'Policy RUSAGE_CHILDREN delta/N, includes GNU time; no per-job attribution'},
                    rss_definition='0.2s active process-group RSS samples; original per-job GNU RSS retained separately',
                    primary_sources=PRIMARY_SOURCES, limitations=LIMITATIONS)
    write_json(args.output / 'experiment.json', metadata)
    summaries = []
    try:
        for rnd in range(ROUNDS):
            for policy in policy_order(rnd):
                if (args.output / 'PAUSE').exists():
                    raise RuntimeError('orchestration pause at batch boundary; no job launched')
                check = quiet.wait_for_quiet(config, status_path=args.output / 'STATUS.json',
                                             context={'round': rnd, 'policy': policy})
                if not check['ready'] or (args.output / 'PAUSE').exists():
                    raise RuntimeError('quiet unavailable or pause requested; no batch launched')
                write_json(args.output / 'STATUS.json', dict(phase='running_batch', round=rnd, policy=policy))
                summaries.append(run_policy(args, record, policy, rnd, args.output/f'r{rnd}'/policy,
                                            check, env, golden_hash))
        if throughput.installed_hashes(args.binary) != binaries:
            raise RuntimeError('native installation changed during campaign')
        if any(digest(path) != harness_hashes[name]['sha256'] for name, path in sources.items()):
            raise RuntimeError('harness changed during campaign')
        if digest(args.eligibility_runs) != metadata['eligibility']['full_corpus_runs_sha256']:
            raise RuntimeError('frozen eligibility corpus changed during campaign')
        if digest(plan_path) != metadata['eligibility']['full_corpus_experiment_sha256'] or throughput.source_hashes(args.source) != frozen_source:
            raise RuntimeError('frozen eligibility plan or native source changed during campaign')
        if digest(Path('/usr/bin/time')) != metadata['gnu_time_sha256']:
            raise RuntimeError('GNU time changed during campaign')
        if (digest(args.manifest) != metadata['manifest_sha256'] or
                digest(args.data/args.file) != record['sha256'] or digest(args.golden/args.file) != golden_hash):
            raise RuntimeError('manifest, input or frozen golden changed during campaign')
    except BaseException as error:
        write_json(args.output / 'STATUS.json', {'finished': False, 'accepted': False, 'reason': str(error)})
        raise
    accepted = clean_batch_set(summaries)
    write_json(args.output / 'STATUS.json', {'finished': True, 'accepted': accepted,
               'selection_contention_source': SELECTION_FIELD, 'completed_batches': len(summaries)})
    print(json.dumps({'output': str(args.output), 'accepted': accepted, 'completed_batches': len(summaries)}))


if __name__ == '__main__':
    main()
